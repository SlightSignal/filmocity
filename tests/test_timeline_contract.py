"""Agreement between client cuts and backend overlap slices on saved clip meaning."""
import copy,json,math,subprocess,sys,unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import render
import test_project_sync as store
from audio_contract import fade_spec
from overlap_normalization import slice_clip


def cuts(cases):
    return json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/cut-fixture.cjs')],input=json.dumps(cases),text=True,cwd=ROOT))


def clips():
    base={'id':'base','media_id':'m','start':2,'in_':3,'out':9,'speed':1,'vendor':{'keep':['extension']},'audio':{'gain_db':-5,'fade_in':4,'fade_out':5},
      'keyframes':{'transform.x':[{'t':0,'v':0,'e':'bezier','o':[.25,2],'vendor':True},{'t':7,'v':30,'i':[.45,-3]}],
                   'audio.duck_db':[{'t':0,'v':0,'e':'hold'},{'t':.5,'v':-9},{'t':1.25,'v':-18},{'t':10,'v':0}]},
      'markers':[{'t':0,'name':'start'},{'t':1.25,'name':'cut'},{'t':1.5,'name':'middle'}]}
    for patch in [{},{'speed':.5},{'speed':2},{'speed':2,'time_remap':[]},{'reverse':True},{'reverse':True,'speed':.5},{'hold':True,'speed':.25},
      {'time_remap':[{'t':0,'v':.5},{'t':2,'v':2},{'t':4,'v':.75}]},
      {'time_remap':[{'t':0,'v':1,'e':'hold'},{'t':1.25,'v':3,'e':'hold'}],'reverse':True}]:
        yield {**copy.deepcopy(base),**patch}


class TimelineContract(unittest.TestCase):
    def same_meaning(self,client,server):
        for key in ('start','in_','out'):self.assertAlmostEqual(client[key],server[key],delta=1e-9)
        duration=render.clip_dur(client);self.assertAlmostEqual(duration,render.clip_dur(server),delta=1e-9)
        for key in ('vendor','audio','time_remap','hold','reverse','markers'):self.assertEqual(client.get(key),server.get(key))
        for key in client['keyframes']:
            for fraction in [0,.01,.3,.5,.99]:self.assertAlmostEqual(render.kf_eval(client['keyframes'][key],duration*fraction),render.kf_eval(server['keyframes'][key],duration*fraction),delta=1e-8)
        self.assertEqual(fade_spec(client,duration),fade_spec(server,duration))
    def test_cut_and_overlap_slice_preserve_the_same_source_curves_markers_and_fades(self):
        originals=list(clips());before=copy.deepcopy(originals);pairs=cuts([{'clip':c,'at':1.25} for c in originals])
        for c,(left,right) in zip(originals,pairs):
            with self.subTest(clip=c):
                self.same_meaning(left,slice_clip(c,0,1.25));self.same_meaning(right,slice_clip(c,1.25,render.clip_dur(c)))
        self.assertEqual(originals,before)
    def test_repeated_slices_agree_with_repeated_client_cuts(self):
        originals=list(clips());pairs=cuts([{'clip':c,'at':1.25} for c in originals]);rights=[p[1] for p in pairs]
        pairs=cuts([{'clip':c,'at':.5} for c in rights])
        for c,(left,right) in zip(rights,pairs):
            with self.subTest(clip=c):
                self.same_meaning(left,slice_clip(c,0,.5));self.same_meaning(right,slice_clip(c,.5,render.clip_dur(c)))
    def test_independent_numeric_ramp_reference_retains_the_same_integrated_source(self):
        c=next(c for c in clips() if c.get('time_remap') and not c.get('reverse'));part=slice_clip(c,1.25,2)
        # Original first speed segment s(t)=.5+.75t; integral .5t+.375t^2.
        self.assertAlmostEqual(part['in_'],3+.5*1.25+.375*1.25**2)
        self.assertAlmostEqual(part['out'],3+.5*2+.375*2**2)
        self.assertAlmostEqual(render.clip_dur(part),.75)


class GestureTransactions(store.ProjectStoreFixture):
    def plan(self,**options):
        value=json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/timeline-move-plan.cjs')],input=json.dumps(options),text=True,cwd=ROOT))
        p=self.env['load_project']();p.update(media=value['before']['media'],sequences=value['before']['sequences']);self.env['save_project'](p)
        return value,p
    def test_dragged_clip_wins_overwrite_after_real_save_and_undo_restores_original_order(self):
        plan,before=self.plan();body={**plan['body'],'_context':self.current()};result=self.invoke('patch_project',body);self.assertTrue(result['ok'])
        after=self.env['load_project']();clips=after['sequences'][0]['tracks'][0]['clips'];moved=next(c for c in clips if c['id']=='a')
        self.assertEqual((moved['start'],moved['in_'],moved['out']),(4,5,8));self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.assertEqual([c['id'] for c in clips if c['start']<=4.5<c['start']+render.clip_dur(c)],['a'])
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])
    def test_group_drag_preserves_each_piece_through_overlap_resolution(self):
        plan,before=self.plan(group=True);result=self.invoke('patch_project',{**plan['body'],'_context':self.current()});self.assertTrue(result['ok'])
        clips=self.env['load_project']()['sequences'][0]['tracks'][0]['clips'];byid={c['id']:c for c in clips}
        self.assertEqual((byid['a']['start'],byid['b']['start']),(3,6));self.assertEqual(render.clip_dur(byid['a']),3);self.assertEqual(render.clip_dur(byid['b']),3)
        self.assertNotIn('c',byid);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
    def test_stale_drag_cannot_remove_or_reinsert_any_clip(self):
        plan,_=self.plan();context=self.current();self.edit('new owner',context);before=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with self.assertRaises(store.HTTPError):self.invoke('patch_project',{**plan['body'],'_context':context})
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)


if __name__=='__main__':unittest.main()
