"""Real worker concurrency, cancellation, persistence and owned media processes."""
import copy
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from background_tasks import TaskManager, TaskStore, TaskError, TaskContext, ACTIVE
from project_sync import workspace_id
from task_inputs import capture, source_stamp
import media_preparation
from render import _run_ffmpeg
from PIL import Image


def wait_for(manager, identity, states=('ready','done','error','cancelled'), timeout=5):
    deadline = time.monotonic() + timeout
    with manager.condition:
        while manager.values[identity]['record']['status'] not in states:
            left = deadline-time.monotonic()
            if left<=0: raise AssertionError(manager.get(identity)['record'])
            manager.condition.wait(left)
    return manager.get(identity)


class Fixture(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Filmocity tasks É ');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name);self.owner={'workspace':workspace_id(self.root),'project':'p','revision':'r'}
        self.managers=[];self.events=[]
        self.addCleanup(self.stop)
    def stop(self):
        for event in self.events:event.set()
        for manager in self.managers:manager.shutdown(5)
    def event(self):
        event=threading.Event();self.events.append(event);return event
    def manager(self,handler=lambda p,c: {'ok':True},**kwargs):
        m=TaskManager(self.root,{'media':handler,'transcribe':handler},**kwargs);self.managers.append(m);return m
    def submit(self,m,kind='media',**kwargs):return m.submit(kind,'<literal>',self.owner,kwargs)


class Tasks(Fixture):
    def test_worker_count_is_bounded_for_many_imports(self):
        release=self.event();entered=self.event();lock=threading.Lock();live=peak=0
        def handler(p,c):
            nonlocal live,peak
            with lock:live+=1;peak=max(peak,live);entered.set()
            release.wait(3)
            with lock:live-=1
            return {}
        m=self.manager(handler);jobs=[self.submit(m,item=i)['id'] for i in range(30)]
        self.assertTrue(entered.wait(2));self.assertEqual(len(m.threads),2);self.assertLessEqual(peak,2)
        self.assertGreaterEqual(sum(v['status']=='queued' for v in m.catalog()['tasks']),28)
        release.set()
        for identity in jobs:self.assertEqual(wait_for(m,identity)['record']['status'],'done')
        self.assertLessEqual(peak,2)

    def test_only_one_speech_model_runs_while_media_can_use_the_other_worker(self):
        speech=self.event();release=self.event();media=self.event()
        def handler(p,c):
            if p['type']=='speech':speech.set();release.wait(3)
            else:media.set()
            return {}
        m=self.manager(handler);a=self.submit(m,'transcribe',type='speech',n=1);b=self.submit(m,'transcribe',type='speech',n=2)
        self.assertTrue(speech.wait(2));self.submit(m,type='media');self.assertTrue(media.wait(2))
        self.assertEqual(m.get(b['id'])['record']['status'],'queued');release.set();wait_for(m,a['id']);wait_for(m,b['id'])

    def test_idempotent_identity_and_duplicate_inputs_do_not_launch_twice(self):
        m=self.manager(start=False);a=m.submit('media','One',self.owner,{'x':1},identity='a'*32)
        self.assertEqual(m.submit('media','One',self.owner,{'x':1},identity='a'*32)['id'],a['id'])
        self.assertEqual(m.submit('media','One',self.owner,{'x':1})['id'],a['id'])
        with self.assertRaises(TaskError):m.submit('media','Other',self.owner,{'x':2},identity='a'*32)
        self.assertEqual(len(m.values),1)

    def test_capacity_and_admission_write_failure_leave_no_phantom_job(self):
        m=self.manager(start=False,capacity=1);self.submit(m,n=1)
        with self.assertRaisesRegex(TaskError,'full'):self.submit(m,n=2)
        m.cancel(next(iter(m.values)))
        with patch.object(m.store,'save',side_effect=OSError('disk full')),self.assertRaises(OSError):self.submit(m,n=3)
        self.assertEqual(len(m.values),1)

    def test_queued_cancel_never_executes_and_running_cancel_waits_for_worker(self):
        entered=self.event();release=self.event();calls=[]
        def handler(p,c):calls.append(p);entered.set();release.wait(3);c.check();return {}
        m=self.manager(handler,workers=1);a=self.submit(m,n=1);self.assertTrue(entered.wait(2));b=self.submit(m,n=2)
        self.assertEqual(m.cancel(b['id'])['status'],'cancelled');self.assertEqual(m.cancel(a['id'])['status'],'cancelling')
        self.assertEqual(m.get(a['id'])['record']['status'],'cancelling');release.set()
        self.assertEqual(wait_for(m,a['id'])['record']['status'],'cancelled');self.assertEqual(len(calls),1)

    def test_failure_does_not_poison_following_jobs(self):
        def handler(p,c):
            if p['fail']:raise RuntimeError('encoder failed')
            c.progress('Stage',.5);return {'ok':True}
        m=self.manager(handler,workers=1);a=self.submit(m,fail=True);b=self.submit(m,fail=False)
        self.assertEqual(wait_for(m,a['id'])['record']['status'],'error');self.assertEqual(wait_for(m,b['id'])['record']['status'],'done')

    def test_publication_refuses_late_cancellation_then_completes(self):
        entered=self.event();release=self.event()
        def handler(p,c):
            def commit():entered.set();release.wait(3)
            c.publish(commit);return {}
        m=self.manager(handler);a=self.submit(m);self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(TaskError,'being saved'):m.cancel(a['id'])
        release.set();self.assertEqual(wait_for(m,a['id'])['record']['status'],'done')

    def test_ready_result_survives_restart_and_unfinished_work_does_not_resume(self):
        m=self.manager();a=self.submit(m,'transcribe',x=1);wait_for(m,a['id']);m.shutdown()
        b=self.manager(start=False);self.assertEqual(b.get(a['id'])['result'],{'ok':True})
        pending=self.submit(b,x=2);b.shutdown()
        c=self.manager(start=False);self.assertEqual(c.get(pending['id'])['record']['status'],'interrupted')
        self.assertEqual(c.get(a['id'])['record']['status'],'ready')

    def test_primary_corruption_recovers_previous_without_overwriting_bad_bytes(self):
        m=self.manager(start=False);a=self.submit(m,x=1);m.cancel(a['id']);path=m.store.path(a['id']);path.write_bytes(b'corrupt')
        b=self.manager(start=False);self.assertEqual(b.get(a['id'])['record']['status'],'interrupted')
        self.assertIn('Recovered',b.get(a['id'])['record']['warning']);self.assertEqual(path.read_bytes(),b'corrupt')
        path.with_suffix('.previous.json').write_bytes(b'also corrupt')
        c=self.manager(start=False);self.assertEqual(len(c.unavailable),1);self.assertNotIn(a['id'],c.values)

    def test_result_apply_is_exclusive_and_restart_preserves_uncertain_result(self):
        m=self.manager();a=self.submit(m,'transcribe');wait_for(m,a['id']);m.begin_apply(a['id'])
        with self.assertRaises(TaskError):m.begin_apply(a['id'])
        with self.assertRaises(TaskError):m.cancel(a['id'])
        m.shutdown();b=self.manager(start=False);self.assertEqual(b.get(a['id'])['record']['status'],'ready')
        b.begin_apply(a['id']);b.finish_apply(a['id'],success=True);self.assertEqual(b.get(a['id'])['record']['status'],'applied')

    def test_terminal_receipt_failure_keeps_result_and_surfaces_warning(self):
        entered=self.event();release=self.event()
        def handler(p,c):entered.set();release.wait(3);return {'words':['retained']}
        m=self.manager(handler);a=self.submit(m,'transcribe');self.assertTrue(entered.wait(2))
        with patch.object(m.store,'save',side_effect=OSError('sharing denied')):
            release.set();value=wait_for(m,a['id']);self.assertIn('history',value['record']['warning']);self.assertIsNotNone(value['result'])

    def test_shutdown_signals_active_and_interrupts_queue(self):
        entered=self.event()
        def handler(p,c):
            entered.set()
            while not c.holder.get('cancelled'):time.sleep(.005)
            c.check()
        m=self.manager(handler,workers=1);a=self.submit(m,n=1);self.assertTrue(entered.wait(2));b=self.submit(m,n=2)
        self.assertTrue(m.shutdown());self.assertEqual(m.get(a['id'])['record']['status'],'cancelled');self.assertEqual(m.get(b['id'])['record']['status'],'interrupted')
        with self.assertRaises(TaskError):self.submit(m,n=3)

    def test_cancel_stops_and_reaps_a_real_ffmpeg_process(self):
        entered=self.event();holder={}
        def handler(p,c):
            holder.update(context=c);entered.set()
            _run_ffmpeg(['ffmpeg','-v','error','-re','-f','lavfi','-i','sine=frequency=1000:duration=20','-f','null','-'],proc_holder=c.holder,total=20)
        m=self.manager(handler,workers=1);a=self.submit(m);self.assertTrue(entered.wait(2))
        deadline=time.monotonic()+2
        while 'proc' not in holder['context'].holder and time.monotonic()<deadline:time.sleep(.005)
        process=holder['context'].holder.get('proc');self.assertIsNotNone(process)
        m.cancel(a['id']);self.assertEqual(wait_for(m,a['id'])['record']['status'],'cancelled');self.assertIsNotNone(process.poll())

    def test_invalid_identity_and_workspace_are_rejected(self):
        m=self.manager(start=False)
        for identity in ('', '../escape','x'*32,'A'*32):
            with self.assertRaises(TaskError):m.submit('media','x',self.owner,{},identity=identity)
        with self.assertRaises(TaskError):m.submit('media','x',{'workspace':'other','project':'p'},{})


class Lifecycle(Fixture):
    def test_partial_worker_startup_stops_already_started_workers(self):
        original=threading.Thread.start;started=[]
        def start(thread):
            if started:raise RuntimeError('thread creation failed')
            original(thread);started.append(thread)
        with patch.object(threading.Thread,'start',start),self.assertRaises(RuntimeError):self.manager()
        self.assertEqual(len(started),1);self.assertFalse(started[0].is_alive())

    def test_abrupt_process_exit_restores_checkpoints_without_restarting(self):
        script="""
import os, sys, time
from background_tasks import TaskManager
from project_sync import workspace_id
root,state=sys.argv[1:]
m=TaskManager(root,{'transcribe':lambda p,c:{'words':['retained']}},start=state!='queued')
t=m.submit('transcribe','Speech',{'workspace':workspace_id(root),'project':'p'}, {})
if state!='queued':
    with m.condition:
        while m.values[t['id']]['record']['status']!='ready':m.condition.wait(5)
if state=='applying':m.begin_apply(t['id'])
print(t['id'],flush=True)
os._exit(73)
"""
        for state in ('queued','ready','applying'):
            with self.subTest(state=state):
                folder=self.root/state;folder.mkdir()
                child=subprocess.run([sys.executable,'-c',script,str(folder),state],capture_output=True,text=True,timeout=10,
                    env=dict(os.environ,PYTHONPATH=str(ROOT/'backend'),PYTHONUTF8='1'))
                self.assertEqual(child.returncode,73,child.stderr);manager=TaskManager(folder,{'transcribe':lambda p,c:self.fail('Automatically restarted')},start=False)
                self.managers.append(manager);value=manager.get(child.stdout.strip())
                self.assertEqual(value['record']['status'],'interrupted' if state=='queued' else 'ready')
                if state!='queued':self.assertEqual(value['result'],{'words':['retained']})


class Media(Fixture):
    def media_case(self,kind):
        if kind=='image':
            path=self.root/'Émile still.png';Image.new('RGB',(64,32),'red').save(path)
            info={'has_video':True,'has_audio':False,'is_image':True,'duration':5}
        else:
            path=self.root/'beeps.wav'
            with wave.open(str(path),'wb') as w:w.setparams((2,2,48000,0,'NONE',''));w.writeframes(b'\0\0\0\0'*4800)
            info={'has_video':False,'has_audio':True,'duration':.1}
        return {'media_id':'m','path':str(path),'info':info,'token':'t','stamp':source_stamp({**info,'path':str(path)})}

    def test_real_still_derivatives_are_checked_and_source_unchanged(self):
        payload=self.media_case('image');before=Path(payload['path']).read_bytes();updates=[]
        m=self.manager(lambda p,c:media_preparation.prepare(self.root,p,c,lambda p:True,lambda p,u:updates.append(u) or True))
        task=m.submit('media','Still',self.owner,payload);self.assertEqual(wait_for(m,task['id'])['record']['status'],'done')
        thumbnail=self.root/updates[0]['thumb'].lstrip('/')
        with Image.open(thumbnail) as image:self.assertEqual(image.size,(320,160))
        self.assertEqual(Path(payload['path']).read_bytes(),before);self.assertFalse(list((self.root/'thumbs').glob('filmocity-render-*')))

    def test_real_audio_waveform_and_no_unneeded_audio_proxy(self):
        payload=self.media_case('audio');updates=[]
        m=self.manager(lambda p,c:media_preparation.prepare(self.root,p,c,lambda p:True,lambda p,u:updates.append(u) or True))
        task=m.submit('media','Audio',self.owner,payload);self.assertEqual(wait_for(m,task['id'])['record']['status'],'done')
        self.assertTrue((self.root/updates[0]['wave'].lstrip('/')).is_file());self.assertEqual(list((self.root/'proxies').iterdir()),[])

    def test_failed_encoder_never_advertises_a_thumbnail(self):
        payload=self.media_case('image');updates=[]
        m=self.manager(lambda p,c:media_preparation.prepare(self.root,p,c,lambda p:True,lambda p,u:updates.append(u) or True,ffmpeg='missing-filmocity-encoder'))
        task=m.submit('media','Still',self.owner,payload);self.assertEqual(wait_for(m,task['id'])['record']['status'],'error')
        self.assertFalse(any('thumb' in u for u in updates));self.assertEqual(updates[-1]['status'],'error')

    def test_changed_source_or_removed_media_prevents_work(self):
        payload=self.media_case('image');Path(payload['path']).write_bytes(b'replaced')
        m=self.manager(lambda p,c:media_preparation.prepare(self.root,p,c,lambda p:True,lambda p,u:True))
        task=m.submit('media','Still',self.owner,payload);self.assertEqual(wait_for(m,task['id'])['record']['status'],'error')
        self.assertEqual(list((self.root/'thumbs').iterdir()),[])


class PackagePublication(Fixture):
    def test_final_publication_survives_shutdown_and_refuses_cancel(self):
        entered=self.event();release=self.event()
        def worker(payload,context):
            def publish(): entered.set();release.wait(3);return {'folder':'published'}
            return context.commit_result(publish)
        m=TaskManager(self.root,{'package':worker});self.managers.append(m)
        task=m.submit('package','Package',self.owner,{})['id'];self.assertTrue(entered.wait(2))
        with self.assertRaisesRegex(TaskError,'saved'):m.cancel(task)
        self.assertFalse(m.shutdown(.01));release.set();self.assertTrue(m.shutdown(3))
        self.assertEqual(m.get(task)['record']['status'],'done');self.assertEqual(m.get(task)['result']['folder'],'published')
        restored=TaskStore(self.root).restore()[0][task];self.assertEqual(restored['record']['status'],'done')
        self.assertEqual(m.catalog()['tasks'][0]['result'],restored['result'])

    def test_cancel_before_final_publication_leaves_no_result(self):
        entered=self.event();release=self.event();published=[]
        def worker(payload,context):
            entered.set();release.wait(3);return context.commit_result(lambda:published.append(True))
        m=TaskManager(self.root,{'package_import':worker});self.managers.append(m)
        task=m.submit('package_import','Import',self.owner,{})['id'];self.assertTrue(entered.wait(2));m.cancel(task);release.set()
        self.assertEqual(wait_for(m,task)['record']['status'],'cancelled');self.assertEqual(published,[])

    def test_publication_error_is_visible_and_restart_does_not_resubmit(self):
        def worker(payload,context):
            def failed():raise OSError('resources retained; project write denied')
            return context.commit_result(failed)
        m=TaskManager(self.root,{'package_import':worker});self.managers.append(m)
        task=m.submit('package_import','Import',self.owner,{})['id'];self.assertEqual(wait_for(m,task)['record']['status'],'error');m.shutdown(2)
        restored=TaskStore(self.root).restore()[0][task];self.assertIn('retained',restored['record']['message']);self.assertIsNone(restored['result'])

    def test_failed_receipt_after_publication_does_not_hide_completed_result(self):
        m=TaskManager(self.root,{'package':lambda p,c:c.commit_result(lambda:{'folder':'recoverable'})},start=False);self.managers.append(m)
        task=m.submit('package','Package',self.owner,{})['id']
        original=m.store.save
        def fail(value):
            if value['record']['status']=='done':raise OSError('receipt disk full')
            original(value)
        with patch.object(m.store,'save',side_effect=fail):
            t=threading.Thread(target=m._worker);m.threads.append(t);t.start();wait_for(m,task);m.shutdown(2)
        record=m.catalog()['tasks'][0];self.assertEqual(record['status'],'done');self.assertEqual(record['result']['folder'],'recoverable');self.assertIn('history',record['warning'])


if __name__=='__main__':unittest.main()
