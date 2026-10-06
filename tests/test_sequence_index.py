"""Indexed slices must retain the previous renderer's exact timeline semantics."""
import copy
import hashlib
import importlib.util
import json
from pathlib import Path
import random
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render
from render_context import RenderContext
from PIL import Image

spec = importlib.util.spec_from_file_location('old_chunks', ROOT / 'benchmarks/baselines/chunk-sequence-before.py')
baseline = importlib.util.module_from_spec(spec)
spec.loader.exec_module(baseline)


def sequence(clips):
    return {'id': 's', 'width': 64, 'height': 64, 'fps': 24, 'captions': [], 'markers': [],
            'tracks': [{'id': 'V1', 'index': 1, 'kind': 'video', 'clips': clips}]}


class SequenceIndexTests(unittest.TestCase):
    def compare(self, seq, windows):
        before = copy.deepcopy(seq)
        index = render.index_sequence(seq)
        for start, end in windows:
            with self.subTest(start=start, end=end):
                self.assertEqual(render.chunk_sequence(seq, start, end, index=index), baseline.chunk_sequence(seq, start, end))
        self.assertEqual(seq, before)

    def test_random_overlapping_unsorted_tracks_and_captions_keep_exact_values(self):
        rng = random.Random(4921)
        seq = sequence([])
        for i in range(200):
            clip = {'id': str(i), 'start': rng.uniform(0, 80), 'in_': 1, 'out': rng.uniform(2, 20),
                    'speed': rng.choice([.5, 1, 2]), 'enabled': i % 3 != 0,
                    'keyframes': {'transform.x': [{'t': 0, 'v': 0, 'e': 'bezier', 'o': [.4, 2]}, {'t': 5, 'v': 10}]},
                    'audio': {'fade_in': 1, 'fade_out': 1}, 'transition_in': {'duration': 1}}
            if i % 7 == 0: clip['hold'] = True
            if i % 9 == 0: clip['reverse'] = True
            if i % 11 == 0: clip['time_remap'] = [{'t': 0, 'v': .5}, {'t': 2, 'v': 2}]
            seq['tracks'][0]['clips'].append(clip)
        seq['tracks'].append({'id': 'A1', 'kind': 'audio', 'muted': True, 'index': 1,
                              'clips': copy.deepcopy(seq['tracks'][0]['clips'][::3])})
        seq['captions'] = [{'id': str(i), 'start': i / 3, 'end': i / 3 + 2, 'text': 'É'} for i in range(200, -1, -1)]
        self.compare(seq, [(x, x + 3.75) for x in range(0, 100, 2)])

    def test_all_historical_projects_keep_slice_contents(self):
        for path in sorted((ROOT / 'tests').glob('sample_project*.json')):
            project = json.loads(path.read_text())
            for seq in project.get('sequences', []):
                duration = render.seq_total(seq)
                with self.subTest(project=path.name, sequence=seq['id']):
                    self.compare(seq, [(0, max(duration, 1)), (duration / 3, duration / 3 + 1), (duration + 1, duration + 2)])

    def test_half_open_boundaries_and_original_layer_order(self):
        seq = sequence([{'id': str(i), 'start': start, 'in_': 0, 'out': end-start}
                        for i, (start, end) in enumerate([(3, 4), (0, 10), (1, 2), (2, 3), (2-1e-7, 2+1e-7)])])
        self.compare(seq, [(0, 1), (1, 2), (2, 3), (2+1e-6, 3-1e-6), (8, 9), (10, 11)])
        result = render.chunk_sequence(seq, 2, 3)
        self.assertEqual([c['id'] for c in result['tracks'][0]['clips']], ['1', '3'])

    def test_output_and_snapshot_have_no_mutable_aliases(self):
        seq = sequence([{'id': 'c', 'start': 0, 'in_': 0, 'out': 3, 'graphic': {'layers': [{'text': 'keep'}]}}])
        seq['captions'] = [{'start': 0, 'end': 3, 'style': {'colors': ['red']}}]
        index = render.index_sequence(seq)
        expected = render.chunk_sequence(seq, 0, 1, index=index)
        changed = render.chunk_sequence(seq, 0, 1, index=index)
        changed['tracks'][0]['clips'][0]['graphic']['layers'][0]['text'] = 'changed'
        changed['captions'][0]['style']['colors'].append('blue')
        self.assertEqual(render.chunk_sequence(seq, 0, 1, index=index), expected)
        self.assertEqual(seq['captions'][0]['style']['colors'], ['red'])
        # The captured snapshot is deliberately fixed for the duration of a job.
        seq['tracks'][0]['clips'][0]['graphic']['layers'][0]['text'] = 'later'
        self.assertEqual(render.chunk_sequence(seq, 0, 1, index=index), expected)
        self.assertNotEqual(render.chunk_sequence(seq, 0, 1), expected)
        with self.assertRaises(ValueError): render.chunk_sequence(copy.deepcopy(seq), 0, 1, index=index)

    def test_duration_work_scales_with_selected_clips_instead_of_all_pairs(self):
        seq = sequence([{'id': str(i), 'start': i*6, 'in_': 0, 'out': 6} for i in range(1200)])
        with mock.patch.object(render, 'clip_dur', wraps=render.clip_dur) as duration:
            index = render.index_sequence(seq)
            for i in range(1200):
                result = render.chunk_sequence(seq, i*6, (i+1)*6, index=index)
                self.assertEqual(len(result['tracks'][0]['clips']), 1)
            self.assertEqual(duration.call_count, 2400)

    def test_cache_keys_and_real_pixels_match_previous_slice(self):
        with tempfile.TemporaryDirectory(prefix="filmocity-index-é's-") as folder:
            folder = Path(folder)
            source = folder / 'source.png'; Image.new('RGB', (64, 64), (30, 160, 80)).save(source)
            source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
            seq = sequence([{'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 3,
                             'color': {'saturation': .8}, 'transform': {'opacity': .7}}])
            project = {'id': 'p', 'media': {'m': {'id': 'm', 'path': str(source), 'is_image': True,
                       'has_video': True, 'has_audio': False, 'width': 64, 'height': 64}}, 'sequences': [seq]}
            old, new = baseline.chunk_sequence(seq, 1, 2), render.chunk_sequence(seq, 1, 2)
            self.assertEqual(render.chunk_key(project, old, {}), render.chunk_key(project, new, {}))
            pixels = []
            for i, chunk in enumerate((old, new)):
                out = folder / f'{i}.png'
                with RenderContext(scratch_parent=str(folder), stall_timeout=15) as context:
                    render.render_frame(dict(project, sequences=[chunk]), 's', .25, str(out), context=context)
                with Image.open(out) as image: pixels.append(image.convert('RGB').tobytes())
            self.assertEqual(pixels[0], pixels[1])
            self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(), source_hash)


if __name__ == '__main__': unittest.main(verbosity=2)
