"""Production JS cuts, real project transactions, and decoded FFmpeg output.

DOM/request adapters are controlled. Native Windows UI and camera media are not
covered. Variable-rate audio retains the renderer's average-speed approximation.
"""
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
import test_audio_contract as audio_fixture
import test_project_sync as store
import test_source_sync as picture_fixture
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import render
from audio_contract import fade_spec
from source_sync_fixture import pictures,pcm


def split(clip,at):
    data=[dict(clip=clip,at=at)]
    return json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/cut-fixture.cjs')],input=json.dumps(data),text=True,cwd=ROOT))[0]


def cut_ops(clip,at,sequence='seq1',track='V1'):
    return [dict(op='set_clip',sequence=sequence,track=track,clip=c) for c in split(clip,at)]


class Cuts(store.ProjectStoreFixture):
    def seed(self,**patch):
        p=self.env['load_project']();p['media']['A']['duration']=10;sq=p['sequences'][0];sq['tracks'][1]['clips']=[]
        self.clip={'id':'c','media_id':'A','start':0,'in_':3,'out':7,'speed':1,**patch};sq['tracks'][0]['clips']=[self.clip]
        self.env['save_project'](p);return p
    def cut(self,at):return self.invoke('patch_project',{'ops':cut_ops(self.clip,at),'_context':self.current(),'tool':'razor','actor':'human'})
    def test_split_saves_as_one_undo_step_and_restores_all_fields_exactly(self):
        before=self.seed(keyframes={'transform.x':[{'t':0,'v':0,'e':'bezier','o':[.2,3]},{'t':4,'v':1,'i':[.5,2]}]},audio={'fade_in':3})
        result=self.cut(1.5);after=self.env['load_project']();self.assertTrue(result['ok']);self.assertEqual(result['warnings'],[])
        self.assertEqual(len(after['sequences'][0]['tracks'][0]['clips']),2);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])
    def test_normalization_keeps_adjacent_retimed_cuts_including_hold_and_reverse(self):
        for change in ({'reverse':True},{'speed':2},{'speed':2,'time_remap':[]},{'hold':True,'speed':.1},{'time_remap':[{'t':0,'v':.5},{'t':2,'v':2}]},{'reverse':True,'time_remap':[{'t':0,'v':.5},{'t':2,'v':2}]}):
            with self.subTest(change=change):
                self.seed(**change);before=render.clip_dur(self.clip);at=before*.4;parts=split(self.clip,at);r=self.cut(at)
                self.assertEqual(r['warnings'],[]);actual=self.env['load_project']()['sequences'][0]['tracks'][0]['clips'];self.assertEqual(actual,parts)
                self.assertAlmostEqual(render.clip_dur(actual[0]),at);self.assertAlmostEqual(render.clip_dur(actual[1]),before-at)
    def test_stale_context_refuses_both_parts_without_history(self):
        self.seed();context=self.current();self.edit('changed',context);before=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with self.assertRaises(store.HTTPError):self.invoke('patch_project',{'ops':cut_ops(self.clip,1),'_context':context})
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_save_failure_does_not_publish_half_a_cut(self):
        self.seed();before=self.raw()
        with patch.dict(self.env,save_project=lambda *a,**k:(_ for _ in ()).throw(OSError('disk full'))):
            with self.assertRaises(OSError):self.cut(1)
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a')['undo'],[])
    def test_bezier_and_eased_export_curve_values_remain_continuous(self):
        for ease in ['linear','hold','ease','ease_in','ease_out','bezier']:
            curve=[{'t':0,'v':-12,'e':ease,'o':[.25,4]},{'t':4,'v':6,'i':[.4,-3]}]
            self.seed(keyframes={'audio.gain_db':curve});parts=split(self.clip,1.35)
            for i in range(80):
                t=i*.05;part=parts[0] if t<1.35 else parts[1];local=t if t<1.35 else t-1.35
                self.assertAlmostEqual(render.kf_eval(part['keyframes']['audio.gain_db'],local),render.kf_eval(curve,t),delta=1e-8)
    def test_fractional_hold_length_is_not_expanded_to_one_thirtieth(self):
        self.seed(hold=True);a,b=split(self.clip,1001/60000);self.assertAlmostEqual(render.clip_dur(a),1001/60000);self.assertAlmostEqual(render.clip_dur(b),4-1001/60000)


class AudioCuts(unittest.TestCase):
    setUp=audio_fixture.Audio.setUp
    render=audio_fixture.Audio.render
    point=audio_fixture.Audio.point
    close=audio_fixture.Audio.close
    def compare_cut(self,at,tolerance=2):
        original=self.render();self.video['clips']=split(self.clip,at);result=self.render();self.assertEqual(len(result),len(original))
        error=max(abs(a-b) for a,b in zip(original,result));self.assertLessEqual(error,tolerance);return error
    def test_cuts_do_not_repeat_in_or_out_fades(self):
        self.clip['audio']={'fade_in':.8,'fade_out':.8,'constant_power':False};self.compare_cut(.4)
    def test_cut_through_constant_power_and_exponential_fades(self):
        self.clip.update(audio_transition_in={'type':'constant_power','duration':.8},audio_transition_out={'type':'exponential','duration':.9});self.compare_cut(.3)
    def test_fractional_frame_fade_cut_retains_sample_clock(self):
        self.seq['fps']=30000/1001;self.clip['audio']={'fade_in':2,'fade_out':2};self.compare_cut(12*1001/30000)
    def test_repeated_cuts_do_not_change_fade_duration(self):
        self.clip['audio']={'fade_in':.7,'fade_out':.7};original=self.render();a,b=split(self.clip,.25);b,c=split(b,.375);self.video['clips']=[a,b,c];result=self.render()
        self.assertEqual(len(result),len(original));self.assertLessEqual(max(abs(a-b) for a,b in zip(original,result)),2)
    def test_reverse_cut_preserves_ordered_pcm_samples(self):
        data=array.array('h',[int(2000+i%10000) for i in range(96000)])
        with wave.open(str(self.source),'wb') as stream:stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(data.tobytes())
        self.before=hashlib.sha256(self.source.read_bytes()).hexdigest();self.clip['reverse']=True;self.compare_cut(.5,0)
    def test_ducking_plateaus_survive_the_cut(self):
        self.clip['keyframes']={'audio.duck_db':[{'t':0,'v':0},{'t':.1,'v':-18},{'t':.7,'v':-18},{'t':.8,'v':0}]}
        expected=self.render();self.video['clips']=split(self.clip,.4);actual=self.render()
        for t in [.2,.399,.4,.5,.65,.9]:self.close(self.point(actual,t),self.point(expected,t))
    def test_python_and_javascript_fade_windows_match_and_edits_reset_them(self):
        self.clip['audio']={'fade_in':.8,'fade_out':.8};a,b=split(self.clip,.4)
        script="const a=require('./frontend/audio-preview.js');console.log(JSON.stringify(a.fadeSpec(JSON.parse(process.argv[1]),.6)));"
        result=json.loads(subprocess.check_output(['node','-e',script,json.dumps(b)],cwd=ROOT,text=True));self.assertEqual(result,fade_spec(b,.6));self.assertAlmostEqual(result[0]['start'],-.4)
        b['audio']['fade_in']=.2;self.assertEqual(fade_spec(b,.6)[0]['start'],0)


class PictureCuts(unittest.TestCase):
    setUp=picture_fixture.SourceSync.setUp
    project=picture_fixture.SourceSync.project
    render=picture_fixture.SourceSync.render
    def test_forward_cut_preserves_decoded_picture_cues(self):
        self.project();expected=pictures(self.render());self.seq['tracks'][0]['clips']=split(self.clip,.5);self.assertEqual(pictures(self.render()),expected)
    def test_reverse_cut_preserves_decoded_picture_cues(self):
        self.project(duration=1.5);self.clip['reverse']=True;expected=pictures(self.render());self.seq['tracks'][0]['clips']=split(self.clip,.5);self.assertEqual(pictures(self.render()),expected)
    def test_hold_cut_keeps_the_original_held_frame(self):
        self.project();self.clip.update(hold=True,in_=.1,out=1.6);expected=pictures(self.render());self.seq['tracks'][0]['clips']=split(self.clip,.5);self.assertEqual(pictures(self.render()),expected)


if __name__=='__main__':unittest.main()
