"""Owned, read-only Cover SDK and explicit PNG download integrity."""
import copy
import hashlib
import io
import unittest
import urllib.error
import urllib.parse
from unittest.mock import patch

from PIL import Image
import test_recipe_sdk as base

FilmocityError = base.FilmocityError


class Client(base.Client):
    def __init__(self):
        super().__init__()
        self.task.update(kind='cover')
        png = io.BytesIO(); Image.new('RGB', (64, 48), (12, 34, 56)).save(png, format='PNG'); self.png = png.getvalue()
        self.result = {'version': 1, 'kind': 'cover', 'sequence': 's', 'context': copy.deepcopy(self.context),
                       'covers': [{'index': 0, 'width': 64, 'height': 48, 'size': len(self.png),
                                   'sha256': hashlib.sha256(self.png).hexdigest(), 'url': 'https://untrusted.invalid/ignored.png'}]}
        self.plan = {'ops': [], 'summary': {'kind': 'cover'}, 'fingerprint': 'cover-review'}

    def _call(self, path, body=None, method=None):
        if path in ('/api/recipes/cover', '/api/tasks/t/cover'):
            self.calls.append((path, copy.deepcopy(body), method))
            if path == '/api/recipes/cover':
                return {'ok': True, 'task': copy.deepcopy(self.task), 'context': copy.deepcopy(body['_context'])}
            return {'task': copy.deepcopy(self.task), 'result': copy.deepcopy(self.result), 'context': copy.deepcopy(body['_context']), 'plan': copy.deepcopy(self.plan)}
        return super()._call(path, body, method)


class CoverSDK(unittest.TestCase):
    def test_convenience_captures_one_saved_owner_and_only_reviews(self):
        c = Client()
        with patch('urllib.request.urlopen') as network:
            reviewed = c.cover('Headline', 1.25, sub='Details', sizes=[(64, 48), (1080, 1920)], framing='contain')
        network.assert_not_called(); self.assertEqual(c.applied(), [])
        self.assertEqual(sum(path == '/api/project/state' for path, _, _ in c.calls), 1)
        body = c.submitted()[0]
        self.assertEqual((body['time'], body['headline'], body['sub'], body['framing']), (1.25, 'Headline', 'Details', 'contain'))
        self.assertEqual(body['sizes'], [[64, 48], [1080, 1920]])
        self.assertEqual(body['_context']['project'], 'a'); self.assertEqual(reviewed['result']['kind'], 'cover')

    def test_frame_only_defaults_keep_size_server_authoritative(self):
        c = Client(); c.cover()
        body = c.submitted()[0]
        self.assertEqual((body['time'], body['headline'], body['sub']), (0, '', ''))
        self.assertNotIn('sizes', body); self.assertEqual(body['framing'], 'blur_fill')

    def test_supplied_context_does_not_read_another_current_project(self):
        c = Client(); context = {'workspace': 'old-w', 'project': 'old-p', 'revision': 'old-r'}
        with self.assertRaises(FilmocityError): c.start_cover(context=context)
        self.assertEqual(c.calls, [])
        c.start_cover(sequence='old-s', context=context, request_id='a' * 32)
        self.assertEqual(len(c.calls), 1); self.assertEqual(c.submitted()[0]['_context'], context)
        self.assertEqual(c.submitted()[0]['sequence'], 'old-s')

    def test_unknown_duplicate_sequence_and_invalid_input_do_not_queue(self):
        cases = [{'time_s': -1}, {'time_s': True}, {'time_s': float('nan')}, {'headline': 'h' * 501}, {'sub': 12}, {'template': ''},
                 {'sizes': []}, {'sizes': [[64, 48]] * 2}, {'sizes': [[15, 64]]}, {'sizes': [[True, 64]]},
                 {'sizes': [[4096, 4096], [4096, 4095], [4096, 4094]]}, {'sizes': [[64.0, 48]]}, {'framing': 'auto'}, {'request_id': 'bad'}]
        for options in cases:
            c = Client()
            with self.subTest(options=options), self.assertRaises(FilmocityError): c.start_cover(**options)
            self.assertEqual(c.calls, [])
        c = Client()
        with self.assertRaises(FilmocityError): c.start_cover(sequence='missing')
        self.assertEqual(c.submitted(), [])
        c = Client(); c.document['sequences'].append(copy.deepcopy(c.document['sequences'][0]))
        with self.assertRaises(FilmocityError): c.start_cover(sequence='s')
        self.assertEqual(c.submitted(), [])

    def test_wait_rejects_foreign_kind_owner_terminal_and_timeout_without_resubmission(self):
        for fault in ('kind', 'owner', 'cancelled', 'error', 'interrupted', 'done'):
            c = Client(); original = copy.deepcopy(c.context)
            if fault == 'kind': c.task['kind'] = 'recipe'
            elif fault == 'owner': c.context['project'] = 'foreign'
            else: c.task['status'] = fault
            with self.subTest(fault=fault), self.assertRaises(FilmocityError): c.wait_cover('t', context=original)
            self.assertEqual(c.submitted(), [])
        c = Client(); c.task['status'] = 'running'
        with patch('filmocity_client.time.monotonic', side_effect=[0, 2]), self.assertRaisesRegex(FilmocityError, 'not finished'):
            c.cover(timeout=1)
        self.assertEqual(len(c.submitted()), 1); self.assertEqual(c.applied(), [])

    def test_explicit_download_uses_reviewed_owner_revision_auth_and_constructed_endpoint(self):
        c = Client(); c.token = 'test-token'; reviewed = c.cover(); c.context['project'] = 'new-active'
        with patch('urllib.request.urlopen', return_value=io.BytesIO(c.png)) as network:
            data = c.download_cover('t', 0, reviewed)
        self.assertEqual(data, c.png); network.assert_called_once()
        request = network.call_args.args[0]; parsed = urllib.parse.urlparse(request.full_url)
        self.assertEqual(parsed.netloc, 'localhost:8787'); self.assertEqual(parsed.path, '/api/tasks/t/cover/0')
        self.assertEqual(urllib.parse.parse_qs(parsed.query), {'workspace': ['w'], 'project': ['a'], 'revision': ['r1'], 'download': ['1']})
        self.assertEqual(request.get_header('Authorization'), 'Bearer test-token')
        self.assertEqual(c.applied(), [])

    def test_download_rejects_tampered_bytes_size_and_geometry(self):
        for fault in ('bytes', 'extra', 'geometry', 'not_png'):
            c = Client(); reviewed = c.cover(); descriptor = reviewed['result']['covers'][0]; data = c.png
            if fault == 'bytes': data = data[:-1] + bytes([data[-1] ^ 1])
            if fault == 'extra': data += b'X'
            if fault == 'geometry': descriptor['width'] = 128
            if fault == 'not_png': data = b'x' * len(data); descriptor['sha256'] = hashlib.sha256(data).hexdigest()
            with self.subTest(fault=fault), patch('urllib.request.urlopen', return_value=io.BytesIO(data)) as network, self.assertRaises(FilmocityError):
                c.download_cover('t', 0, reviewed)
            network.assert_called_once(); self.assertEqual(len(c.submitted()), 1)

    def test_invalid_download_reviews_refuse_without_network(self):
        for fault in ('kind', 'id', 'owner', 'context', 'index', 'size', 'hash', 'duplicate'):
            c = Client(); reviewed = c.cover(); index = 0
            if fault == 'kind': reviewed['task']['kind'] = 'recipe'
            if fault == 'id': reviewed['task']['id'] = 'other'
            if fault == 'owner': reviewed['result']['context']['project'] = 'other'
            if fault == 'context': reviewed['context'].pop('revision')
            if fault == 'index': index = True
            if fault == 'size': reviewed['result']['covers'][0]['size'] = 80 * 1024 * 1024 + 1
            if fault == 'hash': reviewed['result']['covers'][0]['sha256'] = 'bad'
            if fault == 'duplicate': reviewed['result']['covers'] *= 2
            with self.subTest(fault=fault), patch('urllib.request.urlopen') as network, self.assertRaises(FilmocityError): c.download_cover('t', index, reviewed)
            network.assert_not_called()

    def test_refused_download_does_not_retry_or_render_again(self):
        c = Client(); reviewed = c.cover()
        failure = urllib.error.HTTPError(c.base, 409, 'stale owner', {}, io.BytesIO(b'{}'))
        with patch('urllib.request.urlopen', side_effect=failure) as network, self.assertRaisesRegex(FilmocityError, 'refresh'):
            c.download_cover('t', 0, reviewed)
        network.assert_called_once(); self.assertEqual(len(c.submitted()), 1)


class SavedCoverSDK(unittest.TestCase):
    def setUp(self):
        import test_cover_workflow as fixtures
        from sequence_recipe_sdk_support import saved_client
        self.fixture = fixtures.StoreCover(methodName='runTest'); self.fixture.setUp()
        self.addCleanup(self.fixture.tearDown); self.addCleanup(self.fixture.doCleanups)
        self.client = saved_client(self.fixture)

    def _download_route(self, request, **kwargs):
        import test_project_sync as store
        query = urllib.parse.parse_qs(urllib.parse.urlparse(request.full_url).query)
        parts = urllib.parse.urlparse(request.full_url).path.split('/')
        try:
            response = self.fixture.download(parts[3], int(parts[5]), **{
                key: query[key][0] for key in ('workspace', 'project', 'revision')}, download=query['download'][0]=='1')
        except store.HTTPError as error:
            raise urllib.error.HTTPError(request.full_url, error.status_code, str(error.detail), {}, io.BytesIO()) from error
        self.assertEqual(response['media_type'], 'image/png')
        self.assertTrue(response['headers']['Content-Disposition'].startswith('attachment;'))
        return io.BytesIO(response['content'])

    def test_actual_sdk_renders_reviews_and_downloads_verified_png_without_saved_edit(self):
        f = self.fixture; before = f.raw()
        reviewed = self.client.cover('', 2.25, sizes=[(64, 48)], seq_id='s', framing='contain')
        self.assertEqual(f.raw(), before); self.assertEqual(reviewed['plan']['ops'], [])
        with patch('urllib.request.urlopen', side_effect=self._download_route):
            data = self.client.download_cover(reviewed['task']['id'], 0, reviewed)
        with Image.open(io.BytesIO(data)) as image: self.assertEqual(image.size, (64, 48)); image.load()
        self.assertEqual(hashlib.sha256(data).hexdigest(), reviewed['result']['covers'][0]['sha256'])
        self.assertEqual(f.raw(), before); self.assertFalse(f.env['read_undo_history']('a')['undo'])

    def test_actual_sdk_download_refuses_changed_revision_and_foreign_owner(self):
        f = self.fixture; reviewed = self.client.cover('', 2.25, sizes=[(64, 48)], seq_id='s')
        project = f.project(); project['name'] = 'Unrelated saved edit'; f.env['save_project'](project)
        with patch('urllib.request.urlopen', side_effect=self._download_route), self.assertRaises(FilmocityError):
            self.client.download_cover(reviewed['task']['id'], 0, reviewed)
        refreshed = self.client.cover_result(reviewed['task']['id'], context=f.current())
        with patch('urllib.request.urlopen', side_effect=self._download_route):
            self.assertTrue(self.client.download_cover(reviewed['task']['id'], 0, refreshed).startswith(b'\x89PNG'))
        original = f.raw(); f.env['set_active_project']('b'); foreign = f.raw()
        with patch('urllib.request.urlopen', side_effect=self._download_route), self.assertRaises(FilmocityError):
            self.client.download_cover(refreshed['task']['id'], 0, refreshed)
        self.assertEqual(f.raw('a'), original); self.assertEqual(f.raw(), foreign)
        self.assertEqual(sum(path == '/api/recipes/cover' for path, _, _ in self.client.calls), 1)


if __name__ == '__main__': unittest.main()
