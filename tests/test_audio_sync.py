"""Actual PCM synchronization and guarded persistent timeline apply contracts."""
import array
import ast
import asyncio
import copy
import json
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import audio_sync as sync
from background_tasks import TaskManager, TaskContext, TaskError
from test_media_collection import run_async_check
import test_project_sync as store

CONTEXT = {'workspace': 'w', 'project': 'p', 'revision': 'r'}


def recording(root, offset=.123, gain=.25, polarity=-1):
    """Two independent recorders: second opens offset seconds after first."""
    rng = random.Random(4821); amplitudes = [rng.uniform(.05, .9) for _ in range(150)]
    samples = [int(rng.uniform(-1, 1) * amplitudes[i//640] * 24000) for i in range(12*8000)]
    media = {}
    for mid, begin, volume in [('a', 0, 1), ('b', round(offset*8000), gain*polarity)]:
        path = Path(root) / (mid + '.wav'); data = array.array('h')
        for value in samples[begin:begin+8*8000]: data.extend([round(value*volume), -round(value*volume)])
        if sys.byteorder != 'little': data.byteswap()
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((2, 2, 8000, 0, 'NONE', '')); stream.writeframes(data.tobytes())
        media[mid] = {'id': mid, 'path': str(path), 'duration': 8, 'has_audio': True, 'channels': 2, 'sample_rate': 8000}
    clips = [{'id': 'c'+mid, 'media_id': mid, 'start': start, 'in_': 0, 'out': 8, 'speed': 1,
              'audio': {'gain_db': -4}, 'markers': [{'t': .5, 'name': 'Preserved'}], 'name': mid} for mid, start in [('a', 2), ('b', 20)]]
    return {'version': 3, 'media': media, 'sequences': [{'id': 's', 'fps': 30, 'width': 64, 'height': 48,
        'tracks': [{'id': 't'+c['id'], 'kind': 'audio', 'index': i, 'clips': [c]} for i,c in enumerate(clips)]}]}


class Signals(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity sync É's "); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.project = recording(self.root)

    def capture(self, **kw): return sync.capture(self.project, {'media_ids': ['a', 'b'], **kw}, CONTEXT)
    def analyze(self, **kw): return sync.analyze(self.capture(**kw), scratch_parent=str(self.root))

    def test_real_opposite_phase_stereo_gain_and_polarity_refine_signed_offset(self):
        result = self.analyze(); match = result['matches'][1]
        self.assertEqual(result['offsets']['b'], .123); self.assertEqual(match['method'], 'pcm')
        self.assertEqual(match['resolution'], 1/8000); self.assertEqual(match['polarity'], -1)
        self.assertGreater(match['waveform_correlation'], .999)
        reverse = self.analyze(media_ids=['b', 'a']); self.assertEqual(reverse['offsets']['a'], -.123)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_subclip_nonzero_source_in_and_canonical_interpretation_map_offset_once(self):
        for mid in ('a', 'b'):
            self.project['media'][mid].update(frame_rate='30000/1001', fps=29.97, interpret_fps=23.976, duration=10)
            self.project['media']['sub'+mid] = {**self.project['media'][mid], 'id': 'sub'+mid, 'subclip_of': mid, 'sub_in': 1.25, 'duration': 8.75}
            clip = self.project['sequences'][0]['tracks'][ord(mid)-ord('a')]['clips'][0]
            clip.update(media_id='sub'+mid, in_=1.25, out=7.5, speed=1.25)
        payload = sync.capture(self.project, {'sequence': 's', 'clip_ids': ['ca', 'cb']}, CONTEXT)
        self.assertEqual(payload['items'][0]['factor'], 1.25)
        self.assertEqual(sync.analyze(payload)['offsets']['cb'], .123)

    def test_nonzero_container_origin_and_delayed_audio_preserve_common_source_clock(self):
        for mid, origin in [('a', 5), ('b', 7)]:
            media = self.project['media'][mid]; path = self.root / (mid+'.mov')
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:s=32x32:r=10:d=8',
                '-itsoffset', '0.375', '-i', media['path'], '-map', '0:v', '-map', '1:a', '-c:v', 'png', '-threads', '1',
                '-c:a', 'pcm_s16le', '-output_ts_offset', str(origin), str(path)], check=True, capture_output=True, timeout=30)
            media.update(path=str(path), has_video=True, fps=10, duration=8)
        self.assertAlmostEqual(self.analyze()['offsets']['b'], .123, delta=1/8000)

    def test_silent_and_stationary_sources_refuse_instead_of_zero(self):
        for value in (0, 10000):
            with wave.open(self.project['media']['b']['path'], 'wb') as stream:
                stream.setparams((2, 2, 8000, 0, 'NONE', '')); stream.writeframes(array.array('h', [value, -value]*64000).tobytes())
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, 'silent|little changing'): self.analyze()

    def test_normalized_fft_peak_matches_gain_invariant_independent_lag(self):
        rng = random.Random(52); a = [rng.uniform(.1, 1) for _ in range(600)]
        b = [x*.2+.1 for x in a[17:]] + [rng.uniform(.1,.3) for _ in range(17)]
        result = sync.correlate(a,b); self.assertEqual(result['offset'], .34); self.assertAlmostEqual(result['correlation'], 1)
        periodic = [(.1,.8,.2,.5)[i%4] for i in range(600)]
        with self.assertRaisesRegex(ValueError, 'ambiguous'): sync.correlate(periodic, periodic)
        with self.assertRaises(ValueError): sync.correlate(a, [rng.random() for _ in a])

    def test_bounds_malformed_rates_and_advanced_clocks_reject_before_work(self):
        original = copy.deepcopy(self.project)
        for mutation in ('locked','reverse','hold','ramp','rate','short','duplicate','too_many'):
            self.project = copy.deepcopy(original); track = self.project['sequences'][0]['tracks'][1]; clip = track['clips'][0]
            body = {'sequence': 's', 'clip_ids': ['ca','cb']}
            if mutation == 'locked': track['locked'] = True
            if mutation in ('reverse','hold'): clip[mutation] = True
            if mutation == 'ramp': clip['time_remap'] = [{'t':0,'v':1}]
            if mutation == 'rate': clip['speed'] = 2
            if mutation == 'short': clip['out'] = 1
            if mutation == 'duplicate': body['clip_ids'] = ['ca','ca']
            if mutation == 'too_many': body['clip_ids'] = list('abcdefghi')
            with self.subTest(mutation=mutation), self.assertRaises(ValueError): sync.capture(self.project, body, CONTEXT)
        with self.assertRaises(ValueError): sync.correlate([1]*6001,[1]*6001)

    def test_invalid_parent_window_and_extreme_interpretation_fail_before_decode(self):
        self.project['media']['sub']={**self.project['media']['a'],'id':'sub','subclip_of':'a','sub_in':7,'duration':4}
        with self.assertRaisesRegex(ValueError,'parent source'): self.capture(media_ids=['sub','b'])
        for m in self.project['media'].values(): m.update(frame_rate=30,interpret_fps=1e-15)
        with self.assertRaises(ValueError): self.capture()

    def test_refinement_falls_back_honestly_for_different_waveforms(self):
        rng = random.Random(9)
        reference = {'pcm': array.array('f',[rng.uniform(-1,1) for _ in range(64000)]), 'effective_rate':1}
        target = {'pcm': array.array('f',[rng.uniform(-1,1) for _ in range(64000)]), 'effective_rate':1}
        match = {'offset':.12,'method':'envelope','resolution':.02}
        self.assertEqual(sync.refine(reference,target,match), match)

    def test_fft_and_pcm_cancellation_are_cooperative(self):
        def stop(): raise RuntimeError('cancelled')
        with self.assertRaisesRegex(RuntimeError,'cancelled'): sync.correlate([.1,.5]*500,[.1,.5]*500,stop)
        with patch.object(sync.media_analysis, '_run', side_effect=ValueError('FFmpeg failed')), self.assertRaisesRegex(ValueError,'FFmpeg failed'): self.analyze()
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_slow_interpretation_reports_native_analysis_spacing(self):
        for media in self.project['media'].values(): media.update(frame_rate=30, interpret_fps=15, duration=16)
        refined = self.analyze()['matches'][1]
        self.assertEqual(refined['offset'],.246); self.assertEqual(refined['resolution'],1/4000)
        with patch.object(sync,'refine',side_effect=lambda a,b,m,c:m):
            coarse=self.analyze()['matches'][1]
        self.assertEqual(coarse['method'],'envelope'); self.assertEqual(coarse['resolution'],.04)

    def test_running_cancel_reaps_owned_ffmpeg_and_persists_no_result(self):
        actual=sync.media_analysis._run
        def slow(command,context,duration,progress):
            return actual(['ffmpeg','-v','error','-nostdin','-re','-f','lavfi','-i','anullsrc=r=8000:cl=stereo',
                '-t','30','-f','null','-'],context,duration,progress)
        manager=TaskManager(self.root,{'sync':sync.analyze},workers=1); self.addCleanup(manager.shutdown)
        context={'workspace':manager.store.workspace,'project':'p','revision':'r'}
        payload=sync.capture(self.project,{'media_ids':['a','b']},context)
        with patch.object(sync.media_analysis,'_run',slow):
            identity=manager.submit('sync','Cancel actual decoder',context,payload)['id']; deadline=time.monotonic()+5; child=None
            while time.monotonic()<deadline:
                task=manager.contexts.get(identity)
                if task and task.holder.get('proc'): child=task.holder['proc']; break
                time.sleep(.01)
            self.assertIsNotNone(child); manager.cancel(identity)
            while identity in manager.contexts and time.monotonic()<deadline: time.sleep(.01)
            self.assertNotIn(identity,manager.contexts); self.assertIsNotNone(child.poll())
            self.assertEqual(manager.get(identity)['record']['status'],'cancelled'); self.assertIsNone(manager.get(identity)['result'])

    def test_plan_rejects_collision_group_separation_and_negative_origin(self):
        payload=sync.capture(self.project,{'sequence':'s','clip_ids':['ca','cb']},CONTEXT); result=sync.analyze(payload)
        original=copy.deepcopy(self.project)
        # Add peers after capture: source/target identity remains unchanged, but
        # the current arrangement must still be safe to apply.
        self.project['sequences'][0]['tracks'][1]['clips'].append({'id':'neighbor','start':1,'in_':0,'out':4,'media_id':'a'})
        with self.assertRaisesRegex(ValueError,'overlap'): sync.plan(self.project,payload,result,CONTEXT,'task')
        self.project=copy.deepcopy(original)
        for track in self.project['sequences'][0]['tracks']: track['clips'][0]['group']='together'
        payload=sync.capture(self.project,{'sequence':'s','clip_ids':['ca','cb']},CONTEXT); result['signature']=payload['signature']
        with self.assertRaisesRegex(ValueError,'partners'): sync.plan(self.project,payload,result,CONTEXT,'task')
        self.project=original; self.project['sequences'][0]['tracks'][0]['clips'][0]['start']=0
        payload=sync.capture(self.project,{'sequence':'s','clip_ids':['ca','cb']},CONTEXT); result['signature']=payload['signature']; result['offsets']['cb']=-.1
        with self.assertRaisesRegex(ValueError,'before timeline zero'): sync.plan(self.project,payload,result,CONTEXT,'task')

    def test_picture_moves_quantize_to_sequence_frames_with_visible_residual(self):
        self.project['sequences'][0]['tracks'][1]['kind']='video'
        payload=sync.capture(self.project,{'sequence':'s','clip_ids':['ca','cb']},CONTEXT); result=sync.analyze(payload)
        plan=sync.plan(self.project,payload,result,CONTEXT,'task'); move=plan['summary']['moves'][0]
        self.assertAlmostEqual(move['to'],64/30); self.assertAlmostEqual(move['residual'],64/30-2.123)


class StoreSync(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp(); project = self.env['load_project'](); project.update(recording(self.root)); self.env['save_project'](project)
        self.manager = TaskManager(self.root, {'sync': sync.analyze}, start=False); self.addCleanup(self.manager.shutdown)
        self.env.update(asyncio=asyncio,TASKS=self.manager,TaskError=TaskError)
        names = {'audio_sync','_task_audio_sync','_review_audio_sync','background_sync_review','_apply_audio_sync',
                 '_owned_task','background_task_apply','background_task_retry','_workflow_capture','_workflow_commit'}
        nodes = [n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        for node in nodes: node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)

    def route(self,name,body,identity=None):
        return run_async_check(self.env[name](*(([identity] if identity else [])+[store.Request(body)])))

    def ready(self,raw=False):
        body={'_context':self.current(),'request_id':'a'*32, **({'media_ids':['a','b']} if raw else {'sequence':'s','clip_ids':['ca','cb']})}
        identity=self.route('audio_sync',body)['task']['id']; value=self.manager.values[identity]
        value['result']=sync.analyze(value['payload'],TaskContext(self.manager,identity)); value['record']['status']='ready'; self.manager.store.save(value)
        return identity

    def review(self,identity): return self.route('background_sync_review',{'_context':self.current()},identity)
    def apply(self,identity,review,**kw): return self.route('background_task_apply',{'_context':review['context'],'fingerprint':review['plan']['fingerprint'],**kw},identity)

    def test_real_saved_apply_preserves_complete_clip_metadata_and_one_undo_redo(self):
        before=self.env['load_project'](); identity=self.ready(); self.assertEqual(self.env['load_project'](),before)
        review=self.review(identity); self.assertEqual(review['result']['offsets']['cb'],.123)
        self.assertTrue(self.apply(identity,review)['ok']); after=self.env['load_project'](); clips=after['sequences'][0]['tracks']
        expected=copy.deepcopy(before['sequences'][0]['tracks']); expected[1]['clips'][0]['start']=2.123
        self.assertEqual(clips,expected); self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.assertTrue(self.route('background_task_apply',{'_context':self.current()},identity)['ok'])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()}); self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()}); self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])

    def test_unrelated_edits_can_be_reviewed_again_but_stale_review_and_foreign_project_refuse(self):
        identity=self.ready(); review=self.review(identity); self.edit('Unrelated',self.current()); before=self.raw()
        with self.assertRaises(store.HTTPError): self.apply(identity,review)
        self.assertEqual(self.raw(),before); fresh=self.review(identity); self.assertNotEqual(fresh['plan']['fingerprint'],review['plan']['fingerprint'])
        self.env['set_active_project']('b'); other=self.raw('b')
        with self.assertRaises(store.HTTPError): self.review(identity)
        with self.assertRaises(store.HTTPError): self.apply(identity,fresh)
        self.assertEqual(self.raw('b'),other)

    def test_raw_review_empty_plan_is_readonly_and_survives_restart(self):
        identity=self.ready(raw=True); before=self.raw(); review=self.review(identity)
        self.assertEqual(review['plan']['ops'],[]); self.assertTrue(review['plan']['summary']['message'])
        with self.assertRaises(store.HTTPError): self.apply(identity,review)
        self.assertEqual(self.raw(),before)
        restored=TaskManager(self.root,{'sync':sync.analyze},start=False); self.addCleanup(restored.shutdown)
        self.assertEqual(restored.get(identity)['record']['status'],'ready')

    def test_collisions_locks_groups_and_changed_target_or_source_refuse_without_mutation(self):
        identity=self.ready(); original=self.env['load_project']()
        for change in ('collision','lock','group','target','source'):
            project=copy.deepcopy(original); tracks=project['sequences'][0]['tracks']
            if change=='collision': tracks[1]['clips'].append({'id':'other','start':1,'in_':0,'out':3,'media_id':'a'})
            if change=='lock': tracks[1]['locked']=True
            if change=='group':
                tracks[1]['clips'][0]['group']='linked'; tracks[0]['clips'][0]['group']='linked'
            if change=='target': tracks[1]['clips'][0]['audio']['gain_db']=0
            if change=='source': project['media']['b']['duration']=9
            self.env['save_project'](project); before=self.raw()
            with self.subTest(change=change),self.assertRaises(store.HTTPError): self.review(identity)
            self.assertEqual(self.raw(),before)

    def test_submit_is_acknowledged_idempotent_and_context_checked_after_capture(self):
        with self.assertRaises(store.HTTPError): self.route('audio_sync',{'media_ids':['a','b']})
        started,release=threading.Event(),threading.Event(); actual=sync.capture
        def slow(*args): started.set(); release.wait(5); return actual(*args)
        async def scenario():
            task=asyncio.create_task(self.env['audio_sync'](store.Request({'_context':self.current(),'media_ids':['a','b'],'request_id':'a'*32})))
            for _ in range(1000):
                if started.is_set(): break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set()); self.assertFalse(task.done()); self.env['set_active_project']('b'); release.set()
            with self.assertRaises(store.HTTPError): await task
            self.assertFalse(self.manager.values)
        with patch.object(sync,'capture',slow): run_async_check(scenario())

    def test_apply_failure_and_agent_proposals_mode_preserve_ready_task_and_store(self):
        identity=self.ready(); review=self.review(identity); before=self.raw()
        (self.root/'settings.json').write_text(json.dumps({'agent_mode':'proposals_only'}))
        with self.assertRaises(store.HTTPError): self.apply(identity,review,actor='agent')
        with patch.dict(self.env,commit_pair=lambda *a,**kw: (_ for _ in ()).throw(OSError('disk full'))),self.assertRaises(OSError): self.apply(identity,review)
        self.assertEqual(self.raw(),before); self.assertEqual(self.manager.get(identity)['record']['status'],'ready')
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),0)

    def test_lost_receipt_cannot_repeat_saved_moves(self):
        identity=self.ready(); review=self.review(identity); original=self.manager.store.save
        def lose(value):
            if value['record']['status']=='applied': raise OSError('lost receipt')
            return original(value)
        with patch.object(self.manager.store,'save',lose): result=self.apply(identity,review)
        self.assertTrue(result['ok']); self.assertIn('history could not be saved',result['warning'])
        restored=TaskManager(self.root,{'sync':sync.analyze},start=False); self.addCleanup(restored.shutdown); self.env['TASKS']=restored
        before=self.raw(); self.assertTrue(self.route('background_task_apply',{'_context':self.current()},identity)['ok']); self.assertEqual(self.raw(),before)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_harmless_preparation_completion_does_not_invalidate_source_analysis(self):
        identity=self.ready(); project=self.env['load_project']()
        project['media']['a'].update(proxy='/tmp/new-proxy.mp4',thumb='/tmp/new-thumb.jpg',status='ready',name='Renamed')
        self.env['save_project'](project); self.assertTrue(self.review(identity)['plan']['ops'])
        request={'_context':self.current(),'request_id':identity,'sequence':'s','clip_ids':['ca','cb']}
        self.assertEqual(self.route('audio_sync',request)['task']['id'],identity)

    def test_already_aligned_apply_has_no_receipt_or_history_change(self):
        project=self.env['load_project'](); project['sequences'][0]['tracks'][1]['clips'][0]['start']=2.123; self.env['save_project'](project)
        identity=self.ready(); review=self.review(identity); before=self.raw()
        self.assertEqual(review['plan']['ops'],[]); response=self.apply(identity,review)
        self.assertFalse(response['changed']); self.assertEqual(self.raw(),before); self.assertEqual(len(self.env['read_undo_history']('a')['undo']),0)

    def test_source_file_mutation_and_project_switch_during_review_cannot_apply(self):
        identity=self.ready(); review=self.review(identity); before_a,before_b=self.raw('a'),self.raw('b'); actual=sync.plan
        def switch(*args):
            result=actual(*args); self.env['set_active_project']('b'); return result
        with patch.object(sync,'plan',switch),self.assertRaises(store.HTTPError): self.apply(identity,review)
        self.assertEqual(self.raw('a'),before_a); self.assertEqual(self.raw('b'),before_b)
        self.env['set_active_project']('a')
        with (self.root/'b.wav').open('ab') as stream: stream.write(b'changed')
        with self.assertRaises(store.HTTPError): self.review(identity)

    def test_duplicate_submit_and_retry_keep_captured_sources_and_fresh_identity(self):
        identity=self.ready(); request={'_context':self.current(),'request_id':identity,'sequence':'s','clip_ids':['ca','cb']}
        self.assertEqual(self.route('audio_sync',request)['task']['id'],identity)
        self.manager.cancel(identity)
        retry=self.route('background_task_retry',{'_context':self.current()},identity)
        self.assertNotEqual(retry['task']['id'],identity); self.assertEqual(retry['task']['kind'],'sync')


if __name__=='__main__': unittest.main()
