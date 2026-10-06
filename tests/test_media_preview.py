"""Playback availability, file ownership and byte ranges with real files.

Production route/store functions use controlled response adapters; no live HTTP
or WebView2 is available on this host.
"""
import ast
import asyncio
import copy
import json
import mimetypes
import os
from pathlib import Path
import sys
import types
import unittest
from unittest.mock import patch

import test_project_sync as store
from test_project_sync import ROOT
sys.path.insert(0, str(ROOT/'backend'))
import media_preview as preview
from task_inputs import source_stamp


class Response:
    def __init__(self, body=None, *, status_code=200, headers=None, media_type=None, background=None):
        self.body,self.status_code,self.headers,self.background=body,status_code,headers or {},background
    def read(self):
        try:return b''.join(self.body)
        finally:
            if hasattr(self,"close"):self.close()


class Media(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        names={'media_file'};tree=ast.parse((ROOT/'backend/server.py').read_text())
        nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
        for node in nodes:node.decorator_list=[]
        self.env.update(StreamingResponse=Response,Response=Response,mimetypes=mimetypes)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.source=self.root/'original É.mov';self.source.write_bytes(b'0123456789')
        (self.root/'proxies').mkdir();self.proxy=self.root/'proxies'/'prepared.mp4';self.proxy.write_bytes(b'abcdefghij')
        self.media={'id':'m','path':str(self.source),'ingest_token':'v1','proxy':'/proxies/prepared.mp4','has_video':True,
                    'proxy_info':{'validation':'all_picture_timestamps_and_primary_audio_metadata','source_signature':preview.digest(source_stamp({'path':str(self.source)})),'file_stamp':preview.file_stamp(self.proxy)}}
        p=self.env['load_project']();p['media']={'m':self.media};self.env['save_project'](p)

    def query(self,proxy=True):
        state=self.env['get_project_state']();a=state['media_availability']['m'];c=state['context']
        return {'workspace':c['workspace'],'project':c['project'],'generation':a['generation'],
                'lease':a['proxy_lease' if proxy else 'original_lease'],'proxy':'1' if proxy else '0'}
    def request(self,query=None,headers=None,mid='m'):
        return self.env['media_file'](mid,types.SimpleNamespace(query_params=query or {},headers=headers or {}))
    def status(self):return self.env['get_project_state']()['media_availability']['m']

    def test_status_is_read_only_and_checked_proxy_has_distinct_original_and_proxy_leases(self):
        before=self.raw();s=self.status();self.assertTrue(s['original_online']);self.assertEqual(s['proxy_state'],'ready')
        self.assertNotEqual(s['original_lease'],s['proxy_lease']);self.assertEqual(self.raw(),before)
        self.assertEqual(self.request(self.query()).read(),b'abcdefghij')
        self.assertEqual(self.request(self.query(False)).read(),b'0123456789')

    def test_missing_proxy_is_visible_and_bound_request_never_silently_substitutes_original(self):
        query=self.query();self.proxy.unlink();self.assertEqual(self.status()['proxy_state'],'missing')
        with self.assertRaises(store.HTTPError) as error:self.request(query)
        self.assertEqual(error.exception.status_code,409)
        response=self.request({'proxy':'1'});self.assertEqual(response.headers['X-Filmocity-Fallback'],'original');self.assertEqual(response.read(),b'0123456789')

    def test_changed_proxy_or_source_cannot_reuse_an_old_lease(self):
        for kind in ('source','proxy'):
            with self.subTest(kind=kind):
                self.set_bytes();query=self.query();path=self.source if kind=='source' else self.proxy
                path.write_bytes(b'replaced content')
                self.assertEqual(self.status()['proxy_state'],'stale_source' if kind=='source' else 'changed')
                with self.assertRaises(store.HTTPError):self.request(query)
    def set_bytes(self):
        self.source.write_bytes(b'0123456789');self.proxy.write_bytes(b'abcdefghij')
        p=self.env['load_project']();p['media']['m']['proxy_info'].update(source_signature=preview.digest(source_stamp({'path':str(self.source)})),file_stamp=preview.file_stamp(self.proxy));self.env['save_project'](p)

    def test_old_project_or_partial_identity_cannot_load_same_id_in_another_project(self):
        query=self.query();p=self.env['load_project']();self.env['save_project'](p,str(self.root/'projects/b/project.json'))
        self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.request(query)
        for partial in ({'project':'b'},{'lease':query['lease']},{**query,'workspace':'other'}):
            with self.assertRaises(store.HTTPError):self.request(partial)

    def test_unrelated_edits_do_not_invalidate_a_media_lease_but_relink_does(self):
        query=self.query();p=self.env['load_project']();p['name']='Changed title';self.env['save_project'](p)
        self.assertEqual(self.request(query).read(),b'abcdefghij')
        p['media']['m']['ingest_token']='v2';self.env['save_project'](p)
        with self.assertRaises(store.HTTPError):self.request(query)

    def test_proxy_can_preview_an_offline_original_without_claiming_export_readiness(self):
        self.source.unlink();s=self.status();self.assertFalse(s['original_online']);self.assertTrue(s['proxy_available'])
        self.assertEqual(self.request(self.query()).read(),b'abcdefghij')
        with self.assertRaises(store.HTTPError):self.request(self.query(False))

    def test_legacy_proxy_is_explicitly_unverified_and_not_deleted(self):
        p=self.env['load_project']();p['media']['m'].pop('proxy_info');self.env['save_project'](p)
        self.assertEqual(self.status()['proxy_state'],'unverified');self.assertEqual(self.request(self.query()).read(),b'abcdefghij')

    def test_subclip_resolves_current_parent_and_cycles_are_explicit(self):
        p=self.env['load_project']();p['media']['sub']={**p['media']['m'],'id':'sub','subclip_of':'m','path':'stale old path'};self.env['save_project'](p)
        states=self.env['get_project_state']()['media_availability'];self.assertEqual(states['sub'],states['m'])
        self.assertEqual(self.request(self.query(),mid='sub').read(),b'abcdefghij')
        p['media']['m']['subclip_of']='sub';self.env['save_project'](p)
        self.assertEqual(self.status()['proxy_state'],'invalid')
        with self.assertRaises(store.HTTPError) as error:self.request({},mid='sub')
        self.assertEqual(error.exception.status_code,422)

    def test_unsafe_proxy_reference_and_link_are_not_followed(self):
        for reference in ('/proxies/../original É.mov','/proxies/..\\original.mov','/outside','/proxies/C:drive'):
            p=self.env['load_project']();p['media']['m']['proxy']=reference;self.env['save_project'](p)
            self.assertEqual(self.status()['proxy_state'],'invalid')
        with patch.object(Path,'is_symlink',return_value=True):
            with self.assertRaises(preview.PreviewError):preview.proxy_path(self.root,'/proxies/prepared.mp4')

    def test_single_open_suffix_and_clamped_browser_ranges_return_exact_bytes(self):
        for header,expected,content_range in [('bytes=2-4',b'cde','bytes 2-4/10'),('bytes=7-',b'hij','bytes 7-9/10'),('bytes=-3',b'hij','bytes 7-9/10'),('bytes=0-99',b'abcdefghij','bytes 0-9/10')]:
            with self.subTest(header=header):
                r=self.request(self.query(),{'range':header});self.assertEqual(r.status_code,206);self.assertEqual(r.read(),expected)
                self.assertEqual(r.headers['Content-Range'],content_range);self.assertEqual(r.headers['Content-Length'],str(len(expected)))

    def test_invalid_and_unsatisfiable_ranges_return_416_not_500(self):
        for header in ('bytes=20-30','bytes=4-2','bytes=-0','bytes=-','bytes=1-2,4-5','nonsense'):
            with self.subTest(header=header):
                r=self.request(self.query(),{'range':header});self.assertEqual(r.status_code,416);self.assertEqual(r.headers['Content-Range'],'bytes */10')

    def test_if_range_mismatch_returns_complete_current_representation(self):
        r=self.request(self.query(),{'range':'bytes=2-4','if-range':'"old"'});self.assertEqual(r.status_code,200);self.assertEqual(r.read(),b'abcdefghij')
        etag=r.headers['ETag'];r=self.request(self.query(),{'range':'bytes=2-4','if-range':etag});self.assertEqual(r.status_code,206);self.assertEqual(r.read(),b'cde')

    def test_open_response_keeps_captured_file_when_active_project_changes(self):
        response=self.request(self.query());self.env['set_active_project']('b');self.assertEqual(response.read(),b'abcdefghij')
        self.assertTrue(response.body.gi_frame is None)

    def test_changed_open_file_aborts_instead_of_streaming_replacement_bytes(self):
        response=self.request(self.query());self.proxy.write_bytes(b'changed bytes')
        with self.assertRaises(OSError):response.read()
        self.assertTrue(response.body.gi_frame is None)

    def test_disconnect_before_first_chunk_can_close_the_owned_handle(self):
        response=self.request(self.query());response.close()
        with self.assertRaises(ValueError):response.read()

    def test_send_failure_or_cancellation_before_first_chunk_closes_file(self):
        from media_response import owned_stream
        for error in (OSError('client disconnected'), asyncio.CancelledError()):
            stream=open(self.proxy,'rb')
            class Adapter:
                def __init__(self,*a,**kw):pass
                async def __call__(self,*a,**kw):raise error
            response=owned_stream(Adapter,stream,iter(()))
            with self.assertRaises(type(error)):asyncio.run(response({},None,None))
            self.assertTrue(stream.closed)

    def test_response_construction_failure_closes_captured_handle(self):
        from media_response import owned_stream
        stream=open(self.proxy,'rb')
        class Adapter:
            def __init__(self,*a,**kw):raise RuntimeError('failed headers')
        with self.assertRaises(RuntimeError):owned_stream(Adapter,stream,iter(()))
        self.assertTrue(stream.closed)


if __name__=='__main__':unittest.main()
