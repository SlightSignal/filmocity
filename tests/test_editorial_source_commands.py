"""Actual saved editorial commands, font/PNG output and owned audio aliases."""
import array
import ast
import asyncio
import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import unittest
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import source_commands as commands
import media_preparation
import media_preview
import task_inputs
import render
from render_context import RenderContext
from background_tasks import TaskContext
from test_media_collection import run_async_check
import test_recipe_workflow as recipes
import test_project_sync as store


class StoreEditorialSourceCommands(recipes.StoreRecipes):
    for _name in dir(recipes.StoreRecipes):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        recipes.StoreRecipes.setUp(self)
        self.env.update(media_preparation=media_preparation,task_inputs=task_inputs,
            broadcast_threadsafe=lambda event:self.broadcasts.append(copy.deepcopy(event)))
        names={'graphics_split_words','media_input_transform','media_extract_audio','sequence_describe',
            '_editorial_source_command','_commit_editorial_source','_source_command_policy','finish_ingest','_update_ingested_media','_task_media_current',
            '_task_media_update','_task_prepare_media','background_media_prepare'}
        nodes=[node for node in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(node,'name',None) in names]
        self.assertEqual({node.name for node in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.manager.handlers['media']=self.env['_task_prepare_media']
        project=self.project();project['media']['m'].update(ingest_token='original-token',status='ingesting',task_id='original-job',proxy='/proxies/original.mp4',proxy_status='preparing',thumb='/thumbs/original.jpg')
        project['sequences'][0]['tracks'][1]['clips']=[{'id':'g','start':0,'in_':0,'out':6,'speed':1,
            'graphic':{'name':'Words','layers':[{'kind':'text','text':'ONE TWO THREE','size':8,'y':-10},{'kind':'text','text':'KEEP LABEL','size':8,'y':10}]},
            'keyframes':{'g1.x':[{'t':0,'v':0},{'t':3,'v':.1}]},'note':'Keep note'}]
        self.env['save_project'](project)

    def body(self,**kw):return {'_context':self.current(),'actor':'human','client':'editorial-test',**kw}
    def command(self,mode,**kw):return self.route({'words':'graphics_split_words','color':'media_input_transform','extract':'media_extract_audio'}[mode],self.body(**kw))
    def describe(self,**kw):return self.env['sequence_describe'](**{'sequence':'s',**self.current(),**kw})
    def prepare(self,identity):
        task=self.manager.values[identity];task['record']['status']='running';context=TaskContext(self.manager,identity)
        result=self.env['_task_prepare_media'](task['payload'],context);context.commit_result(lambda:result);self.manager.store.save(task);return result
    def audio(self,project,media_id,name,in_=0,out=None,reverse=False):
        result=copy.deepcopy(project);duration=out if out is not None else result['media'][media_id]['duration']
        result['sequences']=[{'id':'audio','name':'Audio','width':64,'height':48,'fps':30,'tracks':[{'id':'a','kind':'audio','index':0,'clips':[
            {'id':'audio','media_id':media_id,'start':0,'in_':in_,'out':duration,'speed':1,'reverse':reverse,'audio':{'maintain_pitch':False}}]}]}]
        path=self.root/(name+'.wav')
        with RenderContext(scratch_parent=str(self.root)) as context:render.render(result,'audio',str(path),{'format':'audio','acodec':'wav'},context=context)
        with wave.open(str(path),'rb') as stream:return stream.getparams(),stream.readframes(stream.getnframes())

    def test_words_save_one_undo_remap_following_automation_and_keep_other_tracks(self):
        before=self.project();reply=self.command('words',sequence='s',clip_id='g',layer=0,anim={},stagger=0)
        after=self.project();graphic=after['sequences'][0]['tracks'][1]['clips'][0]
        self.assertEqual(reply['layers'],3);self.assertTrue(reply['changed']);self.assertEqual(reply['project'],'a')
        self.assertEqual(graphic['keyframes']['g3.x'],before['sequences'][0]['tracks'][1]['clips'][0]['keyframes']['g1.x'])
        self.assertNotIn('g1.x',graphic['keyframes']);self.assertEqual(after['sequences'][0]['tracks'][0],before['sequences'][0]['tracks'][0])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_words_actual_saved_png_keeps_following_label_and_ordinary_overlaps(self):
        from PIL import Image
        project=self.project();sequence=project['sequences'][0];sequence.update(width=640,height=360)
        graphic=sequence['tracks'][1]['clips'][0];graphic['graphic']['layers'][0].update(text='one two three',size=24,y=-50)
        graphic['graphic']['layers'][1].update(size=24,y=60);graphic['keyframes']['g1.x']=[{'t':0,'v':0},{'t':3,'v':.1}]
        sequence['tracks'][0]['clips'].append(dict(sequence['tracks'][0]['clips'][0],id='existing-overlap',start=1,out=2))
        self.env['save_project'](project);before=self.project();paths=[]
        for index in range(2):
            path=self.root/f'words-{index}.png'
            with RenderContext(scratch_parent=str(self.root)) as context:render.render_frame(self.project(),'s',3,str(path),context=context)
            paths.append(path)
            if index==0:self.command('words',sequence='s',clip_id='g',layer=0,anim={},stagger=0)
        with Image.open(paths[0]) as a,Image.open(paths[1]) as b:
            self.assertEqual(a.convert('RGB').crop((0,210,640,360)).tobytes(),b.convert('RGB').crop((0,210,640,360)).tobytes())
        self.assertEqual(before['sequences'][0]['tracks'][0],self.project()['sequences'][0]['tracks'][0])

    def test_mutations_require_owner_policy_and_words_respect_lock(self):
        for route,fields in [('graphics_split_words',{'sequence':'s','clip_id':'g','layer':0}),('media_input_transform',{'media_id':'m','transform':'slog3'}),('media_extract_audio',{'media_id':'m'})]:
            before=(self.raw(),self.raw('b'));ctx=self.current()
            with self.assertRaises(store.HTTPError):self.route(route,fields)
            self.env['set_active_project']('b')
            with self.assertRaises(store.HTTPError):self.route(route,{'_context':ctx,**fields})
            self.env['set_active_project']('a');(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
            with self.assertRaises(store.HTTPError):self.route(route,self.body(actor='agent',**fields))
            (self.root/'settings.json').unlink();self.assertEqual((self.raw(),self.raw('b')),before)
        project=self.project();project['sequences'][0]['tracks'][1]['locked']=True;self.env['save_project'](project);before=self.raw()
        with self.assertRaises(store.HTTPError):self.command('words',sequence='s',clip_id='g',layer=0)
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo']);self.assertFalse(self.manager.values)

    def test_noop_words_color_and_existing_audio_do_not_create_history(self):
        project=self.project();project['sequences'][0]['tracks'][1]['clips'][0]['graphic']['layers'][0]['text']='ONE';project['media']['a']={**project['media']['m'],'id':'a','has_video':False};self.env['save_project'](project);before=self.raw()
        for mode,fields in [('words',{'sequence':'s','clip_id':'g','layer':0}),('color',{'media_id':'m','transform':'none'}),('extract',{'media_id':'a'})]:
            reply=self.command(mode,**fields);self.assertFalse(reply['changed']);self.assertEqual(reply['context'],self.current())
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo']);self.assertFalse(self.manager.values)

    def test_offloop_plan_late_owner_and_changed_font_never_commit(self):
        original=commands.plan;started,release=threading.Event(),threading.Event()
        def delayed(*a,**kw):started.set();release.wait(5);return original(*a,**kw)
        async def scenario():
            before=(self.raw(),self.raw('b'));task=asyncio.create_task(self.env['graphics_split_words'](store.Request(self.body(sequence='s',clip_id='g',layer=0))))
            for _ in range(1000):
                if started.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set());self.assertFalse(task.done());self.env['set_active_project']('b');release.set()
            with self.assertRaises(store.HTTPError):await task
            self.assertEqual((self.raw(),self.raw('b')),before)
        with patch.object(commands,'plan',delayed):run_async_check(scenario())
        self.env['set_active_project']('a');before=self.raw()
        with patch.object(commands,'check_resources',side_effect=ValueError('font changed')),self.assertRaises(store.HTTPError):self.command('words',sequence='s',clip_id='g',layer=0)
        self.assertEqual(self.raw(),before)

    def test_subclip_color_edits_physical_parent_and_actual_pixels_with_one_undo(self):
        from PIL import Image
        project=self.project();parent=project['media']['m'];project['media']['sub']={**parent,'id':'sub','subclip_of':'m','sub_in':1,'duration':3}
        project['sequences'][0]['tracks'][0]['clips'][0].update(media_id='sub',in_=0,out=3);project['sequences'][0]['tracks'][1]['clips']=[];self.env['save_project'](project);before=self.project()
        def picture(name,doc):
            path=self.root/(name+'.png')
            with RenderContext(scratch_parent=str(self.root)) as context:render.render_frame(doc,'s',1,str(path),context=context)
            with Image.open(path) as image:return image.convert('RGB').tobytes()
        original=picture('color-before',before);positive=copy.deepcopy(before);positive['media']['m']['input_transform']='slog3';expected=picture('color-oracle',positive)
        reply=self.command('color',media_id='sub',transform='slog3');self.assertEqual(reply['media_id'],'m');self.assertEqual(reply['scope'],'shared_source')
        self.assertEqual(set(reply['affected_media_ids']),{'m','sub'});self.assertEqual(self.project()['media']['sub'],before['media']['sub'])
        self.assertEqual(picture('color-after',self.project()),expected);self.assertNotEqual(original,expected)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media']['m']['input_transform'],'slog3')

    def test_audio_alias_owns_token_job_and_proxy_and_undo_receipt_after_preparation(self):
        before=self.project();reply=self.command('extract',media_id='m');mid=reply['media_id'];alias=self.project()['media'][mid]
        self.assertNotEqual(alias['ingest_token'],before['media']['m']['ingest_token']);self.assertNotIn('task_id',alias);self.assertNotIn('proxy',alias);self.assertNotIn('thumb',alias)
        self.assertFalse(alias['has_video']);self.assertEqual(alias['status'],'unprepared');self.assertEqual(alias['subclip_of'],'m');self.assertEqual(alias['name'],'Speech — Audio');self.assertEqual(self.project()['media']['m'],before['media']['m'])
        self.prepare(reply['preparation']['task']['id']);after=self.project();prepared=after['media'][mid]
        self.assertEqual(prepared['status'],'ready');self.assertEqual(prepared['task_id'],reply['preparation']['task']['id']);self.assertEqual(prepared['proxy_info']['validation'],'audio_alias_common_clock_metadata')
        available=media_preview.describe(self.root,after,mid);self.assertEqual(available['source_id'],mid);self.assertEqual(available['proxy_state'],'ready')
        path,_,headers=media_preview.resolve(self.root,after,mid,{'proxy':'1','workspace':self.current()['workspace'],'project':'a','generation':available['generation'],'lease':available['proxy_lease']},workspace=self.current()['workspace'],project_id='a')
        self.assertEqual(Path(path).name,Path(prepared['proxy']).name);self.assertEqual(headers['X-Filmocity-Media'],'proxy')
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])

    def test_alias_pending_aac_is_visible_after_waveform_publication(self):
        reply=self.command('extract',media_id='m');mid=reply['media_id'];snapshots=[];actual=media_preparation._run_ffmpeg
        def observe(command,**kwargs):
            if '-c:a' in command and command[command.index('-c:a')+1]=='aac':
                saved=self.project()['media'][mid];snapshots.append(copy.deepcopy(saved))
                self.assertTrue((self.root/saved['wave'].lstrip('/')).is_file())
                self.assertEqual(saved['proxy_status'],'preparing');self.assertIsNone(saved['proxy_error'])
                self.assertNotIn('proxy',saved)
                self.assertEqual(self.manager.get(reply['preparation']['task']['id'])['record']['stage'],'Audio alias preview')
            return actual(command,**kwargs)
        with patch.object(media_preparation,'_run_ffmpeg',observe):self.prepare(reply['preparation']['task']['id'])
        self.assertEqual(len(snapshots),1);self.assertEqual(self.project()['media'][mid]['proxy_status'],'ready')
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_interpreted_subclip_alias_exports_exact_original_pcm_and_proxy_native_clock(self):
        project=self.project();project['media']['m'].update(duration=7.5,interpret_fps=24)
        project['media']['sub']={**project['media']['m'],'id':'sub','subclip_of':'m','sub_in':1.25,'duration':2.5};self.env['save_project'](project)
        reply=self.command('extract',media_id='sub');mid=reply['media_id'];after=self.project()
        for reverse in (False,True):
            a=self.audio(after,'sub','source-'+str(reverse),reverse=reverse);b=self.audio(after,mid,'alias-'+str(reverse),reverse=reverse)
            self.assertEqual(a,b);self.assertEqual(a[0].nframes,120000);self.assertTrue(any(a[1]))
        self.prepare(reply['preparation']['task']['id']);after=self.project();alias=after['media'][mid]
        self.assertEqual(alias['duration'],2.5);self.assertEqual(alias['sub_in'],1.25);self.assertEqual(alias['proxy_info']['native_duration'],6)
        self.assertAlmostEqual(alias['proxy_info']['measured_duration'],6,delta=.022)
        self.assertEqual(media_preview.describe(self.root,after,mid)['source_id'],mid)

    def test_pending_alias_preparation_cannot_publish_after_parent_change_and_public_prepare_rebuilds(self):
        reply=self.command('extract',media_id='m');mid=reply['media_id'];identity=reply['preparation']['task']['id'];old=self.manager.get(identity)['payload'];before=self.project()
        replacement=self.root/'replacement.mkv';shutil.copyfile(self.source,replacement)
        project=self.project();project['media']['m']['path']=str(replacement);self.env['save_project'](project)
        self.assertFalse(self.env['_task_media_current'](old));self.assertFalse(self.env['_task_media_update'](old,{'status':'ready','proxy':'/proxies/wrong.m4a'}))
        with self.assertRaises(ValueError):self.prepare(identity)
        self.assertNotIn('proxy',self.project()['media'][mid]);self.manager.values[identity]['record']['status']='error'
        queued=self.route('background_media_prepare',self.body(),mid);new=queued['task']['id'];self.assertNotEqual(new,identity)
        self.prepare(new);available=media_preview.describe(self.root,self.project(),mid);self.assertEqual(available['proxy_state'],'ready')
        self.assertEqual(self.manager.get(new)['payload']['path'],str(replacement));self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_completed_alias_rebuild_accepts_parent_relink_but_not_unaccepted_file_change(self):
        reply=self.command('extract',media_id='m');mid=reply['media_id'];self.prepare(reply['preparation']['task']['id'])
        old_project=self.project();old_status=media_preview.describe(self.root,old_project,mid);replacement=self.root/'accepted-relink.mkv';shutil.copyfile(self.source,replacement)
        project=self.project();project['media']['m'].update(path=str(replacement),ingest_token='accepted-relink-token');self.env['save_project'](project)
        changed=media_preview.describe(self.root,self.project(),mid);self.assertNotEqual(changed['generation'],old_status['generation']);self.assertEqual(changed['proxy_state'],'stale_source')
        with self.assertRaises(media_preview.PreviewError):media_preview.resolve(self.root,self.project(),mid,
            {'workspace':self.current()['workspace'],'project':'a','generation':old_status['generation'],'lease':old_status['proxy_lease'],'proxy':'1'},workspace=self.current()['workspace'],project_id='a')
        queued=self.route('background_media_prepare',self.body(),mid);self.prepare(queued['task']['id']);self.assertEqual(media_preview.describe(self.root,self.project(),mid)['proxy_state'],'ready')
        with replacement.open('ab') as stream:stream.write(b'unaccepted source modification')
        before_tasks=len(self.manager.values)
        with self.assertRaisesRegex(store.HTTPError,'changed on disk'):self.route('background_media_prepare',self.body(),mid)
        self.assertEqual(len(self.manager.values),before_tasks)
        # A relink can explicitly accept replacement bytes at the SAME path.
        project=self.project();project['media']['m']['ingest_token']='same-path-relink-token';self.env['save_project'](project)
        queued=self.route('background_media_prepare',self.body(),mid);self.prepare(queued['task']['id']);self.assertEqual(media_preview.describe(self.root,self.project(),mid)['proxy_state'],'ready')
        project=self.project();project['media']['m'].update(interpret_fps=24,duration=7.5);self.env['save_project'](project)
        with self.assertRaisesRegex(store.HTTPError,'interpretation changed'):self.route('background_media_prepare',self.body(),mid)
        self.assertFalse(media_preview.catalog(self.root,self.project())[mid]['proxy_available'])

    def test_alias_range_change_cannot_authorize_unaccepted_physical_file_replacement(self):
        reply=self.command('extract',media_id='m');mid=reply['media_id'];self.prepare(reply['preparation']['task']['id'])
        project=self.project();project['media'][mid]['duration']=5;self.env['save_project'](project)
        with self.source.open('ab') as stream:stream.write(b'unaccepted replacement')
        before_tasks=len(self.manager.values)
        with self.assertRaisesRegex(store.HTTPError,'changed on disk'):self.route('background_media_prepare',self.body(),mid)
        self.assertEqual(len(self.manager.values),before_tasks)

    def test_derived_completion_writes_original_project_when_other_project_is_active(self):
        reply=self.command('extract',media_id='m');mid=reply['media_id'];before_b=self.raw('b');self.env['set_active_project']('b')
        self.prepare(reply['preparation']['task']['id']);self.assertEqual(self.raw('b'),before_b);self.env['set_active_project']('a')
        self.assertEqual(self.project()['media'][mid]['status'],'ready');self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_delayed_common_origin_audio_alias_preview_keeps_native_clock(self):
        source=self.root/'delayed.mkv'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:s=64x48:r=30:d=6',
            '-itsoffset','0.5','-i',str(self.root/'speech.wav'),'-copyts','-t','6','-c:v','ffv1','-threads','1',
            '-c:a','pcm_s16le','-output_ts_offset','3',str(source)],check=True,capture_output=True,timeout=30)
        project=self.project();project['media']['m'].update(path=str(source),duration=7.5,interpret_fps=24)
        project['media']['sub']={**project['media']['m'],'id':'sub','subclip_of':'m','sub_in':1.25,'duration':2.5}
        self.env['save_project'](project);before_hash=hashlib.sha256(source.read_bytes()).hexdigest()
        reply=self.command('extract',media_id='sub');mid=reply['media_id'];self.prepare(reply['preparation']['task']['id']);project=self.project()
        self.assertEqual(self.audio(project,'sub','delayed-original'),self.audio(project,mid,'delayed-alias'))
        alias=project['media'][mid];proxy=self.root/alias['proxy'].lstrip('/')
        decoded=subprocess.run(['ffmpeg','-v','error','-i',str(proxy),'-map','0:a:0','-t','6','-f','s16le','-ar','48000','-ac','2','-'],check=True,capture_output=True,timeout=30).stdout
        actual=array.array('h',decoded)
        if sys.byteorder!='little':actual.byteswap()
        self.assertEqual(len(actual),6*48000*2);self.assertLess(max(abs(v) for v in actual[:int(.45*48000)*2]),3)
        # Compare native tone/silence landmarks independently of interpretation.
        level=lambda at:sum(abs(actual[i]) for i in range(int(at*48000)*2,int((at+.05)*48000)*2))/(int(.05*48000)*2)
        self.assertGreater(level(.75),4000);self.assertLess(level(1.75),3);self.assertGreater(level(2.75),4000)
        self.assertTrue(alias['proxy_info']['lossy']);self.assertEqual(alias['proxy_info']['native_duration'],6)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),before_hash)

    def test_lost_request_after_commit_joins_alias_queue_and_workspace_switch_does_not_misroute(self):
        entered=asyncio.Event();release=asyncio.Event();original=self.env['broadcast']
        async def delayed(event): entered.set();await release.wait();await original(event)
        async def scenario():
            self.env['broadcast']=delayed
            task=asyncio.create_task(self.env['media_extract_audio'](store.Request(self.body(media_id='m'))))
            await entered.wait();self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
            task.cancel();task.cancel();release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertEqual(len(self.manager.values),1);self.assertEqual(next(iter(self.manager.values.values()))['record']['kind'],'media')
        run_async_check(scenario());self.env['broadcast']=original
        # A workspace switch after commit must not send preparation to the new
        # service/path. Keep the committed alias in the original store.
        original_root=self.env['ROOT'];original_P=self.env['P'];before_tasks=len(self.manager.values)
        async def switch_workspace(event):
            self.env['ROOT']=str(self.root/'other-workspace');self.env['P']=lambda *parts:str(self.root.joinpath('other-workspace',*parts))
        self.env['broadcast']=switch_workspace
        try:reply=self.command('extract',media_id='m')
        finally:self.env.update(ROOT=original_root,P=original_P,broadcast=original)
        self.assertIn('Workspace changed',reply['preparation']['warning']);self.assertEqual(len(self.manager.values),before_tasks)
        self.assertIn(reply['media_id'],self.project()['media']);self.assertEqual(self.project()['media'][reply['media_id']]['status'],'unprepared')

    def test_describe_uses_canonical_ramp_hold_precise_clock_and_owned_read(self):
        project=self.project();sequence=project['sequences'][0];sequence['tracks'][1]['clips']=[];clip=sequence['tracks'][0]['clips'][0]
        clip.update(in_=0,out=8,speed=1,time_remap=[{'t':0,'v':2},{'t':2,'v':3}]);self.env['save_project'](project);before=self.raw();reply=self.describe()
        self.assertAlmostEqual(reply['duration'],render.clip_dur(clip),places=13);self.assertEqual(reply['timings'][0]['duration'],reply['duration']);self.assertIn('speed ramp',reply['text']);self.assertEqual(reply['frame_rate'],'30')
        clip.update(hold=True,speed=2);self.env['save_project'](project);held=self.describe();self.assertEqual(held['duration'],8);self.assertIn('held frame',held['text'])
        context=self.current();self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.env['sequence_describe'](sequence='s',**context)
        with self.assertRaises(store.HTTPError):self.env['sequence_describe'](sequence='s')
        self.assertFalse(self.env['read_undo_history']('a')['undo'])


if __name__=='__main__':unittest.main()
