"""Real RGB composition, compatibility, nested intermediates and encoded output."""
import array
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import wave

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render as engine
from render_color import ColorPipeline
from render_context import RenderContext
from preflight import inspect_resources


def legacy_cases(project):
    plain = copy.deepcopy(project)
    grade = copy.deepcopy(project)
    grade['sequences'][0]['tracks'][0]['clips'][0].update({
        'color': {'exposure': .2, 'contrast': .1, 'saturation': -.2, 'temperature': .3, 'tint': -.2,
                  'wheels': {'midtones': {'r': .5}}, 'curves': [[0, 0], [1, .8]]},
        'transform': {'opacity': .7, 'scale': .8, 'rotation': 15, 'x': 4, 'y': -2}, 'fit': 'cover'})
    blend = copy.deepcopy(project)
    blend['sequences'][0]['tracks'].append({'id': 'V2', 'kind': 'video', 'index': 2,
        'clips': [{'id': 'top', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': .25, 'blend': 'screen'}]})
    return {'plain': plain, 'grade': grade, 'blend': blend}


class RenderColor(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="filmocity-rgb-é's-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.image = Image.new('RGB', (64, 64))
        self.image.putdata([((x * 37) % 256, (y * 71) % 256, ((x+y) * 109) % 256) for y in range(64) for x in range(64)])
        self.source = self.root / 'fine color detail.png'
        self.image.save(self.source)
        self.project = {'id': 'color', 'media': {'m': {'id': 'm', 'name': 'Color', 'path': str(self.source),
            'width': 64, 'height': 64, 'has_video': True, 'has_audio': False, 'is_image': True, 'duration': .25}},
            'sequences': [{'id': 's', 'name': 'Color', 'width': 64, 'height': 64, 'fps': 24, 'captions': [], 'tracks': [
                {'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': .25}]}]}]}
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def tearDown(self):
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(), self.source_hash)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
        self.assertFalse(list(self.root.glob('*.part.*')))

    @property
    def clip(self): return self.project['sequences'][0]['tracks'][0]['clips'][0]

    def frame(self, project=None, mode='rgb', t=.1):
        project = self.project if project is None else project
        before = copy.deepcopy(project)
        output = self.root / 'frame.png'
        with RenderContext(scratch_parent=str(self.root), stall_timeout=15) as context:
            engine.render_frame(project, 's', t, str(output), color_processing=mode, context=context)
        self.assertEqual(project, before)
        with Image.open(output) as result: return result.convert('RGB')

    def solid(self, rgb):
        self.image = Image.new('RGB', (64, 64), rgb)
        self.image.save(self.source)
        self.source_hash = hashlib.sha256(self.source.read_bytes()).hexdigest()

    def overlay(self, rgb, **properties):
        path = self.root / 'overlay.png'
        Image.new('RGB', (64, 64), rgb).save(path)
        self.project['media']['top'] = {**self.project['media']['m'], 'id': 'top', 'path': str(path)}
        clip = {'id': 'top', 'media_id': 'top', 'start': 0, 'in_': 0, 'out': .25, **properties}
        self.project['sequences'][0]['tracks'].append({'id': 'V2', 'kind': 'video', 'index': 2, 'clips': [clip]})
        return clip

    def near(self, actual, expected, tolerance=1):
        self.assertLessEqual(max(abs(a-b) for a, b in zip(actual, expected)), tolerance, (actual, expected))

    def test_full_color_is_default_and_invalid_modes_fail_preflight(self):
        self.assertTrue(ColorPipeline.from_preset({}).rgb)
        for mode in ['typo', None, 1]:
            with self.subTest(mode=mode):
                report = inspect_resources(self.project, 's', {'color_processing': mode})
                self.assertFalse(report['ok'])
                self.assertIn('color_processing', [i['code'] for i in report['issues']])
                with self.assertRaises(ValueError):
                    engine.build_command(self.project, 's', str(self.root / 'bad.png'), {'color_processing': mode})

    def test_legacy_color_graphs_match_except_duration_rates_and_timestamp_precision(self):
        records = json.loads((ROOT / 'tests/fixtures/legacy-color-graphs.json').read_text())
        for name, project in legacy_cases(self.project).items():
            with self.subTest(name=name), RenderContext(scratch_parent=str(self.root)) as context:
                _, graph = engine.build_command(project, 's', str(self.root / 'legacy.png'),
                                                 {'color_processing': 'legacy'}, video_only=True, context=context)
                # Retain the historical graph evidence. Only its known one-second
                # background duration floor changed; the fixture's content is 0.25s.
                # A real workflow WAV regression independently checks short output.
                expected = records['graphs'][name].replace(':d=1.000', ':d=0.250')
                # The shared rate formatter now writes exact integer/rational rates.
                expected = expected.replace(':r=24.0', ':r=24').replace(',fps=24.0', ',fps=24')
                # Final placement now uses a microsecond clock; at zero the
                # operation is still the identity. Pixel checks remain separate.
                expected = expected.replace('setpts=PTS+0.0000/TB', 'settb=AVTB,setpts=PTS+0.000000000000/TB')
                self.assertEqual(graph, expected)

    def test_png_preserves_every_pixel_of_fine_color_detail(self):
        self.assertEqual(self.frame().tobytes(), self.image.tobytes())
        self.assertNotEqual(self.frame(mode='legacy').tobytes(), self.image.tobytes())

    def test_png_sequence_preserves_pixels_in_every_frame_and_manifest(self):
        # An explicit duration requests a sub-second sequence; the historical
        # implicit sequence length has a one-second minimum.
        self.project['sequences'][0]['duration'] = .25
        before = copy.deepcopy(self.project)
        with RenderContext(scratch_parent=str(self.root)) as context:
            output = Path(engine.render(self.project, 's', str(self.root / 'sequence.png'), {'format': 'png_sequence'}, context=context))
        manifest = json.loads((output / 'sequence.json').read_text())
        self.assertEqual(manifest['frame_count'], 6)
        for frame in manifest['frames']:
            path = output / frame['file']
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), frame['sha256'])
            with Image.open(path) as image: self.assertEqual(image.convert('RGB').tobytes(), self.image.tobytes())
        self.assertEqual(self.project, before)

    def test_ffv1_retains_rgb_pixels_in_the_encoded_video(self):
        output = self.root / 'lossless.mkv'
        with RenderContext(scratch_parent=str(self.root)) as context:
            engine.render(self.project, 's', str(output), {'vcodec': 'ffv1'}, context=context)
        stream = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_streams', '-of', 'json', str(output)]))['streams'][0]
        self.assertEqual(stream['pix_fmt'], 'bgr0')
        decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(output), '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
        self.assertEqual(decoded, self.image.tobytes())

    def test_delivery_encoder_still_receives_yuv420_and_prores_422(self):
        for preset, expected in [({}, 'yuv420p'), ({'format': 'hevc'}, 'yuv420p'), ({'format': 'prores'}, 'yuv422p10le')]:
            with self.subTest(preset=preset), RenderContext(scratch_parent=str(self.root)) as context:
                command, graph = engine.build_command(self.project, 's', str(self.root / 'delivery.mov'), preset, context=context)
                self.assertIn('format=rgb24[vdeliveryrgb]', graph)
                self.assertEqual(command[command.index('-pix_fmt')+1], expected)
        output = self.root / 'delivery.mp4'
        with RenderContext(scratch_parent=str(self.root)) as context:
            engine.render(self.project, 's', str(output), {'vcodec': 'mpeg4'}, context=context)
        stream = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_streams', '-of', 'json', str(output)]))['streams'][0]
        self.assertEqual(stream['pix_fmt'], 'yuv420p')

    def test_opacity_composites_rgb_channels(self):
        self.solid((100, 20, 70))
        self.overlay((40, 180, 90), transform={'opacity': .5})
        self.near(self.frame().getpixel((32, 32)), (70, 100, 80))

    def test_source_alpha_survives_wheel_grading_and_keying_removes_green(self):
        self.solid((10, 20, 200))
        clip = self.overlay((100, 150, 200), color={'wheels': {'highlights': {'g': -.25}}})
        source = Path(self.project['media']['top']['path'])
        Image.new('RGBA', (64, 64), (100, 150, 200, 128)).save(source)
        self.near(self.frame().getpixel((32, 32)), (55, 63, 200))
        Image.new('RGB', (64, 64), (0, 255, 0)).save(source)
        clip.pop('color'); clip['effects'] = {'chromakey': {'enabled': True, 'color': '#00ff00', 'similarity': .2, 'blend': .05}}
        self.near(self.frame().getpixel((32, 32)), (10, 20, 200))

    def test_identity_lut_with_a_quoted_path_keeps_the_wheel_grade(self):
        self.solid((128, 128, 128))
        lut = self.root / "Émile's look.cube"
        lut.write_text('LUT_3D_SIZE 2\n' + '\n'.join(f'{r} {g} {b}' for b in (0, 1) for g in (0, 1) for r in (0, 1)) + '\n')
        self.clip['color'] = {'wheels': {'highlights': {'b': -.2}}, 'lut': str(lut)}
        self.near(self.frame().getpixel((32, 32)), (128, 128, 92))

    def test_rgb_blend_modes(self):
        bottom, top = (100, 20, 70), (40, 180, 90)
        self.solid(bottom)
        clip = self.overlay(top)
        for mode in ['multiply', 'screen', 'difference']:
            with self.subTest(mode=mode):
                clip['blend'] = mode
                expected = [round(a*b/255) if mode == 'multiply' else round(255-(255-a)*(255-b)/255) if mode == 'screen' else abs(a-b) for a, b in zip(bottom, top)]
                self.near(self.frame().getpixel((32, 32)), expected, 2)

    def test_mask_and_transform_keep_opaque_and_transparent_regions(self):
        self.solid((10, 20, 200))
        clip = self.overlay((200, 30, 40), mask={'type': 'rect', 'x': .25, 'y': .25, 'w': .5, 'h': .5})
        image = self.frame()
        self.near(image.getpixel((32, 32)), (200, 30, 40))
        self.near(image.getpixel((2, 2)), (10, 20, 200))
        clip.pop('mask'); clip['transform'] = {'scale': .5, 'rotation': 15}
        image = self.frame()
        self.near(image.getpixel((32, 32)), (200, 30, 40))
        self.near(image.getpixel((2, 2)), (10, 20, 200))

    def test_transitions_remain_colored_during_the_fade(self):
        self.solid((20, 80, 140))
        self.overlay((200, 40, 60), transition_in={'type': 'dissolve', 'duration': .25})
        pixel = self.frame(t=3/24).getpixel((32, 32))
        self.near(pixel, (110, 60, 100), 2)

    def test_graphic_layers_and_captions_render_with_quoted_scratch_paths(self):
        self.solid((10, 20, 30))
        graphic = {'id': 'g', 'start': 0, 'in_': 0, 'out': .25, 'graphic': {'layers': [
            {'kind': 'box', 'x': .25, 'y': .25, 'w': .5, 'h': .5, 'color': '#c81e28'}]}}
        self.project['sequences'][0]['tracks'].append({'id': 'V2', 'kind': 'video', 'index': 2, 'clips': [graphic]})
        image = self.frame()
        self.near(image.getpixel((32, 32)), (200, 30, 40))
        self.project['sequences'][0]['captions'] = [{'start': 0, 'end': .25, 'text': 'Color'}]
        self.project['sequences'][0]['caption_style'] = {'size': 14, 'y': .6, 'borderw': 0}
        image = self.frame()
        self.assertTrue(any(min(pixel) > 180 for pixel in image.getdata()))

    def test_watermark_preserves_color_in_a_real_png_sequence(self):
        self.solid((10, 20, 30))
        self.project['sequences'][0]['duration'] = .25
        logo = self.root / "logo ' color.png"
        Image.new('RGB', (16, 16), (240, 40, 160)).save(logo)
        preset = {'format': 'png_sequence', 'watermark': {'path': str(logo), 'position': 'center', 'scale': .25, 'opacity': 1}}
        with RenderContext(scratch_parent=str(self.root)) as context:
            output = Path(engine.render(self.project, 's', str(self.root / 'watermark.png'), preset, context=context))
        manifest = json.loads((output / 'sequence.json').read_text())
        with Image.open(output / manifest['frames'][0]['file']) as image:
            self.near(image.convert('RGB').getpixel((32, 32)), (240, 40, 160))

    def test_filter_paths_survive_quotes_colons_and_graph_delimiters(self):
        # Actual FFmpeg parsing, including a colon that exercises the same
        # option boundary as a Windows drive prefix. Native Windows is separate.
        path = self.root / "C: Émile's [text],semi;colon.txt"
        path.write_text('Color')
        graph = f"color=s=64x64:d=0.1,drawtext=textfile='{engine.ffpath(path)}':fontsize=14"
        subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', graph, '-frames:v', '1', '-f', 'null', '-'],
                       capture_output=True, check=True, timeout=15)

    def test_nested_rgb_intermediate_preserves_fine_detail_and_pcm_audio(self):
        samples = array.array('h', (round(5000*math.sin(2*math.pi*440*i/48000)) for i in range(12000) for channel in range(2)))
        if sys.byteorder != 'little': samples.byteswap()
        audio = self.root / 'tone.wav'
        with wave.open(str(audio), 'wb') as writer:
            writer.setparams((2, 2, 48000, 0, 'NONE', 'not compressed')); writer.writeframes(samples.tobytes())
        self.project['media']['a'] = {'id': 'a', 'path': str(audio), 'has_audio': True, 'duration': .25}
        child = copy.deepcopy(self.project['sequences'][0]); child['id'] = 'child'; child['duration'] = .25
        child['tracks'].append({'id': 'A1', 'kind': 'audio', 'index': 1, 'clips': [{'id': 'a', 'media_id': 'a', 'start': 0, 'in_': 0, 'out': .25}]})
        self.project['sequences'].append(child)
        self.project['sequences'][0]['duration'] = .25
        self.project['sequences'][0]['tracks'][0]['clips'] = [{'id': 'nested', 'sequence_id': 'child', 'start': 0, 'in_': 0, 'out': .25}]
        before = copy.deepcopy(self.project)
        with RenderContext(scratch_parent=str(self.root)) as context:
            first = engine.prerender_nested(self.project, self.project['sequences'][0], {}, 'ffmpeg', context=context)
            second = engine.prerender_nested(self.project, self.project['sequences'][0], {}, 'ffmpeg', context=context)
            nested_id = first['sequences'][0]['tracks'][0]['clips'][0]['media_id']
            file = first['media'][nested_id]['path']
            second_id = second['sequences'][0]['tracks'][0]['clips'][0]['media_id']
            self.assertEqual(file, second['media'][second_id]['path'])
            info = json.loads(subprocess.check_output(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', file]))['streams']
            self.assertEqual({s['codec_type']: s['codec_name'] for s in info}, {'video': 'ffv1', 'audio': 'pcm_f32le'})
            decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', file, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', 'pipe:1'])
            self.assertEqual(decoded, self.image.tobytes())
            sound = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', file, '-vn', '-f', 's16le', '-ac', '2', 'pipe:1'])
            self.assertEqual(len(sound), len(samples)*2)
            measured = array.array('h', sound)
            if sys.byteorder != 'little': measured.byteswap()
            expected = array.array('h', samples)
            if sys.byteorder != 'little': expected.byteswap()
            self.assertLessEqual(max(abs(a-b) for a, b in zip(measured, expected)), 1)
            self.assertGreater(math.sqrt(sum(n*n for n in measured)/len(measured)), 3000)
        self.assertEqual(self.project, before)
        self.assertEqual(self.frame().tobytes(), self.image.tobytes())

    def test_color_modes_and_pipeline_source_are_part_of_cache_identity(self):
        chunk = self.project['sequences'][0]
        self.assertNotEqual(engine.chunk_key(self.project, chunk, {'color_processing': 'rgb'}),
                            engine.chunk_key(self.project, chunk, {'color_processing': 'legacy'}))
        original = engine.file_revision
        def changed(path, revisions=None):
            result = original(path, revisions)
            return {**result, 'sha256': 'changed-pipeline'} if path and path.endswith('render_color.py') else result
        before = engine.chunk_key(self.project, chunk, {})
        self.assertEqual(before, engine.chunk_key(self.project, chunk, {'color_processing': 'rgb', 'format': 'h264'}))
        with mock.patch.object(engine, 'file_revision', changed):
            self.assertNotEqual(engine.chunk_key(self.project, chunk, {}), before)

    def test_nested_cache_never_substitutes_a_result_from_the_other_color_mode(self):
        child = copy.deepcopy(self.project['sequences'][0]); child['id'] = 'child'
        self.project['sequences'].append(child)
        self.project['sequences'][0]['tracks'][0]['clips'] = [{'id': 'n', 'sequence_id': 'child', 'start': 0, 'in_': 0, 'out': .25}]
        calls = []
        def encode(command, **kwargs):
            calls.append(list(command)); Path(command[-1]).write_bytes(b'controlled-completed-encode')
        with RenderContext(scratch_parent=str(self.root)) as context, mock.patch.object(engine, '_run_ffmpeg', encode):
            for mode in ['rgb', 'legacy', 'rgb', 'legacy']:
                engine.prerender_nested(self.project, self.project['sequences'][0], {'color_processing': mode}, 'ffmpeg', context=context)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][calls[0].index('-c:v')+1], 'ffv1')
            self.assertEqual(calls[1][calls[1].index('-c:v')+1], 'ffv1')
            self.assertEqual(calls[1][calls[1].index('-pix_fmt')+1], 'yuv420p')
            self.assertEqual(calls[0][calls[0].index('-pix_fmt')+1], 'bgra')
            self.assertNotEqual(calls[0][-1], calls[1][-1])


if __name__ == '__main__': unittest.main(verbosity=2)
