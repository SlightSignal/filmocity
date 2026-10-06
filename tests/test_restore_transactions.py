"""Explicit restore crash/restart boundaries using the real store, files and child processes."""
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
from unittest.mock import patch

from test_project_recovery import RecoveryFixture, HTTPError, ROOT
import project_recovery as recovery
import project_transaction as tx


class Crash(BaseException): pass


# Run the production restore and project writer without requiring HTTP/framework dependencies.
CHILD = '''
import ast, json, os, shutil, sys, time
from pathlib import Path
sys.path.insert(0, str(Path(sys.argv[1]) / 'backend'))
import project_recovery as recovery
import project_transaction as tx
root, boundary = Path(sys.argv[2]), sys.argv[3]
project = root / 'project.json'; history = root / 'undo_stack.json'; journal = root / tx.JOURNAL
old_project = project.read_bytes() if project.exists() else None
old_history = history.read_bytes() if history.exists() else None
old_journal = journal.read_bytes() if journal.exists() else None
source = ast.parse((Path(sys.argv[1]) / 'backend/server.py').read_text())
node = next(n for n in source.body if isinstance(n, ast.FunctionDef) and n.name == 'save_project')
node.decorator_list = []
env = dict(json=json, os=os, shutil=shutil, time=time, SCHEMA=3)
exec(compile(ast.Module(body=[node], type_ignores=[]), 'backend/server.py', 'exec'), env)
writer = tx.write_atomic

def write(path, raw):
    name = Path(path).name
    if name == tx.JOURNAL and raw != old_journal:
        record = json.loads(raw)
        if record['phase'] == 'committed' and boundary.startswith('rollback_'):
            raise OSError('failure before commit marker')
    writer(path, raw)
    if name == 'undo_stack.json':
        if boundary == 'history' and raw != old_history: os._exit(73)
        if boundary == 'rollback_history' and raw == old_history: os._exit(73)
    if name == 'project.json' and boundary == 'rollback_project' and raw == old_project: os._exit(73)
    if name == tx.JOURNAL:
        if raw == old_journal:
            if boundary == 'rollback_journal': os._exit(73)
        else:
            record = json.loads(raw)
            if boundary == record['phase']: os._exit(73)

tx.write_atomic = write
preserve = recovery._write_exclusive

def archive(path, raw):
    preserve(path, raw)
    if boundary == 'preserved' and Path(path).name == 'manifest.json': os._exit(73)
recovery._write_exclusive = archive
unlink = tx.os.unlink

def retire(path, *args, **kwargs):
    unlink(path, *args, **kwargs)
    if boundary == 'retired' and Path(path) == journal: os._exit(73)
tx.os.unlink = retire

def save(document, path, **kwargs):
    env['save_project'](document, path, **kwargs)
    if boundary == 'project': os._exit(73)
report = recovery.inspect_recovery(project)
selected = next(item for item in report['candidates'] if item['id'] == 'pending')
recovery.restore_version(project, 'pending', selected['sha256'], report['current_sha256'], save)
'''


class RestoreTransactionTests(RecoveryFixture):
    def initial(self, previous_journal=None):
        self.write(self.current, {**self.project, 'name': 'Before restore'})
        self.write(self.pending(), {**self.project, 'name': 'Chosen restore'})
        self.history = self.write(self.current.parent / 'undo_stack.json', raw=b'{"undo":[{"reason":"old edit"}],"redo":[]}')
        self.journal = self.current.parent / tx.JOURNAL
        if previous_journal is not None: self.journal.write_bytes(previous_journal)
        return self.current.read_bytes(), self.history.read_bytes(), previous_journal
    def assert_original(self, before):
        for path, raw in zip([self.current, self.history, self.journal], before):
            self.assertEqual(path.read_bytes() if path.exists() else None, raw)
    def crash_restore(self):
        writer = self.env['save_project']
        def save(*args, **kwargs): writer(*args, **kwargs); raise Crash()
        with patch.dict(self.env, save_project=save), self.assertRaises(Crash): self.restore()
    def test_real_process_exit_recovers_every_publication_boundary_with_and_without_old_journal(self):
        base = self.current.parent
        for previous in [None, b'unreadable older transaction']:
            for boundary in ['preserved', 'prepared', 'project', 'history', 'committed', 'retired']:
                with self.subTest(previous=previous, boundary=boundary):
                    self.current = base / (('old-' if previous else 'new-') + boundary) / 'project.json'
                    self.current.parent.mkdir(); before = self.initial(previous)
                    result = subprocess.run([sys.executable, '-c', CHILD, str(ROOT), str(self.current.parent), boundary], capture_output=True, timeout=15)
                    self.assertEqual(result.returncode, 73, result.stderr.decode())
                    if previous is not None and boundary not in ('committed', 'retired'):
                        with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.current)
                    else: tx.recover_transaction(self.current)
                    if boundary in ('committed', 'retired'):
                        self.assertEqual(json.loads(self.current.read_bytes())['name'], 'Chosen restore')
                        self.assertEqual(json.loads(self.history.read_bytes()), {'undo': [], 'redo': []}); self.assertFalse(self.journal.exists())
                    else: self.assert_original(before)
                    # The preexisting evidence is in a verified archive even if the child died after publication.
                    manifests = list((self.current.parent / 'recovery').glob('*/manifest.json')); self.assertTrue(manifests)
                    for manifest in manifests:
                        for name, record in json.loads(manifest.read_bytes())['preserved'].items():
                            self.assertEqual(recovery.fingerprint((manifest.parent / (name + '.json')).read_bytes()), record['sha256'])
    def test_real_process_exit_during_rollback_resumes_without_losing_older_evidence(self):
        base = self.current.parent
        for boundary in ['rollback_history', 'rollback_project', 'rollback_journal']:
            with self.subTest(boundary=boundary):
                self.current = base / boundary / 'project.json'; self.current.parent.mkdir(); before = self.initial(b'older journal bytes')
                result = subprocess.run([sys.executable, '-c', CHILD, str(ROOT), str(self.current.parent), boundary], capture_output=True, timeout=15)
                self.assertEqual(result.returncode, 73, result.stderr.decode())
                with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.current)
                self.assert_original(before)
    def test_missing_original_project_and_history_are_restored_as_missing_without_an_empty_default(self):
        self.initial(); self.current.unlink(); self.history.unlink(); self.crash_restore()
        tx.recover_transaction(self.current)
        self.assertFalse(self.current.exists()); self.assertFalse(self.history.exists()); self.assertFalse(self.journal.exists())
        with self.assertRaises(recovery.ProjectRecoveryRequired): self.env['load_project']()
        self.assertTrue(any(c['name'] == 'Chosen restore' for c in recovery.inspect_recovery(self.current)['candidates']))
    def test_changed_project_history_or_journal_during_preservation_prevents_publication(self):
        for name in ['project.json', 'undo_stack.json', tx.JOURNAL]:
            with self.subTest(name=name):
                self.initial(b'previous journal'); before = {p.name: p.read_bytes() for p in [self.current, self.history, self.journal]}
                preserve = recovery._write_exclusive
                def interference(path, raw):
                    preserve(path, raw)
                    if Path(path).name == 'manifest.json': (self.current.parent / name).write_bytes(b'external change')
                with patch.object(recovery, '_write_exclusive', side_effect=interference), self.assertRaises(recovery.RecoveryConflict): self.restore()
                for file, raw in before.items(): self.assertEqual((self.current.parent / file).read_bytes(), b'external change' if file == name else raw)
    def test_prepare_write_failures_preserve_the_previous_pair_and_journal(self):
        for after_replace in [False, True]:
            with self.subTest(after_replace=after_replace):
                before = self.initial(b'old transaction'); writer = tx.write_atomic
                def fail(path, raw):
                    if Path(path) == self.journal:
                        if after_replace: writer(path, raw)
                        raise OSError('prepare flush failed')
                    writer(path, raw)
                with patch.object(tx, 'write_atomic', side_effect=fail), self.assertRaises(recovery.RecoveryError): self.restore()
                if after_replace:
                    with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.current)
                self.assert_original(before)
    def test_valid_older_edit_journal_remains_recoverable_after_restore_rollback(self):
        before = self.initial(); edited = json.dumps({**self.project, 'name': 'Interrupted earlier edit'}).encode()
        def publish(): tx.write_atomic(self.current, edited); raise Crash()
        with self.assertRaises(Crash): tx.commit_pair(self.current, edited, b'{"undo":[],"redo":[]}', publish)
        older = self.journal.read_bytes(); self.crash_restore()
        with self.assertRaisesRegex(tx.TransactionRecoveryRequired, 'previous transaction was reinstated'): tx.recover_transaction(self.current)
        self.assertEqual(self.journal.read_bytes(), older); self.assertEqual(self.current.read_bytes(), edited)
        self.assertEqual(tx.recover_transaction(self.current), 'prepared'); self.assert_original(before)
        names = {c['name'] for c in recovery.inspect_recovery(self.current)['candidates']}
        self.assertTrue({'Chosen restore', 'Interrupted earlier edit', 'Before restore'} <= names)
    def test_unknown_post_crash_bytes_are_never_overwritten_by_automatic_rollback(self):
        self.initial(); self.crash_restore(); self.history.write_bytes(b'new external history')
        current, journal = self.current.read_bytes(), self.journal.read_bytes()
        with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.current)
        self.assertEqual(self.current.read_bytes(), current); self.assertEqual(self.history.read_bytes(), b'new external history'); self.assertEqual(self.journal.read_bytes(), journal)
    def test_explicit_recovery_can_supersede_a_failed_restore_and_retains_its_journal_on_failure(self):
        self.initial(b'old evidence'); self.crash_restore(); older = self.journal.read_bytes()
        self.current.write_bytes(b'external partial project'); self.write(self.pending(), {**self.project, 'name': 'Retry choice'})
        before = (self.current.read_bytes(), self.history.read_bytes(), older)
        with patch.dict(self.env, save_project=lambda *a, **k: (_ for _ in ()).throw(OSError('write denied'))), self.assertRaises(recovery.RecoveryError): self.restore()
        self.assert_original(before)
        with self.assertRaises(tx.TransactionRecoveryRequired): self.env['load_project']()
        result = self.restore(); self.assertTrue(result['ok']); self.assertFalse(self.journal.exists())
        self.assertEqual(self.env['load_project']()['name'], 'Retry choice'); self.assertEqual(json.loads(self.history.read_bytes()), {'undo': [], 'redo': []})
        self.assertEqual((Path(result['preserved']) / 'transaction.json').read_bytes(), older)
    def test_failure_after_project_publication_restores_original_project_and_history(self):
        before = self.initial(); writer = self.env['save_project']
        def save(*a, **k): writer(*a, **k); raise OSError('late writer failure')
        with patch.dict(self.env, save_project=save), self.assertRaises(recovery.RecoveryError): self.restore()
        self.assert_original(before)
    def test_committed_cleanup_failure_reports_success_and_retains_new_pair_on_reopen(self):
        self.initial(b'old evidence'); unlink = tx.os.unlink
        def locked(path, *a, **k):
            if Path(path) == self.journal: raise PermissionError('scanner holds journal')
            unlink(path, *a, **k)
        with patch.object(tx.os, 'unlink', side_effect=locked): result = self.restore()
        self.assertTrue(result['ok']); self.assertIn('cleanup', result['warning'])
        self.assertEqual(json.loads(self.journal.read_bytes())['phase'], 'committed')
        self.assertEqual(self.env['load_project']()['name'], 'Chosen restore'); self.assertFalse(self.journal.exists())
        self.assertEqual(json.loads(self.history.read_bytes()), {'undo': [], 'redo': []})
    def test_late_commit_marker_flush_failure_is_a_committed_warning(self):
        self.initial(b'old evidence'); writer = tx.write_atomic
        def fail(path, raw):
            writer(path, raw)
            if Path(path) == self.journal and json.loads(raw)['phase'] == 'committed': raise OSError('directory flush failed')
        with patch.object(tx, 'write_atomic', side_effect=fail): result = self.restore()
        self.assertTrue(result['ok']); self.assertIn('directory flush failed', result['warning'])
        self.assertEqual(self.env['load_project']()['name'], 'Chosen restore'); self.assertEqual(json.loads(self.history.read_bytes()), {'undo': [], 'redo': []})
    def test_rollback_denial_retains_a_verifiable_journal_until_a_later_attempt(self):
        before = self.initial(); writer = tx.write_atomic
        def fail(path, raw):
            if Path(path) == self.history or (Path(path) == self.current and raw == before[0]): raise PermissionError('sharing denial')
            writer(path, raw)
        with patch.object(tx, 'write_atomic', side_effect=fail), self.assertRaises(recovery.RecoveryError): self.restore()
        record = json.loads(self.journal.read_bytes()); self.assertEqual(record['phase'], 'prepared')
        tx.recover_transaction(self.current); self.assert_original(before)
    def test_unreadable_superseded_journal_checksum_stops_automatic_recovery(self):
        self.initial(b'corrupt prior journal'); self.crash_restore(); record = json.loads(self.journal.read_bytes())
        record['previous_journal']['sha256'] = 'tampered'; self.journal.write_text(json.dumps(record)); before = self.current.read_bytes()
        with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.current)
        self.assertEqual(self.current.read_bytes(), before); self.assertTrue(self.journal.exists())
    def test_malformed_restore_headers_require_recovery_without_touching_the_pair(self):
        self.initial(b'old journal'); self.crash_restore(); original = json.loads(self.journal.read_bytes())
        before = (self.current.read_bytes(), self.history.read_bytes())
        for field, value in [('version', 1), ('version', True), ('kind', 'edit'), ('created', float('nan')),
                             ('created', float('inf')), ('created', True), ('created', 10 ** 1000), ('phase', 'unknown')]:
            with self.subTest(field=field, value=str(value)[:20]):
                data = {**original, field: value}; raw = json.dumps(data).encode(); self.journal.write_bytes(raw)
                with self.assertRaises(tx.TransactionRecoveryRequired): tx.recover_transaction(self.current)
                self.assertEqual((self.current.read_bytes(), self.history.read_bytes()), before); self.assertEqual(self.journal.read_bytes(), raw)
    def test_concurrent_store_reader_waits_until_the_restore_commit_finishes(self):
        self.initial(); entered, release, finished = threading.Event(), threading.Event(), threading.Event(); errors = []
        writer = self.env['save_project']
        def save(*a, **k):
            writer(*a, **k); entered.set()
            if not release.wait(3): raise OSError('test deadline')
        def write():
            try: self.restore()
            except Exception as error: errors.append(error)
        def read():
            try: tx.recover_transaction(self.current); finished.set()
            except Exception as error: errors.append(error)
        with patch.dict(self.env, save_project=save):
            thread = threading.Thread(target=write); thread.start(); self.assertTrue(entered.wait(3)); reader = threading.Thread(target=read); reader.start()
            try: self.assertFalse(finished.wait(.05))
            finally: release.set(); thread.join(3); reader.join(3)
        self.assertEqual(errors, []); self.assertFalse(thread.is_alive()); self.assertFalse(reader.is_alive()); self.assertTrue(finished.is_set())
        self.assertEqual(json.loads(self.current.read_bytes())['name'], 'Chosen restore')
    def test_route_retains_both_cleanup_and_secondary_history_warnings(self):
        self.initial(); report = self.env['project_recovery_versions'](); candidate = next(c for c in report['candidates'] if c['id'] == 'pending')
        class Request:
            async def json(self): return {'project': 'p', 'candidate': candidate['id'], 'sha256': candidate['sha256'], 'current_sha256': report['current_sha256']}
        async def broadcast(event): pass
        restore = recovery.restore_version
        def warning(*a, **k): return {**restore(*a, **k), 'warning': 'Committed; cleanup pending'}
        with patch.dict(self.env, restore_version=warning, broadcast=broadcast, log_event=lambda *a, **k: (_ for _ in ()).throw(OSError('history denied'))):
            result = asyncio.run(self.env['project_recovery_restore'](Request()))
        self.assertTrue(result['ok']); self.assertIn('cleanup pending', result['warning']); self.assertIn('history denied', result['warning'])


from test_project_sync import ProjectStoreFixture


class RestoreThenEditTests(ProjectStoreFixture):
    def test_restored_legacy_project_reopens_edits_and_undoes_without_replaying_old_history(self):
        self.edit('Before recovery', self.current())
        path = self.root / 'projects/a/project.json'; backup = path.parent / 'backups/project_1.json'; backup.parent.mkdir(exist_ok=True)
        backup.write_text(json.dumps({**self.doc, 'version': 1, 'name': 'Chosen legacy project'}))
        report = recovery.inspect_recovery(path); chosen = next(c for c in report['candidates'] if c['id'] == 'backups/project_1.json')
        result = recovery.restore_version(path, chosen['id'], chosen['sha256'], report['current_sha256'], self.env['save_project'])
        self.assertTrue(result['ok']); self.assertEqual(self.env['read_undo_history']('a'), {'undo': [], 'redo': []})
        reopened = self.env['load_project'](); self.assertEqual(reopened['version'], 3); self.assertEqual(reopened['name'], 'Chosen legacy project')
        self.edit('After recovery', self.current()); self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)
        self.invoke('undo', {'_context': self.current()}); undone = self.env['load_project']()
        for project in (undone, reopened): project.pop('updated', None)
        self.assertEqual(undone, reopened)
        self.invoke('redo', {'_context': self.current()}); self.assertEqual(self.env['load_project']()['name'], 'After recovery')


if __name__ == '__main__': unittest.main(verbosity=2)
