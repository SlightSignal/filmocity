"""Resource integrity and relink failure injection; real FFmpeg fixtures, isolated projects."""
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
os.environ.setdefault('FILMOCITY_ROOT', str(Path(tempfile.gettempdir()) / 'filmocity-resource-test-default'))
import preflight
import render
import server
import subprocesses
import source_relink_io


def project(path):
    return {'version': 3, 'id': 'test', 'name': 'Resources',
            'media': {'a': {'id': 'a', 'name': 'Original', 'path': str(path), 'has_video': True, 'has_audio': False}},
            'sequences': [{'id': 's', 'name': 'Test', 'width': 64, 'height': 48, 'fps': 24,
                           'duration': .5, 'captions': [], 'tracks': [
                               {'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [
                                   {'id': 'c', 'media_id': 'a', 'start': 0, 'in_': 0, 'out': .5}]}]}]}


class Resources(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='filmocity-resources-')
        self.root = Path(self.tmp.name); self.source = self.root / 'original.mp4'
        self.source.write_bytes(b'source'); self.p = project(self.source)
    def tearDown(self): self.tmp.cleanup()
    def report(self, preset=None): return preflight.inspect_resources(self.p, 's', preset)
    def codes(self, report=None): return {i['code'] for i in (report or self.report())['issues']}

    def test_present_sources(self): self.assertTrue(self.report()['ok'])
    def test_windows_child_process_options_hide_console_and_preserve_flags(self):
        with patch.object(subprocesses.os, 'name', 'nt'):
            options = subprocesses._options({'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP, 'stdout': subprocess.PIPE})
        self.assertTrue(options['creationflags'] & subprocess.CREATE_NO_WINDOW)
        self.assertTrue(options['creationflags'] & subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertEqual(options['stdout'], subprocess.PIPE)
    def test_proxy_does_not_replace_missing_original(self):
        self.p['media']['a']['proxy'] = '/proxies/available.mp4'; self.source.unlink()
        self.assertIn('missing_source', self.codes())
    def test_empty_source_rejected(self):
        self.source.write_bytes(b''); self.assertIn('unreadable_source', self.codes())
    def test_unreferenced_bin_item_does_not_block(self):
        self.p['media']['unused'] = {'path': str(self.root / 'missing.mp4')}
        self.assertTrue(self.report()['ok'])
    def test_disabled_source_is_not_an_ffmpeg_input(self):
        self.source.unlink(); self.p['sequences'][0]['tracks'][0]['clips'][0]['enabled'] = False
        self.assertTrue(self.report()['ok'])
        cmd, _ = render.build_command(self.p, 's', str(self.root / 'out.mp4'))
        self.assertNotIn(str(self.source), cmd)
    def test_muted_source_is_not_an_ffmpeg_input(self):
        self.source.unlink(); self.p['sequences'][0]['tracks'][0]['muted'] = True
        self.assertTrue(self.report()['ok'])
        cmd, _ = render.build_command(self.p, 's', str(self.root / 'out.mp4'))
        self.assertNotIn(str(self.source), cmd)
    def test_synthetic_media_needs_no_source(self):
        self.p['media']['a'] = {'id': 'a', 'synthetic': {'kind': 'black'}}
        self.assertTrue(self.report()['ok'])
    def test_unknown_media_entry(self):
        self.p['media'].clear(); self.assertIn('missing_media_entry', self.codes())
    def test_nested_source_and_cycle(self):
        child = copy.deepcopy(self.p['sequences'][0]); child['id'] = 'child'; self.p['sequences'].append(child)
        parent = self.p['sequences'][0]['tracks'][0]['clips'][0]
        parent.pop('media_id'); parent['sequence_id'] = 'child'; self.source.unlink()
        self.assertIn('missing_source', self.codes())
        child['tracks'][0]['clips'] = [{'id': 'back', 'sequence_id': 's'}]
        self.assertIn('nested_cycle', self.codes())
    def test_missing_nested_sequence(self):
        clip = self.p['sequences'][0]['tracks'][0]['clips'][0]; clip.pop('media_id'); clip['sequence_id'] = 'missing'
        self.assertIn('missing_sequence', self.codes())
    def test_external_graphic_lut_watermark_are_required(self):
        clip = self.p['sequences'][0]['tracks'][0]['clips'][0]
        clip['color'] = {'lut': str(self.root / 'look.cube')}
        clip['graphic'] = {'layers': [{'kind': 'image', 'path': str(self.root / 'logo.png')}]}
        report = self.report({'watermark': {'path': str(self.root / 'watermark.png')}})
        self.assertTrue({'missing_lut', 'missing_graphic_image', 'missing_watermark'} <= self.codes(report))
    def test_explicit_font_file_cannot_silently_fall_back(self):
        self.p['sequences'][0]['tracks'][0]['clips'][0]['title'] = {'text': 'Title', 'font': str(self.root / 'missing.ttf')}
        self.assertIn('missing_font', self.codes())
    def test_font_family_substitution_is_visible(self):
        self.p['sequences'][0]['tracks'][0]['clips'][0]['title'] = {'text': 'Title', 'font': 'Definitely No Such Family 938'
        }
        self.assertIn('font_substitution', self.codes()); self.assertTrue(self.report()['ok'])
    def test_numbered_sequence_gap_and_byte_cache_revision(self):
        pattern = str(self.root / 'shot_%04d.png')
        for index in (1, 2, 3): Path(pattern % index).write_bytes(b'frame')
        self.p['media']['a'].update(path=pattern, sequence_frames=3, input_opts=['-start_number', '1'])
        self.assertTrue(preflight.media_online(self.p['media']['a'])); self.assertTrue(self.report()['ok'])
        before = render.chunk_key(self.p, self.p['sequences'][0], {})
        frame = Path(pattern % 2); stat = frame.stat(); frame.write_bytes(b'other')
        os.utime(frame, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        self.assertNotEqual(before, render.chunk_key(self.p, self.p['sequences'][0], {}))
        frame.unlink(); self.assertFalse(preflight.media_online(self.p['media']['a']))
        self.assertIn('missing_sequence_frames', self.codes())
    def test_multicam_unchosen_offline_angle_does_not_block(self):
        child = copy.deepcopy(self.p['sequences'][0]); child.update(id='child', multicam=True)
        child['tracks'].append({'id': 'V2', 'kind': 'video', 'index': 2, 'clips': [{'id': 'bad', 'media_id': 'offline'}]})
        self.p['media']['offline'] = {'id': 'offline', 'path': str(self.root / 'missing.mp4')}
        self.p['sequences'].append(child); self.p['sequences'][0]['tracks'][0]['clips'] = [{'id': 'angle', 'sequence_id': 'child', 'multicam_angle': 0}]
        self.assertTrue(self.report()['ok'])
    def test_missing_original_preserves_existing_master(self):
        self.source.unlink(); out = self.root / 'master.mp4'; out.write_bytes(b'previous master')
        with self.assertRaises(preflight.ResourceError): render.render(self.p, 's', str(out))
        self.assertEqual(out.read_bytes(), b'previous master')


class Request:
    def __init__(self, body): self.body = body
    async def json(self): return self.body


class Relink(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixtures = tempfile.TemporaryDirectory(prefix='filmocity-relink-fixtures-')
        cls.fixture_root = Path(cls.fixtures.name)
        for name, duration, color in [('original', .8, 'red'), ('replacement', .8, 'blue'), ('short', .2, 'green')]:
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', f'color={color}:s=64x48:r=24',
                            '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', str(duration),
                            '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', str(cls.fixture_root / (name + '.mp4'))], check=True)
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=frequency=440:duration=1',
                        str(cls.fixture_root / 'audio.wav')], check=True)
    @classmethod
    def tearDownClass(cls): cls.fixtures.cleanup()
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='filmocity-relink-project-'); self.root = Path(self.tmp.name)
        self.scope = patch.object(server, 'ROOT', str(self.root)); self.scope.start()
        self.p = project(self.fixture_root / 'original.mp4')
        self.p['media']['a'].update(server.probe(str(self.fixture_root / 'original.mp4')))
        self.p['media']['a'].update(label='red', note='Keep this', bin='bin1', ingest_token='old', proxy='/proxies/old.mp4')
        server.save_project(self.p); self.project_file = server.PP('project.json')
    def tearDown(self): self.scope.stop(); self.tmp.cleanup()
    def candidate(self, filename='replacement.mp4', **extra):
        return asyncio.run(server._relink_candidate({'_context':server.get_project_state()['context'], 'media_id': 'a', 'path': str(self.fixture_root / filename), **extra}))
    def apply(self, report):
        return asyncio.run(server.media_relink(Request({'_context':report['context'],'media_id':report['requested_media_id'],
            'path':report['path'],'fingerprint':report['fingerprint'],'actor':'human','client':'resource-test'})))

    def test_replacement_inspection_detects_filename_change(self):
        report = self.candidate()['report']; self.assertTrue(report['ok'])
        self.assertIn('changed_filename', {i['code'] for i in report['issues']})
    def test_shorter_replacement_is_rejected(self):
        self.assertFalse(self.candidate('short.mp4')['report']['ok'])
    def test_audio_file_cannot_replace_video(self):
        self.assertFalse(self.candidate('audio.wav')['report']['ok'])
    def test_invalid_media_cannot_replace_original(self):
        (self.fixture_root / 'invalid.mp4').write_bytes(b'bad data')
        with self.assertRaises(server.HTTPException):self.candidate('invalid.mp4')
    def test_stale_inspection_signature_is_rejected(self):
        signature = self.candidate()['report']['expectedSource']
        p = server.load_project(); p['media']['a']['note'] = 'New note'; server.save_project(p)
        with self.assertRaises(server.HTTPException) as error: self.candidate(expectedSource=signature)
        self.assertEqual(error.exception.status_code, 409)
    def test_relink_keeps_identity_timeline_and_labels(self):
        before = copy.deepcopy(self.p['sequences'])
        report=self.candidate()['report']
        with patch.object(server, 'finish_ingest', return_value={'id':'controlled-task'}):
            media = self.apply(report)['media']
        p = server.load_project(); self.assertEqual(p['sequences'], before)
        self.assertEqual(media['id'], 'a'); self.assertEqual(media['label'], 'red'); self.assertEqual(media['note'], 'Keep this')
        self.assertIsNone(media.get('proxy')); self.assertNotEqual(media['ingest_token'], 'old')
    def test_rejected_relink_does_not_modify_project(self):
        before = Path(self.project_file).read_bytes()
        report=self.candidate('short.mp4')['report']
        with self.assertRaises(server.HTTPException):
            self.apply(report)
        self.assertEqual(Path(self.project_file).read_bytes(), before)
    def test_late_proxy_for_old_generation_is_discarded(self):
        self.assertFalse(server._update_ingested_media('a', self.p['media']['a']['path'], {'proxy': '/old'}, self.project_file, 'obsolete'))
        self.assertEqual(server.load_project()['media']['a']['proxy'], '/proxies/old.mp4')
    def test_background_result_stays_with_captured_project_after_switch(self):
        other = self.root / 'projects/other/project.json'; other.parent.mkdir(parents=True); other.write_text(json.dumps(project('different')), encoding='utf-8')
        (self.root / 'active.json').write_text('{"id":"other"}', encoding='utf-8')
        self.assertTrue(server._update_ingested_media('a', self.p['media']['a']['path'], {'proxy': '/new'}, self.project_file, 'old'))
        self.assertEqual(json.loads(Path(self.project_file).read_text(encoding='utf-8'))['media']['a']['proxy'], '/new')
        self.assertNotIn('proxy', server.load_project()['media']['a'])
    def test_project_switch_during_relink_probe_is_rejected(self):
        report=self.candidate()['report'];real_probe=source_relink_io.inspect
        def switching_probe(*args,**kwargs):
            result=real_probe(*args,**kwargs);(self.root/'active.json').write_text('{"id":"other"}',encoding='utf-8');return result
        with patch.object(source_relink_io,'inspect',side_effect=switching_probe),self.assertRaises(server.HTTPException) as error:
            self.apply(report)
        self.assertEqual(error.exception.status_code, 409)
    def test_batch_rejects_offline_sequence_before_any_job_starts(self):
        bad = copy.deepcopy(self.p['sequences'][0]); bad.update(id='bad', name='Offline'); bad['tracks'][0]['clips'][0]['media_id'] = 'missing'
        p = server.load_project(); p['sequences'].append(bad); server.save_project(p)
        with patch.object(server, 'start_render') as start, self.assertRaises(server.HTTPException):
            asyncio.run(server.render_all(Request({})))
        start.assert_not_called()


if __name__ == '__main__': unittest.main()
