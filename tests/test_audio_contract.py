"""Real PCM exports checked against independent core mixer expectations."""
import array
import copy
import hashlib
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import render as engine
from render_context import RenderContext
from audio_contract import route,fade_spec


class Audio(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Filmocity audio É ');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
        self.source=self.root/'stereo.wav'
        samples=array.array('h',[3276,6553]*48000)
        with wave.open(str(self.source),'wb') as stream:stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(samples.tobytes())
        self.before=hashlib.sha256(self.source.read_bytes()).hexdigest()
        self.clip={'id':'c','media_id':'m','start':0,'in_':0,'out':1,'audio':{}}
        self.video={'id':'V1','kind':'video','index':1,'clips':[self.clip]}
        self.audio={'id':'A1','kind':'audio','index':1,'clips':[]}
        self.seq={'id':'s','width':64,'height':48,'fps':30,'tracks':[self.video,self.audio],'captions':[]}
        self.project={'media':{'m':{'path':str(self.source),'duration':1,'has_audio':True,'has_video':False,'sample_rate':48000,'channels':2}},'sequences':[self.seq]}
    def render(self):
        with RenderContext(scratch_parent=str(self.root)) as owned:
            out=self.root/'mix.wav';engine.render(self.project,'s',str(out),{'format':'audio','acodec':'wav'},context=owned)
        with wave.open(str(out),'rb') as stream:
            self.assertEqual((stream.getnchannels(),stream.getframerate()),(2,48000));samples=array.array('h',stream.readframes(stream.getnframes()))
        self.assertEqual(hashlib.sha256(self.source.read_bytes()).hexdigest(),self.before)
        return samples
    def point(self,samples,time):return [samples[2*round(time*48000)+c]/32768 for c in range(2)]
    def close(self,actual,expected,tolerance=2/32768):
        for a,b in zip(actual,expected):self.assertAlmostEqual(a,b,delta=tolerance)
    def test_linked_video_audio_obeys_the_visible_audio_track_gain(self):
        self.video['gain_db']=12;self.audio['gain_db']=-6
        self.close(self.point(self.render(),.5),[3276/32768*10**(-6/20),6553/32768*10**(-6/20)])
    def test_audio_track_mute_and_solo_control_linked_audio_without_removing_video(self):
        self.audio['muted']=True;self.close(self.point(self.render(),.5),[0,0]);self.audio['muted']=False;self.audio['solo']=True
        self.close(self.point(self.render(),.5),[3276/32768,6553/32768])
        self.seq['tracks'].append({'id':'A2','kind':'audio','index':2,'solo':True,'clips':[]});self.audio['solo']=False
        self.close(self.point(self.render(),.5),[0,0])
    def test_video_solo_also_admits_its_linked_audio_bus(self):
        self.video['solo']=True;self.assertEqual(route(self.seq,self.video,self.clip)['id'],'A1');self.close(self.point(self.render(),.5),[3276/32768,6553/32768])
    def test_same_file_overlaps_are_summed_independently(self):
        self.audio['clips']=[{**copy.deepcopy(self.clip),'id':'second','in_':.2,'out':.8}]
        self.close(self.point(self.render(),.3),[2*3276/32768,2*6553/32768])
        self.close(self.point(self.render(),.9),[3276/32768,6553/32768])
    def test_channel_selection_then_balance_never_crossfeeds_opposite_channel(self):
        for mode in ('stereo','left','right','mono','swap'):
            with self.subTest(mode=mode):
                self.clip['audio']={'channels':mode,'pan':.5}
                base=[3276/32768,6553/32768]
                if mode=='stereo':out=[base[0]*.5,base[1]]
                elif mode=='left':out=[base[0]*.5,base[0]]
                elif mode=='right':out=[base[1]*.5,base[1]]
                elif mode=='mono':out=[sum(base)*.25,sum(base)*.5]
                else:out=[base[1]*.5,base[0]]
                self.close(self.point(self.render(),.5),out)
    def test_tiny_balance_is_not_silently_ignored(self):
        self.clip['audio']={'pan':.005};self.close(self.point(self.render(),.5),[3276/32768*.995,6553/32768])
    def test_mono_is_duplicated_at_unity_like_browser_speaker_upmixing(self):
        with wave.open(str(self.source),'wb') as stream:stream.setparams((1,2,48000,0,'NONE',''));stream.writeframes(array.array('h',[3276]*48000).tobytes())
        self.before=hashlib.sha256(self.source.read_bytes()).hexdigest();self.project['media']['m']['channels']=1
        self.close(self.point(self.render(),.5),[3276/32768]*2)
    def test_each_end_keeps_its_own_fade_curve(self):
        self.clip.update(audio_transition_in={'type':'constant_gain','duration':.4},audio_transition_out={'type':'constant_power','duration':.4})
        samples=self.render();self.close(self.point(samples,.2),[3276/32768*.5,6553/32768*.5]);self.close(self.point(samples,.8),[3276/32768*math.sqrt(.5),6553/32768*math.sqrt(.5)])
    def test_fades_longer_than_clip_are_clamped_and_combine(self):
        self.clip['audio']={'fade_in':2,'fade_out':3,'constant_power':False}
        self.close(self.point(self.render(),.5),[3276/32768*.25,6553/32768*.25])
    def test_exponential_fade_matches_declared_curve_and_not_the_other_end(self):
        self.clip.update(audio_transition_in={'type':'exponential','duration':.4},audio_transition_out={'type':'constant_gain','duration':.4})
        samples=self.render();self.close(self.point(samples,.2),[v/32768*math.sqrt(.00001) for v in (3276,6553)]);self.close(self.point(samples,.8),[v/32768*.5 for v in (3276,6553)])
    def test_disabled_unlinked_and_hold_clips_are_silent(self):
        for patch in ({'enabled':False},{'audio':{'linked':False}},{'hold':True}):
            with self.subTest(patch=patch):
                saved=copy.deepcopy(self.clip);self.clip.update(patch);self.close(self.point(self.render(),.5),[0,0]);self.clip.clear();self.clip.update(saved)
    def test_core_fade_values_match_javascript_contract_at_transition_points(self):
        c={'audio':{'fade_in':.25},'audio_transition_out':{'type':'exponential','duration':.4}}
        script="const a=require('./frontend/audio-preview.js'),c=JSON.parse(process.argv[1]); console.log(JSON.stringify({spec:a.fadeSpec(c,1),gains:[0,.125,.5,.8,.999,1].map(t=>a.fadeGain(c,1,t))}));"
        value=json.loads(subprocess.check_output(['node','-e',script,json.dumps(c)],cwd=ROOT,text=True))
        self.assertEqual(value['spec'],fade_spec(c,1));self.assertAlmostEqual(value['gains'][1],math.sqrt(.5));self.assertAlmostEqual(value['gains'][3],math.sqrt(.00001));self.assertEqual(value['gains'][-1],0)


if __name__=='__main__':unittest.main()
