"""Decoded multicam Flatten controls with independent frame and tone references.

These execute the canonical pure plan through the production operation adapter,
then real FFmpeg. Route ownership/history and native browser playback are separate.
"""
import array
import ast
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import render
import multicam_flatten


def operation_adapter():
    tree = ast.parse((ROOT/'backend/server.py').read_text())
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in ('apply_ops', '_walk')]
    scope = {'copy': copy, 'uuid': uuid}
    exec(compile(ast.Module(nodes, type_ignores=[]), 'production-operation-adapter', 'exec'), scope)
    return scope['apply_ops']


class MulticamComposition(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix='filmocity-multicam-composition-')
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.serial = 0
        self.media = {}
        self.source_hashes = {}
        for mid, frequencies, amplitude in [('a', (480, 720), 8192), ('b', (960, 1440), 12288)]:
            raw = self.root/(mid+'.rgb')
            raw.write_bytes(b''.join((bytes([32+3*n])*3 if mid == 'a' else bytes([0, 32+3*n, 160]))*64*48
                                    for n in range(60)))
            sound = self.root/(mid+'.wav')
            values = array.array('h', (round(amplitude*math.sin(2*math.pi*f*n/48000))
                                      for n in range(6*48000) for f in frequencies))
            if sys.byteorder != 'little': values.byteswap()
            with wave.open(str(sound), 'wb') as stream:
                stream.setparams((2, 2, 48000, 0, 'NONE', '')); stream.writeframes(values.tobytes())
            movie = self.root/(mid+'.mkv')
            self.run_command(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                              '-video_size', '64x48', '-framerate', '10', '-i', str(raw), '-i', str(sound),
                              '-map', '0:v:0', '-map', '1:a:0', '-c:v', 'ffv1', '-pix_fmt', 'bgr0',
                              '-c:a', 'pcm_s16le', str(movie)])
            self.media[mid] = {'id': mid, 'name': mid, 'path': str(movie), 'duration': 6,
                              'fps': 10, 'frame_rate': '10/1', 'width': 64, 'height': 48,
                              'has_video': True, 'has_audio': True, 'channels': 2, 'sample_rate': 48000}
            self.source_hashes[movie] = hashlib.sha256(movie.read_bytes()).hexdigest()
        self.inner = self.clip('inner', 'a', begin=1, end=5, audio={'linked': False})
        self.child = self.sequence('child', [self.track('V1', 1, [self.inner])],
                                   multicam=True, multicam_audio='follow')
        self.outer = self.clip('outer', begin=.5, end=1.5, sequence_id='child', multicam_angle=0,
                               audio={'linked': False}, note='Preserve editorial note')
        self.parent = self.sequence('parent', [self.track('PV', 1, [self.outer]),
                                              self.track('PA', 1, [], kind='audio')])
        self.project = {'id': 'multicam-composition', 'media': self.media,
                        'sequences': [self.parent, self.child]}

    @staticmethod
    def clip(cid, mid=None, start=0, begin=0, end=4, **values):
        return {'id': cid, 'media_id': mid, 'start': start, 'in_': begin, 'out': end, 'speed': 1, **values}

    @staticmethod
    def track(tid, index, clips, kind='video', **values):
        return {'id': tid, 'index': index, 'kind': kind, 'clips': clips, **values}

    @staticmethod
    def sequence(sid, tracks, **values):
        return {'id': sid, 'name': sid, 'width': 64, 'height': 48, 'fps': 10,
                'tracks': tracks, 'master': {}, **values}

    def run_command(self, args):
        result = subprocess.run(args, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-5000:].decode(errors='replace'))
        return result.stdout

    def plan(self):
        original = copy.deepcopy(self.project)
        result = multicam_flatten.plan(self.project, {'sequence': 'parent', 'clip_ids': ['outer'],
            '_context': {'workspace': 'w', 'project': 'multicam-composition', 'revision': 'r'}})
        self.assertEqual(self.project, original, 'Planner changed original project')
        return result

    def flatten(self):
        result = self.plan()
        self.assertTrue(result['ok'], result['issues'])
        candidate = copy.deepcopy(self.project)
        operation_adapter()(candidate, result['ops'])
        self.assertEqual(candidate['sequences'][1], self.child, 'Flatten changed original multicam sequence')
        pictures = [c for t in candidate['sequences'][0]['tracks'] if t['kind'] == 'video'
                    for c in t['clips']]
        self.assertTrue(pictures)
        self.assertTrue(all(c.get('media_id') and not c.get('sequence_id') for c in pictures),
                        'Direct Flatten must expose original sources')
        return candidate

    def export(self, project, audio=False):
        self.serial += 1
        path = self.root/f'result-{self.serial}.{"wav" if audio else "mkv"}'
        (self.root/f'project-{self.serial}.json').write_text(json.dumps(project, indent=2)+'\n')
        before = copy.deepcopy(project)
        context = render.RenderContext(scratch_parent=str(self.root))
        try:
            preset = {'format': 'audio', 'acodec': 'wav_float'} if audio else {
                'vcodec': 'ffv1', 'acodec': 'pcm_f32le', 'color_processing': 'rgb'}
            command, graph = render.build_command(project, 'parent', str(path), preset, context=context)
            (self.root/f'graph-{self.serial}.txt').write_text(graph)
            self.run_command(list(command))
        finally:
            context.close()
        self.assertEqual(project, before)
        if audio:
            raw = self.run_command(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0',
                                    '-acodec', 'pcm_f32le', '-f', 'f32le', '-'])
            values = array.array('f'); values.frombytes(raw)
            if sys.byteorder != 'little': values.byteswap()
            return values
        return self.run_command(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0',
                                 '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])

    def assert_frames(self, pixels, expected):
        self.assertEqual(len(pixels), len(expected)*64*48*3)
        self.assertEqual([list(pixels[n*64*48*3:n*64*48*3+3]) for n in range(len(expected))], expected)

    @staticmethod
    def frame(n): return [32+3*n]*3

    @staticmethod
    def amplitude(data, frequency, begin, end, channel=0):
        first, last = round(begin*48000), round(end*48000)
        values = data[2*first+channel:2*last+channel:2]
        cosine = sum(v*math.cos(2*math.pi*frequency*(first+i)/48000) for i, v in enumerate(values))
        sine = sum(v*math.sin(2*math.pi*frequency*(first+i)/48000) for i, v in enumerate(values))
        return 2*math.hypot(cosine, sine)/len(values)

    def test_inner_double_speed_keeps_duration_and_numbered_picture_addresses(self):
        self.inner['speed'] = 2
        expected = [self.frame(n) for n in range(20, 40, 2)]
        self.assert_frames(self.export(self.project), expected)
        self.assert_frames(self.export(self.flatten()), expected)

    def test_unit_speed_inner_and_outer_reverse_combinations_keep_native_addresses(self):
        for inner_reverse, outer_reverse, frames in [
            (False, True, range(24, 14, -1)), (True, False, range(44, 34, -1)),
            (True, True, range(35, 45))]:
            with self.subTest(inner_reverse=inner_reverse, outer_reverse=outer_reverse):
                self.inner['reverse'] = inner_reverse; self.outer['reverse'] = outer_reverse
                expected = [self.frame(n) for n in frames]
                self.assert_frames(self.export(self.project), expected)
                self.assert_frames(self.export(self.flatten()), expected)

    def test_interpreted_subclip_offset_with_inner_speed_and_outer_reverse(self):
        self.media['alias'] = {**self.media['a'], 'id': 'alias', 'subclip_of': 'a',
                               'sub_in': 2, 'duration': 10, 'interpret_fps': 5}
        self.inner.update(media_id='alias', speed=2)
        self.outer['reverse'] = True
        expected = [self.frame(n) for n in range(29, 19, -1)]
        self.assert_frames(self.export(self.project), expected)
        self.assert_frames(self.export(self.flatten()), expected)

    def test_edited_angle_preserves_both_sources_and_black_gap(self):
        self.child['tracks'][0]['clips'] = [self.clip('first', 'a', end=1, audio={'linked': False}),
                                            self.clip('second', 'b', start=2, end=1, audio={'linked': False})]
        self.outer.update(in_=0, out=3)
        expected = [self.frame(n) for n in range(10)] + [[0, 0, 0]]*10 + [[0, 32+3*n, 160] for n in range(10)]
        self.assert_frames(self.export(self.project), expected)
        self.assert_frames(self.export(self.flatten()), expected)

    def test_fixed_and_follow_audio_keep_source_gain_fades_track_and_master(self):
        gain = 20*math.log10(.5)
        self.child['tracks'] = [self.track('V1', 1, [self.clip('v1', 'a', audio={'linked': False})]),
                                self.track('V2', 2, [self.clip('v2', 'b', audio={'linked': False})]),
                                self.track('A1', 1, [self.clip('a1', 'a')], kind='audio'),
                                self.track('A2', 2, [self.clip('a2', 'b')], kind='audio')]
        self.child['multicam_audio_track'] = 'A1'
        self.outer.update(multicam_angle=1, in_=.5, out=2.5,
                          audio={'linked': True, 'gain_db': gain, 'fade_in': 1,
                                 'fade_out': .5, 'constant_power': False})
        self.parent['tracks'][1]['gain_db'] = gain
        self.parent['master']['gain_db'] = gain
        for mode, frequency, level in [('track', 480, .25*.125), ('follow', 960, .375*.125)]:
            with self.subTest(mode=mode):
                self.child['multicam_audio'] = mode
                for project in (self.project, self.flatten()):
                    data = self.export(project, audio=True)
                    self.assertEqual(len(data), 2*48000*2)
                    self.assertAlmostEqual(self.amplitude(data, frequency, 1.125, 1.375), level, delta=2e-5)
                    self.assertAlmostEqual(self.amplitude(data, frequency, .25, .5), level*.375, delta=2e-5)
                    other = 960 if mode == 'track' else 480
                    self.assertLess(self.amplitude(data, other, 1.125, 1.375), 1e-5)

    def test_actual_angle_cut_keeps_direction_and_fixed_follow_sound(self):
        gain = 20*math.log10(.5)
        self.child['tracks'] = [self.track('V1', 1, [self.clip('v1', 'a', audio={'linked': False})]),
                                self.track('V2', 2, [self.clip('v2', 'b', audio={'linked': False})]),
                                self.track('A1', 1, [self.clip('a1', 'a')], kind='audio'),
                                self.track('A2', 2, [self.clip('a2', 'b')], kind='audio')]
        self.child['multicam_audio_track'] = 'A1'
        self.outer.update(in_=0, out=2, audio={'linked': True, 'gain_db': gain,
                          'fade_in': .4, 'fade_out': .4, 'constant_power': False})
        for reverse in (False, True):
            for mode in ('track', 'follow'):
                with self.subTest(reverse=reverse, mode=mode):
                    self.outer['reverse'] = reverse; self.child['multicam_audio'] = mode
                    request = {'project': self.project, 'sequence': 'parent', 'clip_ids': ['outer'],
                               'track': 'PV', 'time': 1, 'angle': 1}
                    result = subprocess.run(['node', str(ROOT/'tests/helpers/multicam-switch-plan.cjs')],
                        input=json.dumps(request).encode(), capture_output=True, timeout=30, cwd=ROOT)
                    self.assertEqual(result.returncode, 0, result.stderr.decode())
                    response = json.loads(result.stdout)
                    candidate = copy.deepcopy(self.project); operation_adapter()(candidate, response['body']['ops'])
                    self.assertEqual(candidate['sequences'], response['optimistic_project']['sequences'])
                    self.assertEqual(candidate['media'], response['optimistic_project']['media'])
                    first = range(19, 9, -1) if reverse else range(10)
                    second = range(9, -1, -1) if reverse else range(10, 20)
                    expected = [self.frame(n) for n in first] + [[0, 32+3*n, 160] for n in second]
                    self.assert_frames(self.export(candidate), expected)
                    sound = self.export(candidate, audio=True)
                    self.assertEqual(len(sound), 2*48000*2)
                    self.assertAlmostEqual(self.amplitude(sound, 480, .5, .75), .125, delta=2e-5)
                    self.assertAlmostEqual(self.amplitude(sound, 480 if mode == 'track' else 960, 1.25, 1.5),
                                           .125 if mode == 'track' else .1875, delta=2e-5)

    def test_inner_and_outer_holds_keep_exact_frozen_native_frame(self):
        for inner_hold in (True, False):
            with self.subTest(inner_hold=inner_hold):
                self.inner.update(in_=1.7 if inner_hold else 1, out=5.7 if inner_hold else 5,
                                  hold=inner_hold)
                self.outer.update(in_=.5 if inner_hold else .7, out=1.5 if inner_hold else 1.7,
                                  hold=not inner_hold)
                expected = [self.frame(17)]*10
                self.assert_frames(self.export(self.project), expected)
                candidate = self.flatten()
                self.assert_frames(self.export(candidate), expected)
                self.assertEqual(max(map(abs, self.export(candidate, audio=True))), 0)

    def test_inner_transform_requires_processing_order_instead_of_silent_loss(self):
        self.inner['transform'] = {'scale': .5, 'x': -16, 'opacity': .5}
        self.outer['transform'] = {'scale': .5, 'x': 16, 'opacity': .5}
        result = self.plan(); self.assertFalse(result['ok']); self.assertFalse(result['ops'])
        self.assertTrue(result['issues'])

    def test_carried_bus_or_master_compressor_retains_gain_processing_order(self):
        gain = 20*math.log10(.5)
        compressor = {'comp': {'enabled': True, 'threshold_db': -24, 'ratio': 6,
                               'attack_ms': 10, 'release_ms': 100}}
        self.inner.update(in_=0, out=4)
        bus = self.track('A1', 1, [self.clip('sound', 'a')], kind='audio', gain_db=gain)
        self.child['tracks'].append(bus)
        self.child['master']['gain_db'] = gain
        self.outer.update(in_=0, out=4, audio={'linked': True, 'gain_db': gain})
        for position in ('bus', 'master'):
            with self.subTest(position=position):
                bus['audio_fx'] = compressor if position == 'bus' else {}
                self.child['master']['audio_fx'] = compressor if position == 'master' else {}
                filters = ['atrim=end_sample=192000', 'asetpts=PTS-STARTPTS',
                           'aformat=sample_fmts=fltp:channel_layouts=stereo']
                comp = 'acompressor=threshold=0.063095734448:ratio=6:attack=10:release=100:makeup=1'
                filters += ([comp, 'volume=0.5', 'volume=0.5', 'volume=0.5'] if position == 'bus'
                            else ['volume=0.5', comp, 'volume=0.5', 'volume=0.5'])
                raw = self.run_command(['ffmpeg', '-v', 'error', '-i', str(self.root/'a.wav'),
                    '-af', ','.join(filters), '-acodec', 'pcm_f32le', '-f', 'f32le', '-'])
                expected = array.array('f'); expected.frombytes(raw)
                if sys.byteorder != 'little': expected.byteswap()
                before = self.export(self.project, audio=True)
                self.assertEqual(len(before), len(expected))
                self.assertLess(max(abs(a-b) for a,b in zip(before,expected)), 3e-8)
                result = self.plan()
                if not result['ok']:
                    self.assertFalse(result['ops'])
                    self.assertIn('processing', json.dumps(result['issues']).lower())
                    continue
                candidate = self.flatten(); actual = self.export(candidate, audio=True)
                self.assertEqual(len(actual), len(expected))
                self.assertLess(max(abs(a-b) for a,b in zip(actual,expected)), 3e-8)

    def test_cropped_inner_time_stretch_refuses_resetting_nonstationary_processor_state(self):
        path = self.root/'chirp.wav'; values = array.array('h')
        for n in range(6*48000):
            t = n/48000
            for factor in (1, 1.19):
                value = .18*math.sin(2*math.pi*(170*factor*t+130*t*t)) + .35*(n % 11237 == 0)
                values.append(round(value*32767))
        if sys.byteorder != 'little': values.byteswap()
        with wave.open(str(path), 'wb') as stream:
            stream.setparams((2, 2, 48000, 0, 'NONE', '')); stream.writeframes(values.tobytes())
        self.media['chirp'] = {'id': 'chirp', 'path': str(path), 'duration': 6, 'has_audio': True,
                               'has_video': False, 'channels': 2, 'sample_rate': 48000}
        self.inner.update(in_=0, out=4, speed=2, audio={'linked': False})
        self.child['tracks'].append(self.track('A1', 1, [self.clip('sound', 'chirp', end=4,
            speed=2, audio={'maintain_pitch': True})], kind='audio'))
        self.outer.update(in_=.5, out=1.5, audio={'linked': True})
        # Independent full-source processor followed by output crop, retaining
        # the same WSOLA history as a child sequence. Direct input-crop-first
        # is not an equivalent operation on this nonstationary signal.
        raw = self.run_command(['ffmpeg', '-v', 'error', '-i', str(path), '-af',
            'aformat=sample_fmts=fltp:channel_layouts=stereo,atrim=end_sample=192000,asetpts=PTS-STARTPTS,'
            'atempo=2,atrim=start_sample=24000:end_sample=72000,asetpts=PTS-STARTPTS',
            '-acodec', 'pcm_f32le', '-f', 'f32le', '-'])
        expected = array.array('f'); expected.frombytes(raw)
        if sys.byteorder != 'little': expected.byteswap()
        before = self.export(self.project, audio=True)
        self.assertEqual(len(before), len(expected))
        self.assertEqual(len(before), 2*48000)
        self.assertLess(max(abs(a-b) for a,b in zip(before,expected)), 1e-7)
        result = self.plan(); self.assertFalse(result['ok']); self.assertFalse(result['ops'])
        self.assertTrue(any(i['code'] == 'audio_tempo_crop' for i in result['issues']), result['issues'])

    def test_outer_temporal_echo_requires_continuous_camera_cut_history(self):
        self.child['tracks'][0]['clips'] = [
            self.clip('first', 'a', end=1, audio={'linked': False}),
            self.clip('second', 'b', start=1, end=1, audio={'linked': False})]
        self.outer.update(in_=0, out=2, fx_stack=[{'type': 'echo', 'params': {'frames': 4}}])
        pixels = self.export(self.project)
        self.assertEqual(len(pixels), 20*64*48*3)
        # At the cut, temporal echo averages A frames7,8,9 and B frame0:
        # ([53,53,53]+[56,56,56]+[59,59,59]+[0,32,160])/4.
        self.assertEqual(list(pixels[10*64*48*3:10*64*48*3+3]), [42, 50, 82])
        result = self.plan(); self.assertFalse(result['ok']); self.assertFalse(result['ops'])
        self.assertTrue(any('processing' in i['code'] or 'effect' in i['code']
                            for i in result['issues']), result['issues'])

    def test_mixed_child_cadence_refuses_instead_of_silently_changing_frames(self):
        self.child['fps'] = 5; self.inner.update(in_=0, out=2)
        self.outer.update(in_=0, out=2)
        expected = [self.frame(2*(n//2)) for n in range(20)]
        self.assert_frames(self.export(self.project), expected)
        result = self.plan(); self.assertFalse(result['ok']); self.assertFalse(result['ops'])
        self.assertIn('sampl', json.dumps(result['issues']).lower())

    def test_reverse_accelerated_inner_refuses_picture_only_phase_mismatch(self):
        self.inner['speed'] = 2; self.outer['reverse'] = True
        self.assert_frames(self.export(self.project), [self.frame(n) for n in range(38, 19, -2)])
        result = self.plan(); self.assertFalse(result['ok']); self.assertFalse(result['ops'])
        self.assertIn('sampl', json.dumps(result['issues']).lower())

    def test_off_native_grid_slow_inner_refuses_first_frame_loss(self):
        self.inner['speed'] = .5
        expected = [12, 13, 13, 14, 14, 15, 15, 16, 16, 17]
        self.assert_frames(self.export(self.project), [self.frame(n) for n in expected])
        result = self.plan(); self.assertFalse(result['ok']); self.assertFalse(result['ops'])
        self.assertIn('sampl', json.dumps(result['issues']).lower())

    def tearDown(self):
        for path, digest in self.source_hashes.items():
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))


if __name__ == '__main__': unittest.main()
