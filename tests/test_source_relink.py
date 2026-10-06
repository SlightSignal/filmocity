"""Pure Relink range/metadata plans, with independent clock and PCM checks."""
import array
import copy
from fractions import Fraction
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import source_relink as relink
import render
from render_context import RenderContext

REPLACEMENT = str(ROOT / 'tests/fixtures/planned-replacement.mkv')
DIFFERENT = str(ROOT / 'tests/fixtures/planned-different.mkv')


def info(duration=10,rate='30000/1001',**changes):
    return {'duration':duration,'frame_rate':rate,'fps':float(Fraction(rate)) if rate else 0,
        'width':64,'height':48,'has_video':True,'has_audio':True,'is_image':False,
        'sample_rate':48000,'channels':2,'channel_layout':'stereo','audio_streams':[{'index':1,'channels':2,'sample_rate':48000}],
        'codec':'ffv1','vcodec':'ffv1','acodec':'pcm_s16le','pix_fmt':'yuv420p',
        'color_transfer':'bt709','color_primaries':'bt709','color_space':'bt709','color_range':'tv',**changes}


def project():
    media={'id':'m','name':'Camera original','path':str(ROOT / 'tests/fixtures/planned-original.mkv'),**info(),
        'ingest_token':'old-token','note':'Keep note','label':'red','markers':[{'t':1,'name':'Authored marker'}],
        'input_transform':'slog3','input_transform_resource':{'name':'slog3','path':'/owned/input.cube'},
        'hdr_peak_nits':900,'proxy':'/proxies/old.mp4','proxy_info':{'source_signature':'old'},'wave':'/waves/old.png',
        'transcript':{'words':[{'word':'Old','start':0,'end':1}]},'stab_trf':'/analysis/old.trf','rendered_from':'old'}
    clip={'id':'c','media_id':'m','start':1,'in_':1,'out':4,'speed':1,'reverse':False,'audio':{'gain_db':-3,'fade_in':.1},
        'keyframes':{'audio.gain_db':[{'t':0,'v':-3},{'t':3,'v':-5}]},'markers':[{'t':2,'name':'Keep'}],
        'source_edit_window':{'version':1,'ramp':{'points':[]}},'group_id':'group','note':'Timeline note'}
    return {'version':3,'id':'document','name':'P','media':{'m':media},'sequences':[{'id':'s','name':'S','fps':'30000/1001','width':64,'height':48,
        'tracks':[{'id':'v','kind':'video','index':0,'locked':True,'clips':[clip]}],'markers':[{'time':2,'name':'Keep'}]}]}


def body(mid='m'):
    return {'media_id':mid,'_context':{'workspace':'w','project':'a','revision':'r1'}}


def plan(p=None,probe=None,b=None,path=REPLACEMENT,stamp=None):
    return relink.plan(p or project(),b or body(),probe or info(),path,stamp or [[path,1000,123,456]])


def apply(p,result):
    candidate=copy.deepcopy(p)
    for op in result['ops']:
        assert op['op']=='set' and op['path'].startswith('/media/')
        candidate['media'][op['path'].split('/')[-1].replace('~1','/').replace('~0','~')]=copy.deepcopy(op['value'])
    return candidate


def child(p,identity='sub',offset=1,duration=4,alias=False,**changes):
    value={**copy.deepcopy(p['media']['m']),'id':identity,'name':identity,'subclip_of':'m','sub_in':offset,'duration':duration,**changes}
    if alias:
        value.update(has_video=False,width=0,height=0,audio_alias={'version':1,'physical_media_id':'m','source_media_id':'m'})
    p['media'][identity]=value
    return value


class SourceRelink(unittest.TestCase):
    def test_complete_timeline_and_editorial_metadata_preserved_with_source_wide_notice(self):
        p=project();before=copy.deepcopy(p);r=plan(p)
        self.assertEqual(p,before);self.assertTrue(r['ok']);self.assertEqual(len(r['ops']),1)
        self.assertEqual(apply(p,r)['sequences'],before['sequences'])
        m=r['media']['m']
        for key in ('note','label','markers','input_transform','input_transform_resource','hdr_peak_nits'):
            self.assertEqual(m[key],before['media']['m'][key])
        for key in ('proxy','proxy_info','wave','transcript','stab_trf','rendered_from'):self.assertNotIn(key,m)
        self.assertTrue(m['workflow_import']);self.assertEqual(m['status'],'unprepared');self.assertNotEqual(m['ingest_token'],'old-token')
        self.assertTrue(r['summary']['uses'][0]['locked']);self.assertIn('locked tracks',' '.join(r['warnings']))
        self.assertIn('markers',' '.join(r['warnings']));self.assertEqual(r['summary']['range_scope'],'container_duration')

    def test_hold_checks_frozen_point_not_timeline_out_and_rejects_exact_eof(self):
        p=project();c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(in_=1,out=101,hold=True,speed=7)
        r=plan(p,info(duration=3));self.assertTrue(r['ok']);self.assertEqual(r['summary']['uses'][0]['duration'],100)
        self.assertEqual(r['summary']['uses'][0]['native_out'],1)
        c.update(in_=3,out=103);r=plan(p,info(duration=3));self.assertFalse(r['ok']);self.assertFalse(r['ops'])
        self.assertIn('held_frame_outside',[x['code'] for x in r['issues']])

    def test_ntsc_interpretation_uses_exact_native_rate_with_rounded_fps_disagreement(self):
        p=project();p['media']['m']['interpret_fps']='24000/1001'
        c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(in_=2.5,out=7.5,hold=True)
        measured=info(duration=2,rate='60000/1001',fps=60)
        r=plan(p,measured);self.assertTrue(r['ok']);self.assertEqual(r['media']['m']['duration'],5)
        self.assertEqual(r['summary']['uses'][0]['native_in'],1)
        self.assertEqual(r['summary']['uses'][0]['source_frame'],59)
        self.assertEqual(r['media']['m']['native_fps'],float(Fraction(60000,1001)))

    def test_audio_only_hold_does_not_require_picture_frame_rate(self):
        p=project();p['media']['m'].update(has_video=False,frame_rate=None,fps=0)
        track=p['sequences'][0]['tracks'][0];track['kind']='audio';track['clips'][0].update(hold=True,in_=1,out=101)
        r=plan(p,info(duration=3,rate=None,has_video=False))
        self.assertTrue(r['ok']);self.assertNotIn('source_frame',r['summary']['uses'][0])
        self.assertEqual(apply(p,r)['sequences'],p['sequences'])

    def test_ordinary_child_own_interpretation_preserves_logical_window(self):
        p=project();p['media']['m']['interpret_fps']=30
        sub=child(p,offset=8,duration=4,interpret_fps=24,native_duration=999,native_fps=1)
        c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(media_id='sub',in_=0,out=4)
        measured=info(duration=5,rate='60/1');r=plan(p,measured)
        self.assertTrue(r['ok']);self.assertEqual(r['media']['m']['duration'],10)
        self.assertEqual(r['media']['sub']['duration'],4);self.assertEqual(r['media']['sub']['sub_in'],8)
        self.assertEqual(r['media']['sub']['interpret_fps'],24);self.assertEqual(r['media']['sub']['native_duration'],1.6)
        self.assertEqual(r['summary']['dependents'][0]['native_out'],4.8)
        self.assertEqual(r['summary']['uses'][0]['native_in'],3.2)

    def test_unused_alias_and_subclip_windows_are_required_before_any_ops(self):
        for alias in (False,True):
            with self.subTest(alias=alias):
                p=project();p['sequences'][0]['tracks'][0]['clips'][0].update(in_=0,out=2)
                child(p,offset=2,duration=6,alias=alias);before=copy.deepcopy(p)
                r=plan(p,info(duration=5));self.assertFalse(r['ok']);self.assertEqual(r['ops'],[]);self.assertEqual(r['media'],{});self.assertEqual(p,before)
                self.assertIn('dependent_too_short',[x['code'] for x in r['issues']])

    def test_typed_alias_preserves_audio_identity_and_requires_parent_factor(self):
        p=project();p['media']['m']['interpret_fps']=30
        alias=child(p,'audio',offset=2,duration=3,alias=True,interpret_fps=30)
        r=plan(p,info(rate='60/1'));self.assertTrue(r['ok']);m=r['media']['audio']
        self.assertFalse(m['has_video']);self.assertTrue(m['has_audio']);self.assertEqual(m['width'],0);self.assertEqual(m['codec'],'pcm_s16le')
        self.assertFalse(relink.VIDEO_ONLY.intersection(m));self.assertEqual(m['frame_rate'],'60/1')
        self.assertEqual(m['audio_alias'],alias['audio_alias']);self.assertNotEqual(m['ingest_token'],r['media']['m']['ingest_token'])
        alias['interpret_fps']=24;r=plan(p,info(rate='60/1'));self.assertFalse(r['ok']);self.assertIn('alias_interpretation_mismatch',[x['code'] for x in r['issues']])

    def test_child_hold_point_respects_own_window_even_with_parent_handles(self):
        p=project();child(p,offset=3,duration=2)
        c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(media_id='sub',in_=1.5,out=31.5,hold=True)
        self.assertTrue(plan(p)['ok']);c.update(in_=2,out=32)
        self.assertFalse(plan(p)['ok'])

    def test_reverse_and_ramp_duration_unchanged_source_bounds_independent_of_speed(self):
        p=project();c=p['sequences'][0]['tracks'][0]['clips'][0]
        c.update(in_=1,out=6,reverse=True,speed=100,time_remap=[{'t':0,'v':2},{'t':2,'v':3}])
        # Integral through t=2 is five source seconds, so duration is exactly2.
        r=plan(p,info(duration=6));self.assertTrue(r['ok']);self.assertEqual(r['summary']['uses'][0]['duration'],2)
        self.assertEqual(apply(p,r)['sequences'],p['sequences']);self.assertFalse(plan(p,info(duration=5.99))['ok'])

    def test_all_sequences_disabled_and_locked_uses_checked_without_normalization(self):
        p=project();other=copy.deepcopy(p['sequences'][0]);other['id']='nested';other['tracks'][0]['clips'][0].update(out=9,enabled=False)
        p['sequences'].append(other);before=copy.deepcopy(p)
        r=plan(p,info(duration=8));self.assertFalse(r['ok']);self.assertEqual(p,before);self.assertEqual(len(r['summary']['uses']),2)

    def test_source_stream_and_image_compatibility_report_not_partial_edit(self):
        for changed in ({'has_audio':False},{'has_video':False},{'is_image':True}):
            with self.subTest(changed=changed):
                r=plan(probe=info(**changed));self.assertFalse(r['ok']);self.assertFalse(r['ops'])
        p=project();p['media']['m'].update(is_image=True,has_audio=False,duration=25)
        c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(in_=300,out=400,hold=True)
        r=plan(p,info(duration=5,rate=None,is_image=True,has_audio=False))
        self.assertTrue(r['ok']);self.assertEqual(r['media']['m']['duration'],25);self.assertEqual(r['summary']['uses'][0]['duration'],100)

    def test_measured_absent_fields_cleared_but_authored_color_override_kept(self):
        p=project();p['media']['m'].update(hdr_max_cll=4000,hdr_mastering_peak_nits=4000,channels=8)
        replacement=info();replacement.pop('audio_streams')
        r=plan(p,replacement);m=r['media']['m']
        self.assertNotIn('hdr_max_cll',m);self.assertNotIn('hdr_mastering_peak_nits',m);self.assertNotIn('audio_streams',m)
        self.assertEqual(m['channels'],2);self.assertEqual(m['hdr_peak_nits'],900);self.assertEqual(m['input_transform'],'slog3')

    def test_authored_source_marker_bounds_refuse_and_unknown_clocks_are_disclosed(self):
        p=project();p['sequences'][0]['tracks'][0]['clips'][0].update(in_=0,out=2)
        for marker in ({'t':5,'name':'Point'},{'start':1,'end':5,'text':'Range'}):
            p['media']['m']['markers']=[marker];r=plan(p,info(duration=3))
            self.assertFalse(r['ok']);self.assertFalse(r['ops']);self.assertIn('source_marker_outside',[x['code'] for x in r['issues']])
        p['media']['m']['markers']=[{'clock':'custom','position':999,'note':'Keep opaque authored annotation'}]
        r=plan(p,info(duration=3));self.assertTrue(r['ok']);self.assertEqual(r['media']['m']['markers'],p['media']['m']['markers'])
        self.assertIn('source_marker_clock_unknown',[x['code'] for x in r['issues']])

    def test_full_owner_project_source_identity_and_warnings_bind_fingerprint(self):
        p=project();r=plan(p);self.assertEqual(plan(copy.deepcopy(p)),r)
        for change in ('context','timeline','stamp','path','probe'):
            q=copy.deepcopy(p);b=body();i=info();path=REPLACEMENT;stamp=[[path,1000,123,456]]
            if change=='context':b['_context']['project']='b'
            elif change=='timeline':q['sequences'][0]['tracks'][0]['clips'][0]['note']='Changed'
            elif change=='stamp':stamp[0][2]+=1
            elif change=='path':path=DIFFERENT;stamp[0][0]=path
            else:i['width']=128
            self.assertNotEqual(plan(q,i,b,path,stamp)['fingerprint'],r['fingerprint'],change)

    def test_repeated_accepted_source_noop_retains_ready_derivatives(self):
        p=project();child(p,'audio',alias=True)
        first=plan(p);saved=apply(p,first)
        saved['media']['m'].update(proxy='/new.mp4',status='ready',proxy_info={'validation':'new'})
        again=plan(saved);self.assertTrue(again['ok']);self.assertFalse(again['summary']['changed']);self.assertEqual(again['ops'],[])
        self.assertEqual(saved['media']['m']['proxy'],'/new.mp4')
        different=plan(saved,stamp=[[REPLACEMENT,1001,124,456]])
        self.assertTrue(different['summary']['changed']);self.assertNotEqual(different['media']['m']['ingest_token'],first['media']['m']['ingest_token'])

    def test_missing_cycles_nested_chain_ambiguous_ids_and_bad_alias_refuse(self):
        cases=[]
        p=project();p['media']['m']['subclip_of']='missing';cases.append((p,body()))
        p=project();sub=child(p);p['media']['m']['subclip_of']='sub';cases.append((p,body()))
        p=project();child(p);child(p,'deep',subclip_of='sub');cases.append((p,body()))
        p=project();p['media']['m']['id']='wrong';cases.append((p,body()))
        p=project();child(p,alias=True)['audio_alias']['physical_media_id']='wrong';cases.append((p,body()))
        for p,b in cases:
            with self.subTest(p=p),self.assertRaises(ValueError):plan(p,b=b)

    def test_finite_strict_flags_context_and_authoritative_invalid_frame_rate(self):
        for changed in ({'duration':float('nan')},{'duration':True},{'has_audio':1}):
            with self.subTest(changed=changed),self.assertRaises(ValueError):plan(probe=info(**changed))
        with self.assertRaises(ValueError):plan(b={'media_id':'m'})
        p=project();p['media']['m']['interpret_fps']=24
        r=plan(p,info(frame_rate='bad'));self.assertFalse(r['ok']);self.assertFalse(r['ops'])
        p=project();p['sequences'][0]['tracks'][0]['clips'][0]['reverse']=1
        with self.assertRaises(ValueError):plan(p)

    def test_copy_amplification_refuses_before_first_media_deepcopy(self):
        p=project()
        for i in range(100):child(p,str(i),duration=2)
        replacement=info(audio_streams=[{'metadata':'x'*400000}])
        with patch.object(relink.copy,'deepcopy',side_effect=AssertionError('must not clone before budget')):
            with self.assertRaisesRegex(ValueError,'copy budget'):plan(p,replacement)

    def test_custom_input_options_refuse_uninspected_decoder_clock(self):
        p=project();p['media']['m']['input_opts']=['-itsoffset','10']
        with self.assertRaisesRegex(ValueError,'Custom decoder input options'):plan(p)

    def test_relative_and_windows_drive_relative_replacements_refuse_without_mutation(self):
        paths = ['relative.mkv', '../outside.mkv']
        if sys.platform == 'win32': paths += ['/drive-relative.mkv', 'C:drive-relative.mkv']
        for path in paths:
            project_before = project(); untouched = copy.deepcopy(project_before)
            with self.subTest(path=path), self.assertRaises(ValueError): plan(project_before, path=path)
            self.assertEqual(project_before, untouched)

    def test_actual_apply_ops_escapes_slash_tilde_ids_without_touching_encoded_collision(self):
        from test_project_sync import ProjectStoreFixture
        f=ProjectStoreFixture('runTest');f.setUp()
        try:
            p=project();media=p['media'].pop('m');mid='camera/one~raw';media['id']=mid;p['media'][mid]=media
            p['media']['camera~1one~0raw']={**copy.deepcopy(media),'id':'camera~1one~0raw','note':'Encoded-key collision must stay unchanged'}
            p['sequences'][0]['tracks'][0]['clips'][0]['media_id']=mid
            r=plan(p,b=body(mid));self.assertEqual(r['ops'][0]['path'],'/media/camera~1one~0raw')
            saved=copy.deepcopy(p);f.env['apply_ops'](saved,r['ops'])
            self.assertEqual(saved['media'][mid]['path'],REPLACEMENT)
            self.assertEqual(saved['media']['camera~1one~0raw'],p['media']['camera~1one~0raw'])
            self.assertEqual(saved['sequences'],p['sequences'])
        finally:f.doCleanups();f.tearDown()

    def test_real_pcm_matches_manual_path_relink_with_retime_fades_gain_automation(self):
        with tempfile.TemporaryDirectory(prefix='filmocity-source-relink-') as folder:
            folder=Path(folder)
            for name,hz in [('old',300),('new',700)]:
                values=array.array('h')
                import math
                for sample in range(3*48000):
                    value=int(10000*math.sin(2*math.pi*hz*sample/48000));values.extend((value,-value))
                if sys.byteorder!='little':values.byteswap()
                with wave.open(str(folder/(name+'.wav')),'wb') as stream:stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(values.tobytes())
            p=project();old=folder/'old.wav';new=folder/'new.wav'
            p['sequences'][0]['fps']=float(Fraction(30000,1001))
            p['media']['m'].update(path=str(old),**info(duration=3,rate=None,has_video=False,width=0,height=0,codec='pcm_s16le',vcodec=None))
            p['media']['m'].pop('input_transform');p['media']['m'].pop('input_transform_resource')
            tr=p['sequences'][0]['tracks'][0];tr['kind']='audio';c=tr['clips'][0]
            c.update(start=0,in_=.25,out=2.25,speed=2,reverse=True,audio={'gain_db':-.005,'fade_in':.1,'fade_out':.15,'maintain_pitch':False},keyframes={'audio.gain_db':[{'t':0,'v':-3},{'t':1,'v':-6}]})
            probe=info(duration=3,rate=None,has_video=False,width=0,height=0,codec='pcm_s16le',vcodec=None)
            st=new.stat();r=plan(p,probe,path=str(new),stamp=[[str(new),st.st_size,st.st_mtime_ns,st.st_ino]])
            self.assertTrue(r['ok']);actual=apply(p,r);expected=copy.deepcopy(p);expected['media']['m']['path']=str(new)
            outputs=[]
            for name,document in [('actual',actual),('expected',expected),('before',p)]:
                output=folder/(name+'.wav')
                with RenderContext(scratch_parent=str(folder)) as context:render.render(document,'s',str(output),{'format':'audio','acodec':'wav'},context=context)
                with wave.open(str(output),'rb') as stream:
                    self.assertEqual(stream.getnframes(),48000);outputs.append(stream.readframes(stream.getnframes()))
            self.assertEqual(outputs[0],outputs[1]);self.assertNotEqual(outputs[0],outputs[2])


if __name__=='__main__':unittest.main()
