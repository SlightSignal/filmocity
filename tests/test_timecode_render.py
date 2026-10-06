"""Real FFmpeg frame-label, rate, In/Out and audio checks; no server/browser mocks."""
import array
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render as engine
from render_context import RenderContext


class RenderTimecode(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity timecode É ')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.width, self.height = 640, 360
        still = self.root / 'black.png'
        Image.new('RGB', (self.width, self.height)).save(still)
        self.seq = {'id': 's', 'name': 'Timing', 'width': self.width, 'height': self.height, 'fps': 29.97,
                    'timecode_format': 'df', 'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1,
                    'clips': [{'id': 'c', 'media_id': 'black', 'start': 0, 'in_': 0, 'out': 1}]}]}
        self.project = {'media': {'black': {'id': 'black', 'name': 'Black', 'path': str(still), 'is_image': True,
            'has_video': True, 'has_audio': False, 'width': self.width, 'height': self.height, 'duration': 1}}, 'sequences': [self.seq]}

    def command(self, args):
        result = subprocess.run(args, capture_output=True, timeout=60)
        self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace')[-2500:])
        return result.stdout

    def render(self, name, preset=None):
        with RenderContext(scratch_parent=str(self.root), stall_timeout=30) as context:
            return engine.render(self.project, 's', str(self.root / name),
                {'vcodec': 'ffv1', 'acodec': 'pcm_s16le', 'burn_tc': True, **(preset or {})}, context=context)

    def frames(self, path):
        raw = self.command(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
        size = self.width * self.height * 3
        self.assertEqual(len(raw) % size, 0)
        return [raw[i:i + size] for i in range(0, len(raw), size)]

    def expected(self, label):
        # Literal expected text, independently specified below, instead of a TC counter.
        text = self.root / 'expected.txt'; text.write_text(label)
        filt = (f"format=rgba,drawtext=textfile='{engine.ffpath(text)}':expansion=none:fontsize=9:fontcolor=white:box=1:boxcolor=black@0.6:boxborderw=8:"
                f"x=(w-text_w)/2:y=h-text_h-10:fontfile='{engine.ffpath(engine.bundled_font(mono=True))}',format=rgb24")
        return self.command(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color=black:s=640x360:r=30', '-vf', filt,
                             '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])

    def test_short_full_render_and_in_out_select_identical_frames_at_fractional_rate(self):
        full = self.render('full.mkv')
        info = json.loads(self.command(['ffprobe', '-v', 'error', '-show_streams', '-of', 'json', full]))
        video = next(s for s in info['streams'] if s['codec_type'] == 'video')
        self.assertEqual(video['r_frame_rate'], '30000/1001')
        frames = self.frames(full); self.assertEqual(len(frames), 30)
        self.assertEqual(frames[0], self.expected('00:00:00;00'))
        self.assertEqual(frames[29], self.expected('00:00:00;29'))
        self.seq.update(in_point=1001/30000, out_point=4*1001/30000)
        selected = self.frames(self.render('selected.mkv', {'range': True}))
        self.assertEqual(len(selected), 3)
        self.assertEqual(selected, frames[1:4])

    def test_2997_df_crosses_the_minute_without_missing_or_repeated_picture_frames(self):
        self.seq['tracks'][0]['clips'][0]['out'] = 1803*1001/30000
        self.seq.update(in_point=1799*1001/30000, out_point=1803*1001/30000)
        frames = self.frames(self.render('minute.mkv', {'range': True}))
        self.assertEqual(len(frames), 4)
        for actual, label in zip(frames, ('00:00:59;29', '00:01:00;02', '00:01:00;03', '00:01:00;04')):
            self.assertEqual(actual, self.expected(label), label)

    def test_5994_df_uses_four_skipped_labels_at_the_minute_boundary(self):
        self.seq['fps'] = 59.94
        self.seq['tracks'][0]['clips'][0]['out'] = 3603*1001/60000
        self.seq.update(in_point=3599*1001/60000, out_point=3603*1001/60000)
        frames = self.frames(self.render('minute60.mkv', {'range': True}))
        self.assertEqual(len(frames), 4)
        for actual, label in zip(frames, ('00:00:59;59', '00:01:00;04', '00:01:00;05', '00:01:00;06')):
            self.assertEqual(actual, self.expected(label), label)

    def test_ndf_counts_frames_instead_of_wall_clock_seconds(self):
        self.seq.update(fps=23.976, timecode_format='ndf', in_point=1439*1001/24000, out_point=1442*1001/24000)
        self.seq['tracks'][0]['clips'][0]['out'] = self.seq['out_point']
        frames = self.frames(self.render('ndf.mkv', {'range': True}))
        self.assertEqual(len(frames), 3)
        for actual, label in zip(frames, ('00:00:59:23', '00:01:00:00', '00:01:00:01')):
            self.assertEqual(actual, self.expected(label), label)

    def test_range_audio_matches_full_render_at_the_selected_sample_boundaries(self):
        sound = self.root / 'sample index.wav'
        samples = array.array('h', ((i % 1000) * 16 - 8000 for i in range(48000)))
        if sys.byteorder != 'little': samples.byteswap()
        with wave.open(str(sound), 'wb') as out:
            out.setparams((1, 2, 48000, 0, 'NONE', '')); out.writeframes(samples.tobytes())
        self.project['media']['sound'] = {'id': 'sound', 'path': str(sound), 'has_audio': True, 'has_video': False, 'duration': 1}
        self.seq['tracks'].append({'id': 'A1', 'kind': 'audio', 'index': 1, 'clips': [{'id': 'a', 'media_id': 'sound', 'start': 0, 'in_': 0, 'out': 1}]})
        def audio(path): return self.command(['ffmpeg', '-v', 'error', '-i', path, '-map', '0:a:0', '-f', 's16le', '-ac', '2', '-ar', '48000', '-'])
        full = audio(self.render('sound.mkv', {'burn_tc': False}))
        self.seq.update(in_point=1001/30000, out_point=4*1001/30000)
        selected = audio(self.render('sound-range.mkv', {'range': True, 'burn_tc': False}))
        # Nearest 48k sample positions for video frames 1 and 4: 1602 and 6406.
        self.assertEqual(selected, full[1602*4:6406*4])

    def test_numbered_frames_use_the_same_exclusive_in_out_and_burned_labels(self):
        self.seq.update(in_point=1001/30000, out_point=4*1001/30000)
        folder = Path(self.render('numbered.png', {'range': True, 'format': 'png_sequence'}))
        frames = sorted(folder.glob('frame_*.png'))
        self.assertEqual(len(frames), 3)
        for index, frame in enumerate(frames, 1):
            with Image.open(frame) as picture:
                self.assertEqual(picture.convert('RGB').tobytes(), self.expected(f'00:00:00;{index:02d}'))


if __name__ == '__main__': unittest.main()
