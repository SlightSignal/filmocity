"""Explicit text selection and owned sequence-recipe SDK contracts."""
import copy
import unittest
from unittest.mock import patch

import test_recipe_sdk as base

FilmocityError = base.FilmocityError


class Client(base.Client):
    def __init__(self):
        super().__init__()
        self.document['sequences'][0]['tracks'][0]['clips'].extend([
            {'id': 'card', 'graphic': {'layers': [{'kind': 'rect'}, {'kind': 'text', 'text': 'Headline'}]}},
            {'id': 'title', 'title': {'text': 'Original title'}}])

    def _call(self, path, body=None, method=None):
        if path == '/api/sequences/variants':
            self.calls.append((path, copy.deepcopy(body), method))
            self.result.update(mode='variants', sequence=body['sequence'])
            return {'ok': True, 'task': copy.deepcopy(self.task), 'context': copy.deepcopy(body['_context'])}
        return super()._call(path, body, method)

    def submitted(self):
        return [body for path, body, _ in self.calls if path.startswith('/api/recipes/') or path == '/api/sequences/variants']


class SequenceRecipeSDK(unittest.TestCase):
    def test_variants_reviews_explicit_ordered_targets_and_hooks_without_mutation(self):
        c = Client(); hooks = ['First', 'Second', 'First']; targets = [{'clip_id': 'card', 'layer': 1}, {'clip_id': 'title', 'title': True}]
        reviewed = c.variants(hooks, targets=targets, name_prefix='Campaign')
        body = c.submitted()[0]
        self.assertEqual((body['hooks'], body['targets'], body['name_prefix']), (hooks, targets, 'Campaign'))
        self.assertEqual(body['_context']['project'], 'a'); self.assertEqual(reviewed['result']['mode'], 'variants')
        self.assertEqual(c.applied(), []); self.assertEqual(sum(p == '/api/project/state' for p, _, _ in c.calls), 1)

    def test_explainer_preserves_explicit_options_and_defaults_to_review(self):
        c = Client(); lower = {'name': 'Ada', 'role': 'Engineer', 'at': 1.25, 'duration': 2.5}
        reviewed = c.explainer(lower, False, 'Thanks', chapter_duration=2, end_duration=4)
        self.assertEqual(c.submitted()[0]['lower_third'], lower)
        self.assertEqual((c.submitted()[0]['chapters'], c.submitted()[0]['end_duration']), (False, 4))
        self.assertEqual(reviewed['result']['mode'], 'explainer'); self.assertEqual(c.applied(), [])

    def test_explicit_apply_retains_review_identity_even_after_current_owner_changes(self):
        for mode, settings in [('explainer', {'end_card': 'Thanks'}), ('variants', {'hooks': ['A'], 'targets': [{'clip_id': 'title', 'title': True}]})]:
            c = Client(); queued = c.start_recipe(mode, **settings); reviewed = c.wait_recipe(queued['task']['id'], context=queued['context'])
            c.context['project'] = 'foreign'; c.apply_recipe('t', reviewed)
            self.assertEqual(c.applied(), [{'_context': reviewed['context'], 'fingerprint': 'reviewed-recipe', 'actor': c.actor, 'client': c.client}])
            self.assertEqual(c.applied()[0]['_context']['project'], 'a')

    def test_convenience_apply_is_one_command_and_noop_is_read_only(self):
        c = Client(); c.explainer(end_card='Thanks', preview=False); self.assertEqual(len(c.applied()), 1)
        c = Client(); c.variants(['A'], targets=[{'clip_id': 'title', 'title': True}], preview=False); self.assertEqual(len(c.applied()), 1)
        c = Client(); c.plan['ops'] = []; reply = c.explainer(chapters=False, preview=False)
        self.assertFalse(reply['changed']); self.assertEqual(c.applied(), [])

    def test_invalid_shapes_bounds_and_reserved_fields_refuse_before_network(self):
        cases = [('variants', {'hooks': ['A']}), ('variants', {'hooks': ['A'] * 21, 'targets': [{'clip_id': 'title', 'title': True}]}),
                 ('variants', {'hooks': [' '], 'targets': [{'clip_id': 'title', 'title': True}]}),
                 ('variants', {'hooks': ['A'], 'targets': [{'clip_id': 'card', 'layer': True}]}),
                 ('variants', {'hooks': ['A'], 'targets': [{'clip_id': 'card', 'layer': 1, 'title': True}]}),
                 ('variants', {'hooks': ['A'], 'targets': [{'clip_id': 'title', 'title': False}]}),
                 ('variants', {'hooks': ['A'], 'targets': [{'clip_id': 'title', 'title': True}] * 2}),
                 ('variants', {'hooks': ['A'], 'targets': [{'clip_id': 'title', 'title': True}], 'name_prefix': ''}),
                 ('explainer', {'chapters': 'false'}), ('explainer', {'end_card': 'A' * 501}), ('explainer', {'end_duration': 0}),
                 ('explainer', {'chapter_duration': float('inf')}), ('explainer', {'lower_third': {'at': -1}}),
                 ('explainer', {'lower_third': {'duration': True}}), ('explainer', {'lower_third': {'name': 'A' * 201}}),
                 ('explainer', {'lower_third': {'unknown': 'value'}}), ('explainer', {'actor': 'human'})]
        for mode, settings in cases:
            c = Client()
            with self.subTest(mode=mode, settings=settings), self.assertRaises(FilmocityError): c.start_recipe(mode, **settings)
            self.assertEqual(c.calls, [])

    def test_missing_locked_duplicate_and_nontext_targets_never_queue(self):
        for fault in ('missing', 'locked', 'duplicate', 'rect', 'layer', 'title'):
            c = Client(); track = c.document['sequences'][0]['tracks'][0]; target = {'clip_id': 'card', 'layer': 1}
            if fault == 'missing': target['clip_id'] = 'absent'
            if fault == 'locked': track['locked'] = True
            if fault == 'duplicate': track['clips'].append(copy.deepcopy(track['clips'][1]))
            if fault == 'rect': target['layer'] = 0
            if fault == 'layer': target['layer'] = 12
            if fault == 'title': target = {'clip_id': 'card', 'title': True}
            with self.subTest(fault=fault), self.assertRaises(FilmocityError): c.variants(['A'], targets=[target])
            self.assertEqual(c.submitted(), [])

    def test_supplied_context_requires_sequence_and_does_not_reread_targets(self):
        c = Client(); context = {'workspace': 'w', 'project': 'original', 'revision': 'old'}
        with self.assertRaises(FilmocityError): c.start_recipe('explainer', context=context)
        self.assertEqual(c.calls, [])
        c.start_recipe('variants', context=context, sequence='captured', hooks=['A'], targets=[{'clip_id': 'captured-title', 'title': True}], request_id='b' * 32)
        self.assertEqual(len(c.calls), 1); self.assertEqual(c.submitted()[0]['sequence'], 'captured')
        self.assertEqual(c.submitted()[0]['_context'], context)

    def test_timeout_and_uncertain_mutation_are_not_replayed(self):
        c = Client(); c.task['status'] = 'running'
        with patch('filmocity_client.time.monotonic', side_effect=[0, 2]), self.assertRaisesRegex(FilmocityError, 'not finished'):
            c.explainer(end_card='End', timeout=1)
        self.assertEqual(len(c.submitted()), 1); self.assertEqual(c.applied(), [])
        c = Client(); reviewed = c.variants(['A'], targets=[{'clip_id': 'title', 'title': True}]); call = c._call
        def uncertain(path, body=None, method=None):
            reply = call(path, body, method)
            if path.endswith('/apply'): raise OSError('lost response')
            return reply
        c._call = uncertain
        with self.assertRaises(OSError): c.apply_recipe('t', reviewed)
        self.assertEqual(len(c.applied()), 1); self.assertEqual(len(c.submitted()), 1)


class SavedSequenceRecipeSDK(unittest.TestCase):
    def setUp(self):
        import test_sequence_recipe_workflow as fixtures
        from sequence_recipe_sdk_support import saved_client
        self.fixture = fixtures.StoreSequenceRecipes(methodName='runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown); self.addCleanup(self.fixture.doCleanups)
        self.client = saved_client(self.fixture)

    def test_actual_explainer_preview_and_one_undo_preserve_original_tracks(self):
        f = self.fixture; before = f.project()
        reviewed = self.client.explainer({'name': 'Ada', 'at': 0, 'duration': 2}, chapters=False, end_card='Thanks', seq_id='s')
        self.assertEqual(f.project(), before); self.assertEqual(reviewed['plan']['summary']['cards'], 2)
        self.client.apply_recipe(reviewed['task']['id'], reviewed); after = f.project()
        original = before['sequences'][0]['tracks']
        self.assertEqual(after['sequences'][0]['tracks'][:len(original)], original)
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']), 1)
        f.invoke('undo', {'_context': f.current()}); self.assertEqual(f.project()['sequences'], before['sequences'])
        f.invoke('redo', {'_context': f.current()}); self.assertEqual(f.project()['sequences'], after['sequences'])

    def test_actual_variants_changes_explicit_text_only_and_saves_all_copies_once(self):
        f = self.fixture; before = f.project()
        result = self.client.variants(['One', 'Two'], seq_id='s', targets=[{'clip_id': 'hook', 'layer': 0}], preview=False)
        after = f.project(); self.assertTrue(result['ok']); self.assertEqual(len(after['sequences']), 3)
        self.assertEqual(after['sequences'][0], before['sequences'][0])
        self.assertEqual(result['sequence'], after['sequences'][1]['id'])
        for sequence, text in zip(after['sequences'][1:], ['One', 'Two']):
            clip = sequence['tracks'][1]['clips'][0]
            self.assertEqual(clip['graphic']['layers'][0]['text'], text)
            self.assertEqual(clip['graphic']['layers'][1]['text'], 'Speaker name')
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']), 1)
        f.invoke('undo', {'_context': f.current()}); self.assertEqual(f.project()['sequences'], before['sequences'])

    def test_actual_foreign_apply_refuses_without_rebinding_or_replay(self):
        f = self.fixture; reviewed = self.client.variants(['One'], seq_id='s', targets=[{'clip_id': 'hook', 'layer': 0}])
        original = f.raw(); f.env['set_active_project']('b'); foreign = f.raw()
        with self.assertRaises(FilmocityError): self.client.apply_recipe(reviewed['task']['id'], reviewed)
        self.assertEqual(f.raw('a'), original); self.assertEqual(f.raw(), foreign)
        self.assertEqual(sum(path.endswith('/apply') for path, _, _ in self.client.calls), 1)


if __name__ == '__main__': unittest.main()
