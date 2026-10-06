"""Real saved-store recipe queue/review/apply with owned local FFmpeg assets."""
import array
import ast
import asyncio
import copy
import json
import shutil
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import recipe_workflow as recipe
from background_tasks import TaskManager,TaskContext,TaskError
from test_media_collection import run_async_check
import test_project_sync as store


class StoreRecipes(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp();audio=self.root/'speech.wav';values=array.array('h')
        for i in range(6*8000):
            value=0 if 1<=i/8000<2 else (6000 if i%80<40 else -6000);values.extend((value,-value))
        if sys.byteorder!='little':values.byteswap()
        with wave.open(str(audio),'wb') as stream:stream.setparams((2,2,8000,0,'NONE',''));stream.writeframes(values.tobytes())
        self.source=self.root/'speech.mkv'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:s=64x48:r=30:d=6','-i',str(audio),
            '-map','0:v','-map','1:a','-c:v','ffv1','-threads','1','-c:a','pcm_s16le',str(self.source)],check=True,capture_output=True,timeout=30)
        project=self.env['load_project']();project.update(version=3,media={'m':{'id':'m','name':'Speech','path':str(self.source),'duration':6,
            'width':64,'height':48,'fps':30,'frame_rate':'30/1','has_video':True,'has_audio':True,'channels':2,'sample_rate':8000}},sequences=[{
                'id':'s','name':'Original sequence','width':64,'height':48,'fps':30,'markers':[{'id':'old','time':.1,'name':'Keep'}],
                'tracks':[{'id':'v','kind':'video','index':0,'clips':[{'id':'c','media_id':'m','start':0,'in_':0,'out':6,'speed':1,
                    'audio':{'gain_db':-4},'markers':[{'t':.5,'name':'Source marker'}],'note':'Retained'}]},
                    {'id':'v2','kind':'video','index':1,'clips':[]},{'id':'a','kind':'audio','index':0,'clips':[]},{'id':'a2','kind':'audio','index':1,'clips':[]}]}])
        self.env['save_project'](project);self.manager=TaskManager(self.root,{'recipe':recipe.analyze},start=False);self.addCleanup(self.manager.shutdown)
        self.env.update(asyncio=asyncio,TASKS=self.manager,TaskError=TaskError,ASSETS=str(ROOT/'assets'))
        names={'recipe_talking_head','recipe_reel','_queue_recipe_workflow','_task_recipe_workflow','_review_recipe_workflow','background_recipe_review',
            '_apply_recipe_workflow','_audio_edit_policy','_owned_task','background_task_apply','background_task_retry','_workflow_capture','_workflow_commit','_owned_render_thread'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)

    def route(self,name,body,identity=None):return run_async_check(self.env[name](*(([identity] if identity else [])+[store.Request(body)])))
    def project(self):return self.env['load_project']()
    def body(self,mode='talking_head',**kw):
        choices={'clip_id':'c','silences':False,'punch_every':3,'voice_preset':False,'captions':False,'broll':[]} if mode=='talking_head' else {
            'shots':['m','m'],'music':None,'target':6,'hook':'','cta':'','captions':False,'sfx':True,'name':'Owned Reel','canvas':'portrait','rhythm':'even'}
        return {'_context':self.current(),'sequence':'s','request_id':'a'*32,**choices,**kw}
    def ready(self,mode='talking_head',**kw):
        queued=self.route('recipe_'+mode,self.body(mode,**kw));identity=queued['task']['id'];value=self.manager.values[identity]
        value['result']=self.env['_task_recipe_workflow'](value['payload'],TaskContext(self.manager,identity));value['record']['status']='ready';self.manager.store.save(value)
        return identity
    def review(self,identity):return self.route('background_recipe_review',{'_context':self.current()},identity)
    def apply(self,identity,review,**kw):return self.route('background_task_apply',{'_context':review['context'],'fingerprint':review['plan']['fingerprint'],**kw},identity)

    def test_queue_ready_review_never_edit_and_restart_retains_result(self):
        before=self.raw();identity=self.ready();review=self.review(identity)
        self.assertEqual(self.raw(),before);self.assertTrue(review['plan']['ops']);self.assertEqual(review['result']['kind'],'recipe')
        self.assertEqual(self.route('recipe_talking_head',self.body())['task']['id'],identity)
        restored=TaskManager(self.root,{'recipe':recipe.analyze},start=False);self.addCleanup(restored.shutdown)
        self.assertEqual(restored.get(identity)['record']['status'],'ready')

    def test_talking_head_one_saved_undo_redo_and_idempotent_lost_receipt(self):
        identity=self.ready();review=self.review(identity);before=self.project();actual=self.manager.store.save
        def failed(value):
            if value['record']['status']=='applied':raise OSError('lost task receipt')
            return actual(value)
        with patch.object(self.manager.store,'save',failed):self.apply(identity,review)
        after=self.project();self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        restored=TaskManager(self.root,{'recipe':recipe.analyze},start=False);self.addCleanup(restored.shutdown);self.env['TASKS']=restored
        self.route('background_task_apply',{'_context':self.current()},identity);self.assertEqual(self.project(),after)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_reel_stages_sound_then_registers_new_sequence_and_media_in_one_commit(self):
        before=self.project();identity=self.ready('reel');review=self.review(identity);result=review['result']
        self.assertEqual(self.project(),before);self.assertTrue(Path(result['sfx']['path']).is_file());self.assertFalse(Path(result['sfx_media']['path']).exists())
        saved=self.apply(identity,review);after=self.project();self.assertNotEqual(saved['sequence'],'s');self.assertEqual(after['sequences'][0],before['sequences'][0])
        new=next(s for s in after['sequences'] if s['id']==saved['sequence']);self.assertEqual((new['width'],new['height']),(1080,1920))
        self.assertEqual(new['name'],'Owned Reel');self.assertIn(result['sfx_media']['id'],after['media']);self.assertTrue(Path(result['sfx_media']['path']).is_file())
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences']);self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_real_rms_silence_result_is_reviewed_before_closing_time(self):
        identity=self.ready(silences=True,punch_every=0);before=self.project();review=self.review(identity)
        self.assertTrue(review['result']['analysis']['silences']['silences']);self.assertEqual(self.project(),before)
        self.apply(identity,review);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.assertLess(max(c['start']+(c['out']-c['in_'])/c.get('speed',1) for c in self.project()['sequences'][0]['tracks'][0]['clips']),6)

    def test_foreign_stale_locked_and_source_changed_reviews_refuse_without_mutation(self):
        identity=self.ready();review=self.review(identity);self.env['set_active_project']('b');before=self.raw('b')
        with self.assertRaises(store.HTTPError):self.apply(identity,review)
        self.assertEqual(self.raw('b'),before);self.env['set_active_project']('a');self.edit('Unrelated',self.current());before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(identity,review)
        self.assertEqual(self.raw(),before);self.review(identity)
        project=self.project();project['sequences'][0]['tracks'][0]['locked']=True;self.env['save_project'](project)
        with self.assertRaises(store.HTTPError):self.review(identity)
        project['sequences'][0]['tracks'][0]['locked']=False;self.env['save_project'](project)
        with self.source.open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(store.HTTPError):self.review(identity)

    def test_invalid_nonfinite_negative_interval_and_missing_context_never_queue(self):
        for mode,extra in [('talking_head',{'punch_every':-1}),('talking_head',{'punch_every':float('nan')}),('reel',{'target':'inf'}),('reel',{'target':float('inf')})]:
            before=self.raw()
            with self.subTest(mode=mode,extra=extra),self.assertRaises(store.HTTPError):self.route('recipe_'+mode,self.body(mode,**extra))
            self.assertEqual(self.raw(),before)
        body=self.body();body.pop('_context')
        with self.assertRaises(store.HTTPError):self.route('recipe_talking_head',body)
        self.assertFalse(self.manager.values)

    def test_capture_after_await_rechecks_project_and_keeps_loop_responsive(self):
        actual=recipe.capture;started,release=threading.Event(),threading.Event()
        def delayed(*a):started.set();release.wait(5);return actual(*a)
        async def scenario():
            task=asyncio.create_task(self.env['recipe_talking_head'](store.Request(self.body())))
            for _ in range(1000):
                if started.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set());self.assertFalse(task.done());self.env['set_active_project']('b');release.set()
            with self.assertRaises(store.HTTPError):await task
            self.assertFalse(self.manager.values)
        with patch.object(recipe,'capture',delayed):run_async_check(scenario())

    def test_publish_race_cannot_commit_to_changed_project(self):
        identity=self.ready('reel');review=self.review(identity);before_a,before_b=self.raw(),self.raw('b');actual=recipe.publish
        def changed(*a,**kw):actual(*a,**kw);self.env['set_active_project']('b')
        with patch.object(recipe,'publish',changed),self.assertRaises(store.HTTPError):self.apply(identity,review)
        self.assertEqual(self.raw('a'),before_a);self.assertEqual(self.raw('b'),before_b);self.assertEqual(self.manager.get(identity)['record']['status'],'ready')

    def test_policy_and_disk_failure_do_not_partially_commit(self):
        identity=self.ready();review=self.review(identity);before=self.raw();(self.root/'settings.json').write_text(json.dumps({'agent_mode':'proposals_only'}))
        with self.assertRaises(store.HTTPError):self.apply(identity,review,actor='agent')
        with patch.dict(self.env,commit_pair=lambda *a,**kw:(_ for _ in ()).throw(OSError('full'))),self.assertRaises(OSError):self.apply(identity,review)
        self.assertEqual(self.raw(),before);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),0)

    def test_cancel_retry_and_derived_media_completion_keep_captured_identity(self):
        identity=self.ready();project=self.project();project['media']['m'].update(proxy='/tmp/proxy.mp4',thumb='/tmp/thumb.png',status='ready');self.env['save_project'](project)
        self.assertTrue(self.review(identity)['plan']['ops']);self.assertEqual(self.route('recipe_talking_head',self.body())['task']['id'],identity)
        self.manager.cancel(identity)
        with self.assertRaises(store.HTTPError):self.review(identity)
        reply=self.route('background_task_retry',{'_context':self.current()},identity);self.assertNotEqual(reply['task']['id'],identity)

    def test_generated_artifact_changes_refuse_review_and_publish(self):
        identity=self.ready('reel');value=self.manager.get(identity);path=Path(value['result']['sfx']['path']);path.write_bytes(b'changed');before=self.raw()
        with self.assertRaises(store.HTTPError):self.review(identity)
        with self.assertRaises(ValueError):recipe.publish(value['payload'],value['result'],identity)
        self.assertEqual(self.raw(),before)

    def test_final_worker_cancel_or_source_change_retires_owned_sound(self):
        for failure,token in (('cancel','a'),('source','b')):
            identity=self.route('recipe_reel',self.body('reel',request_id=token*32))['task']['id']
            value=self.manager.values[identity];task=TaskContext(self.manager,identity);actual=recipe.check_sources;calls=[]
            def changed(payload):
                calls.append(None)
                if len(calls)==2:
                    if failure=='cancel':task.holder['cancelled']=True
                    else:
                        with self.source.open('ab') as stream:stream.write(b'changed')
                actual(payload)
            with self.subTest(failure=failure),patch.object(recipe,'check_sources',changed),self.assertRaises((RuntimeError,ValueError)):
                recipe.analyze(value['payload'],task)
            self.assertEqual(len(calls),2)
            self.assertIsNone(value['result']);self.assertFalse((recipe.artifact_folder(self.root,identity)/'whoosh.wav').exists())
            self.manager.cancel(identity)

    def test_single_shot_reel_does_not_generate_unused_sound(self):
        identity=self.ready('reel',shots=['m']);review=self.review(identity)
        self.assertIsNone(review['result']['sfx']);self.assertIsNone(review['result']['sfx_media'])
        self.assertFalse((self.root/'tasks'/'recipes'/identity/'whoosh.wav').exists())

    def test_measured_onsets_refuse_weak_music_without_even_fallback(self):
        identity=self.route('recipe_reel',self.body('reel',music='m',rhythm='onsets'))['task']['id'];value=self.manager.values[identity];before=self.raw()
        with self.assertRaisesRegex(ValueError,'onsets|candidates|irregular|changing'):
            recipe.analyze(value['payload'],TaskContext(self.manager,identity))
        self.assertEqual(self.raw(),before);self.assertIsNone(value['result']);self.assertFalse((self.root/'media'/'recipes').exists())

    def test_measured_onsets_music_cards_and_source_hashes_survive_full_reel_apply(self):
        path=self.root/'music.wav';values=array.array('h')
        for i in range(8*8000):
            value=(8000 if i%20<10 else -8000) if i%4000<400 else 0;values.extend((value,-value))
        if sys.byteorder!='little':values.byteswap()
        with wave.open(str(path),'wb') as stream:stream.setparams((2,2,8000,0,'NONE',''));stream.writeframes(values.tobytes())
        p=self.project();p['media']['music']={'id':'music','path':str(path),'name':'Pulses','duration':8,'has_audio':True,'has_video':False,'sample_rate':8000,'channels':2};self.env['save_project'](p)
        source_hash=recipe.file_hash(path);identity=self.ready('reel',music='music',rhythm='onsets',target=8,hook='A hook',cta='Subscribe',captions=True)
        reviewed=self.review(identity);measurement=reviewed['result']['analysis']['onsets']['measurements'][0];self.assertGreaterEqual(len(measurement['beats']),5)
        result=self.apply(identity,reviewed);new=next(s for s in self.project()['sequences'] if s['id']==result['sequence'])
        self.assertTrue(new['captions']);self.assertTrue(any(c.get('graphic') for t in new['tracks'] for c in t['clips']))
        self.assertTrue(any(c.get('media_id')=='music' for t in new['tracks'] for c in t['clips']));self.assertEqual(recipe.file_hash(path),source_hash)

    def test_tampered_fingerprint_and_post_publish_source_change_do_not_commit(self):
        identity=self.ready('reel');review=self.review(identity);before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(identity,review,fingerprint='wrong')
        self.assertEqual(self.raw(),before);actual=recipe.publish
        def changed(*args,**kw):
            actual(*args,**kw)
            with self.source.open('ab') as stream:stream.write(b'changed')
        with patch.object(recipe,'publish',changed),self.assertRaises(ValueError):self.apply(identity,review)
        self.assertEqual(self.raw(),before);self.assertEqual(self.manager.get(identity)['record']['status'],'ready')

    def test_noop_recipe_has_no_receipt_or_history_commit(self):
        identity=self.ready(punch_every=0);review=self.review(identity);before=self.raw()
        self.assertEqual(review['plan']['ops'],[]);self.assertFalse(self.apply(identity,review)['changed'])
        self.assertEqual(self.raw(),before);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),0)

    def test_captured_template_and_voice_preset_changes_invalidate_review(self):
        identity=self.ready('reel',hook='A hook',sfx=False);review=self.review(identity)
        template=copy.deepcopy(self.manager.get(identity)['payload']['templates']['hook']);template['review_changed']=True
        (self.root/'settings.json').write_text(json.dumps({'graphics_templates':{'Hook — Big Statement':template}}))
        with self.assertRaises(store.HTTPError):self.review(identity)
        (self.root/'settings.json').unlink();assets=self.root/'asset-copy';(assets/'presets').mkdir(parents=True)
        source=ROOT/'assets'/'presets'/'effect_presets.json';target=assets/'presets'/'effect_presets.json';shutil.copyfile(source,target);self.env['ASSETS']=str(assets)
        identity=self.ready(voice_preset=True,request_id='b'*32);self.review(identity);presets=json.loads(target.read_text());presets['Voice — Clean-up']['afx_stack']=[];target.write_text(json.dumps(presets))
        with self.assertRaises(store.HTTPError):self.review(identity)

    def test_request_cancellation_joins_publication_before_resetting_task(self):
        identity=self.ready('reel');review=self.review(identity);before=self.raw();started,release,joined=threading.Event(),threading.Event(),threading.Event()
        def publication(*args,proc_holder=None):
            started.set();release.wait(5)
            try:
                if proc_holder.get('cancelled'):raise RuntimeError('cancelled')
            finally:joined.set()
        async def scenario():
            task=asyncio.create_task(self.env['background_task_apply'](identity,store.Request({'_context':review['context'],'fingerprint':review['plan']['fingerprint']})))
            for _ in range(1000):
                if started.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set());task.cancel();await asyncio.sleep(.005);self.assertFalse(task.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await task
        with patch.object(recipe,'publish',publication):run_async_check(scenario())
        self.assertTrue(joined.is_set());self.assertEqual(self.raw(),before);self.assertEqual(self.manager.get(identity)['record']['status'],'ready')

    def test_cancel_of_published_ready_result_is_not_revived_by_worker_return(self):
        published,release=threading.Event(),threading.Event()
        def handler(payload,task):
            result=recipe.analyze(payload,task);published.set();release.wait(5);return result
        manager=TaskManager(self.root,{'recipe':handler},workers=1)
        self.addCleanup(lambda:(release.set(),manager.shutdown()))
        payload=recipe.capture(self.project(),self.body(),'talking_head',self.current(),self.root,ROOT/'assets')
        identity=manager.submit('recipe','Talking Head',self.current(),payload)['id']
        self.assertTrue(published.wait(3));self.assertEqual(manager.get(identity)['record']['status'],'ready')
        manager.cancel(identity);release.set();self.assertTrue(manager.shutdown())
        self.assertEqual(manager.get(identity)['record']['status'],'cancelled');self.assertIsNotNone(manager.get(identity)['result'])

    def test_applied_published_result_is_not_reset_by_worker_return(self):
        published,release=threading.Event(),threading.Event()
        def handler(payload,task):
            result=recipe.analyze(payload,task);published.set();release.wait(5);return result
        manager=TaskManager(self.root,{'recipe':handler},workers=1);self.addCleanup(lambda:(release.set(),manager.shutdown()))
        payload=recipe.capture(self.project(),self.body(),'talking_head',self.current(),self.root,ROOT/'assets')
        identity=manager.submit('recipe','Talking Head',self.current(),payload)['id'];self.assertTrue(published.wait(3))
        manager.begin_apply(identity);manager.finish_apply(identity,success=True,message='Applied');release.set();self.assertTrue(manager.shutdown())
        self.assertEqual(manager.get(identity)['record']['status'],'applied')

    def test_generated_caption_fonts_are_captured_even_without_existing_captions(self):
        from project_resources import style_font
        font=self.root/'Caption font.ttf';shutil.copyfile(style_font({}),font)
        style={'font':'Owned Font','font_resource':{'family':'Owned Font','weight':'bold','path':str(font)}}
        identity=self.ready('reel',hook='A hook',captions=True,caption_style=style,sfx=False)
        payload=self.manager.get(identity)['payload'];self.assertIn(str(font),[row[0] for row in payload['resources']]);self.review(identity)
        with font.open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(store.HTTPError):self.review(identity)
        p=self.project();p['sequences'][0]['transcript']=[{'s':0,'e':.5,'w':'Speech'}];self.env['save_project'](p)
        identity=self.ready(captions=True,request_id='b'*32);payload=self.manager.get(identity)['payload']
        self.assertIn(str(Path(style_font({})).absolute()),[row[0] for row in payload['resources']]);self.assertTrue(self.review(identity)['plan']['ops'])

    def test_literal_source_braces_are_not_interpreted_as_template_brand_tokens(self):
        destination=self.root/'speech_{{font}}.mkv';self.source.rename(destination);self.source=destination
        p=self.project();p['media']['m']['path']=str(destination);p['brand']={'font':'Different Name'};self.env['save_project'](p)
        identity=self.ready();self.review(identity)
        self.assertIn(str(destination),[row[0] for row in self.manager.get(identity)['payload']['resources']])


if __name__=='__main__':unittest.main()
