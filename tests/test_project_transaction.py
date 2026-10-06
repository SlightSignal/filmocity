"""Real filesystem failures/crash boundaries; no framework or HTTP substitutes."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import threading
import subprocess
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'backend'))
import project_transaction as tx
from project_history import changes_between, apply_changes, HistoryConflict


class Crash(BaseException):
    pass


class TransactionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity commit É's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.project = self.root / 'project.json'; self.history = self.root / 'undo_stack.json'
        self.project.write_bytes(b'old project'); self.history.write_bytes(b'old history')

    def commit(self, publish=None):
        return tx.commit_pair(self.project, b'new project', b'new history', publish or (lambda: tx.write_atomic(self.project, b'new project')))

    def assert_old(self):
        self.assertEqual(self.project.read_bytes(), b'old project')
        self.assertEqual(self.history.read_bytes(), b'old history')

    def crash_after_project(self):
        def publish():
            tx.write_atomic(self.project, b'new project'); raise Crash()
        with self.assertRaises(Crash): self.commit(publish)

    def test_success_publishes_both_and_retires_journal(self):
        self.assertEqual(self.commit(), '')
        self.assertEqual(self.project.read_bytes(), b'new project'); self.assertEqual(self.history.read_bytes(), b'new history')
        self.assertFalse((self.root / tx.JOURNAL).exists())
        self.assertFalse(list(self.root.glob('.commit-*')))

    def test_real_process_exit_recovers_each_publication_boundary(self):
        script = '''
import os, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import project_transaction as tx
root, phase = Path(sys.argv[2]), sys.argv[3]
writer = tx.write_atomic
def write(path, raw):
    writer(path, raw)
    name = Path(path).name
    if phase == 'history' and name == 'undo_stack.json': os._exit(72)
    if phase == 'committed' and name == tx.JOURNAL and b'"phase":"committed"' in raw: os._exit(72)
tx.write_atomic = write
def publish():
    writer(root / 'project.json', b'new project')
    if phase == 'project': os._exit(72)
tx.commit_pair(root / 'project.json', b'new project', b'new history', publish)
'''
        for phase in ('project', 'history', 'committed'):
            with self.subTest(phase=phase):
                self.project.write_bytes(b'old project'); self.history.write_bytes(b'old history')
                result = subprocess.run([sys.executable, '-c', script, str(Path(tx.__file__).parent), str(self.root), phase], timeout=10)
                self.assertEqual(result.returncode, 72)
                tx.recover_transaction(self.project)
                if phase == 'committed':
                    self.assertEqual(self.project.read_bytes(), b'new project'); self.assertEqual(self.history.read_bytes(), b'new history')
                else: self.assert_old()

    def test_prepare_failure_never_calls_publisher(self):
        with patch.object(tx, 'write_atomic', side_effect=OSError('full disk')):
            with self.assertRaises(OSError): self.commit(lambda: self.fail('must prepare first'))
        self.assert_old()

    def test_history_failure_rolls_back_project_and_preserves_uncommitted_copy(self):
        writer = tx.write_atomic
        def fail(path, raw):
            if Path(path) == self.history and raw == b'new history': raise OSError('history denied')
            return writer(path, raw)
        with patch.object(tx, 'write_atomic', side_effect=fail):
            with self.assertRaisesRegex(OSError, 'history denied'): self.commit()
        self.assert_old(); self.assertFalse((self.root / tx.JOURNAL).exists())
        archive = next((self.root / 'recovery').iterdir())
        self.assertEqual((archive / 'editor.json').read_bytes(), b'new project')
        self.assertEqual((archive / 'project.json').read_bytes(), b'old project')
        self.assertTrue((archive / 'transaction.json').exists())

    def test_crash_after_project_is_rolled_back_before_a_following_commit(self):
        self.crash_after_project(); self.assertEqual(tx.recover_transaction(self.project), 'prepared')
        self.assert_old(); self.commit(); self.assertEqual(self.history.read_bytes(), b'new history')

    def test_crash_after_both_files_but_before_marker_rolls_back_both(self):
        writer = tx.write_atomic
        def fail(path, raw):
            if Path(path).name == tx.JOURNAL and json.loads(raw)['phase'] == 'committed': raise Crash()
            return writer(path, raw)
        with patch.object(tx, 'write_atomic', side_effect=fail), self.assertRaises(Crash): self.commit()
        self.assertEqual(self.history.read_bytes(), b'new history')
        tx.recover_transaction(self.project); self.assert_old()

    def test_crash_after_commit_marker_keeps_new_pair(self):
        writer = tx.write_atomic
        def fail(path, raw):
            writer(path, raw)
            if Path(path).name == tx.JOURNAL and json.loads(raw)['phase'] == 'committed': raise Crash()
        with patch.object(tx, 'write_atomic', side_effect=fail), self.assertRaises(Crash): self.commit()
        self.assertEqual(tx.recover_transaction(self.project), 'committed')
        self.assertEqual(self.project.read_bytes(), b'new project'); self.assertEqual(self.history.read_bytes(), b'new history')

    def test_failure_after_commit_marker_reports_saved_and_does_not_roll_back(self):
        writer = tx.write_atomic
        def fail(path, raw):
            writer(path, raw)
            if Path(path).name == tx.JOURNAL and json.loads(raw)['phase'] == 'committed': raise OSError('directory flush denied')
        with patch.object(tx, 'write_atomic', side_effect=fail): warning = self.commit()
        self.assertIn('saved', warning)
        self.assertEqual(self.project.read_bytes(), b'new project'); self.assertEqual(self.history.read_bytes(), b'new history')
        tx.recover_transaction(self.project)

    def test_rollback_failure_keeps_journal_and_recovers_on_next_attempt(self):
        writer = tx.write_atomic
        def fail(path, raw):
            if Path(path) == self.history or (Path(path) == self.project and raw == b'old project'): raise OSError('sharing denied')
            return writer(path, raw)
        with patch.object(tx, 'write_atomic', side_effect=fail), self.assertRaises(tx.TransactionRecoveryRequired): self.commit()
        self.assertTrue((self.root / tx.JOURNAL).exists())
        tx.recover_transaction(self.project); self.assert_old()

    def test_missing_original_history_is_removed_on_rollback(self):
        self.history.unlink(); writer = tx.write_atomic
        def fail(path, raw):
            if Path(path).name == tx.JOURNAL and json.loads(raw)['phase'] == 'committed': raise Crash()
            return writer(path, raw)
        with patch.object(tx, 'write_atomic', side_effect=fail), self.assertRaises(Crash): self.commit()
        tx.recover_transaction(self.project)
        self.assertFalse(self.history.exists()); self.assertEqual(self.project.read_bytes(), b'old project')

    def test_unknown_bytes_are_not_overwritten_and_both_files_are_inspected_first(self):
        self.crash_after_project(); self.history.write_bytes(b'external history')
        with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.project)
        self.assertEqual(self.project.read_bytes(), b'new project'); self.assertEqual(self.history.read_bytes(), b'external history')
        self.assertTrue((self.root / tx.JOURNAL).exists())

    def test_corrupt_journal_is_retained_without_writes(self):
        self.crash_after_project(); path = self.root / tx.JOURNAL; data = json.loads(path.read_bytes())
        data['files']['project.json']['before']['sha256'] = 'wrong'; path.write_text(json.dumps(data))
        with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.project)
        self.assertEqual(self.project.read_bytes(), b'new project'); self.assertTrue(path.exists())

    def test_concurrent_reader_waits_instead_of_rolling_back_live_writer(self):
        entered = threading.Event(); release = threading.Event(); read_finished = threading.Event(); errors = []
        def publish():
            tx.write_atomic(self.project, b'new project'); entered.set()
            if not release.wait(3): raise RuntimeError('test deadline')
        def write():
            try: self.commit(publish)
            except Exception as error: errors.append(error)
        def read():
            try: tx.recover_transaction(self.project); read_finished.set()
            except Exception as error: errors.append(error)
        writer = threading.Thread(target=write); writer.start(); self.assertTrue(entered.wait(3))
        reader = threading.Thread(target=read); reader.start()
        try: self.assertFalse(read_finished.wait(.05))
        finally: release.set(); writer.join(3); reader.join(3)
        self.assertFalse(writer.is_alive()); self.assertFalse(reader.is_alive()); self.assertEqual(errors, [])
        self.assertEqual(self.project.read_bytes(), b'new project')

    def test_windows_sharing_retry_is_bounded_and_preserves_previous_file(self):
        replace = tx.os.replace; denied = PermissionError('sharing'); denied.winerror = 32; calls = []
        def busy(src, dst):
            calls.append(src)
            self.assertEqual(self.project.read_bytes(), b'old project')
            if len(calls) < 3: raise denied
            return replace(src, dst)
        with patch.object(tx.os, 'replace', side_effect=busy), patch.object(tx.time, 'sleep'):
            tx.write_atomic(self.project, b'new project')
        self.assertEqual(len(calls), 3)
        with patch.object(tx.os, 'replace', side_effect=denied) as mocked, patch.object(tx.time, 'sleep'):
            with self.assertRaises(PermissionError): tx.write_atomic(self.project, b'later')
        self.assertEqual(mocked.call_count, 6); self.assertEqual(self.project.read_bytes(), b'new project')
        self.assertFalse(list(self.root.glob('.commit-*')))


class ExactHistoryTests(unittest.TestCase):
    def document(self):
        return {'id': 'p', 'updated': 1, 'sequences': [{'id': 's', 'tracks': [{'id': 'V1', 'clips': [
            {'id': 'c', 'start': 0, 'out': 5, 'note': None}, {'id': 'd', 'start': 5, 'out': 10}]}]}]}

    def test_added_removed_null_and_nested_values_round_trip_without_merging_leftovers(self):
        before = self.document(); after = copy.deepcopy(before); clip = after['sequences'][0]['tracks'][0]['clips'][0]
        clip['color'] = {'saturation': .5}; del clip['note']; clip['out'] = 3; after['updated'] = 2
        changes = changes_between(before, after)
        restored = apply_changes(after, changes, 'undo'); restored['updated'] = before['updated']
        self.assertEqual(restored, before)
        redone = apply_changes(before, changes, 'redo'); redone['updated'] = after['updated']
        self.assertEqual(redone, after)

    def test_insertions_reordering_and_normalized_splits_round_trip_in_order(self):
        before = self.document(); after = copy.deepcopy(before)
        after['sequences'][0]['tracks'][0]['clips'] = [{'id': 'split', 'start': 2}, {'id': 'c', 'start': 0}]
        changes = changes_between(before, after)
        self.assertEqual(apply_changes(after, changes, 'undo'), before)
        self.assertEqual(apply_changes(before, changes, 'redo'), after)

    def test_changed_value_or_shifted_identity_conflicts_without_partial_application(self):
        before = self.document(); after = copy.deepcopy(before); after['name'] = 'Cut'
        after['sequences'][0]['tracks'][0]['clips'][0]['out'] = 3
        changes = changes_between(before, after)
        for target in ['value', 'identity', 'sequence']:
            with self.subTest(target=target):
                current = copy.deepcopy(after); clip = current['sequences'][0]['tracks'][0]['clips'][0]
                if target == 'value': clip['out'] = 4
                elif target == 'identity': clip['id'] = 'another-clip'
                else: current['sequences'][0]['id'] = 'another-sequence'
                original = copy.deepcopy(current)
                with self.assertRaises(HistoryConflict): apply_changes(current, changes, 'undo')
                self.assertEqual(current, original)

    def test_unrelated_changes_survive_undo(self):
        before = self.document(); after = copy.deepcopy(before); after['name'] = 'New'
        changes = changes_between(before, after); after['unrelated'] = 'keep'
        result = apply_changes(after, changes, 'undo')
        self.assertEqual(result['unrelated'], 'keep'); self.assertNotIn('name', result)

    def test_json_types_and_keys_with_slashes_are_exact(self):
        before = {'enabled': 1, 'a/b~c': None}; after = {'enabled': True, 'a/b~c': []}
        changes = changes_between(before, after); self.assertEqual(len(changes), 2)
        result = apply_changes(after, changes, 'undo')
        self.assertIs(type(result['enabled']), int); self.assertEqual(result, before)


if __name__ == '__main__': unittest.main(verbosity=2)
