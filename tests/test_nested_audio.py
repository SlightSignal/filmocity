"""Real nested PCM/picture renders and shared multicamera selection regressions.

These exercise FFmpeg, not a browser. Web Audio DSP/native timing is separate.
"""
import array
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render as engine
from multicam import view
from audio_contract import route
from preflight import inspect_resources
from render_context import RenderContext


def make_sources(root):
    media = {}
    for i, (color, samples) in enumerate((('red',(3276,6553)),('blue',(-9830,1638)))):
        wav = root / f'camera{i}.wav'
        with wave.open(str(wav), 'wb') as stream:
            stream.setparams((2,2,48000,0,'NONE','')); stream.writeframes(array.array('h',samples*28800).tobytes())
        path = root / f'camera{i}.mov'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i',f'color={color}:s=64x48:r=25:d=0.6',
            '-i',str(wav),'-c:v','png','-pix_fmt','rgb24','-c:a','pcm_s16le','-t','0.6',str(path)],check=True,capture_output=True,timeout=20)
        media[f'm{i}']={'id':f'm{i}','name':f'Camera {i}','path':str(path),'duration':.6,'width':64,'height':48,'fps':25,
            'has_video':True,'has_audio':True,'channels':2,'sample_rate':48000}
    return media


def make_project(media):
    tracks=[]
    for i in range(2):
        c={'id':f'v{i}', 'media_id':f'm{i}', 'start':0, 'in_':0, 'out':.6, 'audio':{'linked':False}}
        tracks.extend([{'id':f'V{i}', 'kind':'video', 'index':i+1, 'clips':[c]},
            {'id':f'A{i}', 'kind':'audio', 'index':i+1, 'muted':i!=0, 'clips':[{**c,'id':f'a{i}','audio':{}}]}])
    child={'id':'child','name':'Cameras','width':64,'height':48,'fps':25,'multicam':True,'multicam_audio':'follow','tracks':tracks}
    nest={'id':'nest','sequence_id':'child','start':0,'in_':0,'out':.6,'multicam_angle':1}
    parent={'id':'parent','name':'Parent','width':64,'height':48,'fps':25,'tracks':[
        {'id':'PV','kind':'video','index':1,'clips':[nest]},{'id':'PA','kind':'audio','index':1,'clips':[]}]}
    return {'id':'nested-fixture','media':copy.deepcopy(media),'sequences':[parent,child]}


def render_output(project, output, mode='rgb', renderer=engine):
    before=copy.deepcopy(project)
    with RenderContext(scratch_parent=str(output.parent),stall_timeout=20) as context:
        renderer.render(project,'parent',str(output),{'vcodec':'ffv1','acodec':'pcm_f32le','color_processing':mode},context=context)
    if project != before: raise AssertionError('Rendering mutated saved project')
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-map','0:a','-f','f32le','-c:a','pcm_f32le','-'],timeout=20)
    pixels=subprocess.check_output(['ffmpeg','-v','error','-i',str(output),'-ss','0.2','-frames:v','1','-f','rawvideo','-pix_fmt','rgb24','-'],timeout=20)
    return array.array('f',raw),list(pixels[:3])


class NestedAudio(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder=tempfile.TemporaryDirectory(prefix="filmocity-nested-é's-");cls.root=Path(cls.folder.name)
        cls.media=make_sources(cls.root);cls.hashes={m['path']:hashlib.sha256(Path(m['path']).read_bytes()).hexdigest() for m in cls.media.values()}
    @classmethod
    def tearDownClass(cls):cls.folder.cleanup()
    def setUp(self):
        self.project=make_project(self.media);self.parent,self.child=self.project['sequences']
        self.nest=self.parent['tracks'][0]['clips'][0]
    def tearDown(self):
        self.assertEqual(self.hashes,{p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in self.hashes})
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
    def render(self,mode='rgb'):
        report=inspect_resources(self.project,'parent');self.assertTrue(report['ok'],report)
        return render_output(self.project,self.root/'result.mkv',mode)
    def point(self,samples,time=.3):return list(samples[2*round(time*48000):2*round(time*48000)+2])
    def close(self,samples,expected,time=.3):
        for a,b in zip(self.point(samples,time),expected):self.assertAlmostEqual(a,b,delta=3/32768)
    def plain(self):
        self.child['multicam']=False;self.child['tracks']=self.child['tracks'][:2]
    def test_follow_second_camera_keeps_separate_audio_instead_of_silence(self):
        samples,pixel=self.render();self.close(samples,[-9830/32768,1638/32768]);self.assertGreater(pixel[2],240);self.assertLess(pixel[0],5)
    def test_absent_angle_defaults_to_first_camera_picture_and_sound(self):
        del self.nest['multicam_angle'];samples,pixel=self.render();self.close(samples,[3276/32768,6553/32768]);self.assertGreater(pixel[0],240)
    def test_fixed_audio_uses_configured_track_while_picture_changes(self):
        self.child.update(multicam_audio='fixed',multicam_audio_track='A0')
        samples,pixel=self.render();self.close(samples,[3276/32768,6553/32768]);self.assertGreater(pixel[2],240)
    def test_fixed_audio_can_come_from_hidden_linked_camera(self):
        self.child.update(multicam_audio='fixed',multicam_audio_track='A0')
        self.child['tracks'][0]['clips'][0]['audio']['linked']=True
        self.child['tracks'][1]['clips']=[];self.child['tracks'][3]['clips']=[]
        samples,pixel=self.render();self.close(samples,[3276/32768,6553/32768]);self.assertGreater(pixel[2],240)
    def test_embedded_follow_works_without_separate_audio_tracks(self):
        self.child['tracks']=[t for t in self.child['tracks'] if t['kind']=='video']
        for t in self.child['tracks']:t['clips'][0]['audio']['linked']=True
        samples,pixel=self.render();self.close(samples,[-9830/32768,1638/32768]);self.assertGreater(pixel[2],240)
    def test_fixed_without_separate_tracks_keeps_first_camera_sound(self):
        self.child['multicam_audio']='fixed';self.child['tracks']=[t for t in self.child['tracks'] if t['kind']=='video']
        for t in self.child['tracks']:t['clips'][0]['audio']['linked']=True
        samples,pixel=self.render();self.close(samples,[3276/32768,6553/32768]);self.assertGreater(pixel[2],240)
    def test_follow_uses_track_index_not_audio_list_ordinal(self):
        self.child['tracks'][0]['index']=2;self.child['tracks'][1]['index']=7
        self.child['tracks'][2]['index']=7;self.child['tracks'][3]['index']=2
        samples,_=self.render();self.close(samples,[3276/32768,6553/32768])
    def test_excluded_camera_solo_does_not_silence_selected_sound(self):
        self.child['tracks'][1]['solo']=True;samples,_=self.render();self.close(samples,[-9830/32768,1638/32768])
    def test_two_occurrences_keep_distinct_selected_media_and_add_independently(self):
        self.parent['tracks'][1]['clips']=[]
        self.parent['tracks'].append({'id':'PV2','kind':'video','index':2,'clips':[{**self.nest,'id':'nest2','multicam_angle':0}]})
        samples,_=self.render();self.close(samples,[(3276-9830)/32768,(6553+1638)/32768])
    def test_each_nested_gain_applies_once_with_parent_channels_and_balance(self):
        self.plain();self.child['tracks'][1]['clips'][0]['audio']={'gain_db':-2}
        self.child['tracks'][1]['gain_db']=-3;self.child['master']={'gain_db':-4}
        self.nest['audio']={'gain_db':-5,'channels':'swap','pan':.25}
        self.parent['tracks'][1]['gain_db']=-6;self.parent['master']={'gain_db':-7}
        samples,_=self.render();g=10**(-27/20);self.close(samples,[6553/32768*.75*g,3276/32768*g])
    def test_audio_track_nested_clip_keeps_child_sound_without_picture(self):
        self.parent['tracks'][0]['clips']=[];self.parent['tracks'][1]['clips']=[self.nest]
        samples,pixel=self.render();self.close(samples,[-9830/32768,1638/32768]);self.assertLess(max(pixel),5)
    def test_parent_fades_apply_to_the_child_sum(self):
        self.plain();self.nest['audio_transition_in']={'type':'constant_gain','duration':.4}
        samples,_=self.render();self.close(samples,[3276/32768*.5,6553/32768*.5],.2)
    def test_float_intermediate_retains_headroom_before_parent_attenuation(self):
        self.plain();self.child['master']={'gain_db':24};self.nest['audio']={'gain_db':-24}
        for mode in ('rgb','legacy'):
            with self.subTest(mode=mode):
                samples,_=self.render(mode);self.close(samples,[3276/32768,6553/32768])
    def test_two_nested_levels_retain_gain_once_at_each_level(self):
        self.plain();self.child['master']={'gain_db':-3};self.nest['audio']={'gain_db':-4}
        outer=copy.deepcopy(self.parent);outer['id']='middle';outer['name']='Middle';outer['master']={'gain_db':-5}
        self.parent['tracks'][0]['clips']=[{**self.nest,'sequence_id':'middle','audio':{'gain_db':-6}}]
        self.project['sequences'].append(outer)
        samples,_=self.render();self.close(samples,[3276/32768*10**(-18/20),6553/32768*10**(-18/20)])
    def test_parent_audio_mute_and_solo_exclusion_are_silent(self):
        for patch in ('mute','solo','unlink','hold'):
            with self.subTest(patch=patch):
                saved=copy.deepcopy(self.parent)
                if patch=='mute':self.parent['tracks'][1]['muted']=True
                elif patch=='solo':self.parent['tracks'].append({'id':'PA2','kind':'audio','index':2,'clips':[],'solo':True})
                elif patch=='unlink':self.nest['audio']={'linked':False}
                else:self.nest['hold']=True
                samples,_=self.render();self.close(samples,[0,0])
                self.parent.clear();self.parent.update(saved);self.nest=self.parent['tracks'][0]['clips'][0]
    def test_preflight_ignores_offline_excluded_camera_but_requires_selected_audio(self):
        self.project['media']['m0']['path']=str(self.root/'missing.mov')
        self.assertTrue(inspect_resources(self.project,'parent')['ok'])
        self.child.update(multicam_audio='fixed',multicam_audio_track='A0')
        report=inspect_resources(self.project,'parent');self.assertFalse(report['ok']);self.assertIn('missing_source',[i['code'] for i in report['issues']])
    def test_hidden_fixed_camera_does_not_require_unused_picture_effect_files(self):
        self.child.update(multicam_audio='fixed',multicam_audio_track='A0');self.child['tracks'][0]['clips'][0]['audio']['linked']=True
        self.child['tracks'][0]['clips'][0]['color']={'lut':str(self.root/'absent.cube')}
        self.project['media']['m0']['stab_trf']=str(self.root/'absent.trf')
        self.assertTrue(inspect_resources(self.project,'parent')['ok'])
        samples,_=self.render();self.close(samples,[2*3276/32768,2*6553/32768])
    def test_invalid_angle_and_fixed_audio_fail_explicitly_without_mutating_source(self):
        for angle in (-1,2,True,1.5,'1',None):
            with self.subTest(angle=angle):
                self.nest['multicam_angle']=angle;before=copy.deepcopy(self.project)
                report=inspect_resources(self.project,'parent');self.assertFalse(report['ok']);self.assertIn('invalid_multicam',[i['code'] for i in report['issues']]);self.assertEqual(self.project,before)
        self.nest['multicam_angle']=1;self.child.update(multicam_audio='fixed',multicam_audio_track='unknown')
        self.assertIn('invalid_multicam',[i['code'] for i in inspect_resources(self.project,'parent')['issues']])
    def test_hidden_unlinked_offline_camera_is_not_needed_for_separate_fixed_sound(self):
        self.child.update(multicam_audio='fixed',multicam_audio_track='A0')
        self.child['tracks'][0]['clips'][0]['media_id']='offline'
        self.project['media']['offline']={**self.media['m0'],'id':'offline','path':str(self.root/'offline.mov')}
        samples,_=self.render();self.close(samples,[3276/32768,6553/32768])
    def test_hidden_matte_camera_still_requires_its_picture_dependencies(self):
        self.child['tracks'][2]['clips'][0]['fx_stack']=[{'type':'track_matte','params':{'track':'V0','type':'luma'}}]
        self.child['tracks'][0]['clips'][0]['color']={'lut':str(self.root/'missing.cube')}
        report=inspect_resources(self.project,'parent')
        self.assertFalse(report['ok']);self.assertIn('missing_lut',[i['code'] for i in report['issues']])
    def test_python_and_frontend_choose_identical_tracks_without_saved_mutations(self):
        cases=[]
        for follow in ('follow','fixed'):
            for angle in (0,1):
                child=copy.deepcopy(self.child);child['multicam_audio']=follow;child['multicam_audio_track']='A0';cases.append([child,angle])
                embedded=copy.deepcopy(child);embedded['tracks']=[t for t in embedded['tracks'] if t['kind']=='video'];cases.append([embedded,angle])
        before=copy.deepcopy(cases)
        script="const a=require('./frontend/audio-preview.js');console.log(JSON.stringify(JSON.parse(process.argv[1]).map(([s,n])=>a.multicam(s,n))));"
        actual=json.loads(subprocess.check_output(['node','-e',script,json.dumps(cases)],cwd=ROOT,text=True))
        self.assertEqual(actual,[view(s,n) for s,n in cases]);self.assertEqual(cases,before)
    def test_selected_clip_enable_and_link_controls_still_apply(self):
        selected=view(self.child,1)
        a=next(t for t in selected['tracks'] if t['id']=='A1');v=next(t for t in selected['tracks'] if t['id']=='V1')
        self.assertIsNone(route(selected,v,v['clips'][0]));self.assertEqual(route(selected,a,a['clips'][0])['id'],'A1')
        self.assertIsNone(route(selected,a,{**a['clips'][0],'enabled':False}))


if __name__=='__main__':unittest.main()
