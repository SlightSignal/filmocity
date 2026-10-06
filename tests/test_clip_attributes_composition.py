"""Independent decoded selective Paste Attributes controls (Linux FFmpeg).

Production planner/operation adapter are used; known tones, linear fades and RGB
values supply the oracle. Framework ownership/history uses the separate saved
route suite. Native browser/Windows and arbitrary processor equivalence are not
claimed. Existing gain automation uses 128-sample control blocks and renderer
Bezier interpolation is bounded by its existing 12-segment approximation.
"""
import array
import ast
import copy
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import uuid
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import render
import clip_attributes
from overlap_normalization import slice_clip


def operation_adapter():
    tree=ast.parse((ROOT/'backend/server.py').read_text())
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in ('apply_ops','_walk')]
    scope={'copy':copy,'uuid':uuid}
    exec(compile(ast.Module(nodes,type_ignores=[]),'production-operation-adapter','exec'),scope)
    return scope['apply_ops']


class ClipAttributesComposition(unittest.TestCase):
    def setUp(self):
        folder=tempfile.TemporaryDirectory(prefix='filmocity-attributes-composition-')
        self.addCleanup(folder.cleanup);self.root=Path(folder.name);self.serial=0;self.extra_tracks=[]
        raw=self.root/'source.rgb';raw.write_bytes(bytes([40,80,120])*64*48*120)
        source=self.root/'source.wav'
        values=array.array('h',(round(8192*math.sin(2*math.pi*f*n/48000)) for n in range(12*48000) for f in (480,960)))
        if sys.byteorder!='little':values.byteswap()
        with wave.open(str(source),'wb') as stream:
            stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(values.tobytes())
        movie=self.root/'source.mkv'
        self.run_command(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-video_size','64x48','-framerate','10',
                          '-i',str(raw),'-i',str(source),'-map','0:v:0','-map','1:a:0','-c:v','ffv1','-pix_fmt','bgr0','-c:a','pcm_s16le',str(movie)])
        self.media={'id':'m','name':'Known RGB/stereo tones','path':str(movie),'duration':12,'fps':10,'frame_rate':'10/1',
                    'width':64,'height':48,'has_video':True,'has_audio':True,'channels':2,'sample_rate':48000}
        self.context={'workspace':'attributes-workspace','project':'attributes-project','revision':'r0'}

    @staticmethod
    def clip(cid='target',begin=0,end=4,**kw):
        return {'id':cid,'media_id':'m','start':0,'in_':begin,'out':end,'speed':1,**kw}

    def project(self,clip):
        return {'id':'attributes-document','media':{'m':copy.deepcopy(self.media)},'sequences':[{
            'id':'s','name':'Attributes','width':64,'height':48,'fps':10,'master':{},
            'tracks':[{'id':'V1','kind':'video','index':1,'clips':[copy.deepcopy(clip)]}]+copy.deepcopy(self.extra_tracks)}]}

    def request(self,donor,groups,timing='seconds',animation=True):
        return {'_context':copy.deepcopy(self.context),'sequence':'s','clip_ids':['target'],
                'donor':{'version':1,'clip':copy.deepcopy(donor),'sequence':{'id':'s','width':64,'height':48,'fps':10},
                         'context':copy.deepcopy(self.context),'media':copy.deepcopy(self.media)},
                'groups':groups,'timing':timing,'include_animation':animation}

    def paste(self,donor,target,groups,timing='seconds',animation=True):
        project=self.project(target);before=copy.deepcopy(project);body=self.request(donor,groups,timing,animation)
        result=clip_attributes.plan(project,body)
        self.assertEqual(project,before,'Planning changed its input')
        self.assertTrue(result['ok'],result.get('issues'))
        after=copy.deepcopy(project);operation_adapter()(after,result['ops'])
        self.serial+=1
        (self.root/f'plan-{self.serial}.json').write_text(json.dumps({'before':before,'request':body,'plan':result,'after':after},indent=2)+'\n')
        clip=after['sequences'][0]['tracks'][0]['clips'][0]
        for key in ('media_id','start','in_','out','speed','reverse','hold','time_remap','group','linked_audio_id','note','markers'):
            self.assertEqual((key in clip,clip.get(key)),(key in target,target.get(key)),key)
        self.assertEqual(after['media'],before['media'])
        return clip

    def run_command(self,argv,stdin=None,*,cwd=None):
        result=subprocess.run(argv,input=stdin,capture_output=True,timeout=60,cwd=cwd)
        self.assertEqual(result.returncode,0,result.stderr[-5000:].decode(errors='replace'))
        return result.stdout

    def export(self,clip,audio=True):
        self.serial+=1;document=self.project(clip);before=copy.deepcopy(document)
        path=self.root/f'output-{self.serial}.{"wav" if audio else "mkv"}'
        (self.root/f'project-{self.serial}.json').write_text(json.dumps(document,indent=2)+'\n')
        with render.RenderContext(scratch_parent=str(self.root)) as context:
            preset={'format':'audio','acodec':'wav_float'} if audio else {'vcodec':'ffv1','acodec':'pcm_f32le','color_processing':'rgb'}
            command,graph=render.build_command(document,'s',str(path),preset,context=context)
            (self.root/f'graph-{self.serial}.txt').write_text(graph);self.run_command(list(command))
        self.assertEqual(document,before)
        args=['ffmpeg','-v','error','-i',str(path)]
        raw=self.run_command(args+(['-map','0:a:0','-acodec','pcm_f32le','-f','f32le','-'] if audio else ['-map','0:v:0','-pix_fmt','rgb24','-f','rawvideo','-']))
        if not audio:return raw
        samples=array.array('f');samples.frombytes(raw)
        if sys.byteorder!='little':samples.byteswap()
        self.assertEqual(len(samples),round(render.clip_dur(clip)*48000)*2)
        return samples

    @staticmethod
    def amplitude(data,frequency,begin,end,channel=0):
        first,last=round(begin*48000),round(end*48000);values=data[2*first+channel:2*last+channel:2]
        cosine=sum(v*math.cos(2*math.pi*frequency*(first+i)/48000) for i,v in enumerate(values))
        sine=sum(v*math.sin(2*math.pi*frequency*(first+i)/48000) for i,v in enumerate(values))
        return 2*math.hypot(cosine,sine)/len(values)

    @staticmethod
    def pixel(data,n=5,x=32,y=24):
        begin=(n*64*48+y*64+x)*3;return list(data[begin:begin+3])

    def fade_donor(self):
        donor=slice_clip(self.clip('donor',end=10,audio={'fade_in':0,'fade_out':2,'constant_power':False}),8,10)
        donor['start']=0
        return donor

    def test_seconds_preserves_visible_donor_fade_for_equivalent_target_shapes(self):
        donor=self.fade_donor();results=[]
        for target in [self.clip(),self.clip(audio={})]:
            actual=self.paste(donor,target,['audio_fades']);samples=self.export(actual);results.append(samples)
            self.assertAlmostEqual(self.amplitude(samples,480,.5,.75),.25*.6875,delta=3e-5)
            self.assertEqual(max(map(abs,samples[2*3*48000:])),0)
        self.assertEqual(results[0].tobytes(),results[1].tobytes())

    def test_scale_maps_visible_fade_to_destination_duration(self):
        actual=self.paste(self.fade_donor(),self.clip(),['audio_fades'],timing='scale')
        samples=self.export(actual)
        self.assertAlmostEqual(self.amplitude(samples,480,.5,.75),.25*.84375,delta=3e-5)
        self.assertAlmostEqual(self.amplitude(samples,480,3,3.25),.25*.21875,delta=3e-5)
        self.assertEqual(actual['audio']['fade_window']['duration'],20)
        self.assertEqual(actual['audio']['fade_window']['offset'],16)

    def test_audio_controls_do_not_transfer_picture_link_and_are_shape_independent(self):
        donor=self.clip('donor',audio={'channels':'left','pan':.5,'maintain_pitch':False,'linked':False})
        results=[]
        for target in [self.clip(),self.clip(audio={})]:
            actual=self.paste(donor,target,['audio_controls']);self.assertIsNot(actual['audio'].get('linked'),False)
            samples=self.export(actual);results.append(samples)
            self.assertAlmostEqual(self.amplitude(samples,480,1,1.25),.125,delta=3e-5)
            self.assertAlmostEqual(self.amplitude(samples,480,1,1.25,1),.25,delta=3e-5)
            self.assertLess(self.amplitude(samples,960,1,1.25,1),1e-5)
        self.assertEqual(results[0].tobytes(),results[1].tobytes())
        muted=self.paste(donor,self.clip(audio={'linked':False}),['audio_controls'])
        self.assertEqual(max(map(abs,self.export(muted))),0)

    def test_gain_only_keeps_unselected_curves_pan_fades_and_linkage(self):
        target=self.clip(audio={'pan':.5,'channels':'right','linked':True,'fade_in':1,'constant_power':False},
            keyframes={'transform.opacity':[{'t':0,'v':.5}],'color.exposure':[{'t':0,'v':.2}]},note='Keep')
        donor=self.clip('donor',audio={'gain_db':-6.020599913279624,'pan':-1,'linked':False,'fade_in':0})
        actual=self.paste(donor,target,['audio_gain']);self.assertEqual(actual['keyframes'],target['keyframes'])
        for key in ('pan','channels','linked','fade_in','constant_power'):self.assertEqual(actual['audio'][key],target['audio'][key])
        samples=self.export(actual)
        self.assertAlmostEqual(self.amplitude(samples,960,1.25,1.5),.0625,delta=3e-5)
        self.assertAlmostEqual(self.amplitude(samples,960,1.25,1.5,1),.125,delta=3e-5)
        self.assertAlmostEqual(self.amplitude(samples,960,.25,.5,1),.125*.375,delta=3e-5)

    def test_selected_picture_stack_and_legacy_crop_are_both_rendered(self):
        donor=self.clip('donor',fx_stack=[{'type':'invert','params':{}}],effects={'crop':{'l':.25,'r':.25}})
        actual=self.paste(donor,self.clip(audio={'linked':False}),['video_effects']);pixels=self.export(actual,False)
        self.assertEqual(self.pixel(pixels),[215,175,135]);self.assertEqual(self.pixel(pixels,x=4),[0,0,0])
        self.assertEqual(actual['audio'],{'linked':False})

    def test_selected_audio_stack_swaps_channels_and_keeps_unselected_gain(self):
        donor=self.clip('donor',afx_stack=[{'type':'swap_channels','params':{}}],audio_fx={'eq':{'low_db':0,'mid_db':0,'high_db':0}})
        actual=self.paste(donor,self.clip(audio={'gain_db':-6.020599913279624}),['audio_effects']);samples=self.export(actual)
        self.assertEqual(actual['audio_fx'],donor['audio_fx']);self.assertEqual(actual['afx_stack'],donor['afx_stack'])
        self.assertAlmostEqual(self.amplitude(samples,960,1,1.25),.125,delta=3e-5)
        self.assertAlmostEqual(self.amplitude(samples,480,1,1.25,1),.125,delta=3e-5)
        self.assertLess(self.amplitude(samples,480,1,1.25),1e-5)

    def test_animation_false_keeps_target_curve_while_copying_scalar_gain(self):
        target=self.clip(audio={'gain_db':0},keyframes={'audio.gain_db':[{'t':0,'v':-6.020599913279624},{'t':4,'v':-6.020599913279624}]})
        donor=self.clip('donor',audio={'gain_db':-18},keyframes={'audio.gain_db':[{'t':0,'v':-30},{'t':4,'v':0}]})
        actual=self.paste(donor,target,['audio_gain'],animation=False)
        self.assertEqual(actual['keyframes'],target['keyframes']);self.assertEqual(actual['audio']['gain_db'],-18)
        self.assertEqual(self.export(actual).tobytes(),self.export(target).tobytes())

    def test_bezier_scale_preserves_handles_endpoints_and_decoded_easing(self):
        curve=[{'t':0,'v':-12,'e':'bezier','o':[1/3,0]},{'t':2,'v':0,'i':[1/3,0]}]
        donor=self.clip('donor',end=2,keyframes={'audio.gain_db':curve})
        target=self.clip(keyframes={'transform.x':[{'t':0,'v':7}]})
        actual=self.paste(donor,target,['audio_gain'],timing='scale');mapped=actual['keyframes']['audio.gain_db']
        self.assertEqual(mapped,[dict(curve[0]),dict(curve[1],t=4)])
        self.assertEqual(actual['keyframes']['transform.x'],target['keyframes']['transform.x'])
        samples=self.export(actual)
        for begin,end in [(.75,.875),(1.75,1.875),(3.75,3.875)]:
            # Analytic cubic y=3u^2-2u^3 because handles keep x(u)=u.
            # Linear-segment error <=max|F''|/(8*12^2) plus one128-sample
            # control delay is <.0032 absolute amplitude for this12dB curve.
            expected=sum(.25*10**((-12+12*(3*(n/48000/4)**2-2*(n/48000/4)**3))/20) for n in range(round(begin*48000),round(end*48000)))/round((end-begin)*48000)
            self.assertAlmostEqual(self.amplitude(samples,480,begin,end),expected,delta=.0032)
        seconds=self.paste(donor,target,['audio_gain'],timing='seconds')
        self.assertEqual(seconds['keyframes']['audio.gain_db'],curve)
        self.assertAlmostEqual(self.amplitude(self.export(seconds),480,3,3.25),.25,delta=3e-5)

    def test_explicit_equal_visible_duck_replacement_retires_target_history_before_extension(self):
        original=self.clip(keyframes={'audio.duck_db':[{'t':0,'v':-24,'e':'hold'},{'t':1,'v':0,'e':'linear'},{'t':2,'v':0,'e':'linear'},{'t':4,'v':0,'e':'linear'}]})
        target=slice_clip(original,1,3);target['start']=0;donor=self.clip('donor',end=2,keyframes=copy.deepcopy(target['keyframes']))
        actual=self.paste(donor,target,['audio_gain'])
        self.assertNotIn('duck',actual.get('source_edit_window') or {})
        js="const fs=require('fs'),p=require(process.argv[1]),c=JSON.parse(fs.readFileSync(0,'utf8'));process.stdout.write(JSON.stringify(p.trim(c,-1,2,{duration:2,sourceOffset:t=>t,speedAt:()=>1},{start:0,sourceLimit:12})));"
        extended=json.loads(self.run_command(['node','-e',js,str(ROOT/'frontend/clip-split.js')],json.dumps(actual).encode()))
        self.assertAlmostEqual(self.amplitude(self.export(extended),480,.25,.5),.25,delta=3e-5)

    def test_retimed_target_keeps_source_clock_and_scales_animation_by_visible_duration(self):
        donor=self.clip('donor',end=2,keyframes={'audio.gain_db':[{'t':0,'v':-6.020599913279624},{'t':2,'v':-6.020599913279624}]})
        for timing_fields in [{'speed':2,'reverse':True},{'time_remap':[{'t':0,'v':1,'e':'hold'},{'t':1,'v':3,'e':'hold'}]}]:
            target=self.clip(end=4,**timing_fields);duration=render.clip_dur(target)
            actual=self.paste(donor,target,['audio_gain'],timing='scale')
            self.assertEqual(actual['keyframes']['audio.gain_db'][-1]['t'],duration)
            before=self.export(target);after=self.export(actual)
            self.assertEqual(len(before),len(after))
            self.assertLess(max(abs(b*.5-a) for a,b in zip(after,before)),2e-6)

    def test_selected_motion_mask_and_lut_keep_independent_picture_geometry(self):
        lut=self.root/'swap-red-blue.cube'
        lut.write_text('LUT_3D_SIZE 2\n'+'\n'.join(f'{b} {g} {r}' for b in (0,1) for g in (0,1) for r in (0,1))+'\n')
        donor=self.clip('donor',transform={'opacity':.5},mask={'type':'rect','x':.25,'y':0,'w':.5,'h':1,'feather':0},color={'lut':str(lut)})
        target=self.clip(audio={'linked':False},keyframes={'audio.gain_db':[{'t':0,'v':-6}]})
        reviewed=clip_attributes.inspect(self.project(target),self.request(donor,['motion','color','mask']))
        self.assertTrue(reviewed['ok']);self.assertIn(str(lut),reviewed['resources']);self.assertTrue(reviewed['resource_stamps'])
        actual=self.paste(donor,target,['motion','color','mask']);pixels=self.export(actual,False)
        for a,b in zip(self.pixel(pixels),[60,40,20]):self.assertAlmostEqual(a,b,delta=1)
        self.assertEqual(self.pixel(pixels,x=4),[0,0,0]);self.assertEqual(actual['keyframes'],target['keyframes'])

    def test_picture_transition_seconds_vs_scale_has_independent_frame_values(self):
        donor=self.clip('donor',end=2,transition_in={'type':'dissolve','duration':.5,'align':'start'})
        target=self.clip(audio={'linked':False})
        seconds=self.paste(donor,target,['picture_transitions'],timing='seconds')
        scaled=self.paste(donor,target,['picture_transitions'],timing='scale')
        self.assertEqual(seconds['transition_in']['duration'],.5);self.assertEqual(scaled['transition_in']['duration'],1)
        self.assertEqual(self.pixel(self.export(seconds,False)),[40,80,120])
        for a,b in zip(self.pixel(self.export(scaled,False)),[20,40,60]):self.assertAlmostEqual(a,b,delta=1)

    def test_same_sequence_live_matte_transfer_renders_current_track_alpha(self):
        matte=self.clip('matte-clip',media_id=None,graphic={'layers':[{'kind':'shape','shape':'rect','x':0,'y':0,'w':.5,'h':1,'color':'#ffffff','opacity':1}]})
        self.extra_tracks=[{'id':'MATTE','kind':'video','index':2,'muted':True,'clips':[matte]}]
        donor=self.clip('donor',fx_stack=[{'type':'track_matte','params':{'track':'MATTE','type':'alpha','invert':0}}])
        target=self.clip(audio={'linked':False});review=clip_attributes.plan(self.project(target),self.request(donor,['video_effects']))
        self.assertTrue(review['ok'],review['issues']);self.assertTrue(any('live' in w.lower() for w in review['summary']['warnings']))
        actual=self.paste(donor,target,['video_effects']);pixels=self.export(actual,False)
        self.assertEqual(self.pixel(pixels,x=16),[40,80,120]);self.assertEqual(self.pixel(pixels,x=48),[0,0,0])

    def test_same_physical_source_stabilization_uses_bound_real_analysis(self):
        # Stable textured edges allow actual motion analysis; changing center
        # color numbers the native pictures independently of the renderer.
        from random import Random
        random=Random(7123);width,height=256,192
        base=bytes(random.randrange(256) for _ in range(width*height*3))
        raw=self.root/'stabilization-source.rgb'
        with raw.open('wb') as stream:
            for frame in range(80):
                pixels=bytearray(base)
                for y in range(64,128):
                    for x in range(88,168):pixels[(y*width+x)*3:(y*width+x)*3+3]=bytes([40+frame,80,120])
                stream.write(pixels)
        movie=self.root/'stabilization-source.mkv'
        self.run_command(['ffmpeg','-v','error','-y','-f','rawvideo','-pix_fmt','rgb24','-video_size','256x192','-framerate','10','-i',str(raw),'-c:v','ffv1','-pix_fmt','bgr0',str(movie)])
        analysis=self.root/'source.trf'
        # Explicit ASCII is supported by this FFmpeg's transform reader; the
        # separate retained first attempt exposes its binary-reader issue.
        # This third-party filter reads its filename relative to the child cwd.
        # Keep drive separators and Unicode parent paths out of filter syntax.
        self.run_command(['ffmpeg','-v','error','-y','-i',str(movie),'-an','-vf',f'vidstabdetect=result={analysis.name}:fileformat=ascii:shakiness=5:accuracy=9','-f','null','-'],cwd=str(self.root))
        self.media.update(path=str(movie),width=256,height=192,duration=8,has_audio=False,stab_trf=str(analysis))
        donor=self.clip('donor',begin=0,end=2,fx_stack=[{'type':'stabilize','params':{'smoothing':0,'zoom':0,'crop':'keep'}}])
        for reverse in (False,True):
            target=self.clip(begin=2,end=6,reverse=reverse,audio={'linked':False})
            review=clip_attributes.inspect(self.project(target),self.request(donor,['video_effects']))
            self.assertTrue(review['ok'],review['issues']);self.assertEqual(set(review['resources']),{str(analysis),str(movie)})
            self.assertEqual(len(review['resource_stamps']),2)
            actual=self.paste(donor,target,['video_effects']);pixels=self.export(actual,False)
            self.assertEqual(len(pixels),40*64*48*3)
            for index in (0,10,20,39):
                native=59-index if reverse else 20+index
                for a,b in zip(self.pixel(pixels,n=index),[40+native,80,120]):self.assertAlmostEqual(a,b,delta=2)

    def test_foreign_analyzed_stabilization_refuses_without_partial_motion_ops(self):
        donor=self.clip('donor',media_id='foreign',fx_stack=[{'type':'stabilize','enabled':True,'params':{}}],transform={'opacity':.5})
        project=self.project(self.clip());body=self.request(donor,['motion','video_effects'])
        body['donor']['media']={**self.media,'id':'foreign','path':str(self.root/'foreign.mkv')}
        before=copy.deepcopy(project);result=clip_attributes.plan(project,body)
        self.assertFalse(result['ok']);self.assertFalse(result['ops']);self.assertEqual(project,before)


if __name__=='__main__':unittest.main()
