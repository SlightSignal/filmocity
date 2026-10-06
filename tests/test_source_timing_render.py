"""Independent decoded timing oracles for holds and directional step ramps."""
import array
import copy
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import render


class SourceTimingRender(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='filmocity-source-timing-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.serial = 0

    def run_command(self, command):
        result = subprocess.run(command, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr[-4000:].decode(errors='replace'))
        return result.stdout

    def project(self, media, clip, kind='video'):
        return {'id': 'p', 'name': 'Timing', 'media': media, 'sequences': [
            {'id': 's', 'name': 'S', 'width': 64, 'height': 48, 'fps': 10,
             'tracks': [{'id': 't', 'index': 0, 'kind': kind, 'clips': [clip]}]}]}

    def export(self, project, audio=False):
        self.serial += 1
        before = copy.deepcopy(project)
        output = self.root/f'export-{self.serial}.{ "wav" if audio else "mkv" }'
        preset = {'format': 'audio', 'acodec': 'wav_float'} if audio else {
            'format': 'matroska', 'vcodec': 'ffv1', 'acodec': 'pcm_s16le', 'color_processing': 'rgb'}
        command, graph = render.build_command(project, 's', str(output), preset)
        try:
            self.run_command(list(command))
        finally:
            command.close()
        self.assertEqual(project, before)
        (self.root/f'export-{self.serial}-graph.txt').write_text(graph)
        return output

    def picture(self):
        raw = self.root/'gray.rgb'
        raw.write_bytes(b''.join(bytes([32+4*n])*64*48*3 for n in range(40)))
        movie = self.root/'gray.mkv'
        self.run_command(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'rgb24',
                          '-video_size', '64x48', '-framerate', '10', '-i', str(raw), '-c:v', 'ffv1', '-pix_fmt', 'bgr0', str(movie)])
        return {'id': 'm', 'path': str(movie), 'duration': 4, 'fps': 10, 'frame_rate': '10/1',
                'has_video': True, 'has_audio': False, 'width': 64, 'height': 48}

    def frames(self, output):
        return self.run_command(['ffmpeg', '-v', 'error', '-i', str(output), '-map', '0:v:0', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])

    def test_hold_dissolve_keeps_frozen_picture_and_original_tail(self):
        media = self.picture()
        for alignment in ('end', 'center'):
            with self.subTest(alignment=alignment):
                clip = {'id': 'c', 'media_id': 'm', 'start': 2, 'in_': 2, 'out': 4, 'hold': True,
                        'transition_in': {'type': 'dissolve', 'duration': .6, 'align': alignment}}
                pixels = self.frames(self.export(self.project({'m': media}, clip)))
                self.assertEqual(len(pixels), 40*64*48*3)
                for frame in (25, 39):
                    self.assertEqual(pixels[frame*64*48*3:(frame+1)*64*48*3], bytes([112])*64*48*3)

    def test_held_interpreted_subclip_preserves_absolute_native_anchor(self):
        parent = self.picture()
        child = {**parent, 'id': 'sub', 'subclip_of': 'm', 'sub_in': 1, 'duration': 6, 'interpret_fps': 5}
        clip = {'id': 'c', 'media_id': 'sub', 'start': 2, 'in_': 3, 'out': 5, 'hold': True, 'reverse': True,
                'speed': 7, 'transition_in': {'type': 'dissolve', 'duration': .5}}
        pixels = self.frames(self.export(self.project({'m': parent, 'sub': child}, clip)))
        self.assertEqual(pixels[25*64*48*3:26*64*48*3], bytes([112])*64*48*3)

    def test_hold_at_source_zero_has_a_virtual_incoming_handle(self):
        media = self.picture()
        clip = {'id': 'c', 'media_id': 'm', 'start': 2, 'in_': 0, 'out': 2, 'hold': True,
                'transition_in': {'type': 'dissolve', 'duration': .5}}
        pixels = self.frames(self.export(self.project({'m': media}, clip)))
        stride = 64*48*3
        self.assertEqual(pixels[14*stride], 0)
        self.assertGreater(pixels[17*stride], 0)
        self.assertEqual(pixels[25*stride:26*stride], bytes([32])*stride)

    def test_reversed_interpreted_subclip_frames_match_independent_native_order(self):
        parent = self.picture()
        child = {**parent, 'id': 'sub', 'subclip_of': 'm', 'sub_in': 1, 'duration': 6, 'interpret_fps': 5}
        clip = {'id': 'c', 'media_id': 'sub', 'start': 0, 'in_': 1, 'out': 5, 'speed': 2, 'reverse': True}
        pixels = self.frames(self.export(self.project({'m': parent, 'sub': child}, clip)))
        stride = 64*48*3
        self.assertEqual(len(pixels), 20*stride)
        for timeline_frame in range(20):
            # Logical [1,5] plus subclip offset1 at factor2 selects native
            # frames10..29; 2x cancels interpretation, one source frame/output.
            self.assertEqual(pixels[timeline_frame*stride:(timeline_frame+1)*stride], bytes([32+4*(29-timeline_frame)])*stride)

    def pulse(self, interpreted=False, channels=6):
        source = self.root/f'pulse-{channels}.wav'
        duration, center = (6, 4.5) if interpreted else (4, 3.5)
        data = array.array('h')
        for n in range(duration*48000):
            data.extend([int(1500*math.sin(2*math.pi*(200+ch*80)*n/48000)) for ch in range(channels-1)] +
                        [12000 if round((center-.02)*48000) <= n < round((center+.02)*48000) else 0])
        if sys.byteorder != 'little': data.byteswap()
        with wave.open(str(source), 'wb') as stream:
            stream.setparams((channels, 2, 48000, 0, 'NONE', '')); stream.writeframes(data.tobytes())
        parent = {'id': 'p', 'path': str(source), 'duration': duration, 'fps': 30, 'frame_rate': '30/1',
                  'has_audio': True, 'has_video': False, 'channels': channels, 'sample_rate': 48000}
        alias = {**parent, 'id': 'a', 'subclip_of': 'p', 'sub_in': 2 if interpreted else 0,
                 'duration': 8 if interpreted else 4, 'audio_alias': {
                     'version': 1, 'source_media_id': 'p', 'physical_media_id': 'p', 'channel_index': channels-1}}
        if interpreted: alias['interpret_fps'] = 15
        return source, parent, alias

    def pcm(self, file):
        raw = self.run_command(['ffmpeg', '-v', 'error', '-i', str(file), '-map', '0:a:0', '-f', 'f32le', '-acodec', 'pcm_f32le', '-'])
        values = array.array('f'); values.frombytes(raw)
        if sys.byteorder != 'little': values.byteswap()
        return values

    def reverse_oracle(self, source, interpreted=False, pitch=False, channel=5):
        # Independent native windows, read in descending order: the opening
        # second traverses one native second, the final second traverses three.
        offset = 1 if interpreted else 0
        tempo = 'atempo=2,atempo=1.5' if pitch else 'asetrate=144000,aresample=48000'
        graph = (f'[0:a:0]pan=mono|c0=c{channel},asplit[a][b];'
                 f'[a]atrim=start_sample={(3+offset)*48000}:end_sample={(4+offset)*48000},asetpts=PTS-STARTPTS,areverse,apad=whole_len=48000,atrim=end_sample=48000[x];'
                 f'[b]atrim=start_sample={offset*48000}:end_sample={(3+offset)*48000},asetpts=PTS-STARTPTS,areverse,{tempo},apad=whole_len=48000,atrim=end_sample=48000[y];'
                 '[x][y]concat=n=2:v=0:a=1,pan=stereo|c0=c0|c1=c0[out]')
        output = self.root/f'oracle-{interpreted}-{pitch}.wav'
        self.run_command(['ffmpeg', '-v', 'error', '-y', '-i', str(source), '-filter_complex', graph, '-map', '[out]', '-c:a', 'pcm_f32le', str(output)])
        return self.pcm(output)

    def reverse_case(self, interpreted=False, pitch=False, channel_alias=True):
        source, parent, alias = self.pulse(interpreted, 6 if channel_alias else 1)
        factor = 2 if interpreted else 1
        begin = 1 if interpreted else 0
        if interpreted: alias['sub_in'] = 1
        clip = {'id': 'c', 'media_id': 'a' if channel_alias else 'p', 'start': 0, 'in_': begin, 'out': begin+4*factor,
                'reverse': True, 'time_remap': [{'t': 0, 'v': factor, 'e': 'hold'}, {'t': 1, 'v': 3*factor}],
                'audio': {'maintain_pitch': pitch}}
        self.assertEqual(render.clip_dur(clip), 2)
        values = self.pcm(self.export(self.project({'p': parent, 'a': alias}, clip, 'audio'), True))
        expected = self.reverse_oracle(source, interpreted, pitch, 5 if channel_alias else 0)
        self.assertEqual(len(values), 2*48000*2)
        self.assertEqual(values, expected)
        times = [i/48000 for i, sample in enumerate(values[::2]) if abs(sample) > .2]
        self.assertAlmostEqual((times[0]+times[-1]+1/48000)/2, .5, places=5)

    def test_reverse_step_ramp_keeps_timeline_profile_and_selected_channel(self):
        self.reverse_case()

    def test_reverse_step_ramp_with_interpretation_and_subclip_offset(self):
        self.reverse_case(interpreted=True)

    def test_reverse_step_ramp_pitch_preservation_uses_same_source_windows(self):
        self.reverse_case(interpreted=True, pitch=True)

    def test_reverse_step_ramp_without_channel_alias(self):
        self.reverse_case(channel_alias=False)

    def test_forward_step_ramp_keeps_forward_profile(self):
        _, parent, alias = self.pulse()
        clip = {'id': 'c', 'media_id': 'a', 'start': 0, 'in_': 0, 'out': 4,
                'time_remap': [{'t': 0, 'v': 1, 'e': 'hold'}, {'t': 1, 'v': 3}],
                'audio': {'maintain_pitch': False}}
        values = self.pcm(self.export(self.project({'p': parent, 'a': alias}, clip, 'audio'), True))
        times = [i/48000 for i, sample in enumerate(values[::2]) if abs(sample) > .2]
        self.assertEqual(len(values), 2*48000*2)
        self.assertAlmostEqual((times[0]+times[-1]+1/48000)/2, 1+(3.5-1)/3, places=4)


if __name__ == '__main__':
    unittest.main()
