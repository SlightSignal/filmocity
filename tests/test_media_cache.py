"""Generated cache cleanup: preparation exclusion, intact projects and real faults."""
import ast
import asyncio
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import test_project_sync as store
from test_project_sync import ROOT
import media_cache


class Cache(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        tree=ast.parse((ROOT/'backend/server.py').read_text());names={'cache_clear','cache_info'}
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        for node in nodes:node.decorator_list=[]
        self.env.update(RENDER_STATE_LOCK=threading.Lock(),JOBS={})
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        for folder in ('proxies','thumbs','thumbs/tpl','renders/cache'):(self.root/folder).mkdir(parents=True,exist_ok=True)
        self.proxy=self.root/'proxies'/'ready.mp4';self.proxy.write_bytes(b'proxy')
        self.source=self.root/'original.mov';self.source.write_bytes(b'original')
        p=self.env['load_project']();p['media']['A'].update(path=str(self.source),proxy='/proxies/ready.mp4',proxy_info={'width':640});self.env['save_project'](p)
    def clear(self,what='proxies'):
        # Real executor/route work; a timer only supplies host-restricted socket
        # wakeups, including during Runner's default-executor shutdown.
        def loop_factory():
            loop=asyncio.new_event_loop()
            def tick():loop.call_later(.01,tick)
            loop.call_later(.01,tick);return loop
        with asyncio.Runner(loop_factory=loop_factory) as runner:
            return runner.run(self.env['cache_clear'](store.Request({'what':what})))

    def test_cleanup_keeps_project_records_and_originals_and_exposes_missing_proxy(self):
        before=self.raw();result=self.clear();self.assertEqual(result['cleared'],['proxies']);self.assertFalse(self.proxy.exists())
        self.assertEqual(self.raw(),before);self.assertEqual(self.source.read_bytes(),b'original')
        self.assertEqual(self.env['get_project_state']()['media_availability']['A']['proxy_state'],'missing')
        self.assertEqual(result['reports'][0]['bytes_removed'],5)

    def test_preparation_excludes_cleanup_without_deleting_any_files(self):
        with media_cache.preparing(self.root):
            with self.assertRaises(store.HTTPError) as error:self.clear('all')
            self.assertEqual(error.exception.status_code,409);self.assertTrue(self.proxy.exists())
        self.assertEqual(self.clear()['cleared'],['proxies'])

    def test_preparation_waits_for_cleaner_and_can_cancel_while_waiting(self):
        attempted=threading.Event();cancelled=threading.Event();finished=threading.Event();errors=[]
        def check():
            attempted.set()
            if cancelled.is_set():raise RuntimeError('cancelled')
        def prepare():
            try:
                with media_cache.preparing(self.root,check):self.fail('Admitted during cleanup')
            except Exception as e:errors.append(str(e))
            finally:finished.set()
        with media_cache.cleaning(self.root):
            worker=threading.Thread(target=prepare);worker.start();self.assertTrue(attempted.wait(2));cancelled.set();self.assertTrue(finished.wait(2));worker.join(2)
        self.assertEqual(errors,['cancelled'])
        with media_cache.preparing(self.root):pass

    def test_concurrent_preparation_is_allowed_but_last_reader_must_exit_before_cleanup(self):
        with media_cache.preparing(self.root):
            with media_cache.preparing(self.root):
                with self.assertRaises(media_cache.CacheBusy):media_cache.clear(self.root,'proxies')
            with self.assertRaises(media_cache.CacheBusy):media_cache.clear(self.root,'proxies')
        media_cache.clear(self.root,'proxies');self.assertFalse(self.proxy.exists())

    def test_denied_deletion_is_reported_and_never_called_cleared(self):
        original=Path.unlink
        def unlink(path,*args,**kwargs):
            if path==self.proxy:raise PermissionError('Windows sharing denied')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):result=self.clear()
        self.assertEqual(result['cleared'],[]);self.assertIn('sharing denied',result['reports'][0]['errors'][0]['message']);self.assertTrue(self.proxy.exists())

    def test_active_scratch_and_unknown_directories_are_preserved_and_reported(self):
        scratch=self.root/'thumbs'/'filmocity-render-owned';scratch.mkdir();(scratch/'partial.mp4').write_bytes(b'active')
        (self.root/'thumbs'/'thumb.jpg').write_bytes(b'thumb');(self.root/'thumbs/tpl'/'template.jpg').write_bytes(b'template')
        result=self.clear('thumbs');self.assertEqual(result['cleared'],[]);self.assertTrue((scratch/'partial.mp4').exists())
        report=result['reports'][0];self.assertEqual(len(report['removed']),2);self.assertEqual(len(report['skipped']),1)

    def test_linked_cache_directory_cannot_delete_sources(self):
        original=Path.is_symlink
        with patch.object(Path,'is_symlink',lambda p:p==self.root/'proxies' or original(p)):
            result=self.clear()
        self.assertEqual(result['cleared'],[]);self.assertTrue(self.proxy.exists());self.assertTrue(result['reports'][0]['errors'])

    def test_bad_category_and_busy_segments_are_explicit(self):
        with self.assertRaises(store.HTTPError) as error:self.clear('../originals')
        self.assertEqual(error.exception.status_code,422)
        import segment_cache
        cache=self.root/'renders/cache'/('a'*32+'.mp4');cache.write_bytes(b'encoded')
        with segment_cache.reading(cache.parent):
            result=self.clear('segments');self.assertEqual(result['cleared'],[]);self.assertTrue(cache.exists());self.assertEqual(result['reports'][0]['status'],'deferred')
        result=self.clear('segments');self.assertEqual(result['cleared'],['segments']);self.assertFalse(cache.exists())

    def test_clear_all_reports_other_cleanup_when_segment_cache_is_busy(self):
        import segment_cache
        path=self.root/'renders/cache'/('a'*32+'.mp4');path.write_bytes(b'encoded');before=self.raw()
        with segment_cache.reading(path.parent):
            result=self.clear('all')
            self.assertEqual(result['cleared'],['proxies','thumbs']);self.assertFalse(self.proxy.exists());self.assertTrue(path.exists())
            self.assertEqual(result['reports'][-1]['status'],'deferred');self.assertEqual(result['segment_activity']['read_leases'],1)
        self.assertEqual(self.raw(),before);self.assertEqual(self.source.read_bytes(),b'original')

    def test_unrelated_export_job_does_not_block_an_idle_cache(self):
        path=self.root/'renders/cache'/('a'*32+'.mp4');path.write_bytes(b'encoded');self.env['JOBS']={'job':{'status':'running'}}
        result=self.clear('segments');self.assertEqual(result['cleared'],['segments']);self.assertFalse(path.exists())

    def test_growing_cache_does_not_make_completed_export_size_negative(self):
        import os
        original=os.walk
        def walk(path,*args,**kwargs):
            yield from original(path,*args,**kwargs)
            if Path(path)==self.root/'renders':(self.root/'renders/cache'/('b'*32+'.mp4')).write_bytes(b'x'*1_000_000)
        with patch.object(os,'walk',walk):result=self.env['cache_info']()
        self.assertEqual(result['renders_mb'],0)


if __name__=='__main__':unittest.main()
