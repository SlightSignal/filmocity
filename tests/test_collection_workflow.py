"""Real collection workers, project/history transactions and failure boundaries.

Routes use controlled request/framework adapters. No HTTP/native UI acceptance.
"""
import ast
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import threading
import unittest
from link_fixture import file_link, directory_link
from unittest.mock import patch

import test_project_sync as store
from test_media_collection import run_async_check
from test_background_tasks import wait_for
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT/'backend'))
from background_tasks import TaskManager, TaskError
import collection_workflow as collect
import media_collection as mc
import project_lifecycle as lifecycle


class CollectionFixture(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        names = {'_workflow_capture', 'projects_collect', '_task_collect', '_commit_collection', '_apply_collection',
                 '_owned_task', 'background_task_list', 'background_task_cancel', 'background_task_apply', 'background_task_retry'}
        tree = ast.parse((ROOT/'backend/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        for node in nodes: node.decorator_list=[]
        self.env.update(asyncio=asyncio, TaskError=TaskError, MediaCollectionError=mc.MediaCollectionError)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.worker=lifecycle.ActionWorker();self.worker_patch=patch.object(lifecycle,'COPY_WORKER',self.worker);self.worker_patch.start()
        self.source=self.root/'original É.mov';self.source.write_bytes(b'verified original bytes')
        self.project=self.env['load_project']();self.project['media']={'m':{'id':'m','name':'Original','path':str(self.source),'has_video':True,'ingest_token':'t'}}
        self.env['save_project'](self.project)
        self.manager=TaskManager(self.root,{'collect':self.env['_task_collect']},workers=1);self.env['TASKS']=self.manager
        self.releases=[]
    def tearDown(self):
        for event in self.releases:event.set()
        self.manager.shutdown(5);self.worker.shutdown();self.worker_patch.stop();super().tearDown()
    def event(self):
        event=threading.Event();self.releases.append(event);return event
    def request(self,route,identity=None,body=None):
        req=store.Request({'_context':self.current(),**(body or {})})
        return run_async_check(self.env[route](req) if identity is None else self.env[route](identity,req))
    def queue(self,**kw):return self.request('projects_collect',body=kw)['task']['id']
    def ready(self):
        identity=self.queue();value=wait_for(self.manager,identity)
        self.assertEqual(value['record']['status'],'ready',value);return identity
    def apply(self,identity,**body):return self.request('background_task_apply',identity,body)
    def mutate(self,fn):
        project=self.env['load_project']();fn(project);self.env['save_project'](project)
    def destination(self,identity):return Path(self.manager.get(identity)['result']['paths']['m'])


class Collections(CollectionFixture):
    def test_queue_and_completion_never_mutate_project_then_one_undo_redo_restores_exact_paths(self):
        before=self.raw();identity=self.ready();self.assertEqual(self.raw(),before)
        reply=self.apply(identity);self.assertTrue(reply['ok'])
        after=self.env['load_project']();self.assertNotEqual(after['media']['m']['path'],str(self.source))
        self.assertEqual(self.destination(identity).read_bytes(),self.source.read_bytes())
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.request('undo');self.assertEqual(self.env['load_project']()['media'],self.project['media'])
        self.assertTrue(self.destination(identity).is_file());self.request('redo')
        self.assertEqual(self.env['load_project']()['media'],after['media'])

    def test_missing_or_stale_context_never_queues_work(self):
        for body in ({},{'_context':{**self.current(),'revision':'stale'}}):
            with self.assertRaises(store.HTTPError):run_async_check(self.env['projects_collect'](store.Request(body)))
        self.assertEqual(self.manager.catalog()['tasks'],[])

    def test_project_switch_refuses_apply_and_never_changes_original_or_target(self):
        identity=self.ready();before=self.raw();self.env['set_active_project']('b');other=self.raw('b')
        with self.assertRaises(store.HTTPError):self.apply(identity)
        self.assertEqual(self.raw(),before);self.assertEqual(self.raw('b'),other)
        self.assertTrue(self.destination(identity).is_file())

    def test_timeline_and_presentation_edits_survive_collection_and_do_not_duplicate_task(self):
        identity=self.ready();self.mutate(lambda p:(p.update(name='Edited while copying'),p['media']['m'].update(name='Renamed',thumb='new-thumbnail')))
        self.assertEqual(self.queue(),identity);self.apply(identity)
        project=self.env['load_project']();self.assertEqual(project['name'],'Edited while copying')
        self.assertEqual(project['media']['m']['name'],'Renamed');self.assertEqual(project['media']['m']['thumb'],'new-thumbnail')

    def test_changed_library_refuses_apply_without_losing_result(self):
        identity=self.ready();self.mutate(lambda p:p['media']['m'].update(ingest_token='replacement'));before=self.raw()
        with self.assertRaises(store.HTTPError) as caught:self.apply(identity)
        self.assertEqual(caught.exception.status_code,422);self.assertEqual(self.raw(),before)
        self.assertEqual(self.manager.get(identity)['record']['status'],'ready')

    def test_changed_collected_file_or_manifest_refuses_relink(self):
        for target in ('file','manifest'):
            with self.subTest(target=target):
                identity=self.ready();value=self.manager.get(identity)
                path=self.destination(identity) if target=='file' else Path(value['result']['manifest'])
                old=path.read_bytes();path.write_bytes(b'damaged');before=self.raw()
                with self.assertRaises(store.HTTPError):self.apply(identity)
                self.assertEqual(self.raw(),before);path.write_bytes(old)

    def test_offline_original_is_allowed_but_replaced_original_is_not(self):
        identity=self.ready();original=self.source.read_bytes();self.source.write_bytes(b'replacement')
        with self.assertRaises(store.HTTPError):self.apply(identity)
        self.source.unlink();self.apply(identity);self.assertEqual(self.destination(identity).read_bytes(),original)

    def test_valid_proxy_signature_moves_to_new_source_identity_and_undo_restores_it(self):
        from media_preview import digest
        from task_inputs import source_stamp
        self.mutate(lambda p:p['media']['m'].update(proxy='existing-proxy.mp4',proxy_info={'source_signature':digest(source_stamp(p['media']['m']))}))
        original=self.env['load_project']()['media']['m'];identity=self.ready();self.apply(identity)
        media=self.env['load_project']()['media']['m']
        self.assertEqual(media['proxy_info']['source_signature'],digest(source_stamp(media)));self.assertEqual(media['proxy'],'existing-proxy.mp4')
        self.request('undo');self.assertEqual(self.env['load_project']()['media']['m'],original)

    def test_old_preparation_cannot_publish_into_relocated_media(self):
        self.mutate(lambda p:p['media']['m'].update(status='ingesting',task_id='old-task'))
        identity=self.ready();self.apply(identity);media=self.env['load_project']()['media']['m']
        self.assertEqual(media['status'],'ready');self.assertNotIn('task_id',media);self.assertIn('Prepare media',media['ingest_error'])
        self.assertNotEqual(media['path'],str(self.source))

    def test_lost_apply_receipt_or_reply_cannot_add_another_undo_entry(self):
        identity=self.ready();save=self.manager.store.save
        def denied(value):
            if value['record']['status']=='applied':raise OSError('receipt denied')
            return save(value)
        with patch.object(self.manager.store,'save',side_effect=denied):
            result=self.apply(identity);self.assertIn('history',result['warning'])
        before=self.raw();reply=self.apply(identity);self.assertIn('already applied',reply['message'])
        self.assertEqual(self.raw(),before);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_project_commit_failure_rolls_back_both_files_and_retains_copies(self):
        identity=self.ready();before=self.raw();old_history=self.env['read_undo_history']('a')
        with patch.dict(self.env,save_project=lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full'))):
            with self.assertRaises(OSError):self.apply(identity)
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),old_history)
        self.assertTrue(self.destination(identity).is_file());self.assertEqual(self.manager.get(identity)['record']['status'],'ready')

    def test_event_and_broadcast_failures_return_confirmed_success_with_warning(self):
        identity=self.ready()
        async def fail(event):raise OSError('broadcast denied')
        with patch.dict(self.env,log_event=lambda *a,**kw:(_ for _ in ()).throw(OSError('event denied')),broadcast=fail):result=self.apply(identity)
        self.assertTrue(result['ok']);self.assertIn('event',result['warning']);self.assertIn('refresh',result['warning'])

    def test_copy_failure_and_missing_original_never_relink_or_publish_partial_generation(self):
        before=self.raw()
        with patch.object(mc,'_verified_file',side_effect=OSError('copy full')):
            identity=self.queue();self.assertEqual(wait_for(self.manager,identity)['record']['status'],'error')
        self.assertEqual(self.raw(),before);self.assertEqual(list((self.root/'collected/a').iterdir()),[])
        self.source.unlink()
        with self.assertRaises(store.HTTPError):self.queue()

    def test_cancellation_stops_owned_copy_and_removes_only_staging(self):
        entered=self.event();release=self.event();real=mc._verified_file
        def slow(*args,**kwargs):entered.set();release.wait(4);return real(*args,**kwargs)
        before=self.raw()
        with patch.object(mc,'_verified_file',side_effect=slow):
            identity=self.queue();self.assertTrue(entered.wait(3));self.manager.cancel(identity);release.set()
            self.assertEqual(wait_for(self.manager,identity)['record']['status'],'cancelled')
        self.assertEqual(self.raw(),before);self.assertEqual(list((self.root/'collected/a').iterdir()),[])

    def test_ready_result_persists_across_restart_and_is_never_automatically_applied(self):
        before=self.raw();identity=self.ready();self.manager.shutdown(5)
        self.manager=TaskManager(self.root,{'collect':self.env['_task_collect']});self.env['TASKS']=self.manager
        self.assertEqual(self.manager.get(identity)['record']['status'],'ready');self.assertEqual(self.raw(),before)
        self.apply(identity);self.assertEqual(self.manager.get(identity)['record']['status'],'applied')

    def test_discard_keeps_copies_and_current_paths_and_explicit_retry_is_separate(self):
        identity=self.ready();before=self.raw();path=self.destination(identity);self.manager.cancel(identity)
        self.assertEqual(self.raw(),before);self.assertTrue(path.is_file())
        result=self.request('background_task_retry',identity);new=result['task']['id'];self.assertNotEqual(identity,new)
        self.assertEqual(wait_for(self.manager,new)['record']['status'],'ready');self.assertEqual(self.raw(),before)

    def test_changed_original_before_worker_starts_is_rejected(self):
        self.manager.shutdown();self.manager=TaskManager(self.root,{'collect':self.env['_task_collect']},start=False);self.env['TASKS']=self.manager
        identity=self.queue();self.source.write_bytes(b'replaced while queued');value=self.manager.get(identity)
        from background_tasks import TaskContext
        with self.assertRaisesRegex(mc.MediaCollectionError,'changed before'):collect.prepare(value['payload'],TaskContext(self.manager,identity))
        self.assertFalse((self.root/'collected').exists())

    def test_edit_during_apply_verification_is_preserved_and_result_stays_ready(self):
        identity=self.ready();real=collect.verify
        def verify(*args,**kwargs):
            after=real(*args,**kwargs);self.mutate(lambda p:p.update(name='Concurrent saved edit'));return after
        with patch.object(collect,'verify',side_effect=verify),self.assertRaises(store.HTTPError) as caught:self.apply(identity)
        self.assertEqual(caught.exception.status_code,409);self.assertEqual(self.env['load_project']()['name'],'Concurrent saved edit')
        self.assertEqual(self.manager.get(identity)['record']['status'],'ready')

    def test_apply_hashing_does_not_block_event_loop_and_repeated_cancel_joins_worker(self):
        identity=self.ready();entered=self.event();release=self.event();real=collect._verified_file
        def slow(*args,**kwargs):entered.set();release.wait(4);return real(*args,**kwargs)
        async def run():
            with patch.object(collect,'_verified_file',side_effect=slow):
                task=asyncio.create_task(self.env['background_task_apply'](identity,store.Request({'_context':self.current()})))
                try:
                    while not entered.is_set():await asyncio.sleep(.01)
                    self.assertFalse(task.done());task.cancel();await asyncio.sleep(.01);task.cancel();await asyncio.sleep(.01)
                    self.assertFalse(task.done())
                finally:release.set()
                with self.assertRaises(asyncio.CancelledError):await task
        before=self.raw();run_async_check(run());self.assertEqual(self.raw(),before)
        self.assertEqual(self.manager.get(identity)['record']['status'],'ready');self.assertFalse(self.worker.busy)

    def test_numbered_frames_subclips_duplicates_and_existing_generations_are_preserved(self):
        paths=[]
        for i in (12,13):
            path=self.root/f'frame{i:04}.png';path.write_bytes(f'frame {i}'.encode());paths.append(path)
        def change(p):
            p['media']={'m':{'id':'m','path':str(self.root/'frame%04d.png'),'sequence_frames':2,'input_opts':['-start_number','12']},
                        'sub':{'id':'sub','subclip_of':'m','path':'unused-parent.mov','sub_in':.1},
                        'same':{'id':'same','path':str(self.root/'frame%04d.png'),'sequence_frames':2,'input_opts':['-start_number','12']}}
        self.mutate(change);identity=self.ready();self.assertEqual(self.manager.get(identity)['result']['copied'],2);self.apply(identity)
        p=self.env['load_project']();self.assertEqual(p['media']['m']['path'],p['media']['sub']['path']);self.assertEqual(p['media']['m']['path'],p['media']['same']['path'])
        from preflight import media_files
        self.assertEqual([Path(n).read_bytes() for n in media_files(p['media']['m'])],[n.read_bytes() for n in paths])
        again=self.ready();self.assertEqual(self.manager.get(again)['result']['copied'],0);self.assertEqual(self.manager.get(again)['result']['reused'],2)

    def test_linked_source_is_refused(self):
        link=self.root/'linked.mov';file_link(link,self.source)
        self.mutate(lambda p:p['media']['m'].update(path=str(link)))
        with self.assertRaises(store.HTTPError):self.queue()

    def test_linked_destination_is_refused(self):
        target=self.root/'elsewhere';target.mkdir();directory_link(self.root/'collected',target)
        identity=self.queue();self.assertEqual(wait_for(self.manager,identity)['record']['status'],'error');self.assertEqual(list(target.iterdir()),[])

    def test_orderly_shutdown_cannot_cancel_a_published_ready_result(self):
        entered=self.event();release=self.event();real=mc.MediaCollection.publish
        def publish(collection):entered.set();release.wait(4);return real(collection)
        before=self.raw()
        with patch.object(mc.MediaCollection,'publish',publish):
            identity=self.queue();self.assertTrue(entered.wait(3))
            with self.assertRaises(TaskError):self.manager.cancel(identity)
            self.assertFalse(self.manager.shutdown(.01));release.set()
            value=wait_for(self.manager,identity);self.assertEqual(value['record']['status'],'ready')
        self.assertEqual(self.raw(),before);self.assertTrue(self.destination(identity).is_file())
        self.assertTrue(self.manager.shutdown(5))

    def test_preparation_receipt_failure_keeps_verified_copies_and_warning(self):
        save=self.manager.store.save
        def denied(value):
            if value['record']['status']=='ready':raise OSError('final receipt denied')
            return save(value)
        before=self.raw()
        with patch.object(self.manager.store,'save',side_effect=denied):
            identity=self.ready();value=self.manager.get(identity);self.assertIn('history',value['record']['warning'])
        self.assertEqual(self.raw(),before);self.assertTrue(self.destination(identity).is_file())
        # A saved earlier receipt cannot invent a completed result on restart.
        self.manager.shutdown(5);self.manager=TaskManager(self.root,{'collect':self.env['_task_collect']},start=False);self.env['TASKS']=self.manager
        value=self.manager.get(identity);self.assertEqual(value['record']['status'],'interrupted');self.assertIsNone(value['result'])

    def test_undo_does_not_make_an_applied_task_replayable(self):
        identity=self.ready();self.apply(identity);self.request('undo');before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(identity)
        self.assertEqual(self.raw(),before);self.assertTrue(self.destination(identity).is_file())

    def test_manifest_limit_is_checked_before_publication_not_only_during_apply(self):
        before=self.raw()
        with patch.object(collect,'MAX_MANIFEST_BYTES',16):
            identity=self.queue();value=wait_for(self.manager,identity)
        self.assertEqual(value['record']['status'],'error');self.assertIn('manifest exceeds',value['record']['message'])
        self.assertEqual(self.raw(),before);self.assertEqual(list((self.root/'collected/a').iterdir()),[])


if __name__=='__main__':unittest.main(verbosity=2)
