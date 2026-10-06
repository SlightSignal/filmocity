"""Actual placement/removal/drag requests through production disk and history paths."""
import copy,json,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import render
import test_project_sync as store

def run(name,body):return json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers'/name)],input=json.dumps(body),text=True,cwd=ROOT))
def source(**patch):return {'id':'base','media_id':'m','start':0,'in_':3,'out':9,'speed':1,'reverse':True,'audio':{'fade_in':4,'fade_out':3},'keyframes':{'transform.x':[{'t':0,'v':0,'e':'ease'},{'t':8,'v':20}],'audio.duck_db':[{'t':0,'v':0},{'t':3,'v':-12}]},'markers':[{'t':.5,'name':'left'},{'t':3,'name':'right'}],**patch}
def track(*clips,**patch):return {'id':'v1','kind':'video','index':0,'clips':list(clips),**patch}
MEDIA={'m':{'id':'m','path':'/fixtures/source.mov','duration':20,'has_video':True,'has_audio':True,'fps':30}}

class RangeTransactions(store.ProjectStoreFixture):
    def install(self,value):
        self.assertIsNotNone(value['body'],value['messages']);p=self.env['load_project']();p.update(media=value['before']['media'],sequences=value['before']['sequences']);self.env['save_project'](p);return self.env['load_project']()
    def check_saved(self,value):
        before=self.install(value);n=len(self.env['read_undo_history']('a')['undo']);r=self.invoke('patch_project',{**value['body'],'_context':self.current()});self.assertTrue(r.get('ok'),r)
        after=self.env['load_project']();self.assertEqual(after['sequences'],value['optimistic']['sequences']);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),n+1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences']);return after
    def test_source_insert_and_overwrite_preserve_advanced_survivors_and_sequence_annotations(self):
        for mode in ['insert','overwrite']:
            for extra in [{},{'reverse':False,'time_remap':[{'t':0,'v':.5},{'t':2,'v':2},{'t':4,'v':1}]},{'hold':True}]:
                with self.subTest(mode=mode,extra=extra):
                    value=run('placement-fixture.cjs',{'tracks':[track(source(**extra))],'media':MEDIA,'at':1,'in_':0,'out':1,'mode':mode,'markers':[{'time':2}],'captions':[{'id':'cap','start':.5,'end':4,'text':'text'}]});self.check_saved(value)
    def test_clipboard_insert_and_overwrite_commit_all_fragments_once(self):
        for command in ['pasteClips','pasteInsert']:
            with self.subTest(command=command):
                clips=[source(),source(id='copied',start=10,in_=0,out=1,reverse=False,keyframes={},audio={})]
                value=run('placement-fixture.cjs',{'tracks':[track(*clips)],'media':MEDIA,'at':2,'copy':['copied'],'command':command});self.check_saved(value)
    def test_lift_extract_survivors_match_saved_model_including_joined_right_piece(self):
        for extract in [False,True]:
            with self.subTest(extract=extract):
                seq={'id':'s1','width':64,'height':48,'fps':30,'tracks':[track(source())],'in_point':1,'out_point':3,'markers':[{'time':4}],'captions':[{'id':'cap','start':.5,'end':4,'text':'text'}]}
                value=run('removal-fixture.cjs',{'sequence':seq,'media':MEDIA,'command':'liftExtract','args':[extract]});after=self.check_saved(value)
                right=after['sequences'][0]['tracks'][0]['clips'][1];self.assertEqual(right['start'],1 if extract else 3);self.assertEqual(right['in_'],3);self.assertEqual(right['out'],6)
    def test_selected_union_ripple_and_gap_closure_persist_markers_and_exact_group_history(self):
        for gap in [False,True]:
            clips=[source(id='a',out=4,reverse=False),source(id='b',start=2,in_=0,out=1,reverse=False),source(id='tail',start=5,in_=0,out=1,reverse=False)]
            if gap:clips=clips[:1]+clips[2:]
            seq={'id':'s1','width':64,'height':48,'fps':30,'tracks':[track(*clips)],'markers':[{'time':5}]}
            body={'sequence':seq,'media':MEDIA,'command':'deleteSel','args':[True],'selection':[] if gap else ['a','b']}
            if gap:body['gap']={'track':'v1','before':'a','after':'tail'}
            value=run('removal-fixture.cjs',body);after=self.check_saved(value);tail=next(c for c in after['sequences'][0]['tracks'][0]['clips'] if c['id']=='tail')
            self.assertEqual(tail['start'],1 if gap else 3);self.assertEqual(after['sequences'][0]['markers'][0]['time'],tail['start'])
    def test_ctrl_drag_crossing_source_and_annotations_save_as_one_undo(self):self.check_saved(run('range-gesture-plan.cjs',{}))
    def test_stale_range_batch_cannot_publish_any_clips_or_annotations(self):
        value=run('range-gesture-plan.cjs',{});self.install(value);context=self.current();self.edit('Another writer',context);raw=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with self.assertRaises(store.HTTPError):self.invoke('patch_project',{**value['body'],'_context':context})
        self.assertEqual(self.raw(),raw);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_failed_range_commit_keeps_both_project_and_history_unchanged(self):
        from unittest.mock import patch
        value=run('range-gesture-plan.cjs',{});self.install(value);raw=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with patch.dict(self.env,commit_pair=lambda *a,**k:(_ for _ in ()).throw(OSError('isolated write failure'))),self.assertRaises(OSError):
            self.invoke('patch_project',{**value['body'],'_context':self.current()})
        self.assertEqual(self.raw(),raw);self.assertEqual(self.env['read_undo_history']('a'),history)

if __name__=='__main__':unittest.main()
