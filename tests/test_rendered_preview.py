"""Production preview routes/store with controlled queue/framework and real resources."""
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from PIL import Image
from test_project_sync import ProjectStoreFixture, ROOT, Request, HTTPError
from preview_binding import preview_binding, PREVIEW_PRESET
import render
from preflight import inspect_resources


class RenderedPreview(ProjectStoreFixture):
    def run_async(self, coroutine):
        # Keep a deadline timer during executor shutdown on the restricted host;
        # production route work and background filesystem reads still execute.
        def loop_factory():
            loop = asyncio.new_event_loop()
            def tick(): loop.call_later(.02, tick)
            loop.call_later(.02, tick)
            return loop
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(coroutine)

    def invoke(self, route, body):
        return self.run_async(self.env[route](Request(body)))

    def setUp(self):
        super().setUp()
        self.source = self.root / "source é's.png"
        Image.new('RGB', (64, 64), (160, 70, 30)).save(self.source)
        self.doc.update(media={'m': {'id': 'm', 'path': str(self.source), 'is_image': True, 'has_video': True,
            'has_audio': False, 'duration': 1, 'width': 64, 'height': 64}}, sequences=[{'id': 's', 'name': 'Preview',
            'width': 64, 'height': 64, 'fps': 24, 'duration': 1, 'captions': [], 'in_point': .25, 'out_point': .75,
            'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [
                {'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': 1}]}]}])
        for pid in ('a', 'b'):
            (self.root / 'projects' / pid / 'project.json').write_text(json.dumps(self.doc))
        self.env.update(asyncio=asyncio, JOBS={}, RENDER_Q=queue.Queue(), RENDER_STATE_LOCK=threading.RLock(),
            render_workers=lambda: None, PREVIEW_PRESET=PREVIEW_PRESET, preview_binding=preview_binding,
            inspect_resources=inspect_resources, segment_boundaries=render.segment_boundaries,
            chunk_key=render.chunk_key, chunk_sequence=render.chunk_sequence, index_sequence=render.index_sequence)
        names = {'preview_state', 'preview_descriptor', 'render_segments', 'existing_preview_request',
                 'render_preview', 'validate_render_preview', 'check_render_resources', 'start_render', '_record_render_event'}
        tree = ast.parse((ROOT / 'backend/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        for n in nodes: n.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    def start(self, **extra):
        return self.invoke('render_preview', {'sequence': 's', '_context': self.current(), **extra})

    def finish(self, job):
        path = self.root / job['out'].lstrip('/')
        path.write_bytes(b'completed output placeholder')
        job['status'] = 'done'
        return path

    def validate(self, job, context=None):
        return self.run_async(self.env['validate_render_preview'](job['id'], Request({'_context': context or self.current()})))

    def test_preview_binding_reports_quantized_picture_range_without_rewriting_the_project(self):
        project = copy.deepcopy(self.doc)
        project['sequences'][0].update(fps=29.97, in_point=.034, out_point=.134)
        before = copy.deepcopy(project)
        binding = preview_binding(project, self.current(), 's', True)
        self.assertEqual(binding['range'], [1001/30000, 4*1001/30000])
        self.assertEqual(project, before)

    def test_captures_saved_revision_and_range_without_mutating_project(self):
        before = self.raw(); context = self.current()
        job = self.start(range=True)
        self.assertEqual(self.raw(), before)
        self.assertEqual(job['preview']['context'], context)
        self.assertEqual(job['preview']['range'], [.25, .75])
        item = self.env['RENDER_Q'].get_nowait()
        self.assertFalse(item[3]['incremental'])
        self.assertFalse(item[3]['loudnorm'])
        self.assertTrue(item[3]['range'])
        self.edit('Later edit')
        self.assertNotEqual(item[1]['name'], self.env['load_project']()['name'])

    def test_real_server_descriptor_is_accepted_by_production_frontend_for_its_owned_job(self):
        node = os.environ.get('FILMOCITY_NODE') or shutil.which('node')
        if not node: self.skipTest('Node is required for the backend/frontend preview contract')
        job = self.start(range=True)
        self.finish(job)
        descriptor = self.validate(job)
        self.assertRegex(job['id'], r'^[a-f0-9]{16}$')
        self.assertRegex(descriptor['out'], rf'^/renders/job-{job["id"]}/preview_[a-f0-9]{{32}}\.mp4$')
        self.assertEqual(set(descriptor), {'id', 'out', 'preview'})
        script = r'''
const fs = require('node:fs'), assert = require('node:assert/strict');
const {create} = require('./frontend/rendered-preview.js');
const result = JSON.parse(fs.readFileSync(0, 'utf8'));
const view = {context: result.preview.context, sequence: result.preview.sequence, revision: 1};
let adopted = 0;
const controller = create({capture: () => view, ready: () => adopted++});
assert.equal(controller.adopt(result, view, true), true, 'Actual server preview descriptor must be playable');
assert.equal(controller.playable(result.preview.range[0]).out, result.out);
assert.equal(adopted, 1);
const foreign = {...result, id: result.id === '0000000000000000' ? '1111111111111111' : '0000000000000000'};
assert.equal(controller.adopt(foreign, view, true), false, 'Output folder must belong to returned job');
assert.equal(adopted, 1);
console.log(JSON.stringify({accepted: result.id, out: result.out, foreign_job_refused: true}));
'''
        result = subprocess.run([node, '-e', script], cwd=ROOT,
                                input=json.dumps(descriptor), text=True, capture_output=True,
                                timeout=15, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(json.loads(result.stdout)['accepted'], job['id'])

    def test_outputs_are_unique_across_same_sequence_ids_and_storage_projects(self):
        one = self.start(); two = self.start()
        self.env['set_active_project']('b'); three = self.start()
        self.assertEqual(len({j['out'] for j in (one, two, three)}), 3)
        self.assertNotEqual(one['preview']['context'], three['preview']['context'])

    def test_idempotent_request_identity_queues_once_and_rejects_reuse_for_other_edit(self):
        one = self.start(request_id='request-12345'); two = self.start(request_id='request-12345')
        self.assertIs(one, two); self.assertEqual(self.env['RENDER_Q'].qsize(), 1)
        self.edit('new edit')
        with self.assertRaises(HTTPError) as error: self.start(request_id='request-12345')
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(self.env['RENDER_Q'].qsize(), 1)

    def test_concurrent_retries_still_queue_one_job(self):
        original = self.env['check_render_resources']; barrier = threading.Barrier(2)
        def check(*args):
            barrier.wait(timeout=5); return original(*args)
        self.env['check_render_resources'] = check
        body = {'sequence': 's', '_context': self.current(), 'request_id': 'concurrent-123'}
        async def run():
            async def ticks():
                while True: await asyncio.sleep(.02)
            timer = asyncio.create_task(ticks())
            try: return await asyncio.gather(self.env['render_preview'](Request(body)), self.env['render_preview'](Request(body)))
            finally: timer.cancel()
        one, two = self.run_async(run())
        self.assertIs(one, two); self.assertEqual(self.env['RENDER_Q'].qsize(), 1)

    def test_edit_or_switch_during_preflight_prevents_queuing(self):
        original = self.env['check_render_resources']
        def check(*args):
            result = original(*args); self.env['set_active_project']('b'); return result
        self.env['check_render_resources'] = check
        with self.assertRaises(HTTPError) as error: self.start()
        self.assertEqual(error.exception.status_code, 409)
        self.assertTrue(self.env['RENDER_Q'].empty())

    def test_coverage_window_hashes_only_intersecting_segments_and_keeps_full_compatibility(self):
        project = copy.deepcopy(self.doc)
        seq = project['sequences'][0]
        seq['duration'] = 7200
        seq['tracks'][0]['clips'] = [dict(seq['tracks'][0]['clips'][0], id=str(i), start=i*6, out=6) for i in range(1200)]
        (self.root / 'projects/a/project.json').write_text(json.dumps(project))
        calls, indexes = [], []
        def key(project, chunk, preset, revisions):
            calls.append(chunk); return 'absent'
        def index(sequence):
            result = render.index_sequence(sequence); indexes.append(result); return result
        self.env.update(chunk_key=key, index_sequence=index)
        window = self.env['render_segments']('s', start=3600, end=3612)
        self.assertEqual(window['window'], [3600, 3612])
        self.assertEqual([(s['t0'], s['t1']) for s in window['segments']], [(3600, 3606), (3606, 3612)])
        self.assertEqual(len(calls), 2); self.assertEqual(len(indexes), 1)
        calls.clear(); indexes.clear()
        full = self.env['render_segments']('s')
        self.assertEqual(len(full['segments']), 1200); self.assertEqual(len(indexes), 1)
        self.assertIsNone(full['window'])
        self.assertEqual(window['segments'], full['segments'][600:602])

    def test_coverage_rejects_bad_ranges_and_changed_project_after_inspection(self):
        for start, end in [(None, 1), (0, None), (-1, 1), (1, 1), (2, 1), (0, float('inf')), (float('nan'), 1)]:
            with self.assertRaises(HTTPError) as error: self.env['render_segments']('s', start=start, end=end)
            self.assertEqual(error.exception.status_code, 422)
        original = self.env['chunk_key']
        def key(*args):
            result = original(*args); self.env['set_active_project']('b'); return result
        self.env['chunk_key'] = key
        with self.assertRaises(HTTPError) as error: self.env['render_segments']('s', start=0, end=1)
        self.assertEqual(error.exception.status_code, 409)

    def test_visible_coverage_still_rechecks_source_bytes_before_offering_playback(self):
        job = self.start(); self.finish(job)
        report = self.env['render_segments']('s', start=0, end=1)
        self.assertEqual(report['ready_preview']['id'], job['id'])
        Image.new('RGB', (64, 64), 'blue').save(self.source)
        report = self.env['render_segments']('s', start=0, end=1)
        self.assertIsNone(report['ready_preview'])
        self.assertTrue(all(not segment['rendered'] for segment in report['segments']))

    def test_invalid_in_out_is_rejected_before_a_job_is_created(self):
        for start, end in [(None, .5), (-1, .5), (.5, .5), (0, 2), (0, float('inf'))]:
            project = copy.deepcopy(self.doc); project['sequences'][0].update(in_point=start, out_point=end)
            with self.assertRaises(ValueError): preview_binding(project, self.current(), 's', True)
        self.assertTrue(self.env['RENDER_Q'].empty())

    def test_validation_rejects_changed_sources_audio_values_missing_output_and_pending_jobs(self):
        job = self.start()
        with self.assertRaises(HTTPError): self.validate(job)
        path = self.finish(job); self.assertEqual(self.validate(job)['id'], job['id'])
        original = self.source.read_bytes(); Image.new('RGB', (64, 64), 'blue').save(self.source)
        with self.assertRaises(HTTPError): self.validate(job)
        self.source.write_bytes(original)
        document = copy.deepcopy(self.doc); document['sequences'][0]['master'] = {'gain_db': -6}
        (self.root / 'projects/a/project.json').write_text(json.dumps(document))
        with self.assertRaises(HTTPError): self.validate(job)
        (self.root / 'projects/a/project.json').write_text(json.dumps(self.doc))
        path.unlink()
        with self.assertRaises(HTTPError): self.validate(job)

    def test_validation_rechecks_context_after_slow_fingerprinting(self):
        job = self.start(); self.finish(job)
        original = self.env['preview_descriptor']
        def descriptor(*args):
            result = original(*args); self.env['set_active_project']('b'); return result
        self.env['preview_descriptor'] = descriptor
        with self.assertRaises(HTTPError) as error: self.validate(job)
        self.assertEqual(error.exception.status_code, 409)

    def test_render_bar_only_adopts_a_validated_job_not_old_named_files(self):
        folder = self.root / 'renders'; folder.mkdir(exist_ok=True)
        (folder / 'preview_s.mp4').write_bytes(b'old unrelated output')
        report = self.env['render_segments']('s')
        self.assertIsNone(report['preview']); self.assertIsNone(report['ready_preview'])
        job = self.start(); self.finish(job)
        report = self.env['render_segments']('s')
        self.assertEqual(report['ready_preview']['id'], job['id'])
        self.assertTrue(all(x['rendered'] for x in report['segments']))
        self.env['set_active_project']('b')
        self.assertIsNone(self.env['render_segments']('s')['ready_preview'])

    def test_real_range_render_uses_captured_project_and_does_not_change_sources(self):
        job = self.start(range=True); item = self.env['RENDER_Q'].get_nowait()
        before = self.source.read_bytes(); self.edit('edited while rendering')
        # This host lacks libx264; only the encoder is substituted for the real
        # main graph/range/file publication exercise. Native H.264 is unverified.
        preset = dict(item[3], vcodec='mpeg4')
        render.render(item[1], item[2], item[6], preset)
        result = json.loads(__import__('subprocess').check_output(['ffprobe', '-v', 'error', '-show_format', '-of', 'json', item[6]]))
        self.assertAlmostEqual(float(result['format']['duration']), .5, delta=.06)
        self.assertEqual(self.source.read_bytes(), before)
        job['status'] = 'done'
        with self.assertRaises(HTTPError): self.validate(job)

    def test_preview_audit_event_keeps_its_captured_project_after_switching(self):
        job = self.start(); self.env['set_active_project']('b')
        self.env['_record_render_event'](job, 'human')
        self.assertEqual(job['event_persistence']['status'], 'recorded')
        path = self.root / 'projects/a/events.jsonl'
        self.assertTrue(path.is_file())
        self.assertFalse((self.root / 'projects/b/events.jsonl').exists())
        event = json.loads(path.read_text().splitlines()[-1])
        self.assertEqual(event['job']['id'], job['id'])


if __name__ == '__main__': unittest.main(verbosity=2)
