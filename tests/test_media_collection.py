"""Original-media collection: real files, failure injection and endpoint logic.

The store/route functions are compiled directly from production source to keep
these filesystem tests runnable without the optional FastAPI runtime. This does
not establish framework, browser, or native Windows acceptance.
"""
import ast
import asyncio
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import media_collection as mc


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


def run_async_check(coroutine):
    async def checked():
        loop = asyncio.get_running_loop()
        # Keep a timer alive through executor shutdown. Some restricted hosts do
        # not wake the selector for cross-thread notifications; real workers and
        # assertions still run, and the coroutine has a bounded deadline.
        def tick(): loop.call_later(0.02, tick)
        tick()
        return await asyncio.wait_for(coroutine, 10)
    return asyncio.run(checked())


class CollectionFixture(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='filmocity collection ')
        self.root = Path(self.temporary.name)
        self.destination = self.root / 'collected'

    def tearDown(self):
        self.temporary.cleanup()

    def source(self, name, data):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def project(self, sources):
        return {'version': 3, 'id': 'p', 'name': 'Collection', 'sequences': [],
                'media': {key: {'id': key, 'name': key, 'path': str(source)} for key, source in sources.items()}}

    def collect(self, project):
        with mc.MediaCollection(project, self.destination) as collection:
            collection.publish()
            return collection.project, collection.result()

    def assert_no_staging(self):
        self.assertFalse(list(self.destination.glob('.collect-*')))


class CollectionTests(CollectionFixture):
    def test_distinct_same_named_sources_never_merge_or_overwrite_old_collections(self):
        a = self.source('camera A/shot.mov', b'camera A content')
        b = self.source('camera B/shot.mov', b'camera B content')
        old = self.source('collected/shot.mov', b'existing collected file')
        original = self.project({'a': a, 'b': b}); unchanged = copy.deepcopy(original)
        project, result = self.collect(original)
        self.assertNotEqual(project['media']['a']['path'], project['media']['b']['path'])
        for key, source in [('a', a), ('b', b)]:
            self.assertEqual(Path(project['media'][key]['path']).read_bytes(), source.read_bytes())
        self.assertEqual(old.read_bytes(), b'existing collected file')
        self.assertEqual(original, unchanged)
        self.assertEqual((result['copied'], result['verified']), (2, 2))
        manifest = json.loads(Path(result['manifest']).read_text(encoding='utf-8'))
        for item in manifest['files']:
            self.assertEqual(hashlib.sha256(Path(item['destination']).read_bytes()).hexdigest(), item['sha256'])
        self.assert_no_staging()

    def test_unicode_spaces_and_windows_reserved_names_have_portable_destinations(self):
        source = self.source('Émilie clips/CON.mov', b'clip')
        project, _ = self.collect(self.project({'a': source}))
        target = Path(project['media']['a']['path'])
        self.assertEqual(target.name, '_CON.mov')
        self.assertEqual(target.read_bytes(), b'clip')
        self.assertEqual(mc._portable_name('clip:take?.mov '), 'clip_take_.mov')

    def test_duplicate_references_share_a_verified_copy_and_subclips_follow_the_original(self):
        source = self.source('source/original.mkv', b'frames')
        project = self.project({'a': source, 'b': source, 'sub': source, 'nested': source})
        project['media']['sub'].update(subclip_of='a', sub_in=2, duration=3)
        project['media']['nested'].update(subclip_of='sub', sub_in=1, duration=1)
        result, receipt = self.collect(project)
        self.assertEqual(len({m['path'] for m in result['media'].values()}), 1)
        self.assertEqual(receipt['copied'], 1)
        self.assertEqual(result['media']['sub']['sub_in'], 2)
        self.assertEqual(result['media']['nested']['subclip_of'], 'sub')

    def test_numbered_sequences_retain_frame_start_and_input_options(self):
        for i in range(7, 10): self.source(f'stills/take{i:04d}.png', f'frame {i}'.encode())
        project = self.project({'seq': self.root / 'stills/take%04d.png'})
        project['media']['seq'].update(sequence_frames=3, input_opts=['-framerate', '24', '-start_number', '7'])
        result, receipt = self.collect(project)
        media = result['media']['seq']
        for i in range(7, 10): self.assertEqual(Path(media['path'] % i).read_bytes(), f'frame {i}'.encode())
        self.assertEqual(media['input_opts'], project['media']['seq']['input_opts'])
        self.assertEqual(receipt['copied'], 3)

    def test_missing_numbered_frame_or_original_refuses_the_whole_collection(self):
        source = self.source('source/real.mov', b'real')
        for missing in [self.root / 'missing.mov', self.root / 'frame%04d.png']:
            project = self.project({'good': source, 'bad': missing})
            if '%' in str(missing): project['media']['bad']['sequence_frames'] = 2
            snapshot = copy.deepcopy(project)
            with self.assertRaises(mc.MediaCollectionError): self.collect(project)
            self.assertEqual(project, snapshot)
            self.assertEqual(source.read_bytes(), b'real')
            self.assertFalse(self.destination.exists())

    def test_empty_media_is_not_admitted(self):
        source = self.source('empty.mov', b'')
        with self.assertRaises(mc.MediaCollectionError): self.collect(self.project({'a': source}))
        self.assertFalse(self.destination.exists())

    def test_synthetic_media_needs_no_file_and_is_not_relinked(self):
        project = self.project({})
        project['media']['black'] = {'id': 'black', 'synthetic': {'kind': 'black'}}
        result, receipt = self.collect(project)
        self.assertEqual(result, project)
        self.assertEqual(receipt['verified'], 0)

    def test_missing_and_cyclic_subclip_parents_are_reported(self):
        source = self.source('clip.mov', b'clip')
        for parent in ['missing', 'sub']:
            project = self.project({'sub': source}); project['media']['sub']['subclip_of'] = parent
            with self.assertRaisesRegex(mc.MediaCollectionError, 'missing or cyclic'): self.collect(project)
        self.assertFalse(self.destination.exists())

    def test_collecting_an_already_collected_project_reuses_files(self):
        source = self.source('clip.mov', b'clip')
        first, _ = self.collect(self.project({'a': source}))
        second, receipt = self.collect(first)
        self.assertEqual(second, first)
        self.assertEqual((receipt['copied'], receipt['reused'], receipt['verified']), (0, 1, 1))

    def test_copy_failure_keeps_originals_and_cleans_owned_staging_only(self):
        source = self.source('clip.mov', b'clip')
        previous = self.source('collected/keep.mov', b'keep')
        with patch.object(mc.os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaisesRegex(mc.MediaCollectionError, 'disk full'): self.collect(self.project({'a': source}))
        self.assertEqual(source.read_bytes(), b'clip')
        self.assertEqual(previous.read_bytes(), b'keep')
        self.assert_no_staging()
        self.assertEqual(list(self.destination.iterdir()), [previous])

    def test_corrupted_copy_is_refused(self):
        source = self.source('clip.mov', b'clip')
        with patch.object(mc, '_digest', return_value='wrong'):
            with self.assertRaisesRegex(mc.MediaCollectionError, 'verification'): self.collect(self.project({'a': source}))
        self.assert_no_staging()

    def test_source_modification_during_collection_is_refused(self):
        source = self.source('clip.mov', b'clip')
        digest = mc._digest
        def changed(target):
            result = digest(target)
            source.write_bytes(b'changed source bytes')
            return result
        with patch.object(mc, '_digest', side_effect=changed):
            with self.assertRaisesRegex(mc.MediaCollectionError, 'changed during'): self.collect(self.project({'a': source}))
        self.assert_no_staging()

    def test_published_files_survive_later_failure_for_project_recovery(self):
        source = self.source('clip.mov', b'clip')
        with self.assertRaisesRegex(OSError, 'project save'):
            with mc.MediaCollection(self.project({'a': source}), self.destination) as collection:
                collection.publish(); target = Path(collection.project['media']['a']['path'])
                raise OSError('project save failed')
        self.assertEqual(target.read_bytes(), b'clip')
        self.assertTrue((collection.published / 'media/manifest.json').is_file())
        self.assert_no_staging()

    def test_publication_failure_removes_staging_and_preserves_old_media(self):
        source = self.source('clip.mov', b'clip')
        old = self.source('collected/existing.mov', b'previous')
        with patch.object(mc.os, 'rename', side_effect=PermissionError('sharing denial')):
            with self.assertRaises(PermissionError): self.collect(self.project({'a': source}))
        self.assertEqual(list(self.destination.iterdir()), [old])

    def test_real_videos_with_identical_names_decode_identically_after_originals_are_removed(self):
        if not shutil.which('ffmpeg'): self.skipTest('FFmpeg is required for real-media acceptance')
        paths = {}
        for key, color in [('a', 'red'), ('b', 'blue')]:
            paths[key] = self.root / key / 'shot.mkv'; paths[key].parent.mkdir()
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', f'color={color}:s=32x24:r=10', '-t', '0.3', '-c:v', 'ffv1', str(paths[key])], check=True, capture_output=True)
        def decoded(path):
            return subprocess.check_output(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'hash', '-hash', 'sha256', '-'])
        hashes = {key: decoded(path) for key, path in paths.items()}
        self.assertNotEqual(hashes['a'], hashes['b'])
        project, _ = self.collect(self.project(paths))
        for path in paths.values(): path.unlink()
        for key, digest in hashes.items(): self.assertEqual(decoded(project['media'][key]['path']), digest)


# Guarded task/commit coverage lives in test_collection_workflow.py.

if __name__ == '__main__':
    unittest.main(verbosity=2)
