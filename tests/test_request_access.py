"""Real ASGI HTTP/WebSocket authorization against a disposable library."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = tempfile.TemporaryDirectory(prefix='filmocity-access-')
os.environ['FILMOCITY_ROOT'] = PRIVATE.name
os.environ.pop('FILMOCITY_TOKEN', None)
sys.path.insert(0, str(ROOT / 'backend'))
import server
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect
from starlette.requests import HTTPConnection
from request_access import access_error, authority, origin_tuple


class RequestAuthorityTests(unittest.TestCase):
    """Explicit ASGI socket scopes, not a browser or live LAN attack test."""
    def connection(self, host='127.0.0.1:8787', origin=None, server_address=('127.0.0.1', 8787),
                   scheme='http', extra=()):
        headers = [('host', host), *extra]
        if origin is not None:
            headers.append(('origin', origin))
        return HTTPConnection({'type': 'http', 'scheme': scheme, 'server': server_address,
            'path': '/api/project', 'method': 'GET', 'query_string': b'',
            'headers': [(key.lower().encode('ascii'), value.encode('ascii')) for key, value in headers]})

    def test_equivalent_loopback_ipv6_spellings_share_authority(self):
        for host in ('[::1]:8787', '[0:0:0:0:0:0:0:1]:8787'):
            for origin in ('http://[::1]:8787', 'http://[0:0:0:0:0:0:0:1]:8787'):
                with self.subTest(host=host, origin=origin):
                    self.assertIsNone(access_error(self.connection(host, origin, ('::1', 8787)), None))

    def test_expanded_socket_literal_matches_compressed_host(self):
        self.assertIsNone(access_error(self.connection('[2001:db8::a]:8787',
            'http://[2001:db8::a]:8787', ('2001:0db8:0:0:0:0:0:000a', 8787)), None))

    def test_actual_lan_socket_authority_does_not_trust_dns_or_other_ip(self):
        for address, host in (('192.0.2.25', '192.0.2.25:8787'),
                              ('2001:db8::a', '[2001:db8::a]:8787')):
            with self.subTest(address=address):
                self.assertIsNone(access_error(self.connection(host, 'http://' + host, (address, 8787)), None))
                for wrong in ('filmocity.example:8787', '192.0.2.26:8787', '[2001:db8::b]:8787'):
                    self.assertEqual(access_error(self.connection(wrong, server_address=(address, 8787)), None)[0], 403)

    def test_wildcard_scope_cannot_reflect_an_incoming_lan_authority(self):
        for address in ('0.0.0.0', '::', '0:0:0:0:0:0:0:0'):
            with self.subTest(address=address):
                self.assertEqual(access_error(self.connection('192.0.2.25:8787',
                    server_address=(address, 8787)), None)[0], 403)
                self.assertEqual(access_error(self.connection('[::]:8787',
                    server_address=(address, 8787)), None)[0], 403)

    def test_exact_scheme_port_and_host_alias_origin_are_required(self):
        for origin in ('https://127.0.0.1:8787', 'http://127.0.0.1:8788', 'http://localhost:8787'):
            self.assertEqual(access_error(self.connection(origin=origin), None)[0], 403)
        self.assertEqual(access_error(self.connection('127.0.0.1:8788'), None)[0], 403)

    def test_default_tls_port_and_websocket_scheme_are_normalized(self):
        self.assertEqual(authority('[::1]', 'https'), ('::1', 443))
        self.assertEqual(origin_tuple('https://[0:0:0:0:0:0:0:1]'), ('https', '::1', 443))
        self.assertIsNone(access_error(self.connection('[::1]', 'https://[::1]', ('::1', 443), 'wss'), None))
        self.assertEqual(access_error(self.connection('[::1]', 'http://[::1]', ('::1', 443), 'wss'), None)[0], 403)

    def test_scoped_literals_credentials_and_ambiguous_origin_are_refused(self):
        for host in ('[fe80::1%25eth0]:8787', '[::1]:65536', '127.0.0.1:8787#x', '127.0.0.1:8787?x'):
            with self.subTest(host=host):
                self.assertEqual(access_error(self.connection(host), None)[0], 400)
        for origin in ('null', 'http://user@127.0.0.1:8787', 'http://127.0.0.1:8787/path',
                       'http://127.0.0.1:8787?x', 'http://127.0.0.1:8787#x'):
            with self.subTest(origin=origin):
                self.assertEqual(access_error(self.connection(origin=origin), None)[0], 403)

    def test_duplicate_origin_is_not_combined_into_a_trusted_value(self):
        self.assertEqual(access_error(self.connection(origin='http://127.0.0.1:8787',
            extra=(('origin', 'https://external.invalid'),)), None)[0], 400)


class RequestAccessTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(server.app, base_url='http://127.0.0.1:8787')
        cls.client.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls.client.__exit__(None, None, None)
        # The process holds the library lock until exit; Windows cannot remove
        # the marker while held. Release only this test's own lock first.
        import workspace_lock
        workspace_lock.hold_workspace(PRIVATE.name).close()
        PRIVATE.cleanup()

    def setUp(self):
        server.TOKEN['value'] = None
        self.client.cookies.clear()

    def test_same_origin_and_originless_sdk_requests_succeed(self):
        for headers in ({}, {'Origin':'http://127.0.0.1:8787'}, {'Sec-Fetch-Site':'same-origin'}):
            with self.subTest(headers=headers):
                self.assertEqual(self.client.get('/api/project', headers=headers).status_code, 200)

    def test_external_origins_and_hosts_cannot_mutate_project(self):
        before = self.client.get('/api/project').content
        cases = ({'Origin':'https://external.invalid'}, {'Origin':'null'},
                 {'Origin':'http://127.0.0.1:8788'}, {'Host':'external.invalid:8787'},
                 {'Host':'127.0.0.1:8788'}, {'Sec-Fetch-Site':'cross-site'})
        for headers in cases:
            with self.subTest(headers=headers):
                response = self.client.post('/api/annotate', content=json.dumps({'label':'must not be saved'}),
                                            headers={'Content-Type':'text/plain', **headers})
                self.assertEqual(response.status_code, 403)
                self.assertEqual(self.client.get('/api/project').content, before)

    def test_ambiguous_authorities_are_rejected(self):
        for host in ('user@127.0.0.1:8787', '127.0.0.1:8787,external.invalid', '127.0.0.1:8787/path', '[::1', '127.0.0.1:0'):
            with self.subTest(host=host):
                self.assertEqual(self.client.get('/api/project', headers={'Host':host}).status_code, 400)
        response = self.client.get('/api/project', headers=[('Host','127.0.0.1:8787'), ('Host','external.invalid')])
        self.assertEqual(response.status_code, 400)

    def test_token_protects_every_private_static_mount_and_api(self):
        server.TOKEN['value'] = 'fixture-token'
        for name in ('renders', 'proxies', 'thumbs', 'fonts'):
            fixture = Path(PRIVATE.name) / name / 'private.txt'
            fixture.write_bytes(b'private fixture')
            route = f'/{name}/private.txt'
            with self.subTest(route=route):
                self.assertEqual(self.client.get(route).status_code, 401)
                response = self.client.get(route, headers={'Authorization':'Bearer fixture-token'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.content, b'private fixture')
                self.assertEqual(response.headers['cache-control'], 'private, no-store')
        self.assertEqual(self.client.get('/api/project').status_code, 401)
        self.assertEqual(self.client.get('/').status_code, 401)
        self.assertEqual(self.client.get('/api/version').status_code, 200)

    def test_browser_token_entry_sets_cookie_for_subsequent_resources(self):
        server.TOKEN['value'] = 'fixture-token'
        response = self.client.get('/?token=fixture-token')
        self.assertEqual(response.status_code, 200)
        self.assertIn('HttpOnly', response.headers['set-cookie'])
        self.assertIn('SameSite=strict', response.headers['set-cookie'])
        self.assertEqual(response.headers['referrer-policy'], 'no-referrer')
        self.assertEqual(self.client.get('/api/project').status_code, 200)
        self.assertEqual(self.client.get('/static/app.js').status_code, 200)

    def test_valid_token_never_bypasses_origin_policy(self):
        server.TOKEN['value'] = 'fixture-token'
        self.assertEqual(self.client.post('/api/annotate?token=fixture-token', json={'label':'not saved'},
                         headers={'Origin':'https://external.invalid'}).status_code, 403)
        self.assertNotIn('filmocity_token', self.client.cookies)

    def test_websocket_origin_and_token_denials_before_accept(self):
        cases = [({'Origin':'https://external.invalid'}, None, 4403),
                 ({'Origin':'null'}, None, 4403),
                 ({'Host':'external.invalid'}, None, 4403),
                 ({}, 'fixture-token', 4401)]
        for headers, token, code in cases:
            server.TOKEN['value'] = token
            with self.subTest(headers=headers, token=bool(token)):
                with self.assertRaises(WebSocketDisconnect) as caught:
                    with self.client.websocket_connect('ws://127.0.0.1:8787/ws', headers=headers):
                        self.fail('Unauthorized websocket accepted')
                self.assertEqual(caught.exception.code, code)

    def test_authorized_websocket_receives_editor_events(self):
        server.TOKEN['value'] = 'fixture-token'
        with self.client.websocket_connect('ws://127.0.0.1:8787/ws', headers={
                'Origin':'http://127.0.0.1:8787', 'Authorization':'Bearer fixture-token'}) as ws:
            response = self.client.post('/api/annotate', json={'label':'authorized test'},
                                        headers={'Authorization':'Bearer fixture-token'})
            self.assertEqual(response.status_code, 200)
            self.assertIsInstance(ws.receive_json(), dict)

    def test_cookie_media_range_and_same_origin_websocket_events(self):
        server.TOKEN['value'] = 'fixture-token'
        self.assertEqual(self.client.get('/?token=fixture-token',
            headers={'Origin': 'http://127.0.0.1:8787'}).status_code, 200)
        fixture = Path(PRIVATE.name) / 'renders' / 'range-fixture.bin'
        fixture.write_bytes(b'0123456789')
        response = self.client.get('/renders/range-fixture.bin', headers={
            'Origin': 'http://127.0.0.1:8787', 'Range': 'bytes=2-5'})
        self.assertEqual(response.status_code, 206)
        self.assertEqual(response.content, b'2345')
        self.assertEqual(response.headers['content-range'], 'bytes 2-5/10')
        self.assertEqual(response.headers['cache-control'], 'private, no-store')
        with self.client.websocket_connect('ws://127.0.0.1:8787/ws', headers={
                'Origin': 'http://127.0.0.1:8787'}) as ws:
            self.assertEqual(self.client.post('/api/annotate', json={'label': 'cookie event'},
                headers={'Origin': 'http://127.0.0.1:8787'}).status_code, 200)
            self.assertIsInstance(ws.receive_json(), dict)
        with self.assertRaises(WebSocketDisconnect) as denied:
            with self.client.websocket_connect('ws://127.0.0.1:8787/ws',
                    headers={'Origin': 'https://external.invalid'}):
                self.fail('Cookie must not bypass the WebSocket origin check')
        self.assertEqual(denied.exception.code, 4403)

    def test_duplicate_origin_is_rejected_before_http_dispatch(self):
        before = self.client.get('/api/project').content
        response = self.client.post('/api/annotate', json={'label': 'duplicate origin'}, headers=[
            ('Origin', 'http://127.0.0.1:8787'), ('Origin', 'https://external.invalid')])
        self.assertEqual(response.status_code, 400)
        self.assertEqual(self.client.get('/api/project').content, before)

    def test_public_identity_probe_still_checks_host_origin_and_method(self):
        server.TOKEN['value'] = 'fixture-token'
        response = self.client.get('/api/version')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['app'], 'Filmocity')
        self.assertNotIn('set-cookie', response.headers)
        for headers in ({'Host': 'external.invalid:8787'}, {'Origin': 'https://external.invalid'},
                        {'Sec-Fetch-Site': 'cross-site'}):
            self.assertEqual(self.client.get('/api/version', headers=headers).status_code, 403)
        self.assertEqual(self.client.post('/api/version').status_code, 401)

    def test_https_cookie_is_secure_and_origin_scheme_remains_exact(self):
        server.TOKEN['value'] = 'fixture-token'
        client = TestClient(server.app, base_url='https://127.0.0.1:8787')
        try:
            response = client.get('/?token=fixture-token', headers={'Origin': 'https://127.0.0.1:8787'})
            self.assertEqual(response.status_code, 200)
            self.assertIn('Secure', response.headers['set-cookie'])
            self.assertEqual(client.get('/api/project', headers={'Origin': 'https://127.0.0.1:8787'}).status_code, 200)
            self.assertEqual(client.get('/api/project', headers={'Origin': 'http://127.0.0.1:8787'}).status_code, 403)
        finally:
            client.close()


if __name__ == '__main__': unittest.main()
