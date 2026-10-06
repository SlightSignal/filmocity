"""Parent review: inspection admission is bounded before actual command building."""
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import uuid

ROOT=Path(__file__).resolve().parents[1]
RUN=ROOT/'.command-scope-tests'/uuid.uuid4().hex[:8];RUN.mkdir(parents=True)
os.environ.update(TEMP=str(RUN),TMP=str(RUN),FILMOCITY_ROOT=str(RUN/'data'))
tempfile.tempdir=str(RUN)
sys.path.insert(0,str(ROOT/'backend'))
import server
import render_context
from fastapi import HTTPException

def project():
    return {'version':3,'media':{},'sequences':[{'id':'s','name':'Scopes','width':64,'height':48,'fps':24,'duration':.5,'tracks':[], 'captions':[{'start':0,'end':.5,'text':'Owned command'}]}]}

class CommandAdmission(unittest.TestCase):
    def setUp(self):
        self.contexts={}
        self.patches=[patch.object(server,'COMMAND_CONTEXTS',self.contexts),patch.object(server,'COMMAND_CONTEXT_BUILDING',0),patch.object(server,'load_project',side_effect=project)]
        for p in self.patches:p.start()
    def tearDown(self):
        for scope in list(self.contexts):server.release_render_command(scope)
        self.assertEqual(server.COMMAND_CONTEXT_BUILDING,0)
        for p in reversed(self.patches):p.stop()
    def full(self):
        with self.assertRaises(HTTPException) as raised:server.render_command('s')
        self.assertEqual(raised.exception.status_code,429)
    def test_completed_scopes_are_bounded_and_explicit_release_reopens_admission(self):
        with patch.object(server,'COMMAND_CONTEXT_LIMIT',2):
            first=server.render_command('s');server.render_command('s')
            self.assertEqual(len(self.contexts),2);self.full()
            roots=[Path(cmd.context.root) for cmd in self.contexts.values()]
            self.assertTrue(all(root.is_dir() for root in roots))
            server.release_render_command(first['scope']['id'])
            self.assertFalse(roots[0].exists())
            third=server.render_command('s');self.assertEqual(third['scope']['limit'],2)
            self.assertEqual(len(self.contexts),2);self.full()
    def test_building_scope_reserves_admission_before_its_real_builder_finishes(self):
        entered=threading.Event();release=threading.Event();results=[];errors=[];real=server.build_command
        def held(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(8));return real(*args,**kwargs)
        def build():
            try:results.append(server.render_command('s'))
            except BaseException as e:errors.append(e)
        with patch.object(server,'COMMAND_CONTEXT_LIMIT',1),patch.object(server,'build_command',side_effect=held) as builder:
            thread=threading.Thread(target=build);thread.start()
            try:
                self.assertTrue(entered.wait(8));self.assertEqual(server.COMMAND_CONTEXT_BUILDING,1)
                self.full();self.assertEqual(builder.call_count,1)
            finally:release.set();thread.join(8)
            self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);self.assertEqual(len(results),1)
            self.assertEqual(server.COMMAND_CONTEXT_BUILDING,0);self.full()
    def test_builder_failure_releases_reservation(self):
        with patch.object(server,'COMMAND_CONTEXT_LIMIT',1):
            with patch.object(server,'build_command',side_effect=RuntimeError('fixture build failure')):
                with self.assertRaisesRegex(RuntimeError,'fixture build failure'):server.render_command('s')
            self.assertEqual(server.COMMAND_CONTEXT_BUILDING,0);self.assertEqual(self.contexts,{})
            result=server.render_command('s');self.assertTrue(Path(self.contexts[result['scope']['id']].context.root).is_dir())
    def test_cleanup_failure_keeps_scope_and_bounds_diagnostics_until_explicit_retry(self):
        with patch.object(server,'COMMAND_CONTEXT_LIMIT',1):
            result=server.render_command('s');scope=result['scope']['id'];ctx=self.contexts[scope].context
            with patch.object(render_context.shutil,'rmtree',side_effect=PermissionError('sharing denial')):
                for _ in range(12):
                    with self.assertRaises(HTTPException) as raised:server.release_render_command(scope)
                    self.assertEqual(raised.exception.status_code,500)
                self.assertIn(scope,self.contexts);self.full();self.assertTrue(Path(ctx.root).is_dir())
                self.assertEqual(len(ctx.holder['scratch_diagnostics']),8)
            self.assertEqual(server.release_render_command(scope),{'ok':True})
            self.assertFalse(Path(ctx.root).exists());self.assertEqual(self.contexts,{})

if __name__=='__main__':unittest.main(verbosity=2)
