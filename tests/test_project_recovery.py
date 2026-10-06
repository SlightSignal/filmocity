"""Disk recovery and save integrity using real files and production store/route code."""
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import project_recovery as recovery
import project_transaction as transaction
from project_transaction import recover_transaction, commit_pair, TransactionRecoveryRequired
from project_history import changes_between, apply_changes, HistoryConflict
from project_sync import workspace_id


class HTTPError(Exception):
    def __init__(self, status_code, detail):
        self.status_code, self.detail = status_code, detail
        super().__init__(detail)


class RecoveryFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='filmocity recovery É ')
        self.root = Path(self.temp.name)
        self.current = self.root / 'projects/p/project.json'
        self.current.parent.mkdir(parents=True)
        self.project = json.loads((ROOT / 'tests/sample_project.json').read_text(encoding='utf-8'))
        self.project.update(name='Saved project', version=3)
        self.events = []
        self.env = {'os': os, 'json': json, 'time': time, 'shutil': shutil, 'uuid': uuid, 'SCHEMA': 3,
            'ROOT': str(self.root), 'workspace_id': workspace_id, 'LOCK': threading.Lock(), 'Request': object, 'HTTPException': HTTPError,
            'P': lambda *parts: str(self.root.joinpath(*parts)), 'PP': lambda *parts: str(self.current.parent.joinpath(*parts)),
            'active_id': lambda: 'p', 'default_project': lambda: copy.deepcopy(self.project),
            'log_event': lambda event, project_id=None: self.events.append((event, project_id))}
        self.env.update(recover_transaction=recover_transaction, commit_pair=commit_pair,
                        TransactionRecoveryRequired=TransactionRecoveryRequired,
                        changes_between=changes_between, apply_changes=apply_changes, HistoryConflict=HistoryConflict)
        for name in ['ProjectRecoveryRequired', 'RecoveryError', 'RecoveryConflict', 'parse_project', 'has_recovery_files', 'inspect_recovery', 'restore_version', 'preserve_editor_draft']:
            self.env[name] = getattr(recovery, name)
        tree = ast.parse((ROOT / 'backend/server.py').read_text(encoding='utf-8'))
        names = {'load_project', 'save_project', 'migrate_project', 'project_recovery_versions', 'project_recovery_restore'}
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    def tearDown(self): self.temp.cleanup()

    def write(self, path, project=None, raw=None):
        path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw if raw is not None else json.dumps(project or self.project, ensure_ascii=False).encode('utf-8'))
        return path

    def pending(self): return Path(str(self.current) + '.tmp')

    def candidate(self, kind='pending'):
        return next(item for item in recovery.inspect_recovery(self.current)['candidates'] if item['id'] == kind)

    def restore(self, kind='pending', editor=None):
        report = recovery.inspect_recovery(self.current); selected = self.candidate(kind)
        return recovery.restore_version(self.current, kind, selected['sha256'], report['current_sha256'], self.env['save_project'], editor)


class RecoveryTests(RecoveryFixture):
    def test_corrupt_project_can_be_restored_from_complete_pending_save(self):
        self.write(self.current, raw=b'{"partial":')
        self.write(self.pending())
        report = recovery.inspect_recovery(self.current)
        self.assertFalse(report['current_valid']); self.assertEqual(len(report['candidates']), 1)
        result = self.restore()
        self.assertEqual(json.loads(self.current.read_text(encoding='utf-8'))['name'], 'Saved project')
        archive = Path(result['preserved'])
        self.assertEqual((archive / 'project.json').read_bytes(), b'{"partial":')
        self.assertEqual(recovery.parse_project((archive / 'pending.json').read_bytes())['name'], 'Saved project')
        self.assertTrue((archive / 'manifest.json').is_file())

    def test_missing_project_with_pending_save_is_not_replaced_with_an_empty_project(self):
        self.write(self.pending())
        original = self.pending().read_bytes()
        with self.assertRaises(recovery.ProjectRecoveryRequired): self.env['load_project']()
        self.assertFalse(self.current.exists()); self.assertEqual(self.pending().read_bytes(), original)
        self.restore(); self.assertEqual(self.env['load_project']()['name'], 'Saved project')

    def test_new_library_still_creates_a_default_project(self):
        project = self.env['load_project']()
        self.assertEqual(project['name'], 'Saved project')
        self.assertTrue(self.current.is_file())

    def test_interrupted_edit_rolls_back_before_load_and_preserves_new_editor_candidate(self):
        self.write(self.current); original = self.current.read_bytes()
        changed = json.dumps({**self.project, 'name': 'Interrupted edit'}).encode('utf-8')
        class Crash(BaseException): pass
        def publish():
            self.current.write_bytes(changed); raise Crash()
        with self.assertRaises(Crash):
            transaction.commit_pair(self.current, changed, b'{"undo":[],"redo":[]}', publish)
        self.assertEqual(self.env['load_project']()['name'], 'Saved project')
        self.assertEqual(self.current.read_bytes(), original)
        candidates = recovery.inspect_recovery(self.current)['candidates']
        self.assertTrue(any(item['name'] == 'Interrupted edit' for item in candidates))

    def test_explicit_recovery_preserves_and_retires_inconsistent_transaction(self):
        self.write(self.current); self.pending().write_bytes(json.dumps({**self.project, 'name': 'Chosen'}).encode())
        journal = self.current.parent / transaction.JOURNAL; journal.write_bytes(b'corrupt transaction evidence')
        undo = self.current.parent / 'undo_stack.json'; undo.write_bytes(b'corrupt undo evidence')
        result = self.restore(); archive = Path(result['preserved'])
        self.assertEqual((archive / 'transaction.json').read_bytes(), b'corrupt transaction evidence')
        self.assertEqual((archive / 'undo.json').read_bytes(), b'corrupt undo evidence')
        self.assertFalse(journal.exists()); self.assertEqual(self.env['load_project']()['name'], 'Chosen')
        self.assertEqual(json.loads(undo.read_bytes()), {'undo': [], 'redo': []})

    def test_failed_explicit_recovery_keeps_interrupted_transaction_and_undo(self):
        self.write(self.current); self.pending().write_bytes(json.dumps(self.project).encode())
        journal = self.current.parent / transaction.JOURNAL; journal.write_bytes(b'journal evidence')
        undo = self.current.parent / 'undo_stack.json'; undo.write_bytes(b'undo evidence')
        with patch.dict(self.env, save_project=lambda *a, **kw: (_ for _ in ()).throw(OSError('denied'))):
            with self.assertRaises(recovery.RecoveryError): self.restore()
        self.assertEqual(journal.read_bytes(), b'journal evidence'); self.assertEqual(undo.read_bytes(), b'undo evidence')

    def test_corrupt_and_nonfinite_current_files_request_explicit_recovery(self):
        for raw in [b'broken', b'[]', b'{"media":{},"sequences":[]}', json.dumps({**self.project, 'updated': float('nan')}).encode()]:
            with self.subTest(raw=raw[:40]):
                self.write(self.current, raw=raw)
                with self.assertRaises(recovery.ProjectRecoveryRequired): self.env['load_project']()
                self.assertEqual(self.current.read_bytes(), raw)

    def test_legacy_project_migration_still_works(self):
        legacy = copy.deepcopy(self.project); legacy['version'] = 1
        self.write(self.current, legacy)
        project = self.env['load_project']()
        self.assertEqual(project['version'], 3)
        self.assertIn('proposals', project)
        self.assertIn('captions', project['sequences'][0])

    def test_unreadable_candidates_are_excluded_with_reasons(self):
        self.write(self.current)
        self.write(self.pending(), raw=b'partial')
        self.write(self.current.parent / 'backups/project_1.json')
        self.write(self.current.parent / 'backups/project_2.json', raw=b'{')
        report = recovery.inspect_recovery(self.current)
        self.assertTrue(report['current_valid'])
        self.assertEqual({item['id'] for item in report['candidates']}, {'current', 'backups/project_1.json'})
        self.assertEqual(len(report['unavailable']), 2)

    def test_modified_candidate_is_rejected_without_touching_the_current_project(self):
        self.write(self.current); self.write(self.pending())
        report = recovery.inspect_recovery(self.current); old = self.current.read_bytes()
        self.write(self.pending(), {**self.project, 'name': 'Newer pending save'})
        with self.assertRaises(recovery.RecoveryConflict):
            recovery.restore_version(self.current, 'pending', next(item['sha256'] for item in report['candidates'] if item['id'] == 'pending'), report['current_sha256'], self.env['save_project'])
        self.assertEqual(self.current.read_bytes(), old)
        self.assertFalse((self.current.parent / 'recovery').exists())

    def test_modified_current_project_is_rejected_without_losing_new_edits(self):
        self.write(self.current); self.write(self.pending())
        report = recovery.inspect_recovery(self.current)
        self.write(self.current, {**self.project, 'name': 'New edit'})
        with self.assertRaises(recovery.RecoveryConflict):
            recovery.restore_version(self.current, 'pending', next(item['sha256'] for item in report['candidates'] if item['id'] == 'pending'), report['current_sha256'], self.env['save_project'])
        self.assertEqual(json.loads(self.current.read_text(encoding='utf-8'))['name'], 'New edit')

    def test_candidate_paths_cannot_escape_the_project(self):
        self.write(self.current); self.write(self.pending())
        for candidate in ['../project.json', '/tmp/project.json', 'backups/../../project.json', 'recovery/../editor.json']:
            with self.subTest(candidate=candidate), self.assertRaises(recovery.RecoveryError):
                recovery.restore_version(self.current, candidate, '', '', self.env['save_project'])

    def test_open_editor_and_unselected_pending_copies_are_preserved_and_recoverable(self):
        self.write(self.current)
        self.write(self.pending(), {**self.project, 'name': 'Pending edit'})
        self.write(self.current.parent / 'backups/project_1.json', {**self.project, 'name': 'Earlier edit'})
        result = self.restore('backups/project_1.json', {**self.project, 'name': 'Unsaved editor edit'})
        archive = Path(result['preserved'])
        self.assertEqual(json.loads((archive / 'editor.json').read_text(encoding='utf-8'))['name'], 'Unsaved editor edit')
        self.assertEqual(json.loads((archive / 'pending.json').read_text(encoding='utf-8'))['name'], 'Pending edit')
        candidates = recovery.inspect_recovery(self.current)['candidates']
        self.assertIn('Unsaved editor edit', [item['name'] for item in candidates])
        self.assertIn('Pending edit', [item['name'] for item in candidates])

    def test_preservation_failure_never_calls_the_project_writer(self):
        self.write(self.current, raw=b'corrupt'); self.write(self.pending())
        with patch.object(recovery.os, 'fsync', side_effect=OSError('disk full')), patch.dict(self.env, save_project=lambda *a, **kw: self.fail('must preserve first')):
            with self.assertRaises(OSError): self.restore()
        self.assertEqual(self.current.read_bytes(), b'corrupt')

    def test_restore_write_failure_preserves_the_previous_files_and_reports_archive(self):
        self.write(self.current, raw=b'corrupt'); self.write(self.pending())
        with patch.dict(self.env, save_project=lambda *a, **kw: (_ for _ in ()).throw(PermissionError('sharing denial'))):
            with self.assertRaisesRegex(recovery.RecoveryError, 'previous files were preserved'): self.restore()
        self.assertEqual(self.current.read_bytes(), b'corrupt')
        archives = list((self.current.parent / 'recovery').glob('*/project.json'))
        self.assertTrue(archives); self.assertTrue(all(path.read_bytes() == b'corrupt' for path in archives))

    def test_previous_undo_history_is_preserved_until_the_replacement_is_published(self):
        self.write(self.current); self.write(self.pending())
        undo = self.write(self.current.parent / 'undo_stack.json', raw=b'{"undo":[{"reason":"old cut"}],"redo":[]}')
        before = undo.read_bytes(); writer = self.env['save_project']
        def save(project, path, **kwargs):
            self.assertEqual(undo.read_bytes(), before)
            self.assertTrue((self.current.parent / transaction.JOURNAL).exists())
            writer(project, path, **kwargs)
        with patch.dict(self.env, save_project=save): result = self.restore()
        self.assertEqual((Path(result['preserved']) / 'undo.json').read_bytes(), before)
        self.assertEqual(json.loads(undo.read_text(encoding='utf-8')), {'undo': [], 'redo': []})

    def test_project_save_failure_reinstates_the_previous_undo_history(self):
        self.write(self.current); self.write(self.pending())
        undo = self.write(self.current.parent / 'undo_stack.json', raw=b'{"undo":[{"reason":"old cut"}],"redo":[]}')
        before, current = undo.read_bytes(), self.current.read_bytes()
        with patch.dict(self.env, save_project=lambda *a, **kw: (_ for _ in ()).throw(OSError('save denied'))):
            with self.assertRaises(recovery.RecoveryError): self.restore()
        self.assertEqual(undo.read_bytes(), before); self.assertEqual(self.current.read_bytes(), current)

    def test_locked_undo_history_prevents_recovery_from_changing_the_project(self):
        self.write(self.current); self.write(self.pending())
        undo = self.write(self.current.parent / 'undo_stack.json', raw=b'{"undo":[],"redo":[]}')
        before = self.current.read_bytes()
        writer = transaction.write_atomic
        def deny_history(path, raw):
            if Path(path) == undo: raise PermissionError('undo locked')
            writer(path, raw)
        with patch.object(transaction, 'write_atomic', side_effect=deny_history):
            with self.assertRaisesRegex(recovery.RecoveryError, 'undo locked'): self.restore()
        self.assertEqual(self.current.read_bytes(), before)


    def test_invalid_edit_serialization_cannot_truncate_a_previous_pending_save(self):
        self.write(self.current); self.write(self.pending())
        current, pending = self.current.read_bytes(), self.pending().read_bytes()
        for value in [float('nan'), object()]:
            with self.assertRaises((ValueError, TypeError)):
                self.env['save_project']({**self.project, 'bad': value}, str(self.current))
            self.assertEqual(self.current.read_bytes(), current); self.assertEqual(self.pending().read_bytes(), pending)

    def test_flush_failure_preserves_the_previous_project(self):
        self.write(self.current); original = self.current.read_bytes()
        with patch.object(os, 'fsync', side_effect=OSError('disk full')):
            with self.assertRaises(OSError): self.env['save_project']({**self.project, 'name': 'New'}, str(self.current))
        self.assertEqual(self.current.read_bytes(), original)

    def test_unrelated_backup_files_do_not_break_saves_or_get_deleted(self):
        self.write(self.current)
        note = self.write(self.current.parent / 'backups/notes.txt', raw=b'keep this')
        self.env['save_project']({**self.project, 'name': 'New'}, str(self.current))
        self.assertEqual(note.read_bytes(), b'keep this')

    def test_windows_sharing_retries_remain_bounded_and_keep_previous_bytes(self):
        self.write(self.current); original = self.current.read_bytes(); replace = os.replace
        calls = []
        error = PermissionError('Windows sharing denial'); error.winerror = 32
        def transient(source, destination):
            self.assertEqual(self.current.read_bytes(), original)
            calls.append(source)
            if len(calls) < 3: raise error
            replace(source, destination)
        with patch.object(os, 'replace', side_effect=transient), patch.object(time, 'sleep') as sleep:
            self.env['save_project']({**self.project, 'name': 'New'}, str(self.current))
        self.assertEqual(sleep.call_count, 2)
        original = self.current.read_bytes()
        with patch.object(os, 'replace', side_effect=error) as replace, patch.object(time, 'sleep') as sleep:
            with self.assertRaises(PermissionError): self.env['save_project']({**self.project, 'name': 'Later'}, str(self.current))
        self.assertEqual(replace.call_count, 6); self.assertEqual(sleep.call_count, 5)
        self.assertEqual(self.current.read_bytes(), original)
        self.assertEqual(json.loads(self.pending().read_text(encoding='utf-8'))['name'], 'Later')

    def test_route_refuses_recovery_after_project_switch(self):
        self.write(self.current); self.write(self.pending())
        class Request:
            async def json(self): return {'project': 'other'}
        with self.assertRaises(HTTPError) as error: asyncio.run(self.env['project_recovery_restore'](Request()))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.events, [])

    def test_route_restores_and_reports_a_history_failure_separately(self):
        self.write(self.current, raw=b'corrupt'); self.write(self.pending())
        report = self.env['project_recovery_versions'](); candidate = report['candidates'][0]
        class Request:
            async def json(self): return {'project': 'p', 'candidate': candidate['id'], 'sha256': candidate['sha256'], 'current_sha256': report['current_sha256']}
        broadcasts = []
        async def broadcast(event): broadcasts.append(event)
        with patch.dict(self.env, broadcast=broadcast, log_event=lambda *a, **k: (_ for _ in ()).throw(OSError('history full'))):
            result = asyncio.run(self.env['project_recovery_restore'](Request()))
        self.assertTrue(result['ok']); self.assertIn('history', result['warning'])
        self.assertEqual(broadcasts[0]['project'], 'p')

    def draft_request(self, draft=None, **extra):
        report = self.env['project_recovery_versions']()
        body = {'project': 'p', 'workspace': workspace_id(str(self.root)),
                'draft_project': draft or {**self.project, 'name': 'Unsaved browser draft'},
                'current_sha256': report['current_sha256'], **extra}
        class Request:
            async def json(self): return body
        return Request()

    def test_browser_draft_restore_preserves_current_pending_editor_and_undo_bytes(self):
        self.write(self.current); self.write(self.pending(), {**self.project, 'name': 'Pending'})
        self.write(self.current.parent / 'undo_stack.json', raw=b'{"undo":[{"ops":[]}],"redo":[]}')
        previous = self.current.read_bytes()
        async def broadcast(event): pass
        with patch.dict(self.env, broadcast=broadcast):
            result = asyncio.run(self.env['project_recovery_restore'](self.draft_request(editor_project={**self.project, 'name': 'Open editor'})))
        self.assertTrue(result['ok']); self.assertEqual(self.env['load_project']()['name'], 'Unsaved browser draft')
        archive = Path(result['preserved'])
        self.assertEqual((archive / 'project.json').read_bytes(), previous)
        self.assertEqual(json.loads((archive / 'editor.json').read_bytes())['name'], 'Open editor')
        self.assertTrue((archive / 'pending.json').exists()); self.assertTrue((archive / 'undo.json').exists())
        self.assertEqual(json.loads((self.current.parent / 'undo_stack.json').read_bytes()), {'undo': [], 'redo': []})

    def test_browser_draft_wrong_workspace_or_stale_current_is_rejected_before_staging(self):
        self.write(self.current); before = self.current.read_bytes()
        for extra in [{'workspace': 'other'}, {'current_sha256': 'outdated'}]:
            with self.subTest(extra=extra), self.assertRaises(HTTPError) as error:
                asyncio.run(self.env['project_recovery_restore'](self.draft_request(**extra)))
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.current.read_bytes(), before)
        self.assertFalse((self.current.parent / 'recovery').exists())

    def test_invalid_browser_draft_cannot_replace_current_project(self):
        self.write(self.current); before = self.current.read_bytes()
        with self.assertRaises(HTTPError) as error:
            asyncio.run(self.env['project_recovery_restore'](self.draft_request({'name': 'Incomplete'})))
        self.assertEqual(error.exception.status_code, 422); self.assertEqual(self.current.read_bytes(), before)
        self.assertFalse((self.current.parent / 'recovery').exists())

    def test_browser_draft_staging_failure_leaves_current_file_untouched(self):
        self.write(self.current); before = self.current.read_bytes()
        with patch.object(recovery, '_write_exclusive', side_effect=OSError('Disk full')):
            with self.assertRaises(HTTPError) as error:
                asyncio.run(self.env['project_recovery_restore'](self.draft_request()))
        self.assertEqual(error.exception.status_code, 500); self.assertEqual(self.current.read_bytes(), before)

    def test_choosing_current_saved_version_preserves_unsaved_editor_for_later(self):
        self.write(self.current)
        result = self.restore('current', editor={**self.project, 'name': 'Unsaved decision'})
        self.assertEqual(self.env['load_project']()['name'], 'Saved project')
        self.assertEqual(json.loads((Path(result['preserved']) / 'editor.json').read_bytes())['name'], 'Unsaved decision')

    def test_all_historical_samples_are_readable(self):
        for path in ROOT.glob('tests/sample_project*.json'):
            with self.subTest(path=path.name): recovery.parse_project(path.read_bytes())


if __name__ == '__main__': unittest.main(verbosity=2)
