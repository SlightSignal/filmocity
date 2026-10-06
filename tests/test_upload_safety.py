"""Real multipart/FFprobe/Pillow and contained storage; disposable files only.

Routes are loaded from server.py without starting the application's library or
workers. The adapter supplies a real private project store and observes saved
changes; media background preparation is captured rather than queued.
"""
import ast
import asyncio
import copy
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import unquote
import uuid
import wave

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import upload_storage as storage
import subprocesses
from project_sync import project_context, matches_context
from link_fixture import directory_link


def wav():
    output = io.BytesIO()
    with wave.open(output, 'wb') as stream:
        stream.setnchannels(1); stream.setsampwidth(2); stream.setframerate(8000)
        stream.writeframes(b'\x00\x00' * 800)
    return output.getvalue()


class PrivateFiles(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity upload Émile\'s ')
        self.base = Path(self.temp.name)
        self.assertTrue(self.base.resolve().is_relative_to(Path(tempfile.gettempdir()).resolve()))
        self.addCleanup(self.temp.cleanup)
        self.data = self.base / 'data'; self.data.mkdir()

    def remove_link(self, link):
        if os.name == 'nt': link.rmdir()
        else: link.unlink()


class Storage(PrivateFiles):
    def test_display_unicode_and_windows_portable_font_leaf(self):
        self.assertEqual(storage.display_name('../C:\\elsewhere\\Émile\'s take.wav'), "Émile's take.wav")
        self.assertEqual(storage.display_name('\r\n'), 'Media')
        for name in ('CON.ttf', 'COM¹.ttf', 'LPT².otf'):
            self.assertTrue(storage.font_name(name).startswith('_'))
        self.assertLessEqual(len(storage.font_name('𐐀' * 240 + '.ttf').encode('utf-16-le')) // 2, 255)

    def test_distinct_new_uploads_cannot_overwrite_prior_files(self):
        folder = self.data / 'media'; folder.mkdir()
        original = folder / 'original.wav'; original.write_bytes(b'original')
        paths = []
        for payload in (b'one', b'two'):
            with storage.OwnedUpload(self.data, 'media', io.BytesIO(payload), '.wav') as upload:
                paths.append(upload.path); upload.retain()
        self.assertNotEqual(*paths)
        self.assertEqual([p.read_bytes() for p in paths], [b'one', b'two'])
        self.assertEqual(original.read_bytes(), b'original')

    def test_uuid_collision_refuses_and_preserves_existing_file(self):
        folder = self.data / 'media'; folder.mkdir()
        identity = 'a' * 32
        existing = folder / ('upload-' + identity + '.wav'); existing.write_bytes(b'preserve')
        with patch.object(storage.uuid, 'uuid4', return_value=SimpleNamespace(hex=identity)):
            with self.assertRaises(storage.UploadError):
                storage.OwnedUpload(self.data, 'media', io.BytesIO(b'new'), '.wav')
        self.assertEqual(existing.read_bytes(), b'preserve')

    def test_copy_failure_removes_only_its_partial_file(self):
        class Broken:
            count = 0
            def read(self, amount):
                self.count += 1
                if self.count == 1: return b'partial'
                raise OSError('controlled reader failure')
        folder = self.data / 'media'; folder.mkdir()
        existing = folder / 'existing.wav'; existing.write_bytes(b'preserve')
        with self.assertRaises(OSError): storage.OwnedUpload(self.data, 'media', Broken(), '.wav')
        self.assertEqual(list(folder.iterdir()), [existing])

    def test_cleanup_refuses_a_replaced_file_identity(self):
        upload = storage.OwnedUpload(self.data, 'media', io.BytesIO(b'owned'), '.wav')
        owned_backup = upload.path.with_name('retained-owned.wav')
        upload.path.rename(owned_backup); upload.path.write_bytes(b'unrelated replacement')
        with self.assertRaises(storage.UploadError): upload.close()
        self.assertEqual(upload.path.read_bytes(), b'unrelated replacement')
        self.assertEqual(owned_backup.read_bytes(), b'owned')

    def test_junction_destination_is_refused_without_touching_target(self):
        outside = self.base / 'outside'; outside.mkdir()
        marker = outside / 'keep'; marker.write_bytes(b'preserve')
        link = self.data / 'media'; directory_link(link, outside)
        self.addCleanup(self.remove_link, link)
        with self.assertRaises(storage.UploadError):
            storage.OwnedUpload(self.data, 'media', io.BytesIO(b'new'), '.wav')
        self.assertEqual(list(outside.iterdir()), [marker])
        self.assertEqual(marker.read_bytes(), b'preserve')

    def test_invalid_extensions_do_not_create_an_upload(self):
        for extension in ('.wav/../bad', '.wav:stream', 'wav', '.超长'):
            with self.subTest(extension=extension), self.assertRaises(storage.UploadError):
                storage.OwnedUpload(self.data, 'media', io.BytesIO(b'new'), extension)
        self.assertEqual(list((self.data / 'media').iterdir()), [])


class Routes(PrivateFiles):
    def setUp(self):
        super().setUp()
        self.pid = 'default'
        self.saved = self.data / 'project.json'
        self.document = {'version': 3, 'id': 'test', 'name': 'Uploads', 'media': {},
                         'sequences': [{'id': 'seq1', 'width': 32, 'height': 32, 'fps': 30, 'tracks': []}]}
        self.saved.write_text(json.dumps(self.document), encoding='utf-8')
        self.finished, self.events = [], []
        async def owned(function, *args, **kwargs):
            return await asyncio.to_thread(function, *args, proc_holder={}, **kwargs)
        async def broadcast(event): pass
        def save(doc): self.saved.write_text(json.dumps(doc), encoding='utf-8')
        def event(ev, project_id=None):
            self.events.append({**ev, 'project': project_id}); return self.events[-1]
        self.env = {'__name__': 'upload_route_fixture', 'ROOT': str(self.data), 'os': os, 'time': __import__('time'),
                    'uuid': uuid, 'json': json, 'asyncio': asyncio, 'UploadFile': UploadFile, 'File': File,
                    'HTTPException': HTTPException, 'subprocess': subprocesses, 'LOCK': threading.Lock(),
                    'project_context': project_context, 'matches_context': matches_context,
                    'P': lambda *parts: str(self.data.joinpath(*parts)), 'active_id': lambda: self.pid,
                    'load_project': lambda: json.loads(self.saved.read_text(encoding='utf-8')),
                    'save_project': save, 'PP': lambda *parts: str(self.data.joinpath(*parts)),
                    '_owned_render_thread': owned,
                    'finish_ingest': lambda *args: self.finished.append(args), 'log_event': event, 'broadcast': broadcast}
        names = {'media_upload', 'fonts_upload', 'fonts_delete', 'user_fonts', 'probe', 'ingest', 'require_project_context'}
        tree = ast.parse((ROOT / 'backend/server.py').read_text(encoding='utf-8'))
        nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
        self.assertEqual({node.name for node in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'server.py upload routes', 'exec'), self.env)
        app = FastAPI()
        app.post('/api/media/upload')(self.env['media_upload'])
        app.post('/api/fonts/upload')(self.env['fonts_upload'])
        app.delete('/api/fonts/{file}')(self.env['fonts_delete'])
        self.client = TestClient(app)
        self.addCleanup(self.client.close)

    def media(self, name, payload=None):
        return self.client.post('/api/media/upload', files={'file': (name, wav() if payload is None else payload, 'audio/wav')})

    def font(self, name, payload):
        return self.client.post('/api/fonts/upload', files={'file': (name, payload, 'application/octet-stream')})

    def test_real_multipart_parent_traversal_retains_outside_file(self):
        outside = self.data.parent / 'upload-escape.wav'; outside.write_bytes(b'outside original')
        response = self.media('../../../../upload-escape.wav')
        self.assertEqual(response.status_code, 200, response.text)
        record = response.json(); path = Path(record['path'])
        self.assertEqual(record['name'], 'upload-escape.wav')
        self.assertEqual(path.parent, self.data / 'media')
        self.assertEqual(path.read_bytes(), wav())
        self.assertEqual(outside.read_bytes(), b'outside original')
        self.assertEqual(self.finished[0][1], str(path))
        self.assertEqual(self.events[-1]['project'], 'default')

    def test_unicode_label_and_same_name_uploads_have_distinct_retained_bytes(self):
        replies = [self.media("Émile's take.wav") for _ in range(2)]
        self.assertTrue(all(reply.status_code == 200 for reply in replies))
        paths = [reply.json()['path'] for reply in replies]
        self.assertNotEqual(*paths)
        self.assertTrue(all(reply.json()['name'] == "Émile's take.wav" for reply in replies))
        self.assertEqual(len(json.loads(self.saved.read_text())['media']), 2)

    def test_invalid_media_leaves_project_and_existing_files_unchanged(self):
        folder = self.data / 'media'; folder.mkdir()
        old = folder / 'original.wav'; old.write_bytes(wav())
        before = self.saved.read_bytes()
        response = self.media('bad.wav', b'not media')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(self.saved.read_bytes(), before)
        self.assertEqual(list(folder.iterdir()), [old])

    def test_project_switch_during_probe_refuses_and_discards_uncommitted_upload(self):
        async def changed(function, *args, **kwargs):
            result = await asyncio.to_thread(function, *args, proc_holder={}, **kwargs)
            self.pid = 'other'
            return result
        self.env['_owned_render_thread'] = changed
        before = self.saved.read_bytes(); response = self.media('take.wav')
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(self.saved.read_bytes(), before)
        self.assertEqual(list((self.data / 'media').iterdir()), [])
        self.assertFalse(self.finished)

    def test_invalid_same_name_font_preserves_valid_original(self):
        folder = self.data / 'fonts'; folder.mkdir()
        old = folder / 'existing.ttf'; shutil.copyfile(ROOT / 'assets/fonts/DejaVuSans.ttf', old)
        before = old.read_bytes(); response = self.font('existing.ttf', b'not a font')
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(list(folder.iterdir()), [old])

    def test_valid_unicode_font_replacement_publishes_complete_bytes_and_url(self):
        folder = self.data / 'fonts'; folder.mkdir()
        old = folder / "Émile's 20% font.ttf"; old.write_bytes((ROOT / 'assets/fonts/DejaVuSans.ttf').read_bytes())
        new = (ROOT / 'assets/fonts/DejaVuSans-Bold.ttf').read_bytes()
        response = self.font("Émile's 20% font.ttf", new)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(old.read_bytes(), new)
        self.assertEqual(unquote(response.json()['url']), '/fonts/' + old.name)
        self.assertIn('%25', response.json()['url'])
        self.assertEqual(self.env['user_fonts']()[0]['url'], response.json()['url'])
        self.assertEqual(list(folder.iterdir()), [old])

    def test_atomic_font_publication_failure_preserves_old_bytes(self):
        folder = self.data / 'fonts'; folder.mkdir()
        old = folder / 'existing.ttf'; old.write_bytes((ROOT / 'assets/fonts/DejaVuSans.ttf').read_bytes())
        before = old.read_bytes()
        with patch.object(storage.os, 'replace', side_effect=PermissionError('controlled publication failure')):
            response = self.font('existing.ttf', (ROOT / 'assets/fonts/DejaVuSans-Bold.ttf').read_bytes())
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(old.read_bytes(), before)
        self.assertEqual(list(folder.iterdir()), [old])

    def test_font_destination_directory_cannot_be_removed_or_replaced(self):
        folder = self.data / 'fonts'; folder.mkdir()
        target = folder / 'existing.ttf'; target.mkdir(); marker = target / 'keep'; marker.write_bytes(b'preserve')
        response = self.font('existing.ttf', (ROOT / 'assets/fonts/DejaVuSans.ttf').read_bytes())
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(marker.read_bytes(), b'preserve')
        self.assertEqual(list(folder.iterdir()), [target])


if __name__ == '__main__':
    if (ROOT / 'bin').is_dir(): os.environ['PATH'] = str(ROOT / 'bin') + os.pathsep + os.environ.get('PATH', '')
    unittest.main()
