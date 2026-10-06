"""Pure range plans exercised through real FFmpeg PCM and saved-project history."""
import array,copy,hashlib,json,subprocess,sys,unittest,wave
from pathlib import Path
from unittest.mock import patch
import test_audio_contract as audio_fixture
import test_project_sync as store
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import render


def plan(sequence,method,**options):
    return json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/range-engine-fixture.cjs')],input=json.dumps(dict(sequence=sequence,method=method,trackIds=['V1'],**options)),text=True,cwd=ROOT))


class PCM(unittest.TestCase):
    render=audio_fixture.Audio.render;point=audio_fixture.Audio.point;close=audio_fixture.Audio.close
    def setUp(self):
        audio_fixture.Audio.setUp(self)
        with wave.open(str(self.source),'wb') as stream:
            stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(array.array('h',[3276,6553]*144000).tobytes())
        self.before=hashlib.sha256(self.source.read_bytes()).hexdigest();self.project['media']['m']['duration']=3;self.clip['out']=3
        self.clip['audio']={'fade_in':2,'fade_out':2,'constant_power':False}
    def use(self,value):
        self.project['sequences'][0]=value['sequence'];self.seq=self.project['sequences'][0];self.video=self.seq['tracks'][0];self.audio=self.seq['tracks'][1]
    def test_insertion_keeps_every_original_fade_sample_and_adds_only_the_gap(self):
        original=self.render();result=plan(self.seq,'insert',at=1,duration=.5);self.use(result);actual=self.render()
        expected=original[:96000]+array.array('h',[0]*48000)+original[96000:]
        self.assertEqual(len(actual),len(expected));self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),1)
    def test_disjoint_union_close_concatenates_each_surviving_sample_once(self):
        original=self.render();result=plan(self.seq,'remove',intervals=[[.5,.75],[.7,1],[1.5,2]],close=True);self.use(result);actual=self.render()
        expected=original[:48000]+original[96000:144000]+original[192000:]
        self.assertEqual(result['removedDuration'],1);self.assertEqual(len(actual),len(expected));self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),1)
    def test_lift_keeps_time_and_silences_only_removed_ranges(self):
        original=self.render();result=plan(self.seq,'remove',intervals=[[.5,1],[1.5,2]],close=False);self.use(result);actual=self.render();expected=array.array('h',original)
        expected[48000:96000]=array.array('h',[0]*48000);expected[144000:192000]=array.array('h',[0]*48000)
        self.assertEqual(len(actual),len(expected));self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,expected)),1)
    def test_reverse_insert_preserves_source_sample_order_around_the_gap(self):
        data=array.array('h',[2000+(i//2)%12000 for i in range(288000)])
        with wave.open(str(self.source),'wb') as stream:stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(data.tobytes())
        self.before=hashlib.sha256(self.source.read_bytes()).hexdigest();self.clip.update(reverse=True,audio={});original=self.render()
        result=plan(self.seq,'insert',at=1,duration=.5);self.use(result);actual=self.render();expected=original[:96000]+array.array('h',[0]*48000)+original[96000:]
        self.assertEqual(actual,expected)


class Persistence(store.ProjectStoreFixture):
    def seed(self):
        p=self.env['load_project']();sq=p['sequences'][0];sq['tracks'][0]['clips']=[{'id':'base','media_id':'A','start':0,'in_':0,'out':10,'time_remap':[{'t':0,'v':1},{'t':2,'v':3}],
          'audio':{'fade_in':4},'keyframes':{'transform.x':[{'t':0,'v':0,'e':'bezier','o':[.2,4]},{'t':5,'v':20,'i':[.4,-2]}]},'markers':[{'t':1,'name':'kept'},{'t':2,'name':'removed'}]}];sq['tracks'][1]['clips']=[];p['media']['A']['duration']=20;self.env['save_project'](p);return p
    def test_full_track_range_plan_saves_once_and_undo_redo_restores_exact_metadata(self):
        before=self.seed();value=plan(before['sequences'][0],'remove',intervals=[[1.5,2.5]],close=True);ops=[{'op':'set','path':'/sequences/0/tracks/0/clips','value':value['sequence']['tracks'][0]['clips']}]
        reply=self.invoke('patch_project',{'_context':self.current(),'ops':ops});self.assertTrue(reply['ok']);self.assertEqual(reply['warnings'],[]);after=self.env['load_project']()['sequences']
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after)
    def test_range_save_fault_never_publishes_partial_track_changes_or_history(self):
        p=self.seed();value=plan(p['sequences'][0],'insert',at=1,duration=1);before=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with patch.dict(self.env,save_project=lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full'))),self.assertRaises(OSError):
            self.invoke('patch_project',{'_context':self.current(),'ops':[{'op':'set','path':'/sequences/0/tracks/0/clips','value':value['sequence']['tracks'][0]['clips']}]})
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)


if __name__=='__main__':unittest.main(verbosity=2)
