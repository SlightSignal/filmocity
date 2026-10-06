"""Live listener/lifespan retirement; separate from the server-free focused suite.

No editor window, user library, encoder or process outside this fixture is used.
Run in the installed build environment, which already provides Uvicorn.
"""
import asyncio
import concurrent.futures
import importlib.util
import os
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'packaging'))
from runtime_identity import stop_server, REQUEST_SHUTDOWN_GRACE
from http_protocol import FilmocityH11Protocol
import uvicorn


class NativeShutdownTests(unittest.TestCase):
    def test_actual_source_wrapper_retires_application_workers_after_window_returns(self):
        self.assertNotIn('server', sys.modules, 'This integration fixture needs a fresh process')
        spec = importlib.util.spec_from_file_location('filmocity_actual_close_fixture', ROOT / 'packaging/filmocity_app.py')
        launcher = importlib.util.module_from_spec(spec); spec.loader.exec_module(launcher)
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0)); port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory(prefix="Filmocity real close É's ") as root:
            owned = []
            def window(url, backend):
                import server
                from project_lifecycle import COPY_WORKER
                self.assertTrue(backend.is_alive()); self.assertEqual(server.ROOT, root)
                # Exercise both idle render admission and the actual non-daemon
                # copy executor. No media, editor input or export is started.
                server.render_workers()
                COPY_WORKER.executor.submit(lambda: None).result(timeout=2)
                owned.extend([backend, server.AUTOSAVE_THREAD, *server.TASKS.threads,
                              *server.RENDER_WORKERS, *COPY_WORKER.executor._threads])
                self.assertTrue(all(thread.is_alive() for thread in owned))
            try:
                with patch.object(sys, 'argv', ['Filmocity.exe', '--data', root]), patch.object(sys, 'path', list(sys.path)), \
                     patch.dict(os.environ, FILMOCITY_ROOT=root), patch.object(launcher, 'free_port', return_value=port), \
                     patch.object(launcher, 'run_window', side_effect=window):
                    launcher.main()
                self.assertGreaterEqual(len(owned), 5)
                self.assertTrue(all(not thread.is_alive() for thread in owned), [t.name for t in owned if t.is_alive()])
                with socket.socket() as probe: self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)
            finally:
                # The library lease lasts through process teardown in the app.
                # Only this explicitly ended fixture's lease is released here.
                if 'server' in sys.modules:
                    if not sys.modules['server'].SHUTDOWN.is_set() or any(t.is_alive() for t in owned):
                        sys.modules['server']._shutdown_workers(5)
                from workspace_lock import hold_workspace
                hold_workspace(root).close()

    def fixture(self, kind):
        entered = threading.Event(); cancelled = threading.Event(); cleaned = threading.Event()
        stop_work = threading.Event(); worker_started = threading.Event()
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix='Filmocity shutdown fixture')
        worker = executor.submit(lambda: (worker_started.set(), stop_work.wait()))
        async def app(scope, receive, send):
            if scope['type'] == 'lifespan':
                await receive(); await send({'type': 'lifespan.startup.complete'})
                await receive()
                stop_work.set()
                await asyncio.to_thread(executor.shutdown, wait=True)
                cleaned.set(); await send({'type': 'lifespan.shutdown.complete'})
            else:
                if scope['type'] == 'websocket':
                    await receive(); await send({'type': 'websocket.accept'})
                entered.set()
                # Deliberately keep a client request/close handshake in flight.
                # The finite request grace must cancel it, then run lifespan.
                try: await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set(); raise
        # Exercise the native launcher's production grace setting. The
        # launcher suite separately verifies it is supplied to Config.
        config = uvicorn.Config(app, host='127.0.0.1', port=0, log_level='critical', lifespan='on',
                                timeout_graceful_shutdown=REQUEST_SHUTDOWN_GRACE, http=FilmocityH11Protocol)
        server = uvicorn.Server(config)
        errors = []
        def run():
            try: server.run()
            except BaseException as error: errors.append(error)
        thread = threading.Thread(target=run, name='Filmocity owned fixture backend', daemon=True)
        thread.start(); client = None
        try:
            deadline = time.monotonic() + 5
            while not server.started and thread.is_alive() and time.monotonic() < deadline: time.sleep(.01)
            self.assertTrue(server.started); self.assertTrue(worker_started.wait(1))
            port = server.servers[0].sockets[0].getsockname()[1]
            if kind != 'idle':
                client = socket.create_connection(('127.0.0.1', port), timeout=2)
                request = 'GET /held HTTP/1.1\r\nHost: 127.0.0.1\r\n'
                if kind == 'websocket':
                    request += ('Upgrade: websocket\r\nConnection: Upgrade\r\n'
                                'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n')
                client.sendall((request + '\r\n').encode('ascii'))
                self.assertTrue(entered.wait(2))
                if kind == 'websocket': self.assertIn(b'101 Switching Protocols', client.recv(4096))
            started = time.monotonic(); stop_server(server, thread, timeout=REQUEST_SHUTDOWN_GRACE + 5)
            self.assertLess(time.monotonic() - started, REQUEST_SHUTDOWN_GRACE + 3)
            self.assertFalse(thread.is_alive()); self.assertFalse(server.force_exit)
            self.assertTrue(cleaned.is_set()); self.assertTrue(worker.done()); self.assertEqual(errors, [])
            if kind != 'idle': self.assertTrue(cancelled.is_set())
            with socket.socket() as probe: self.assertNotEqual(probe.connect_ex(('127.0.0.1', port)), 0)
        finally:
            if client is not None: client.close()
            # Retire only this fixture's resources, even on assertion failure.
            stop_work.set(); server.should_exit = True
            executor.shutdown(wait=True, cancel_futures=True)
            thread.join(REQUEST_SHUTDOWN_GRACE + 5)
            self.assertFalse(thread.is_alive(), 'Owned backend fixture failed to retire')

    def test_idle_native_backend_runs_worker_cleanup_and_releases_listener(self): self.fixture('idle')
    def test_pending_http_is_cancelled_before_lifespan_worker_cleanup(self): self.fixture('http')
    def test_unanswered_websocket_close_does_not_skip_lifespan_cleanup(self): self.fixture('websocket')


if __name__ == '__main__': unittest.main(verbosity=2)
