"""Actual guarded source creation, saved history and owned FFmpeg preparation.

Requests/HTTP wrappers are controlled; storage, command functions and codecs are
real. This does not replace Windows/WebView acceptance.
"""
import array
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import source_creation_io as creation_io
import media_preview
from controlled_probe import python_probe
from test_media_collection import run_async_check
import test_source_relink_workflow as relink
import test_project_sync as store


class StoreSourceCreation(relink.StoreSourceRelink):
    for _name in dir(relink.StoreSourceRelink):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'media_subclip','media_breakout','media_duplicate','_source_creation_command','_commit_source_creation'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)

    def create(self,mode,**kw):
        fields={'media_ids':['m']} if mode=='duplicate' else {'media_id':'m','in':1,'out':3} if mode=='subclip' else {'media_id':'m'}
        return self.route('media_'+mode,self.body(**{**fields,**kw}))

    def test_complete_registration_keeps_original_media_tracks_and_one_exact_undo(self):
        before=self.project();reply=self.create('duplicate');after=self.project();mid=reply['media_ids'][0]
        self.assertEqual(reply['kind'],'source_creation');self.assertEqual(reply['mode'],'duplicate')
        self.assertEqual(reply['summary']['source_media_ids'],['m']);self.assertEqual(reply['summary']['created_media_ids'],[mid])
        self.assertEqual(after['sequences'],before['sequences']);self.assertEqual(after['media']['m'],before['media']['m'])
        self.assertNotEqual(after['media'][mid]['ingest_token'],before['media']['m']['ingest_token'])
        self.assertNotIn('task_id',after['media'][mid]);self.assertNotIn('proxy',after['media'][mid])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])

    def test_all_commands_require_context_and_proposal_policy_before_changes(self):
        for mode,fields in [('subclip',{'media_id':'m','in':0,'out':1}),('breakout',{'media_id':'m'}),('duplicate',{'media_ids':['m']})]:
            before=(self.raw(),self.raw('b'));count=len(self.manager.values)
            with self.subTest(mode=mode):
                with self.assertRaises(store.HTTPError):self.route('media_'+mode,fields)
                (self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
                with self.assertRaises(store.HTTPError):self.route('media_'+mode,self.body(actor='agent',**fields))
                (self.root/'settings.json').unlink()
                self.assertEqual((self.raw(),self.raw('b')),before);self.assertEqual(len(self.manager.values),count)
        self.assertFalse(self.env['read_undo_history']('a')['undo'])

    def test_stale_project_context_cannot_retarget_matching_foreign_media_id(self):
        original=self.body(media_id='m',**{'in':1,'out':2});self.env['set_active_project']('b')
        b=self.project();b['media']=copy.deepcopy(json.loads(self.raw('a'))['media']);self.env['save_project'](b)
        before=(self.raw(),self.raw('b'))
        for mode,fields in [('subclip',{}),('breakout',{}),('duplicate',{'media_ids':['m']})]:
            with self.assertRaises(store.HTTPError):self.route('media_'+mode,{**original,**fields})
        self.assertEqual((self.raw(),self.raw('b')),before);self.assertFalse(self.manager.values)

    def test_all_or_nothing_multi_duplicate_and_ordered_ids(self):
        project=self.project();project['media']['other']={**copy.deepcopy(project['media']['m']),'id':'other','name':'Other'};self.env['save_project'](project)
        before=self.raw()
        for ids in (['m','missing'],['m','m'],[],['m']*51):
            with self.assertRaises(store.HTTPError):self.create('duplicate',media_ids=ids)
            self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)
        reply=self.create('duplicate',media_ids=['other','m'])
        self.assertEqual(reply['summary']['source_media_ids'],['other','m'])
        self.assertEqual([m['id'] for m in reply['media']],reply['media_ids'])
        self.assertEqual(len(set(reply['media_ids'])),2);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.assertEqual(len({m['ingest_token'] for m in reply['media']}),2)

    def test_subclip_selected_local_range_keeps_child_interpretation_and_shared_preview(self):
        project=self.children(interpreted=True);project['media'].pop('alias')
        project['media']['sub'].update(interpret_fps=15,duration=3,sub_in=2);self.env['save_project'](project)
        before=self.project();reply=self.create('subclip',media_id='sub',**{'in':.5,'out':2.5});media=reply['media'][0]
        self.assertEqual(media['subclip_of'],'m');self.assertEqual(media['sub_in'],2.5);self.assertEqual(media['duration'],2)
        self.assertEqual(media['interpret_fps'],15);self.assertFalse(reply['preparation']['tasks'])
        for key in ('task_id','proxy','wave','thumb','strip'):self.assertNotIn(key,media)
        self.assertEqual(media_preview.describe(self.root,self.project(),media['id'])['source_id'],'m')
        self.assertEqual(self.project()['media']['m'],before['media']['m'])
        self.assertEqual(self.audio(self.project(),media['id'],'child',0,2),self.audio(before,'sub','source',.5,2.5))

    def test_offline_subclip_and_duplicate_keep_metadata_without_preparation(self):
        self.source.unlink();before=self.project()
        for mode in ('subclip','duplicate'):
            reply=self.create(mode);self.assertTrue(reply['warnings']);self.assertFalse(reply['preparation']['tasks'])
            self.assertEqual(self.project()['media']['m'],before['media']['m'])
        before=self.raw()
        with self.assertRaises(store.HTTPError):self.create('breakout')
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)

    def test_invalid_source_windows_refuse_without_any_partial_registration(self):
        before=self.raw()
        for inn,out in [(-1,1),(0,7),(2,2),(float('nan'),2),(0,float('inf')),(True,2)]:
            with self.assertRaises(store.HTTPError):self.create('subclip',**{'in':inn,'out':out})
            self.assertEqual(self.raw(),before)
        self.assertFalse(self.manager.values)

    def test_breakout_fresh_primary_audio_probe_refuses_stale_metadata(self):
        for field,value in [('channels',1),('sample_rate',48000),('duration',8)]:
            project=self.project();old=project['media']['m'][field];project['media']['m'][field]=value;self.env['save_project'](project);before=self.raw()
            with self.assertRaisesRegex(store.HTTPError,'Relink'):self.create('breakout')
            self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)
            project['media']['m'][field]=old;self.env['save_project'](project)

    def test_probe_error_source_change_and_accepted_basis_mismatch_never_register(self):
        before=self.raw()
        with patch.object(creation_io,'_probe',side_effect=ValueError('probe failed')),self.assertRaises(store.HTTPError):self.create('breakout')
        self.assertEqual(self.raw(),before)
        original=creation_io._probe
        def changed(*args):
            value=original(*args)
            with self.source.open('ab') as stream:stream.write(b'changed during source inspection')
            return value
        with patch.object(creation_io,'_probe',changed),self.assertRaises(store.HTTPError):self.create('breakout')
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)

    def test_offloop_planning_cannot_commit_after_project_or_policy_change(self):
        original=creation_io.prepare
        for policy in (False,True):
            started,release=threading.Event(),threading.Event()
            def delayed(*args,**kwargs):
                result=original(*args,**kwargs);started.set();release.wait(5);return result
            async def scenario():
                before=(self.raw(),self.raw('b'));pending=asyncio.create_task(self.env['media_duplicate'](store.Request(self.body(actor='agent',media_ids=['m']))))
                for _ in range(1000):
                    if started.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(started.is_set());self.assertFalse(pending.done())
                if policy:(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
                else:self.env['set_active_project']('b')
                release.set()
                with self.assertRaises(store.HTTPError):await pending
                self.assertEqual((self.raw(),self.raw('b')),before);self.assertFalse(self.manager.values)
            with patch.object(creation_io,'prepare',delayed):run_async_check(scenario())
            self.env['set_active_project']('a')
            if policy:(self.root/'settings.json').unlink()

    def test_real_preparation_is_independent_and_updates_only_new_undo_after_value(self):
        before=self.project();reply=self.create('duplicate');mid=reply['media_ids'][0];task=reply['preparation']['tasks'][0]['task']['id']
        self.prepare(task);after=self.project();self.assertEqual(after['media'][mid]['status'],'ready')
        self.assertEqual(after['media']['m'],before['media']['m']);self.assertEqual(after['media'][mid]['task_id'],task)
        history=self.env['read_undo_history']('a');self.assertEqual(len(history['undo']),1)
        change=history['undo'][0]['changes'][0];self.assertEqual(change['path'],['media',mid]);self.assertFalse(change['before']['exists']);self.assertEqual(change['after']['value'],after['media'][mid])
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])

    def test_undo_before_worker_and_foreign_active_project_cannot_publish_elsewhere(self):
        reply=self.create('duplicate');task=reply['preparation']['tasks'][0]['task']['id'];payload=self.manager.values[task]['payload']
        self.invoke('undo',{'_context':self.current()});before=(self.raw(),self.raw('b'));self.env['set_active_project']('b')
        self.assertFalse(self.env['_task_media_current'](payload));self.assertFalse(self.env['_task_media_update'](payload,{'status':'ready'}))
        with self.assertRaises(ValueError):self.prepare(task)
        self.assertEqual((self.raw(),self.raw('b')),before)

    def test_completed_commit_joins_queue_even_when_reply_is_cancelled(self):
        started,release=asyncio.Event(),asyncio.Event();original=self.env['_workflow_commit']
        async def delayed(*args,**kwargs):
            result=await original(*args,**kwargs);started.set();await release.wait();return result
        async def scenario():
            pending=asyncio.create_task(self.env['media_duplicate'](store.Request(self.body(media_ids=['m']))))
            await started.wait();pending.cancel();await asyncio.sleep(.01);self.assertFalse(pending.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        self.env['_workflow_commit']=delayed;run_async_check(scenario());self.env['_workflow_commit']=original
        self.assertEqual(len(self.manager.values),1);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.assertEqual(len(self.project()['media']),2)

    def test_workspace_switch_after_commit_leaves_saved_registration_without_wrong_queue(self):
        original=self.env['_workflow_commit'];owned_root=self.env['ROOT']
        async def switched(*args,**kwargs):
            result=await original(*args,**kwargs);self.env['ROOT']=str(self.root/'other-workspace');return result
        self.env['_workflow_commit']=switched
        try:reply=self.create('duplicate')
        finally:self.env['ROOT']=owned_root;self.env['_workflow_commit']=original
        self.assertTrue(reply['changed']);self.assertTrue(reply['preparation']['warnings']);self.assertFalse(self.manager.values)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_workspace_change_during_postcommit_file_recheck_cannot_retarget_queue(self):
        original=creation_io.check;owned_root=self.env['ROOT'];checks=[]
        def switch_after_recheck(resources):
            original(resources);checks.append(True)
            # prepare-final, precommit, then the first postcommit queue check.
            if len(checks)==3:self.env['ROOT']=str(self.root/'changed-during-file-recheck')
        try:
            with patch.object(creation_io,'check',switch_after_recheck):reply=self.create('duplicate')
        finally:self.env['ROOT']=owned_root
        self.assertEqual(len(checks),3);self.assertTrue(reply['changed']);self.assertTrue(reply['preparation']['warnings'])
        self.assertFalse(self.manager.values);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_cancelled_actual_probe_reaps_owned_process_without_creating_media(self):
        script=self.root/'slow-probe.py'
        original=creation_io._probe;holder={};before=self.raw()
        def slow(path,context):
            holder['value']=context.holder
            return original(path,context,ffprobe=str(script))
        async def scenario():
            pending=asyncio.create_task(self.env['media_breakout'](store.Request(self.body(media_id='m'))))
            for _ in range(1000):
                if holder.get('value',{}).get('proc'):break
                await asyncio.sleep(.001)
            child=holder['value']['proc'];pending.cancel();await asyncio.sleep(.005);pending.cancel()
            with self.assertRaises(asyncio.CancelledError):await pending
            self.assertIsNotNone(child.poll())
        with python_probe(script,'import time\ntime.sleep(30)\n'),patch.object(creation_io,'_probe',slow):run_async_check(scenario())
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_actual_probe_output_bound_and_nonzero_exit_are_checked(self):
        before=self.raw();original=creation_io._probe
        for script_text in ('print("x"*4097)','import sys\nsys.stderr.write("unreadable source")\nsys.exit(2)'):
            path=self.root/'failed-probe.py'
            def invalid(source,context):return original(source,context,ffprobe=str(path))
            expected='exceeds four MiB' if script_text.startswith('print') else 'cannot be inspected: unreadable source'
            with python_probe(path,script_text+'\n'),patch.object(creation_io,'_probe',invalid),patch.object(creation_io,'MAX_OUTPUT',4096),self.assertRaisesRegex(store.HTTPError,expected):self.create('breakout')
            self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)

    def test_known_accepted_source_change_refuses_creation_and_empty_file_refuses(self):
        self.apply(self.inspect());before=self.raw();count=len(self.manager.values)
        with self.replacement.open('ab') as stream:stream.write(b'not accepted')
        for mode in ('duplicate','subclip','breakout'):
            with self.assertRaisesRegex(store.HTTPError,'Relink'):self.create(mode)
            self.assertEqual(self.raw(),before);self.assertEqual(len(self.manager.values),count)
        self.replacement.write_bytes(b'')
        with self.assertRaises(store.HTTPError):self.create('duplicate')
        self.assertEqual(self.raw(),before)

    def test_lost_success_reply_cannot_replay_same_saved_context(self):
        body=self.body(media_ids=['m']);reply=self.route('media_duplicate',body);after=self.raw();tasks=set(self.manager.values)
        with self.assertRaises(store.HTTPError):self.route('media_duplicate',body)
        self.assertEqual(self.raw(),after);self.assertEqual(set(self.manager.values),tasks)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);self.assertTrue(reply['changed'])

    def test_actual_discrete_breakout_export_channels_and_owned_preparation(self):
        before=self.project();reply=self.create('breakout');self.assertEqual(len(reply['media_ids']),2)
        self.assertEqual(len(reply['preparation']['tasks']),2);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        params,raw=self.audio(before,'m','stereo');reference=array.array('h');reference.frombytes(raw)
        if sys.byteorder!='little':reference.byteswap()
        for index,media in enumerate(reply['media']):
            self.assertEqual(media['audio_alias']['channel_index'],index)
            output,pcm=self.audio(self.project(),media['id'],'channel'+str(index));samples=array.array('h');samples.frombytes(pcm)
            if sys.byteorder!='little':samples.byteswap()
            self.assertEqual(output,params);self.assertEqual(samples[0::2],reference[index::2]);self.assertEqual(samples[1::2],reference[index::2])
        for value in reply['preparation']['tasks']:self.prepare(value['task']['id'])
        after=self.project();self.assertEqual(after['media']['m'],before['media']['m'])
        self.assertEqual(len({after['media'][mid]['proxy'] for mid in reply['media_ids']}),2)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['media'],before['media'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['media'],after['media'])


if __name__=='__main__':unittest.main()
