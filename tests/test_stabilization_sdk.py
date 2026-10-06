"""Captured Stabilize inspection/apply transport; no ambiguous command replay."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from filmocity_client import Filmocity, FilmocityError


class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='human', client='stabilization-sdk')
        self.owner = {'workspace': 'work É', 'project': 'A', 'revision': 'a'*64}
        self.calls, self.fail, self.switch = [], None, False
        self.review_change = lambda r: None
        self.apply_change = lambda r: None

    def _call(self, path, body=None, method=None):
        self.calls.append((path, copy.deepcopy(body)))
        if path == '/api/project/state':
            result = {'context': copy.deepcopy(self.owner), 'project': {'media': {'sub': {}}}}
            if self.switch: self.owner['project'] = 'B'
            return result
        if path == self.fail: raise FilmocityError('Response lost')
        if path == '/api/stabilize/inspect':
            result = {'kind': 'stabilization', 'ok': True, 'context': copy.deepcopy(body['_context']),
                'requested_media_id': body['media_id'], 'media_id': 'physical', 'affected_media_ids': ['physical', 'sub'],
                'settings': {k: body[k] for k in ('shakiness', 'force')}, 'fingerprint': 'c'*64,
                'source': {'path': "C:/Émile's clip.mkv", 'stamp': [['p', '1', '2', '3']], 'sha256': 'd'*64},
                'analyzer': {'sha256': 'e'*64}}
            self.review_change(result); self.review = copy.deepcopy(result); return result
        assert path == '/api/stabilize'
        result = {'kind': 'stabilization', 'ok': True, 'context': {**body['_context'], 'revision': 'b'*64},
            'requested_media_id': body['media_id'], 'media_id': 'physical', 'media': ['physical', 'sub'], 'cached': False, 'changed': True,
            'trf': 'C:/private/stab/analysis-uuid/transforms.trf', 'analysis': {
                'source': self.review['source'], 'settings': {'shakiness': body['shakiness']},
                'analyzer': self.review['analyzer'],
                'owner': {k: body['_context'][k] for k in ('workspace', 'project')}, 'sha256': 'f'*64}}
        self.apply_change(result); return result


class StabilizationSDK(unittest.TestCase):
    def test_capture_owner_once_and_apply_exact_review_once_after_active_switch(self):
        c = Client(); owner = copy.deepcopy(c.owner); c.switch = True
        review = c.inspect_stabilization('sub', shakiness=7, force=True)
        self.assertEqual(review['context'], owner)
        c.calls.clear(); c.analyze_stabilization(review)
        self.assertEqual(c.calls, [('/api/stabilize', {'media_id': 'sub', 'shakiness': 7, 'force': True,
            '_context': owner, 'fingerprint': 'c'*64, 'actor': 'human', 'client': 'stabilization-sdk'})])

    def test_explicit_owner_does_not_reread(self):
        c = Client(); c.inspect_stabilization('sub', context=c.owner)
        self.assertEqual([p for p, _ in c.calls], ['/api/stabilize/inspect'])

    def test_invalid_settings_and_review_never_dispatch(self):
        for settings in ({'shakiness': True}, {'shakiness': 11}, {'force': 1}):
            c = Client()
            with self.assertRaises(FilmocityError): c.inspect_stabilization('sub', **settings)
            self.assertEqual(c.calls, [])
        for change in (lambda r: r.update(context=None), lambda r: r.update(fingerprint='bad'),
                       lambda r: r['settings'].update(force=1), lambda r: r.update(source={}),
                       lambda r: r.update(affected_media_ids=[]), lambda r: r.update(analyzer={})):
            c = Client(); review = c.inspect_stabilization('sub'); change(review); c.calls.clear()
            with self.assertRaises(FilmocityError): c.analyze_stabilization(review)
            self.assertEqual(c.calls, [])

    def test_inspection_response_must_match_owner_source_and_settings(self):
        for change in (lambda r: r['context'].update(project='foreign'),
                       lambda r: r.update(requested_media_id='other'), lambda r: r['settings'].update(shakiness=9)):
            c = Client(); c.review_change = change
            with self.assertRaises(FilmocityError): c.inspect_stabilization('sub')
            self.assertEqual(len(c.calls), 2)

    def test_lost_apply_response_is_not_retried_or_reinspected(self):
        c = Client(); review = c.inspect_stabilization('sub'); c.calls.clear(); c.fail = '/api/stabilize'
        with self.assertRaises(FilmocityError): c.analyze_stabilization(review)
        self.assertEqual([p for p, _ in c.calls], ['/api/stabilize'])

    def test_malformed_or_foreign_acknowledgement_is_uncertain_without_replay(self):
        for change in (lambda r: r.update(ok=False), lambda r: r['context'].update(project='B'),
                       lambda r: r['analysis']['source'].update(sha256='0'*64), lambda r: r.update(cached=1),
                       lambda r: r.update(media=['foreign'])):
            c = Client(); review = c.inspect_stabilization('sub'); c.calls.clear(); c.apply_change = change
            with self.assertRaisesRegex(FilmocityError, 'uncertain'): c.analyze_stabilization(review)
            self.assertEqual([p for p, _ in c.calls], ['/api/stabilize'])


if __name__ == '__main__': unittest.main()
