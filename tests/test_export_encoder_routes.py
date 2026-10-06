"""Production export routes with real project files and controlled encoder results."""
import ast
import asyncio
import copy
import json
import queue
import threading
import unittest
from unittest import mock

import test_project_sync as sync
import test_rendered_preview as preview
from encoder_capabilities import CAPABILITIES
from render import png_sequence_directory


class ExportEncoderRoutes(sync.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        self.doc.update(media={}, sequences=[{'id': 's', 'name': 'Export', 'width': 320, 'height': 180,
                                              'fps': 30, 'duration': 1, 'captions': [], 'tracks': []}])
        for pid in ('a', 'b'): (self.root / f'projects/{pid}/project.json').write_text(json.dumps(self.doc))
        self.env.update(asyncio=asyncio, JOBS={}, RENDER_Q=queue.Queue(), RENDER_STATE_LOCK=threading.RLock(),
                        render_workers=lambda: None, png_sequence_directory=png_sequence_directory)
        names = {'preview_state', 'inspect_export_resources', 'check_export_resources', 'render_job',
                 'render_preflight', 'render_all', 'start_render', '_record_render_event'}
        tree = ast.parse((sync.ROOT / 'backend/server.py').read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)
        self.check = mock.patch.object(CAPABILITIES, 'check', return_value={'ok': True, 'encoder': 'libx264', 'message': 'controlled encode check'}).start()
        self.addCleanup(mock.patch.stopall)

    def invoke(self, name, body=None):
        return preview.RenderedPreview.run_async(self, self.env[name](sync.Request(body or {'sequence': 's', 'preset': {}})))

    def test_preflight_and_export_block_failed_hardware_before_creating_jobs(self):
        self.check.return_value = {'ok': False, 'encoder': 'h264_nvenc', 'message': 'GPU driver unavailable'}
        body = {'sequence': 's', 'preset': {'vcodec': 'h264_nvenc'}}
        report = self.invoke('render_preflight', body)
        self.assertFalse(report['ok']); self.assertEqual(report['issues'][-1]['code'], 'encoder_unavailable')
        for route in ('render_job', 'render_all'):
            with self.assertRaises(sync.HTTPError) as error: self.invoke(route, body)
            self.assertEqual(error.exception.status_code, 422)
        self.assertFalse(self.env['JOBS']); self.assertTrue(self.env['RENDER_Q'].empty())

    def test_wrong_format_is_rejected_without_probing_and_fixed_formats_bypass_delivery_check(self):
        report = self.invoke('render_preflight', {'sequence': 's', 'preset': {'format': 'h264', 'vcodec': 'hevc_qsv'}})
        self.assertFalse(report['ok']); self.check.assert_not_called()
        report = self.invoke('render_preflight', {'sequence': 's', 'preset': {'format': 'png_sequence', 'vcodec': 'hevc_qsv'}})
        self.assertTrue(report['ok']); self.check.assert_not_called()

    def test_project_switch_during_slow_probe_cannot_queue_or_return_valid_preflight(self):
        for route in ('render_job', 'render_all', 'render_preflight'):
            with self.subTest(route=route):
                self.env['set_active_project']('a')
                def check(*args):
                    self.env['set_active_project']('b'); return {'ok': True}
                self.check.side_effect = check
                with self.assertRaises(sync.HTTPError) as error: self.invoke(route)
                self.assertEqual(error.exception.status_code, 409)
                self.assertFalse(self.env['JOBS'])

    def test_saved_edit_during_probe_invalidates_export_check(self):
        def check(*args):
            changed = copy.deepcopy(self.doc); changed['name'] = 'Edited during probe'
            (self.root / 'projects/a/project.json').write_text(json.dumps(changed))
            return {'ok': True}
        self.check.side_effect = check
        with self.assertRaises(sync.HTTPError) as error: self.invoke('render_job')
        self.assertEqual(error.exception.status_code, 409); self.assertFalse(self.env['JOBS'])

    def test_success_binds_preflight_job_snapshot_and_audit_to_original_project(self):
        report = self.invoke('render_preflight'); context = report['context']
        job = self.invoke('render_job', {'sequence': 's', 'preset': {}, '_context': context})
        self.assertEqual(job['context'], context)
        item = self.env['RENDER_Q'].get_nowait(); self.assertEqual(item[1]['name'], self.doc['name'])
        self.env['set_active_project']('b')
        self.env['_record_render_event'](job, 'human')
        events = self.root / 'projects/a/events.jsonl'
        self.assertTrue(events.is_file()); self.assertIn(job['id'], events.read_text())
        other = self.root / 'projects/b/events.jsonl'
        self.assertNotIn(job['id'], other.read_text() if other.exists() else '')

    def test_stale_client_context_is_rejected_before_encoder_work(self):
        report = self.invoke('render_preflight'); self.check.reset_mock()
        self.env['set_active_project']('b')
        with self.assertRaises(sync.HTTPError) as error:
            self.invoke('render_job', {'sequence': 's', '_context': report['context']})
        self.assertEqual(error.exception.status_code, 409); self.check.assert_not_called()


if __name__ == '__main__': unittest.main(verbosity=2)
