"""Guarded project editing using real files and production route/store functions.

FastAPI is unavailable on the restricted host. Only framework request/response
wrappers are substituted; the store, operation handlers, locking and hashes run.
"""
import ast
import asyncio
import copy
import hashlib
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
from project_sync import project_context, matches_context, workspace_id
import project_recovery as recovery
from project_transaction import recover_transaction, commit_pair, TransactionRecoveryRequired
from project_history import changes_between, apply_changes, HistoryConflict
from project_versions import write_snapshot


class HTTPError(Exception):
    def __init__(self, status_code, detail=None):
        self.status_code, self.detail = status_code, detail
        super().__init__(str(detail))


class Response(dict):
    def __init__(self, body, status_code=200):
        super().__init__(body); self.status_code = status_code


class Request:
    def __init__(self, body): self.body, self.headers = body, {'content-length': '100'}
    async def json(self): return copy.deepcopy(self.body)


class ProjectStoreFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity edits É ')
        self.root = Path(self.temp.name)
        self.doc = json.loads((ROOT / 'tests/sample_project.json').read_text(encoding='utf-8'))
        self.doc.update(version=3, name='Original', id='shared-document-id')
        self.broadcasts = []
        async def broadcast(event): self.broadcasts.append(copy.deepcopy(event))
        self.env = dict(os=os, json=json, time=time, uuid=uuid, shutil=shutil, copy=copy, hashlib=hashlib,
            ROOT=str(self.root), LOCK=threading.Lock(), SCHEMA=3, Request=object,
            HTTPException=HTTPError, JSONResponse=Response, project_context=project_context,
            matches_context=matches_context, workspace_id=workspace_id, LAST_OPS_TS={'t': 0},
            OPS_SINCE_AUTOSAVE=0, broadcast=broadcast, default_project=lambda: copy.deepcopy(self.doc),
            P=lambda *parts: str(self.root.joinpath(*parts)))
        self.env.update(recover_transaction=recover_transaction, commit_pair=commit_pair,
                        TransactionRecoveryRequired=TransactionRecoveryRequired,
                        changes_between=changes_between, apply_changes=apply_changes, HistoryConflict=HistoryConflict,
                        write_snapshot=write_snapshot)
        for name in ('ProjectRecoveryRequired', 'RecoveryError', 'parse_project', 'has_recovery_files'):
            self.env[name] = getattr(recovery, name)
        tree = ast.parse((ROOT / 'backend/server.py').read_text(encoding='utf-8'))
        names = {'active_id', 'PP', 'load_project', 'migrate_project', 'save_project', 'log_event',
            'get_project', 'get_project_state', 'require_project_context', 'put_project', 'patch_project',
            '_walk', 'validate_ops', 'normalize_tracks', 'apply_ops', 'undo_stack_path', 'read_undo_history', 'commit_project_history', 'commit_edit', 'project_history_action',
            'inverse_ops', 'undo', 'redo', '_switch', 'set_active_project', '_read_project_target', '_activate_project_locked', '_project_action_notify'}
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)
        self.make_project('a'); self.make_project('b')
        self.env['set_active_project']('a')

    def tearDown(self): self.temp.cleanup()
    def make_project(self, pid):
        path = self.root / 'projects' / pid / 'project.json'; path.parent.mkdir(parents=True)
        path.write_text(json.dumps(self.doc), encoding='utf-8')
    def raw(self, pid='a'): return (self.root / 'projects' / pid / 'project.json').read_bytes()
    def current(self): return self.env['get_project_state']()['context']
    def invoke(self, route, body): return asyncio.run(self.env[route](Request(body)))
    def edit(self, name, ctx=None, **extra):
        body = {'ops': [{'op': 'set', 'path': '/name', 'value': name}], **extra}
        if ctx is not None: body['_context'] = ctx
        return self.invoke('patch_project', body)


class ProjectSyncTests(ProjectStoreFixture):
    def test_state_envelope_uses_storage_folder_without_changing_legacy_get(self):
        result = self.env['get_project_state']()
        self.assertEqual(result['project'], self.env['get_project']())
        self.assertEqual(result['context']['project'], 'a')
        self.assertNotIn('_context', result['project'])
        self.assertNotEqual(result['context']['project'], self.doc['id'])

    def test_context_survives_formatting_but_changes_with_content_folder_and_workspace(self):
        ctx = self.current()
        self.assertEqual(ctx, project_context(str(self.root), 'a', dict(reversed(list(self.doc.items())))))
        for changed in (project_context(str(self.root), 'b', self.doc),
                        project_context(str(self.root / 'other'), 'a', self.doc),
                        project_context(str(self.root), 'a', {**self.doc, 'name': 'Changed'})):
            self.assertFalse(matches_context(ctx, changed))

    def test_rapid_guarded_edits_use_successive_commits(self):
        first = self.edit('First', self.current()); second = self.edit('Second', first['context'])
        self.assertTrue(second['ok']); self.assertEqual(self.env['load_project']()['name'], 'Second')
        self.assertNotEqual(first['context']['revision'], second['context']['revision'])
        self.assertEqual(second['context'], self.current())
        self.assertEqual(len(self.broadcasts), 2)
        events = [json.loads(line) for line in (self.root / 'projects/a/events.jsonl').read_text().splitlines()]
        self.assertEqual([e['ops'][0]['value'] for e in events], ['First', 'Second'])

    def test_stale_request_is_rejected_before_project_history_or_snapshot_changes(self):
        old = self.current(); self.edit('Accepted', old); raw = self.raw()
        history = (self.root / 'projects/a/undo_stack.json').read_bytes()
        with self.assertRaises(HTTPError) as error: self.edit('Stale', old)
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(error.exception.detail['code'], 'project_changed')
        self.assertEqual(self.raw(), raw); self.assertEqual((self.root / 'projects/a/undo_stack.json').read_bytes(), history)
        self.assertEqual(len(self.broadcasts), 1)

    def test_delayed_edit_never_changes_the_new_active_project_even_with_same_document_id(self):
        old = self.current(); before_a, before_b = self.raw(), self.raw('b')
        asyncio.run(self.env['_switch']('b', 'human'))
        with self.assertRaises(HTTPError): self.edit('Wrong folder', old)
        self.assertEqual(self.raw(), before_a); self.assertEqual(self.raw('b'), before_b)

    def test_legacy_agent_edits_remain_usable_and_invalidate_guarded_old_reads(self):
        old = self.current(); result = self.edit('Legacy agent', actor='agent')
        self.assertTrue(result['ok'])
        with self.assertRaises(HTTPError): self.edit('Stale human', old)
        self.assertEqual(self.env['load_project']()['name'], 'Legacy agent')

    def test_supplied_malformed_context_never_falls_back_to_unguarded_behavior(self):
        for value in [None, '', {}, {'project': 'a'}, {**self.current(), 'workspace': 'other'}]:
            with self.subTest(value=value), self.assertRaises(HTTPError):
                self.invoke('patch_project', {'ops': [], '_context': value})
        self.assertEqual(self.broadcasts, [])

    def test_guarded_history_replacement_strips_request_metadata(self):
        doc = {**self.doc, 'name': 'History state', '_context': self.current(), '_actor': 'human', '_source': 'history', '_client': 'ui-123'}
        result = self.invoke('put_project', doc)
        self.assertEqual(result['context'], self.current())
        self.assertEqual(self.env['load_project']()['name'], 'History state')
        self.assertFalse(any(k.startswith('_') for k in self.env['load_project']()))
        self.assertEqual(self.broadcasts[0]['client'], 'ui-123')

    def test_stale_put_undo_and_redo_cannot_mutate_another_project(self):
        ctx = self.current(); self.env['set_active_project']('b'); before = self.raw('b')
        for route, body in [('put_project', self.doc), ('undo', {}), ('redo', {})]:
            with self.subTest(route=route), self.assertRaises(HTTPError) as error:
                self.invoke(route, {**body, '_context': ctx})
            self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.raw('b'), before)
        self.assertFalse((self.root / 'projects/b/undo_stack.json').exists())

    def test_invalid_full_replacement_or_ops_leave_readable_saved_project(self):
        before = self.raw()
        for body in [{**self.doc, 'sequences': []}, {**self.doc, 'version': 99}]:
            with self.assertRaises(HTTPError) as error: self.invoke('put_project', {**body, '_context': self.current()})
            self.assertEqual(error.exception.status_code, 422)
        with self.assertRaises(HTTPError) as error:
            self.invoke('patch_project', {'_context': self.current(), 'ops': [{'op': 'set', 'path': '/sequences', 'value': []}]})
        self.assertEqual(error.exception.status_code, 422); self.assertEqual(self.raw(), before)

    def test_save_failure_does_not_advance_version_or_record_success(self):
        ctx, before = self.current(), self.raw()
        with patch.dict(self.env, save_project=lambda *a, **k: (_ for _ in ()).throw(OSError('Disk full'))):
            with self.assertRaises(OSError): self.edit('Unsaved', ctx)
        self.assertEqual(self.raw(), before); self.assertEqual(self.current(), ctx)
        self.assertEqual(self.broadcasts, [])
        self.assertFalse((self.root / 'projects/a/undo_stack.json').exists())

    def test_history_failure_is_reported_as_saved_with_warning_and_same_request_cannot_reapply(self):
        ctx = self.current()
        with patch.dict(self.env, log_event=lambda *a, **k: (_ for _ in ()).throw(OSError('History read only'))):
            result = self.edit('Committed', ctx)
        self.assertTrue(result['ok']); self.assertIn('history', result['warning'].lower())
        self.assertEqual(result['context'], self.current())
        with self.assertRaises(HTTPError): self.edit('Replay', ctx)

    def test_switch_during_broadcast_cannot_redirect_commit_event_or_result(self):
        async def broadcast(event):
            self.broadcasts.append(event); self.env['set_active_project']('b')
        with patch.dict(self.env, broadcast=broadcast): result = self.edit('Project A only', self.current())
        self.assertEqual(result['context']['project'], 'a')
        self.assertEqual(self.broadcasts[0]['project'], 'a')
        self.assertEqual(json.loads(self.raw())['name'], 'Project A only')
        self.assertEqual(json.loads(self.raw('b'))['name'], 'Original')
        self.assertFalse((self.root / 'projects/b/events.jsonl').exists())

    def test_two_writers_with_the_same_read_have_exactly_one_winner(self):
        ctx = self.current(); barrier = threading.Barrier(2); results = []
        def run(name):
            barrier.wait()
            try: results.append(self.edit(name, ctx))
            except HTTPError as error: results.append(error.status_code)
        threads = [threading.Thread(target=run, args=(name,)) for name in ['First', 'Second']]
        for thread in threads: thread.start()
        for thread in threads: thread.join(timeout=5); self.assertFalse(thread.is_alive())
        self.assertEqual(sum(isinstance(value, dict) and value['ok'] for value in results), 1)
        self.assertIn(409, results)
        self.assertEqual(len(self.broadcasts), 1)

    def test_active_pointer_keeps_previous_project_during_windows_sharing_retries(self):
        replace = os.replace; calls = []
        error = PermissionError('Active project temporarily open'); error.winerror = 32
        def busy(source, destination):
            calls.append(source); self.assertEqual(self.env['active_id'](), 'a')
            if len(calls) < 3: raise error
            replace(source, destination)
        with patch.object(os, 'replace', side_effect=busy), patch.object(time, 'sleep'):
            self.env['set_active_project']('b')
        self.assertEqual(len(calls), 3); self.assertEqual(self.env['active_id'](), 'b')
        with patch.object(os, 'replace', side_effect=error) as failed, patch.object(time, 'sleep'):
            with self.assertRaises(PermissionError): self.env['set_active_project']('a')
        self.assertEqual(failed.call_count, 6); self.assertEqual(self.env['active_id'](), 'b')


if __name__ == '__main__': unittest.main(verbosity=2)
