"""Actual captured Cover worker, PNGs and saved-store ownership guards."""
import ast
import asyncio
import copy
import hashlib
import io
import json
from pathlib import Path
import sys
import subprocess
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import cover_workflow as cover
from background_tasks import TaskManager, TaskContext
from render import render_frame
from render_context import RenderContext
import test_recipe_workflow as recipes
from test_media_collection import run_async_check
import test_project_sync as store


class StoreCover(recipes.StoreRecipes):
    # Reuse the saved-store/media setup without rerunning its separate suite.
    for _name in dir(recipes.StoreRecipes):
        if _name.startswith('test_'): locals()[_name] = None
    def setUp(self):
        recipes.StoreRecipes.setUp(self)
        self.manager.handlers['cover'] = cover.analyze
        self.env['Response'] = lambda **value: value
        names = {'recipe_cover', '_task_cover_workflow', '_review_cover_workflow', 'background_cover_review', 'background_cover_image'}
        nodes = [n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n, 'name', None) in names]
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    route = recipes.StoreRecipes.route
    project = recipes.StoreRecipes.project

    def body(self, **kw):
        return {'_context': self.current(), 'request_id': 'a'*32, 'sequence': 's', 'time': 2.25,
                'headline': '', 'sub': '', 'sizes': [[64,48]], 'framing': 'contain', **kw}

    def ready(self, **kw):
        identity = self.route('recipe_cover', self.body(**kw))['task']['id']
        value = self.manager.values[identity]
        self.env['_task_cover_workflow'](value['payload'], TaskContext(self.manager, identity))
        self.manager.store.save(value)
        return identity

    def review(self, identity): return self.route('background_cover_review', {'_context': self.current()}, identity)

    def download(self, identity, index=0, **kw):
        context = self.current()
        return run_async_check(self.env['background_cover_image'](identity, index, **{
            'workspace': context['workspace'], 'project': context['project'], 'revision': context['revision'], **kw}))

    def test_real_frame_only_png_matches_original_and_never_changes_history(self):
        from PIL import Image
        before = self.raw(); expected = self.root/'expected.png'
        with RenderContext(scratch_parent=str(self.root)) as context: render_frame(self.project(), 's', 2.25, str(expected), context=context)
        identity = self.ready(); review = self.review(identity); download = self.download(identity)
        path = cover.verify_result(self.manager.get(identity)['payload'], review['result'], identity)[0]
        with Image.open(path) as actual, Image.open(expected) as original:
            self.assertEqual(actual.convert('RGB').tobytes(), original.convert('RGB').tobytes())
        self.assertEqual(hashlib.sha256(download['content']).hexdigest(), review['result']['covers'][0]['sha256'])
        self.assertEqual(download['media_type'], 'image/png'); self.assertEqual(review['plan']['ops'], [])
        self.assertEqual(self.raw(), before); self.assertFalse(self.env['read_undo_history']('a')['undo'])
        with self.assertRaises(store.HTTPError): self.route('background_task_apply', {'_context': self.current()}, identity)

    def test_nested_composition_captions_titles_and_overlay_preserve_source_graph(self):
        from PIL import Image
        project = self.project(); source = project['sequences'][0]
        child = copy.deepcopy(source); child['id'] = 'child'; child['name'] = 'Nested picture'
        child['tracks'][1]['clips'] = [{'id': 'text', 'media_id': None, 'start': 0, 'in_': 0, 'out': 6, 'speed': 1,
            'graphic': {'layers': [{'kind':'shape','shape':'rect','x':0,'y':0,'w':.5,'h':1,'color':'#00FF00'}]}}]
        source['tracks'][0]['clips'] = [{'id':'nest','sequence_id':'child','start':0,'in_':0,'out':6,'speed':1}]
        source['captions'] = [{'id':'caption','start':0,'end':6,'text':'NEST'}]
        source['caption_style'] = {'size': 10}
        project['sequences'].append(child); self.env['save_project'](project); before = self.raw()
        expected = self.root/'nested-expected.png'
        with RenderContext(scratch_parent=str(self.root)) as context: render_frame(project,'s',2.25,str(expected),context=context)
        identity = self.ready(); value = self.manager.get(identity)
        self.assertEqual({s['id'] for s in value['payload']['project']['sequences']}, {'s','child'})
        path = cover.verify_result(value['payload'],value['result'],identity)[0]
        with Image.open(path) as image, Image.open(expected) as original:
            self.assertEqual(image.convert('RGB').tobytes(), original.convert('RGB').tobytes())
            self.assertGreater(image.convert('RGB').getpixel((5,5))[1], 150)
        self.assertEqual(self.raw(), before)
        project = self.project(); project['sequences'][1]['tracks'][1]['clips'][0]['graphic']['layers'][0]['color']='#FF0000';self.env['save_project'](project)
        with self.assertRaises(store.HTTPError): self.review(identity)

    def test_each_target_canvas_fits_headline_after_size_selection(self):
        from PIL import Image
        identity = self.ready(headline='A professional headline',sub='Owned cover',sizes=[[320,180],[180,320]])
        value = self.manager.get(identity); review = self.review(identity)
        self.assertEqual(len(review['result']['covers']),2)
        for descriptor,path in zip(review['result']['covers'],cover.verify_result(value['payload'],value['result'],identity)):
            with Image.open(path) as image: self.assertEqual(image.size,(descriptor['width'],descriptor['height']))
        large = cover.target_project(value['payload'],'/captured.png',1080,1920)
        small = cover.target_project(value['payload'],'/captured.png',64,48)
        layers = lambda p:p['sequences'][0]['tracks'][1]['clips'][0]['graphic']['layers']
        large_text = next(x for x in layers(large) if x.get('kind')=='text'); small_text = next(x for x in layers(small) if x.get('kind')=='text')
        self.assertGreater(large_text['size'],small_text['size']*5)

    def _glyph_mask(self, layer, width, height, name):
        from PIL import Image
        from render import drawtext_opts
        path = self.root/(name+'.png')
        layer = dict(layer, color='white', box=False, shadow=False, borderw=0)
        with RenderContext(scratch_parent=str(self.root)) as context:
            options = drawtext_opts(layer, width, height, 28, context=context)
            subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color=black:s={width}x{height}:r=30:d=1',
                            '-vf',options,'-frames:v','1','-threads','1',str(path)],check=True,capture_output=True,timeout=30)
        with Image.open(path) as image: mask = image.convert('L')
        bounds = mask.getbbox(); self.assertIsNotNone(bounds)
        return mask, mask.crop(bounds), options

    def test_generated_subtitle_retains_complete_centered_glyphs_and_authored_default_stays_unchanged(self):
        identity = self.route('recipe_cover',self.body(headline='A professional headline',sub='Owned composition',sizes=[[320,180]]))['task']['id']
        payload = self.manager.get(identity)['payload']
        target = cover.target_project(payload,'/captured.png',320,180)
        text = [layer for layer in target['sequences'][0]['tracks'][1]['clips'][0]['graphic']['layers'] if layer.get('kind')=='text']
        subtitle = text[1]; self.assertTrue(subtitle['fix_bounds']); self.assertEqual(subtitle['text'],'Owned\ncomposition')
        fixed, fixed_crop, options = self._glyph_mask(subtitle,320,180,'fixed')
        _, centered_crop, _ = self._glyph_mask(dict(subtitle,y=0,fix_bounds=False),320,180,'centered')
        authored = dict(subtitle); authored.pop('fix_bounds')
        clipped, clipped_crop, authored_options = self._glyph_mask(authored,320,180,'authored')
        unchanged, _, _ = self._glyph_mask(dict(authored,fix_bounds=False),320,180,'false-flag')
        self.assertIn('fix_bounds=1',options); self.assertNotIn('fix_bounds=',authored_options)
        self.assertEqual(fixed_crop.size,centered_crop.size); self.assertEqual(fixed_crop.tobytes(),centered_crop.tobytes())
        self.assertEqual(clipped.tobytes(),unchanged.tobytes())
        self.assertLess(sum(bool(p) for p in clipped.tobytes()),sum(bool(p) for p in fixed.tobytes()))
        self.assertLess(clipped_crop.height,fixed_crop.height)

    def test_small_cover_scales_explicit_line_spacing_and_keeps_separate_complete_lines(self):
        identity = self.route('recipe_cover',self.body(headline='WIDE WORDS'))['task']['id']
        payload = self.manager.get(identity)['payload']; target = cover.target_project(payload,'/captured.png',64,48)
        headline = next(layer for layer in target['sequences'][0]['tracks'][1]['clips'][0]['graphic']['layers'] if layer.get('kind')=='text')
        self.assertEqual(headline['text'],'WIDE\nWORDS'); self.assertEqual(int(headline['line_spacing']),0)
        mask, glyphs, _ = self._glyph_mask(headline,64,48,'small-fixed')
        _, centered, _ = self._glyph_mask(dict(headline,y=0,fix_bounds=False),64,48,'small-centered')
        self.assertEqual(glyphs.size,centered.size); self.assertEqual(glyphs.tobytes(),centered.tobytes())
        rows = [any(glyphs.crop((0,y,glyphs.width,y+1)).tobytes()) for y in range(glyphs.height)]
        bands = sum(on and (i==0 or not rows[i-1]) for i,on in enumerate(rows))
        self.assertEqual(bands,2)
        _, overlapping, _ = self._glyph_mask(dict(headline,y=0,line_spacing=-10),64,48,'old-spacing')
        self.assertGreater(sum(bool(p) for p in glyphs.tobytes()),sum(bool(p) for p in overlapping.tobytes()))

    def test_measured_font_bounds_shrink_or_refuse_before_queuing_without_project_changes(self):
        before=self.raw()
        with self.assertRaisesRegex(store.HTTPError,'shorten the text or choose a larger canvas'):
            self.route('recipe_cover',self.body(headline='W'*500,sizes=[[16,16]]))
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)
        from PIL import ImageFont
        from project_resources import style_font
        layer={'kind':'text','text':'W'*12,'size':20,'font':'','line_spacing':-10}
        calls=[];original=ImageFont.truetype
        def counted(*args,**kwargs): calls.append(args[1]);return original(*args,**kwargs)
        with patch.object(ImageFont,'truetype',counted): cover._fit_layer_bounds(layer,150,64,48)
        font=original(style_font(layer),int(layer['size']))
        self.assertLess(layer['size'],20);self.assertLessEqual(font.getlength(layer['text']),64)
        self.assertLessEqual(len(calls),11)
        self.assertAlmostEqual(layer['line_spacing'],-10*layer['size']/150)
        calls.clear()
        with patch.object(ImageFont,'truetype',counted),self.assertRaisesRegex(ValueError,'shorten the text'):
            cover._fit_layer_bounds({'text':'W'*500,'size':655.36},2048,16,4096)
        self.assertLessEqual(len(calls),11)
        with self.assertRaisesRegex(ValueError,'shorten the text'):
            cover._fit_layer_bounds({'text':'W\nW','size':1,'line_spacing':-100},1,64,48)

    def test_foreign_and_stale_context_never_queue_review_or_download(self):
        identity = self.ready(); context = self.current(); original = self.project()
        self.env['set_active_project']('b'); self.env['save_project'](copy.deepcopy(original)); before = self.raw()
        for action in (lambda:self.route('recipe_cover',self.body(_context=context)),lambda:self.review(identity),lambda:self.download(identity)):
            with self.assertRaises(store.HTTPError):action()
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('b')['undo'])
        self.env['set_active_project']('a')
        with self.assertRaises(store.HTTPError):self.download(identity,workspace='foreign')
        project=self.project();project['name']='Unrelated edit';self.env['save_project'](project)
        self.assertTrue(self.review(identity)['result']['covers'])
        with self.assertRaises(store.HTTPError):self.download(identity,revision=context['revision'])

    def test_restart_reuses_ready_result_and_retry_gets_new_owned_directory(self):
        identity=self.ready();first=self.manager.get(identity)['result']['covers'][0]
        restored=TaskManager(self.root,{'cover':cover.analyze},start=False);self.addCleanup(restored.shutdown)
        self.assertEqual(restored.get(identity)['record']['status'],'ready')
        self.assertEqual(self.route('recipe_cover',self.body())['task']['id'],identity)
        self.manager.cancel(identity)
        with self.assertRaises(store.HTTPError):self.download(identity)
        reply=self.route('background_task_retry',{'_context':self.current(),'request_id':'b'*32},identity);other=reply['task']['id']
        self.assertNotEqual(other,identity);value=self.manager.values[other];cover.analyze(value['payload'],TaskContext(self.manager,other))
        self.assertNotEqual(value['result']['covers'][0]['filename'],first['filename'])
        self.assertTrue((cover.artifact_folder(self.root,identity)/first['filename']).exists())

    def test_validation_bounds_missing_resources_and_no_overwrite(self):
        before=self.raw()
        for patch_ in ({'time':float('nan')},{'time':6},{'sizes':[[0,48]]},{'sizes':[[64,48],[64,48]]},
                       {'sizes':[[4096,4096]]*4},{'headline':'x'*501},{'framing':'unknown'},{'request_id':'bad'}):
            with self.subTest(patch_=patch_),self.assertRaises(store.HTTPError):self.route('recipe_cover',self.body(**patch_))
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)
        identity=self.route('recipe_cover',self.body())['task']['id'];value=self.manager.get(identity)
        folder=cover.artifact_folder(self.root,identity);sentinel=folder/'source-frame.png';sentinel.write_bytes(b'owned elsewhere')
        with self.assertRaises(FileExistsError):cover.analyze(value['payload'],TaskContext(self.manager,identity))
        self.assertEqual(sentinel.read_bytes(),b'owned elsewhere')

    def test_source_artifact_and_template_changes_refuse(self):
        identity=self.ready(headline='Owned');value=self.manager.get(identity);before=self.raw()
        original=cover._templates
        def changed(*args):
            templates=original(*args);templates['Hook — Big Statement']['layers'][0]['opacity']=.1;return templates
        with patch.object(cover,'_templates',changed),self.assertRaises(store.HTTPError):self.review(identity)
        path=cover.verify_result(value['payload'],value['result'],identity)[0];raw=path.read_bytes();path.write_bytes(raw+b'changed')
        with self.assertRaises(store.HTTPError):self.download(identity)
        path.write_bytes(raw)
        with self.source.open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(store.HTTPError):self.review(identity)
        self.assertEqual(self.raw(),before)

    def test_final_source_change_and_cancel_clean_all_owned_pngs(self):
        for kind,token in (('source','a'),('cancel','b')):
            identity=self.route('recipe_cover',self.body(request_id=token*32))['task']['id'];value=self.manager.values[identity];task=TaskContext(self.manager,identity)
            actual=cover.check_sources;calls=[]
            def checkpoint(payload):
                calls.append(None)
                if len(calls)==2:
                    if kind=='source':raise ValueError('controlled source changed')
                    task.holder['cancelled']=True
                actual(payload)
            with self.subTest(kind=kind),patch.object(cover,'check_sources',checkpoint),self.assertRaises((ValueError,RuntimeError)):
                cover.analyze(value['payload'],task)
            self.assertIsNone(value['result']);self.assertFalse(list(cover.artifact_folder(self.root,identity).glob('*.png')))
            self.manager.cancel(identity)

    def test_capture_off_event_loop_and_owner_switch_after_capture(self):
        actual=cover.capture;checks=[]
        def capture(*args):
            checks.append((threading.current_thread() is threading.main_thread(),self.env['LOCK'].locked()))
            result=actual(*args);self.env['set_active_project']('b');return result
        with patch.object(cover,'capture',capture),self.assertRaises(store.HTTPError):self.route('recipe_cover',self.body())
        self.assertEqual(checks,[(False,False)]);self.assertFalse(self.manager.values)

    def test_long_form_admitted_and_excessive_dependency_graph_refused(self):
        project=self.project();project['sequences'][0]['duration']=2*3600
        payload=cover.capture(project,self.body(time=3500),self.current(),self.root,ROOT/'assets')
        self.assertEqual(payload['work']['seconds'],7200)
        project['sequences'][0]['tracks'][0]['clips'][0].update(sequence_id='s',media_id=None)
        with self.assertRaisesRegex(ValueError,'cycle'):cover.capture(project,self.body(),self.current(),self.root,ROOT/'assets')

    def test_running_task_cancel_joins_owned_worker_and_never_publishes(self):
        entered=threading.Event();finished=threading.Event()
        def blocked(*args,context=None,**kwargs):
            entered.set()
            try:
                while True:context.check_cancelled();time.sleep(.005)
            finally:finished.set()
        manager=TaskManager(self.root,{'cover':cover.analyze},workers=1,start=True);self.addCleanup(manager.shutdown);self.env['TASKS']=manager
        with patch('render.render_frame',blocked):
            identity=self.route('recipe_cover',self.body())['task']['id'];self.assertTrue(entered.wait(5));manager.cancel(identity)
            self.assertTrue(finished.wait(5));self.assertTrue(manager.shutdown())
        self.assertEqual(manager.get(identity)['record']['status'],'cancelled');self.assertIsNone(manager.get(identity)['result'])
        self.assertFalse(list(cover.artifact_folder(self.root,identity).glob('*.png')))

    def test_watchdog_bounds_stalled_render_and_joins_before_artifact_cleanup(self):
        identity=self.route('recipe_cover',self.body())['task']['id'];value=self.manager.get(identity);task=TaskContext(self.manager,identity);finished=[]
        def stalled(*args,context=None,**kwargs):
            try:
                while True:context.check_cancelled();time.sleep(.005)
            finally:finished.append(True)
        started=time.monotonic()
        with patch('render.render_frame',stalled),patch.object(cover,'MAX_RUNTIME',.01),self.assertRaisesRegex(ValueError,'budget'):
            cover.analyze(value['payload'],task)
        self.assertLess(time.monotonic()-started,3);self.assertEqual(finished,[True]);self.assertIsNone(value['result'])
        self.assertFalse(list(cover.artifact_folder(self.root,identity).glob('*.png')))
        self.assertFalse(any(t.name=='Filmocity cover budget' for t in threading.enumerate()))

    def test_checked_ffmpeg_failure_leaves_no_ready_artifact(self):
        self.source.write_bytes(b'This is not a media stream')
        identity=self.route('recipe_cover',self.body())['task']['id'];value=self.manager.get(identity)
        with self.assertRaises((RuntimeError,ValueError)):
            cover.analyze(value['payload'],TaskContext(self.manager,identity))
        self.assertIsNone(value['result']);self.assertFalse(list(cover.artifact_folder(self.root,identity).glob('*.png')))

    def test_result_context_and_artifact_links_are_not_trusted(self):
        identity=self.ready();value=self.manager.get(identity);changed=copy.deepcopy(value['result']);changed['context']['project']='foreign'
        with self.assertRaises(ValueError):cover.verify_result(value['payload'],changed,identity)
        changed=copy.deepcopy(value['result']);changed['covers'][0]['url']='/renders/unowned.png'
        with self.assertRaises(ValueError):cover.verify_result(value['payload'],changed,identity)

    def test_download_growth_race_is_bounded_and_owner_is_rechecked_after_read(self):
        identity=self.ready();value=self.manager.get(identity);path=cover.verify_result(value['payload'],value['result'],identity)[0]
        raw=path.read_bytes();actual=Path.open;reads=[]
        class Grown(io.BytesIO):
            def read(self, size=-1):reads.append(size);return super().read(size)
        def changed(instance,*args,**kwargs):
            if instance==path and args==('rb',):return Grown(raw+b'changed after verification')
            return actual(instance,*args,**kwargs)
        with patch.object(Path,'open',changed),self.assertRaises(store.HTTPError):self.download(identity)
        self.assertEqual(reads,[len(raw)+1])
        original=self.project();self.env['set_active_project']('b');self.env['save_project'](original);foreign=self.raw();self.env['set_active_project']('a')
        class Switched(io.BytesIO):
            def read(self,size=-1):self_owner.env['set_active_project']('b');return super().read(size)
        self_owner=self
        def switch(instance,*args,**kwargs):
            if instance==path and args==('rb',):return Switched(raw)
            return actual(instance,*args,**kwargs)
        with patch.object(Path,'open',switch),self.assertRaises(store.HTTPError):self.download(identity)
        self.assertEqual(self.raw(),foreign)


if __name__=='__main__':unittest.main()
