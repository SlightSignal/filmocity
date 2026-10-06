"""SDK capture, no-replay and owned source/graphics/description contracts."""
import copy
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from filmocity_client import Filmocity, FilmocityError


class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='human', client='editorial-sdk-test')
        self.calls = []
        self.owner = {'workspace': 'workspace / a', 'project': 'project & one', 'revision': 'r1'}
        self.project_value = {'media': {'m': {'id': 'm', 'has_audio': True}}, 'sequences': [
            {'id': 'sequence / one', 'tracks': [{'id': 'V1', 'clips': [
                {'id': 'g', 'graphic': {'layers': [{'kind': 'text', 'text': 'One two three'}]}}
            ]}]}]}
        self.switch_after_capture = False
        self.fail_command = False
        self.foreign_description = False
        self.wrong_sequence_description = False

    def _call(self, path, body=None, method=None):
        self.calls.append((path, copy.deepcopy(body), method))
        if path == '/api/project/state':
            state = {'project': copy.deepcopy(self.project_value), 'context': copy.deepcopy(self.owner)}
            if self.switch_after_capture: self.owner['project'] = 'new active project'
            return state
        if self.fail_command: raise FilmocityError('uncertain reply; inspect saved state')
        if path.startswith('/api/sequence/describe?'):
            query = {key: values[0] for key, values in parse_qs(urlsplit(path).query).items()}
            context = {key: query[key] for key in ('workspace', 'project', 'revision')}
            if self.foreign_description: context['project'] = 'foreign'
            return {'context': context, 'sequence': 'wrong' if self.wrong_sequence_description else query['sequence'], 'duration': 10 / 3, 'text': 'canonical'}
        if path in ('/api/graphics/split_words', '/api/media/input_transform', '/api/media/extract_audio'):
            return {'ok': True, 'changed': True, 'context': {**body['_context'], 'revision': 'r2'}, 'summary': {'message': 'Saved'}}
        raise AssertionError(path)


class EditorialSDK(unittest.TestCase):
    def test_split_uses_one_saved_capture_and_never_rebinds_after_switch(self):
        client = Client(); original = copy.deepcopy(client.owner); client.switch_after_capture = True
        reply = client.split_words('g', anim={'type': 'fade', 'duration': .125}, stagger=.0125)
        self.assertTrue(reply['ok'])
        self.assertEqual([call[0] for call in client.calls], ['/api/project/state', '/api/graphics/split_words'])
        body = client.calls[-1][1]
        self.assertEqual(body['_context'], original)
        self.assertEqual((body['sequence'], body['clip_id'], body['layer']), ('sequence / one', 'g', 0))
        self.assertEqual(body['anim'], {'type': 'fade', 'duration': .125})
        self.assertEqual((body['stagger'], body['actor'], body['client']), (.0125, 'human', 'editorial-sdk-test'))
        self.assertNotIn('ops', body)

    def test_explicit_context_never_reads_or_infers_new_sequence(self):
        client = Client(); context = copy.deepcopy(client.owner)
        with self.assertRaises(FilmocityError): client.split_words('g', context=context)
        with self.assertRaises(FilmocityError): client.describe(context=context)
        self.assertEqual(client.calls, [])
        client.split_words('captured-clip', seq_id='captured-sequence', context=context)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(client.calls[0][1]['sequence'], 'captured-sequence')
        self.assertNotIn('anim', client.calls[0][1])

    def test_invalid_settings_refuse_before_read_or_write(self):
        for args, options in [((None,), {}), (('g',), {'layer_index': True}), (('g',), {'layer_index': -1}),
                              (('g',), {'stagger': float('nan')}), (('g',), {'stagger': -.1}),
                              (('g',), {'stagger': True}), (('g',), {'anim': []}),
                              (('g',), {'anim': {'duration': float('inf')}})]:
            client = Client()
            with self.subTest(args=args, options=options), self.assertRaises(FilmocityError):
                client.split_words(*args, **options)
            self.assertEqual(client.calls, [])

    def test_split_refuses_missing_locked_ambiguous_or_nontext_target(self):
        for mode in ('sequence', 'clip', 'locked', 'duplicate', 'nontext', 'layer'):
            client = Client(); track = client.project_value['sequences'][0]['tracks'][0]; options = {}
            if mode == 'sequence': options['seq_id'] = 'missing'
            elif mode == 'clip': track['clips'] = []
            elif mode == 'locked': track['locked'] = True
            elif mode == 'duplicate': track['clips'].append(copy.deepcopy(track['clips'][0]))
            elif mode == 'nontext': track['clips'][0]['graphic']['layers'][0]['kind'] = 'shape'
            elif mode == 'layer': options['layer_index'] = 1
            with self.subTest(mode=mode), self.assertRaises(FilmocityError): client.split_words('g', **options)
            self.assertEqual([p for p, _, _ in client.calls], ['/api/project/state'])

    def test_source_commands_capture_once_and_preserve_explicit_target(self):
        for method, args, route in [('input_transform', ('m', 'slog3'), '/api/media/input_transform'),
                                    ('extract_audio', ('m',), '/api/media/extract_audio')]:
            client = Client(); context = copy.deepcopy(client.owner); client.switch_after_capture = True
            getattr(client, method)(*args)
            self.assertEqual([p for p, _, _ in client.calls], ['/api/project/state', route])
            body = client.calls[-1][1]
            self.assertEqual((body['media_id'], body['_context'], body['client']), ('m', context, client.client))
            client.calls.clear(); getattr(client, method)(*args, context=context)
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0][1]['_context'], context)

    def test_invalid_or_missing_source_never_mutates(self):
        client = Client()
        with self.assertRaises(FilmocityError): client.input_transform('m', 'guess_hdr')
        with self.assertRaises(FilmocityError): client.extract_audio('')
        self.assertEqual(client.calls, [])
        with self.assertRaises(FilmocityError): client.input_transform('missing', 'none')
        self.assertEqual([p for p, _, _ in client.calls], ['/api/project/state'])
        client.calls.clear(); client.project_value['media']['m']['has_audio'] = False
        with self.assertRaises(FilmocityError): client.extract_audio('m')
        self.assertEqual([p for p, _, _ in client.calls], ['/api/project/state'])

    def test_describe_encodes_exact_captured_owner_and_preserves_fractional_duration(self):
        client = Client(); owner = copy.deepcopy(client.owner); client.switch_after_capture = True
        result = client.describe()
        self.assertEqual(result['duration'], 10 / 3)
        query = parse_qs(urlsplit(client.calls[-1][0]).query)
        self.assertEqual(query, {**{key: [value] for key, value in owner.items()}, 'sequence': ['sequence / one']})
        self.assertEqual(result['context'], owner)
        self.assertEqual(len(client.calls), 2)
        client.calls.clear(); client.describe(seq_id='captured & sequence', context=owner)
        self.assertEqual(len(client.calls), 1)
        for attr in ('foreign_description', 'wrong_sequence_description'):
            client.foreign_description = client.wrong_sequence_description = False
            setattr(client, attr, True)
            with self.subTest(attr=attr), self.assertRaisesRegex(FilmocityError, 'does not match'):
                client.describe(seq_id='captured', context=owner)

    def test_unknown_mutation_outcome_is_never_retried(self):
        for method, args in [('split_words', ('g',)), ('input_transform', ('m', 'none')), ('extract_audio', ('m',))]:
            client = Client(); client.fail_command = True
            with self.subTest(method=method), self.assertRaisesRegex(FilmocityError, 'uncertain reply'):
                getattr(client, method)(*args)
            self.assertEqual(len(client.calls), 2)
            self.assertEqual(sum(body is not None for _, body, _ in client.calls), 1)

    def test_invalid_explicit_owner_never_dispatches(self):
        for context in ({}, {'workspace': 'w', 'project': 'p'}, {'workspace': 'w', 'project': 'p', 'revision': 1}, []):
            client = Client()
            with self.subTest(context=context), self.assertRaises(FilmocityError):
                client.extract_audio('m', context=context)
            self.assertEqual(client.calls, [])


class SavedEditorialSDK(unittest.TestCase):
    def setUp(self):
        import test_editorial_source_commands as routes
        self.fixture = routes.StoreEditorialSourceCommands(methodName='runTest')
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups); self.addCleanup(self.fixture.tearDown)
        fixture = self.fixture

        class SavedClient(Filmocity):
            def __init__(self):
                super().__init__(actor='human', client='actual-editorial-sdk')
                self.calls = []

            def _call(self, path, body=None, method=None):
                self.calls.append((path, copy.deepcopy(body), method))
                try:
                    if path == '/api/project/state': return {'project': fixture.project(), 'context': fixture.current()}
                    if path.startswith('/api/sequence/describe?'):
                        query = {key: values[0] for key, values in parse_qs(urlsplit(path).query).items()}
                        return fixture.env['sequence_describe'](**query)
                    route = {'/api/graphics/split_words': 'graphics_split_words',
                             '/api/media/input_transform': 'media_input_transform',
                             '/api/media/extract_audio': 'media_extract_audio'}[path]
                    return fixture.route(route, body)
                except routes.store.HTTPError as error: raise FilmocityError(str(error.detail)) from error

        self.client = SavedClient()

    def test_actual_sdk_word_split_retains_label_curve_and_one_undo_redo(self):
        fixture = self.fixture; before = fixture.project()
        reply = self.client.split_words('g', anim={}, stagger=0, seq_id='s')
        after = fixture.project(); graphic = after['sequences'][0]['tracks'][1]['clips'][0]
        self.assertTrue(reply['changed']); self.assertEqual(reply['layers'], 3)
        self.assertEqual(graphic['graphic']['layers'][3]['text'], 'KEEP LABEL')
        self.assertEqual(graphic['keyframes']['g3.x'], before['sequences'][0]['tracks'][1]['clips'][0]['keyframes']['g1.x'])
        self.assertEqual(len(fixture.env['read_undo_history']('a')['undo']), 1)
        fixture.invoke('undo', {'_context': fixture.current()})
        self.assertEqual(fixture.project()['sequences'], before['sequences'])
        fixture.invoke('redo', {'_context': fixture.current()})
        self.assertEqual(fixture.project()['sequences'], after['sequences'])

    def test_actual_sdk_subclip_transform_changes_shared_parent_and_undo(self):
        fixture = self.fixture; project = fixture.project(); parent = project['media']['m']
        project['media']['sub'] = {**parent, 'id': 'sub', 'subclip_of': 'm', 'sub_in': 1, 'duration': 3}
        fixture.env['save_project'](project); before = fixture.project()
        reply = self.client.input_transform('sub', 'slog3')
        self.assertEqual(reply['media_id'], 'm'); self.assertEqual(reply['scope'], 'shared_source')
        self.assertEqual(set(reply['affected_media_ids']), {'m', 'sub'})
        self.assertEqual(fixture.project()['media']['m']['input_transform'], 'slog3')
        self.assertEqual(fixture.project()['media']['sub'], before['media']['sub'])
        self.assertEqual(len(fixture.env['read_undo_history']('a')['undo']), 1)
        fixture.invoke('undo', {'_context': fixture.current()})
        self.assertEqual(fixture.project()['media'], before['media'])

    def test_actual_sdk_alias_prepares_own_audio_and_undo_restores_original(self):
        fixture = self.fixture; before = fixture.project()
        reply = self.client.extract_audio('m'); identity = reply['media_id']; task_id = reply['preparation']['task']['id']
        fixture.prepare(task_id); after = fixture.project(); alias = after['media'][identity]
        self.assertEqual(alias['status'], 'ready'); self.assertEqual(alias['task_id'], task_id)
        self.assertNotEqual(alias['ingest_token'], before['media']['m']['ingest_token'])
        self.assertEqual(after['media']['m'], before['media']['m'])
        self.assertEqual(fixture.audio(after, 'm', 'sdk-original'), fixture.audio(after, identity, 'sdk-alias'))
        self.assertEqual(len(fixture.env['read_undo_history']('a')['undo']), 1)
        fixture.invoke('undo', {'_context': fixture.current()})
        self.assertEqual(fixture.project()['media'], before['media'])
        fixture.invoke('redo', {'_context': fixture.current()})
        self.assertEqual(fixture.project()['media'], after['media'])

    def test_actual_sdk_stale_context_never_edits_or_reads_repeated_foreign_ids(self):
        fixture = self.fixture; context = fixture.current(); original = fixture.project(); before_a = fixture.raw()
        fixture.env['set_active_project']('b'); fixture.env['save_project'](copy.deepcopy(original)); before_b = fixture.raw('b')
        actions = [lambda: self.client.split_words('g', seq_id='s', context=context),
                   lambda: self.client.input_transform('m', 'slog3', context=context),
                   lambda: self.client.extract_audio('m', context=context),
                   lambda: self.client.describe(seq_id='s', context=context)]
        for action in actions:
            before_calls = len(self.client.calls)
            with self.assertRaises(FilmocityError): action()
            self.assertEqual(len(self.client.calls), before_calls + 1)
            self.assertEqual((fixture.raw('a'), fixture.raw('b')), (before_a, before_b))
        self.assertFalse(fixture.env['read_undo_history']('b')['undo'])

    def test_actual_sdk_description_reports_canonical_ramp_without_history(self):
        import render
        fixture = self.fixture; project = fixture.project(); sequence = project['sequences'][0]
        sequence['tracks'][1]['clips'] = []; sequence['fps'] = 29.97
        clip = sequence['tracks'][0]['clips'][0]
        clip.update(in_=0, out=8, speed=1, time_remap=[{'t': 0, 'v': 1}, {'t': 2, 'v': 3}])
        fixture.env['save_project'](project); before = fixture.raw(); reply = self.client.describe('s')
        self.assertEqual(reply['duration'], render.seq_total(sequence))
        self.assertEqual(reply['timings'][0]['duration'], render.clip_dur(clip))
        self.assertEqual(reply['frame_rate'], '30000/1001')
        self.assertEqual(fixture.raw(), before); self.assertFalse(fixture.env['read_undo_history']('a')['undo'])


if __name__ == '__main__': unittest.main()
