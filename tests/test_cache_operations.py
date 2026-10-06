"""Real worker/async ownership and filesystem faults; no native HTTP/WebView.

The timer supports this host's restricted socket wakeup during executor exit.
No executor or filesystem work is replaced by an eager async adapter.
"""
import asyncio
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from link_fixture import directory_link
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import cache_operations as cache
import media_cache


def run(coroutine):
    def loop_factory():
        loop = asyncio.new_event_loop()
        def tick(): loop.call_later(.01, tick)
        loop.call_later(.01, tick)
        return loop
    with asyncio.Runner(loop_factory=loop_factory) as runner: return runner.run(coroutine)


async def until(predicate):
    for _ in range(1000):
        if predicate(): return
        await asyncio.sleep(.005)
    raise AssertionError('Observed worker did not reach the required state')


class Operations(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity cache É ')
        self.root = Path(self.temp.name)
        for name in ('proxies', 'thumbs/tpl', 'renders/cache', 'sfx'): (self.root/name).mkdir(parents=True)
        self.proxy = self.root/'proxies/a.mp4'; self.proxy.write_bytes(b'proxy')
        self.original = self.root/'original.mov'; self.original.write_bytes(b'original')
        self.project = self.root/'project.json'; self.project.write_bytes(b'{"saved":true}')

    def tearDown(self):
        self.assertIsNone(cache.activity(self.root)['cleanup_active'])
        self.assertEqual(self.original.read_bytes(), b'original')
        self.assertEqual(self.project.read_bytes(), b'{"saved":true}')
        self.temp.cleanup()

    def held_delete(self):
        entered, release = threading.Event(), threading.Event(); threads = []
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == self.proxy:
                threads.append(threading.get_ident()); entered.set()
                if not release.wait(5): raise RuntimeError('Fixture worker release timed out')
            return original(path, *args, **kwargs)
        return patch.object(Path, 'unlink', unlink), entered, release, threads

    def test_slow_delete_yields_event_loop_and_second_cleanup_is_rejected(self):
        hook, entered, release, threads = self.held_delete()
        async def scenario():
            owner = threading.get_ident(); task = asyncio.create_task(cache.clear(self.root, 'proxies'))
            try:
                await until(entered.is_set)
                for _ in range(5): await asyncio.sleep(.005)
                self.assertFalse(task.done()); self.assertNotEqual(threads, [owner])
                with self.assertRaises(cache.CacheBusy): await cache.clear(self.root, 'segments')
                self.assertEqual(cache.activity(self.root)['cleanup_active']['category'], 'proxies')
                with self.assertRaises(media_cache.CacheBusy):
                    with media_cache.cleaning(self.root): pass
            finally: release.set()
            result = await task; self.assertEqual(result['cleared'], ['proxies']); self.assertIsNone(result['cleanup_active'])
            self.assertNotIn('last_cleanup', result, 'Do not duplicate every removed filename in the POST response')
            self.assertEqual(cache.inspect(self.root)['last_cleanup']['id'], result['id'])
        with hook: run(scenario())

    def test_repeated_request_cancellation_waits_for_worker_and_keeps_receipt(self):
        hook, entered, release, _ = self.held_delete()
        async def scenario():
            task = asyncio.create_task(cache.clear(self.root, 'proxies'))
            try:
                await until(entered.is_set)
                task.cancel(); await asyncio.sleep(.02); task.cancel(); await asyncio.sleep(.02)
                self.assertFalse(task.done()); self.assertTrue(self.proxy.exists())
                with self.assertRaises(cache.CacheBusy): await cache.clear(self.root, 'all')
                with self.assertRaises(media_cache.CacheBusy): media_cache.clear(self.root, 'proxies')
            finally: release.set()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertFalse(self.proxy.exists())
            receipt = cache.inspect(self.root)['last_cleanup']
            self.assertEqual(receipt['reports'][0]['removed'], ['proxies/a.mp4'])
            self.assertEqual(receipt['reports'][0]['bytes_removed'], 5)
            self.assertEqual((await cache.clear(self.root, 'segments'))['cleared'], ['segments'])
        with hook: run(scenario())

    def test_size_failure_after_deletion_does_not_erase_the_result(self):
        with patch.object(cache, 'inspect', side_effect=PermissionError('scan denied')):
            result = run(cache.clear(self.root, 'proxies'))
        self.assertEqual(result['cleared'], ['proxies']); self.assertEqual(result['inventory_error'], 'scan denied')
        self.assertFalse(self.proxy.exists()); self.assertEqual(cache.inspect(self.root)['last_cleanup']['id'], result['id'])

    def test_partial_deletion_is_retained_as_partial_not_all_cleared(self):
        other = self.root/'proxies/b.mp4'; other.write_bytes(b'other')
        original = Path.unlink
        def unlink(path, *args, **kwargs):
            if path == self.proxy: raise PermissionError('sharing denied')
            return original(path, *args, **kwargs)
        with patch.object(Path, 'unlink', unlink): result = run(cache.clear(self.root, 'proxies'))
        self.assertEqual(result['cleared'], []); self.assertFalse(other.exists()); self.assertTrue(self.proxy.exists())
        receipt = cache.inspect(self.root)['last_cleanup']; self.assertEqual(receipt['reports'], result['reports'])
        self.assertEqual(receipt['reports'][0]['removed'], ['proxies/b.mp4'])

    def test_preparation_busy_and_worker_failure_retire_admission(self):
        with media_cache.preparing(self.root):
            with self.assertRaises(cache.CacheBusy): run(cache.clear(self.root, 'all'))
        self.assertEqual(cache.activity(self.root)['last_cleanup']['status'], 'not_started')
        with patch.object(cache, '_clear_and_scan', side_effect=RuntimeError('worker failed')):
            with self.assertRaisesRegex(RuntimeError, 'worker failed'): run(cache.clear(self.root, 'all'))
        self.assertTrue(self.proxy.exists()); self.assertEqual(run(cache.clear(self.root, 'proxies'))['cleared'], ['proxies'])

    def test_unexpected_later_failure_retains_prior_deletions_and_marks_incomplete(self):
        with patch.object(cache.segment_cache, 'cleanup', side_effect=RuntimeError('segment stage failed')):
            result = run(cache.clear(self.root, 'all'))
        self.assertEqual(result['status'], 'incomplete'); self.assertEqual(result['cleared'], ['proxies', 'thumbs'])
        self.assertEqual(result['reports'][0]['removed'], ['proxies/a.mp4'])
        self.assertFalse(self.proxy.exists()); self.assertEqual(cache.activity(self.root)['last_cleanup']['operation_error'], 'segment stage failed')

    def test_executor_submission_failure_does_not_leak_admission(self):
        async def scenario():
            with patch.object(asyncio.get_running_loop(), 'run_in_executor', side_effect=RuntimeError('executor stopped')):
                with self.assertRaisesRegex(RuntimeError, 'executor stopped'): await cache.clear(self.root, 'all')
            self.assertEqual((await cache.clear(self.root, 'proxies'))['cleared'], ['proxies'])
        run(scenario())

    def test_size_scan_is_bounded_and_busy_scan_does_not_undo_cleanup(self):
        entered, release = threading.Event(), threading.Event(); result = {}; original = os.walk
        def walk(path, *args, **kwargs):
            if Path(path) == self.root/'proxies':
                entered.set()
                if not release.wait(5): raise RuntimeError('scan fixture release timed out')
            yield from original(path, *args, **kwargs)
        def scan():
            try: result['value'] = cache.inspect(self.root)
            except BaseException as exc: result['error'] = exc
        with patch.object(os, 'walk', walk):
            thread = threading.Thread(target=scan); thread.start()
            try:
                self.assertTrue(entered.wait(2))
                with self.assertRaises(cache.CacheBusy): cache.inspect(self.root)
                cleared = run(cache.clear(self.root, 'proxies'))
                self.assertEqual(cleared['cleared'], ['proxies']); self.assertIn('already running', cleared['inventory_error'])
            finally: release.set(); thread.join(5)
        self.assertFalse(thread.is_alive()); self.assertNotIn('error', result)
        self.assertEqual(cache.inspect(self.root)['last_cleanup']['id'], cleared['id'])

    def test_counts_exclude_segment_subtree_and_include_template_thumbnails(self):
        (self.root/'renders/delivered.mp4').write_bytes(b'a'*200)
        (self.root/'renders/cache'/('a'*32+'.mp4')).write_bytes(b'b'*700)
        (self.root/'thumbs/tpl/template.png').write_bytes(b'abc')
        result = cache.inspect(self.root)
        self.assertEqual(result['size_reports']['renders']['bytes'], 200)
        self.assertEqual(result['size_reports']['segments']['bytes'], 700)
        self.assertEqual(result['size_reports']['thumbs']['bytes'], 3)
        self.assertTrue(all(row['complete'] for row in result['size_reports'].values()))

    def test_unreadable_walk_reports_unknown_not_zero_or_partial_total(self):
        original = os.walk
        def walk(path, *args, **kwargs):
            yield from original(path, *args, **kwargs)
            if Path(path) == self.root/'proxies': kwargs['onerror'](PermissionError('subfolder denied'))
        with patch.object(os, 'walk', walk): result = cache.inspect(self.root)
        self.assertIsNone(result['proxies_mb']); row = result['size_reports']['proxies']
        self.assertEqual(row['bytes'], 5); self.assertFalse(row['complete']); self.assertIn('subfolder denied', row['errors'][0])

    def test_never_created_cache_is_empty_but_non_directory_path_is_unknown(self):
        (self.root/'sfx').rmdir(); self.assertEqual(cache.inspect(self.root)['sfx_mb'], 0)
        (self.root/'sfx').write_bytes(b'file'); self.assertIsNone(cache.inspect(self.root)['sfx_mb'])

    def test_linked_cache_and_entries_are_not_measured_as_complete(self):
        target = self.root/'outside'; target.mkdir(); (target/'keep').write_bytes(b'untouched')
        directory_link(self.root/'proxies/link',target)
        result = cache.inspect(self.root); self.assertIsNone(result['proxies_mb'])
        self.assertEqual(result['size_reports']['proxies']['skipped'], ['proxies/link'])
        (self.root/'sfx').rmdir(); directory_link(self.root/'sfx',target)
        self.assertIsNone(cache.inspect(self.root)['sfx_mb']); self.assertEqual((target/'keep').read_bytes(), b'untouched')

    def test_receipt_is_copied_and_never_leaks_to_another_workspace(self):
        result = run(cache.clear(self.root, 'proxies')); result['reports'][0]['removed'].clear()
        self.assertEqual(cache.activity(self.root)['last_cleanup']['reports'][0]['removed'], ['proxies/a.mp4'])
        self.assertEqual(cache.activity(self.root/'other'), {'cleanup_active': None, 'last_cleanup': None})

    def test_folder_validation_fault_preserves_prior_category_receipts(self):
        (self.root/'thumbs/thumb.png').write_bytes(b'thumb')
        original = media_cache.linked
        def linked(path, *args):
            if path == self.root/'thumbs': raise PermissionError('thumb folder denied')
            return original(path, *args)
        with patch.object(media_cache, 'linked', linked): result = run(cache.clear(self.root, 'all'))
        self.assertIn('proxies', result['cleared']); self.assertNotIn('thumbs', result['cleared'])
        self.assertEqual(result['reports'][0]['removed'], ['proxies/a.mp4'])
        self.assertTrue(result['reports'][1]['errors']); self.assertTrue((self.root/'thumbs/thumb.png').exists())


if __name__ == '__main__': unittest.main()
