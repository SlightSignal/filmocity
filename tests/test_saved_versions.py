"""Saved-version routes with real projects/journals and controlled HTTP wrappers."""
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import shutil
import unittest
from unittest.mock import patch

from test_project_sync import ProjectStoreFixture, HTTPError, Request, ROOT
import project_versions as versions
import project_transaction as tx
from project_recovery import RecoveryConflict


class SavedVersionTests(ProjectStoreFixture):
    def tearDown(self):
        # Snapshot names can take an ordinary private test root beyond MAX_PATH.
        # Retire the same owned tree through the production filesystem adapter.
        try:
            shutil.rmtree(versions._filesystem_path(self.root))
        finally:
            self.temp.cleanup()

    def setUp(self):
        super().setUp()
        self.env.update(read_version=versions.read_version, list_versions=versions.list_versions,
                        write_snapshot=versions.write_snapshot, RecoveryConflict=RecoveryConflict)
        names = {'saved_versions', 'restore_saved_version', 'snapshots_list', 'snapshots_get',
                 'snapshots_restore', 'backups_list', 'backups_restore', 'snapshot', 'diff_sequences', 'autosave_tick'}
        tree = ast.parse((ROOT / 'backend/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    def version(self, kind='snapshots', document=None, pid='a', name=None):
        document = document or {**self.doc, 'name': 'Earlier cut'}
        name = name or ('1700000000_manual_save.json' if kind == 'snapshots' else 'project_1700000000.json')
        path = self.root / 'projects' / pid / kind / name
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(document), encoding='utf-8')
        return path

    def catalog(self, kind='snapshots'): return self.env['saved_versions'](kind)
    def restore(self, kind='snapshots', catalog=None, **extra):
        catalog = catalog or self.catalog(kind)
        chosen = next(v for v in catalog['versions'] if v['name'] == 'Earlier cut')
        return self.invoke(kind + '_restore', {'name' if kind == 'snapshots' else 'file': chosen['file'],
            'sha256': chosen['sha256'], '_context': catalog['context'], **extra})
    def saved(self): return json.loads(self.raw())
    def history(self): return self.env['read_undo_history']('a')

    def test_snapshot_and_backup_restores_are_single_undoable_commits_with_checkpoints(self):
        for kind in ('snapshots', 'backups'):
            with self.subTest(kind=kind):
                self.version(kind)
                before = self.saved(); before_steps = len(self.history()['undo'])
                result = self.restore(kind)
                self.assertEqual(self.saved()['name'], 'Earlier cut')
                self.assertEqual(result['context'], self.current())
                self.assertEqual(len(self.history()['undo']), before_steps + 1)
                checkpoint, _ = versions.read_version(self.root / 'projects/a/project.json', 'snapshots', result['preserved'])
                self.assertEqual(checkpoint, before)
                self.assertTrue(self.invoke('undo', {'_context': self.current()})['ok'])
                actual = self.saved(); actual.pop('updated', None); before.pop('updated', None); self.assertEqual(actual, before)
                self.assertTrue(self.invoke('redo', {'_context': self.current()})['ok'])
                self.assertEqual(self.saved()['name'], 'Earlier cut')
                self.invoke('undo', {'_context': self.current()})

    def test_stale_project_version_and_storage_switch_are_rejected_before_checkpoint_creation(self):
        self.version(); catalog = self.catalog(); self.edit('Newer edit')
        for switch in (False, True):
            if switch: self.env['set_active_project']('b')
            before = {p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
            with self.assertRaises(HTTPError) as error: self.restore(catalog=catalog)
            self.assertEqual(error.exception.status_code, 409)
            self.assertEqual({p: p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, before)

    def test_changed_selected_file_is_rejected_even_when_the_project_is_unchanged(self):
        path = self.version(); catalog = self.catalog(); self.version(document={**self.doc, 'name': 'Changed file'})
        before = self.raw()
        with self.assertRaises(HTTPError) as error: self.restore(catalog=catalog)
        self.assertEqual(error.exception.status_code, 409); self.assertEqual(self.raw(), before)
        self.assertEqual(len(list(path.parent.glob('*.json'))), 1)

    def test_unreadable_and_unrelated_files_do_not_break_listing(self):
        self.version(); self.version('backups')
        for kind in ('snapshots', 'backups'):
            base = self.root / 'projects/a' / kind
            (base / ('broken.json' if kind == 'snapshots' else 'project_1700000001.json')).write_text('{')
            (base / 'notes.txt').write_text('keep this')
            catalog = self.catalog(kind)
            self.assertEqual(len(catalog['versions']), 1); self.assertEqual(len(catalog['unavailable']), 1)
            self.assertEqual(catalog['context'], self.current())
            self.assertEqual(len(self.env[kind + '_list']()), 1)

    def test_invalid_missing_and_traversing_files_do_not_change_the_project(self):
        self.version(); before = self.raw()
        for name in ('../project.json', '..\\project.json', 'C:\\project.json', 'missing.json', 'bad.json'):
            if name == 'bad.json': self.version(name=name, document={'bad': 'project'})
            with self.assertRaises(HTTPError) as error:
                self.invoke('snapshots_restore', {'name': name})
            self.assertIn(error.exception.status_code, (404, 422))
            self.assertEqual(self.raw(), before)
        with self.assertRaises(HTTPError) as error:
            self.invoke('snapshots_restore', {'name': '1700000000_manual_save.json', '_context': self.current()})
        self.assertEqual(error.exception.status_code, 422)

    @unittest.skipUnless(os.name != 'nt', 'Symlink creation needs separate Windows privilege acceptance')
    def test_linked_snapshot_is_not_read_or_restored(self):
        self.version(); link = self.root / 'projects/a/snapshots/linked.json'
        link.symlink_to(self.root / 'projects/b/project.json')
        self.assertEqual(len(self.catalog()['unavailable']), 1)
        with self.assertRaises(HTTPError): self.invoke('snapshots_restore', {'name': link.name})
        self.assertEqual(self.saved()['name'], 'Original')

    def test_checkpoint_failure_aborts_restore_and_history_failure_rolls_back_the_pair(self):
        self.version(); before = self.raw(); catalog = self.catalog()
        with patch.dict(self.env, write_snapshot=lambda *a: (_ for _ in ()).throw(OSError('checkpoint denied'))):
            with self.assertRaises(OSError): self.restore(catalog=catalog)
        self.assertEqual(self.raw(), before)
        writer = tx.write_atomic
        def fail_history(path, raw):
            if Path(path).name == 'undo_stack.json': raise OSError('disk full')
            return writer(path, raw)
        with patch.object(tx, 'write_atomic', side_effect=fail_history), self.assertRaises(OSError): self.restore(catalog=catalog)
        self.assertEqual(self.raw(), before); self.assertEqual(self.history(), {'undo': [], 'redo': []})

    def test_legacy_endpoint_contract_and_schema_migration_survive_restore_reopen_edit_undo(self):
        old = copy.deepcopy(self.doc); old.update(version=1, name='Earlier cut'); old.pop('proposals', None)
        path = self.version(document=old)
        result = self.invoke('snapshots_restore', {'name': path.name})
        self.assertTrue(result['ok']); self.assertEqual(self.saved()['version'], 3)
        self.env['set_active_project']('b'); self.env['set_active_project']('a')
        self.edit('After reopen', self.current()); self.invoke('undo', {'_context': self.current()})
        self.assertEqual(self.saved()['name'], 'Earlier cut')
        self.invoke('undo', {'_context': self.current()}); self.assertEqual(self.saved()['name'], 'Original')

    def test_secondary_restore_failures_report_a_saved_result_in_the_captured_project(self):
        self.version()
        async def fail_broadcast(event): self.env['set_active_project']('b'); raise OSError('socket closed')
        with patch.dict(self.env, broadcast=fail_broadcast, log_event=lambda *a, **kw: (_ for _ in ()).throw(OSError('history denied'))):
            result = self.restore()
        self.assertTrue(result['ok']); self.assertIn('history', result['warning']); self.assertIn('notification', result['warning'])
        self.assertEqual(result['context']['project'], 'a'); self.assertEqual(json.loads(self.raw('b'))['name'], 'Original')

    def test_repeated_snapshot_labels_are_unique_and_remain_with_the_captured_project(self):
        before = self.raw()
        async def switch(event): self.env['set_active_project']('b')
        with patch.object(versions.time, 'time', return_value=1700000000):
            a = self.invoke('snapshot', {'label': '../a <snapshot>', '_context': self.current()})
            with patch.dict(self.env, broadcast=switch): b = self.invoke('snapshot', {'label': '../a <snapshot>', '_context': self.current()})
        self.assertNotEqual(a['snapshot'], b['snapshot']); self.assertEqual(self.raw(), before)
        self.assertEqual(len(list((self.root / 'projects/a/snapshots').glob('*.json'))), 2)
        self.assertFalse((self.root / 'projects/b/snapshots').exists())
        self.assertEqual(a['context']['project'], 'a'); self.assertEqual(b['context']['project'], 'a')

    def test_snapshot_requires_matching_context_and_secondary_training_failure_is_explicit(self):
        old = self.current(); self.edit('Later')
        with self.assertRaises(HTTPError): self.invoke('snapshot', {'label': 'manual_save', '_context': old})
        self.invoke('snapshot', {'label': 'agent_proposal'})
        with patch.dict(self.env, diff_sequences=lambda *a: (_ for _ in ()).throw(ValueError('missing sequence'))):
            result = self.invoke('snapshot', {'label': 'human_final'})
        self.assertTrue(result['ok']); self.assertIn('training pair', result['warning']); self.assertEqual(len(self.catalog()['versions']), 2)

    def test_timed_autosave_captures_the_project_and_prunes_only_old_automatic_versions(self):
        self.version(); path = self.root / 'projects/a/project.json'
        for stamp in range(1700000000, 1700000022):
            self.version(name=f'{stamp}_autosave.json')
        self.env['LAST_OPS_TS']['t'] = 1800000000
        with patch.object(self.env['time'], 'time', return_value=1800000001): self.env['autosave_tick']()
        files = list((path.parent / 'snapshots').glob('*_autosave.json'))
        self.assertEqual(len(files), 20); self.assertTrue((path.parent / 'snapshots/1700000000_manual_save.json').exists())
        self.assertFalse((self.root / 'projects/b/snapshots').exists())

    def test_failed_snapshot_flush_preserves_existing_copies_and_removes_only_the_partial_file(self):
        existing = self.version(); before = existing.read_bytes(); project = self.raw()
        with patch.object(versions.os, 'fsync', side_effect=OSError('flush failed')):
            with self.assertRaises(OSError): self.invoke('snapshot', {'label': 'manual_save'})
        self.assertEqual(self.raw(), project); self.assertEqual(existing.read_bytes(), before)
        self.assertEqual(list(existing.parent.iterdir()), [existing])

    def test_unicode_labels_have_portable_bounded_filenames(self):
        result = self.invoke('snapshot', {'label': '\U0001f3ac' * 120})
        self.assertLess(len((result['snapshot'] + '.json').encode()), 255)
        self.assertEqual(self.catalog()['versions'][0]['sha256'], result['sha256'])

    def test_long_library_saved_versions_create_list_read_and_remove(self):
        library = self.root / ("Émile's long library " + "x" * 50)
        project_file = library / ("preserved hierarchy " + "y" * 60) / ("project " + "z" * 60) / 'project.json'
        while len(str(project_file)) < 320:
            project_file = project_file.parent / ('nested ' + 'n' * 40) / 'project.json'
        internal = versions._filesystem_path(project_file)
        internal.parent.mkdir(parents=True)
        original = self.raw()
        internal.write_bytes(original)
        document = json.loads(original)
        try:
            snapshot = versions.write_snapshot(project_file, document, '\U0001f3ac' * 120)
            self.assertNotIn('\\\\?\\', snapshot['file'])
            self.assertLess(len(snapshot['file'].encode('utf-8')), 255)
            listed = versions.list_versions(project_file, 'snapshots')
            self.assertEqual(listed['unavailable'], [])
            self.assertEqual(listed['versions'][0], snapshot)
            restored, sha = versions.read_version(project_file, 'snapshots', snapshot['file'], snapshot['sha256'])
            self.assertEqual(restored, document)
            self.assertEqual(sha, snapshot['sha256'])
            with self.assertRaises(RecoveryConflict):
                versions.read_version(project_file, 'snapshots', snapshot['file'], '0' * 64)
            with self.assertRaises(versions.RecoveryError):
                versions.version_path(project_file, 'snapshots', '../unrelated.json')
            backup = versions.version_path(project_file, 'backups', 'project_1700000000.json')
            backup.parent.mkdir()
            backup.write_bytes(original)
            self.assertEqual(versions.list_versions(project_file, 'backups')['versions'][0]['file'], backup.name)
            self.assertEqual(versions.read_version(project_file, 'backups', backup.name)[0], document)
            versions.version_path(project_file, 'snapshots', snapshot['file']).unlink()
            self.assertEqual(versions.list_versions(project_file, 'snapshots')['versions'], [])
            self.assertEqual(internal.read_bytes(), original)
        finally:
            shutil.rmtree(versions._filesystem_path(library))

    def test_restoring_the_oldest_backup_does_not_prune_the_selected_source(self):
        chosen = self.version('backups', name='project_9.json'); original = chosen.read_bytes()
        for stamp in range(10, 40):
            self.version('backups', name=f'project_{stamp}.json', document={**self.doc, 'name': 'Other backup'})
        self.restore('backups')
        self.assertEqual(chosen.read_bytes(), original)
        self.assertFalse((chosen.parent / 'project_10.json').exists())
        self.assertEqual(self.saved()['name'], 'Earlier cut')


if __name__ == '__main__': unittest.main(verbosity=2)
