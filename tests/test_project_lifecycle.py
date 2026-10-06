"""Real project storage, guarded routes and worker lifetime; no HTTP/native UI."""
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
from unittest.mock import patch

import test_project_sync as store
from link_fixture import file_link, directory_link
from test_media_collection import run_async_check
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import project_lifecycle as lifecycle


class Lifecycle(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        names={'_workflow_capture','_prepare_project_action','_commit_prepared_project','projects_list','projects_recent','projects_open','projects_new','projects_save_as','projects_duplicate','projects_sample','_prepare_sample_project','default_project','project_recovery_versions','project_recovery_restore'}
        tree=ast.parse((ROOT/'backend/server.py').read_text());nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        for n in nodes:n.decorator_list=[]
        import subprocess
        import project_recovery
        for name in ('inspect_recovery','RecoveryConflict','preserve_editor_draft','restore_version'):self.env[name]=getattr(project_recovery,name)
        from media_collection import MediaCollectionError
        self.env.update(asyncio=asyncio,subprocess=subprocess,MediaCollectionError=MediaCollectionError,ASSETS=str(ROOT/"assets"))
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
    def request(self,route,**body):return run_async_check(self.env[route](store.Request({'_context':self.current(),**body})))

    def test_open_validates_destination_before_selection_and_source_is_unchanged(self):
        before=self.raw();reply=self.request('projects_open',id='b')
        self.assertEqual(reply['context']['project'],'b');self.assertEqual(self.env['active_id'](),'b');self.assertEqual(self.raw(),before)

    def test_corrupt_and_missing_destination_do_not_replace_active_project(self):
        before=self.raw();target=self.root/'projects/b/project.json';target.write_bytes(b'corrupt')
        for identity in ('b','missing','../a','C:\\project',''):
            with self.assertRaises(store.HTTPError):self.request('projects_open',id=identity)
            self.assertEqual(self.env['active_id'](),'a');self.assertEqual(self.raw(),before)
    def test_linked_destination_file_does_not_replace_active_project(self):
        target=self.root/'projects/b/project.json'
        target.unlink();file_link(target,self.root/'projects/a/project.json')
        with self.assertRaises(store.HTTPError):self.request('projects_open',id='b')
        self.assertEqual(self.env['active_id'](),'a')

    def test_linked_destination_folder_does_not_replace_active_project(self):
        before=self.raw();directory_link(self.root/'projects/alias',self.root/'projects/b')
        with self.assertRaises(store.HTTPError):self.request('projects_open',id='alias')
        self.assertEqual(self.env['active_id'](),'a');self.assertEqual(self.raw(),before)

    def test_stale_and_omitted_context_cannot_open_or_create(self):
        old=self.current();self.edit('Newer saved edit',old)
        folders=set((self.root/'projects').iterdir())
        for route in ('projects_open','projects_new','projects_save_as','projects_duplicate','projects_sample'):
            with self.subTest(route=route),self.assertRaises(store.HTTPError):self.request(route,id='b',_context=old)
            with self.assertRaises(store.HTTPError):run_async_check(self.env[route](store.Request({'id':'b'})))
        self.assertEqual(set((self.root/'projects').iterdir()),folders);self.assertEqual(self.env['active_id'](),'a')

    def test_changed_target_hash_requires_refresh_before_open(self):
        entry=next(r for r in self.env['projects_list']() if r['id']=='b')
        p=self.root/'projects/b/project.json';doc=json.loads(p.read_text());doc['name']='Changed target';p.write_text(json.dumps(doc))
        with self.assertRaises(store.HTTPError) as raised:self.request('projects_open',id='b',_target_sha256=entry['file_sha256'])
        self.assertEqual(raised.exception.status_code,409);self.assertEqual(self.env['active_id'](),'a')

    def test_catalog_exposes_literal_names_distinct_ids_and_damaged_projects(self):
        p=self.root/'projects/a/project.json';doc=json.loads(p.read_text());doc['name']='<img onerror=bad>';p.write_text(json.dumps(doc))
        (self.root/'projects/b/project.json').write_bytes(b'broken')
        rows=self.env['projects_list']();self.assertEqual(len(rows),2);self.assertEqual(next(r['name'] for r in rows if r['id']=='a'),'<img onerror=bad>')
        self.assertIn('error',next(r for r in rows if r['id']=='b'));self.assertEqual(self.env['projects_recent'](),rows)

    def test_new_and_duplicate_publish_complete_files_and_preserve_original_history(self):
        original=self.raw();marker=self.root/'projects/a/snapshots/keep.json';marker.parent.mkdir();marker.write_bytes(b'preserve')
        reply=self.request('projects_new',name='新 project');new=self.env['load_project']();self.assertEqual(new['name'],'新 project');self.assertEqual(reply['id'],self.env['active_id']())
        self.request('projects_open',id='a');reply=self.request('projects_duplicate',name='Working copy')
        self.assertEqual(self.raw(),original);self.assertEqual(marker.read_bytes(),b'preserve');self.assertNotEqual(reply['id'],'a');self.assertEqual(self.env['load_project']()['proposals'],[])
        self.assertFalse((self.root/'projects'/reply['id']/'snapshots').exists())

    def test_save_as_preserves_folder_files_and_uses_captured_document(self):
        f=self.root/'projects/a/content/resource.bin';f.parent.mkdir();f.write_bytes(b'project resource')
        history=self.root/'projects/a/undo_stack.json';history.write_text('{"undo":[],"redo":[]}')
        before=self.raw();reply=self.request('projects_save_as',name='Saved copy')
        folder=self.root/'projects'/reply['id'];self.assertEqual((folder/'content/resource.bin').read_bytes(),f.read_bytes());self.assertEqual((folder/'undo_stack.json').read_bytes(),history.read_bytes())
        self.assertEqual(self.env['load_project']()['name'],'Saved copy');self.assertEqual(self.raw(),before)
        self.assertFalse(list((self.root/'projects').glob('.project-*')))

    def test_copy_conflict_rejects_publication_after_expensive_work(self):
        original=lifecycle.PreparedProject.__init__
        def changed(obj,*args,**kw):
            original(obj,*args,**kw)
            with self.env['LOCK']:
                p=self.env['load_project']();p['name']='Concurrent edit';self.env['save_project'](p)
        with patch.object(lifecycle.PreparedProject,'__init__',changed),self.assertRaises(store.HTTPError):self.request('projects_save_as',name='Must not open')
        self.assertEqual(self.env['active_id'](),'a');self.assertEqual(self.env['load_project']()['name'],'Concurrent edit')
        self.assertEqual({p.name for p in (self.root/'projects').iterdir()},{'a','b'})

    def test_invalid_names_never_create_folders(self):
        for value in ('', '   ', 'x'*241, 4, 'bad\nname'):
            with self.assertRaises(store.HTTPError):self.request('projects_new',name=value)
        self.assertEqual({p.name for p in (self.root/'projects').iterdir()},{'a','b'})

    def test_event_and_broadcast_failure_report_success_with_warning(self):
        async def offline(event):raise OSError('client disconnected')
        with patch.dict(self.env,log_event=lambda *a,**k:(_ for _ in ()).throw(OSError('event disk full')),broadcast=offline):
            reply=self.request('projects_open',id='b')
        self.assertTrue(reply['ok']);self.assertIn('event history',reply['warning']);self.assertIn('refresh',reply['warning']);self.assertEqual(self.env['active_id'](),'b')

    def test_failed_activation_keeps_original_active_and_completed_copy_discoverable(self):
        with patch.dict(self.env,set_active_project=lambda *a:(_ for _ in ()).throw(OSError('sharing denial'))):
            with self.assertRaises(store.HTTPError) as raised:self.request('projects_new',name='Retained copy')
        self.assertIn('saved at',str(raised.exception));self.assertEqual(self.env['active_id'](),'a');self.assertIn('Retained copy',[r['name'] for r in self.env['projects_list']()])

    def test_copy_file_failure_keeps_existing_data_and_discards_only_private_stage(self):
        before=self.raw()
        with patch('media_collection._verified_file',side_effect=OSError('full disk')),self.assertRaises(store.HTTPError):self.request('projects_save_as',name='Not published')
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['active_id'](),'a');self.assertEqual({p.name for p in (self.root/'projects').iterdir()},{'a','b'})

    def test_existing_empty_destination_is_not_replaced(self):
        dest=self.root/'projects/existing';dest.mkdir()
        with self.assertRaisesRegex(lifecycle.ProjectActionError,'already exists'):lifecycle.PreparedProject(self.root,self.doc,'existing')
        self.assertTrue(dest.is_dir())

    def test_failed_publication_retains_project_snapshot_for_inspection(self):
        prepared=lifecycle.PreparedProject(self.root,self.doc,'failed')
        with patch.object(lifecycle.os,'rename',side_effect=OSError('sharing denial')):
            with self.assertRaisesRegex(lifecycle.ProjectActionError,'inspect'):prepared.publish()
        stage=prepared.stage;prepared.close();self.assertTrue((stage/'project.json').is_file());self.assertEqual(self.env['active_id'](),'a')

    def test_worker_is_bounded_and_cancellation_waits_for_owned_thread(self):
        worker=lifecycle.ActionWorker();entered=threading.Event();release=threading.Event();exited=threading.Event()
        def work(check):
            entered.set()
            try:release.wait(4);check()
            finally:exited.set()
        async def run():
            task=asyncio.create_task(worker.run(work));self.assertTrue(await asyncio.to_thread(entered.wait,2))
            try:
                with self.assertRaises(lifecycle.ProjectActionBusy):await worker.run(lambda c:None)
                task.cancel();await asyncio.sleep(.03);self.assertFalse(task.done());self.assertTrue(worker.busy)
                task.cancel();await asyncio.sleep(.03);self.assertFalse(task.done())
            finally:release.set()
            with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue(exited.is_set());self.assertFalse(worker.busy);self.assertEqual(await worker.run(lambda c:'next'),'next')
        try:run_async_check(run())
        finally:release.set();worker.executor.shutdown(wait=True)

    def test_inactive_project_recovery_preserves_current_project_and_requires_its_context(self):
        bad=self.root/'projects/b/project.json';backup=bad.parent/'backups/project_100.json';backup.parent.mkdir();backup.write_bytes(bad.read_bytes());bad.write_bytes(b'broken')
        before=self.raw();catalog=self.env['project_recovery_versions']('b');candidate=catalog['candidates'][0]
        reply=self.request('project_recovery_restore',project='b',candidate=candidate['id'],sha256=candidate['sha256'],current_sha256=catalog['current_sha256'],_context=catalog['origin_context'])
        self.assertTrue(reply['ok']);self.assertEqual(self.env['active_id'](),'a');self.assertEqual(self.raw(),before);self.assertEqual(json.loads(bad.read_text())['name'],'Original')
        self.request('projects_open',id='b');self.assertEqual(self.env['active_id'](),'b')

    def test_inactive_recovery_cannot_follow_a_later_switch_to_its_target(self):
        catalog=self.env['project_recovery_versions']('b');self.env['set_active_project']('b');before=self.raw('b')
        with self.assertRaises(store.HTTPError):self.request('project_recovery_restore',project='b',candidate='current',sha256=catalog['current_sha256'],current_sha256=catalog['current_sha256'],_context=catalog['origin_context'])
        self.assertEqual(self.raw('b'),before)

    def sample_inputs(self):
        folder=self.root/'sample_media';folder.mkdir()
        for name in ('shot_01_open.mp4','shot_02_product.mp4','shot_03_ride.mp4','music_bed.m4a','end_card.png'):(folder/name).write_bytes(b'controlled source')
        self.env['ingest']=lambda path,name:(name,{'id':name,'path':path,'ingest_token':'token','duration':8,'has_video':True,'has_audio':True})
        self.preparations=[];self.env['finish_ingest']=lambda *a:self.preparations.append(a)

    def test_sample_publishes_complete_timeline_then_prepares_captured_media(self):
        self.sample_inputs();before=self.raw();reply=self.request('projects_sample',name='Requested tour title');doc=self.env['load_project']()
        self.assertEqual(doc['name'],'Requested tour title')
        self.assertEqual(self.raw(),before);self.assertEqual(len(doc['media']),5);self.assertEqual(len(self.preparations),5)
        self.assertEqual(len(doc['sequences'][0]['captions']),3);self.assertTrue(all(reply['id'] in args[3] for args in self.preparations))

    def test_sample_probe_failure_does_not_activate_a_partial_project(self):
        self.sample_inputs();before=self.raw();self.env['ingest']=lambda *a:(_ for _ in ()).throw(OSError('source unreadable'))
        with self.assertRaises(OSError):self.request('projects_sample')
        self.assertEqual(self.env['active_id'](),'a');self.assertEqual(self.raw(),before);self.assertEqual({p.name for p in (self.root/'projects').iterdir()},{'a','b'})

    def test_a_broken_active_project_can_be_left_using_its_exact_recovery_receipt(self):
        path=self.root/'projects/a/project.json';path.write_bytes(b'broken current edit');receipt=self.env['project_recovery_versions']()
        reply=run_async_check(self.env['projects_open'](store.Request({'id':'b','_recovery_origin':receipt})))
        self.assertEqual(reply['id'],'b');self.assertEqual(path.read_bytes(),b'broken current edit')
        with self.assertRaises(store.HTTPError):run_async_check(self.env['projects_open'](store.Request({'id':'a','_recovery_origin':receipt})))
        self.assertEqual(self.env['active_id'](),'b')

    def test_unreadable_saved_project_folder_is_not_silently_omitted(self):
        original_walk = lifecycle.os.walk
        source = self.root/'projects/a'
        def denied(path,*args,**kwargs):
            # Inject the source read failure only. Windows shutil.rmtree also
            # uses os.walk; faulting it would separately deny private cleanup.
            if Path(path) == source:
                kwargs['onerror'](PermissionError('directory denied'))
                return iter(())
            return original_walk(path,*args,**kwargs)
        with patch.object(lifecycle.os,'walk',side_effect=denied),self.assertRaises(store.HTTPError):self.request('projects_save_as',name='Refuse missing data')
        self.assertEqual({p.name for p in (self.root/'projects').iterdir()},{'a','b'})

    def test_broken_target_history_can_be_recovered_without_becoming_active(self):
        (self.root/'projects/b/undo_stack.json').write_bytes(b'broken history')
        with self.assertRaises(store.HTTPError):self.request('projects_open',id='b')
        self.assertEqual(self.env['active_id'](),'a');self.assertEqual((self.root/'projects/b/undo_stack.json').read_bytes(),b'broken history')

    def test_cancelled_sample_generation_retires_its_real_ffmpeg_and_private_output(self):
        worker=lifecycle.ActionWorker();holders=[];target=self.root/'cancelled.mkv'
        command=['ffmpeg','-hide_banner','-y','-re','-f','lavfi','-i','color=red:s=32x32:r=10','-t','20','-c:v','ffv1']
        def run(check):holders.append(check.holder);return lifecycle.sample_file(target,command,check)
        async def exercise():
            task=asyncio.create_task(worker.run(run))
            for _ in range(150):
                if holders and holders[0].get('proc') is not None:break
                await asyncio.sleep(.01)
            try:
                self.assertTrue(holders and holders[0].get('proc') is not None)
                process=holders[0]['proc'];task.cancel()
                with self.assertRaises(asyncio.CancelledError):await asyncio.wait_for(task,4)
                self.assertIsNotNone(process.poll());self.assertFalse(target.exists());self.assertFalse(list(self.root.glob('filmocity-render-*')))
            finally:
                if not task.done():task.cancel()
        try:run_async_check(exercise())
        finally:worker.shutdown()

    def test_sample_encoder_failure_preserves_existing_files_and_never_publishes_partial(self):
        worker=lifecycle.ActionWorker();target=self.root/'failed.mkv';before=self.raw()
        async def run():
            with self.assertRaises(lifecycle.ProjectActionError):await worker.run(lambda check:lifecycle.sample_file(target,['ffmpeg','-v','error','-f','lavfi','-i','color=s=16x16','-t','.1','-c:v','missing-filmocity-test-encoder'],check))
        try:run_async_check(run())
        finally:worker.shutdown()
        self.assertFalse(target.exists());self.assertEqual(self.raw(),before);self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_slow_project_catalog_does_not_hold_the_edit_lock(self):
        entered=threading.Event();release=threading.Event();original=lifecycle.read
        def blocked(path):entered.set();release.wait(4);return original(path)
        async def run():
            with patch.object(lifecycle,'read',side_effect=blocked):
                task=asyncio.create_task(asyncio.to_thread(self.env['projects_list']))
                try:
                    self.assertTrue(await asyncio.to_thread(entered.wait,2))
                    acquired=self.env['LOCK'].acquire(blocking=False);self.assertTrue(acquired)
                    if acquired:self.env['LOCK'].release()
                    reply=await self.env['patch_project'](store.Request({'_context':self.current(),'ops':[{'op':'set','path':'/name','value':'Edit during list read'}]}))
                    self.assertTrue(reply['ok']);self.assertFalse(task.done())
                finally:release.set()
                await asyncio.wait_for(task,3)
        run_async_check(run());self.assertEqual(self.env['load_project']()['name'],'Edit during list read')


if __name__=='__main__':unittest.main()
