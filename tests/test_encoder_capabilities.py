"""Delivery selection, real probe behavior and bounded failure handling."""
import concurrent.futures
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import encoder_capabilities as ec
import render
from render_context import RenderContext


class EncoderTests(unittest.TestCase):
    def setUp(self):
        self.calls = []; self.time = 0
        self.identity = (('/fixture/ffmpeg.exe', 100, 1, 1), ('/fixture/ffprobe.exe', 90, 1, 1))
        self.codec = 'h264'; self.fail = None
        def runner(command):
            self.calls.append(command)
            if self.fail: return self.fail(command)
            if '-encoders' in command:
                return SimpleNamespace(returncode=0, stderr=b'', stdout=b' V....D libx264 software\n V....D h264_nvenc hardware\n V....D libx265 software\n V....D hevc_qsv hardware\n')
            if '-show_entries' in command:
                return SimpleNamespace(returncode=0, stderr=b'', stdout=json.dumps({'streams': [
                    {'codec_name': self.codec, 'width': 320, 'height': 180, 'nb_read_frames': '4',
                     'color_range':'tv','color_space':'bt709','color_transfer':'bt709','color_primaries':'bt709'}]}).encode())
            if 'trace_headers' in command:
                values={'vui_parameters_present_flag':1,'video_signal_type_present_flag':1,'colour_description_present_flag':1,'video_full_range_flag':0,'colour_primaries':1,'transfer_characteristics':1,'matrix_coefficients':1}
                trace='\n'.join(f'[trace_headers @ fixture] 0 {k} {v:08b} = {v}' for k,v in values.items())
                return SimpleNamespace(returncode=0,stderr=trace.encode(),stdout=b'')
            if command[-1] == '-':
                return SimpleNamespace(returncode=0, stderr=b'', stdout=bytes((48, 160, 80)) * (320*180*4))
            Path(command[-1]).write_bytes(b'encoded sample')
            return SimpleNamespace(returncode=0, stderr=b'', stdout=b'')
        self.runner = runner
        self.service = ec.EncoderCapabilities(runner=runner, clock=lambda: self.time)
        self.service.identity = lambda: self.identity

    def test_format_selection_rejects_cross_codec_and_unimplemented_upload(self):
        self.assertEqual(ec.selected_encoder({}), 'libx264')
        self.assertEqual(ec.selected_encoder({'format': 'hevc'}), 'libx265')
        self.assertIsNone(ec.selected_encoder({'format': 'prores', 'vcodec': 'h264_nvenc'}))
        for preset in [{'vcodec': 'libx265'}, {'format': 'hevc', 'vcodec': 'h264_nvenc'}, {'vcodec': 'h264_vaapi'}]:
            with self.assertRaises(ValueError): ec.selected_encoder(preset)

    def test_catalog_distinguishes_build_listing_from_device_support(self):
        catalog = self.service.catalog()
        self.assertIn('h264_nvenc', catalog['encoders'])
        self.assertNotIn('h264_amf', catalog['encoders'])
        self.assertTrue(all('ok' not in row for row in catalog['details']))
        catalog['encoders'].clear(); self.assertIn('libx264', self.service.catalog()['encoders'])
        self.assertEqual(len(self.calls), 1)

    def test_listed_hardware_failure_is_reported_and_not_replaced_with_software(self):
        self.service.catalog()
        self.fail = lambda cmd: SimpleNamespace(returncode=1, stdout=b'', stderr=b'No usable GPU driver')
        result = self.service.check('h264_nvenc')
        self.assertFalse(result['ok']); self.assertIn('GPU driver', result['message'])
        self.assertEqual([c[c.index('-c:v')+1] for c in self.calls if '-c:v' in c], ['h264_nvenc'])

    def test_missing_encoder_never_starts_a_probe(self):
        result = self.service.check('h264_amf')
        self.assertFalse(result['ok']); self.assertIn('not included', result['message'])
        self.assertEqual(len(self.calls), 1)

    def test_success_checks_metadata_decoded_pixels_and_removes_temporary_files(self):
        result = self.service.check('libx264', {'crf': 20})
        self.assertTrue(result['ok']); self.assertEqual(len(result['sample_sha256']), 64)
        encode = next(c for c in self.calls if '-c:v' in c)
        self.assertIn("é's", encode[-1]); self.assertFalse(Path(encode[-1]).parent.exists())
        self.assertEqual(encode[encode.index('-crf')+1], '20')

    def test_wrong_codec_or_black_decoded_frames_cannot_pass(self):
        self.codec = 'hevc'
        self.assertFalse(self.service.check('libx264')['ok'])
        self.fail = lambda cmd: SimpleNamespace(returncode=0, stderr=b'', stdout=bytes(320*180*3*4)) if cmd[-1] == '-' and 'trace_headers' not in cmd else self.good_runner(cmd)
        # A separate runner lets earlier encode/metadata steps stay realistic.
        self.codec = 'h264'; original = self.runner
        def good(command):
            saved = self.fail; self.fail = None
            try: return original(command)
            finally: self.fail = saved
        self.good_runner = good
        result = self.service.check('libx264', refresh=True)
        self.assertFalse(result['ok']); self.assertIn('pixels', result['message'])

    def test_success_cache_is_detached_and_keyed_by_tools_and_encoding_options(self):
        result = self.service.check('libx264'); n = len(self.calls); result['ok'] = False
        self.assertTrue(self.service.check('libx264')['ok']); self.assertEqual(len(self.calls), n)
        self.assertTrue(self.service.check('libx264', {'crf': 25})['ok']); self.assertGreater(len(self.calls), n)
        n = len(self.calls); self.identity = (('/fixture/new-ffmpeg.exe', 101, 2, 2), self.identity[1])
        self.assertTrue(self.service.check('libx264')['ok']); self.assertGreater(len(self.calls), n)
        n = len(self.calls); self.time += 301
        self.assertTrue(self.service.check('libx264')['ok']); self.assertGreater(len(self.calls), n)

    def test_concurrent_requests_share_one_completed_probe(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
            reports = list(pool.map(lambda _: self.service.check('libx264'), range(5)))
        self.assertTrue(all(r['ok'] for r in reports))
        self.assertEqual(sum('-c:v' in c and c[c.index('-c:v')+1] != 'copy' for c in self.calls), 1)

    def test_timeout_releases_the_check_lock_and_preserves_failure(self):
        def timeout(command): raise subprocess.TimeoutExpired(command, 15)
        self.fail = timeout; result = self.service.check('libx264')
        self.assertFalse(result['ok']); self.assertIn('timed out', result['message'])
        self.fail = None; self.assertTrue(self.service.check('libx264')['ok'])

    def test_executable_change_during_probe_rejects_the_result(self):
        calls = 0
        def identity():
            nonlocal calls
            calls += 1
            return self.identity if calls == 1 else ((self.identity[0][0], 100, 2, 2), self.identity[1])
        self.service.identity = identity
        self.assertIn('changed', self.service.check('libx264')['message'])

    def test_production_and_probe_use_the_same_delivery_encoder_arguments(self):
        project = {'id': 'p', 'media': {}, 'sequences': [{'id': 's', 'width': 320, 'height': 180,
                   'fps': 30, 'duration': 1, 'captions': [], 'tracks': []}]}
        for codec, (family, _, _) in ec.PROFILES.items():
            preset = {'format': family, 'vcodec': codec, 'bitrate': '4M'}
            with self.subTest(codec=codec), tempfile.TemporaryDirectory() as folder, RenderContext(scratch_parent=folder) as context:
                command, _ = render.build_command(project, 's', str(Path(folder)/'out.mp4'), preset, context=context)
                options = ec.video_options(codec, preset)
                i = command.index('-c:v'); self.assertEqual(command[i:i+len(options)], options)

    def test_windows_probes_hide_console_and_bound_subprocess_lifetime(self):
        with mock.patch.object(ec.os, 'name', 'nt'), mock.patch.object(ec.subprocess, 'CREATE_NO_WINDOW', 0x08000000, create=True), mock.patch.object(ec.subprocess, 'run') as run:
            ec._run(['ffmpeg.exe', '-encoders'])
        kwargs = run.call_args.kwargs
        self.assertEqual(kwargs['creationflags'], 0x08000000); self.assertEqual(kwargs['timeout'], 15)
        self.assertEqual(kwargs['stdin'], subprocess.DEVNULL)

    def test_real_available_software_encoder_exercises_probe_and_decode(self):
        # This host lacks x264/x265. OpenH264 exercises real H.264 color metadata
        # and decode without certifying the shipping x264/HEVC/Windows path.
        with mock.patch.dict(ec.PROFILES, {'libopenh264': ('h264', 'OpenH264 fixture', False)}):
            result = ec.EncoderCapabilities().check('libopenh264', {'bitrate': '4M'})
        self.assertTrue(result['ok'], result)

    def test_missing_or_wrong_color_tags_cannot_pass_device_qualification(self):
        real = self.runner
        def runner(command):
            result = real(command)
            if '-show_entries' in command:
                doc = json.loads(result.stdout);doc['streams'][0].pop('color_range');doc['streams'][0]['color_transfer']='smpte2084';result.stdout=json.dumps(doc).encode()
            return result
        self.service.run = runner
        result = self.service.check('libx264')
        self.assertFalse(result['ok']);self.assertIn('color tags',result['message'])

    def test_container_tags_cannot_hide_missing_video_header_metadata(self):
        real = self.runner
        def runner(command):
            result = real(command)
            if 'trace_headers' in command:
                result.stderr=b'[trace_headers @ fixture] 0 vui_parameters_present_flag 0 = 0\n'
            return result
        self.service.run = runner
        result = self.service.check('libx264')
        self.assertFalse(result['ok']);self.assertIn('video headers',result['message'])


if __name__ == '__main__': unittest.main(verbosity=2)
