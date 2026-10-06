"""Actual sequence creation route/store/history; controlled HTTP wrappers."""
import ast
import asyncio
import copy
from pathlib import Path
import sys
import threading
import unittest
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import sequence_creation
import render
from render_context import RenderContext
import test_source_creation_workflow as source
import test_project_sync as store
from test_media_collection import run_async_check


class StoreSequenceCreation(source.StoreSourceCreation):
    for _name in dir(source.StoreSourceCreation):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'sequence_create','_commit_sequence_creation'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)

    def create_sequence(self,**fields):
        return self.route('sequence_create',self.body(mode='source',sequence='s',**fields))

    def test_empty_and_source_each_create_one_complete_undo_without_tasks(self):
        before=self.project();reply=self.route('sequence_create',self.body(mode='empty',sequence='s',name='Empty'));middle=self.project()
        self.assertEqual(middle['sequences'][:-1],before['sequences']);self.assertEqual(middle['media'],before['media'])
        self.assertEqual(reply['sequence'],middle['sequences'][-1]['id']);self.assertIsNone(reply['clip_id'])
        reply=self.create_sequence(media_id='m',name='Source');after=self.project()
        self.assertEqual(after['sequences'][:-1],middle['sequences']);self.assertEqual(after['media'],before['media'])
        self.assertEqual(after['sequences'][-1]['tracks'][1]['clips'][0]['out'],6)
        history=self.env['read_undo_history']('a')['undo'];self.assertEqual(len(history),2)
        self.assertTrue(all(e['tool']=='workflow_sequence_create' for e in history));self.assertFalse(self.manager.values)
        for expected in (middle,before):self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],expected['sequences'])
        for expected in (middle,after):self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],expected['sequences'])

    def test_unknown_duration_refuses_before_any_saved_sequence_or_history(self):
        project=self.project();project['media']['m']['duration']=None;self.env['save_project'](project);before=self.raw()
        with self.assertRaisesRegex(store.HTTPError,'duration'):self.create_sequence(media_id='m')
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo']);self.assertFalse(self.manager.values)

    def test_tail_channel_sequence_decodes_exact_selected_window_without_offset_twice(self):
        before=self.children();parent=before['media']['m'];child=before['media']['alias']
        child.update(sub_in=2,duration=1,interpret_fps=60,audio_alias={'version':1,'physical_media_id':'m','source_media_id':'m','channel_index':1})
        self.env['save_project'](before);reply=self.create_sequence(media_id='alias');after=self.project()
        path=self.root/'created-audio.wav'
        with RenderContext(scratch_parent=str(self.root)) as context:
            render.render(after,reply['sequence'],str(path),{'format':'audio','acodec':'wav'},context=context)
        with wave.open(str(path),'rb') as stream:actual=(stream.getparams(),stream.readframes(stream.getnframes()))
        reference=copy.deepcopy(before);reference['sequences']=[{'id':'oracle','width':64,'height':48,'fps':30,
            'tracks':[{'id':'a','kind':'audio','index':0,'clips':[{'id':'reference','media_id':'alias',
            'start':0,'in_':0,'out':1,'speed':1,'audio':{'maintain_pitch':True}}]}]}]
        reference_path=self.root/'selected-oracle.wav'
        with RenderContext(scratch_parent=str(self.root)) as context:
            render.render(reference,'oracle',str(reference_path),{'format':'audio','acodec':'wav'},context=context)
        with wave.open(str(reference_path),'rb') as stream:oracle=(stream.getparams(),stream.readframes(stream.getnframes()))
        self.assertEqual(actual,oracle);self.assertEqual(after['media'],before['media']);self.assertEqual(after['sequences'][:-1],before['sequences'])

    def test_missing_stale_foreign_context_and_policy_refuse_without_save(self):
        before=(self.raw(),self.raw('b'));owner=self.current()
        with self.assertRaises(store.HTTPError):self.route('sequence_create',{'mode':'empty','sequence':'s'})
        self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.route('sequence_create',{'_context':owner,'mode':'empty','sequence':'s'})
        self.env['set_active_project']('a');(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
        with self.assertRaises(store.HTTPError):self.create_sequence(media_id='m',actor='agent')
        self.assertEqual((self.raw(),self.raw('b')),before);self.assertFalse(self.env['read_undo_history']('a')['undo'])

    def test_offloop_owner_revision_and_policy_changes_refuse_atomic_commit(self):
        original=sequence_creation.plan
        for changed in ('owner','revision','policy'):
            started,release=threading.Event(),threading.Event()
            def delayed(*a,**kw):
                result=original(*a,**kw);started.set();release.wait(5);return result
            async def scenario():
                pending=asyncio.create_task(self.env['sequence_create'](store.Request(self.body(mode='source',sequence='s',media_id='m',actor='agent'))))
                for _ in range(1000):
                    if started.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(started.is_set())
                if changed=='owner':self.env['set_active_project']('b')
                elif changed=='revision':
                    await self.env['patch_project'](store.Request({'_context':self.current(),'actor':'human',
                        'ops':[{'op':'set','path':'/name','value':'Concurrent rename'}]}))
                else:(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
                expected=(self.raw('a'),self.raw('b'));release.set()
                with self.assertRaises(store.HTTPError):await pending
                self.assertEqual((self.raw('a'),self.raw('b')),expected)
            with patch.object(sequence_creation,'plan',delayed):run_async_check(scenario())
            self.env['set_active_project']('a')
            if changed=='policy':(self.root/'settings.json').unlink()
        self.assertEqual(len(self.project()['sequences']),1)

    def test_cancellation_during_readonly_planning_discards_result(self):
        original=sequence_creation.plan;started,release=threading.Event(),threading.Event();before=self.raw()
        def delayed(*a,**kw):result=original(*a,**kw);started.set();release.wait(5);return result
        async def scenario():
            pending=asyncio.create_task(self.env['sequence_create'](store.Request(self.body(mode='empty',sequence='s'))))
            for _ in range(1000):
                if started.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set());pending.cancel();release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        with patch.object(sequence_creation,'plan',delayed):run_async_check(scenario())
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo'])

    def test_cancelled_saved_acknowledgement_joins_once_and_stale_replay_refuses(self):
        entered=asyncio.Event();release=asyncio.Event();body=self.body(mode='empty',sequence='s')
        async def blocked(event):entered.set();await release.wait()
        original=self.env['broadcast'];self.env['broadcast']=blocked
        async def scenario():
            pending=asyncio.create_task(self.env['sequence_create'](store.Request(body)));await entered.wait();pending.cancel();await asyncio.sleep(.02)
            self.assertFalse(pending.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        try:run_async_check(scenario())
        finally:self.env['broadcast']=original
        self.assertEqual(len(self.project()['sequences']),2);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);before=self.raw()
        with self.assertRaises(store.HTTPError):self.route('sequence_create',body)
        self.assertEqual(self.raw(),before)


if __name__=='__main__':unittest.main()
