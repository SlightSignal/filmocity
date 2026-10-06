"""SDK managed render/remix workflows use captured context and explicit reviewed Apply."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from filmocity_client import Filmocity, FilmocityError


class Client(Filmocity):
    def __init__(self, kind='analysis'):
        super().__init__(); self.calls = []
        self.context = {'workspace': 'w', 'project': 'p', 'revision': 'r1'}
        self.project_doc = {'sequences': [{'id': 's', 'tracks': [{'id': 'a', 'kind': 'audio', 'clips':
            [{'id': 'c', 'media_id': 'm', 'in_': 2, 'out': 8, 'reverse': True, 'time_remap': [{'t': 0, 'v': 2}]}]}]}]}
        self.task = {'id': 'task', 'kind': kind, 'status': 'ready'}
        self.result = {'kind': 'remix' if kind == 'analysis' else 'render_replace', 'requested': 2, 'achieved': 2}
        self.plan = {'fingerprint': 'reviewed-plan', 'ops': [{'op': 'set'}]}

    def _call(self, path, body=None, method=None):
        self.calls.append((path, copy.deepcopy(body), method))
        if path == '/api/project/state': return {'context': copy.deepcopy(self.context), 'project': copy.deepcopy(self.project_doc)}
        if path in ('/api/render_replace', '/api/audio/remix'): return {'ok': True, 'task': self.task, 'context': copy.deepcopy(body['_context'])}
        if path == '/api/tasks': return {'context': copy.deepcopy(self.context), 'tasks': [self.task]}
        if path in ('/api/tasks/task/analysis', '/api/tasks/task/render-replace'):
            return {'ok': True, 'task': copy.deepcopy(self.task), 'context': copy.deepcopy(self.context), 'result': self.result, 'plan': self.plan}
        if path == '/api/tasks/task/apply': return {'ok': True, 'context': {**self.context, 'revision': 'r2'}}
        raise AssertionError(path)


class ReplacementSDK(unittest.TestCase):
    def test_raw_remix_is_read_only_and_captures_the_requested_source_window(self):
        client = Client(); self.assertEqual(client.remix('m', 2, in_=3, out=7, bars_per_phrase=2), client.result)
        body = next(body for path, body, _ in client.calls if path == '/api/audio/remix')
        self.assertEqual((body['in'], body['out'], body['target'], body['bars_per_phrase']), (3, 7, 2, 2))
        self.assertEqual(body['_context'], client.context); self.assertRegex(body['request_id'], r'^[a-f0-9]{32}$')
        self.assertNotIn('clip_id', body); self.assertFalse(any(path.endswith('/apply') for path, _, _ in client.calls))

    def test_remix_clip_preserves_captured_ramped_window_and_applies_only_the_server_fingerprint(self):
        client = Client(); self.assertTrue(client.remix_clip('c', 2, preview=False)['ok'])
        body = next(body for path, body, _ in client.calls if path == '/api/audio/remix')
        self.assertEqual((body['sequence'], body['clip_id'], body['in'], body['out']), ('s', 'c', 2, 8))
        applied = [body for path, body, _ in client.calls if path.endswith('/apply')]
        self.assertEqual(len(applied), 1); self.assertEqual(applied[0]['fingerprint'], 'reviewed-plan'); self.assertNotIn('ops', applied[0])
        self.assertFalse(any(method == 'PATCH' for _, _, method in client.calls))

    def test_render_default_is_review_and_explicit_apply_keeps_the_reviewed_revision(self):
        client = Client('render_replace'); reviewed = client.render_replace('c')
        self.assertFalse(any(path.endswith('/apply') for path, _, _ in client.calls))
        body = next(body for path, body, _ in client.calls if path == '/api/render_replace')
        self.assertEqual((body['sequence'], body['clip_id']), ('s', 'c')); self.assertEqual(body['_context']['revision'], 'r1')
        client.context['revision'] = 'different'; client.apply_render_replace('task', reviewed)
        self.assertEqual(client.calls[-1][1]['_context']['revision'], 'r1'); self.assertEqual(client.calls[-1][1]['fingerprint'], 'reviewed-plan')

    def test_locked_disabled_missing_and_wrong_track_targets_refuse_before_submission(self):
        for mode in ('locked', 'disabled', 'missing', 'video', 'hold'):
            client = Client('render_replace' if mode in ('locked', 'disabled', 'missing') else 'analysis'); track = client.project_doc['sequences'][0]['tracks'][0]
            if mode == 'locked': track['locked'] = True
            if mode == 'disabled': track['clips'][0]['enabled'] = False
            if mode == 'video': track['kind'] = 'video'
            if mode == 'hold': track['clips'][0]['hold'] = True
            with self.subTest(mode=mode), self.assertRaises(FilmocityError):
                if client.task['kind'] == 'render_replace': client.render_replace('absent' if mode == 'missing' else 'c')
                else: client.remix_clip('c', 2)
            self.assertEqual([path for path, _, _ in client.calls], ['/api/project/state'])

    def test_poll_timeout_wrong_kind_and_terminal_results_never_resubmit_or_apply(self):
        client = Client();
        with self.assertRaises(FilmocityError): client.wait_render_replace('task', context=client.context)
        client = Client('render_replace'); client.task['status'] = 'running'
        with patch('filmocity_client.time.monotonic', side_effect=[0, 2]), self.assertRaisesRegex(FilmocityError, 'not finished'):
            client.wait_render_replace('task', timeout=1, context=client.context)
        self.assertEqual([path for path, _, _ in client.calls], ['/api/tasks'])
        for status in ('done', 'applied', 'cancelled', 'error', 'interrupted'):
            client = Client('render_replace'); client.task['status'] = status
            with self.assertRaises(FilmocityError): client.wait_render_replace('task', context=client.context)
            self.assertEqual([path for path, _, _ in client.calls], ['/api/tasks'])

    def test_explicit_render_apply_runs_once_and_cannot_use_an_analysis_review(self):
        client = Client('render_replace'); self.assertTrue(client.render_replace('c', preview=False)['ok'])
        self.assertEqual(len([path for path, _, _ in client.calls if path.endswith('/apply')]), 1)
        reviewed = Client().remix_clip('c', 2); count = len(client.calls)
        with self.assertRaises(FilmocityError): client.apply_render_replace('task', reviewed)
        self.assertEqual(len(client.calls), count)


if __name__ == '__main__': unittest.main()
