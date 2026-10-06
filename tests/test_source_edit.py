"""Shared JS source edits against actual FFmpeg PCM and backend slice meaning."""
import array,copy,hashlib,json,subprocess,sys,unittest,wave
from pathlib import Path
import test_audio_contract as audio_fixture
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import render
from audio_contract import fade_spec,fade_window
from overlap_normalization import slice_clip


def plan(clip,**step):
    return json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/source-range-fixture.cjs')],input=json.dumps({'clip':clip,**step}),text=True,cwd=ROOT))


def frontend_fades(clip,duration,times):
    script="const a=require('./frontend/audio-preview.js'),[c,d,t]=JSON.parse(process.argv[1]);console.log(JSON.stringify({spec:a.fadeSpec(c,d),gain:t.map(x=>a.fadeGain(c,d,x))}));"
    return json.loads(subprocess.check_output(['node','-e',script,json.dumps([clip,duration,times])],text=True,cwd=ROOT))


class PCM(unittest.TestCase):
    render=audio_fixture.Audio.render;point=audio_fixture.Audio.point;close=audio_fixture.Audio.close
    def setUp(self):
        audio_fixture.Audio.setUp(self)
        with wave.open(str(self.source),'wb') as stream:
            stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(array.array('h',[3276,6553]*192000).tobytes())
        self.before=hashlib.sha256(self.source.read_bytes()).hexdigest();self.project['media']['m']['duration']=4;self.clip.update(in_=1,out=3)
    def test_signed_fade_clock_extends_before_and_after_the_original_fades(self):
        for curve in ('constant_gain','constant_power','exponential'):
            with self.subTest(curve=curve):
                c=copy.deepcopy(self.clip);c.update(audio_transition_in={'type':curve,'duration':1.5},audio_transition_out={'type':curve,'duration':1.5})
                part=plan(c,begin=-.5,end=2.5,options={'start':0,'sourceLimit':4});self.video['clips']=[part];samples=self.render();times=[.1,.5,.75,1,1.25,1.75,2,2.25,2.499,2.7]
                live=frontend_fades(part,3,times);self.assertEqual(live['spec'],fade_spec(part,3));self.assertEqual(fade_window(part,3)['offset'],-.5)
                for t,gain in zip(times,live['gain']):self.close(self.point(samples,t),[v/32768*gain for v in (3276,6553)],tolerance=2/32768)
                self.assertEqual(len(samples),3*48000*2)
    def test_inherited_offset_past_original_duration_remains_silent_after_out_fade(self):
        c=copy.deepcopy(self.clip);c['out']=1.5;c['audio']={'fade_out':.3}
        part=plan(c,begin=1,end=2,options={'start':0,'sourceLimit':4});self.video['clips']=[part];samples=self.render()
        self.assertEqual(fade_window(part,1)['offset'],1);self.assertEqual(fade_window(part,1)['duration'],.5)
        self.assertEqual(max(abs(s) for s in samples),0)
    def test_trim_and_restore_retains_all_original_decoded_fade_samples(self):
        c=copy.deepcopy(self.clip);c['audio']={'fade_in':1.8,'fade_out':1.7};self.video['clips']=[c];original=self.render()
        restored=plan(c,steps=[{'begin':.5,'end':1.5,'options':{'start':0,'sourceLimit':4}},{'begin':-.5,'end':1.5,'options':{'start':0,'sourceLimit':4}}]);self.video['clips']=[restored]
        actual=self.render();self.assertEqual(len(actual),len(original));self.assertLessEqual(max(abs(a-b) for a,b in zip(actual,original)),1)
    def test_forward_and_reverse_slip_move_source_samples_without_moving_fade_clock(self):
        values=array.array('h',[int(2000+(i//2)%12000) for i in range(384000)])
        with wave.open(str(self.source),'wb') as stream:stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(values.tobytes())
        self.before=hashlib.sha256(self.source.read_bytes()).hexdigest()
        for reverse in (False,True):
            c=copy.deepcopy(self.clip);c.update(reverse=reverse,audio={'fade_in':.8,'fade_out':.8});part=plan(c,method='slip',delta=.125,options={'sourceLimit':4})
            direct=copy.deepcopy(c);direct['in_']+=(-.125 if reverse else .125);direct['out']+=(-.125 if reverse else .125)
            self.video['clips']=[direct];expected=self.render();self.video['clips']=[part];actual=self.render();self.assertEqual(actual,expected)


class Agreement(unittest.TestCase):
    def test_client_crop_and_backend_slice_agree_on_source_duration_curves_and_fades(self):
        for extra in ({},{'reverse':True},{'speed':2},{'hold':True},{'time_remap':[{'t':0,'v':1},{'t':2,'v':3}]},{'reverse':True,'time_remap':[{'t':0,'v':1},{'t':2,'v':3}]}):
            c={'id':'c','start':4,'in_':3,'out':13,'audio':{'fade_in':2,'fade_out':3},'keyframes':{'audio.duck_db':[{'t':0,'v':0},{'t':3,'v':-12}],'transform.x':[{'t':0,'v':1,'e':'bezier','o':[.2,2]},{'t':8,'v':10,'i':[.3,-2]}]},**extra}
            client=plan(c,begin=.5,end=2);server=slice_clip(c,.5,2)
            for key in ('start','in_','out'):self.assertAlmostEqual(client[key],server[key],places=9)
            self.assertAlmostEqual(render.clip_dur(client),render.clip_dur(server),places=9);self.assertEqual(fade_spec(client,1.5),fade_spec(server,1.5))
            for key in client['keyframes']:
                for at in (0,.25,.75,1.49):self.assertAlmostEqual(render.kf_eval(client['keyframes'][key],at),render.kf_eval(server['keyframes'][key],at),places=7)
    def test_trim_overlap_slice_and_reextension_restore_the_original_ramp_and_duck_clock(self):
        original={'id':'c','start':0,'in_':10,'out':40,'speed':1,'time_remap':[{'t':0,'v':1},{'t':2,'v':3},{'t':4,'v':1}],
          'keyframes':{'audio.duck_db':[{'t':0,'v':0},{'t':1,'v':-12},{'t':3,'v':0}]}}
        cropped=plan(original,begin=5,end=20);sliced=slice_clip(cropped,2,12)
        self.assertEqual(sliced['source_edit_window']['ramp']['offset'],7)
        self.assertEqual(sliced['source_edit_window']['duck']['offset'],7)
        restored=plan(sliced,begin=-3,end=10,options={'start':4});expected=plan(original,begin=4,end=17)
        self.assertAlmostEqual(restored['in_'],18);self.assertEqual(restored['time_remap'],expected['time_remap']);self.assertEqual(restored['keyframes'],expected['keyframes'])
        for key in ('in_','out','start'):self.assertAlmostEqual(restored[key],expected[key])
        # Repeating slices must advance the inherited clock each time.
        again=slice_clip(sliced,1,5);self.assertEqual(again['source_edit_window']['ramp']['offset'],8)
        restored=plan(again,begin=-4,end=5,options={'start':4});self.assertAlmostEqual(restored['in_'],18)

    def test_backend_overlap_invalidates_superseded_history_and_counts_inherited_points(self):
        import overlap_normalization as overlap
        from unittest.mock import patch
        original={'id':'c','start':0,'in_':10,'out':40,'time_remap':[{'t':0,'v':1},{'t':2,'v':3},{'t':4,'v':1}],
          'keyframes':{'audio.duck_db':[{'t':0,'v':0},{'t':1,'v':-12},{'t':3,'v':0}]}}
        cropped=plan(original,begin=5,end=20);cropped['time_remap']=[{'t':0,'v':2}];cropped['keyframes']['audio.duck_db']=[{'t':0,'v':-6}]
        sliced=slice_clip(cropped,1,4);self.assertNotIn('ramp',sliced['source_edit_window']);self.assertNotIn('duck',sliced['source_edit_window'])
        cropped=plan(original,begin=5,end=20);project={'sequences':[{'id':'s','tracks':[{'id':'V','clips':[cropped,{'id':'cover','start':8,'in_':0,'out':1}]}]}]};before=copy.deepcopy(project)
        with patch.object(overlap,'MAX_COPIED_POINTS',8),self.assertRaisesRegex(ValueError,'too many automation'):overlap.normalize_tracks(project)
        self.assertEqual(project,before)

    def test_ducking_slice_uses_unclamped_submicrosecond_linear_intervals(self):
        c={'id':'c','start':0,'in_':0,'out':1,'keyframes':{'audio.duck_db':[{'t':0,'v':0},{'t':1e-7,'v':-20}]}}
        part=slice_clip(c,5e-8,.5);self.assertAlmostEqual(part['keyframes']['audio.duck_db'][0]['v'],-10)

    def test_signed_fade_specs_agree_at_negative_and_past_duration_offsets(self):
        c={'id':'c','start':5,'in_':5,'out':7,'audio':{'fade_in':1,'fade_out':1}}
        for begin,end in ((-2,4),(3,4),(.5,1.5)):
            part=plan(c,begin=begin,end=end);self.assertEqual(frontend_fades(part,end-begin,[0])['spec'],fade_spec(part,end-begin))


if __name__=='__main__':unittest.main(verbosity=2)
