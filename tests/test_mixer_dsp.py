"""Independent static Web Audio EQ math vs real FFmpeg PCM; no browser DSP.

The response equations below are from the W3C Audio EQ Cookbook (shelf S=1),
not copied from Filmocity's export or parameter code. Browser scheduling,
coefficient interpolation and native compressor/limiter algorithms are separate.
"""
import array
import copy
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


def capture(options):
    return json.loads(subprocess.check_output(['node',str(ROOT/'tests/mixer_probe.cjs'),json.dumps(options)],text=True))


def coefficients(band,rate):
    gain=band['gain'];w=2*math.pi*band['frequency']/rate;c=math.cos(w);s=math.sin(w);A=10**(gain/40)
    if band['type']=='peaking':
        alpha=s/(2*band['q']);a=[1+alpha/A,-2*c,1-alpha/A];b=[1+alpha*A,-2*c,1-alpha*A]
    else:
        beta=math.sqrt(2*A)*s  # shelf slope S=1 in the Web Audio cookbook
        if band['type']=='lowshelf':
            a=[A+1+(A-1)*c+beta,-2*(A-1+(A+1)*c),A+1+(A-1)*c-beta]
            b=[A*(A+1-(A-1)*c+beta),2*A*(A-1-(A+1)*c),A*(A+1-(A-1)*c-beta)]
        elif band['type']=='highshelf':
            a=[A+1-(A-1)*c+beta,2*(A-1-(A+1)*c),A+1-(A-1)*c-beta]
            b=[A*(A+1+(A-1)*c+beta),-2*A*(A-1+(A+1)*c),A*(A+1+(A-1)*c-beta)]
        else:raise ValueError(band['type'])
    return [v/a[0] for v in b],[v/a[0] for v in a]


def reference(samples,snapshot,rate=48000):
    signal=list(samples)
    for layer in snapshot['layers']:
        for band in layer['eq']:
            if not band['gain']:continue
            b,a=coefficients(band,rate);out=[];x1=x2=y1=y2=0.
            for x in signal:
                y=b[0]*x+b[1]*x1+b[2]*x2-a[1]*y1-a[2]*y2
                out.append(y);x2,x1=x1,x;y2,y1=y1,y
            signal=out
        signal=[x*layer['gain'] for x in signal]
    return signal


def source(root,rate=48000):
    values=array.array('h',(round(32768*(.018*math.sin(2*math.pi*70*i/rate)+.013*math.sin(2*math.pi*1000*i/rate)+.009*math.sin(2*math.pi*8000*i/rate))) for i in range(round(rate*.4))))
    path=root/f'tones-{rate}.wav'
    with wave.open(str(path),'wb') as stream:stream.setparams((1,2,rate,0,'NONE',''));stream.writeframes(values.tobytes())
    return path,[x/32768 for x in values]


def project(path,options):
    clip={'id':'c','media_id':'m','start':0,'in_':0,'out':.4,'audio_fx':options.get('clip',{})}
    leaf={'id':'leaf','name':'Leaf','fps':25,'width':64,'height':48,'tracks':[{'id':'A1','kind':'audio','index':1,'clips':[clip],'audio_fx':options.get('track',{})}]}
    media={'m':{'id':'m','name':'Tones','path':str(path),'duration':.4,'has_audio':True,'has_video':False,'channels':1,'sample_rate':48000}}
    if options.get('nested'):
        leaf['master']={'audio_fx':options.get('childMaster',{})}
        seq={'id':'s','name':'Parent','fps':25,'width':64,'height':48,'tracks':[{'id':'A1','kind':'audio','index':1,'audio_fx':options.get('parentTrack',{}),
            'clips':[{'id':'nest','sequence_id':'leaf','start':0,'in_':0,'out':.4,'audio_fx':options.get('parentClip',{})}]}]};sequences=[seq,leaf]
    else:seq=leaf;seq['id']='s';sequences=[seq]
    seq['master']={'gain_db':options.get('gain',0),'audio_fx':options.get('master',{})}
    return {'media':media,'sequences':sequences}


def decoded(path):
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0:a','-af','pan=mono|c0=c0','-c:a','pcm_f32le','-f','f32le','-'],timeout=20)
    return array.array('f',raw)


def export(root,path,options):
    proj=project(path,options);before=copy.deepcopy(proj);output=root/'output.mkv'
    with RenderContext(scratch_parent=str(root)) as context:engine.render(proj,'s',str(output),{'vcodec':'ffv1','acodec':'pcm_f32le'},context=context)
    assert proj==before
    return decoded(output)


class MixerDSP(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='filmocity-mixer-');self.addCleanup(folder.cleanup);self.root=Path(folder.name);self.path,self.samples=source(self.root)
    def compare(self,options):
        actual=export(self.root,self.path,options);expected=reference(self.samples,capture(options))
        self.assertEqual(len(actual),len(expected));error=max(abs(a-b) for a,b in zip(actual,expected))
        self.assertLess(error,3e-6);return actual
    def test_clip_shelves_and_peak_match_independent_web_audio_response(self):
        for band in ('low_db','mid_db','high_db'):
            for gain in (-6,6):
                with self.subTest(band=band,gain=gain):self.compare({'clip':{'eq':{band:gain}}})
    def test_track_eq_matches_static_live_parameters(self):self.compare({'track':{'eq':{'low_db':6,'mid_db':-4,'high_db':3}}})
    def test_master_eq_matches_static_live_parameters(self):self.compare({'master':{'eq':{'low_db':-5,'mid_db':4,'high_db':2}}})
    def test_clip_track_and_master_eq_compose_in_order(self):
        self.compare({'clip':{'eq':{'low_db':4}},'track':{'eq':{'mid_db':-3}},'master':{'eq':{'high_db':6}},'gain':-6})
    def test_nested_master_and_parent_bus_use_the_same_static_eq_response(self):
        self.compare({'nested':True,'clip':{'eq':{'low_db':2}},'track':{'eq':{'mid_db':-2}},'childMaster':{'eq':{'high_db':3}},
            'parentClip':{'eq':{'low_db':-2}},'parentTrack':{'eq':{'high_db':2}},'master':{'eq':{'mid_db':1}},'gain':-3})
    def test_fractional_and_small_eq_gains_are_not_rounded_or_dropped(self):
        for gain in (.25,-.25,.005):
            with self.subTest(gain=gain):self.compare({'master':{'eq':{'low_db':gain,'mid_db':gain,'high_db':gain}}})
    def test_eq_headroom_is_retained_before_later_master_attenuation(self):
        self.compare({'clip':{'eq':{'low_db':48}},'gain':-48})
    def test_shelves_match_ffmpeg_at_three_device_sample_rates(self):
        for rate in (44100,48000,96000):
            with self.subTest(rate=rate):
                path,samples=source(self.root,rate);fx={'eq':{'low_db':6,'mid_db':-3,'high_db':4}}
                out=self.root/'rate.wav';subprocess.run(['ffmpeg','-v','error','-y','-i',str(path),'-af',','.join(engine.audio_fx_chain(fx)),
                    '-c:a','pcm_f32le',str(out)],check=True,capture_output=True,timeout=20)
                actual=decoded(out);snapshot={'layers':[capture({'clip':fx})['layers'][0]]};expected=reference(samples,snapshot,rate)
                self.assertEqual(len(actual),len(expected));self.assertLess(max(abs(a-b) for a,b in zip(actual,expected)),3e-6)
    def test_submillisecond_attack_and_release_are_valid_export_values(self):
        fx={'comp':{'enabled':True,'threshold_db':-40,'ratio':4.25,'attack_ms':.25,'release_ms':.5,'makeup_db':.25}}
        actual=export(self.root,self.path,{'master':fx});self.assertEqual(len(actual),len(self.samples))
        self.assertTrue(all(math.isfinite(x) for x in actual));self.assertGreater(max(abs(x) for x in actual),0)
        # Separate direct FFmpeg reference pins exact fractional controls.
        direct=self.root/'reference.wav';chain='acompressor=threshold='+str(10**(-40/20))+':ratio=4.25:attack=0.25:release=0.5:makeup='+str(10**(.25/20))
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(self.path),'-af',chain,'-c:a','pcm_f32le',str(direct)],check=True,capture_output=True)
        expected=decoded(direct);self.assertLess(max(abs(a-b) for a,b in zip(actual,expected)),3e-6)
    def test_default_export_eq_keeps_previous_filter_response(self):
        fx={'eq':{'low_db':6,'mid_db':-3,'high_db':4}};actual=export(self.root,self.path,{'master':fx});out=self.root/'old.wav'
        subprocess.run(['ffmpeg','-v','error','-y','-i',str(self.path),'-af','aformat=sample_fmts=dblp,bass=g=6.0:f=120,equalizer=f=1000:t=q:w=1:g=-3.0,treble=g=4.0:f=6000',
            '-c:a','pcm_f32le',str(out)],check=True,capture_output=True)
        expected=decoded(out);self.assertLess(max(abs(a-b) for a,b in zip(actual,expected)),3e-6)


if __name__=='__main__':unittest.main()
