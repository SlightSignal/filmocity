"""Actual Filmocity GLES shader pixels versus the production FFmpeg color chain.

Run separately: python tests/test_gpu_color.py
Requires Linux Mesa surfaceless EGL/GLES3, Node.js and FFmpeg. A missing driver
fails explicitly; this suite is not a substitute for Windows WebGL2 acceptance.
"""
import argparse
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
sys.path.insert(0, str(ROOT / 'tests' / 'helpers'))
from render import color_chain, render_frame
from render_context import RenderContext
from gles import OffscreenGLES

SHADER = ROOT / 'frontend/gpu.js'


def capture(color, width, height, source=None):
    request = {'clip': {'color': color}, 'width': width, 'height': height}
    request['sourcePath'] = str(source or SHADER)
    result = subprocess.run(['node', str(ROOT / 'tests/helpers/gpu-capture.cjs')],
                            input=json.dumps(request), text=True, capture_output=True, check=True, timeout=15)
    return json.loads(result.stdout)


def exported(color, pixels, width, height):
    chain = ','.join(color_chain(color)) or 'null'
    return subprocess.run(['ffmpeg', '-v', 'error', '-threads', '1', '-filter_threads', '1',
        '-f', 'rawvideo', '-pixel_format', 'rgba', '-video_size', f'{width}x{height}', '-i', 'pipe:0',
        '-vf', chain, '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', 'pipe:1'],
        input=pixels, capture_output=True, check=True, timeout=15).stdout


def palette():
    colors = [(r, g, b, 255) for r in range(0, 256, 17) for g in range(0, 256, 17) for b in range(0, 256, 17)]
    colors += [(i, i, i, 255) for i in range(256)]
    return bytes(channel for pixel in colors for channel in pixel), 256, 17


class GPUColorPixels(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.gpu = OffscreenGLES()
        cls.addClassCleanup(cls.gpu.close)
        cls.pixels, cls.width, cls.height = palette()
        cls.measurements = []
        print('Actual shader driver:', json.dumps(cls.gpu.info), flush=True)

    def compare(self, color, pixels=None, width=None, height=None, tolerance=1):
        pixels = self.pixels if pixels is None else pixels
        width = self.width if width is None else width
        height = self.height if height is None else height
        before = copy.deepcopy(color)
        actual = self.gpu.render(capture(color, width, height), pixels, width, height)
        expected = exported(color, pixels, width, height)
        self.assertEqual(color, before)
        self.assertEqual(len(actual), len(expected))
        # Filmocity's shader returns premultiplied RGBA for drawImage composition.
        expected = bytes(round(v * expected[i // 4 * 4 + 3] / 255) if i % 4 != 3 else v for i, v in enumerate(expected))
        differences = [abs(a - b) for a, b in zip(actual, expected)]
        worst = max(differences)
        at = differences.index(worst) // 4 * 4
        measurement = {'test': self._testMethodName, 'color': color, 'pixels': width * height,
            'max_error': worst, 'allowed_error': tolerance, 'source': list(pixels[at:at+4]),
            'preview': list(actual[at:at+4]), 'export': list(expected[at:at+4])}
        self.measurements.append(measurement)
        self.assertLessEqual(worst, tolerance, measurement)
        return actual

    def test_neutral_pass_through_is_exact_across_the_color_cube_and_gray_ramp(self):
        self.compare({}, tolerance=0)
        self.compare({'wheels': {}}, tolerance=0)

    def test_each_wheel_band_channel_and_polarity_matches_export(self):
        for band in ['shadows', 'midtones', 'highlights']:
            for channel in ['r', 'g', 'b']:
                for amount in [-0.5, 0.5]:
                    with self.subTest(band=band, channel=channel, amount=amount):
                        self.compare({'wheels': {band: {channel: amount}}})

    def test_combined_wheels_and_clipping_match_export(self):
        for wheels in [
            {'shadows': {'r': 1, 'g': -1}, 'midtones': {'b': 1}, 'highlights': {'r': -1, 'g': 1, 'b': -1}},
            {'shadows': {'r': -.3, 'g': .8, 'b': .2}, 'midtones': {'r': .9, 'g': -.8, 'b': -.7}, 'highlights': {'r': .1, 'g': .4, 'b': -.2}},
        ]:
            with self.subTest(wheels=wheels): self.compare({'wheels': wheels})

    def test_export_precision_and_activation_threshold_match(self):
        for wheels in [
            {'shadows': {'r': .001, 'b': -.0009}},
            {'midtones': {'r': .0010001, 'g': -.00099}},
            {'highlights': {'r': .123456, 'g': -.45678, 'b': .654321}},
        ]:
            with self.subTest(wheels=wheels): self.compare({'wheels': wheels})

    def test_midgray_uses_the_same_band_as_export(self):
        source = bytes([128, 128, 128, 255])
        actual = self.compare({'wheels': {'midtones': {'r': .5}}}, source, 1, 1, tolerance=0)
        self.assertEqual(actual, source)
        actual = self.compare({'wheels': {'highlights': {'r': .5}}}, source, 1, 1)
        self.assertGreater(actual[0], 210)

    def test_alpha_is_preserved_and_color_is_premultiplied_once(self):
        source = bytes(x for a in [0, 1, 64, 128, 254, 255] for x in [64, 33, 16, a])
        actual = self.compare({'wheels': {'midtones': {'r': .5, 'b': -.4}}}, source, 6, 1)
        self.assertEqual(actual[3::4], source[3::4])

    def test_wheels_follow_saturation_with_bounded_neutral_conversion_error(self):
        source = bytes(v for i in range(256) for v in [i, i, i, 255])
        for saturation in [-1, 1]:
            with self.subTest(saturation=saturation):
                # eq performs an RGB→YUV→RGB round trip even on neutral input.
                # Measure that difference separately; this case bounds the known
                # combined error, rather than claiming saturation color parity.
                converted = exported({'saturation': saturation}, source, 256, 1)
                self.assertLessEqual(max(abs(a-b) for a, b in zip(source, converted)), 3)
                self.compare({'saturation': saturation, 'wheels': {'midtones': {'r': .5}, 'highlights': {'g': -.3}}}, source, 256, 1, tolerance=4)

    def test_complete_project_frame_wheel_parity(self):
        self.assertLessEqual(max(m['max_error'] for m in self.project_measurement), 1, self.project_measurement)

    def setUp(self):
        if self._testMethodName == 'test_complete_project_frame_wheel_parity':
            self.project_measurement = self.measure_project_frame()

    def measure_project_frame(self):
        swatches = [(16, 16, 16), (32, 32, 32), (64, 64, 64), (128, 128, 128),
                    (192, 192, 192), (48, 80, 96), (32, 16, 64), (170, 85, 102)]
        image = Image.new('RGBA', (64, 64))
        for x, rgb in enumerate(swatches): image.paste((*rgb, 255), (x * 8, 0, x * 8 + 8, 64))
        color = {'wheels': {'shadows': {'r': .2, 'b': -.1}, 'midtones': {'g': .3}, 'highlights': {'b': -.2}}}
        actual = self.gpu.render(capture(color, 64, 64), image.tobytes(), 64, 64)
        with tempfile.TemporaryDirectory(prefix='filmocity-color-é-') as folder:
            source, output = Path(folder) / 'color chart.png', Path(folder) / 'export.png'
            image.convert('RGB').save(source)
            original = source.read_bytes()
            clip = {'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 1, 'color': color}
            project = {'id': 'color', 'media': {'m': {'id': 'm', 'path': str(source), 'is_image': True,
                'has_video': True, 'has_audio': False, 'width': 64, 'height': 64, 'duration': 1}},
                'sequences': [{'id': 's', 'width': 64, 'height': 64, 'fps': 24, 'tracks': [
                    {'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [clip]}]}]}
            before = copy.deepcopy(project)
            with RenderContext(scratch_parent=folder, stall_timeout=15) as context:
                render_frame(project, 's', .5, str(output), context=context)
            self.assertEqual(project, before)
            self.assertEqual(source.read_bytes(), original)
            with Image.open(output) as result:
                exported_image = result.convert('RGBA')
            measurements = []
            for patch in range(len(swatches)):
                x, y = patch * 8 + 4, 32
                index = (y * 64 + x) * 4
                pixel = tuple(actual[index:index+4])
                expected = exported_image.getpixel((x, y))
                measurements.append({'source': swatches[patch], 'preview': pixel, 'export': expected,
                                     'max_error': max(abs(a-b) for a, b in zip(pixel, expected))})
            self.measurements.append({'test': self._testMethodName, 'color': color, 'allowed_error': 1,
                                      'max_error': max(m['max_error'] for m in measurements), 'swatches': measurements})
            return measurements


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, add_help=False)
    parser.add_argument('--report', type=Path, help='Write measured errors, source hashes and driver identity as JSON')
    parser.add_argument('--shader', type=Path, default=SHADER, help='Compare a frozen earlier gpu.js with the same fixtures')
    args, rest = parser.parse_known_args()
    SHADER = args.shader.resolve()
    result = unittest.main(argv=[sys.argv[0], *rest], verbosity=2, exit=False).result
    if args.report:
        report = {'shader': str(SHADER.relative_to(ROOT)) if SHADER.is_relative_to(ROOT) else str(SHADER),
            'sha256': hashlib.sha256(SHADER.read_bytes()).hexdigest(),
            'inputs': {name: hashlib.sha256((ROOT / name).read_bytes()).hexdigest() for name in [
                'backend/render.py', 'backend/render_color.py', 'backend/matte_tracks.py', 'backend/effects.py', 'backend/preflight.py', 'tests/test_gpu_color.py', 'tests/helpers/gpu-capture.cjs', 'tests/helpers/gles.py']},
            'driver': getattr(GPUColorPixels, 'gpu', None).info if hasattr(GPUColorPixels, 'gpu') else None,
            'ffmpeg': subprocess.check_output(['ffmpeg', '-version'], text=True).splitlines()[0],
            'tests_run': result.testsRun, 'unexpected_failures': len(result.failures), 'errors': len(result.errors),
            'expected_failures': [test.id() for test, _ in result.expectedFailures],
            'measurements': getattr(GPUColorPixels, 'measurements', []),
            'scope': 'Captured production GLSL, uniforms and LUTs on Linux GLES; not browser/WebGL2 or native Windows evidence.'}
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, indent=2) + '\n')
    sys.exit(0 if result.wasSuccessful() else 1)
