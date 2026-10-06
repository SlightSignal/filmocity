"""Real filesystem/threads/encoder lifecycle, not native Windows sharing/UI.

New segment encodes explicitly substitute OpenH264 in a controlled command
adapter on hosts without x264. Production x264/HEVC and segment seams remain
separate native qualification; the production orchestration itself is exercised.
"""
import copy
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import segment_cache as cache
import render
from render_context import RenderContext
from test_work_budget import wait_for


class SegmentCache(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='filmocity-segment-cache-');self.root=Path(self.temp.name);self.directory=self.root/'renders'/'cache';self.directory.mkdir(parents=True)
        self.threads=[];self.events=[];self.holders=[]
    def tearDown(self):
        for holder in self.holders:holder['cancelled']=True
        for event in self.events:event.set()
        for thread in self.threads:thread.join(5);self.assertFalse(thread.is_alive())
        self.assertEqual(cache.state(self.directory),{'read_leases':0,'writers':0,'cleaning':False,'retention_bytes':cache.LIMIT_BYTES});self.temp.cleanup()
    def event(self):
        event=threading.Event();self.events.append(event);return event
    def start(self,function):
        result={};done=threading.Event()
        def run():
            try:result['value']=function()
            except BaseException as error:result['error']=error
            finally:done.set()
        thread=threading.Thread(target=run,daemon=True);self.threads.append(thread);thread.start();return result,done
    def finish(self,task,error=None):
        result,done=task;self.assertTrue(done.wait(10),result)
        if error:self.assertIsInstance(result.get('error'),error)
        elif result.get('error'):raise result['error']
        return result.get('value')
    def entry(self,key='a',data=b'cached',stamp=None):
        path=self.directory/(key*32+'.mp4');path.write_bytes(data)
        if stamp is not None:os.utime(path,ns=(stamp,stamp))
        return path

    def test_manual_cleanup_deletes_only_completed_segments(self):
        completed=self.entry();partial=self.directory/'.segment-live.part.mp4';partial.write_bytes(b'writing')
        unknown=self.directory/'a-movie.mp4';unknown.write_bytes(b'keep');folder=self.directory/'scratch';folder.mkdir();(folder/'inside.mp4').write_bytes(b'keep')
        result=cache.cleanup(self.directory)
        self.assertEqual(result['removed'],[completed.name]);self.assertEqual(result['bytes_removed'],6);self.assertEqual(result['status'],'partial')
        self.assertEqual(set(result['skipped']),{partial.name,unknown.name,folder.name});self.assertEqual(partial.read_bytes(),b'writing');self.assertEqual(unknown.read_bytes(),b'keep')

    def test_retention_removes_oldest_completed_bytes_and_preserves_partials(self):
        a=self.entry('a',b'a'*6,100);b=self.entry('b',b'b'*6,200);c=self.entry('c',b'c'*6,300)
        partial=self.directory/'.segment-writing.part.mp4';partial.write_bytes(b'p'*100);os.utime(partial,ns=(1,1))
        result=cache.cleanup(self.directory,limit=12)
        self.assertEqual(result['removed'],[a.name]);self.assertEqual(result['managed_bytes_after'],12);self.assertTrue(b.exists() and c.exists() and partial.exists())

    def test_active_readers_and_writers_defer_manual_and_automatic_cleanup(self):
        entry=self.entry()
        with cache.reading(self.directory):
            with cache.writing(self.directory,'b'*32):
                self.assertEqual(cache.cleanup(self.directory,limit=0)['status'],'deferred');self.assertTrue(entry.exists())
            self.assertEqual(cache.cleanup(self.directory)['status'],'deferred')
        self.assertEqual(cache.cleanup(self.directory)['removed'],[entry.name])

    def test_new_reader_waits_for_cleanup_and_can_cancel(self):
        cancel=self.event();attempt=self.event()
        def check():
            attempt.set()
            if cancel.is_set():raise RuntimeError('cancelled')
        def run():
            with cache.reading(self.directory,check):self.fail('Entered during cleanup')
        with cache.cleaning(self.directory):
            task=self.start(run);self.assertTrue(attempt.wait(2));cancel.set();self.finish(task,RuntimeError)

    def test_reader_admission_resumes_after_cleanup_and_excludes_a_second_cleaner(self):
        entered=self.event()
        def run():
            with cache.reading(self.directory):entered.set()
        with cache.cleaning(self.directory):
            task=self.start(run);self.assertFalse(entered.wait(.05));self.assertEqual(cache.cleanup(self.directory)['status'],'deferred')
        self.finish(task);self.assertTrue(entered.is_set())

    def test_same_key_serializes_and_waiter_can_cancel_without_releasing_owner(self):
        cancel=self.event();attempt=self.event()
        def check():
            attempt.set()
            if cancel.is_set():raise RuntimeError('cancelled')
        def run():
            with cache.writing(self.directory,'a'*32,check):self.fail('Duplicate writer')
        with cache.writing(self.directory,'a'*32):
            task=self.start(run);self.assertTrue(attempt.wait(2));cancel.set();self.finish(task,RuntimeError)
            self.assertEqual(cache.state(self.directory)['writers'],1)
            with cache.writing(self.directory,'b'*32):self.assertEqual(cache.state(self.directory)['writers'],2)

    def test_real_symlink_directory_file_and_parent_are_never_followed(self):
        outside=self.root/'outside';outside.mkdir();original=outside/('a'*32+'.mp4');original.write_bytes(b'original')
        try:(self.directory/('b'*32+'.mp4')).symlink_to(original)
        except OSError as error:self.skipTest('Symlink creation unavailable; native junction acceptance remains required: '+str(error))
        result=cache.cleanup(self.directory);self.assertEqual(result['removed'],[]);self.assertEqual(len(result['skipped']),1)
        alias=self.root/'alias';alias.symlink_to(outside,target_is_directory=True)
        self.assertEqual(cache.cleanup(alias)['status'],'partial')
        with self.assertRaises(ValueError),cache.reading(alias/'cache'):pass
        self.assertEqual(original.read_bytes(),b'original')

    def test_windows_reparse_attribute_is_detected_without_newer_pathlib_api(self):
        entry=self.entry();info=types.SimpleNamespace(st_file_attributes=0x400)
        self.assertTrue(cache.linked(entry,info))
        original=Path.lstat
        def lstat(path,*args,**kwargs):
            if path==self.directory:return types.SimpleNamespace(st_file_attributes=0x400,st_mode=0o40755)
            return original(path,*args,**kwargs)
        with patch.object(Path,'lstat',lstat):self.assertEqual(cache.cleanup(self.directory)['status'],'partial')
        self.assertTrue(entry.exists())

    def test_sharing_denial_is_reported_and_other_completed_entries_can_be_removed(self):
        a=self.entry('a',b'locked',100);b=self.entry('b',b'delete',200);original=Path.unlink
        def unlink(path,*args,**kwargs):
            if path==a:raise PermissionError('sharing denied')
            return original(path,*args,**kwargs)
        with patch.object(Path,'unlink',unlink):result=cache.cleanup(self.directory,limit=0)
        self.assertEqual(result['removed'],[b.name]);self.assertEqual(result['managed_bytes_after'],6);self.assertIn('sharing denied',result['errors'][0]['message']);self.assertTrue(a.exists())

    def test_mid_cleanup_directory_failure_preserves_partial_result(self):
        a=self.entry('a',b'a',100);b=self.entry('b',b'b',200);actual=cache.validate;calls=0
        def validate(path):
            nonlocal calls
            calls+=1
            if calls==4:raise ValueError('directory changed')
            return actual(path)
        with patch.object(cache,'validate',validate):result=cache.cleanup(self.directory)
        self.assertEqual(result['removed'],[a.name]);self.assertTrue(b.exists());self.assertEqual(result['bytes_removed'],1);self.assertEqual(result['status'],'partial')

    def test_failures_and_invalid_keys_leave_no_ownership_and_missing_cache_is_empty(self):
        with self.assertRaises(RuntimeError),cache.reading(self.directory):raise RuntimeError('failed')
        with self.assertRaises(RuntimeError),cache.writing(self.directory,'a'*32):raise RuntimeError('failed')
        for key in ('../file','A'*32,'b'*31):
            with self.assertRaises(ValueError),cache.writing(self.directory,key):pass
        for limit in (-1,True,1.5):
            with self.assertRaises(ValueError):cache.cleanup(self.directory,limit=limit)
        self.assertEqual(cache.cleanup(self.root/'missing')['status'],'complete')

    def project(self):return {'media':{},'sequences':[{'id':'s','name':'Cache check','fps':24,'width':64,'height':48,'duration':.5,'tracks':[],'captions':[]}]}
    def export(self,name,holder=None):
        holder=holder if holder is not None else {};self.holders.append(holder)
        with RenderContext(proc_holder=holder,scratch_parent=str(self.root)) as context:
            return render.render_incremental(self.project(),'s',str(self.root/(name+'.mp4')),{'full':True},cache_dir=str(self.directory),context=context)
    @staticmethod
    def encode_adapter(command,**kwargs):
        command=list(command)
        if '-c:v' in command and command[command.index('-c:v')+1]=='libx264':
            command[command.index('-c:v')+1]='libopenh264'
            for option in ('-preset','-crf'):
                if option in command:i=command.index(option);del command[i:i+2]
            command[-1:-1]=['-b:v','1M']
        return render._execute_ffmpeg(command,proc_holder=kwargs['context'].holder,progress=kwargs.get('progress'),total=kwargs.get('total',1))

    def test_real_incremental_export_reuse_and_clear_during_stitch(self):
        calls=[];attempts=[]
        def encode(command,**kwargs):
            calls.append(list(command))
            if '-f' in command and 'concat' in command:
                attempts.append(cache.cleanup(self.directory));self.assertEqual(attempts[-1]['status'],'deferred')
            return self.encode_adapter(command,**kwargs)
        with patch.object(render,'_run_ffmpeg',side_effect=encode):
            first=self.export('first');second=self.export('second')
        self.assertEqual(first[1]['reused'],0);self.assertEqual(second[1]['reused'],1)
        self.assertEqual(len([c for c in calls if '-c:v' in c and 'libx264' in c]),1)
        self.assertEqual(hashlib.sha256(Path(first[0]).read_bytes()).digest(),hashlib.sha256(Path(second[0]).read_bytes()).digest())
        self.assertEqual(len(attempts),2);self.assertEqual(len(cache.cleanup(self.directory)['removed']),1)
        self.assertFalse(list(self.directory.glob('.*.mp4')));self.assertFalse(list(self.root.glob('filmocity-render-*')))

    def test_two_real_exports_share_one_writer_without_replacing_an_in_use_segment(self):
        entered=self.event();release=self.event();encodes=[]
        def encode(command,**kwargs):
            if '-c:v' in command and 'libx264' in command:
                encodes.append(list(command));entered.set()
                if not release.wait(5):raise RuntimeError('fixture timeout')
            return self.encode_adapter(command,**kwargs)
        with patch.object(render,'_run_ffmpeg',side_effect=encode):
            a=self.start(lambda:self.export('one'));self.assertTrue(entered.wait(3));b=self.start(lambda:self.export('two'))
            wait_for(lambda:cache.state(self.directory)['read_leases']>=4)
            self.assertEqual(cache.state(self.directory)['writers'],1);self.assertEqual(cache.cleanup(self.directory,limit=0)['status'],'deferred')
            release.set();ra=self.finish(a);rb=self.finish(b)
        self.assertEqual(len(encodes),1);self.assertEqual(sorted([ra[1]['reused'],rb[1]['reused']]),[0,1])
        self.assertEqual(Path(ra[0]).read_bytes(),Path(rb[0]).read_bytes());self.assertEqual(len(list(self.directory.glob('*.mp4'))),1)

    def test_segment_failure_removes_only_its_stage_and_releases_writer_and_reader(self):
        def fail(command,**kwargs):Path(command[-1]).write_bytes(b'partial');raise RuntimeError('encoder failed')
        with patch.object(render,'_run_ffmpeg',side_effect=fail),self.assertRaisesRegex(RuntimeError,'encoder failed'):self.export('failed')
        self.assertFalse(list(self.directory.iterdir()));self.assertFalse((self.root/'failed.mp4').exists());self.assertFalse(list(self.root.glob('*.part.mp4')))


if __name__=='__main__':unittest.main()
