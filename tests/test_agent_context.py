"""The SDK binds prepared operations to the context supplied by its caller."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from filmocity_client import Filmocity


class AgentContextTests(unittest.TestCase):
    def setUp(self):
        self.calls = []
        self.client = Filmocity()
        def call(path, body=None, method=None):
            self.calls.append((path, copy.deepcopy(body), method))
            return {'id': 'proposal', 'project': {'id': 'document'}, 'context': {'project': 'storage', 'revision': 'r0', 'workspace': 'root'}}
        self.client._call = call

    def test_state_read_returns_project_and_context_in_one_response(self):
        state = self.client.project_state()
        self.assertEqual(self.calls[0], ('/api/project/state', None, None))
        self.assertEqual(state['context']['project'], 'storage')

    def test_patch_and_propose_use_the_supplied_version_without_fetching_a_new_one(self):
        context = {'project': 'p', 'workspace': 'w', 'revision': 'old-read'}
        self.client.patch([{'op': 'set', 'path': '/name', 'value': 'Edit'}], context=context)
        self.assertEqual(self.client.propose('Review', [{'ops': []}], context=context), 'proposal')
        self.assertEqual(len(self.calls), 2)
        for _, body, _ in self.calls: self.assertEqual(body['_context'], context)

    def test_legacy_omission_and_explicit_malformed_context_remain_distinct(self):
        self.client.patch([]); self.assertNotIn('_context', self.calls[-1][1])
        self.client.propose('Legacy', []); self.assertNotIn('_context', self.calls[-1][1])
        self.client.propose('Must reject', [], context={}); self.assertEqual(self.calls[-1][1]['_context'], {})

    def test_snapshot_passes_the_callers_context_and_preserves_legacy_omission(self):
        self.client.sequence = lambda seq_id: {'id': seq_id or 'seq1'}
        expected = {'workspace': 'w', 'project': 'p', 'revision': 'captured'}
        self.client.snapshot('human_final', 'seq2', context=expected)
        self.assertEqual(self.calls[-1][1]['_context'], expected)
        self.assertEqual(self.calls[-1][1]['sequence'], 'seq2')
        self.client.snapshot(); self.assertNotIn('_context', self.calls[-1][1])
        self.client.snapshot(context={}); self.assertEqual(self.calls[-1][1]['_context'], {})

    def test_render_preview_carries_the_captured_context_range_and_retry_identity(self):
        self.client.sequence = lambda seq_id: {'id': seq_id or 'seq1'}
        expected = {'workspace': 'w', 'project': 'p', 'revision': 'captured'}
        self.client.render_preview('seq2', context=expected, in_out=True, request_id='same-request-123')
        body = self.calls[-1][1]
        self.assertEqual(body['_context'], expected); self.assertTrue(body['range'])
        self.assertEqual(body['request_id'], 'same-request-123'); self.assertEqual(body['sequence'], 'seq2')
        self.client.render_preview(); self.assertNotIn('_context', self.calls[-1][1])
        self.client.render_preview(context={}); self.assertEqual(self.calls[-1][1]['_context'], {})

    def test_ducking_explicit_review_context_never_rebases_or_fetches(self):
        expected = {'workspace':'w', 'project':'p', 'revision':'reviewed'}
        self.client.duck_all(seq_id='s', context=expected, music_tracks=['A2'], dialogue_tracks=['V1'],
                             attack=.2, release=.6, hold=.1, preview_plan='reviewed-plan')
        self.assertEqual(len(self.calls),1); body=self.calls[-1][1]
        self.assertEqual(body['_context'],expected); self.assertEqual(body['sequence'],'s')
        self.assertEqual(body['preview_plan'],'reviewed-plan'); self.assertEqual(body['music_tracks'],['A2'])
        self.assertEqual(body['attack'],.2)

    def test_ducking_without_context_captures_sequence_and_owner_together(self):
        def call(path, body=None, method=None):
            self.calls.append((path,body,method))
            return {'project':{'sequences':[{'id':'s'}]},'context':{'workspace':'w','project':'p','revision':'r'}}
        self.client._call=call; self.client.duck_all(preview=True)
        self.assertEqual([c[0] for c in self.calls],['/api/project/state','/api/audio/duck_all'])
        self.assertEqual(self.calls[-1][1]['sequence'],'s'); self.assertEqual(self.calls[-1][1]['_context']['revision'],'r')

    def test_ducking_never_guesses_sequence_for_explicit_context_or_invalid_selection(self):
        from filmocity_client import FilmocityError
        with self.assertRaises(FilmocityError): self.client.duck_all(context={})
        self.assertEqual(self.calls,[])
        self.client.project_state=lambda:{'project':{'sequences':[{'id':'s'}]},'context':{'revision':'r'}}
        with self.assertRaises(FilmocityError): self.client.duck_all(seq_id='missing')
        self.assertEqual(self.calls,[])


if __name__ == '__main__': unittest.main(verbosity=2)
