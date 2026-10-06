"""Real media checks for the repeatable Windows acceptance kit and inspector."""
import array
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

from acceptance_tools import digest, tools
from create_acceptance_media import generate
from inspect_acceptance_output import inspect, color_argument


class AcceptanceKit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='Filmocity QA É '); cls.root = Path(cls.temp.name)
        cls.selected = tools(); cls.kit = cls.root / 'kit'
        cls.report = generate(cls.kit, cls.selected, long_minutes=120)
        cls.project = json.loads((cls.kit / 'reference-project.json').read_text(encoding='utf-8'))
        cls.red = Path(cls.project['media']['A']['path'])
    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def test_manifest_binds_all_media_and_expected_paths_and_frames(self):
        self.assertEqual(self.report['status'], 'created')
        for item in self.report['files']: self.assertEqual(digest(self.kit / item['path']), item['sha256'])
        a, b = [Path(self.project['media'][key]['path']) for key in ('A','B')]
        self.assertEqual(a.name, b.name); self.assertNotEqual(a, b)
        self.assertIn("O'Neill", str(a)); self.assertIn('東京', str(b))
        self.assertNotEqual(digest(a), digest(b))

    def test_existing_directory_and_evidence_are_preserved(self):
        before = (self.kit / 'fixture-manifest.json').read_bytes()
        with self.assertRaises(FileExistsError): generate(self.kit, self.selected)
        self.assertEqual((self.kit / 'fixture-manifest.json').read_bytes(), before)
        report = self.root / 'existing.json'; report.write_text('preserve', encoding='utf-8')
        result = subprocess.run([sys.executable, str(Path(__file__).with_name('inspect_acceptance_output.py')), '--input', str(self.red), '--output', str(report),
            '--width','640','--height','360','--fps','30','--duration','6'], capture_output=True)
        self.assertNotEqual(result.returncode, 0); self.assertEqual(report.read_text(), 'preserve')

    def test_stereo_beeps_have_exact_sample_count_channels_and_silence(self):
        with wave.open(str(self.kit / 'Stereo beeps.wav'), 'rb') as stream:
            self.assertEqual((stream.getnchannels(),stream.getframerate(),stream.getnframes()), (2,48000,288000))
            samples = array.array('h', stream.readframes(stream.getnframes()))
        if sys.byteorder != 'little': samples.byteswap()
        self.assertTrue(any(samples[:9600])); self.assertFalse(any(samples[9600:96000]))
        self.assertTrue(all(abs(samples[i] - 2 * samples[i+1]) <= 1 for i in range(0,9600,2)))

    def test_color_audio_metadata_and_full_decode_pass_real_clip(self):
        result = inspect(self.red, self.selected, width=640,height=360,fps=30,duration=6,expect_audio=True,
                         colors=[color_argument('1:255,0,0')],full_decode=True)
        self.assertEqual(result['status'],'passed', result); self.assertTrue(result['full_decode'])
        self.assertEqual(result['sha256'],digest(self.red))

    def test_wrong_color_dimensions_and_duration_are_recorded_as_failures(self):
        result = inspect(self.red, self.selected, width=1080,height=1920,fps=60,duration=8,colors=[color_argument('1:0,0,255')])
        self.assertEqual(result['status'],'failed')
        self.assertEqual({c['name'] for c in result['checks'] if not c['pass']}, {'width','height','fps','duration','center_RGB_at_1.0'})

    def test_corrupt_media_cannot_pass(self):
        bad = self.root / 'corrupt.mp4'; bad.write_bytes(b'not a video')
        result = inspect(bad,self.selected,width=640,height=360,fps=30,duration=6)
        self.assertEqual(result['status'],'failed'); self.assertIn('error',result)

    def test_numbered_seek_clip_matches_all_six_color_regions(self):
        result = inspect(self.kit / 'Seek and sync.mkv',self.selected,width=640,height=360,fps=30,duration=6,
                         colors=[{'time':i+.5,'rgb':rgb} for i,rgb in enumerate(self.report['expected']['seek_colors_per_second'])])
        self.assertEqual(result['status'],'passed',result)

    def test_reference_story_build_has_exact_ranges_and_two_hour_fixture_is_valid(self):
        sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'backend'))
        import editing_workflow
        before = copy.deepcopy(self.project)
        result, reply = editing_workflow.build(self.project, {'action':'story','sequence':'qa-reference','words':[0,1,22,23],'padding':0})
        seq = editing_workflow.sequence(result, reply['sequence'])
        self.assertEqual(len(seq['tracks'][0]['clips']), 2)
        for clip, expected in zip(seq['tracks'][0]['clips'], [(0,.1,.85),(.75,5.1,5.85)]):
            for key, value in zip(('start','in_','out'), expected): self.assertAlmostEqual(clip[key],value,places=8)
        self.assertEqual(self.project,before)
        long = json.loads((self.kit / 'long-project.json').read_text(encoding='utf-8'))['sequences'][0]
        self.assertEqual(len(long['tracks'][0]['clips']),1200); self.assertEqual(len(long['transcript']),14400)
        self.assertEqual(long['tracks'][0]['clips'][-1]['start']+6,7200)

    def test_failed_generation_leaves_a_failed_receipt(self):
        selected = copy.deepcopy(self.selected); selected['ffmpeg']['path'] = str(self.root / 'missing-ffmpeg')
        output = self.root / 'failed'
        with self.assertRaises(OSError): generate(output,selected,long_minutes=1)
        report = json.loads((output / 'fixture-manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(report['status'],'failed'); self.assertIn('error',report)


if __name__ == '__main__': unittest.main()
