"""Canonical isolated-audio plans and actual managed project/history routes."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import subprocess
import array
import wave
import math
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import audio_workflow as audio
import audio_measurement as engine
from background_tasks import TaskManager,TaskContext,TaskError
from test_audio_sync import recording,CONTEXT
from test_media_collection import run_async_check
import test_project_sync as store


class StoreAudio(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp();project=self.env['load_project']();project.update(recording(self.root));self.env['save_project'](project)
        self.manager=TaskManager(self.root,{'audio_analysis':lambda p,t:self.env['_task_audio_workflow'](p,t)},start=False);self.addCleanup(self.manager.shutdown)
        self.env.update(asyncio=asyncio,TASKS=self.manager,TaskError=TaskError)
        names={'audio_peak','audio_measure','audio_beats','audio_gain','_queue_audio_workflow','_audio_edit_policy','_task_audio_workflow',
               '_review_audio_workflow','background_audio_review','_apply_audio_workflow','_owned_task','background_task_apply','background_task_retry','_workflow_capture','_workflow_commit'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)

    def route(self,name,body,identity=None):
        return run_async_check(self.env[name](*(([identity] if identity else [])+[store.Request(body)])))

    def request(self,**kw):return {'_context':self.current(),'sequence':'s','clip_ids':['ca'],'request_id':'a'*32,**kw}
    def ready(self,mode='peak',**kw):
        route={'peak':'audio_peak','loudness':'audio_measure','beats':'audio_beats'}[mode]
        identity=self.route(route,self.request(**kw))['task']['id'];value=self.manager.values[identity]
        value['result']=self.env['_task_audio_workflow'](value['payload'],TaskContext(self.manager,identity));value['record']['status']='ready';self.manager.store.save(value)
        return identity
    def review(self,identity):return self.route('background_audio_review',{'_context':self.current()},identity)
    def apply(self,identity,review,**kw):return self.route('background_task_apply',{'_context':review['context'],'fingerprint':review['plan']['fingerprint'],**kw},identity)
    def project(self):return self.env['load_project']()
    def clip(self,project=None):return (project or self.project())['sequences'][0]['tracks'][0]['clips'][0]
    def edit_clip(self,**updates):
        p=self.project();self.clip(p).update(updates);self.env['save_project'](p)
    def manual(self,**kw):return self.route('audio_gain',self.request(mode='adjust',value=1.125,**kw))

    def test_manual_complete_clip_curve_history_and_one_undo_redo(self):
        self.edit_clip(audio={'gain_db':-6,'fade_in':1},keyframes={'audio.gain_db':[{'t':-2,'v':-8,'e':'bezier','b':[.1,.2,.8,.9]},{'t':3,'v':-4},{'t':12,'v':-2}]},
                       source_edit_window={'version':1,'ramp':{'points':[{'t':0,'v':1}],'offset':1},'duck':{'points':[{'t':0,'v':0}],'offset':1}})
        before=self.project();result=self.manual();self.assertTrue(result['ok']);after=self.project();clip=self.clip(after)
        self.assertEqual(clip['audio']['gain_db'],-4.875);self.assertEqual([p['v'] for p in clip['keyframes']['audio.gain_db']],[-6.875,-2.875,-.875])
        self.assertEqual(clip['source_edit_window'],self.clip(before)['source_edit_window']);self.assertEqual(clip['markers'],self.clip(before)['markers'])
        self.assertEqual(clip['keyframes']['audio.gain_db'][0]['b'],[.1,.2,.8,.9]);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_manual_locks_selection_bounds_malformed_and_unknown_history_are_atomic(self):
        original=self.project()
        for case in ('locked','curve_limit','nan','duplicate','history'):
            p=copy.deepcopy(original);c=self.clip(p);body=self.request(mode='adjust',value=1,clip_ids=['ca','cb'])
            if case=='locked':p['sequences'][0]['tracks'][1]['locked']=True
            if case=='curve_limit':p['sequences'][0]['tracks'][1]['clips'][0]['keyframes']={'audio.gain_db':[{'t':0,'v':24}]}
            if case=='nan':body['value']=float('nan')
            if case=='duplicate':body['clip_ids']=['ca','ca']
            if case=='history':c['source_edit_window']={'version':1,'gain':{'points':[{'t':-1,'v':1}],'offset':2}}
            self.env['save_project'](p);body['_context']=self.current();before=self.raw()
            with self.subTest(case=case),self.assertRaises(store.HTTPError):self.route('audio_gain',body)
            self.assertEqual(self.raw(),before)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),0)

    def test_manual_noop_and_stale_context_do_not_create_history(self):
        before=self.raw();r=self.route('audio_gain',self.request(mode='adjust',value=0));self.assertFalse(r['changed']);self.assertEqual(self.raw(),before)
        body=self.request(mode='set',value=-3);self.edit('Other edit',self.current());before=self.raw()
        with self.assertRaises(store.HTTPError):self.route('audio_gain',body)
        self.assertEqual(self.raw(),before)

    def test_real_peak_normalization_includes_effects_ramp_reverse_fades_and_curve_excludes_buses(self):
        p=self.project();track=p['sequences'][0]['tracks'][0];track.update(gain_db=18,audio_fx={'compressor':True});p['sequences'][0]['master']={'gain_db':-12}
        self.clip(p).update(in_=0,out=6,reverse=True,time_remap=[{'t':0,'v':1.5}],audio={'gain_db':-6,'fade_in':.5,'fade_out':.4,'pan':.2},
                            keyframes={'audio.gain_db':[{'t':-1,'v':-8},{'t':3,'v':-4},{'t':5,'v':-2}]},audio_fx={'highpass':80})
        self.env['save_project'](p);before=self.project();identity=self.ready(target=-3);review=self.review(identity)
        self.assertEqual(self.project(),before);self.assertEqual(review['result']['measurements'][0]['duration'],4)
        self.assertTrue(self.apply(identity,review)['ok']);after=self.project()
        payload=audio.capture(after,self.request(target=-3),'peak',self.current());measured=engine.analyze(payload)['measurements'][0]
        self.assertAlmostEqual(measured['peak_db'],-3,delta=.002)
        delta=review['plan']['summary']['gains'][0]['delta_db'];changed=self.clip(after)
        self.assertAlmostEqual(changed['audio']['gain_db'],self.clip(before)['audio']['gain_db']+delta)
        self.assertEqual(changed['time_remap'],self.clip(before)['time_remap']);self.assertEqual(changed['markers'],self.clip(before)['markers'])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_raw_interpreted_subclip_window_and_readonly_review(self):
        # Retained audit WAV: first six native seconds 1000 peak, then10000.
        source=ROOT/'benchmarks/source-replacement-sync/reviews/m1k-audio-audit/media/quiet_then_loud.wav'
        p=self.project();p['media']['parent']={'id':'parent','path':str(source),'duration':24,'native_duration':12,'fps':30,'frame_rate':30,'interpret_fps':15,'has_audio':True,'channels':1}
        p['media']['sub']={**p['media']['parent'],'id':'sub','subclip_of':'parent','sub_in':6,'duration':8};self.env['save_project'](p)
        identity=self.ready(clip_ids=None,sequence=None,media_id='sub',**{'in':2,'out':4});review=self.review(identity)
        self.assertEqual(review['result']['scope'],'media');self.assertEqual(review['result']['range'],{'start':2,'end':4})
        self.assertLess(review['result']['measurements'][0]['peak_db'],-29);self.assertEqual(review['plan']['ops'],[])
        before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(identity,review)
        self.assertEqual(self.raw(),before)

    def test_loudness_plan_predicts_peak_and_preserves_settings_without_limiting(self):
        identity=self.ready('loudness',target=-10);review=self.review(identity);item=review['result']['measurements'][0]
        gain=review['plan']['summary']['gains'][0]
        self.assertAlmostEqual(gain['predicted_true_peak_dbtp'],item['true_peak_dbtp']+gain['delta_db']);self.assertIsNotNone(item['integrated_lufs'])
        before=self.clip();self.apply(identity,review);after=self.clip()
        self.assertEqual(after.get('afx_stack'),before.get('afx_stack'));self.assertEqual(after['audio'].get('fade_in'),before['audio'].get('fade_in'))

    def test_source_target_locks_foreign_project_and_stale_review_cannot_apply(self):
        identity=self.ready();review=self.review(identity);self.edit('Unrelated',self.current());before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(identity,review)
        self.assertEqual(self.raw(),before);fresh=self.review(identity)
        self.env['set_active_project']('b');other=self.raw('b')
        with self.assertRaises(store.HTTPError):self.apply(identity,fresh)
        self.assertEqual(self.raw('b'),other);self.env['set_active_project']('a')
        p=self.project();p['sequences'][0]['tracks'][0]['locked']=True;self.env['save_project'](p)
        with self.assertRaises(store.HTTPError):self.review(identity)

    def test_lost_receipt_restart_and_duplicate_submission_are_idempotent(self):
        identity=self.ready();review=self.review(identity);original=self.manager.store.save
        self.assertEqual(self.route('audio_peak',self.request())['task']['id'],identity)
        def fail(value):
            if value['record']['status']=='applied':raise OSError('lost receipt')
            return original(value)
        with patch.object(self.manager.store,'save',fail):self.assertTrue(self.apply(identity,review)['ok'])
        restored=TaskManager(self.root,{'audio_analysis':engine.analyze},start=False);self.addCleanup(restored.shutdown);self.env['TASKS']=restored
        before=self.raw();self.assertTrue(self.route('background_task_apply',{'_context':self.current()},identity)['ok']);self.assertEqual(self.raw(),before)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_manual_and_task_agent_proposals_mode_and_commit_failure_preserve_project(self):
        identity=self.ready();review=self.review(identity);before=self.raw();(self.root/'settings.json').write_text(json.dumps({'agent_mode':'proposals_only'}))
        with self.assertRaises(store.HTTPError):self.manual(actor='agent')
        with self.assertRaises(store.HTTPError):self.apply(identity,review,actor='agent')
        with patch.dict(self.env,commit_pair=lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full'))),self.assertRaises(OSError):self.apply(identity,review)
        self.assertEqual(self.raw(),before);self.assertEqual(self.manager.get(identity)['record']['status'],'ready');self.assertEqual(len(self.env['read_undo_history']('a')['undo']),0)

    def test_capture_and_apply_recheck_owner_after_await_and_keep_loop_responsive(self):
        original=audio.capture;started,release=threading.Event(),threading.Event()
        def slow(*args):started.set();release.wait(5);return original(*args)
        async def scenario():
            task=asyncio.create_task(self.env['audio_peak'](store.Request(self.request())))
            for _ in range(1000):
                if started.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set());self.assertFalse(task.done());self.env['set_active_project']('b');release.set()
            with self.assertRaises(store.HTTPError):await task
            self.assertFalse(self.manager.values)
        with patch.object(audio,'capture',slow):run_async_check(scenario())
        self.env['set_active_project']('a');identity=self.ready();review=self.review(identity);actual=audio.plan;before_a,before_b=self.raw('a'),self.raw('b')
        def switch(*args):
            result=actual(*args);self.env['set_active_project']('b');return result
        with patch.object(audio,'plan',switch),self.assertRaises(store.HTTPError):self.apply(identity,review)
        self.assertEqual(self.raw('a'),before_a);self.assertEqual(self.raw('b'),before_b)

    def test_derived_preparation_and_retry_preserve_audio_identity(self):
        identity=self.ready();p=self.project();p['media']['a'].update(proxy='/tmp/derived.mp4',thumb='/tmp/derived.jpg',status='ready',name='Renamed');self.env['save_project'](p)
        self.assertTrue(self.review(identity)['plan']['ops']);self.assertEqual(self.route('audio_peak',self.request())['task']['id'],identity)
        self.manager.cancel(identity);retry=self.route('background_task_retry',{'_context':self.current()},identity)
        self.assertNotEqual(retry['task']['id'],identity);self.assertEqual(retry['task']['kind'],'audio_analysis')

    def test_silence_and_unsupported_inaudible_selection_do_not_normalize(self):
        silent=ROOT/'benchmarks/source-replacement-sync/reviews/m1k-audio-audit/media/silence.wav';p=self.project();p['media']['a'].update(path=str(silent),duration=4);self.clip(p).update(out=4);self.env['save_project'](p)
        identity=self.ready();before=self.raw()
        with self.assertRaisesRegex(store.HTTPError,'Silent or gated'):self.review(identity)
        self.assertEqual(self.raw(),before)
        for extra in ({'hold':True},{'enabled':False}):
            self.edit_clip(**extra)
            with self.assertRaises(store.HTTPError):self.route('audio_peak',self.request(request_id='b'*32))



    def test_signed_gain_anchors_survive_actual_trim_gain_then_extend(self):
        clip=self.clip();clip.update(start=4,in_=1,out=7,keyframes={'audio.gain_db':[{'t':0,'v':-9,'e':'bezier','b':[.2,.1,.8,.9]},{'t':5,'v':-3}]})
        helper=ROOT/'tests/helpers/source-range-fixture.cjs'
        def trim(value,begin,end,start):
            data={'clip':value,'begin':begin,'end':end,'options':{'start':start,'sourceLimit':8}}
            return json.loads(subprocess.check_output(['node',str(helper)],input=json.dumps(data),text=True))
        cropped=trim(clip,2,6,6);changed=audio._gain(cropped,2.25);extended=trim(changed,-2,4,4)
        self.assertEqual(extended['keyframes']['audio.gain_db'],[{**p,'v':p['v']+2.25} for p in clip['keyframes']['audio.gain_db']])

    def test_rich_video_audio_ignores_foreign_picture_matte_but_retains_original_clip(self):
        p=self.project();track=p['sequences'][0]['tracks'][0];track['kind']='video';p['media']['a']['has_video']=True
        clip=self.clip(p);clip['fx_stack']=[{'type':'track_matte','enabled':True,'params':{'track':'foreign-picture','type':'alpha'}}]
        self.env['save_project'](p);identity=self.ready();review=self.review(identity);self.apply(identity,review)
        self.assertEqual(self.clip()['fx_stack'],clip['fx_stack'])

    def test_result_clock_duration_and_source_identity_are_checked_before_plan(self):
        identity=self.ready();value=self.manager.values[identity];result=copy.deepcopy(value['result']);original=copy.deepcopy(result)
        for change in ('clock','sequence','ids','duration','channels','source'):
            result=copy.deepcopy(original)
            if change=='clock':result['clock']='media'
            if change=='sequence':result['sequence']='other'
            if change=='ids':result['clip_ids']=['cb']
            if change=='duration':result['measurements'][0]['duration']+=1
            if change=='channels':result['measurements'][0]['channels']=1
            if change=='source':result['measurements'][0]['media_id']='b'
            with self.subTest(change=change),self.assertRaises(ValueError):audio.plan(self.project(),value['payload'],result,self.current(),identity)

    def test_actual_regular_onsets_apply_only_measured_local_candidates_with_unique_ids(self):
        path=self.root/'pulses.wav';rate=48000;samples=array.array('h')
        for i in range(6*rate):
            t=i/rate;phase=(t-.25)%.5
            samples.append(round(14000*math.sin(2*math.pi*523*t)) if t>=.25 and phase<.05 else 0)
        if sys.byteorder!='little':samples.byteswap()
        with wave.open(str(path),'wb') as stream:stream.setparams((1,2,rate,0,'NONE',''));stream.writeframes(samples.tobytes())
        p=self.project();p['media']['a'].update(path=str(path),duration=6,channels=1,sample_rate=rate);self.clip(p).update(in_=1,out=5,start=4,speed=1)
        p['sequences'][0]['markers']=[{'id':'existing','time':1,'name':'Keep'}];self.env['save_project'](p)
        identity=self.ready('beats',every=2);review=self.review(identity);beats=review['result']['measurements'][0]['beats'];markers=review['plan']['summary']['markers']
        self.assertGreaterEqual(len(beats),6);self.assertEqual([m['time'] for m in markers],[4+b for b in beats[::2]])
        before=self.project();self.apply(identity,review);after=self.project();saved=after['sequences'][0]['markers'];self.assertEqual(saved[0],before['sequences'][0]['markers'][0]);self.assertEqual(len({m['id'] for m in saved}),len(saved))
        self.assertEqual(before['sequences'][0]['tracks'],after['sequences'][0]['tracks']);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])



    def test_reanalyzing_normalized_float_pcm_is_noop_without_second_undo(self):
        identity=self.ready();self.apply(identity,self.review(identity));before=self.raw()
        second=self.ready(request_id='b'*32);review=self.review(second);self.assertEqual(review['plan']['ops'],[])
        self.assertFalse(self.apply(second,review)['changed']);self.assertEqual(self.raw(),before);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_selection_and_duration_work_limits_fail_before_queue(self):
        p=self.project();template=self.clip(p);track=p['sequences'][0]['tracks'][0]
        track['clips']=[{**copy.deepcopy(template),'id':'c'+str(i),'start':i*10} for i in range(51)];self.env['save_project'](p)
        with self.assertRaises(store.HTTPError):self.route('audio_peak',self.request(clip_ids=[c['id'] for c in track['clips']]))
        track['clips']=track['clips'][:7];p['media']['a']['duration']=700
        for c in track['clips']:c.update(out=600,start=0)
        self.env['save_project'](p)
        with self.assertRaises(store.HTTPError):self.route('audio_peak',self.request(clip_ids=[c['id'] for c in track['clips']]))
        with self.assertRaises(store.HTTPError):self.route('audio_beats',self.request(clip_ids=[c['id'] for c in track['clips'][:2]]))
        self.assertFalse(self.manager.values)

    def test_source_bytes_change_invalidates_review_and_cancelled_ready_cannot_apply(self):
        identity=self.ready();review=self.review(identity);before=self.raw();self.manager.cancel(identity)
        with self.assertRaises(store.HTTPError):self.apply(identity,review)
        self.assertEqual(self.raw(),before)
        retry=self.route('background_task_retry',{'_context':self.current()},identity);value=self.manager.values[retry['task']['id']]
        value['result']=self.env['_task_audio_workflow'](value['payload'],TaskContext(self.manager,retry['task']['id']));value['record']['status']='ready';self.manager.store.save(value)
        with (self.root/'a.wav').open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(store.HTTPError):self.review(retry['task']['id'])
        self.assertEqual(self.raw(),before)

    def test_nested_audio_capture_keeps_inner_mix_but_excludes_outer_processing(self):
        p=self.project();child=copy.deepcopy(p['sequences'][0]);child['id']='nested';child['name']='Nested sound';child['tracks']=child['tracks'][:1];child['tracks'][0]['clips'][0].update(id='inner',start=0)
        child['master']={'gain_db':-2};p['sequences'].append(child)
        clip=self.clip(p);clip.pop('media_id');clip.update(sequence_id='nested',in_=0,out=6)
        p['sequences'][0]['tracks'][0]['gain_db']=24;self.env['save_project'](p)
        identity=self.ready();review=self.review(identity);self.assertIsNotNone(review['result']['measurements'][0]['peak_db'])
        self.apply(identity,review);self.assertEqual(self.project()['sequences'][1],child);self.assertEqual(self.clip()['sequence_id'],'nested')



    def test_nested_render_dependency_work_is_bounded_before_queue(self):
        p=self.project();child=copy.deepcopy(p['sequences'][0]);child.update(id='long-child',name='Long nested audio');child['tracks']=child['tracks'][:1]
        child['tracks'][0]['clips'][0].update(id='inner',start=0,in_=0,out=601);p['media']['a']['duration']=700;p['sequences'].append(child)
        clip=self.clip(p);clip.pop('media_id');clip.update(sequence_id='long-child',in_=0,out=2);self.env['save_project'](p)
        before=self.raw()
        with self.assertRaises(store.HTTPError):self.route('audio_peak',self.request())
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)

    def test_native_decode_and_batch_work_are_bounded_before_queue(self):
        original=self.project()
        for case in ('fast_reverse','batch_native'):
            p=copy.deepcopy(original);p['media']['a']['duration']=700;track=p['sequences'][0]['tracks'][0]
            clip=track['clips'][0];targets=['ca']
            if case=='fast_reverse':clip.update(in_=0,out=601,speed=100,reverse=True)
            else:
                clip.update(in_=0,out=590,speed=59,start=0);tracks=[];targets=[]
                for index in range(7):
                    item=copy.deepcopy(track);item.update(id='budget-track-'+str(index),index=index)
                    item['clips'][0]['id']='budget-clip-'+str(index);tracks.append(item);targets.append(item['clips'][0]['id'])
                p['sequences'][0]['tracks']=tracks
            self.env['save_project'](p);before=self.raw()
            with self.subTest(case=case),self.assertRaises(store.HTTPError):
                self.route('audio_peak',self.request(clip_ids=targets))
            self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)

    def test_short_late_source_window_remains_available_for_owned_analysis(self):
        p=self.project();p['media']['a']['duration']=700;self.clip(p).update(in_=600,out=601,speed=1);self.env['save_project'](p)
        before=self.raw();reply=self.route('audio_peak',self.request())
        self.assertEqual(reply['task']['kind'],'audio_analysis');self.assertEqual(self.raw(),before)
        item=self.manager.values[reply['task']['id']]['payload']['items'][0]
        estimate=engine.estimated_work(item['project'],item['sequence'])
        self.assertLessEqual(estimate['native_seconds'],3.1)


if __name__=='__main__':unittest.main()
