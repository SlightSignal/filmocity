"""Production startup handshake and bounded shutdown without binding a listener."""
import ast
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from runtime_identity import wait_for_backend, workspace_key, read_build_info, stop_server, StartupError, ShutdownError, BACKEND_SHUTDOWN_TIMEOUT


class LaunchIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity launch É's "); self.addCleanup(self.temp.cleanup); self.root = self.temp.name
        self.now = 0; self.calls = []
    def sleep(self, seconds): self.now += seconds
    def response(self, **extra): return {'app': 'Filmocity', 'instance': 'new', 'workspace': workspace_key(self.root), 'pid': 42, **extra}
    def wait(self, responses, **extra):
        def open_(url, timeout):
            self.calls.append((url, timeout)); response = responses.pop(0) if len(responses) > 1 else responses[0]
            if isinstance(response, Exception): raise response
            return io.BytesIO(json.dumps(response).encode() if not isinstance(response, bytes) else response)
        return wait_for_backend('http://127.0.0.1:8787', instance='new', root=self.root, pid=42, opener=open_, clock=lambda: self.now, sleep=self.sleep, timeout=.4, **extra)
    def test_only_the_launched_process_workspace_and_nonce_can_become_ready(self):
        responses = [self.response(instance='old'), self.response(workspace='other'), self.response(pid=43), self.response()]
        self.assertEqual(self.wait(responses), self.response()); self.assertEqual(len(self.calls), 4)
    def test_occupied_port_or_old_release_never_passes_readiness(self):
        for body in [self.response(instance='old'), {'app': 'Filmocity', 'version': '0.46.4'}, {'app': 'Other'}]:
            self.now = 0
            with self.assertRaisesRegex(StartupError, 'Another process answered'): self.wait([body])
    def test_invalid_html_large_payloads_and_connection_failure_time_out(self):
        for body in [OSError('refused'), b'<html>error</html>', b'x' * 65537, [], None]:
            self.now = 0
            with self.assertRaises(StartupError): self.wait([body])
    def test_process_exit_short_circuits_polling_and_is_rechecked_after_the_response(self):
        with self.assertRaisesRegex(StartupError, 'exited before'): self.wait([self.response()], alive=lambda: False)
        self.assertFalse(self.calls)
        alive = iter([True, False])
        with self.assertRaisesRegex(StartupError, 'exited during'): self.wait([self.response()], alive=lambda: next(alive))
    def test_build_identity_is_optional_for_source_and_strict_for_packaged_metadata(self):
        self.assertIsNone(read_build_info(self.root)); path = Path(self.root) / 'build-info.json'
        path.write_text(json.dumps({'id': 'candidate', 'source_sha256': 'source', 'extra': 'excluded'}))
        self.assertEqual(read_build_info(self.root), {'id': 'candidate', 'source_sha256': 'source'})
        path.write_text('{}')
        with self.assertRaises(StartupError): read_build_info(self.root)
    def test_workspace_identity_matches_the_guarded_project_api(self):
        from project_sync import workspace_id
        self.assertEqual(workspace_key(self.root), workspace_id(self.root))
        self.assertEqual(workspace_key(self.root), workspace_key(str(Path(self.root) / 'child/..')))
    def test_graceful_shutdown_never_skips_lifespan_cleanup_to_hide_a_live_backend(self):
        class Server: should_exit = False; force_exit = False
        class Thread:
            def __init__(self, states): self.states = iter(states); self.joins = []
            def join(self, timeout): self.joins.append(timeout)
            def is_alive(self): return next(self.states)
        server, thread = Server(), Thread([False]); stop_server(server, thread)
        self.assertTrue(server.should_exit); self.assertFalse(server.force_exit); self.assertEqual(thread.joins, [BACKEND_SHUTDOWN_TIMEOUT])
        server, thread = Server(), Thread([True])
        with self.assertLogs('runtime_identity', level='ERROR') as captured:
            with self.assertRaisesRegex(ShutdownError, 'shutdown deadline'): stop_server(server, thread, timeout=.1)
        self.assertFalse(server.force_exit); self.assertEqual(thread.joins, [.1])
        self.assertIn('thread stack unavailable', captured.output[0])
        server, thread = Server(), Thread([False]); server.lifespan = SimpleNamespace(shutdown_failed=True)
        with self.assertRaisesRegex(ShutdownError, 'cleanup failed'): stop_server(server, thread)
    def test_version_route_reports_additive_identity_fields_without_revealing_data_paths(self):
        tree = ast.parse((ROOT / 'backend/server.py').read_text()); node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'version'); node.decorator_list = []
        env = dict(os=os, ROOT=self.root, VERSION='dev', SCHEMA=3, INSTANCE_ID='nonce', BUILD_INFO={'id': 'build'}, workspace_id=workspace_key)
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'backend/server.py', 'exec'), env)
        result = env['version'](); self.assertEqual(result['instance'], 'nonce'); self.assertEqual(result['pid'], os.getpid()); self.assertEqual(result['schema'], 3)
        self.assertNotIn(self.root, json.dumps(result)); self.assertEqual(result['build']['id'], 'build')


if __name__ == '__main__': unittest.main(verbosity=2)
