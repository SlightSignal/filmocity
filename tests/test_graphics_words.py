"""Word splitting preserves editorial payload and rendered layer ownership."""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from PIL import Image, ImageChops, ImageFilter

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT/'backend')]
import graphics_words as words
import text_metrics
from render import render_frame, layer_kf_chain
from render_context import RenderContext


def fixture(text='Apple gig Red', **style):
    layer = {'kind':'text','text':text,'size':40,'color':'white',
             'font_resource':{'path':str(ROOT/'assets/fonts/DejaVuSans-Bold.ttf'),'family':'','weight':'bold'},**style}
    clip = {'id':'c','start':0,'in_':0,'out':4,'graphic':{'name':'Keep graphic name','layers':[layer]}}
    sequence = {'id':'s','name':'Original','width':640,'height':180,'fps':30,
                'tracks':[{'id':'V1','kind':'video','index':0,'clips':[clip]}],'markers':[],'captions':[]}
    return {'version':3,'media':{},'sequences':[sequence]},clip


def plan(project, **options):
    return words.plan(project,{'sequence':'s','clip_id':'c','layer':0,'anim':{'type':'none'},'stagger':0,**options})


class GraphicsWords(unittest.TestCase):
    def test_full_clip_and_unrelated_tracks_are_unchanged_and_one_precise_op(self):
        project,clip=fixture('One two three')
        clip.update(start=3,in_=2,out=10,reverse=True,hold=True,speed=2,note='Keep',group='group',markers=[{'t':1,'name':'Mark'}],
                    fade_window={'version':1,'start':2},source_edit_window={'version':1,'ramp':{'points':[]}},rendered_from={'clip_id':'old'})
        project['sequences'][0]['tracks'].append({'id':'locked','kind':'video','index':2,'locked':True,'clips':[{'id':'overlap','start':0,'in_':0,'out':20}]})
        before=copy.deepcopy(project);result=plan(project);changed=result['ops'][0]['value']
        self.assertEqual(project,before);self.assertEqual(result['ops'][0]['path'],'/sequences/0/tracks/0/clips/0');self.assertEqual(len(result['ops']),1)
        self.assertEqual({k:v for k,v in changed.items() if k!='graphic'},{k:v for k,v in clip.items() if k!='graphic'})
        self.assertEqual(result['summary']['duration'],8);self.assertEqual(result['layers'],3)
        changed['graphic']['layers'][0]['font_resource']['path']='changed'
        self.assertEqual(project,before)

    def test_following_curves_move_with_layer_and_selected_curves_copy_clock(self):
        project,clip=fixture('One two three');clip['graphic']['layers'].append({'kind':'text','text':'KEEP LABEL','size':20})
        clip['keyframes']={'g0.opacity':[{'t':-.5,'v':.2,'ease':'linear'},{'t':2,'v':1}],
                          'g0.x':[{'t':0,'v':3},{'t':2,'v':40}], 'g1.x':[{'t':0,'v':0},{'t':2,'v':80}],
                          'opacity':[{'t':0,'v':.5},{'t':2,'v':1}]}
        result=plan(project)['ops'][0]['value'];curves=result['keyframes']
        for index in range(3):
            self.assertEqual(curves[f'g{index}.opacity'],clip['keyframes']['g0.opacity'])
            self.assertEqual(curves[f'g{index}.x'],clip['keyframes']['g0.x'])
        self.assertEqual(result['graphic']['layers'][3]['text'],'KEEP LABEL')
        self.assertEqual(curves['g3.x'],clip['keyframes']['g1.x']);self.assertEqual(curves['opacity'],clip['keyframes']['opacity'])
        with RenderContext() as context:
            before=layer_kf_chain(clip['keyframes'],1,640,180,4,30,context=context)
            after=layer_kf_chain(curves,3,640,180,4,30,context=context)
        self.assertEqual(before,after)

    def test_empty_and_single_word_are_noops_even_with_existing_effects(self):
        for text in ('','  ','Only'):
            project,clip=fixture(text,box=True);clip['keyframes']={'g0.opacity':[{'t':0,'v':.5}]}
            before=copy.deepcopy(project);result=plan(project)
            self.assertEqual(result['ops'],[]);self.assertFalse(result['summary']['changed']);self.assertEqual(project,before)

    def test_lock_ambiguous_owner_and_invalid_layer_refuse(self):
        project,clip=fixture();project['sequences'][0]['tracks'][0]['locked']=True
        with self.assertRaisesRegex(ValueError,'Unlock'):plan(project)
        project['sequences'][0]['tracks'][0]['locked']=False;project['sequences'][0]['tracks'][0]['clips'].append(copy.deepcopy(clip))
        with self.assertRaisesRegex(ValueError,'ambiguous'):plan(project)
        project,clip=fixture()
        for index in (-1,True,1,1.0):
            with self.subTest(index=index),self.assertRaisesRegex(ValueError,'existing text layer'):plan(project,layer=index)

    def test_stagger_uses_ramp_and_held_duration_and_preserves_exit(self):
        project,clip=fixture('One two three');clip.update(out=8,time_remap=[{'t':0,'v':1},{'t':2,'v':3}])
        clip['graphic']['layers'][0].update(anim_in={'type':'fade','duration':.2,'delay':.1},anim_out={'type':'fade','duration':.2,'delay':.05})
        result=plan(project,anim={'type':'pop','duration':.35,'ease':'back_out'},stagger=.12)
        self.assertAlmostEqual(result['summary']['duration'],10/3)
        self.assertEqual([layer['anim_in']['delay'] for layer in result['ops'][0]['value']['graphic']['layers']],[.1,.22,.33999999999999997])
        self.assertTrue(any('replaces' in message for message in result['summary']['warnings']))
        for layer in result['ops'][0]['value']['graphic']['layers']:self.assertEqual(layer['anim_out'],clip['graphic']['layers'][0]['anim_out'])
        with self.assertRaisesRegex(ValueError,'exceeds|overlap'):plan(project,anim={'type':'pop','duration':1},stagger=1.5)
        clip.update(hold=True,speed=9)
        self.assertEqual(plan(project,anim={'type':'fade','duration':1},stagger=2)['summary']['duration'],8)

    def test_preserves_whitespace_and_uppercase_word_spelling(self):
        project,_=fixture('  Mix   Stra\u00dfe  Élan ',uppercase=True)
        result=plan(project)['ops'][0]['value']['graphic']['layers']
        self.assertEqual([layer['text'] for layer in result],['Mix','Stra\u00dfe','Élan']);self.assertTrue(all(layer['uppercase'] for layer in result))
        single,_=fixture('Mix Stra\u00dfe Élan',uppercase=True)
        self.assertNotEqual([layer['x'] for layer in result],[layer['x'] for layer in plan(single)['ops'][0]['value']['graphic']['layers']])

    def test_unsupported_aggregate_and_shaping_semantics_refuse_atomically(self):
        cases=[('\nOne two',{}),('One\ttwo',{}),('One two',{'vertical':True}),('One two',{'box':True}),('One two',{'glow':True}),
               ('One two',{'blur':2}),('One two',{'fix_bounds':True}),('One two',{'letter_spacing':2}),('One %{n}',{}),('שלום עולם',{})]
        for text,style in cases:
            with self.subTest(text=text,style=style):
                project,_=fixture(text,**style);before=copy.deepcopy(project)
                with self.assertRaises(ValueError):plan(project)
                self.assertEqual(project,before)
        project,clip=fixture();clip['keyframes']={'g0.unimplemented':[{'t':0,'v':1}]}
        with self.assertRaisesRegex(ValueError,'cannot be preserved'):plan(project)

    def test_strict_finite_limits_and_expansion_are_checked_before_copying(self):
        for options in ({'stagger':True},{'stagger':float('nan')},{'anim':{'type':'unknown'}},{'anim':{'type':'fade','duration':0}},{'anim':{'type':'fade','ease':'unknown'}}):
            with self.subTest(options=options),self.assertRaises(ValueError):plan(fixture()[0],**options)
        project,_=fixture('word '*101)
        with self.assertRaisesRegex(ValueError,'100 words'):plan(project)
        project,clip=fixture(' '.join(['Word']*100));clip['keyframes']={'g0.opacity':[{'t':i/100,'v':1} for i in range(400)]}
        with self.assertRaisesRegex(ValueError,'copied points'):plan(project)
        project,clip=fixture(' '.join(['Word']*100));clip['out']=600
        with self.assertRaisesRegex(ValueError,'control commands'):plan(project,anim={'type':'pop','duration':.35})

    def test_resource_and_fingerprint_are_stable_and_font_failure_is_clear(self):
        project,clip=fixture();first=plan(project);second=plan(project)
        self.assertEqual(first,second);self.assertEqual(words.resources(project,{'sequence':'s','clip_id':'c'}),[str(ROOT/'assets/fonts/DejaVuSans-Bold.ttf')])
        clip['graphic']['layers'][0]['font_resource']['path']='/missing-font.ttf'
        with self.assertRaisesRegex(ValueError,'font'):plan(project)

    def test_omitted_animation_preserves_existing_and_empty_object_clears(self):
        project,clip=fixture();clip['graphic']['layers'][0]['anim_in']={'type':'slide_left','duration':.7,'delay':.1,'distance':.1}
        result=words.plan(project,{'sequence':'s','clip_id':'c','stagger':0})
        for layer in result['ops'][0]['value']['graphic']['layers']:
            self.assertEqual(layer['anim_in'],clip['graphic']['layers'][0]['anim_in'])
        self.assertFalse(any('replaces' in warning for warning in result['summary']['warnings']))
        cleared=plan(project,anim={})
        self.assertTrue(all(layer['anim_in']['type']=='none' for layer in cleared['ops'][0]['value']['graphic']['layers']))
        self.assertTrue(any('replaces' in warning for warning in cleared['summary']['warnings']))
        clip['graphic']['layers'][0]['anim_in']={'type':'typewriter','duration':1}
        with self.assertRaisesRegex(ValueError,'typewriter'):words.plan(project,{'sequence':'s','clip_id':'c'})
        self.assertTrue(plan(project,anim={'type':'fade','duration':.2})['summary']['changed'])

    def test_curve_expression_and_handles_refuse_unsafe_or_ambiguous_data(self):
        for key,points in [('g00.x',[{'t':0,'v':1}]),('g5.x',[{'t':0,'v':1}]),
                           ('g0.x',[{'t':i/100,'v':i,'e':'bezier'} for i in range(25)]),
                           ('g0.x',[{'t':0,'v':1,'e':'bezier','o':[.3]}]),
                           ('g0.x',[{'t':1,'v':1},{'t':1,'v':2}])]:
            with self.subTest(key=key):
                project,clip=fixture();clip['keyframes']={key:points};before=copy.deepcopy(project)
                with self.assertRaises(ValueError):plan(project)
                self.assertEqual(project,before)

    def test_large_layer_metadata_refuses_before_per_word_copy(self):
        project,clip=fixture(' '.join(['Word']*100));layer=clip['graphic']['layers'][0]
        layer['editorial_metadata']={'preserve':'x'*(3*1024*1024)}
        before=json.dumps(project,sort_keys=True);original=copy.deepcopy;selected_copies=[]
        def guarded(value,*args,**kwargs):
            if value is layer:
                selected_copies.append(True)
                raise AssertionError('Oversized expansion reached the per-word copy loop')
            return original(value,*args,**kwargs)
        with patch.object(words.copy,'deepcopy',side_effect=guarded),self.assertRaisesRegex(ValueError,'metadata expansion'):
            plan(project)
        self.assertEqual(selected_copies,[]);self.assertEqual(json.dumps(project,sort_keys=True),before)

    def render(self,project,path):
        with RenderContext(scratch_parent=str(path.parent)) as context:render_frame(project,'s',1,str(path),context=context)
        with Image.open(path) as image:return image.convert('RGB')

    def test_actual_font_render_keeps_baseline_and_placement(self):
        with tempfile.TemporaryDirectory() as folder:
            for align in ('left','center','right'):
                for valign in ('top','center','bottom'):
                    with self.subTest(align=align,valign=valign):
                        project,_=fixture('Apple gig Red',align=align,valign=valign,x=3,y=-2,baseline_dy=4)
                        changed=copy.deepcopy(project);changed['sequences'][0]['tracks'][0]['clips'][0]=plan(project)['ops'][0]['value']
                        before=self.render(project,Path(folder)/'before.png');after=self.render(changed,Path(folder)/'after.png')
                        # Integer layer positions cannot retain drawtext's
                        # fractional glyph phase. Every glyph must remain
                        # within one pixel of its original silhouette, with
                        # the same baseline and no omitted text.
                        self.assertTrue(all(abs(a-b)<=1 for a,b in zip(before.getbbox(),after.getbbox())),(before.getbbox(),after.getbbox()))
                        a=before.convert('L').point(lambda x:255 if x>32 else 0)
                        b=after.convert('L').point(lambda x:255 if x>32 else 0)
                        self.assertIsNone(ImageChops.subtract(a,b.filter(ImageFilter.MaxFilter(3))).getbbox())
                        self.assertIsNone(ImageChops.subtract(b,a.filter(ImageFilter.MaxFilter(3))).getbbox())
                        # Integrated antialiased coverage is more stable than
                        # thresholded edge-pixel counts across fractional phase.
                        aa=before.convert('L');bb=after.convert('L')
                        self.assertLess(abs(sum(aa.tobytes())-sum(bb.tobytes()))/sum(aa.tobytes()),.02)

    def test_actual_multiline_baselines_and_order_are_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            for text in ('Apple\ngig Red','aaaa\nHHH Red','Apple\n\ngig Red'):
                for valign in ('top','center','bottom'):
                    with self.subTest(text=text,valign=valign):
                        project,_=fixture(text,align='left',valign=valign);project['sequences'][0]['height']=300
                        changed=copy.deepcopy(project);changed['sequences'][0]['tracks'][0]['clips'][0]=plan(project)['ops'][0]['value']
                        before=self.render(project,Path(folder)/'before.png');after=self.render(changed,Path(folder)/'after.png')
                        self.assertTrue(all(abs(a-b)<=1 for a,b in zip(before.getbbox(),after.getbbox())),(before.getbbox(),after.getbbox()))
                        a=before.convert('L').point(lambda x:255 if x>32 else 0);b=after.convert('L').point(lambda x:255 if x>32 else 0)
                        self.assertIsNone(ImageChops.subtract(a,b.filter(ImageFilter.MaxFilter(3))).getbbox())
                        self.assertIsNone(ImageChops.subtract(b,a.filter(ImageFilter.MaxFilter(3))).getbbox())

    def test_actual_shaped_kerning_and_ligatures_keep_word_positions(self):
        # BASIC Pillow metrics can appear correct for plain text while drifting
        # on kerning pairs and ligatures. Match the renderer's own shaping.
        with tempfile.TemporaryDirectory() as folder:
            for align in ('left','center','right'):
                with self.subTest(align=align):
                    project,_=fixture('AVATAR office Toffee',align=align,valign='center')
                    changed=copy.deepcopy(project);changed['sequences'][0]['tracks'][0]['clips'][0]=plan(project)['ops'][0]['value']
                    before=self.render(project,Path(folder)/'before.png');after=self.render(changed,Path(folder)/'after.png')
                    self.assertTrue(all(abs(a-b)<=1 for a,b in zip(before.getbbox(),after.getbbox())),(before.getbbox(),after.getbbox()))
                    a=before.convert('L').point(lambda x:255 if x>32 else 0);b=after.convert('L').point(lambda x:255 if x>32 else 0)
                    self.assertIsNone(ImageChops.subtract(a,b.filter(ImageFilter.MaxFilter(3))).getbbox())
                    self.assertIsNone(ImageChops.subtract(b,a.filter(ImageFilter.MaxFilter(3))).getbbox())
                    self.assertLess(abs(sum(before.convert('L').tobytes())-sum(after.convert('L').tobytes()))/sum(before.convert('L').tobytes()),.02)

    def test_font_cache_hashes_same_size_replacement_and_measures_owned_snapshot(self):
        bold=ROOT/'assets/fonts/DejaVuSans-Bold.ttf'; regular=ROOT/'assets/fonts/DejaVuSans.ttf'
        text='AVATAR office Toffee'
        expected_bold=text_metrics.measure(str(bold),40,[text])[text]
        expected_regular=text_metrics.measure(str(regular),40,[text])[text]
        self.assertNotEqual(expected_bold,expected_regular)
        first,second=bold.read_bytes(),regular.read_bytes(); length=max(len(first),len(second))
        first+=b'\0'*(length-len(first));second+=b'\0'*(length-len(second))
        with tempfile.TemporaryDirectory(prefix='Filmocity font É ') as folder:
            path=Path(folder)/'selected.ttf';path.write_bytes(first);before=path.stat();calls=[]
            actual=text_metrics.subprocess.Popen
            def replace_during_launch(command,**options):
                if '-frames:v' in command:
                    calls.append(options.get('cwd'))
                    path.write_bytes(second);os.utime(path,ns=(before.st_atime_ns,before.st_mtime_ns))
                return actual(command,**options)
            with text_metrics._LOCK:text_metrics._CACHE.clear()
            with patch.object(text_metrics,'RenderContext',side_effect=lambda:RenderContext(scratch_parent=folder)),patch.object(text_metrics.subprocess,'Popen',side_effect=replace_during_launch):
                observed=text_metrics.measure(str(path),40,[text])[text]
            self.assertEqual(observed,expected_bold)
            after=path.stat();self.assertEqual((before.st_size,before.st_mtime_ns),(after.st_size,after.st_mtime_ns))
            with patch.object(text_metrics,'RenderContext',side_effect=lambda:RenderContext(scratch_parent=folder)):
                self.assertEqual(text_metrics.measure(str(path),40,[text])[text],expected_regular)
            if os.name=='nt':self.assertTrue(calls and all(cwd and 'É' in cwd for cwd in calls))
            self.assertFalse(list(Path(folder).glob('filmocity-render-*')))

    def test_shaped_measurement_cancel_reaps_direct_child_and_unicode_scratch(self):
        holder={};errors=[];children=[];ready=threading.Event();contexts=[]
        with tempfile.TemporaryDirectory(prefix='Filmocity metrics É ') as folder:
            actual=text_metrics.subprocess.Popen
            def launch(command,**options):
                child=actual(['ffmpeg','-nostdin','-v','error','-re','-f','lavfi','-i','anullsrc=r=8000:cl=mono','-t','60','-f','null','-'],**options)
                children.append(child);ready.set();return child
            def context():
                ctx=RenderContext(proc_holder=holder,scratch_parent=folder);contexts.append(ctx);return ctx
            def operation():
                try:text_metrics.measure(str(ROOT/'assets/fonts/DejaVuSans-Bold.ttf'),40,['cancel me'])
                except BaseException as error:errors.append(error)
            with text_metrics._LOCK:text_metrics._CACHE.clear()
            with patch.object(text_metrics,'RenderContext',side_effect=context),patch.object(text_metrics.subprocess,'Popen',side_effect=launch):
                worker=threading.Thread(target=operation);worker.start()
                try:
                    self.assertTrue(ready.wait(5));holder['cancelled']=True
                finally:
                    holder['cancelled']=True;worker.join(12)
                self.assertFalse(worker.is_alive())
            self.assertTrue(errors);self.assertRegex(str(errors[0]),'cancelled')
            self.assertTrue(children and all(child.poll() is not None for child in children));self.assertNotIn('proc',holder)
            self.assertNotIn('scratch_diagnostics',holder)
            self.assertTrue(contexts and all(not Path(ctx.root).exists() for ctx in contexts));self.assertFalse(list(Path(folder).glob('filmocity-render-*')))

    def test_actual_animated_following_label_keeps_its_pixels_and_owner(self):
        project,clip=fixture('Apple gig Red',valign='top',y=0);project['sequences'][0]['height']=300
        label=copy.deepcopy(clip['graphic']['layers'][0]);label.update(text='KEEP LABEL',size=24,y=170)
        clip['graphic']['layers'].append(label)
        clip['keyframes']={'g0.x':[{'t':0,'v':0},{'t':2,'v':20}],
                          'g0.opacity':[{'t':0,'v':.25},{'t':2,'v':1}],
                          'g1.x':[{'t':0,'v':0},{'t':2,'v':80}]}
        changed=copy.deepcopy(project);changed['sequences'][0]['tracks'][0]['clips'][0]=plan(project)['ops'][0]['value']
        with tempfile.TemporaryDirectory() as folder:
            before=self.render(project,Path(folder)/'before.png');after=self.render(changed,Path(folder)/'after.png')
            a=before.crop((0,160,640,300));b=after.crop((0,160,640,300))
            self.assertIsNotNone(a.getbbox());self.assertEqual(a.tobytes(),b.tobytes())
            a=before.crop((0,0,640,120)).convert('L');b=after.crop((0,0,640,120)).convert('L')
            self.assertLess(abs(sum(a.tobytes())-sum(b.tobytes()))/sum(a.tobytes()),.02)


if __name__=='__main__':unittest.main()
