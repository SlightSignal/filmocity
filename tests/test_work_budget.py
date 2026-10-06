"""Real thread/process admission, controlled model/route adapters; no native UI.

The real encoder/probe checks use local disposable PCM and FFmpeg, not camera
footage. Speech is an explicitly controlled lazy model iterator, not real ASR.
"""
import ast
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import work_budget
from work_budget import WorkBudget, WorkBusy, status
import render
import proxy_media
from render_context import RenderContext
from background_tasks import TaskManager
from project_sync import workspace_id


def wait_for(predicate, seconds=5):
    deadline = time.monotonic()+seconds
    while time.monotonic() < deadline:
        if predicate(): return
        time.sleep(.005)
    raise AssertionError('Timed out waiting for the observed worker condition')


class WorkChecks(unittest.TestCase):
    def setUp(self):
        self.budget=WorkBudget();self.patch=patch.object(work_budget,'BUDGET',self.budget);self.patch.start()
        self.temp=tempfile.TemporaryDirectory(prefix='filmocity-work-budget-');self.root=Path(self.temp.name)
        self.threads=[];self.holders=[];self.events=[]

    def tearDown(self):
        for holder in self.holders: holder['cancelled']=True
        for event in self.events: event.set()
        for thread in self.threads: thread.join(5);self.assertFalse(thread.is_alive(),'Worker survived cleanup')
        self.patch.stop();self.temp.cleanup()
        self.assertEqual(self.budget.snapshot()['active'],0);self.assertEqual(self.budget.snapshot()['waiting'],0)

    def holder(self, **values):
        self.holders.append(values);return values

    def event(self):
        event=threading.Event();self.events.append(event);return event

    def start(self, function):
        result={};done=threading.Event()
        def worker():
            try: result['value']=function()
            except BaseException as error: result['error']=error
            finally: done.set()
        thread=threading.Thread(target=worker,daemon=True);self.threads.append(thread);thread.start()
        return result,done

    def finished(self, task, error=None):
        result,done=task;self.assertTrue(done.wait(5),result)
        if error: self.assertIsInstance(result.get('error'),error)
        elif 'error' in result: raise result['error']
        return result.get('value')

    def occupy(self, operation='encode'):
        holder=self.holder();entered=self.event();release=self.event()
        def run():
            with work_budget.work(holder,operation): entered.set();release.wait(5)
        task=self.start(run);self.assertTrue(entered.wait(5));return holder,release,task

    def test_bounds_and_invalid_policy_never_admit_work(self):
        for value in (0,9,True,'2'):
            with self.assertRaises(ValueError): WorkBudget(value)
        for timeout in (-1,float('nan'),float('inf'),True,'1'):
            with self.assertRaises(ValueError),work_budget.work({'resource_wait_timeout':timeout}): pass
        with self.assertRaises(ValueError),work_budget.work(operation='other'): pass
        self.assertEqual(self.budget.snapshot()['released_phases'],0)

    def test_two_slots_fifo_and_waiter_cannot_jump_an_earlier_request(self):
        _,a,ta=self.occupy();_,b,tb=self.occupy();order=[];hold=self.event()
        def waiting(label,holder):
            with work_budget.work(holder): order.append(label);hold.wait(5)
        first=self.holder();t1=self.start(lambda:waiting('first',first));wait_for(lambda:status(first))
        second=self.holder();t2=self.start(lambda:waiting('second',second));wait_for(lambda:status(second))
        self.assertEqual(self.budget.snapshot()['waiting'],2);a.set();wait_for(lambda:order)
        self.assertEqual(order,['first']);b.set();wait_for(lambda:len(order)==2);self.assertEqual(order,['first','second'])
        hold.set()
        for task in (ta,tb,t1,t2):self.finished(task)
        self.assertEqual(self.budget.snapshot()['peak_active'],2)

    def test_waiting_cancel_releases_queue_without_entering_body(self):
        _,a,ta=self.occupy();_,b,tb=self.occupy();holder=self.holder();entered=[]
        def waiter():
            with work_budget.work(holder):entered.append(True)
        task=self.start(waiter);wait_for(lambda:status(holder));holder['cancelled']=True
        self.finished(task,RuntimeError);self.assertFalse(entered);self.assertIsNone(status(holder));self.assertEqual(self.budget.snapshot()['active'],2)
        a.set();b.set();self.finished(ta);self.finished(tb)

    def test_queue_deadline_and_queue_capacity_leave_existing_owners_intact(self):
        self.budget.pending_limit=1;_,a,ta=self.occupy();_,b,tb=self.occupy();holder=self.holder(resource_wait_timeout=.1)
        def wait():
            with work_budget.work(holder): self.fail('Must time out')
        task=self.start(wait);wait_for(lambda:status(holder))
        with self.assertRaisesRegex(WorkBusy,'queue is full'),work_budget.work():pass
        self.finished(task,WorkBusy);self.assertIsNone(status(holder));self.assertEqual(self.budget.snapshot()['active'],2)
        a.set();b.set();self.finished(ta);self.finished(tb)

    def test_explicit_context_check_and_precancelled_inputs_never_enter(self):
        with self.assertRaisesRegex(RuntimeError,'cancelled'),work_budget.work({'cancelled':True}):self.fail()
        def reject():raise LookupError('owner changed')
        with self.assertRaisesRegex(LookupError,'owner changed'),work_budget.work(check=reject):self.fail()
        self.assertEqual(self.budget.snapshot()['released_phases'],0)

    def test_failure_and_baseexception_release_slot_without_masking_error(self):
        for error in (ValueError('failed'),KeyboardInterrupt()):
            holder=self.holder()
            with self.assertRaises(type(error)),work_budget.work(holder):raise error
            self.assertIsNone(status(holder));self.assertEqual(self.budget.snapshot()['active'],0)
        with work_budget.work(): self.assertEqual(self.budget.snapshot()['active'],1)

    def test_nested_admission_is_rejected_instead_of_deadlocking(self):
        with work_budget.work():
            with self.assertRaisesRegex(RuntimeError,'leaf operations'),work_budget.work():pass
            self.assertEqual(self.budget.snapshot()['active'],1)

    def test_speech_limit_skips_ineligible_waiter_without_blocking_encoder(self):
        _,speech,ts=self.occupy('speech');holder=self.holder();entered=self.event();release=self.event()
        def speech_waiter():
            with work_budget.work(holder,'speech'):entered.set();release.wait(5)
        waiting=self.start(speech_waiter);wait_for(lambda:status(holder));self.assertFalse(entered.is_set())
        with work_budget.work(operation='encode'):
            self.assertEqual(self.budget.snapshot()['active_operations'],['speech','encode'])
            self.assertFalse(entered.is_set())
        speech.set();self.finished(ts);self.assertTrue(entered.wait(5));release.set();self.finished(waiting)

    def test_public_status_has_no_process_handles_and_cannot_mutate_admission(self):
        holder=self.holder(proc=object())
        with work_budget.work(holder):
            snap=status(holder);json.dumps(snap);snap['state']='changed'
            self.assertEqual(status(holder)['state'],'running');self.assertEqual(set(snap),{'state','operation','wait_seconds'})
        self.assertIsNone(status(holder));self.assertGreaterEqual(holder['resource_wait_seconds'],0)

    def test_task_catalog_exposes_wait_and_cancels_before_handler_process_start(self):
        _,a,ta=self.occupy();_,b,tb=self.occupy();entered=[]
        def handler(payload,context):
            context.progress('Video proxy',.5)
            with work_budget.work(context.holder):entered.append(True)
            return {}
        manager=TaskManager(self.root,{'media':handler},workers=1)
        try:
            job=manager.submit('media','Clip',{'workspace':workspace_id(self.root),'project':'p'},{})
            wait_for(lambda:manager.catalog()['tasks'][0].get('resource'))
            public=manager.catalog()['tasks'][0];self.assertEqual(public['resource']['state'],'waiting');self.assertEqual(public['stage'],'Video proxy')
            self.assertNotIn('resource',manager.get(job['id'])['record']);manager.cancel(job['id'])
            wait_for(lambda:manager.get(job['id'])['record']['status']=='cancelled');self.assertFalse(entered)
            self.assertNotIn('resource',manager.catalog()['tasks'][0])
        finally:manager.shutdown(5);a.set();b.set();self.finished(ta);self.finished(tb)

    def test_shutdown_cancels_a_task_waiting_for_another_queue(self):
        _,a,ta=self.occupy();_,b,tb=self.occupy()
        def handler(payload,context):
            with work_budget.work(context.holder):self.fail('Unexpected admission')
        manager=TaskManager(self.root,{'media':handler},workers=1)
        try:
            manager.submit('media','Clip',{'workspace':workspace_id(self.root),'project':'p'},{})
            wait_for(lambda:manager.catalog()['tasks'][0].get('resource'));self.assertTrue(manager.shutdown(2))
        finally:manager.shutdown(2);a.set();b.set();self.finished(ta);self.finished(tb)

    def tone(self):
        path=self.root/'source.wav'
        with wave.open(str(path),'wb') as stream:stream.setparams((1,2,48000,0,'NONE',''));stream.writeframes(b'\0\0'*4800)
        return path

    def test_encoder_cancel_while_waiting_never_starts_a_child(self):
        _,a,ta=self.occupy();_,b,tb=self.occupy();holder=self.holder()
        with patch.object(render.subprocess,'Popen') as popen:
            task=self.start(lambda:render._run_ffmpeg(['ffmpeg','-f','null','-'],proc_holder=holder))
            wait_for(lambda:status(holder));holder['cancelled']=True;self.finished(task,RuntimeError);popen.assert_not_called()
        a.set();b.set();self.finished(ta);self.finished(tb)

    def test_encoder_launch_failure_and_progress_failure_reap_before_slot_release(self):
        holder=self.holder()
        with self.assertRaises(OSError):render._run_ffmpeg([str(self.root/'not-an-encoder')],proc_holder=holder)
        self.assertEqual(self.budget.snapshot()['active'],0)
        def broken(fraction):raise RuntimeError('progress handler failed')
        with self.assertRaisesRegex(RuntimeError,'progress handler failed'):
            render._run_ffmpeg(['ffmpeg','-v','error','-f','lavfi','-i','sine=duration=0.1','-f','null','-'],progress=broken,proc_holder=holder)
        self.assertNotIn('proc',holder);self.assertIsNone(status(holder));self.assertEqual(self.budget.snapshot()['active'],0)

    def test_real_encoders_and_real_probe_share_two_slots_and_cancel_reaps(self):
        path=self.tone();holders=[self.holder(),self.holder()]
        cmd=['ffmpeg','-v','error','-re','-f','lavfi','-i','sine=duration=20','-f','null','-']
        tasks=[self.start(lambda h=h:render._run_ffmpeg(cmd,proc_holder=h)) for h in holders]
        wait_for(lambda:all(h.get('proc') for h in holders));children=[h['proc'] for h in holders]
        probe_holder=self.holder()
        def probe():
            with RenderContext(proc_holder=probe_holder,scratch_parent=str(self.root)) as ctx:return proxy_media.inspect_file(path,ctx)
        inspection=self.start(probe);wait_for(lambda:status(probe_holder))
        self.assertEqual(status(probe_holder)['state'],'waiting');self.assertNotIn('proc',probe_holder)
        holders[0]['cancelled']=True;self.finished(tasks[0],RuntimeError);result=self.finished(inspection)
        self.assertEqual(result['streams'][0]['sample_rate'],'48000');self.assertIsNotNone(children[0].poll())
        holders[1]['cancelled']=True;self.finished(tasks[1],RuntimeError);self.assertIsNotNone(children[1].poll())
        self.assertTrue(all('proc' not in h for h in holders+[probe_holder]));self.assertFalse(list(self.root.glob('filmocity-render-*')))
        self.assertEqual(self.budget.snapshot()['peak_active'],2)

    def test_waiting_probe_deadline_does_not_launch_or_leave_scratch(self):
        _,a,ta=self.occupy();_,b,tb=self.occupy();holder=self.holder(resource_wait_timeout=.05)
        with patch.object(proxy_media.subprocess,'Popen') as popen:
            with self.assertRaises(WorkBusy),RenderContext(proc_holder=holder,scratch_parent=str(self.root)) as ctx:
                proxy_media.inspect_file('unused',ctx)
            popen.assert_not_called()
        self.assertFalse(list(self.root.iterdir()));a.set();b.set();self.finished(ta);self.finished(tb)

    def speech_function(self, model):
        tree=ast.parse((ROOT/'backend/server.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_transcribe_sequence')
        module=types.ModuleType('faster_whisper');module.WhisperModel=model
        env={'RenderContext':lambda **kw:RenderContext(scratch_parent=str(self.root),**kw),'do_render':render.render,
             'seq_total':lambda s:s['duration'],'uuid':uuid,'JSONResponse':lambda *a,**kw:None}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'backend/server.py','exec'),env)
        return env['_transcribe_sequence'],patch.dict(sys.modules,{'faster_whisper':module})

    def project(self):
        return {'media':{},'sequences':[{'id':'s','name':'Source','fps':24,'width':64,'height':48,'duration':.25,'tracks':[],'captions':[]}]}

    def test_speech_lease_covers_lazy_iteration_and_closes_before_releasing(self):
        observations=[];holder=self.holder()
        class Model:
            def __init__(model,*args,**kwargs):observations.append(('load',self.budget.snapshot()['active_operations']))
            def transcribe(model,path,**kw):
                self.assertTrue(Path(path).is_file())
                def segments():
                    try:
                        observations.append(('next',self.budget.snapshot()['active_operations']))
                        yield types.SimpleNamespace(start=0,end=.2,text=' hello ',words=[])
                    finally:observations.append(('close',self.budget.snapshot()['active_operations']))
                return segments(),None
        function,models=self.speech_function(Model)
        with models:words,captions=function(self.project(),'s','fake',proc_holder=holder,word_timestamps=True)
        self.assertEqual(captions[0]['text'],'hello');self.assertEqual([v[0] for v in observations],['load','next','close'])
        self.assertTrue(all(ops==['speech'] for _,ops in observations));self.assertEqual(self.budget.snapshot()['active'],0)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_cancelled_lazy_model_closes_under_lease_and_keeps_wav_until_exit(self):
        entered=self.event();release=self.event();holder=self.holder();observations=[]
        class Model:
            def __init__(model,*a,**kw):pass
            def transcribe(model,path,**kw):
                def segments():
                    try:
                        entered.set();release.wait(5)
                        yield types.SimpleNamespace(start=0,end=.2,text='hello',words=[])
                    finally:observations.append((Path(path).exists(),self.budget.snapshot()['active_operations']))
                return segments(),None
        function,models=self.speech_function(Model)
        with models:
            task=self.start(lambda:function(self.project(),'s','fake',proc_holder=holder,word_timestamps=True))
            self.assertTrue(entered.wait(5));holder['cancelled']=True
            self.assertEqual(self.budget.snapshot()['active_operations'],['speech']);release.set();self.finished(task,RuntimeError)
        self.assertEqual(observations,[(True,['speech'])]);self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_nested_real_export_uses_leaf_slots_without_deadlock_at_capacity_one(self):
        self.budget.capacity=1;project=self.project();child=project['sequences'][0]
        parent=copy.deepcopy(child);parent.update(id='parent',tracks=[{'id':'V','kind':'video','index':1,'clips':[{'id':'nested','sequence_id':'s','start':0,'in_':0,'out':.25}]}]);project['sequences'].append(parent)
        target=self.root/'nested.mkv'
        with RenderContext(scratch_parent=str(self.root)) as context:
            render.render(project,'parent',str(target),{'vcodec':'ffv1','acodec':'pcm_f32le'},context=context)
        self.assertGreater(target.stat().st_size,0);self.assertGreaterEqual(self.budget.snapshot()['released_phases'],2)
        self.assertEqual(self.budget.snapshot()['peak_active'],1)
        subprocess.run(['ffmpeg','-v','error','-i',str(target),'-f','null','-'],check=True,capture_output=True)

    def test_second_speech_call_waits_without_loading_another_model_and_can_cancel(self):
        _,release,first=self.occupy('speech');holder=self.holder()
        def forbidden(*args,**kwargs):self.fail('A second model was loaded before admission')
        function,models=self.speech_function(forbidden)
        with models:
            task=self.start(lambda:function(self.project(),'s','fake',proc_holder=holder,word_timestamps=True))
            wait_for(lambda:status(holder) and status(holder)['operation']=='speech')
            self.assertEqual(status(holder)['state'],'waiting');holder['cancelled']=True;self.finished(task,RuntimeError)
        release.set();self.finished(first);self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_real_export_verification_shares_admission_for_probe_and_loudness_decode(self):
        import hashlib
        path=self.tone();original=path.read_bytes();holder=self.holder();observed=[]
        tree=ast.parse((ROOT/'backend/server.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_qa')
        run=subprocess.run
        def inspected(*args,**kwargs):
            observed.append(self.budget.snapshot()['active_operations']);return run(*args,**kwargs)
        env={'subprocess':types.SimpleNamespace(run=inspected),'json':json,'os':os,'hashlib':hashlib,'PLATFORM_RULES':{}}
        exec(compile(ast.Module(body=[node],type_ignores=[]),'backend/server.py','exec'),env)
        _,release,first=self.occupy('speech')
        try:result=env['render_qa'](str(path),proc_holder=holder)
        finally:release.set();self.finished(first)
        self.assertEqual(observed,[['speech','probe'],['speech','probe']]);self.assertIn(result['status'],('checked','warnings'))
        self.assertEqual(result['sha256'],hashlib.sha256(original).hexdigest());self.assertEqual(path.read_bytes(),original)

    def test_verification_admission_failure_reports_failed_qa_and_preserves_published_bytes(self):
        path=self.tone();original=path.read_bytes()
        tree=ast.parse((ROOT/'backend/server.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='render_qa')
        env={};exec(compile(ast.Module(body=[node],type_ignores=[]),'backend/server.py','exec'),env)
        with patch('work_budget.work',side_effect=WorkBusy('Processing queue is full')):
            result=env['render_qa'](str(path))
        self.assertEqual(result['status'],'error');self.assertIn('queue is full',result['error']);self.assertTrue(result['flags'])
        self.assertEqual(path.read_bytes(),original)


if __name__=='__main__':unittest.main()
