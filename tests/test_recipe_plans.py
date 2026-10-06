"""Canonical recipe plans against source clocks, metadata and real decoded PCM."""
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

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import recipe_plans as recipes
from audio_contract import fade_window
from editing_workflow import transcript_basis
from overlap_normalization import _source_offset
from render import clip_dur, kf_eval, build_command
from render_context import RenderContext
from timeline_time import to_frames, from_frames


def fixture(**changes):
    media={key:{'id':key,'path':'/fixtures/'+key+'.mov','duration':30,'has_audio':True,'has_video':True,'width':1920,'height':1080,'fps':30} for key in ('m','a','b','music')}
    clip={'id':'head','media_id':'m','start':10,'in_':2,'out':10,'speed':1,**changes}
    seq={'id':'seq','name':'Original','width':1920,'height':1080,'fps':30,'tracks':[{'id':'V1','kind':'video','index':0,'clips':[clip]},{'id':'V2','kind':'video','index':1,'clips':[]},{'id':'A1','kind':'audio','index':0,'clips':[]}],'markers':[],'captions':[]}
    return {'version':3,'media':media,'sequences':[seq]},clip


def captured(project,mode='talking_head',**settings):
    body={'sequence':'seq',**({'clip_id':'head','silences':False,'punch_every':0,'voice_preset':False,'captions':False} if mode=='talking_head' else {'shots':['a','b'],'sfx':False}),**settings}
    p=recipes.capture(project,body,mode);p.update(signature='frozen',voice_preset={'afx_stack':[{'type':'highpass','params':{'frequency':90}}]},templates={})
    for key,name in (('hook','Hook_-_Big_Statement'),('cta','CTA_-_Follow')):p['templates'][key]=json.loads((ROOT/'assets/templates'/f'{name}.json').read_text())
    return p


def result(payload,silences=None,onsets=None,sfx=None):
    if silences is not None:
        c=next(c for t in payload['sequence_basis']['tracks'] for c in t['clips'] if c['id']==payload['clip_id'])
        silences={'version':1,'kind':'silences','clock':'media','media_id':c['media_id'],'sequence':payload['sequence'],'clip_id':c['id'],'range':{'start':c['in_'],'end':c['out']},'silences':silences}
    return {'version':1,'kind':'recipe','mode':payload['mode'],'signature':payload['signature'],'analysis':{'silences':silences,'onsets':onsets},'sfx_media':sfx}


def apply(project,planned):
    p=copy.deepcopy(project)
    for op in planned['ops']:
        keys=[k.replace('~1','/').replace('~0','~') for k in op['path'].strip('/').split('/')];obj=p
        for key in keys[:-1]:obj=obj[int(key)] if isinstance(obj,list) else obj[key]
        key=int(keys[-1]) if isinstance(obj,list) else keys[-1]
        if op['op']=='insert':obj.insert(key,copy.deepcopy(op['value']))
        else:obj[key]=copy.deepcopy(op['value'])
    return p


class RecipePlans(unittest.TestCase):
    def test_capture_rejects_nonfinite_false_booleans_tiny_punch_and_excess_sources(self):
        p,_=fixture()
        for change in ({'punch_every':-1},{'punch_every':1e-10},{'punch_every':float('nan')},{'silences':1},{'voice_preset':'false'},{'broll':['a']*101}):
            with self.subTest(change=change),self.assertRaises(ValueError):captured(p,**change)
        for change in ({'target':float('inf')},{'target':True},{'captions':'no'},{'shots':[]},{'shots':['a']*101},{'rhythm':'fallback'},{'rhythm':'onsets'}):
            with self.subTest(change=change),self.assertRaises(ValueError):captured(p,'reel',**change)
        for value in (None,[],{},3):
            with self.subTest(source=value),self.assertRaises(ValueError):captured(p,'reel',shots=[value])

    def test_deep_style_and_generated_anchor_budget_refuse_before_construction(self):
        p,_=fixture();style={};cursor=style
        for _ in range(70):cursor['nested']={};cursor=cursor['nested']
        with self.assertRaisesRegex(ValueError,'depth'):captured(p,'reel',caption_style=style)
        p,c=fixture(out=602);p['media']['m']['duration']=700
        with self.assertRaisesRegex(ValueError,'4096 anchors'):captured(p,punch_every=1/30)
        p,c=fixture();p['media']['a']['id']='reused-other-id'
        with self.assertRaisesRegex(ValueError,'missing'):captured(p,'reel')

    def test_silence_ripple_remaps_transcript_before_captions_and_preserves_outside_annotations(self):
        p,c=fixture();seq=p['sequences'][0]
        seq['tracks'][2]['clips']=[{'id':'later','media_id':'m','start':20,'in_':0,'out':2}]
        seq['transcript']=[{'w':'First.','s':10.2,'e':10.8},{'w':'removed.','s':11.3,'e':12},{'w':'Later.','s':13.2,'e':13.8},{'w':'outside.','s':20,'e':21}]
        seq['transcript_basis']=transcript_basis(seq,p);seq['captions']=[{'id':'old','start':10,'end':18,'text':'old speech'},{'id':'out','start':20,'end':21,'text':'outside'}];seq['markers']=[{'id':'mark','time':21}]
        payload=captured(p,silences=True,captions=True);before=copy.deepcopy(p);plan=recipes.plan(p,payload,result(payload,[{'start':3,'end':5}]),'task')
        after=apply(p,plan)['sequences'][0]
        self.assertEqual(p,before);self.assertEqual(plan['summary']['removed_duration'],2);self.assertEqual([(w['w'],w['s']) for w in after['transcript']],[('First.',10.2),('Later.',11.2),('outside.',18)])
        self.assertEqual([(x['text'],x['start']) for x in after['captions']],[('First.',10.2),('Later.',11.2),('outside',18)])
        self.assertEqual(after['markers'][0]['time'],19);self.assertEqual(after['tracks'][2]['clips'][0]['start'],18);self.assertEqual(after['transcript_basis'],transcript_basis(after,apply(p,plan)))

    def test_fractional_silence_boundary_does_not_leave_a_stale_caption_sliver(self):
        p,c=fixture(start=0,in_=0,out=6);seq=p['sequences'][0]
        seq['captions']=[{'id':'stale','start':0,'end':6,'text':'old full caption'}]
        seq['transcript']=[{'w':'First.','s':.2,'e':.8},{'w':'Second.','s':2.2,'e':2.8},{'w':'Third.','s':5.2,'e':5.8}]
        seq['transcript_basis']=transcript_basis(seq,p);payload=captured(p,silences=True,captions=True)
        plan=recipes.plan(p,payload,result(payload,[{'start':1.1,'end':1.9}]),'fractional');caps=apply(p,plan)['sequences'][0]['captions']
        self.assertEqual([cap['text'] for cap in caps],['First.','Second.','Third.']);self.assertTrue(all(cap['end']-cap['start']>.5 for cap in caps))
        self.assertAlmostEqual(plan['summary']['achieved'],5.2,places=9)

    def test_reverse_ramp_fragments_keep_source_integral_gain_curve_and_inherited_fade_clock(self):
        p,c=fixture(reverse=True,time_remap=[{'t':0,'v':1},{'t':2,'v':3}],audio={'gain_db':-3,'fade_in':4,'fade_out':2},keyframes={'audio.gain_db':[{'t':0,'v':-12},{'t':8,'v':0}],'transform.x':[{'t':0,'v':0},{'t':8,'v':100}]})
        payload=captured(p,silences=True);plan=recipes.plan(p,payload,result(payload,[{'start':4,'end':6}]),'ramp');pieces=apply(p,plan)['sequences'][0]['tracks'][0]['clips'];a,b=plan['summary']['ranges'][0]
        for piece in pieces:
            offset=0 if piece['id']=='head' else b-c['start']
            for local in (0,clip_dur(piece)/3,clip_dur(piece)*.9):
                expected=c['out']-_source_offset(c,offset+local);actual=piece['out']-_source_offset(piece,local)
                self.assertAlmostEqual(actual,expected,places=8)
                self.assertAlmostEqual(kf_eval(piece['keyframes']['audio.gain_db'],local),kf_eval(c['keyframes']['audio.gain_db'],offset+local),places=7)
            self.assertAlmostEqual(fade_window(piece,clip_dur(piece))['offset'],offset)
        self.assertGreater(a,10);self.assertEqual(len(pieces),2)

    def test_later_transition_or_peer_conflict_rejects_the_whole_plan(self):
        p,c=fixture(transition_out={'type':'dissolve','duration':2});payload=captured(p,silences=True);before=copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError,'transition'):recipes.plan(p,payload,result(payload,[{'start':3,'end':4},{'start':9,'end':9.5}]))
        self.assertEqual(p,before)
        c.pop('transition_out');p['sequences'][0]['tracks'][2]['clips']=[{'id':'music','media_id':'music','start':0,'in_':0,'out':30}];payload=captured(p,silences=True)
        with self.assertRaisesRegex(ValueError,'other material'):recipes.plan(p,payload,result(payload,[{'start':3,'end':4}]))

    def test_grouped_or_detached_peer_cannot_be_cut_or_moved_alone(self):
        p,c=fixture(group='pair');p['sequences'][0]['tracks'][2]['clips']=[{'id':'peer','media_id':'m','start':20,'in_':0,'out':2,'group':'pair'}];payload=captured(p,silences=True)
        with self.assertRaisesRegex(ValueError,'grouped'):recipes.plan(p,payload,result(payload,[{'start':3,'end':4}]))
        c.pop('group');p['sequences'][0]['tracks'][2]['clips'][0]['group']='later';p['sequences'][0]['tracks'][1].update(locked=True,clips=[{'id':'locked','media_id':'m','start':21,'in_':0,'out':1,'group':'later'}]);payload=captured(p,silences=True)
        with self.assertRaisesRegex(ValueError,'move grouped'):recipes.plan(p,payload,result(payload,[{'start':3,'end':4}]))
        c['audio_detached_id']='peer'
        with self.assertRaisesRegex(ValueError,'Relink'):captured(p,silences=True)

    def test_zero_punch_preserves_existing_curve_and_voice_replacement_is_explicit(self):
        p,c=fixture(audio={'gain_db':-5,'fade_in':1},afx_stack=[{'type':'echo'}],keyframes={'transform.scale':[{'t':0,'v':2},{'t':8,'v':3}],'audio.gain_db':[{'t':0,'v':-5}]})
        payload=captured(p,voice_preset=True);plan=recipes.plan(p,payload,result(payload));new=apply(p,plan)['sequences'][0]['tracks'][0]['clips'][0]
        self.assertEqual(new['keyframes'],c['keyframes']);self.assertEqual(new['audio'],c['audio']);self.assertEqual(new['afx_stack'],payload['voice_preset']['afx_stack']);self.assertTrue(any('replaces' in text for text in plan['summary']['warnings']))
        payload=captured(p,punch_every=1.1);plan=recipes.plan(p,payload,result(payload));new=apply(p,plan)['sequences'][0]['tracks'][0]['clips'][0]
        self.assertEqual(new['keyframes']['audio.gain_db'],c['keyframes']['audio.gain_db'])
        for point in new['keyframes']['transform.scale']:self.assertAlmostEqual((new['start']+point['t'])*30,round((new['start']+point['t'])*30))

    def test_broll_order_repeat_ids_handles_locks_and_collision_guards(self):
        p,c=fixture();payload=captured(p,broll=['b','a','b']);plan=recipes.plan(p,payload,result(payload),'broll');clips=apply(p,plan)['sequences'][0]['tracks'][1]['clips']
        self.assertEqual([x['media_id'] for x in clips],['b','a','b']);self.assertEqual(len({x['id'] for x in clips}),3)
        for clip in clips:self.assertGreater(clip['out'],clip['in_']);self.assertLessEqual(clip['out'],30);self.assertFalse(clip['audio']['linked'])
        p['sequences'][0]['tracks'][1]['locked']=True;payload=captured(p,broll=['a'])
        with self.assertRaisesRegex(ValueError,'Unlock'):recipes.plan(p,payload,result(payload))
        p['sequences'][0]['tracks'][1].update(locked=False,clips=[{'id':'occupied','media_id':'a','start':0,'in_':0,'out':30}]);payload=captured(p,broll=['a'])
        with self.assertRaisesRegex(ValueError,'overlap'):recipes.plan(p,payload,result(payload))

    def test_reel_creates_new_sequence_with_rational_frame_grid_and_repeated_shot_order(self):
        p,_=fixture();p['sequences'][0]['fps']='30000/1001';before=copy.deepcopy(p);payload=captured(p,'reel',shots=['b','a','b'],target=12.1,name='Ordered',hook='Hook',cta='Follow',canvas='portrait');plan=recipes.plan(p,payload,result(payload),'reel');after=apply(p,plan);seq=after['sequences'][-1]
        self.assertEqual(p,before);self.assertEqual(after['sequences'][0],p['sequences'][0]);self.assertEqual(plan['ops'][-1]['op'],'insert');self.assertEqual((seq['width'],seq['height']),(1080,1920));self.assertEqual([c['media_id'] for c in seq['tracks'][0]['clips']],['b','a','b'])
        self.assertEqual(seq['duration'],from_frames(to_frames(12.1,seq['fps']),seq['fps']))
        for track in seq['tracks']:
            if track['kind']=='video':
                for c in track['clips']:
                    for at in (c['start'],c['start']+clip_dur(c)):self.assertAlmostEqual(at*30000/1001,round(at*30000/1001),places=7)
        self.assertAlmostEqual(sum(m['duration'] for m in seq['markers']),seq['duration'],places=9);self.assertEqual(plan,recipes.plan(p,payload,result(payload),'reel'))

    def test_reel_transitions_keep_alternation_frame_handles_and_visible_fallback(self):
        p,_=fixture();p['sequences'][0]['fps']='24000/1001';payload=captured(p,'reel',shots=['a','b','a','b'],target=12)
        plan=recipes.plan(p,payload,result(payload));clips=apply(p,plan)['sequences'][-1]['tracks'][0]['clips']
        self.assertEqual([c['transition_in']['type'] for c in clips[1:]],['dissolve','dip_black','dissolve'])
        for c in clips[1:]:
            transition=c['transition_in'];self.assertAlmostEqual(transition['duration']*24000/1001,round(transition['duration']*24000/1001))
            if transition['type']=='dissolve':self.assertGreaterEqual(c['in_'],transition['duration'])
        p['media']['b']['duration']=from_frames(144,'24000/1001');payload=captured(p,'reel',shots=['a','b'],target=12);plan=recipes.plan(p,payload,result(payload))
        self.assertEqual(plan['summary']['transitions'][0]['type'],'dip_black');self.assertTrue(any('no incoming dissolve handle' in warning for warning in plan['summary']['warnings']))

    def test_long_hook_uses_balanced_wrapping_and_base_scale_is_preserved_by_punches(self):
        p,c=fixture(transform={'scale':2});payload=captured(p,punch_every=2);plan=recipes.plan(p,payload,result(payload));clip=apply(p,plan)['sequences'][0]['tracks'][0]['clips'][0]
        self.assertEqual([point['v'] for point in clip['keyframes']['transform.scale']],[2,2.24,2,2.24]);self.assertEqual(clip['transform']['scale'],2)
        hook='A carefully edited long headline should wrap into two balanced lines for mobile screens';payload=captured(p,'reel',hook=hook,target=15);plan=recipes.plan(p,payload,result(payload));cards=apply(p,plan)['sequences'][-1]['tracks'][1]['clips']
        text=next(layer for layer in cards[0]['graphic']['layers'] if layer.get('kind')=='text');lines=text['text'].split('\n')
        self.assertEqual(len(lines),2);self.assertEqual(' '.join(lines),hook);self.assertLessEqual(max(map(len,lines))*text['size']*.62,1080*.9)
        self.assertTrue(any('approximate' in warning for warning in plan['summary']['warnings']))

    def test_reel_short_source_fails_instead_of_silently_shortening_requested_duration(self):
        p,_=fixture();p['media']['a']['duration']=1;payload=captured(p,'reel',target=10)
        with self.assertRaisesRegex(ValueError,'insufficient source handles'):recipes.plan(p,payload,result(payload))
        p['media']['a']['is_image']=True;payload=captured(p,'reel',target=10);plan=recipes.plan(p,payload,result(payload));self.assertEqual(plan['summary']['achieved'],10)

    def test_reel_onsets_map_through_music_edits_without_even_fallback_or_downbeat_claim(self):
        p,_=fixture();p['media']['music']['duration']=8;payload=captured(p,'reel',target=4,music='music',rhythm='onsets')
        beats=[.25+.5*i for i in range(16)];onsets={'version':1,'kind':'audio_analysis','mode':'beats','scope':'media','clock':'clip-local','media_id':'music','range':{'start':0,'end':8},'measurements':[{'media_id':'music','silent':False,'beats':beats,'tempo_confidence':.99}]}
        plan=recipes.plan(p,payload,result(payload,onsets=onsets));self.assertEqual(plan['summary']['onset_count'],1);self.assertEqual(plan['summary']['music_coverage'],4)
        mapped={from_frames(to_frames(x,30),30) for x in (.25,.75,1.25,1.75,2.25,2.75,3.25,3.75)};self.assertTrue(set(plan['summary']['cuts'])<=mapped)
        with self.assertRaisesRegex(ValueError,'onset'):recipes.plan(p,payload,result(payload))
        onsets['measurements'][0]['tempo_confidence']=.1
        with self.assertRaisesRegex(ValueError,'irregular'):recipes.plan(p,payload,result(payload,onsets=onsets))

    def test_sfx_media_and_reel_share_one_plan_and_never_overlap_or_extend_past_end(self):
        p,_=fixture();payload=captured(p,'reel',target=4,shots=['a','b','a','b'],sfx=True);sfx={'id':'owned-sfx','path':'/owned/whoosh.wav','duration':.8,'has_audio':True,'sample_rate':48000,'channels':2}
        plan=recipes.plan(p,payload,result(payload,sfx=sfx),'sfx');after=apply(p,plan);seq=after['sequences'][-1]
        self.assertEqual(len(plan['ops']),2);self.assertEqual(after['media']['owned-sfx'],sfx);sounds=next(t for t in seq['tracks'] if t['name']=='SFX')['clips'];self.assertEqual(len(sounds),3)
        for left,right in zip(sounds,sounds[1:]):self.assertLessEqual(left['start']+clip_dur(left),right['start']+1e-10)
        self.assertLessEqual(sounds[-1]['start']+clip_dur(sounds[-1]),4)
        single=captured(p,'reel',target=4,shots=['a'],sfx=True);self.assertEqual(len(recipes.plan(p,single,result(single))['ops']),1)

    def test_reel_source_audio_has_its_own_bus_and_custom_template_shape_is_checked(self):
        from audio_contract import route
        p,_=fixture();payload=captured(p,'reel',target=10);plan=recipes.plan(p,payload,result(payload));seq=apply(p,plan)['sequences'][-1];video=seq['tracks'][0];sfx=next(t for t in seq['tracks'] if t['name']=='SFX');sfx['muted']=True
        self.assertEqual(route(seq,video,video['clips'][0])['name'],'Source audio')
        payload=captured(p,'reel',hook='Title');payload['templates']['hook']['layers']=['malformed']
        with self.assertRaisesRegex(ValueError,'template'):recipes.plan(p,payload,result(payload))

    def test_stale_source_sequence_transcript_and_result_are_refused(self):
        p,_=fixture();payload=captured(p);p['media']['m']['duration']=29
        with self.assertRaisesRegex(ValueError,'source changed'):recipes.plan(p,payload,result(payload))
        p,c=fixture();payload=captured(p);c['start']=11
        with self.assertRaisesRegex(ValueError,'dependency changed'):recipes.plan(p,payload,result(payload))
        p,_=fixture();p['sequences'][0].update(transcript=[{'s':10,'e':11,'w':'Hi'}],transcript_basis='stale')
        with self.assertRaisesRegex(ValueError,'transcription'):captured(p,captions=True)
        p,_=fixture();payload=captured(p);value=result(payload);value['signature']='wrong'
        with self.assertRaisesRegex(ValueError,'captured input'):recipes.plan(p,payload,value)

    def test_noop_recipe_does_not_create_an_edit(self):
        p,_=fixture();payload=captured(p);self.assertEqual(recipes.plan(p,payload,result(payload))['ops'],[])

    def test_real_interpreted_reverse_silence_recipe_pcm_matches_original_kept_samples(self):
        with tempfile.TemporaryDirectory(prefix='filmocity-recipe-pcm-') as temporary:
            root=Path(temporary);path=root/'levels.wav';samples=array.array('h')
            for i in range(8*48000):samples.extend([round((.08+.02*(i//48000))*math.sin(2*math.pi*1000*i/48000)*32767)]*2)
            if sys.byteorder!='little':samples.byteswap()
            with wave.open(str(path),'wb') as stream:stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(samples.tobytes())
            p,c=fixture(start=0,in_=4,out=12,speed=2,reverse=True,audio={'maintain_pitch':False,'gain_db':-6})
            p['media']['m'].update(path=str(path),duration=16,has_video=False,fps=20,frame_rate='20/1',interpret_fps=10,channels=2,sample_rate=48000)
            p['sequences'][0]['tracks'][0]['kind']='audio';payload=captured(p,silences=True);plan=recipes.plan(p,payload,result(payload,[{'start':6,'end':8}]),'pcm');after=apply(p,plan)
            def decoded(project,name):
                with RenderContext(scratch_parent=temporary) as context:
                    output=str(root/name);command,_=build_command(project,'seq',output,{'format':'audio','acodec':'wav_float'},context=context)
                    command=list(command[:-1])+['-f','f32le',output];subprocess.run(command,check=True,capture_output=True,timeout=30)
                return Path(output).read_bytes()
            original=decoded(p,'before.raw');actual=decoded(after,'after.raw');a,b=plan['summary']['ranges'][0];stride=48000*2*4
            expected=original[:round(a*stride)]+original[round(b*stride):]
            self.assertEqual(len(actual),len(expected));self.assertEqual(actual,expected)

if __name__=='__main__':unittest.main()
