"""Real source-window analysis, resource ownership, and guarded task contracts."""
import array
import ast
import asyncio
import copy
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import uuid
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import media_analysis as analysis
from background_tasks import TaskManager, TaskError
from render_context import RenderContext
from source_sync_fixture import make_source
import test_project_sync as store

CONTEXT = {'workspace': 'workspace', 'project': 'project', 'revision': 'revision'}


class AnalysisTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity analysis É's "); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def source(self, **options):
        path, _, _ = make_source(self.root / str(len(list(self.root.iterdir()))), duration=1.5, **options)
        return {'media': {'m': {'id': 'm', 'path': str(path), 'duration': 1.5, 'has_audio': True, 'has_video': True,
                                'fps': 20, 'frame_rate': '20/1', 'channels': 1}}, 'sequences': []}

    def wav(self, seconds, *, channels=1, sound=lambda t: 0):
        path = self.root / f'{len(list(self.root.iterdir()))}.wav'; rate = 8000
        samples = array.array('h')
        for index in range(round(seconds * rate)):
            value = sound(index / rate)
            samples.extend([value] if channels == 1 else [value, -value])
        if sys.byteorder != 'little': samples.byteswap()
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((channels, 2, rate, 0, 'NONE', '')); stream.writeframes(samples.tobytes())
        return {'media': {'m': {'id': 'm', 'path': str(path), 'duration': seconds, 'has_audio': True, 'channels': channels}}, 'sequences': []}

    def analyze(self, project, mode='silences', **request):
        payload = analysis.capture(project, {'media_id': 'm', 'min_gap': 0, 'pad': 0, **request}, mode, CONTEXT)
        return analysis.analyze(payload, scratch_parent=str(self.root))

    def test_audio_offset_and_nonzero_container_origin_preserve_common_clock(self):
        baseline = self.analyze(self.source(audio_offset=.375))
        shifted = self.analyze(self.source(origin=5, audio_offset=.375))
        self.assertEqual(baseline['silences'], shifted['silences'])
        first = baseline['silences'][0]
        self.assertAlmostEqual(first['end'], .475, delta=.021)
        self.assertAlmostEqual(baseline['silences'][1]['start'], .525, delta=.021)
        self.assertEqual(first['start'], 0)
        self.assertEqual(baseline['silences'][-1]['end'], 1.5)

    def test_zero_short_and_opposite_phase_audio_are_measured_without_downmix_cancellation(self):
        for duration in (.001, .03, .3):
            result = self.analyze(self.wav(duration))
            self.assertEqual(result['silences'], [{'start': 0.0, 'end': duration}])
        result = self.analyze(self.wav(.2, channels=2, sound=lambda t: 10000))
        self.assertEqual(result['silences'], [])

    def test_requested_window_and_padding_never_remove_outside_the_window(self):
        project = self.wav(1, sound=lambda t: 10000 if t < .2 or t >= .8 else 0)
        result = self.analyze(project, **{'in': .3, 'out': .6, 'pad': .08})
        self.assertEqual(result['silences'], [{'start': .3, 'end': .6}])
        whole = self.analyze(project, min_gap=.3, pad=.08)
        self.assertAlmostEqual(whole['silences'][0]['start'], .28, places=5)
        self.assertAlmostEqual(whole['silences'][0]['end'], .72, places=5)
        self.assertAlmostEqual(whole['removed'], .44, places=5)

    def test_subclip_and_late_seek_analyze_parent_offset_once(self):
        project = self.wav(8, sound=lambda t: 10000 if 5.2 <= t < 5.4 else 0)
        project['media']['sub'] = {**project['media']['m'], 'id': 'sub', 'subclip_of': 'm', 'sub_in': 5, 'duration': 1}
        payload = analysis.capture(project, {'media_id': 'sub', 'out': None, 'min_gap': 0, 'pad': 0}, 'silences', CONTEXT)
        result = analysis.analyze(payload)
        self.assertEqual(result['range'], {'start': 0, 'end': 1})
        self.assertAlmostEqual(result['silences'][0]['end'], .2, delta=.021)
        self.assertAlmostEqual(result['silences'][1]['start'], .4, delta=.021)
        self.assertEqual(result['silences'][-1]['end'], 1)

    def test_audio_window_wholly_before_or_after_actual_stream_is_silent(self):
        project = self.source(audio_offset=.375)
        for begin, end in [(0, .2), (1.4, 1.5)]:
            self.assertEqual(self.analyze(project, **{'in': begin, 'out': end})['silences'], [{'start': begin, 'end': end}])
        project = self.wav(1, sound=lambda t: 10000); project['media']['m']['duration'] = 6
        self.assertEqual(self.analyze(project, **{'in': 5, 'out': 6})['silences'], [{'start': 5, 'end': 6}])

    def test_scene_results_are_absolute_media_times_in_requested_subclip_interpreted_clock(self):
        project = self.source(origin=5)
        result = self.analyze(project, 'scenes', **{'in': .4, 'out': 1.0})
        self.assertEqual(len(result['cuts']), 1); self.assertAlmostEqual(result['cuts'][0], .6); self.assertEqual(result['clock'], 'media')
        project['media']['m'].update(interpret_fps=10, duration=3, fps=19.99)
        interpreted = self.analyze(project, 'scenes', **{'in': .8, 'out': 2})
        self.assertEqual(len(interpreted['cuts']), 1); self.assertAlmostEqual(interpreted['cuts'][0], 1.2)
        project['media']['sub'] = {**project['media']['m'], 'id': 'sub', 'subclip_of': 'm', 'sub_in': .8, 'duration': 1.2}
        payload = analysis.capture(project, {'media_id': 'sub', 'out': None}, 'scenes', CONTEXT)
        self.assertAlmostEqual(analysis.analyze(payload)['cuts'][0], .4, places=6)

    def test_scene_late_seek_with_nonzero_origin_preserves_absolute_subclip_time(self):
        path = self.root / 'late-scene.mov'
        command = ['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', "color=black:s=64x48:r=20:d=7,drawbox=c=white:t=fill:enable='gte(t,5.5)'",
                   '-c:v', 'png', '-threads', '1', '-output_ts_offset', '5', str(path)]
        subprocess.run(command, check=True, capture_output=True, timeout=30)
        project = {'media': {'m': {'id': 'm', 'path': str(path), 'duration': 7, 'fps': 20, 'has_video': True}}}
        result = self.analyze(project, 'scenes', **{'in': 5, 'out': 6})
        self.assertEqual(result['cuts'], [5.5])
        project['media']['sub'] = {**project['media']['m'], 'id': 'sub', 'subclip_of': 'm', 'sub_in': 5, 'duration': 1}
        payload = analysis.capture(project, {'media_id': 'sub'}, 'scenes', CONTEXT)
        self.assertEqual(analysis.analyze(payload)['cuts'], [.5])

    def test_interpreted_audio_times_use_rational_native_rate(self):
        project = self.wav(1, sound=lambda t: 10000 if .2 <= t < .4 else 0)
        project['media']['m'].update(fps=19.99, frame_rate='20/1', interpret_fps=10, duration=2)
        result = self.analyze(project)
        self.assertAlmostEqual(result['silences'][0]['end'], .4, delta=.04)
        self.assertAlmostEqual(result['silences'][1]['start'], .8, delta=.04)
        self.assertEqual(result['silences'][-1]['end'], 2)

    def test_decimal_interpretation_alias_uses_the_same_rational_source_window(self):
        project = self.wav(1, sound=lambda t: 10000 if .2 <= t < .4 else 0)
        project['media']['m'].update(fps=29.97, frame_rate='30000/1001', interpret_fps=23.976, duration=1.25)
        payload = analysis.capture(project, {'media_id': 'm', 'min_gap': 0, 'pad': 0}, 'silences', CONTEXT)
        self.assertEqual(payload['factor'], 1.25)
        result = analysis.analyze(payload, scratch_parent=str(self.root))
        self.assertAlmostEqual(result['silences'][0]['end'], .25, places=9)
        self.assertAlmostEqual(result['silences'][1]['start'], .5, places=9)
        project['media']['m']['interpret_fps'] = 24000 / 1001
        self.assertEqual(self.analyze(project)['silences'], result['silences'])

    def test_interpretation_factor_rejects_malformed_authoritative_rates_without_fallback(self):
        from timeline_time import interpretation_factor
        for field in ('frame_rate', 'interpret_fps'):
            for value in (0, -1, False, '', 'bad', float('nan'), float('inf')):
                media = {'frame_rate': '30000/1001', 'fps': 30, 'interpret_fps': 23.976, field: value}
                with self.subTest(field=field, value=value), self.assertRaises(ValueError): interpretation_factor(media)
        self.assertEqual(interpretation_factor({'fps': 29.97, 'interpret_fps': 23.976}), 1.25)
        self.assertEqual(interpretation_factor({'fps': 29.97}), 1)

    def test_picture_and_sound_render_windows_use_identical_rational_interpretation(self):
        import render
        project = self.source()
        project['media']['m'].update(fps=29.97, frame_rate='30000/1001', interpret_fps=23.976, duration=4000)
        project['sequences'] = [{'id': 's', 'width': 64, 'height': 48, 'fps': 30,
            'tracks': [{'id': 'v', 'kind': 'video', 'index': 0, 'clips':
                [{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 3000, 'out': 3001.25}]}]}]
        with RenderContext(scratch_parent=str(self.root)) as context:
            command, _ = render.build_command(project, 's', str(self.root / 'out.mp4'), context=context)
            graph = command[command.index('-filter_complex') + 1]
            self.assertIn('trim=start=2400.000000000000:end=2401.000000000000', graph)
            self.assertIn('atrim=start_sample=115200000:end_sample=115248000', graph)
            self.assertIn('setpts=PTS*1.250000000000', graph)
            self.assertIn('atempo=0.800000000000', graph)
        project['media']['m']['frame_rate'] = 0
        with RenderContext(scratch_parent=str(self.root)) as context, self.assertRaises(ValueError):
            render.build_command(project, 's', str(self.root / 'bad.mp4'), context=context)

    def test_held_picture_graph_uses_interpreted_native_frame_and_rejects_unknown_timed_rate(self):
        import render
        project = self.source()
        project['media']['m'].update(fps=29.97, frame_rate='30000/1001', interpret_fps=23.976, sub_in=6, duration=4000)
        project['sequences'] = [{'id': 's', 'width': 64, 'height': 48, 'fps': 30,
            'tracks': [{'id': 'v', 'kind': 'video', 'index': 0, 'clips':
                [{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 2994, 'out': 2995, 'hold': True}]}]}]
        before = copy.deepcopy(project)
        with RenderContext(scratch_parent=str(self.root)) as context:
            command, _ = render.build_command(project, 's', str(self.root / 'hold.mp4'), context=context)
            graph = command[command.index('-filter_complex') + 1]
            self.assertIn('settb=expr=1/(30000/1001),trim=start_pts=71928:end_pts=71929', graph)
        self.assertEqual(project, before)
        for value in (None, 0, 'bad'):
            project['media']['m']['frame_rate'] = value
            project['media']['m'].pop('fps', None)
            with self.subTest(rate=value), RenderContext(scratch_parent=str(self.root)) as context, self.assertRaises(ValueError):
                render.build_command(project, 's', str(self.root / 'bad.mp4'), context=context)

    def test_held_interpreted_subclip_decodes_containing_fractional_cfr_picture(self):
        import render
        from PIL import Image
        from source_sync_fixture import run
        source = self.root / 'fractional held source.mov'
        # Independent source oracle: only decoded frame 1 is white. Both the
        # unconverted logical timestamp and rounding forward select black.
        run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i',
             "color=black:s=64x48:r=30000/1001:d=1,drawbox=c=white:t=fill:enable='eq(n,1)'",
             '-c:v', 'png', '-threads', '1', '-video_track_timescale', '30000', str(source)])
        project = {'media': {'m': {'id': 'm', 'path': str(source), 'duration': 1.25,
            'has_video': True, 'has_audio': False, 'fps': 29.97, 'frame_rate': '30000/1001',
            'interpret_fps': 23.976, 'sub_in': .02}}, 'sequences': [
            {'id': 's', 'width': 64, 'height': 48, 'fps': 30, 'tracks': [
                {'id': 'v', 'kind': 'video', 'index': 0, 'clips': [
                    {'id': 'c', 'media_id': 'm', 'start': 0, 'in_': .05, 'out': .25, 'hold': True}]}]}]}
        clip = project['sequences'][0]['tracks'][0]['clips'][0]
        for source_in in (.05, 1001 / 24000 - .02):
            clip.update(in_=source_in, out=source_in + .2)
            for at in (0, .15):
                with self.subTest(source_in=source_in, timeline=at), RenderContext(scratch_parent=str(self.root)) as context:
                    output = self.root / 'held.png'
                    render.render_frame(project, 's', at, str(output), context=context)
                    with Image.open(output) as image:
                        self.assertGreater(min(image.convert('RGB').getpixel((32, 24))), 245)

    def test_held_still_uses_first_picture_without_native_fps(self):
        import render
        from PIL import Image
        source = self.root / 'held still.png'; Image.new('RGB', (64, 48), (255, 0, 0)).save(source)
        project = {'media': {'m': {'id': 'm', 'path': str(source), 'duration': 1,
            'has_video': True, 'has_audio': False, 'is_image': True}}, 'sequences': [
            {'id': 's', 'width': 64, 'height': 48, 'fps': 30, 'tracks': [
                {'id': 'v', 'kind': 'video', 'index': 0, 'clips': [
                    {'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 4, 'out': 4.2, 'hold': True}]}]}]}
        with RenderContext(scratch_parent=str(self.root)) as context:
            output = self.root / 'held still output.png'
            render.render_frame(project, 's', .1, str(output), context=context)
            with Image.open(output) as image:
                red, green, blue = image.convert('RGB').getpixel((32, 24))
                self.assertGreater(red, 245); self.assertLess(max(green, blue), 5)

    def test_invalid_inputs_and_over_hour_ranges_fail_before_decoding(self):
        project = self.wav(.1)
        for request in ({'in': -1}, {'in': True}, {'out': float('nan')}, {'out': 0}, {'out': .2}, {'min_gap': -1}, {'pad': 11}, {'threshold_db': 1}):
            with self.subTest(request=request), self.assertRaises(ValueError): analysis.capture(project, {'media_id': 'm', **request}, 'silences', CONTEXT)
        project['media']['m']['duration'] = 7200
        with self.assertRaisesRegex(ValueError, 'one hour'): analysis.capture(project, {'media_id': 'm'}, 'silences', CONTEXT)
        self.assertEqual(analysis.capture(project, {'media_id': 'm', 'in': 6000, 'out': 6100}, 'silences', CONTEXT)['range']['end'], 6100)

    def test_changed_source_and_clip_refuse_results_while_unrelated_edits_allow_review(self):
        project = self.wav(.1); clip = {'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': .1}
        project['sequences'] = [{'id': 's', 'tracks': [{'id': 'a', 'kind': 'audio', 'clips': [clip]}]}]
        payload = analysis.capture(project, {'media_id': 'm', 'sequence': 's', 'clip_id': 'c'}, 'silences', CONTEXT)
        project['name'] = 'Changed'; current = {**CONTEXT, 'revision': 'next'}
        self.assertTrue(analysis.validate_current(project, payload, current))
        clip['start'] = 1
        with self.assertRaisesRegex(ValueError, 'clip changed'): analysis.validate_current(project, payload, current)
        clip['start'] = 0
        with open(project['media']['m']['path'], 'ab') as stream: stream.write(b'changed')
        with self.assertRaisesRegex(ValueError, 'source changed'): analysis.validate_current(project, payload, current)
        with self.assertRaisesRegex(ValueError, 'source file changed'): analysis.analyze(payload)

    def test_decode_failure_is_visible_and_scratch_and_child_are_retired(self):
        project = self.wav(.1); path = Path(project['media']['m']['path']); path.write_bytes(b'not media')
        payload = analysis.capture(project, {'media_id': 'm'}, 'silences', CONTEXT)
        with self.assertRaisesRegex(ValueError, 'analysis failed'): analysis.analyze(payload, scratch_parent=str(self.root))
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_source_change_after_decode_and_bounded_output_never_publish_results(self):
        project = self.wav(.1); payload = analysis.capture(project, {'media_id': 'm'}, 'silences', CONTEXT)
        original = analysis._run
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            with open(project['media']['m']['path'], 'ab') as stream: stream.write(b'changed')
            return result
        with patch.object(analysis, '_run', changed), self.assertRaisesRegex(ValueError, 'source file changed'):
            analysis.analyze(payload, scratch_parent=str(self.root))
        payload = analysis.capture(project, {'media_id': 'm'}, 'silences', CONTEXT)
        with patch.object(analysis, 'MAX_OUTPUT', 1), self.assertRaisesRegex(ValueError, 'bounded result limit'):
            analysis.analyze(payload, scratch_parent=str(self.root))
        self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_cancelled_processing_reaps_real_child_and_releases_work_slot(self):
        holder = {}; started = threading.Event(); child_ref = []; error = []
        original = analysis.subprocess.Popen
        def launch(*args, **kwargs):
            # Exercise the direct encoder process used in production. Windows
            # virtualenv python.exe is a launcher: killing it leaves the Python
            # grandchild alive with inherited output handles.
            child = original(['ffmpeg', '-hide_banner', '-nostdin', '-re', '-f', 'lavfi',
                              '-i', 'anullsrc=r=8000:cl=mono', '-t', '60', '-f', 'null', '-'], **kwargs)
            child_ref.append(child); started.set(); return child
        def worker():
            try:
                with RenderContext(proc_holder=holder, scratch_parent=str(self.root)) as context:
                    analysis._run(['unused'], context, 1, None)
            except Exception as exc: error.append(exc)
        with patch.object(analysis.subprocess, 'Popen', launch):
            thread = threading.Thread(target=worker); thread.start(); self.assertTrue(started.wait(3)); holder['cancelled'] = True; thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertIsNotNone(child_ref[0].poll()); self.assertRegex(str(error[0]), 'cancelled')
        self.assertNotIn('scratch_diagnostics', holder)
        self.assertNotIn('proc', holder); self.assertNotIn('resource_state', holder); self.assertFalse(list(self.root.glob('filmocity-render-*')))


class AnalysisRoutes(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        names = {'_workflow_capture', '_workflow_commit', '_queue_media_analysis', '_task_media_analysis', '_review_media_analysis',
                 '_apply_media_analysis', 'background_analysis_review', 'background_task_apply', '_owned_task', 'media_scenes', 'audio_silences'}
        tree = ast.parse((ROOT / 'backend/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        for node in nodes: node.decorator_list = []
        self.env.update(TaskError=TaskError)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'analysis-production-routes', 'exec'), self.env)
        self.tasks = TaskManager(str(self.root), {'analysis': self.env['_task_media_analysis']}, start=False)
        self.addCleanup(self.tasks.shutdown); self.env['TASKS'] = self.tasks
        path = self.root / 'source.wav'
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((1, 2, 8000, 0, 'NONE', '')); stream.writeframes(b'\0\0' * 16000)
        project = self.env['load_project'](); project['media'] = {'m': {'id': 'm', 'path': str(path), 'duration': 2, 'has_audio': True}}
        project['sequences'] = [{'id': 's', 'fps': 30, 'width': 64, 'height': 48, 'tracks': [{'id': 'a', 'kind': 'audio', 'clips': [{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 2, 'speed': 1}]}]}]
        self.env['save_project'](project)

    def queued(self, edit=False):
        body = {'media_id': 'm', 'out': None, '_context': self.current()}
        if edit: body.update(sequence='s', clip_id='c')
        response = self.invoke('audio_silences', body); identity = response['task']['id']
        value = self.tasks.values[identity]; value['record']['status'] = 'ready'
        value['result'] = { 'version': 1, 'kind': 'silences', 'clock': 'media', 'media_id': 'm', 'sequence': 's' if edit else None,
                           'clip_id': 'c' if edit else None, 'range': {'start': 0, 'end': 2}, 'silences': [{'start': .5, 'end': 1}],
                           'removed': .5, 'signature': value['payload']['signature'], 'context': value['payload']['context']}
        return identity

    def review(self, identity): return asyncio.run(self.env['background_analysis_review'](identity, store.Request({'_context': self.current()})))
    def apply(self, identity, body): return asyncio.run(self.env['background_task_apply'](identity, store.Request(body)))

    def test_submission_requires_context_accepts_null_out_and_persists_analysis_kind(self):
        with self.assertRaises(store.HTTPError): self.invoke('audio_silences', {'media_id': 'm'})
        identity = self.queued(); self.tasks.store.save(self.tasks.values[identity])
        restored, unavailable = self.tasks.store.restore()
        self.assertEqual(restored[identity]['record']['kind'], 'analysis'); self.assertFalse(unavailable)
        self.assertEqual(self.review(identity)['result']['clock'], 'media')

    def test_unrelated_edit_can_be_reviewed_but_source_and_project_switch_cannot(self):
        identity = self.queued(True); self.edit('Unrelated', self.current())
        review = self.review(identity); self.assertIn('plan', review); self.assertEqual(review['context'], self.current())
        project = self.env['load_project'](); project['sequences'][0]['tracks'][0]['clips'][0]['start'] = 1; self.env['save_project'](project)
        with self.assertRaisesRegex(store.HTTPError, 'clip changed'): self.review(identity)
        self.env['set_active_project']('b')
        with self.assertRaisesRegex(store.HTTPError, 'original project'): self.review(identity)

    def test_preview_apply_is_atomic_idempotent_and_undo_restores_original(self):
        identity = self.queued(True); before = self.env['load_project'](); review = self.review(identity)
        result = self.apply(identity, {'_context': review['context'], 'fingerprint': review['plan']['fingerprint']})
        self.assertTrue(result['ok']); self.assertEqual(self.tasks.values[identity]['record']['status'], 'applied')
        after = self.env['load_project'](); self.assertEqual(after['sequences'][0]['workflow']['analysis_task'], identity)
        count = len(self.env['read_undo_history']('a')['undo'])
        repeated = self.apply(identity, {'_context': self.current(), 'fingerprint': review['plan']['fingerprint']})
        self.assertIn('already', repeated['message']); self.assertEqual(len(self.env['read_undo_history']('a')['undo']), count)
        self.invoke('undo', {'_context': self.current()})
        self.assertEqual(self.env['load_project']()['sequences'], before['sequences'])

    def test_agent_apply_respects_proposal_mode_and_invalid_settings_before_claiming_task(self):
        identity = self.queued(True); review = self.review(identity); before = self.raw()
        for settings in ('{"agent_mode":"proposals_only"}', '{invalid', '[]'):
            (self.root / 'settings.json').write_text(settings)
            with self.assertRaises(store.HTTPError) as caught:
                self.apply(identity, {'_context': review['context'], 'fingerprint': review['plan']['fingerprint'], 'actor': 'agent'})
            self.assertEqual(caught.exception.status_code, 403); self.assertEqual(self.raw(), before)
            self.assertEqual(self.tasks.values[identity]['record']['status'], 'ready')
        (self.root / 'settings.json').write_text('{"agent_mode":"proposals_only"}')
        self.assertTrue(self.apply(identity, {'_context': review['context'], 'fingerprint': review['plan']['fingerprint'], 'actor': 'human'})['ok'])

    def test_failed_commit_preserves_project_history_and_reviewable_task(self):
        identity = self.queued(True); review = self.review(identity)
        before = self.raw(); history = copy.deepcopy(self.env['read_undo_history']('a'))
        with patch.dict(self.env, {'commit_pair': lambda *a, **k: (_ for _ in ()).throw(OSError('injected write failure'))}):
            with self.assertRaisesRegex(OSError, 'injected write failure'):
                self.apply(identity, {'_context': review['context'], 'fingerprint': review['plan']['fingerprint']})
        self.assertEqual(self.raw(), before); self.assertEqual(self.env['read_undo_history']('a'), history)
        self.assertEqual(self.tasks.values[identity]['record']['status'], 'ready')
        self.assertIn('plan', self.review(identity))

    def test_cancelled_result_is_not_reviewable_and_worker_marks_analysis_ready(self):
        identity = self.queued(True); self.tasks.cancel(identity)
        with self.assertRaisesRegex(store.HTTPError, 'not ready'): self.review(identity)
        manager = TaskManager(str(self.root), {'analysis': lambda payload, task: {'clock': 'media'}}, workers=1)
        self.addCleanup(manager.shutdown)
        record = manager.submit('analysis', 'Actual worker', self.current(), {'unique': True})
        deadline = time.monotonic() + 3
        while manager.get(record['id'])['record']['status'] not in ('ready', 'error') and time.monotonic() < deadline: time.sleep(.01)
        self.assertEqual(manager.get(record['id'])['record']['status'], 'ready')
        self.assertEqual(manager.get(record['id'])['result'], {'clock': 'media'})

    def test_stale_review_context_fingerprint_and_source_change_refuse_entire_apply(self):
        for mode in ('context', 'fingerprint', 'source'):
            identity = self.queued(True); review = self.review(identity); body = {'_context': review['context'], 'fingerprint': review['plan']['fingerprint']}
            if mode == 'context': self.edit('Unrelated-after-review', self.current())
            elif mode == 'fingerprint': body['fingerprint'] = 'changed'
            else:
                with open(self.env['load_project']()['media']['m']['path'], 'ab') as stream: stream.write(b'changed')
            before = self.raw()
            with self.assertRaises(store.HTTPError): self.apply(identity, body)
            self.assertEqual(self.raw(), before)


class InterpretationRoutes(unittest.TestCase):
    """Actual interpretation route and owned Relink with controlled probe metadata."""
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity interpretation '); self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'replacement.mov'; self.path.write_bytes(b'probe fixture')
        self.project = {'media': {'m': {'id': 'm', 'path': str(self.path), 'fps': 29.97,
            'frame_rate': '30000/1001', 'duration': 4000, 'has_video': True}}, 'sequences': []}
        self.saved = []
        async def broadcast(event): pass
        self.env = dict(Request=store.Request, HTTPException=store.HTTPError, LOCK=threading.Lock(),
            math=math, os=os, copy=copy, hashlib=__import__('hashlib'), json=json, asyncio=asyncio, uuid=uuid,
            load_project=lambda: copy.deepcopy(self.project), save_project=self.save,
            PP=lambda name: str(self.path.parent / name), log_event=lambda value: value, broadcast=broadcast,
            finish_ingest=lambda *args: None)
        names = {'media_interpret'}
        nodes = [node for node in ast.parse((ROOT / 'backend/server.py').read_text()).body if getattr(node, 'name', None) in names]
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    def save(self, value):
        self.saved.append(copy.deepcopy(value)); self.project = copy.deepcopy(value)

    def test_interpret_alias_duration_is_canonical_idempotent_and_resettable(self):
        from test_source_interpretation_workflow import StoreSourceInterpretation
        value = StoreSourceInterpretation(); value.setUp()
        self.addCleanup(value.doCleanups); self.addCleanup(value.tearDown)
        project = value.project(); project['media']['m'].update(fps=29.97, frame_rate='30000/1001', duration=4000)
        value.env['save_project'](project)
        def call(fps): return value.apply(value.review(fps=fps))['media']
        for rate in (23.976, 24000 / 1001, '24000/1001'):
            media = call(rate); self.assertEqual(media['duration'], 5000); self.assertEqual(media['native_duration'], 4000)
        self.assertEqual(call(None)['duration'], 4000)
        self.assertNotIn('interpret_fps', value.project()['media']['m'])
        before = value.raw()
        for rate in (0, False, '', 'bad', -2):
            with self.assertRaises(store.HTTPError) as caught: value.review(fps=rate)
            self.assertEqual(caught.exception.status_code, 422); self.assertEqual(value.raw(), before)

    def test_relink_duration_and_required_source_end_use_canonical_alias(self):
        from test_source_relink_workflow import StoreSourceRelink
        import source_relink_io
        value=StoreSourceRelink();value.setUp();self.addCleanup(value.tearDown)
        project=value.project();project['media']['m'].update(fps=29.97,frame_rate='30000/1001',interpret_fps=23.976,native_duration=4000,duration=5000)
        project['sequences'][0]['tracks'][0]['clips'][0]['out']=5000;value.env['save_project'](project)
        measured=source_relink_io.summarize;replacement={'fps':29.98,'frame_rate':'30000/1001','duration':3999}
        def metadata(document):return {**measured(document),**replacement}
        with patch.object(source_relink_io,'summarize',metadata):
            report=value.inspect();self.assertFalse(report['ok'])
            self.assertIn('replacement_too_short',[issue['code'] for issue in report['issues']])
            replacement['duration']=4000;report=value.inspect();self.assertTrue(report['ok'])
            result=value.apply(report)
        self.assertEqual(result['media']['duration'],5000);self.assertEqual(result['media']['native_duration'],4000)


if __name__ == '__main__': unittest.main()
