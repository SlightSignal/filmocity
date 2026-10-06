"""Prepared proposal decisions and exact frames; real store/FFmpeg, framework wrappers only."""
import ast
import asyncio
import copy
import hashlib
import io
import json
import math
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from test_project_sync import ProjectStoreFixture, HTTPError, Request, ROOT
from proposal_preview import PreviewStore, PreviewUnavailable, plan_digest
import render as engine
from render_context import RenderContext
from PIL import Image


class PreviewStoreTests(unittest.TestCase):
    def setUp(self):
        self.clock = [20.0]
        self.store = PreviewStore(limit=2, lifetime=10, clock=lambda: self.clock[0], wall_clock=lambda: 1000)
        self.context = {'project': 'a', 'workspace': 'root', 'revision': 'one'}
    def create(self, document=None): return self.store.create('p', ['i'], document or {'name': 'New'}, self.context, [])
    def test_snapshot_copies_inputs_and_returned_documents(self):
        doc = {'nested': [1]}; view = self.create(doc); doc['nested'].append(2); view['project']['nested'].append(3)
        self.assertEqual(self.store.get(view['id'], self.context)['project'], {'nested': [1]})
        self.assertEqual(view['expires_at'], 1010)
    def test_capacity_is_explicit_and_expired_views_free_slots_and_cancel_work(self):
        view = self.create(); holder = {}; self.store.attach(view['id'], self.context, holder); self.create()
        with self.assertRaises(PreviewUnavailable): self.create()
        self.clock[0] = 30; self.create(); self.assertTrue(holder['cancelled']); self.assertEqual(len(self.store.views), 1)
    def test_release_is_idempotent_and_cancels_attached_but_not_detached_work(self):
        view = self.create(); a, b = {}, {}; self.store.attach(view['id'], self.context, a); self.store.attach(view['id'], self.context, b)
        self.store.detach(view['id'], b); self.assertTrue(self.store.release(view['id'])); self.assertFalse(self.store.release(view['id']))
        self.assertTrue(a['cancelled']); self.assertEqual(b, {})
    def test_changed_revision_or_storage_identity_retires_snapshot(self):
        for key in self.context:
            view = self.create(); context = {**self.context, key: 'different'}
            with self.assertRaises(PreviewUnavailable): self.store.get(view['id'], context)
            self.assertNotIn(view['id'], self.store.views)
    def test_plan_includes_sequence_media_and_unknown_fields_but_ignores_decision_metadata(self):
        project = {'name': 'A', 'media': {}, 'sequences': [], 'extra': None}
        original = plan_digest(project)
        self.assertEqual(original, plan_digest({**project, 'updated': 2, 'proposals': [{}]}))
        for field, value in [('media', {'m': {}}), ('sequences', [{}]), ('extra', False)]:
            self.assertNotEqual(original, plan_digest({**project, field: value}))


class ImageResponse:
    def __init__(self, content, **kwargs): self.body = content; self.options = kwargs


class ProposalPreviewTests(ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        names = {'proposals_add', 'proposal_selection', 'prepare_proposal', 'proposal_preview_error', 'current_proposal_preview',
                 'proposal_preview', 'release_proposal_preview', 'proposal_preview_frame', 'decide_proposal_items', 'proposal_batch_decide'}
        tree = ast.parse((ROOT / 'backend/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        for node in nodes: node.decorator_list = []
        self.store = PreviewStore()
        self.env.update(PROPOSAL_PREVIEWS=self.store, PreviewUnavailable=PreviewUnavailable, PROPOSAL_FRAME_SLOTS=threading.BoundedSemaphore(2),
                        math=math, Response=ImageResponse, seq_total=engine.seq_total, render_frame=engine.render_frame,
                        RenderContext=lambda **kw: RenderContext(scratch_parent=str(self.root), **kw))
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)
    def propose(self, ops=None):
        items = ops or [[{'op': 'set', 'path': '/name', 'value': 'Previewed'}]]
        return self.invoke('proposals_add', {'items': [{'ops': op} for op in items]})
    def preview(self, proposal, ids=None, context=None):
        return asyncio.run(self.env['proposal_preview'](proposal['id'], Request({'items': ids or [i['id'] for i in proposal['items']], '_context': context or self.current()})))
    def accept(self, view, **body):
        return asyncio.run(self.env['proposal_batch_decide'](view['proposal'], Request({'decision': 'accept', 'items': [{'id': i} for i in view['items']],
            '_context': self.current(), '_preview': {'id': view['id'], 'plan': view['plan']}, **body})))
    def saved(self): return json.loads(self.raw())
    def test_preview_is_read_only_and_accept_commits_the_exact_normalized_plan(self):
        proposal = self.propose([[{'op': 'set_clip', 'sequence': 'seq1', 'track': 'V1', 'clip': {'id': 'new', 'media_id': 'B', 'start': 1, 'in_': 0, 'out': .5}}]])
        before = self.raw(); files = {p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}
        view = self.preview(proposal); candidate = copy.deepcopy(self.store.get(view['id'], self.current())['project'])
        self.assertTrue(view['warnings']); self.assertTrue(view['changes']); self.assertNotIn('project', view)
        self.assertEqual({p.relative_to(self.root): p.read_bytes() for p in self.root.rglob('*') if p.is_file()}, files)
        result = self.accept(view); self.assertTrue(result['ok']); self.assertEqual(view['plan'], plan_digest(self.saved()))
        self.assertEqual(candidate['sequences'], self.saved()['sequences']); self.assertNotIn(view['id'], self.store.views)
        self.invoke('undo', {'_context': self.current()}); restored = self.saved(); prior = json.loads(before)
        restored.pop('updated', None); prior.pop('updated', None); self.assertEqual(restored, prior)
    def test_review_subset_order_and_digest_cannot_silently_change(self):
        proposal = self.propose([[{'op': 'set', 'path': '/name', 'value': 'A'}], [{'op': 'set', 'path': '/name', 'value': 'B'}]])
        view = self.preview(proposal); before = self.raw()
        for body in ({'items': [{'id': view['items'][0]}]}, {'items': [{'id': i} for i in reversed(view['items'])]}, {'_preview': {'id': view['id'], 'plan': 'wrong'}}):
            with self.subTest(body=body), self.assertRaises(HTTPError) as error: self.accept(view, **body)
            self.assertEqual(error.exception.status_code, 409); self.assertEqual(self.raw(), before)
        self.assertTrue(self.accept(view)['ok']); self.assertEqual(self.saved()['name'], 'B')
    def test_stale_review_cannot_accept_even_with_a_fresh_context(self):
        view = self.preview(self.propose()); self.edit('Human edit', self.current()); before = self.raw()
        with self.assertRaises(HTTPError) as error: self.accept(view)
        self.assertEqual(error.exception.status_code, 409); self.assertEqual(self.raw(), before)
    def test_other_project_with_identical_bytes_cannot_use_preview(self):
        view = self.preview(self.propose()); (self.root / 'projects/b/project.json').write_bytes(self.raw()); self.env['set_active_project']('b')
        with self.assertRaises(HTTPError) as error: self.accept(view)
        self.assertEqual(error.exception.status_code, 409)
    def test_preview_failure_in_a_later_item_never_exposes_or_saves_partial_result(self):
        proposal = self.propose([[{'op': 'remove', 'path': '/sequences/0/tracks/0'}], [{'op': 'set_clip', 'sequence': 'seq1', 'track': 'V1', 'clip': {'id': 'c1', 'out': 2}}]])
        before = self.raw()
        with self.assertRaises(HTTPError): self.preview(proposal)
        self.assertEqual(self.raw(), before); self.assertEqual(self.store.views, {})
    def test_nested_sequences_media_captions_and_schema_migration_are_in_the_accepted_plan(self):
        child = copy.deepcopy(self.doc['sequences'][0]); child.update(id='child', name='New child', captions=[{'start': 0, 'end': 1, 'text': 'Literal <text>'}])
        proposal = self.propose([[{'op': 'insert', 'path': '/sequences/1', 'value': child},
            {'op': 'set', 'path': '/media/A/path', 'value': "C:\\Émile's files\\new.mov"},
            {'op': 'set', 'path': '/version', 'value': 1},
            {'op': 'set_clip', 'sequence': 'seq1', 'track': 'V1', 'clip': {'id': 'c1', 'sequence_id': 'child', 'media_id': None}}]])
        view = self.preview(proposal); self.assertEqual([s['id'] for s in view['sequences']], ['seq1', 'child'])
        self.accept(view); self.assertEqual(view['plan'], plan_digest(self.saved())); self.assertEqual(self.saved()['version'], 3)
    def test_expired_stopped_or_wrong_proposal_review_leaves_project_untouched(self):
        proposal = self.propose(); view = self.preview(proposal); other = self.propose(); view = self.preview(proposal)
        before = self.raw()
        with self.assertRaises(HTTPError): self.accept({**view, 'proposal': other['id'], 'items': [other['items'][0]['id']]})
        self.env['release_proposal_preview'](view['id'])
        with self.assertRaises(HTTPError): self.accept(view)
        self.assertEqual(self.raw(), before)
    def test_real_frames_match_accepted_pixels_after_media_change_and_overlap_normalization(self):
        media = {}; colors = {'red': (255, 0, 0), 'blue': (0, 0, 255), 'green': (0, 255, 0)}
        for name, color in colors.items():
            path = self.root / ("Émile's " + name + '.png'); Image.new('RGB', (64, 48), color).save(path)
            media[name] = {'id': name, 'path': str(path), 'is_image': True, 'has_video': True, 'has_audio': False, 'duration': 2, 'width': 64, 'height': 48, 'fps': 24}
        doc = {'version': 3, 'id': 'real', 'name': 'Frame parity', 'media': media, 'sequences': [{'id': 's', 'width': 64, 'height': 48, 'fps': 24,
            'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [{'id': 'base', 'media_id': 'red', 'start': 0, 'in_': 0, 'out': 2}]}]}]}
        self.env['save_project'](doc)
        proposal = self.propose([[{'op': 'set', 'path': '/media/red/path', 'value': media['green']['path']},
            {'op': 'set_clip', 'sequence': 's', 'track': 'V1', 'clip': {'id': 'insert', 'media_id': 'blue', 'start': .5, 'in_': 0, 'out': .5}}]])
        view = self.preview(proposal); before = self.raw(); hashes = {m['path']: hashlib.sha256(Path(m['path']).read_bytes()).hexdigest() for m in media.values()}
        rendered = []
        for t, color in [(0, colors['green']), (.5, colors['blue']), (1, colors['green'])]:
            response = self.env['proposal_preview_frame'](view['id'], 's', t)
            self.assertEqual(response.options['headers']['Cache-Control'], 'no-store')
            with Image.open(io.BytesIO(response.body)) as image: pixels = image.convert('RGB'); actual = pixels.getpixel((32, 24)); rendered.append(pixels.tobytes())
            for a, b in zip(actual, color): self.assertLessEqual(abs(a-b), 3)
            self.assertEqual(self.raw(), before); self.assertFalse(list(self.root.glob('filmocity-render-*')))
        self.accept(view)
        for i, t in enumerate([0, .5, 1]):
            out = self.root / 'accepted.png'
            with RenderContext(scratch_parent=str(self.root), stall_timeout=15) as context: engine.render_frame(self.saved(), 's', t, str(out), context=context)
            with Image.open(out) as image: self.assertEqual(image.convert('RGB').tobytes(), rendered[i])
        self.assertEqual(hashes, {path: hashlib.sha256(Path(path).read_bytes()).hexdigest() for path in hashes})
    def test_invalid_frame_bounds_do_not_allocate_a_preview(self):
        proposal = self.propose([[{'op': 'set', 'path': '/sequences/0/duration', 'value': 'invalid'}]])
        before = self.raw()
        with self.assertRaises(HTTPError) as error: self.preview(proposal)
        self.assertEqual(error.exception.status_code, 422); self.assertEqual(self.store.views, {}); self.assertEqual(self.raw(), before)
    def test_frame_bounds_and_capacity_do_not_leak_render_slots(self):
        view = self.preview(self.propose())
        for sid, t in [('missing', 0), ('seq1', math.nan), ('seq1', math.inf), ('seq1', -1), ('seq1', 100)]:
            with self.assertRaises(HTTPError): self.env['proposal_preview_frame'](view['id'], sid, t)
        semaphore = self.env['PROPOSAL_FRAME_SLOTS']; self.assertTrue(semaphore.acquire(False)); self.assertTrue(semaphore.acquire(False))
        with self.assertRaises(HTTPError) as error: self.env['proposal_preview_frame'](view['id'], 'seq1', 0)
        self.assertEqual(error.exception.status_code, 429); semaphore.release(); semaphore.release()
        self.assertEqual(self.store.get(view['id'], self.current())['_holders'], {})
    def test_close_or_project_change_during_render_discards_frame_and_cleans_owned_scratch(self):
        for close in [False, True]:
            view = self.preview(self.propose())
            def render(project, sid, t, out, *, proc_holder, context):
                Path(out).write_bytes(b'frame')
                if close:
                    self.env['release_proposal_preview'](view['id']); self.assertTrue(proc_holder['cancelled']); context.check_cancelled()
                else: self.edit('During render', self.current())
            with patch.dict(self.env, render_frame=render), self.assertRaises(HTTPError) as error:
                self.env['proposal_preview_frame'](view['id'], 'seq1', 0)
            self.assertEqual(error.exception.status_code, 409); self.assertFalse(list(self.root.glob('filmocity-render-*')))
    def test_failed_history_write_retains_review_and_pending_items_for_explicit_retry(self):
        view = self.preview(self.propose()); before = self.raw()
        with patch.dict(self.env, commit_edit=lambda *a: (_ for _ in ()).throw(OSError('disk full'))), self.assertRaises(OSError): self.accept(view)
        self.assertEqual(self.raw(), before); self.assertIn(view['id'], self.store.views)
        self.assertTrue(self.accept(view)['ok'])


if __name__ == '__main__': unittest.main(verbosity=2)
