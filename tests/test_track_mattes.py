"""Real rendered matte pixels, timing, hidden dependencies and preflight failures."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render as engine
from preflight import inspect_resources, ResourceError
from render_context import RenderContext


def matte(track='V2', kind='alpha', invert=False):
    return {'type': 'track_matte', 'params': {'track': track, 'type': kind, 'invert': invert}}


class TrackMattes(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="filmocity-matte-é's-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.sources = {}
        self.project = {'id': 'matte', 'media': {}, 'sequences': [{'id': 's', 'name': 'Matte',
            'width': 64, 'height': 64, 'fps': 24, 'duration': 1, 'tracks': [], 'captions': []}]}
        self.seq = self.project['sequences'][0]
        self.source('red', Image.new('RGBA', (64, 64), (240, 0, 0, 255)))
        self.clip = {'id': 'consumer', 'media_id': 'red', 'in_': 0, 'out': 1, 'start': 0, 'fx_stack': [matte()]}
        self.seq['tracks'] = [self.track('V1', [self.clip]), self.track('V2', [], muted=True)]
        self.mattes = self.seq['tracks'][1]['clips']

    def track(self, tid, clips, **kwargs):
        return {'id': tid, 'index': int(tid[1:]), 'kind': 'video', 'clips': clips, **kwargs}

    def source(self, name, image):
        path = self.root / (name + '.png')
        image.save(path)
        self.sources[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.project['media'][name] = {'id': name, 'path': str(path), 'has_video': True, 'has_audio': False,
            'is_image': True, 'width': 64, 'height': 64, 'duration': 1}
        return path

    def box(self, x=0, w=.5, **kwargs):
        return {'id': 'box' + str(len(self.mattes)), 'start': 0, 'in_': 0, 'out': 1,
            'graphic': {'layers': [{'kind': 'box', 'x': x, 'y': 0, 'w': w, 'h': 1, 'color': 'white'}]}, **kwargs}

    def half_alpha(self):
        image = Image.new('RGBA', (64, 64), (255, 255, 255, 0))
        image.paste((255, 255, 255, 255), (0, 0, 32, 64))
        self.source('mask', image)
        self.mattes.append({'id': 'mask', 'media_id': 'mask', 'in_': 0, 'out': 1, 'start': 0})

    def frame(self, t=.25, mode='rgb'):
        before = copy.deepcopy(self.project)
        output = self.root / 'frame.png'
        with RenderContext(scratch_parent=str(self.root), stall_timeout=15) as context:
            engine.render_frame(self.project, 's', t, str(output), context=context, color_processing=mode)
        self.assertEqual(self.project, before)
        with Image.open(output) as image:
            return image.convert('RGB')

    def assert_pixel(self, image, x, expected, y=32, tolerance=2):
        actual = image.getpixel((x, y))
        self.assertLessEqual(max(abs(a-b) for a, b in zip(actual, expected)), tolerance, (x, y, actual, expected))

    def tearDown(self):
        for path, digest in self.sources.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
        self.assertFalse(list(self.root.glob('*.part.*')))

    def test_hidden_image_alpha_matte_and_inversion(self):
        self.half_alpha()
        for inverse in (False, True):
            with self.subTest(inverse=inverse):
                self.clip['fx_stack'] = [matte(invert=inverse)]
                image = self.frame()
                self.assert_pixel(image, 16, (0, 0, 0) if inverse else (240, 0, 0))
                self.assert_pixel(image, 48, (240, 0, 0) if inverse else (0, 0, 0))

    def test_layered_graphics_matte_uses_all_layers(self):
        box = self.box(w=.25)
        box['graphic']['layers'].append({'kind': 'box', 'x': .75, 'y': 0, 'w': .25, 'h': 1, 'color': 'white'})
        self.mattes.append(box)
        image = self.frame()
        for x, value in [(8, 240), (32, 0), (56, 240)]: self.assert_pixel(image, x, (value, 0, 0))

    def test_multiple_matte_clips_use_timeline_positions_and_leave_gaps(self):
        self.mattes.extend([self.box(out=.25, start=.25), self.box(x=.5, out=.25, start=.75)])
        for t, left, right in [(0, 0, 0), (.25, 240, 0), (.5, 0, 0), (.625, 0, 0), (.75, 0, 240)]:
            with self.subTest(t=t):
                image = self.frame(t)
                self.assert_pixel(image, 16, (left, 0, 0)); self.assert_pixel(image, 48, (right, 0, 0))

    def test_luma_accounts_for_transparency_and_inversion(self):
        image = Image.new('RGBA', (64, 64), (255, 255, 255, 0))
        image.paste((128, 128, 128, 255), (0, 0, 32, 64))
        self.source('gray', image)
        self.mattes.append({'id': 'gray', 'media_id': 'gray', 'start': 0, 'in_': 0, 'out': 1})
        for inverse in (False, True):
            self.clip['fx_stack'] = [matte(kind='luma', invert=inverse)]
            result = self.frame()
            self.assert_pixel(result, 16, (120, 0, 0)); self.assert_pixel(result, 48, (240 if inverse else 0, 0, 0))

    def test_matte_multiplies_existing_source_alpha_and_opacity(self):
        self.source('red', Image.new('RGBA', (64, 64), (240, 0, 0, 128)))
        self.clip['transform'] = {'opacity': .5}
        self.mattes.append(self.box(w=1, transform={'opacity': .5}))
        self.assert_pixel(self.frame(), 32, (30, 0, 0))

    def test_consumer_and_matte_transforms_share_sequence_coordinates(self):
        self.clip['transform'] = {'scale': .5, 'x': 16}
        self.half_alpha()
        self.mattes[0]['transform'] = {'x': 32}
        image = self.frame()
        for x, y, value in [(8, 32, 0), (40, 32, 240), (40, 8, 0)]:
            self.assert_pixel(image, x, (value, 0, 0), y=y)

    def test_matte_keyframes_and_opacity_are_evaluated_in_clip_time(self):
        self.mattes.append(self.box(start=.25, out=.5, keyframes={'transform.opacity': [
            {'t': 0, 'v': 0}, {'t': .5, 'v': 1}]}))
        self.assert_pixel(self.frame(.25), 16, (0, 0, 0))
        self.assert_pixel(self.frame(.5), 16, (120, 0, 0), tolerance=3)

    def test_disabled_matte_clips_are_ignored_and_empty_inversion_is_opaque(self):
        self.mattes.append(self.box(w=1, enabled=False))
        self.assert_pixel(self.frame(), 32, (0, 0, 0))
        self.clip['fx_stack'] = [matte(invert=True)]
        self.assert_pixel(self.frame(), 32, (240, 0, 0))

    def test_visible_matte_track_still_composites_its_own_output(self):
        self.half_alpha(); self.seq['tracks'][1]['muted'] = False
        self.assert_pixel(self.frame(), 16, (255, 255, 255))

    def test_graphic_consumer_and_title_matte(self):
        self.clip.pop('media_id')
        self.clip['graphic'] = {'layers': [{'kind': 'box', 'color': 'red', 'x': 0, 'y': 0, 'w': 1, 'h': 1}]}
        self.mattes.append({'id': 'title', 'start': 0, 'in_': 0, 'out': 1,
                            'title': {'text': 'M', 'size': 40, 'x': .5, 'y': .5, 'color': 'white'}})
        data = self.frame().tobytes()
        self.assertGreater(max(data[::3]), 200)
        self.assertEqual(max(data[1::3]), 0)
        self.assertGreater(data[::3].count(0), 1000)

    def test_chained_mattes_reuse_the_shared_stream(self):
        self.half_alpha()
        self.mattes[0]['fx_stack'] = [matte('V3')]
        top = self.box(w=1)
        top['graphic']['layers'][0].update(y=.5, h=.5)
        self.seq['tracks'].append(self.track('V3', [top], muted=True))
        second = copy.deepcopy(self.clip); second.update(id='second', start=.5, out=.5)
        self.clip['out'] = .5; self.seq['tracks'][0]['clips'].append(second)
        for t in (.25, .75):
            image = self.frame(t)
            self.assert_pixel(image, 16, (240, 0, 0), y=48)
            self.assert_pixel(image, 16, (0, 0, 0), y=16)
            self.assert_pixel(image, 48, (0, 0, 0), y=48)
        with engine.build_command(self.project, 's', str(self.root / 'unused.png'), video_only=True)[0] as cmd:
            graph = cmd[cmd.index('-filter_complex') + 1]
            self.assertEqual(graph.count('drawbox='), 1)

    def test_nested_graphics_retain_alpha_in_a_hidden_matte_track(self):
        self.project['sequences'].append({'id': 'nested', 'name': 'Nested matte', 'width': 64, 'height': 64,
            'fps': 24, 'duration': 1, 'captions': [], 'tracks': [self.track('V1', [self.box()])]})
        self.mattes.append({'id': 'nested-clip', 'sequence_id': 'nested', 'start': 0, 'in_': 0, 'out': 1})
        for mode in ('rgb', 'legacy'):
            image = self.frame(mode=mode)
            self.assert_pixel(image, 16, (240, 0, 0), tolerance=5)
            self.assert_pixel(image, 48, (0, 0, 0), tolerance=5)

    def test_video_matte_trim_speed_reverse_hold_and_muted_audio(self):
        path = self.root / 'moving matte.mkv'
        pixels = bytes([255] * (64 * 64 * 3)) * 12 + bytes(64 * 64 * 3) * 12
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'rawvideo', '-pixel_format', 'rgb24',
            '-video_size', '64x64', '-framerate', '24', '-i', 'pipe:0', '-f', 'lavfi', '-i',
            'sine=frequency=440:sample_rate=48000:duration=1', '-c:v', 'ffv1', '-pix_fmt', 'bgr0',
            '-c:a', 'pcm_s16le', '-t', '1', str(path)], input=pixels, check=True, capture_output=True, timeout=20)
        self.sources[path] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.project['media']['moving'] = {'id': 'moving', 'path': str(path), 'has_video': True,
            'has_audio': True, 'duration': 1, 'width': 64, 'height': 64, 'fps': 24}
        clip = {'id': 'moving', 'media_id': 'moving', 'start': 0, 'in_': .25, 'out': .75, 'speed': .5}
        self.mattes.append(clip); self.clip['fx_stack'] = [matte(kind='luma')]
        for reverse in (False, True):
            clip['reverse'] = reverse
            self.assert_pixel(self.frame(.125), 32, (0 if reverse else 240, 0, 0))
            self.assert_pixel(self.frame(.75), 32, (240 if reverse else 0, 0, 0))
        clip.update(hold=True, in_=.75, out=1.75, speed=1)
        self.assert_pixel(self.frame(.75), 32, (0, 0, 0))
        clip.update(hold=False, reverse=False, in_=0, out=1, speed=1)
        output = self.root / 'silent.wav'
        engine.render(self.project, 's', str(output), {'format': 'audio', 'acodec': 'wav'})
        audio = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(output), '-f', 's16le', '-'],
            check=True, capture_output=True, timeout=20).stdout
        self.assertGreater(len(audio), 48000)
        self.assertFalse(any(audio), 'Hidden matte audio must not enter the main mix')

    def test_matte_curves_and_overlapping_layers_affect_luma(self):
        self.source('white', Image.new('RGBA', (64, 64), 'white'))
        self.mattes.append({'id': 'graded', 'media_id': 'white', 'start': 0, 'in_': 0, 'out': 1,
            'color': {'curves': [[0, 0], [1, .5]]}})
        self.mattes.append(self.box(x=.5, transform={'opacity': .5}))
        self.clip['fx_stack'] = [matte(kind='luma')]
        image = self.frame()
        self.assert_pixel(image, 16, (120, 0, 0)); self.assert_pixel(image, 48, (180, 0, 0))

    def test_invalid_matte_kind_and_audio_track_fail_preflight(self):
        self.clip['fx_stack'] = [matte(kind='unknown')]
        self.assertIn('invalid_matte_type', [i['code'] for i in inspect_resources(self.project, 's')['issues']])
        self.clip['fx_stack'] = [matte()]; self.seq['tracks'][1]['kind'] = 'audio'
        self.assertIn('invalid_matte_track', [i['code'] for i in inspect_resources(self.project, 's')['issues']])

    def test_cancellation_during_nested_matte_render_preserves_previous_frame(self):
        self.project['sequences'].append({'id': 'nested', 'name': 'Nested matte', 'width': 64, 'height': 64,
            'fps': 24, 'duration': 1, 'captions': [], 'tracks': [self.track('V1', [self.box()])]})
        self.mattes.append({'id': 'nested-clip', 'sequence_id': 'nested', 'start': 0, 'in_': 0, 'out': 1})
        output = self.root / 'previous.png'; output.write_bytes(b'previous published frame')
        holder = {}; progress = []
        def cancel(fraction):
            progress.append(fraction); holder['cancelled'] = True
        before = copy.deepcopy(self.project)
        with RenderContext(scratch_parent=str(self.root), proc_holder=holder, progress=cancel, stall_timeout=15) as context:
            with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                engine.render_frame(self.project, 's', .25, str(output), context=context)
            self.assertFalse(context.nested_completed)
        self.assertTrue(progress)
        self.assertEqual(output.read_bytes(), b'previous published frame')
        self.assertEqual(before, self.project)

    def test_missing_sources_and_cycles_fail_preflight_before_render(self):
        cases = [('missing', 'missing_matte_track'), ('V1', 'matte_cycle'), (['V2'], 'missing_matte_track')]
        for target, code in cases:
            with self.subTest(target=target):
                self.clip['fx_stack'] = [matte(target)]
                report = inspect_resources(self.project, 's')
                self.assertIn(code, [i['code'] for i in report['issues']])
                with self.assertRaises(ResourceError): engine.build_command(self.project, 's', str(self.root / 'out.mp4'))
        self.clip['fx_stack'] = [matte()]
        self.mattes.append(self.box(fx_stack=[matte('V1')]))
        self.assertIn('matte_cycle', [i['code'] for i in inspect_resources(self.project, 's')['issues']])

    def test_hidden_dependencies_are_preflighted_but_unreferenced_muted_tracks_are_not(self):
        self.half_alpha()
        self.project['media']['mask']['path'] = str(self.root / 'missing.png')
        self.assertIn('missing_source', [i['code'] for i in inspect_resources(self.project, 's')['issues']])
        self.clip['fx_stack'][0]['enabled'] = False
        self.assertTrue(inspect_resources(self.project, 's')['ok'])

    def test_cache_changes_when_hidden_graphics_source_bytes_change(self):
        path = self.source('graphic', Image.new('RGBA', (64, 64), 'white'))
        self.mattes.append({'id': 'image-graphic', 'start': 0, 'in_': 0, 'out': 1,
            'graphic': {'layers': [{'kind': 'image', 'path': str(path), 'w': 1, 'h': 1, 'x': 0, 'y': 0}]}})
        before = engine.chunk_key(self.project, self.seq, {})
        self.source('graphic', Image.new('RGBA', (64, 64), 'black'))
        self.assertNotEqual(before, engine.chunk_key(self.project, self.seq, {}))

    def test_adjustment_layer_without_a_matte_keeps_its_existing_graph(self):
        self.clip.pop('fx_stack'); self.mattes.clear()
        self.seq['tracks'].append(self.track('V3', [{'id': 'adjust', 'adjustment': True, 'start': 0, 'in_': 0,
            'out': 1, 'color': {'curves_r': [[0, 0], [1, .5]]}}]))
        self.assert_pixel(self.frame(), 32, (120, 0, 0))


if __name__ == '__main__':
    unittest.main(verbosity=2)
