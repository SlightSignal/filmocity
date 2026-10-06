"""Real command requests through the project store; decoded sound survives unlink."""
import array
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
import wave
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import render
from render_context import RenderContext
import test_project_sync as store

def invoke(body):
    return json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/link-match-fixture.cjs')],input=json.dumps(body),text=True,cwd=ROOT))

def request(**options):
    clip={'id':'v','media_id':'m','start':0,'in_':2,'out':8,'reverse':True,'speed':1,'time_remap':[{'t':0,'v':1.5}],
          'audio':{'linked':True,'gain_db':-3,'fade_in':1,'fade_out':2},
          'keyframes':{'audio.gain_db':[{'t':0,'v':-6},{'t':4,'v':0}]},'markers':[{'t':1,'name':'source note'}]}
    seq={'id':'s1','width':64,'height':48,'fps':30,'tracks':[{'id':'v1','kind':'video','index':0,'clips':[clip]},{'id':'a1','kind':'audio','index':0,'clips':[]}]}
    return invoke({'sequence':seq,'selection':['v'],**options})

class SavedLinkCommands(store.ProjectStoreFixture):
    def install(self,value):
        self.assertIsNotNone(value['body'],value['messages']);project=self.env['load_project']();project.update(media=value['before']['media'],sequences=value['before']['sequences']);self.env['save_project'](project);return self.env['load_project']()
    def apply(self,value):
        before=self.install(value);n=len(self.env['read_undo_history']('a')['undo']);reply=self.invoke('patch_project',{**value['body'],'_context':self.current()});self.assertTrue(reply.get('ok'),reply)
        after=self.env['load_project']();self.assertEqual(after['sequences'],value['optimistic']['sequences']);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),n+1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences']);return before,after
    def test_unlink_full_source_audio_payload_and_pair_receipt_save_in_one_history_entry(self):
        _,after=self.apply(request());seq=after['sequences'][0];parent,child=seq['tracks'][0]['clips'][0],seq['tracks'][1]['clips'][0]
        self.assertEqual(parent['audio_detached_id'],child['id']);self.assertEqual(child['unlinked_from'],parent['id']);self.assertEqual(render.clip_dur(child),4)
        for key in ['reverse','speed','time_remap','keyframes','markers']:self.assertEqual(parent[key],child[key])
    def test_relink_child_moved_to_equivalent_bus_removes_exact_partner_from_its_saved_track(self):
        unlinked=request()['optimistic']['sequences'][0];child=unlinked['tracks'][1]['clips'].pop();unlinked['tracks'].append({'id':'a2','kind':'audio','index':1,'clips':[child]})
        value=invoke({'sequence':unlinked,'selection':['v']});_,after=self.apply(value);seq=after['sequences'][0]
        self.assertTrue(seq['tracks'][0]['clips'][0]['audio']['linked']);self.assertFalse(any(t['clips'] for t in seq['tracks'][1:]));self.assertFalse(seq['tracks'][0]['clips'][0].get('audio_detached_id'))
    def test_grouping_actual_request_persists_without_changing_clip_source_fields(self):
        self.apply(invoke({'selection':['a','b'],'command':'groupSel','args':[True]}))
    def test_stale_context_or_commit_failure_cannot_publish_half_a_pair(self):
        value=request();self.install(value);context=self.current();self.edit('newer');raw=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with self.assertRaises(store.HTTPError):self.invoke('patch_project',{**value['body'],'_context':context})
        self.assertEqual(self.raw(),raw);self.assertEqual(self.env['read_undo_history']('a'),history)
        with patch.dict(self.env,commit_pair=lambda *a,**kw:(_ for _ in ()).throw(OSError('injected commit failure'))),self.assertRaises(OSError):
            self.invoke('patch_project',{**value['body'],'_context':self.current()})
        self.assertEqual(self.raw(),raw);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_actual_saved_unlink_preserves_complete_rendered_pcm_with_reverse_ramp_and_fades(self):
        before,after=self.apply(request());source=self.root/'source.wav'
        with wave.open(str(source),'wb') as stream:
            stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(array.array('h',[3276,6553]*48000*10).tobytes())
        original=hashlib.sha256(source.read_bytes()).hexdigest();outputs=[]
        for name,project in [('linked',before),('detached',after)]:
            project=copy.deepcopy(project);project['media']['m'].update(path=str(source),duration=10,has_video=False,has_audio=True,sample_rate=48000,channels=2)
            path=self.root/(name+'.wav')
            with RenderContext(scratch_parent=str(self.root)) as context:
                render.render(project,'s1',str(path),{'format':'audio','acodec':'wav'},context=context)
            with wave.open(str(path),'rb') as stream:
                self.assertEqual((stream.getnchannels(),stream.getframerate()),(2,48000));outputs.append(array.array('h',stream.readframes(stream.getnframes())))
        self.assertEqual(len(outputs[0]),len(outputs[1]));self.assertLessEqual(max(abs(a-b) for a,b in zip(*outputs)),1)
        self.assertEqual(hashlib.sha256(source.read_bytes()).hexdigest(),original)

if __name__=='__main__':unittest.main()
