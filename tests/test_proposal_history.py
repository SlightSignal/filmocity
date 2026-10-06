"""Production store/proposal routes with real files and controlled framework wrappers."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from test_project_sync import ProjectStoreFixture, HTTPError, Request, ROOT
import project_transaction as tx


class ProposalHistoryTests(ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        tree = ast.parse((ROOT / 'backend/server.py').read_text(encoding='utf-8'))
        names = {'proposals_add', 'decide_proposal_items', 'proposal_batch_decide', 'proposal_decide', 'prepare_proposal', 'proposal_selection'}
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    def propose(self, items=None, **body):
        return self.invoke('proposals_add', {'title': 'Suggested cut', 'items': items or [
            {'reason': 'Clearer project name', 'ops': [{'op': 'set', 'path': '/name', 'value': 'Proposed'}]}], **body})

    def decide(self, proposal, decision='accept', ids=None, context=True):
        body = {'decision': decision, 'items': [{'id': iid, 'note': "Émile's review", 'reasons': ['pacing']} for iid in (ids or [it['id'] for it in proposal['items']])]}
        if context: body['_context'] = self.current()
        return asyncio.run(self.env['proposal_batch_decide'](proposal['id'], Request(body)))

    def saved(self): return json.loads(self.raw())
    def history(self): return self.env['read_undo_history']('a')
    def undo(self): return self.invoke('undo', {'_context': self.current()})
    def redo(self): return self.invoke('redo', {'_context': self.current()})

    def test_accept_is_one_reversible_commit_including_status_and_review_notes(self):
        proposal = self.propose(); before = self.saved()
        result = self.decide(proposal)
        self.assertTrue(result['ok']); self.assertEqual(result['context'], self.current())
        self.assertEqual(self.saved()['name'], 'Proposed')
        self.assertEqual(result['items'][0]['status'], 'accept'); self.assertEqual(result['items'][0]['reasons'], ['pacing'])
        self.assertEqual(len(self.history()['undo']), 1)
        self.assertTrue(self.undo()['ok']); restored = self.saved(); restored['updated'] = before['updated']; self.assertEqual(restored, before)
        self.assertTrue(self.redo()['ok']); self.assertEqual(self.saved()['name'], 'Proposed')
        self.assertEqual(self.saved()['proposals'][0]['items'][0]['note'], "Émile's review")

    def test_batch_accept_and_reject_are_single_history_steps(self):
        proposal = self.propose([{'reason': 'A', 'ops': [{'op': 'set', 'path': '/name', 'value': 'A'}]},
                                 {'reason': 'B', 'ops': [{'op': 'set', 'path': '/brief', 'value': {'client': 'B'}}]}])
        self.decide(proposal); self.assertEqual(len(self.history()['undo']), 1)
        self.undo(); self.assertTrue(all(it['status'] == 'pending' for it in self.saved()['proposals'][0]['items']))
        self.decide(proposal, 'reject'); self.assertEqual(self.saved()['name'], 'Original')
        self.assertTrue(all(it['status'] == 'reject' for it in self.saved()['proposals'][0]['items']))
        self.assertEqual(len(self.history()['undo']), 1); self.assertEqual(self.history()['redo'], [])

    def test_invalid_later_batch_item_preserves_project_history_and_all_statuses(self):
        proposal = self.propose([{'reason': 'Name', 'ops': [{'op': 'set', 'path': '/name', 'value': 'New'}]},
                                 {'reason': 'Trim', 'ops': [{'op': 'set_clip', 'sequence': 'seq1', 'track': 'V1', 'clip': {'id': 'c1', 'out': 3}}]}])
        self.invoke('patch_project', {'ops': [{'op': 'remove', 'path': '/sequences/0/tracks/0'}], '_context': self.current()})
        before, history = self.raw(), copy.deepcopy(self.history())
        with self.assertRaises(HTTPError) as error: self.decide(proposal)
        self.assertEqual(error.exception.status_code, 422); self.assertEqual(self.raw(), before); self.assertEqual(self.history(), history)

    def test_repeated_or_invalid_decisions_do_not_reapply_operations(self):
        proposal = self.propose(); self.decide(proposal); before, history = self.raw(), self.history()
        with self.assertRaises(HTTPError) as error: self.decide(proposal, context=False)
        self.assertEqual(error.exception.status_code, 409)
        with self.assertRaises(HTTPError) as error: self.decide(proposal, 'anything')
        self.assertEqual(error.exception.status_code, 422)
        self.assertEqual(self.raw(), before); self.assertEqual(self.history(), history)

    def test_stale_decision_and_duplicate_project_ids_cannot_redirect_edits(self):
        proposal = self.propose(); context = self.current(); self.edit('Newer edit', context)
        body = {'decision': 'accept', 'items': [{'id': proposal['items'][0]['id']}], '_context': context}
        before = self.raw()
        with self.assertRaises(HTTPError): asyncio.run(self.env['proposal_batch_decide'](proposal['id'], Request(body)))
        self.assertEqual(self.raw(), before)
        (self.root / 'projects/b/project.json').write_bytes(before); self.env['set_active_project']('b')
        with self.assertRaises(HTTPError): asyncio.run(self.env['proposal_batch_decide'](proposal['id'], Request(body)))
        self.assertEqual(self.raw('b'), before)

    def test_legacy_single_decision_contract_remains_usable_and_undoable(self):
        proposal = self.propose()
        result = asyncio.run(self.env['proposal_decide'](proposal['id'], proposal['items'][0]['id'], 'accept', Request({'note': 'Legacy caller'})))
        self.assertEqual(result['id'], proposal['items'][0]['id']); self.assertEqual(result['status'], 'accept')
        self.assertTrue(self.undo()['ok'])

    def test_creation_checks_context_and_validates_the_actual_result(self):
        old = self.current(); self.edit('Later', old); before = self.raw()
        with self.assertRaises(HTTPError): self.propose(_context=old)
        for ops in [[{'op': 'set', 'path': '/sequences', 'value': []}],
                    [{'op': 'set', 'path': '/proposals', 'value': []}],
                    [{'op': 'set_clip', 'sequence': 'seq1', 'track': 'V1', 'clip': {'id': 'c1', 'start': 'invalid'}}]]:
            with self.subTest(ops=ops):
                # Ensure changing proposal control records is an observable edit.
                if ops[0].get('path') == '/proposals': self.propose(); before = self.raw()
                with self.assertRaises(HTTPError): self.propose([{'ops': ops}])
                self.assertEqual(self.raw(), before)

    def test_failure_writing_undo_rolls_back_decision_and_edit_together(self):
        proposal = self.propose(); before = self.raw(); writer = tx.write_atomic
        def deny(path, raw):
            if Path(path).name == 'undo_stack.json': raise OSError('disk full')
            return writer(path, raw)
        with patch.object(tx, 'write_atomic', side_effect=deny), self.assertRaises(OSError): self.decide(proposal)
        self.assertEqual(self.raw(), before); self.assertEqual(self.history(), {'undo': [], 'redo': []})
        self.assertEqual(self.saved()['proposals'][0]['items'][0]['status'], 'pending')

    def test_secondary_event_failure_reports_committed_decision(self):
        proposal = self.propose()
        with patch.dict(self.env, log_event=lambda *a, **k: (_ for _ in ()).throw(OSError('event history denied'))):
            result = self.decide(proposal)
        self.assertTrue(result['ok']); self.assertIn('history', result['warning'])
        self.assertEqual(self.saved()['name'], 'Proposed'); self.assertEqual(len(self.history()['undo']), 1)

    def test_switch_during_broadcast_keeps_events_training_and_result_with_original_project(self):
        proposal = self.propose()
        async def switched(event): self.env['set_active_project']('b')
        with patch.dict(self.env, broadcast=switched): result = self.decide(proposal)
        self.assertEqual(result['context']['project'], 'a')
        self.assertEqual(json.loads(self.raw())['name'], 'Proposed'); self.assertEqual(json.loads(self.raw('b'))['name'], 'Original')
        self.assertTrue((self.root / 'projects/a/training/proposal_decisions.jsonl').exists())
        self.assertFalse((self.root / 'projects/b/training').exists())

    def test_new_clip_fields_and_overlap_normalization_undo_exactly(self):
        before = self.saved()
        result = self.invoke('patch_project', {'_context': self.current(), 'ops': [
            {'op': 'set_clip', 'sequence': 'seq1', 'track': 'V1', 'clip': {'id': 'c1', 'new_field': None}},
            {'op': 'set_clip', 'sequence': 'seq1', 'track': 'V1', 'clip': {'id': 'inserted', 'media_id': 'A', 'start': 1, 'in_': 0, 'out': 1}}]})
        self.assertTrue(result['warnings']); after = self.saved(); self.undo()
        undone = self.saved(); undone.pop('updated', None); before.pop('updated', None); self.assertEqual(undone, before)
        self.redo(); redone = self.saved(); redone['updated'] = after['updated']; self.assertEqual(redone, after)

    def test_full_replacement_is_undoable_and_clears_abandoned_redo(self):
        self.edit('First'); self.undo()
        self.invoke('put_project', {**self.doc, 'name': 'Replacement', '_context': self.current()})
        self.assertEqual(self.history()['redo'], []); self.undo(); self.assertEqual(self.saved()['name'], 'Original')
        self.redo(); self.assertEqual(self.saved()['name'], 'Replacement')

    def test_old_schema_replacement_is_migrated_before_context_and_history_are_captured(self):
        result = self.invoke('put_project', {**self.doc, 'version': 1, 'name': 'Old format', '_context': self.current()})
        self.assertEqual(self.saved()['version'], 3)
        self.assertEqual(result['context'], self.current())
        self.undo(); self.assertEqual(self.saved()['name'], 'Original')
        self.redo(); self.assertEqual(self.saved()['name'], 'Old format'); self.assertEqual(self.saved()['version'], 3)

    def test_corrupt_undo_history_prevents_editing_until_explicit_recovery(self):
        path = self.root / 'projects/a/undo_stack.json'; path.write_bytes(b'broken history')
        before = self.raw()
        with self.assertRaises(tx.TransactionRecoveryRequired): self.edit('Would destroy history')
        self.assertEqual(self.raw(), before); self.assertEqual(path.read_bytes(), b'broken history')

    def test_failed_legacy_inversion_retains_the_entry_and_project(self):
        self.edit('First'); history = self.history(); entry = history['undo'][0]; del entry['changes']
        entry['ops'] = [{'op': 'remove', 'path': '/sequences/99/tracks/0'}]; entry['befores'] = [{}]
        (self.root / 'projects/a/undo_stack.json').write_text(json.dumps(history))
        before = self.raw()
        with self.assertRaises(HTTPError): self.undo()
        self.assertEqual(self.raw(), before); self.assertEqual(self.history(), history)

    def test_failed_undo_and_redo_leave_both_files_and_history_position_unchanged(self):
        self.edit('First'); writer = tx.write_atomic
        for action in ['undo', 'redo']:
            if action == 'redo': self.undo()
            before, history = self.raw(), copy.deepcopy(self.history())
            def deny(path, raw):
                if Path(path).name == 'undo_stack.json' and json.loads(raw) != history: raise OSError('history denied')
                return writer(path, raw)
            with patch.object(tx, 'write_atomic', side_effect=deny), self.assertRaises(OSError):
                self.invoke(action, {'_context': self.current()})
            self.assertEqual(self.raw(), before); self.assertEqual(self.history(), history)

    def test_untracked_edit_conflict_does_not_consume_the_undo_entry(self):
        self.edit('First'); proj = self.saved(); proj['name'] = 'Untracked'; self.env['save_project'](proj)
        before, history = self.raw(), self.history()
        with self.assertRaises(HTTPError) as error: self.undo()
        self.assertEqual(error.exception.status_code, 409); self.assertEqual(self.raw(), before); self.assertEqual(self.history(), history)

    def test_old_operation_history_can_undo_and_is_upgraded_for_exact_redo(self):
        self.edit('First'); history = self.history(); del history['undo'][0]['changes']
        (self.root / 'projects/a/undo_stack.json').write_text(json.dumps(history))
        self.undo(); self.assertEqual(self.saved()['name'], 'Original'); self.assertIn('changes', self.history()['redo'][0])
        self.redo(); self.assertEqual(self.saved()['name'], 'First')


if __name__ == '__main__': unittest.main(verbosity=2)
