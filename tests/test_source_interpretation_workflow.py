"""Actual owned interpretation routes, saved Undo and decoded source clocks."""
import array
import ast
import asyncio
import copy
import hashlib
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import source_interpretation as interpretation
import source_commands
import test_project_sync as store
import test_source_creation_workflow as creation
from test_media_collection import run_async_check


class StoreSourceInterpretation(creation.StoreSourceCreation):
    for _name in dir(creation.StoreSourceCreation):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'media_interpret','media_interpret_review','_interpretation_candidate','_commit_source_interpretation'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)

    def review(self,media_id='m',fps=24,include_fullmix=False,**kw):
        return self.route('media_interpret_review',self.body(media_id=media_id,fps=fps,include_fullmix=include_fullmix,**kw))
    def apply(self,report,**kw):
        return self.route('media_interpret',{'_context':report['context'],'media_id':report['requested_media_id'],
            **report['settings'],'fingerprint':report['fingerprint'],'actor':'human','client':'interpret-test',**kw})

    def test_review_readonly_with_proposals_policy_and_explicit_group_consent(self):
        self.children();before=(self.raw(),self.raw('b'));history=copy.deepcopy(self.env['read_undo_history']('a'))
        (self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
        report=self.review(actor='agent');self.assertFalse(report['ok']);self.assertEqual(report['summary']['required_fullmix_ids'],['alias'])
        report=self.review(include_fullmix=True,actor='agent');self.assertTrue(report['ok'])
        self.assertEqual(len(report['fingerprint']),64);self.assertEqual((self.raw(),self.raw('b')),before)
        self.assertEqual(self.env['read_undo_history']('a'),history);self.assertFalse(self.manager.values)
        with self.assertRaises(store.HTTPError):self.apply(report,actor='agent')
        self.assertEqual((self.raw(),self.raw('b')),before)
        unchanged=self.apply(self.review('alias','30/1'));self.assertFalse(unchanged['changed'])
        self.assertEqual((self.raw(),self.raw('b')),before);self.assertFalse(self.manager.values)

    def test_parent_and_fullmix_save_one_undo_and_preparation_updates_after_only(self):
        self.children();before=self.project();report=self.review(include_fullmix=True);reply=self.apply(report)
        self.assertTrue(reply['changed']);self.assertEqual(reply['kind'],'source_interpretation');self.assertEqual(reply['project'],'a')
        self.assertEqual({r['media_id'] for r in reply['preparation']['tasks']},{'m','alias'})
        for item in reply['preparation']['tasks']:self.prepare(item['task']['id'])
        after=self.project();self.assertEqual(after['sequences'],before['sequences']);self.assertEqual(after['media']['m']['duration'],7.5)
        self.assertEqual((after['media']['alias']['sub_in'],after['media']['alias']['duration']),(1.25,3.75))
        self.assertEqual((after['media']['sub']['sub_in'],after['media']['sub']['duration']),(1,3))
        history=self.env['read_undo_history']('a');self.assertEqual(len(history['undo']),1);self.assertEqual(history['undo'][0]['tool'],'workflow_interpret')
        for change in history['undo'][0]['changes']:
            mid=change['path'][1];self.assertEqual(change['before']['value'],before['media'][mid]);self.assertEqual(change['after']['value'],after['media'][mid])
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])

    def test_missing_owner_fingerprint_settings_and_foreign_project_cannot_write(self):
        report=self.review();before=(self.raw(),self.raw('b'))
        for route,body in [('media_interpret_review',{'media_id':'m','fps':24}),('media_interpret',self.body(media_id='m',fps=24)),
            ('media_interpret_review',self.body(media_id='m'))]:
            with self.assertRaises(store.HTTPError):self.route(route,body)
        self.assertEqual((self.raw(),self.raw('b')),before)
        self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.apply(report)
        with self.assertRaises(store.HTTPError):self.route('media_interpret_review',{'_context':report['context'],'media_id':'m','fps':24})
        self.assertEqual((self.raw(),self.raw('b')),before)

    def test_changed_revision_or_tampered_settings_require_new_review(self):
        report=self.review();before=self.raw()
        for change in ({'fps':25},{'include_fullmix':True},{'fingerprint':'a'*64}):
            with self.assertRaises(store.HTTPError):self.apply(report,**change)
            self.assertEqual(self.raw(),before)
        self.edit('New name',self.current());before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual(self.raw(),before)

    def test_noop_native_and_same_fraction_do_not_write_or_queue(self):
        before=self.raw();reply=self.apply(self.review(fps=None));self.assertFalse(reply['changed']);self.assertEqual(self.raw(),before)
        self.assertFalse(reply['preparation']['tasks']);self.assertFalse(self.env['read_undo_history']('a')['undo'])
        self.apply(self.review(fps='24000/1001'));before=self.raw();count=len(self.manager.values)
        reply=self.apply(self.review(fps=23.976));self.assertFalse(reply['changed']);self.assertEqual(self.raw(),before);self.assertEqual(len(self.manager.values),count)

    def test_native_window_selected_channel_matches_independent_decoded_oracle(self):
        sub=self.create('subclip',**{'in':1,'out':3})['media_ids'][0];created=self.create('breakout',media_id=sub);mid=created['media_ids'][0]
        for item in created['preparation']['tasks']:self.manager.cancel(item['task']['id'])
        before=self.project();digest=hashlib.sha256(self.source.read_bytes()).hexdigest();reply=self.apply(self.review(mid,60));after=self.project()
        self.assertEqual((after['media'][mid]['sub_in'],after['media'][mid]['duration']),(.5,1));source_commands.alias_source(after,after['media'][mid])
        oracle=copy.deepcopy(before);oracle['media'][mid].update(sub_in=.5,duration=1,interpret_fps='60/1')
        params,actual=self.audio(after,mid,'interpreted-actual',0,1);expected_params,expected=self.audio(oracle,mid,'independent-oracle',0,1)
        self.assertEqual(params,expected_params);self.assertEqual(actual,expected);samples=array.array('h',actual)
        self.assertEqual(max(abs(v) for v in samples[int(.1*params.framerate)*params.nchannels:int(.4*params.framerate)*params.nchannels]),0)
        self.assertGreater(max(abs(v) for v in samples),1000);self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),digest)
        self.assertEqual(after['media']['m'],before['media']['m']);self.assertTrue(reply['preparation']['tasks'])

    def test_end_channel_window_remains_preparable_after_faster_rate(self):
        sub=self.create('subclip',**{'in':4,'out':6})['media_ids'][0];created=self.create('breakout',media_id=sub);mid=created['media_ids'][0]
        for item in created['preparation']['tasks']:self.manager.cancel(item['task']['id'])
        reply=self.apply(self.review(mid,60));self.assertEqual((self.project()['media'][mid]['sub_in'],self.project()['media'][mid]['duration']),(2,1))
        self.prepare(reply['preparation']['tasks'][0]['task']['id']);self.assertEqual(self.project()['media'][mid]['proxy_status'],'ready')

    def test_selected_ordinary_subclip_no_own_queue_and_preserved_parent(self):
        project=self.children();project['media'].pop('alias');self.env['save_project'](project);before=self.project()
        reply=self.apply(self.review('sub',60));after=self.project();self.assertEqual(after['media']['m'],before['media']['m'])
        self.assertEqual((after['media']['sub']['sub_in'],after['media']['sub']['duration']),(.5,1.5));self.assertFalse(reply['preparation']['tasks'])
        self.assertEqual(after['media']['sub']['status'],'ready')

    def test_invalid_locked_ranges_and_hold_frames_refuse_without_mutation(self):
        project=self.project();track=project['sequences'][0]['tracks'][0];track['locked']=True;self.env['save_project'](project);before=self.raw()
        report=self.review(fps=60);self.assertFalse(report['ok']);self.assertTrue(report['summary']['uses'][0]['locked'])
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual(self.raw(),before)
        project=self.project();project['sequences'][0]['tracks'][0]['clips'][0].update(hold=True,in_=2,out=50);self.env['save_project'](project)
        self.assertTrue(self.review(fps=60)['ok']);project['sequences'][0]['tracks'][0]['clips'][0]['in_']=3;self.env['save_project'](project)
        self.assertFalse(self.review(fps=60)['ok'])

    def test_offline_metadata_edit_warns_without_queue_and_reappearance_invalidates(self):
        original=self.source.read_bytes();self.source.unlink();report=self.review();self.assertTrue(report['ok']);self.assertTrue(any('offline' in w for w in report['summary']['warnings']))
        self.source.write_bytes(original);before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual(self.raw(),before);self.source.unlink();reply=self.apply(self.review());self.assertTrue(reply['changed']);self.assertFalse(reply['preparation']['tasks'])

    def test_file_change_after_review_and_known_relink_basis_mismatch_refuse(self):
        report=self.review();before=self.raw()
        with self.source.open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual(self.raw(),before)
        # Establish an accepted physical identity through the existing route.
        relink=self.inspect()
        self.route('media_relink',{'_context':relink['context'],'media_id':'m','path':relink['path'],'fingerprint':relink['fingerprint'],'actor':'human'})
        with self.replacement.open('ab') as stream:stream.write(b'changed after acceptance')
        before=self.raw()
        with self.assertRaises(store.HTTPError):self.review()
        self.assertEqual(self.raw(),before)

    def test_offloop_project_or_policy_change_cannot_commit(self):
        original=interpretation.inspect
        for policy in (False,True):
            report=self.review();started,release=threading.Event(),threading.Event()
            def delayed(*a,**k):result=original(*a,**k);started.set();release.wait(5);return result
            async def scenario():
                body={'_context':report['context'],'media_id':'m',**report['settings'],'fingerprint':report['fingerprint'],'actor':'agent'}
                before=(self.raw(),self.raw('b'));pending=asyncio.create_task(self.env['media_interpret'](store.Request(body)))
                for _ in range(1000):
                    if started.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(started.is_set())
                if policy:(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
                else:self.env['set_active_project']('b')
                release.set()
                with self.assertRaises(store.HTTPError):await pending
                self.assertEqual((self.raw(),self.raw('b')),before)
            with patch.object(interpretation,'inspect',delayed):run_async_check(scenario())
            self.env['set_active_project']('a')
        self.assertFalse(self.manager.values)

    def test_undo_before_old_worker_completion_blocks_publication(self):
        reply=self.apply(self.review());item=reply['preparation']['tasks'][0];payload=self.manager.values[item['task']['id']]['payload']
        self.assertTrue(self.env['_task_media_current'](payload));self.invoke('undo',{'_context':self.current()});before=self.raw()
        self.assertFalse(self.env['_task_media_current'](payload));self.assertFalse(self.env['_task_media_update'](payload,{'proxy':'/wrong.mp4'}));self.assertEqual(self.raw(),before)

    def test_lost_reply_joins_commit_queue_and_cannot_replay(self):
        report=self.review();entered=asyncio.Event();release=asyncio.Event()
        async def blocked(event):entered.set();await release.wait()
        original=self.env['broadcast'];self.env['broadcast']=blocked
        async def scenario():
            body={'_context':report['context'],'media_id':'m',**report['settings'],'fingerprint':report['fingerprint']}
            pending=asyncio.create_task(self.env['media_interpret'](store.Request(body)));await entered.wait();pending.cancel();await asyncio.sleep(.02)
            self.assertFalse(pending.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        try:run_async_check(scenario())
        finally:self.env['broadcast']=original
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);self.assertEqual(len(self.manager.values),1);before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual(self.raw(),before)

    def test_escaped_source_id_is_applied_without_touching_literal_escape_decoy(self):
        project=self.project();media=project['media'].pop('m');media['id']='a/b~c';project['media'][media['id']]=media
        project['media']['a~1b~0c']={**copy.deepcopy(media),'id':'a~1b~0c','name':'Decoy'}
        project['sequences'][0]['tracks'][0]['clips'][0]['media_id']=media['id'];self.env['save_project'](project);before=self.project()
        self.apply(self.review('a/b~c'));self.assertEqual(self.project()['media']['a~1b~0c'],before['media']['a~1b~0c'])

    def test_known_source_identity_survives_cleared_proxy_and_late_file_change(self):
        from media_preview import digest
        from task_inputs import source_stamp
        project=self.project();signature=digest(source_stamp(project['media']['m']))
        project['media']['m']['proxy_info']={'source_signature':signature,'validation':'old','file_stamp':['old']};self.env['save_project'](project)
        original=self.env['broadcast']
        async def changed(event):
            with self.source.open('ab') as stream:stream.write(b'changed after interpretation commit')
        self.env['broadcast']=changed
        try:reply=self.apply(self.review())
        finally:self.env['broadcast']=original
        self.assertTrue(reply['preparation']['warnings']);self.assertFalse(reply['preparation']['tasks'])
        self.assertEqual(self.project()['media']['m']['proxy_info'],{'source_signature':signature})
        with self.assertRaisesRegex(store.HTTPError,'source changed'):self.route('background_media_prepare',self.body(),'m')
        self.assertFalse(self.manager.values);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_existing_source_signature_survives_offline_edit_and_unchanged_prepare(self):
        from media_preview import digest
        from task_inputs import source_stamp
        project=self.project();signature=digest(source_stamp(project['media']['m']));project['media']['m']['proxy_info']={'source_signature':signature}
        self.env['save_project'](project);original=self.source.read_bytes();self.source.unlink()
        reply=self.apply(self.review());self.assertFalse(reply['preparation']['tasks']);self.assertEqual(self.project()['media']['m']['proxy_info'],{'source_signature':signature})
        # Restore the same file object rather than forge unchanged inode: an
        # offline rename retains the true identity in the second half below.
        self.source.write_bytes(original)
        with self.assertRaises(store.HTTPError):self.route('background_media_prepare',self.body(),'m')
        project=self.project();project['media']['m']['proxy_info']={'source_signature':digest(source_stamp(project['media']['m']))};self.env['save_project'](project)
        backup=self.source.with_suffix('.offline');self.source.rename(backup)
        self.apply(self.review(fps=25));backup.rename(self.source)
        queued=self.route('background_media_prepare',self.body(),'m');self.prepare(queued['task']['id'])
        self.assertEqual(self.project()['media']['m']['proxy_status'],'ready')

    def test_family_alias_signature_is_checked_even_when_parent_has_none(self):
        from media_preview import digest
        from task_inputs import source_stamp
        project=self.children();project['media']['alias']['proxy_info']={'source_signature':digest(source_stamp(project['media']['m']))}
        self.env['save_project'](project)
        with self.source.open('ab') as stream:stream.write(b'changed alias source')
        before=self.raw()
        with self.assertRaisesRegex(store.HTTPError,'Source changed'):self.review(include_fullmix=True)
        self.assertEqual(self.raw(),before)

    def test_workspace_switch_during_postcommit_recheck_never_retargets_queue(self):
        report=self.review();original=interpretation.check;owned_root=self.env['ROOT'];checks=[]
        def switch(resources):
            original(resources);checks.append(True)
            if len(checks)==4:self.env['ROOT']=str(self.root/'another-workspace')
        try:
            with patch.object(interpretation,'check',switch):reply=self.apply(report)
        finally:self.env['ROOT']=owned_root
        self.assertEqual(len(checks),4);self.assertTrue(reply['changed']);self.assertTrue(reply['preparation']['warnings']);self.assertFalse(self.manager.values)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_numbered_png_source_keeps_decoder_clock_and_interpreted_picture(self):
        from PIL import Image
        import render
        from render_context import RenderContext
        for index,color in enumerate(('red','green','blue'),1):Image.new('RGB',(64,48),color).save(self.root/f'frame_{index:04d}.png')
        project=self.project();project['media']={'m':{'id':'m','name':'Numbered','path':str(self.root/'frame_%04d.png'),
            'fps':2,'frame_rate':'2/1','duration':1.5,'has_video':True,'has_audio':False,'width':64,'height':48,'is_image':False,
            'sequence_frames':3,'input_opts':['-framerate','2','-start_number','1'],'ingest_token':'numbered-old','status':'ready'}}
        sequence=project['sequences'][0];sequence.update(fps=4)
        for track in sequence['tracks']:track['clips']=[]
        sequence['tracks'][0]['clips']=[{'id':'c','media_id':'m','start':0,'in_':0,'out':.75,'speed':1}]
        self.env['save_project'](project);report=self.review(fps=4);self.assertTrue(report['ok']);self.apply(report)
        after=self.project();self.assertEqual(after['media']['m']['input_opts'],project['media']['m']['input_opts'])
        self.assertEqual(after['media']['m']['duration'],.75);path=self.root/'interpreted-numbered.png'
        with RenderContext(scratch_parent=str(self.root)) as context:render.render_frame(after,'s',.5,str(path),context=context)
        with Image.open(path) as image:
            rgb=image.convert('RGB').getpixel((32,24));self.assertGreater(rgb[2],240);self.assertLess(rgb[0],10);self.assertLess(rgb[1],10)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],project['media'])

if __name__=='__main__':unittest.main()
