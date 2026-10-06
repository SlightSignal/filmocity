"""Independent frame-label vectors shared with the frontend, plus interchange contracts."""
import copy
import json
from pathlib import Path
import sys
import unittest
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from timeline_time import (frame_rate, frame_range, display_frame, format_frames, format_timecode,
                           parse_timecode, to_frames, from_frames)
from interchange import to_edl, to_fcp7_xml, from_fcp7_xml
from project_recovery import parse_project, RecoveryError
from project_history import changes_between, apply_changes


class TimecodeContract(unittest.TestCase):
    def setUp(self):
        self.vectors = json.loads((ROOT / 'tests/fixtures/timecode.json').read_text())
        self.seq = {'id': 's', 'name': 'Timing', 'width': 640, 'height': 360, 'fps': 29.97, 'timecode_format': 'df',
                    'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [
                        {'id': 'c', 'media_id': 'm', 'start': 60.06, 'in_': 0, 'out': 60.06}]}]}
        self.project = {'version': 3, 'sequences': [self.seq], 'media': {'m': {'name': 'Camera', 'path': '/Camera.mov',
            'duration': 200, 'has_video': True, 'has_audio': False, 'width': 640, 'height': 360}}}

    def test_independent_golden_labels_and_display_round_trip(self):
        for v in self.vectors['cases']:
            with self.subTest(v=v):
                self.assertEqual(format_frames(v['frames'], v['fps'], v['mode']), v['label'])
                self.assertEqual(to_frames(parse_timecode(v['label'], v['fps']), v['fps']), v['frames'])
                self.assertEqual(format_timecode(from_frames(v['frames'], v['fps']), v['fps'], v['mode']), v['label'])

    def test_invalid_skipped_and_malformed_labels_rejected(self):
        for v in self.vectors['invalid']:
            with self.subTest(v=v), self.assertRaises(ValueError): parse_timecode(v['text'], v['fps'])
        for value in (-1, 0.5, float('inf'), float('nan'), True):
            with self.assertRaises(ValueError): format_frames(value, 30)

    def test_display_containing_frame_differs_from_boundary_quantization(self):
        self.assertEqual(display_frame(.999, 30), 29)
        self.assertEqual(to_frames(.999, 30), 30)
        self.assertEqual(parse_timecode(' .00125 ', 29.97), .00125)
        self.assertEqual(parse_timecode('-1.25', 24), -1.25)
        self.assertEqual(to_frames(parse_timecode('01:00:00:00', 29.97), 29.97), 108000)
        self.assertEqual(to_frames(parse_timecode('01:00:00;00', 29.97), 29.97), 107892)

    def test_minute_boundaries_have_no_lost_or_duplicate_frames(self):
        for fps in (29.97, 59.94):
            for start in (1750, 3500, 17950, 35940, 107850, 215740):
                for frame in range(start, start + 100):
                    self.assertEqual(to_frames(parse_timecode(format_frames(frame, fps, 'df'), fps), fps), frame)

    def test_export_intervals_are_exclusive_quantized_and_nonempty(self):
        self.assertEqual(frame_range(.1, .4, 24), (2, 10))
        for start, end in ((None, 1), (True, 2), (0, float('inf')), (-1, 1), (1, 1), (0, .001)):
            with self.assertRaises(ValueError): frame_range(start, end, 29.97)

    def test_edl_header_and_cut_labels_follow_sequence_mode(self):
        edl = to_edl(self.project, 's')
        self.assertIn('FCM: DROP FRAME', edl)
        self.assertIn('00:00:00;00 00:01:00;02 00:01:00;02 00:02:00;04', edl)
        self.seq.pop('timecode_format')
        self.assertIn('FCM: NON-DROP FRAME', to_edl(self.project, 's'))
        self.assertIn('00:00:00:00 00:01:00:00 00:01:00:00 00:02:00:00', to_edl(self.project, 's'))
        with self.assertRaisesRegex(ValueError, 'track not found'): to_edl(self.project, 's', 'missing')

    def test_zero_origin_xml_preserves_df_preference_and_frame_positions(self):
        xml = to_fcp7_xml(self.project, 's')
        self.assertEqual(ET.fromstring(xml).findtext('sequence/timecode/displayformat'), 'DF')
        seq = from_fcp7_xml(xml, lambda *_: 'm')[0]
        self.assertEqual(seq['timecode_format'], 'df')
        self.assertEqual(seq['tracks'][0]['clips'][0]['start'], 60.06)

    def test_xml_rejects_unsupported_df_rate_and_nonzero_sequence_origin_explicitly(self):
        xml = to_fcp7_xml(self.project, 's')
        for replacement in ('<string>01:00:00;00</string>', '<frame>1</frame>'):
            bad = xml.replace('<string>00:00:00;00</string>', replacement) if replacement.startswith('<string>') else xml.replace('<frame>0</frame>', replacement)
            with self.assertRaisesRegex(ValueError, 'Nonzero sequence start'): from_fcp7_xml(bad, lambda *_: 'm')
        self.seq['fps'] = 59.94
        with self.assertRaisesRegex(ValueError, 'only at 29.97'): to_fcp7_xml(self.project, 's')

    def test_project_recovery_preserves_valid_preferences_and_rejects_invalid_combinations(self):
        for rate, mode in ((29.97, 'df'), (59.94, 'df'), (23.976, 'ndf')):
            self.seq.update(fps=rate, timecode_format=mode)
            self.assertEqual(parse_project(json.dumps(self.project).encode()), self.project)
        for rate, mode in ((24, 'df'), (30, 'df'), (29.97, 'DF'), (29.97, None)):
            self.seq.update(fps=rate, timecode_format=mode)
            with self.assertRaises(RecoveryError): parse_project(json.dumps(self.project).encode())

    def test_history_round_trip_changes_display_only(self):
        before = copy.deepcopy(self.project)
        self.seq['timecode_format'] = 'ndf'
        changes = changes_between(before, self.project)
        restored = apply_changes(self.project, changes, 'undo')
        self.assertEqual(restored, before)
        self.assertEqual(apply_changes(restored, changes, 'redo'), self.project)


if __name__ == '__main__': unittest.main()
