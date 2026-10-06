"""Real H11 controls and the exact completed-read close race, without a listener."""
import ast
import asyncio
import http.client
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import h11
import uvicorn
from uvicorn.protocols.http.h11_impl import H11Protocol
from uvicorn.server import ServerState
from http_protocol import FilmocityH11Protocol


class MemoryTransport(asyncio.Transport):
    def __init__(self):
        self.closing = False
        self.output = bytearray()
        self.protocol = None
        self.paused = False

    def get_extra_info(self, name, default=None):
        return {'sockname': ('127.0.0.1', 53355), 'peername': ('127.0.0.1', 50000)}.get(name, default)

    def is_closing(self): return self.closing
    def close(self): self.closing = True
    def write(self, data): self.output.extend(data)
    def pause_reading(self): self.paused = True
    def resume_reading(self): self.paused = False
    def set_protocol(self, protocol): self.protocol = protocol


def urllib_probe():
    class CaptureConnection(http.client.HTTPConnection):
        def __init__(self):
            super().__init__('127.0.0.1:53355'); self.raw = bytearray()
        def send(self, data): self.raw.extend(data)
    connection = CaptureConnection()
    connection.request('GET', '/api/version', headers={'Host': '127.0.0.1:53355',
                       'User-Agent': 'Python-urllib/3.12', 'Connection': 'close'})
    return bytes(connection.raw)


class HttpProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.protocols = []

    async def asyncTearDown(self):
        for protocol, transport, state in self.protocols:
            protocol._unset_keepalive_if_required()
            transport.close()
            protocol.connection_lost(None)
            pending = tuple(state.tasks)
            for task in pending: task.cancel()
            if pending: await asyncio.gather(*pending, return_exceptions=True)

    def fixture(self, protocol_class=FilmocityH11Protocol, app=None, ws=None):
        dispatched = []
        if app is None:
            async def app(scope, receive, send):
                dispatched.append(scope['path'])
                await send({'type': 'http.response.start', 'status': 204, 'headers': []})
                await send({'type': 'http.response.body', 'body': b''})
        config = uvicorn.Config(app, http=protocol_class, ws=ws, lifespan='off', log_config=None, log_level=None)
        config.load()
        state = ServerState()
        protocol = config.http_protocol_class(config, state, {}, _loop=asyncio.get_running_loop())
        transport = MemoryTransport(); protocol.connection_made(transport)
        self.protocols.append((protocol, transport, state))
        return protocol, transport, state, dispatched

    async def settle(self, state):
        async def drain():
            while state.tasks:
                await asyncio.gather(*tuple(state.tasks))
                await asyncio.sleep(0)
        await asyncio.wait_for(drain(), 2)

    def completed_proactor_read(self, protocol, raw):
        # Use the actual Windows callback implementation, bypassing only its
        # socket/IOCP constructor. No test byte is sent over a network.
        from asyncio.proactor_events import _ProactorReadPipeTransport
        callback = object.__new__(_ProactorReadPipeTransport)
        callback._sock = None; callback._protocol = protocol; callback._paused = False
        callback._data = bytearray(raw); callback._closing = True
        future = asyncio.get_running_loop().create_future(); future.set_result(len(raw))
        callback._read_fut = future
        callback._loop_reading(future)

    @unittest.skipUnless(sys.platform == 'win32', 'Exact Proactor callback is Windows-specific; portable close/read tests also run')
    async def test_upstream_reproduces_exact_130_byte_completed_read_error_before_fix(self):
        protocol, transport, state, dispatched = self.fixture(H11Protocol)
        raw = urllib_probe(); self.assertEqual(len(raw), 130)
        protocol.shutdown(); self.assertIs(protocol.conn.our_state, h11.CLOSED)
        with self.assertLogs('uvicorn.error', level='WARNING') as logs:
            with self.assertRaisesRegex(h11.LocalProtocolError, 'Response when role=SERVER and state=CLOSED'):
                self.completed_proactor_read(protocol, raw)
        self.assertIn('Invalid HTTP request', logs.output[0])
        self.assertEqual(dispatched, []); self.assertEqual(transport.output, b'')

    @unittest.skipUnless(sys.platform == 'win32', 'Exact Proactor callback is Windows-specific; portable close/read tests also run')
    async def test_completed_proactor_read_after_shutdown_drops_bytes_without_false_400(self):
        protocol, transport, state, dispatched = self.fixture()
        raw = urllib_probe(); self.assertEqual(len(raw), 130)
        protocol.shutdown()
        with self.assertNoLogs('uvicorn.error', level='WARNING'):
            self.completed_proactor_read(protocol, raw)
        self.assertIs(protocol.conn.our_state, h11.CLOSED)
        self.assertEqual(dispatched, []); self.assertEqual(transport.output, b'')

    async def test_closing_transport_drops_read_even_before_h11_close_notification(self):
        protocol, transport, state, dispatched = self.fixture()
        self.assertIs(protocol.conn.our_state, h11.IDLE)
        transport.close()
        with self.assertNoLogs('uvicorn.error', level='WARNING'): protocol.data_received(urllib_probe())
        self.assertIs(protocol.conn.our_state, h11.IDLE)
        self.assertEqual(dispatched, []); self.assertEqual(transport.output, b'')

    async def test_closed_h11_drops_late_read_without_relying_on_transport_flag(self):
        protocol, transport, state, dispatched = self.fixture()
        protocol.conn.send(h11.ConnectionClosed())
        self.assertFalse(transport.is_closing())
        with self.assertNoLogs('uvicorn.error', level='WARNING'): protocol.data_received(urllib_probe())
        self.assertIs(protocol.conn.our_state, h11.CLOSED)
        self.assertEqual(dispatched, []); self.assertEqual(transport.output, b'')

    async def test_normal_request_dispatches_and_responds(self):
        protocol, transport, state, dispatched = self.fixture()
        with self.assertNoLogs('uvicorn.error', level='WARNING'):
            protocol.data_received(urllib_probe()); await self.settle(state)
        self.assertEqual(dispatched, ['/api/version'])
        self.assertIn(b'HTTP/1.1 204 No Content', transport.output)
        self.assertEqual(state.total_requests, 1)

    async def test_keepalive_accepts_next_request_after_first_response(self):
        protocol, transport, state, dispatched = self.fixture()
        for path in ('/first', '/second'):
            protocol.data_received(f'GET {path} HTTP/1.1\r\nHost: fixture\r\n\r\n'.encode())
            await self.settle(state)
            self.assertFalse(transport.is_closing())
        self.assertEqual(dispatched, ['/first', '/second'])
        self.assertEqual(transport.output.count(b'HTTP/1.1 204 No Content'), 2)
        self.assertEqual(state.total_requests, 2)

    async def test_pipelined_requests_are_resumed_in_order_without_dropped_data(self):
        protocol, transport, state, dispatched = self.fixture()
        protocol.data_received(b'GET /one HTTP/1.1\r\nHost: fixture\r\n\r\nGET /two HTTP/1.1\r\nHost: fixture\r\n\r\n')
        await self.settle(state)
        self.assertEqual(dispatched, ['/one', '/two'])
        self.assertFalse(transport.is_closing())
        self.assertEqual(transport.output.count(b'HTTP/1.1 204 No Content'), 2)
        self.assertEqual(state.total_requests, 2)

    async def test_shutdown_keeps_active_request_body_draining_before_close(self):
        received = bytearray(); entered = asyncio.Event()
        async def app(scope, receive, send):
            entered.set()
            while True:
                event = await receive(); received.extend(event.get('body', b''))
                if not event.get('more_body'): break
            await send({'type': 'http.response.start', 'status': 204, 'headers': []})
            await send({'type': 'http.response.body', 'body': b''})
        protocol, transport, state, dispatched = self.fixture(app=app)
        protocol.data_received(b'POST /body HTTP/1.1\r\nHost: fixture\r\nContent-Length: 4\r\n\r\nab')
        await asyncio.wait_for(entered.wait(), 2)
        protocol.shutdown(); self.assertFalse(transport.is_closing())
        protocol.data_received(b'cd'); await self.settle(state)
        self.assertEqual(received, b'abcd')
        self.assertIn(b'HTTP/1.1 204 No Content', transport.output)
        self.assertTrue(transport.is_closing()); self.assertEqual(state.total_requests, 1)

    async def test_invalid_request_while_open_still_warns_and_returns_400(self):
        protocol, transport, state, dispatched = self.fixture()
        with self.assertLogs('uvicorn.error', level='WARNING') as logs:
            protocol.data_received(b'not an HTTP request\r\n\r\n')
        self.assertIn('Invalid HTTP request received', logs.output[0])
        self.assertIn(b'HTTP/1.1 400 Bad Request', transport.output)
        self.assertEqual(dispatched, [])

    async def test_unrelated_open_protocol_failure_is_not_swallowed(self):
        protocol, transport, state, dispatched = self.fixture()
        with patch.object(H11Protocol, 'data_received', side_effect=RuntimeError('unrelated failure')):
            with self.assertRaisesRegex(RuntimeError, 'unrelated failure'): protocol.data_received(urllib_probe())

    async def test_websocket_upgrade_still_hands_request_to_configured_protocol(self):
        upgrades = []
        class UpgradeProtocol:
            def __init__(self, **kwargs): upgrades.append(self)
            def connection_made(self, transport): self.transport = transport
            def data_received(self, raw): self.raw = raw
        protocol, transport, state, dispatched = self.fixture(ws=UpgradeProtocol)
        protocol.data_received(b'GET /ws HTTP/1.1\r\nHost: fixture\r\nConnection: Upgrade\r\nUpgrade: websocket\r\n'
                               b'Sec-WebSocket-Version: 13\r\nSec-WebSocket-Key: dGhlIHNhbXBsZSBub25jZQ==\r\n\r\n')
        self.assertEqual(len(upgrades), 1); self.assertIs(transport.protocol, upgrades[0])
        self.assertIn(b'GET /ws HTTP/1.1', upgrades[0].raw)
        self.assertIn(b'upgrade: websocket', upgrades[0].raw)
        self.assertNotIn(protocol, state.connections); self.assertEqual(dispatched, [])


class HttpEntrypointTests(unittest.TestCase):
    def test_both_actual_entrypoint_configurations_select_local_protocol(self):
        # Execute the real Config/run call expressions with controlled runner
        # capture, without importing server (which owns threads and storage).
        for relative, method in (('packaging/filmocity_app.py', 'Config'), ('backend/server.py', 'run')):
            with self.subTest(entrypoint=relative):
                tree = ast.parse((ROOT / relative).read_text(encoding='utf-8'))
                calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                         and isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name)
                         and node.func.value.id == 'uvicorn' and node.func.attr == method]
                self.assertEqual(len(calls), 1)
                found = []
                class Capture:
                    def Config(self, *args, **kwargs): found.append((args, kwargs))
                    def run(self, *args, **kwargs): found.append((args, kwargs))
                from types import SimpleNamespace
                app = object()
                env = {'uvicorn': Capture(), 'FilmocityH11Protocol': FilmocityH11Protocol,
                       'server': SimpleNamespace(app=app), 'app': app, 'port': 53355,
                       'a': SimpleNamespace(host='127.0.0.1', port=53355), 'REQUEST_SHUTDOWN_GRACE': 5}
                exec(compile(ast.Expression(calls[0]), relative, 'eval'), env)
                self.assertEqual(len(found), 1)
                self.assertIs(found[0][1]['http'], FilmocityH11Protocol)
                self.assertEqual(found[0][1]['timeout_graceful_shutdown'], 5)


if __name__ == '__main__': unittest.main(verbosity=2)
