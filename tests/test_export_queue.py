"""Actual queue/store functions, isolated files, and real short media exports.

Only framework wrappers/background-worker startup are controlled. Native
Windows file sharing, process shutdown and browser behavior remain separate.
"""
import ast
import concurrent.futures
import copy
import json
import math
import os
from pathlib import Path
import queue
import subprocess
import threading
import time
from types import SimpleNamespace
import unittest
from unittest import mock

from test_project_sync import ProjectStoreFixture, ROOT, Request, HTTPError
import test_rendered_preview as preview
import export_storage as storage
import render
import job_history
from PIL import Image
from preflight import inspect_resources, require_resources


class StopWorker(Exception): pass


class BoundedQueue(queue.Queue):
    def get(self, *args, **kwargs):
        try: return super().get(block=False)
        except queue.Empty: raise StopWorker()


class ExportQueue(ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        self.doc.update(media={}, sequences=[{'id': 's', 'name': 'Sequence', 'width': 64, 'height': 48,
            'fps': 24, 'duration': .5, 'tracks': [], 'captions': [], 'markers': []}])
        for pid in ('a', 'b'): (self.root / f'projects/{pid}/project.json').write_text(json.dumps(self.doc))
        self.env.update(JOBS={}, RENDER_Q=BoundedQueue(), RENDER_PROCS={}, RENDER_WORKERS=[],
            RENDER_STATE_LOCK=threading.RLock(), threading=threading, math=math,
            inspect_resources=inspect_resources, require_resources=require_resources,
            do_render=render.render, render_incremental=render.render_incremental,
            render_qa=lambda path, preset, **kw: {'status': 'not_run', 'flags': ['Worker QA controlled; output independently decoded in this fixture.']},
            HTMLResponse=lambda text: text)
        names = {'check_render_resources', 'start_render', 'render_workers', '_render_worker', '_record_render_event',
                 'restore_render_history', 'jobs_list', 'render_status', 'render_cancel', 'review_job', 'review_project', 'review_page', 'review_notes', 'review_note_add', 'renders_manifest'}
        tree = ast.parse((ROOT / 'backend/server.py').read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)
        self.env['REVIEW_HTML'] = next(ast.literal_eval(n.value) for n in tree.body if isinstance(n, ast.Assign) and
                                      any(isinstance(t, ast.Name) and t.id == 'REVIEW_HTML' for t in n.targets))
        self.workers = self.env['render_workers']; self.env['render_workers'] = lambda: None

    def invoke(self, route, body): return preview.RenderedPreview.run_async(self, self.env[route](body))

    def start(self, name='export', preset=None, project=None):
        return self.env['start_render'](project or self.doc, 's', preset if preset is not None else {'vcodec': 'ffv1'},
                                        name, 'human', context=self.current())

    def drain(self):
        with self.assertRaises(StopWorker): self.env['_render_worker']()
        self.assertEqual(self.env['RENDER_Q'].unfinished_tasks, 0)
        self.assertFalse(self.env['RENDER_PROCS'])

    def completed(self, **kw):
        job = self.start(**kw); job['status'] = 'done'; return job

    def test_same_name_concurrent_reservations_never_reuse_existing_or_each_other(self):
        old = self.root / 'renders/export.mp4'; old.parent.mkdir(); old.write_bytes(b'approved old output')
        def start(_): return self.start(name='export')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            jobs = list(pool.map(start, range(24)))
        self.assertEqual(len({j['out'] for j in jobs}), 24)
        self.assertEqual(len({j['command_log'] for j in jobs}), 24)
        self.assertEqual(len(self.env['JOBS']), 24); self.assertEqual(self.env['RENDER_Q'].qsize(), 24)
        self.assertEqual(old.read_bytes(), b'approved old output')
        self.assertTrue(all(Path(storage.output_path(self.root/'renders', j['out'])).parent.is_dir() for j in jobs))

    def test_reservation_collision_including_restart_and_files_is_not_adopted(self):
        old = self.root/'renders/job-1111111111111111'; old.parent.mkdir(); old.mkdir(); (old/'keep').write_bytes(b'old')
        (old.parent/'job-2222222222222222').write_bytes(b'not a directory')
        with mock.patch.object(storage.uuid, 'uuid4', side_effect=[SimpleNamespace(hex=n*32) for n in '123']):
            dest = storage.reserve_export(old.parent, 'export', {})
        self.assertEqual(dest['id'], '3'*16); self.assertEqual((old/'keep').read_bytes(), b'old')
        with mock.patch.object(storage.uuid, 'uuid4', return_value=SimpleNamespace(hex='1'*32)):
            with self.assertRaisesRegex(OSError, 'unique export'): storage.reserve_export(old.parent, 'export', {})

    def test_names_and_extensions_are_safe_for_windows_and_keep_useful_unicode(self):
        for name in ['CON', 'nul', 'LPT1', 'COM¹', 'LPT³', 'aux']:
            self.assertEqual(storage.safe_name(name), '_'+name)
        self.assertEqual(storage.safe_name(" Émile's cut /:*? "), 'Émiles_cut')
        self.assertEqual(storage.safe_name('..'), 'export')
        self.assertLessEqual(len(storage.safe_name('𐐀'*150).encode('utf-16-le')), 160)
        for preset in [{'format':'audio','acodec':'../../outside'}, {'format':'unknown'}, {'container':'../mov'}]:
            with self.assertRaises(HTTPError) as error: self.start(preset=preset)
            self.assertEqual(error.exception.status_code, 422)
        self.assertFalse(self.env['JOBS']); self.assertTrue(self.env['RENDER_Q'].empty())
        self.assertFalse((self.root/'renders').exists())

    def test_jobs_freeze_project_preset_report_and_context_inputs(self):
        project, preset = copy.deepcopy(self.doc), {'vcodec':'ffv1', 'metadata': {'key':'before'}}
        context=self.current(); report={'ok':True, 'issues':[]}
        job=self.env['start_render'](project,'s',preset,'test','human',report,context=context)
        project['sequences'][0]['duration']=999; preset['metadata']['key']='changed'; report['issues'].append('later'); context['project']='other'
        item=self.env['RENDER_Q'].get_nowait()
        self.assertEqual(item[1]['sequences'][0]['duration'],.5); self.assertEqual(item[3]['metadata']['key'],'before')
        job['preset']['metadata']['key']='job metadata changed'
        self.assertEqual(item[3]['metadata']['key'],'before'); self.assertEqual(job['context']['project'],'a'); self.assertEqual(job['preflight']['issues'],[])

    def test_worker_and_queue_start_failures_leave_no_orphan_job_or_reservation(self):
        self.env['render_workers']=mock.Mock(side_effect=RuntimeError('thread start failed'))
        with self.assertRaisesRegex(RuntimeError,'thread start failed'): self.start()
        self.assertFalse(self.env['JOBS']); self.assertEqual(list((self.root/'renders').iterdir()),[])
        self.env['render_workers']=lambda:None
        with mock.patch.object(self.env['RENDER_Q'],'put_nowait',side_effect=queue.Full):
            with self.assertRaises(queue.Full): self.start()
        self.assertFalse(self.env['JOBS']); self.assertEqual(list((self.root/'renders').iterdir()),[])

    def test_worker_startup_is_serialized_under_simultaneous_submissions(self):
        (self.root/'settings.json').write_text(json.dumps({'prefs':{'render_workers':3}}))
        started=[]
        class Worker:
            def __init__(self, **kwargs): self.alive=False
            def start(self): time.sleep(.005); self.alive=True; started.append(self)
            def is_alive(self): return self.alive
        # Replace only the production function's thread factory; executor uses
        # real threads, making the former check/start/append race observable.
        self.env['threading']=SimpleNamespace(Thread=Worker)
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            list(pool.map(lambda _:self.workers(),range(24)))
        self.assertEqual(len(started),3)
        started[0].alive=False; self.workers()
        self.assertEqual(len(started),4); self.assertEqual(len(self.env['RENDER_WORKERS']),3)

    def test_real_repeated_video_exports_keep_both_outputs_logs_and_manifest(self):
        first=self.start(name='same'); self.drain(); first_file=Path(storage.output_path(self.root/'renders',first['out']))
        original=first_file.read_bytes()
        second=self.start(name='same'); self.drain()
        self.assertEqual(first_file.read_bytes(),original)
        for job in (first,second):
            self.assertEqual(job['status'],'done',job.get('error'))
            path=storage.output_path(self.root/'renders',job['out'])
            decoded=subprocess.run(['ffmpeg','-v','error','-i',path,'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
            self.assertEqual(len(decoded),64*48*3*12)
            self.assertTrue(Path(storage.output_path(self.root/'renders',job['command_log'])).is_file())
        self.assertEqual({r['file'] for r in self.env['renders_manifest']()},{first['out'],second['out']})

    def test_real_png_sequences_with_same_name_and_video_name_are_independent(self):
        one=self.start(name='CON',preset={'format':'png_sequence'}); two=self.start(name='CON',preset={'format':'png_sequence'}); self.drain()
        for job in (one,two):
            self.assertEqual(job['status'],'done',job.get('error'))
            manifest=Path(storage.output_path(self.root/'renders',job['out']))
            self.assertTrue(manifest.is_file()); self.assertEqual(job['qa']['frame_count'],12)
            self.assertTrue(Path(storage.output_path(self.root/'renders',job['frames']['first_frame'])).is_file())
        self.assertNotEqual(one['out'],two['out'])

    def test_real_parallel_same_named_exports_keep_distinct_decoded_pixels(self):
        jobs=[]
        for name,color in [('red',(255,0,0)),('blue',(0,0,255))]:
            path=self.root/(name+'.png');Image.new('RGB',(64,48),color).save(path)
            project=copy.deepcopy(self.doc)
            project['media']={'m':{'id':'m','path':str(path),'has_video':True,'has_audio':False,'is_image':True,'width':64,'height':48}}
            project['sequences'][0]['tracks']=[{'id':'V1','kind':'video','index':1,'clips':[{'id':'c','media_id':'m','start':0,'in_':0,'out':.5}]}]
            jobs.append((self.start(name='same',project=project),color))
        def work():
            try:self.env['_render_worker']()
            except StopWorker:pass
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures=[pool.submit(work) for _ in range(2)]
            for future in futures:future.result(timeout=20)
        self.assertEqual(self.env['RENDER_Q'].unfinished_tasks,0);self.assertFalse(self.env['RENDER_PROCS'])
        for job,color in jobs:
            self.assertEqual(job['status'],'done',job.get('error'))
            path=storage.output_path(self.root/'renders',job['out'])
            pixels=subprocess.run(['ffmpeg','-v','error','-i',path,'-map','0:v:0','-f','rawvideo','-pix_fmt','rgb24','-'],capture_output=True,check=True).stdout
            self.assertEqual(len(pixels),64*48*3*12)
            for offset in (0,64*48*3*6,64*48*3*11):
                self.assertLessEqual(max(abs(a-b) for a,b in zip(pixels[offset:offset+3],color)),2)

    def test_export_wait_is_visible_in_both_status_routes_and_cancels_without_output(self):
        import work_budget
        from test_work_budget import wait_for
        budget=work_budget.WorkBudget(capacity=1);job=self.start()
        def run():
            try:self.env['_render_worker']()
            except StopWorker:pass
        with mock.patch.object(work_budget,'BUDGET',budget),work_budget.work(),concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(run)
            try:
                wait_for(lambda:self.env['render_status'](job['id']).get('resource',{}).get('state')=='waiting')
                detail=self.env['render_status'](job['id']);catalog=self.env['jobs_list']()
                self.assertEqual(catalog[0]['resource']['state'],'waiting');self.assertEqual(detail['status'],'running')
                self.assertNotIn('resource',job);self.assertNotIn('proc',self.env['RENDER_PROCS'][job['id']])
                self.assertEqual(self.invoke('render_cancel',job['id']),{'ok':True})
            finally:
                if job['id'] in self.env['RENDER_PROCS']:self.env['RENDER_PROCS'][job['id']]['cancelled']=True
            future.result(timeout=5)
        self.assertEqual(job['status'],'error');self.assertIn('cancelled',job['error'])
        self.assertFalse(Path(storage.output_path(self.root/'renders',job['out'])).exists())
        self.assertEqual(budget.snapshot()['waiting'],0);self.assertEqual(budget.snapshot()['active'],0)

    def test_running_cancel_after_real_encode_before_publication_keeps_old_output(self):
        old=self.start();self.drain();path=Path(storage.output_path(self.root/'renders',old['out']));before=path.read_bytes()
        job=self.start();encoded=threading.Event();release=threading.Event();actual=render._run_ffmpeg
        def encode(*args,**kwargs):
            actual(*args,**kwargs);encoded.set()
            if not release.wait(10):raise RuntimeError('fixture release timeout')
        def work():
            try:self.env['_render_worker']()
            except StopWorker:pass
        with mock.patch.object(render,'_run_ffmpeg',side_effect=encode), concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future=pool.submit(work)
            try:
                self.assertTrue(encoded.wait(10));self.assertEqual(self.invoke('render_cancel',job['id']),{'ok':True})
            finally:release.set()
            future.result(timeout=10)
        self.assertEqual(job['status'],'error');self.assertEqual(job['error'],'cancelled')
        self.assertEqual(path.read_bytes(),before)
        target=Path(storage.output_path(self.root/'renders',job['out']))
        self.assertFalse(target.exists());self.assertFalse(list(target.parent.glob('*.part.*')))
        self.assertFalse(self.env['RENDER_PROCS']);self.assertEqual(self.env['RENDER_Q'].unfinished_tasks,0)

    def test_queued_cancel_and_encoder_failure_do_not_touch_previous_output_or_stop_queue(self):
        old=self.start(); self.drain(); path=Path(storage.output_path(self.root/'renders',old['out'])); before=path.read_bytes()
        cancelled=self.start(); self.invoke('render_cancel',cancelled['id'])
        failed=self.start(); next_job=self.start()
        actual=self.env['do_render']; calls=[]
        def render_next(*args,**kwargs):
            calls.append(args[2])
            if len(calls)==1: raise RuntimeError('injected encoder failure')
            return actual(*args,**kwargs)
        self.env['do_render']=render_next; self.drain()
        self.assertEqual(cancelled['status'],'error'); self.assertEqual(failed['status'],'error'); self.assertEqual(next_job['status'],'done')
        self.assertEqual(len(calls),2); self.assertEqual(path.read_bytes(),before)
        self.assertFalse(Path(storage.output_path(self.root/'renders',failed['out'])).exists())

    def test_output_resolution_accepts_old_flat_paths_and_rejects_escape(self):
        root=self.root/'renders';root.mkdir()
        self.assertEqual(storage.output_path(root,'/renders/old.mp4'),str(root/'old.mp4'))
        for url in ['/renders/../project.json','/renders//bad','/renders/C:bad','/renders/a\\b','/renders/%2e%2e/x','https://example.com/a']:
            with self.assertRaises(ValueError):storage.output_path(root,url)
        if os.name!='nt':
            (root/'outside').symlink_to(self.root,target_is_directory=True)
            with self.assertRaises(ValueError):storage.output_path(root,'/renders/outside/a')

    def test_review_resolves_exact_job_and_legacy_duplicate_name_fails_clearly(self):
        first=self.completed();second=self.completed()
        self.assertIn(first['out'],self.env['review_page']('job-'+first['id']))
        self.assertNotIn(second['out'],self.env['review_page']('job-'+first['id']))
        with self.assertRaises(HTTPError) as error:self.env['review_page']('export')
        self.assertEqual(error.exception.status_code,409)
        only=self.completed(name='unique');self.assertIn(only['out'],self.env['review_page']('unique'))

    def test_review_notes_belong_to_one_export_are_undoable_and_do_not_follow_project_switches(self):
        one=self.completed();two=self.completed();reference='job-'+one['id']
        note=preview.RenderedPreview.run_async(self,self.env['review_note_add'](reference,Request({'time':.2,'text':'First cut','author':'Reviewer'})))
        self.assertEqual(note['review_job'],one['id']);self.assertEqual(len(self.env['review_notes'](reference)),1)
        self.assertEqual(self.env['review_notes']('job-'+two['id']),[])
        self.env['set_active_project']('b'); before=self.raw('b')
        with self.assertRaises(HTTPError) as error:preview.RenderedPreview.run_async(self,self.env['review_note_add'](reference,Request({'text':'wrong project'})))
        self.assertEqual(error.exception.status_code,409);self.assertEqual(self.raw('b'),before)
        with self.assertRaises(HTTPError):self.env['review_notes'](reference)
        self.env['set_active_project']('a')
        preview.RenderedPreview.run_async(self,self.env['project_history_action']('undo',Request({})))
        self.assertEqual(self.env['review_notes'](reference),[])
        preview.RenderedPreview.run_async(self,self.env['project_history_action']('redo',Request({})))
        self.assertEqual(len(self.env['review_notes'](reference)),1)

    def test_range_review_note_preserves_the_exact_first_frame_time(self):
        self.doc['sequences'][0].update(fps=29.97,in_point=.034,out_point=.134)
        job=self.completed(preset={'vcodec':'ffv1','range':True})
        note=preview.RenderedPreview.run_async(self,self.env['review_note_add']('job-'+job['id'],Request({'time':0,'text':'First frame'})))
        self.assertEqual(note['time'],1001/30000)
        self.assertEqual(note['review_time'],0)

    def test_range_review_notes_keep_video_time_and_captured_sequence_offset(self):
        self.doc['sequences'][0].update(in_point=.1,out_point=.4)
        job=self.completed(preset={'vcodec':'ffv1','range':True})
        note=preview.RenderedPreview.run_async(self,self.env['review_note_add']('job-'+job['id'],Request({'time':.2,'text':'Here'})))
        self.assertAlmostEqual(job['review_start'],2/24);self.assertAlmostEqual(note['time'],2/24+.2);self.assertEqual(note['review_time'],.2)

    def test_invalid_note_time_and_missing_project_identity_never_modify_project(self):
        job=self.completed();reference='job-'+job['id'];before=self.raw()
        for position in [-1,'bad',float('nan'),float('inf')]:
            with self.assertRaises(HTTPError) as error:preview.RenderedPreview.run_async(self,self.env['review_note_add'](reference,Request({'time':position})))
            self.assertEqual(error.exception.status_code,422)
        job.pop('context')
        with self.assertRaises(HTTPError):self.env['review_notes'](reference)
        self.assertEqual(self.raw(),before)

    def test_note_commit_failure_preserves_project_and_audit_failure_reports_saved_note(self):
        job=self.completed();reference='job-'+job['id'];before=self.raw()
        commit=self.env['commit_edit'];self.env['commit_edit']=mock.Mock(side_effect=OSError('disk full'))
        with self.assertRaises(OSError):preview.RenderedPreview.run_async(self,self.env['review_note_add'](reference,Request({'text':'not saved'})))
        self.assertEqual(self.raw(),before)
        self.env['commit_edit']=commit;self.env['log_event']=mock.Mock(side_effect=OSError('audit denied'))
        note=preview.RenderedPreview.run_async(self,self.env['review_note_add'](reference,Request({'text':'saved'})))
        self.assertIn('Note saved',note['warning']);self.assertEqual(len(self.env['review_notes'](reference)),1)

    def test_real_completed_video_png_and_review_notes_return_after_history_restore(self):
        video=self.start(name='kept');png=self.start(name='kept',preset={'format':'png_sequence'});self.drain()
        reference='job-'+video['id']
        preview.RenderedPreview.run_async(self,self.env['review_note_add'](reference,Request({'time':.2,'text':'Remember this'})))
        files={job['id']:Path(storage.output_path(self.root/'renders',job['out'])).read_bytes() for job in (video,png)}
        self.env['JOBS'].clear();self.env['restore_render_history']()
        self.assertTrue(self.env['RENDER_Q'].empty());self.assertFalse(self.env['RENDER_WORKERS'])
        restored=self.env['jobs_list']();self.assertEqual({j['id'] for j in restored},{video['id'],png['id']})
        for job in restored:
            self.assertEqual(job['status'],'done');self.assertEqual(job['history']['status'],'restored')
            self.assertEqual(Path(storage.output_path(self.root/'renders',job['out'])).read_bytes(),files[job['id']])
        self.assertIn(video['out'],self.env['review_page'](reference))
        self.assertEqual(self.env['review_notes'](reference)[0]['name'],'Remember this')
        self.assertEqual(len(self.env['renders_manifest']()),2)
        # API reads must not expose mutable queue dictionaries to callers.
        self.env['render_status'](video['id'])['status']='wrong'
        self.assertEqual(self.env['JOBS'][video['id']]['status'],'done')

    def test_restore_refuses_live_queue_and_interrupted_work_is_never_requeued(self):
        job=self.start()
        with self.assertRaisesRegex(RuntimeError,'live render queue'):self.env['restore_render_history']()
        self.env['JOBS'].clear();self.env['RENDER_Q']=BoundedQueue()
        self.env['restore_render_history']()
        restored=self.env['JOBS'][job['id']]
        self.assertEqual(restored['status'],'error');self.assertTrue(restored['recovery']['interrupted'])
        self.assertTrue(self.env['RENDER_Q'].empty());self.assertFalse(self.env['RENDER_WORKERS'])
        self.assertEqual(self.invoke('render_cancel',job['id']),{'ok':False})

    def test_cancellation_is_saved_even_if_worker_never_dequeues_the_job(self):
        job=self.start();self.invoke('render_cancel',job['id'])
        self.env['JOBS'].clear();self.env['RENDER_Q']=BoundedQueue();self.env['restore_render_history']()
        self.assertEqual(self.env['JOBS'][job['id']]['error'],'cancelled')
        self.assertNotIn('recovery',self.env['JOBS'][job['id']])

    def test_initial_receipt_failure_refuses_submission_and_removes_empty_reservation(self):
        import project_transaction
        for failure in (mock.patch.object(job_history,'write_atomic',side_effect=PermissionError('disk write denied')),
                        mock.patch.object(project_transaction,'_sync_parent',side_effect=OSError('flush failed after rename'))):
            with failure:
                with self.assertRaises(HTTPError) as error:self.start()
            self.assertEqual(error.exception.status_code,503);self.assertFalse(self.env['JOBS'])
            self.assertTrue(self.env['RENDER_Q'].empty());self.assertEqual(list((self.root/'renders').iterdir()),[])

    def test_terminal_history_failure_preserves_real_output_and_retires_queue_with_warning(self):
        first=self.start(name='history-error');second=self.start(name='successor');actual=job_history.save
        def fail(root,job):
            if job['id']==first['id'] and job['status']=='done':raise OSError('terminal receipt denied')
            return actual(root,job)
        with mock.patch.object(job_history,'save',side_effect=fail):self.drain()
        self.assertEqual(first['status'],'done');self.assertEqual(first['history']['status'],'error')
        self.assertTrue(Path(storage.output_path(self.root/'renders',first['out'])).is_file())
        self.assertEqual(second['status'],'done');self.assertEqual(second['history']['status'],'saved')
        self.env['JOBS'].clear();self.env['restore_render_history']()
        self.assertTrue(self.env['JOBS'][first['id']]['recovery']['interrupted'])
        self.assertEqual(self.env['JOBS'][second['id']]['status'],'done')

    def test_status_poll_waits_for_terminal_receipt_attempt_and_includes_its_warning(self):
        job=self.start();actual=job_history.save
        writing=threading.Event();release=threading.Event();reading=[threading.Event(),threading.Event()]
        def delayed_failure(root,current):
            if current['id']==job['id'] and current['status']=='done':
                writing.set()
                if not release.wait(5):raise AssertionError('Receipt fixture was not released')
                raise OSError('terminal receipt denied')
            return actual(root,current)
        def read(index):
            reading[index].set()
            return self.env['render_status'](job['id']) if index==0 else self.env['jobs_list']()[0]
        with mock.patch.object(job_history,'save',side_effect=delayed_failure), concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            worker=pool.submit(self.drain)
            try:
                self.assertTrue(writing.wait(5))
                readers=[pool.submit(read,index) for index in range(2)]
                for event in reading:self.assertTrue(event.wait(2))
                for future in readers:
                    with self.assertRaises(concurrent.futures.TimeoutError):future.result(timeout=.05)
            finally:release.set()
            worker.result(timeout=5)
            for future in readers:
                result=future.result(timeout=5)
                self.assertEqual(result['status'],'done');self.assertEqual(result['history']['status'],'error')
                self.assertIn('terminal receipt denied',result['history']['message'])


if __name__=='__main__':unittest.main(verbosity=2)
