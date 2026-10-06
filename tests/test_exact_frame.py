"""Real fractional-rate frame identity and owned production frame-route checks."""
import ast
import copy
import io
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
from typing import Optional

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render as engine
from render_context import RenderContext
from frame_source import source_project
from test_project_sync import ProjectStoreFixture, HTTPError


class FramePixels(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity exact É ')
        self.addCleanup(self.temp.cleanup); self.root = Path(self.temp.name)
        self.colors = [(30 + 20*i, 80, 120) for i in range(8)]
        for i, color in enumerate(self.colors): Image.new('RGB', (64, 48), color).save(self.root / f'source_{i:02d}.png')

    def project(self, rate):
        source = self.root / 'source.mov'
        result = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-framerate', rate, '-i', str(self.root / 'source_%02d.png'),
                                 '-c:v', 'png', '-pix_fmt', 'rgb24', str(source)], capture_output=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        fps = float(engine.frame_rate(rate)); duration = 8/fps
        return {'media': {'m': {'path': str(source), 'has_video': True, 'has_audio': False, 'width': 64, 'height': 48, 'fps': fps, 'duration': duration}},
                'sequences': [{'id': 's', 'width': 64, 'height': 48, 'fps': fps, 'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1,
                'clips': [{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': duration}]}]}]}

    def frame(self, project, t):
        output = self.root / 'frame.png'
        with RenderContext(scratch_parent=str(self.root), stall_timeout=15) as owned:
            engine.render_frame(project, 's', t, str(output), context=owned)
        with Image.open(output) as picture: return picture.convert('RGB').tobytes()

    def test_every_single_frame_matches_the_input_at_integer_and_ntsc_rates(self):
        for rate in ('24', '25', '30000/1001', '24000/1001', '60000/1001'):
            project = self.project(rate); before = copy.deepcopy(project); fps = project['sequences'][0]['fps']
            source_hash = hashlib.sha256(Path(project['media']['m']['path']).read_bytes()).hexdigest()
            for index, color in enumerate(self.colors):
                with self.subTest(rate=rate, index=index):
                    self.assertEqual(self.frame(project, index/fps), bytes(color) * (64*48))
            self.assertEqual(project, before)
            self.assertEqual(hashlib.sha256(Path(project['media']['m']['path']).read_bytes()).hexdigest(), source_hash)
            self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_subframe_requests_select_the_containing_frame_including_just_before_a_cut(self):
        project = self.project('24000/1001'); fps = project['sequences'][0]['fps']
        for t, index in ((.51/fps, 0), (.99/fps, 0), (1.001/fps, 1), (6.999/fps, 6)):
            with self.subTest(t=t): self.assertEqual(self.frame(project, t), bytes(self.colors[index]) * (64*48))

    def test_invalid_positions_preserve_existing_output_and_leave_no_scratch(self):
        project = self.project('30000/1001'); output = self.root / 'frame.png'; output.write_bytes(b'previous')
        for t in (-1, float('nan'), float('inf'), 8/(30000/1001), 1):
            with self.assertRaises(ValueError): engine.render_frame(project, 's', t, str(output))
            self.assertEqual(output.read_bytes(), b'previous')
        self.assertFalse(list(self.root.glob('*.part.png')))

    def test_cancelled_single_frame_preserves_existing_output(self):
        project = self.project('25'); output = self.root / 'frame.png'; output.write_bytes(b'previous')
        with RenderContext(scratch_parent=str(self.root)) as owned:
            owned.holder['cancelled'] = True
            with self.assertRaisesRegex(RuntimeError, 'cancelled'): engine.render_frame(project, 's', 0, str(output), context=owned)
        self.assertEqual(output.read_bytes(), b'previous')

    def test_source_frame_uses_native_footage_without_interpretation_or_timeline_effects(self):
        project = self.project('60000/1001'); fps = 60000/1001; before = copy.deepcopy(project)
        project['media']['m'].update(interpret_fps=25, native_duration=8/fps, duration=8/25)
        project['sequences'][0]['tracks'][0]['clips'][0]['color'] = {'exposure': 3}
        source = source_project(project, 'm')
        self.assertNotIn('interpret_fps', source['media']['m'])
        self.assertEqual(source['sequences'][0]['fps'], fps)
        source['sequences'][0]['id'] = 's'
        self.assertEqual(self.frame(source, 5/fps), bytes(self.colors[5]) * (64*48))
        self.assertEqual(project['media']['m']['interpret_fps'], 25)
        self.assertEqual(project['media']['m']['path'], before['media']['m']['path'])

    def test_source_subclips_resolve_to_the_loaded_original_file_and_cycles_fail(self):
        project = self.project('25'); project['media']['sub'] = {**project['media']['m'], 'subclip_of': 'm', 'sub_in': .1, 'duration': .1}
        source = source_project(project, 'sub')
        self.assertEqual(set(source['media']), {'m'})
        self.assertEqual(source['sequences'][0]['tracks'][0]['clips'][0]['in_'], 0)
        project['media']['m']['subclip_of'] = 'sub'
        with self.assertRaisesRegex(ValueError, 'cycle'): source_project(project, 'sub')
        with self.assertRaisesRegex(ValueError, 'video picture'): source_project(project, 'missing')


class ImageResponse:
    def __init__(self, content, **kwargs): self.body, self.options = content, kwargs


class FrameRoute(ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        self.source = self.root / 'source.png'; Image.new('RGB', (64, 48), (70, 80, 90)).save(self.source)
        self.doc.update(media={'m': {'path': str(self.source), 'has_video': True, 'is_image': True, 'has_audio': False,
            'duration': 1, 'width': 64, 'height': 48}}, sequences=[{'id': 's', 'name': 'Frame', 'fps': 29.97, 'width': 64, 'height': 48,
            'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 1}]}]}])
        self.env['save_project'](self.doc)
        self.slots = threading.BoundedSemaphore(2)
        self.env.update(FRAME_SLOTS=self.slots, Optional=Optional, math=math, Response=ImageResponse, seq_total=engine.seq_total,
            render_frame=engine.render_frame, RenderContext=lambda **kw: RenderContext(scratch_parent=str(self.root), **kw))
        tree = ast.parse((ROOT / 'backend/server.py').read_text())
        node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'frame'); node.decorator_list = []
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    def request(self, **kwargs):
        return self.env['frame']('s', kwargs.pop('t', 1001/30000), context=kwargs.pop('context', json.dumps(self.current())), **kwargs)

    def assert_retired(self):
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
        self.assertFalse(list((self.root / 'renders').glob('frame_*.png')))
        self.assertTrue(self.slots.acquire(False)); self.assertTrue(self.slots.acquire(False)); self.slots.release(); self.slots.release()

    def test_real_response_reports_frame_identity_and_retires_output_after_copying(self):
        response = self.request(); headers = response.options['headers']
        self.assertEqual(headers['Cache-Control'], 'no-store'); self.assertEqual(headers['X-Filmocity-Frame'], '1')
        self.assertEqual(float(headers['X-Filmocity-Time']), 1001/30000)
        with Image.open(io.BytesIO(response.body)) as picture: self.assertEqual(picture.convert('RGB').getpixel((32, 24)), (70, 80, 90))
        self.assert_retired()

    def test_legacy_request_without_context_is_still_captured_and_revalidated(self):
        response = self.request(context=None); self.assertTrue(response.body.startswith(b'\x89PNG')); self.assert_retired()

    def test_source_frame_request_does_not_export_the_program_monitor(self):
        self.doc['sequences'][0]['tracks'][0]['clips'][0]['color'] = {'exposure': 3}
        self.env['save_project'](self.doc)
        response = self.request(media='m')
        with Image.open(io.BytesIO(response.body)) as picture: self.assertEqual(picture.convert('RGB').getpixel((32, 24)), (70, 80, 90))
        self.assert_retired()

    def test_invalid_time_context_and_sequence_never_call_the_renderer(self):
        with patch.dict(self.env, render_frame=lambda *a, **k: self.fail('Renderer must not start')):
            for kwargs, code in [({'t': -1}, 422), ({'t': float('nan')}, 422), ({'t': float('inf')}, 422), ({'t': 1}, 422),
                                 ({'context': 'broken'}, 400), ({'context': '{}'}, 409)]:
                with self.subTest(kwargs=kwargs), self.assertRaises(HTTPError) as error: self.request(**kwargs)
                self.assertEqual(error.exception.status_code, code)
            with self.assertRaises(HTTPError) as error: self.env['frame']('missing', 0)
            self.assertEqual(error.exception.status_code, 404)
        self.assert_retired()

    def test_capacity_rejection_does_not_consume_another_slot(self):
        self.slots.acquire(); self.slots.acquire()
        try:
            with self.assertRaises(HTTPError) as error: self.request()
            self.assertEqual(error.exception.status_code, 429)
        finally: self.slots.release(); self.slots.release()
        self.assert_retired()

    def test_changed_project_after_render_rejects_the_png_and_cleans_up(self):
        def render(*args, **kwargs):
            engine.render_frame(*args, **kwargs); self.env['set_active_project']('b')
        with patch.dict(self.env, render_frame=render), self.assertRaises(HTTPError) as error: self.request()
        self.assertEqual(error.exception.status_code, 409); self.assert_retired()

    def test_changed_saved_edit_after_render_rejects_the_png(self):
        def render(*args, **kwargs):
            engine.render_frame(*args, **kwargs); changed=self.env['load_project'](); changed['name']='Edited'; self.env['save_project'](changed)
        with patch.dict(self.env, render_frame=render), self.assertRaises(HTTPError) as error: self.request()
        self.assertEqual(error.exception.status_code, 409); self.assert_retired()

    def test_changed_source_after_render_rejects_the_png(self):
        def render(*args, **kwargs):
            engine.render_frame(*args, **kwargs); Image.new('RGB', (64, 48), (255, 0, 0)).save(self.source)
        with patch.dict(self.env, render_frame=render), self.assertRaises(HTTPError) as error: self.request()
        self.assertEqual(error.exception.status_code, 409); self.assert_retired()

    def test_encoder_failure_retires_partials_and_releases_capacity(self):
        def render(project, seq, t, output, **kwargs): Path(output).write_bytes(b'partial'); raise RuntimeError('Encoder failed')
        with patch.dict(self.env, render_frame=render), self.assertRaises(HTTPError) as error: self.request()
        self.assertEqual(error.exception.status_code, 422); self.assertIn('Encoder failed', str(error.exception)); self.assert_retired()

    def test_processing_slot_deadline_returns_retryable_busy_and_releases_frame_request(self):
        from work_budget import WorkBusy
        with patch('work_budget.work',side_effect=WorkBusy('Processing slots are busy')), self.assertRaises(HTTPError) as error:
            self.request()
        self.assertEqual(error.exception.status_code,429);self.assertIn('Processing slots',str(error.exception));self.assert_retired()


if __name__ == '__main__': unittest.main()
