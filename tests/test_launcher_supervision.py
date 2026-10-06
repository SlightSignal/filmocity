"""Production launch paths with controlled server/process/window adapters, no listener."""
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend')); sys.path.insert(0, str(ROOT / 'packaging'))
import runtime_identity
from http_protocol import FilmocityH11Protocol


def module(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / file); value = importlib.util.module_from_spec(spec); spec.loader.exec_module(value); return value


class LauncherSupervisionTests(unittest.TestCase):
    def test_source_launcher_rejects_python_without_runtime_hashing_support(self):
        app = module('filmocity_python_fixture', 'launcher/bootstrap.py')
        with patch.object(sys, 'version_info', (3, 10, 16)), self.assertRaisesRegex(SystemExit, 'Python 3.11'):
            app.ensure_python()
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity launcher É's "); self.addCleanup(self.temp.cleanup); self.data = Path(self.temp.name) / 'isolated data'
    def desktop(self, failure=None):
        app = module('filmocity_desktop_fixture', 'packaging/filmocity_app.py'); calls = []; root = self.data
        class Server:
            def __init__(self, config): self.should_exit = False; self.force_exit = False; self.config = config
            def run(self): pass
        class Thread:
            def __init__(self, target, **kw): self.target = target; self.alive = False
            def start(self): self.alive = True
            def is_alive(self): return self.alive
            def join(self, timeout): calls.append(('join', timeout)); self.alive = False
        instances = []
        def new_server(config): instance = Server(config); instances.append(instance); return instance
        def ready(url, **kwargs):
            calls.append(('ready', url, kwargs)); self.assertTrue(kwargs['alive']()); self.assertEqual(kwargs['root'], str(root)); self.assertEqual(kwargs['pid'], os.getpid())
            if failure == 'startup': raise runtime_identity.StartupError('wrong backend')
        def window(*args):
            calls.append(('window', args))
            if failure == 'window': raise RuntimeError('window failed')
        server = SimpleNamespace(FRONT=str(ROOT / 'frontend'), INSTANCE_ID='process-nonce', app=object())
        # Lifetime binding is native acceptance; do not attach this test process
        # to a Windows job object when exercising the launcher repeatedly.
        lifetime = SimpleNamespace(bind_lifetime=lambda: calls.append(('lifetime',)))
        with patch.dict(sys.modules, server=server, windows_lifetime=lifetime, uvicorn=SimpleNamespace(Config=lambda *a, **kw: kw, Server=new_server)), \
             patch.dict(os.environ, {'FILMOCITY_ROOT': 'must not use inherited data', 'FILMOCITY_LAUNCH_ID': 'process-nonce'}), \
             patch.object(sys, 'argv', ['Filmocity.exe', '--data', str(root)]), patch.object(sys, 'path', list(sys.path)), \
             patch.object(app, 'free_port', return_value=9011), patch.object(app.threading, 'Thread', Thread), \
             patch.object(runtime_identity, 'wait_for_backend', side_effect=ready), patch.object(app, 'run_window', side_effect=window):
            if failure:
                with self.assertRaises(RuntimeError): app.main()
            else: app.main()
        return calls, instances[0]
    def test_desktop_uses_explicit_data_and_waits_for_its_own_backend_before_opening_a_window(self):
        calls, server = self.desktop(); self.assertEqual([call[0] for call in calls], ['lifetime', 'ready', 'window', 'join'])
        self.assertEqual(calls[1][1], 'http://127.0.0.1:9011'); self.assertTrue(server.should_exit); self.assertTrue(self.data.is_dir())
        self.assertEqual(server.config['timeout_graceful_shutdown'], runtime_identity.REQUEST_SHUTDOWN_GRACE)
        self.assertIs(server.config['http'], FilmocityH11Protocol)
    def test_startup_or_window_failure_always_stops_the_owned_backend(self):
        for failure in ['startup', 'window']:
            calls, server = self.desktop(failure); self.assertTrue(server.should_exit); self.assertEqual(calls[-1], ('join', runtime_identity.BACKEND_SHUTDOWN_TIMEOUT))
            if failure == 'startup': self.assertNotIn('window', [call[0] for call in calls])
    def test_headless_does_not_hide_an_unexpected_backend_exit(self):
        app = module('filmocity_headless_fixture', 'packaging/filmocity_app.py')
        with patch.dict(os.environ, FILMOCITY_HEADLESS='1'), self.assertRaisesRegex(RuntimeError, 'stopped unexpectedly'):
            app.run_window('http://127.0.0.1:9011', SimpleNamespace(is_alive=lambda: False))
    def test_pinned_ports_are_checked_before_using_them(self):
        app = module('filmocity_port_fixture', 'packaging/filmocity_app.py')
        for value in ['0', '-1', '65536', 'not-a-port']:
            with patch.dict(os.environ, FILMOCITY_PORT=value), self.assertRaises(ValueError): app.free_port()
        with patch.dict(os.environ, FILMOCITY_PORT='9011'): self.assertEqual(app.free_port(), 9011)
    def test_native_window_failure_does_not_open_an_unowned_browser_fallback(self):
        app = module('filmocity_window_failure_fixture', 'packaging/filmocity_app.py')
        browser = SimpleNamespace(open=lambda *a: self.fail('A failed native window must not leave a browser fallback server'))
        native = SimpleNamespace(settings={}, create_window=lambda *a, **kw: None, start=lambda: (_ for _ in ()).throw(RuntimeError('window failed')))
        with patch.dict(sys.modules, webview=native, webbrowser=browser), patch.dict(os.environ, FILMOCITY_HEADLESS='0'):
            with self.assertRaisesRegex(RuntimeError, 'window failed'):
                app.run_window('http://127.0.0.1:9011', SimpleNamespace(is_alive=lambda: True))
    def test_frozen_missing_webview_retires_backend_instead_of_waiting_without_a_console(self):
        app = module('filmocity_frozen_window_failure_fixture', 'packaging/filmocity_app.py')
        with patch.dict(sys.modules, webview=None), patch.object(sys, 'frozen', True, create=True), patch.dict(os.environ, FILMOCITY_HEADLESS='0'):
            with self.assertRaises(ImportError): app.run_window('http://127.0.0.1:9011', SimpleNamespace(is_alive=lambda: True))
    def source(self, failing=False):
        app = module('filmocity_source_fixture', 'launcher/bootstrap.py'); calls = []; environments = []
        class Process:
            pid = 72
            def __init__(self): self.code = None; self.stopping = False; self.killed = False
            def poll(self): return self.code
            def send_signal(self, value): calls.append('signal'); self.stopping = True
            def terminate(self): calls.append('terminate'); self.stopping = True
            def kill(self): calls.append('kill'); self.killed = True
            def wait(self, timeout=None):
                calls.append(('wait', timeout))
                if self.stopping and not self.killed: raise subprocess.TimeoutExpired('server', timeout)
                self.code = 0; return 0
        proc = Process()
        def start(args, env, **options):
            self.assertEqual(options, {'creationflags': subprocess.CREATE_NEW_PROCESS_GROUP} if app.IS_WIN else {})
            environments.append(env); self.assertIn('--root', args); self.assertIn(str(self.data), args); return proc
        def ready(url, **kw):
            calls.append('ready'); self.assertEqual(kw['pid'], proc.pid); self.assertEqual(kw['instance'], environments[0]['FILMOCITY_LAUNCH_ID']); self.assertEqual(kw['root'], str(self.data))
            if failing: raise runtime_identity.StartupError('startup failed')
        lifetime = SimpleNamespace(bind_lifetime=lambda: None)
        with patch.dict(sys.modules, windows_lifetime=lifetime), patch.object(app, 'PY', sys.executable), patch.object(app, 'have_ffmpeg', return_value=True), patch.object(app, 'say'), \
             patch.object(app, 'ensure_environment_runtime'), \
             patch.object(app.subprocess, 'Popen', side_effect=start), patch.object(app.webbrowser, 'open', side_effect=lambda *a: calls.append('browser')), \
             patch.object(sys, 'path', list(sys.path)), patch.object(runtime_identity, 'wait_for_backend', side_effect=ready):
            if failing:
                with self.assertRaises(runtime_identity.StartupError): app.serve(port=9011, data=str(self.data))
            else: app.serve(port=9011, data=str(self.data))
        return calls, environments
    def test_source_launcher_waits_for_the_child_identity_before_opening_the_browser(self):
        calls, envs = self.source(); self.assertEqual(calls, ['ready', 'browser', ('wait', None)])
        self.assertTrue(envs[0]['FILMOCITY_LAUNCH_ID']); self.assertEqual(envs[0]['PYTHONUTF8'], '1')
    def test_failed_source_startup_never_opens_browser_and_terminates_only_its_child(self):
        calls, _ = self.source(True); self.assertEqual(calls, ['ready', 'signal', ('wait', 20), 'terminate', ('wait', 5), 'kill', ('wait', 5)])
    def test_source_child_that_finishes_gracefully_is_never_terminated(self):
        app = module('filmocity_source_stop_fixture', 'launcher/bootstrap.py'); calls = []
        child = SimpleNamespace(poll=lambda: None, send_signal=lambda value: calls.append('signal'), wait=lambda **kw: calls.append(('wait', kw['timeout'])),
                                terminate=lambda: self.fail('A retired child must not be terminated'), kill=lambda: self.fail('A retired child must not be killed'))
        app.stop_child(child); self.assertEqual(calls, ['signal', ('wait', 20)])


if __name__ == '__main__': unittest.main(verbosity=2)
