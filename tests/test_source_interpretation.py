"""Pure interpretation contracts; no project/file mutation in planning."""
import copy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from source_interpretation import plan
from timeline_time import interpretation_factor

CONTEXT={'workspace':'w','project':'p','revision':'r'}
def project():
    return {'media':{'m':{'id':'m','name':'Source','path':'/source.mkv','fps':30,'frame_rate':'30/1','duration':6,
        'has_video':True,'has_audio':True,'channels':2,'sample_rate':8000,'ingest_token':'old','status':'ready','proxy':'/old.mp4'}},
        'sequences':[{'id':'s','tracks':[{'id':'v','kind':'video','clips':[{'id':'c','media_id':'m','start':0,'in_':0,'out':2,'speed':1}]}]}]}
def child(p,identity='sub',*,channel=False,fullmix=False):
    m={**copy.deepcopy(p['media']['m']),'id':identity,'subclip_of':'m','sub_in':1,'duration':2,'native_duration':2}
    if channel or fullmix:
        m.update(has_video=False,audio_alias={'version':1,'source_media_id':'m','physical_media_id':'m'})
        if channel:m['audio_alias']['channel_index']=0
    p['media'][identity]=m;return m
def run(p,media_id='m',fps=24,**kw):return plan(p,{'_context':CONTEXT,'media_id':media_id,'fps':fps,**kw})

class InterpretationPlans(unittest.TestCase):
    def test_selected_channel_native_window_preserved_with_independent_parent(self):
        p=project();c=child(p,'channel',channel=True);c['markers']=[{'t':.5,'duration':1}];before=copy.deepcopy(p)
        r=run(p,'channel',60);m=r['media']['channel']
        self.assertTrue(r['ok']);self.assertEqual((m['sub_in'],m['duration'],m['interpret_fps']),(.5,1,'60/1'))
        self.assertEqual(m['markers'],[{'t':.25,'duration':.5}]);self.assertEqual(p,before)
        self.assertEqual(r['affected_media_ids'],['channel']);self.assertEqual(r['prepare_ids'],['channel'])

    def test_parent_group_requires_consent_and_keeps_independent_child_rates(self):
        p=project();child(p,'mix',fullmix=True);sub=child(p);ch=child(p,'channel',channel=True)
        sub.update(interpret_fps=24,duration=2.5);ch.update(interpret_fps=15,duration=4)
        r=run(p);self.assertFalse(r['ok']);self.assertEqual(r['summary']['required_fullmix_ids'],['mix']);self.assertFalse(r['ops'])
        r=run(p,include_fullmix=True);self.assertTrue(r['ok']);self.assertEqual(r['media']['mix']['sub_in'],1.25)
        for mid in ('sub','channel'):
            for key in ('interpret_fps','sub_in','duration'):self.assertEqual(r['media'][mid][key],p['media'][mid][key])
            self.assertNotEqual(r['media'][mid]['ingest_token'],p['media'][mid]['ingest_token'])
        self.assertEqual(set(r['prepare_ids']),{'m','mix','channel'})

    def test_fullmix_alias_change_refuses_and_unchanged_setting_is_noop(self):
        p=project();child(p,'mix',fullmix=True)
        r=run(p,'mix',24,include_fullmix=True);self.assertFalse(r['ok']);self.assertIn('interpret_parent_group',[x['code'] for x in r['issues']])
        r=run(p,'mix',None);self.assertTrue(r['ok']);self.assertFalse(r['ops']);self.assertFalse(r['prepare_ids'])
        r=run(p,'mix','30/1');self.assertTrue(r['ok']);self.assertFalse(r['ops']);self.assertNotIn('interpret_fps',r['media']['mix'])

    def test_invalid_locked_timeline_range_rejects_without_mutating(self):
        p=project();p['sequences'][0]['tracks'][0].update(locked=True);p['sequences'][0]['tracks'][0]['clips'][0]['out']=6
        before=copy.deepcopy(p);r=run(p,fps=60)
        self.assertFalse(r['ok']);self.assertEqual(r['issues'][0]['code'],'clip_range_outside');self.assertTrue(r['summary']['uses'][0]['locked']);self.assertEqual(p,before)

    def test_hold_out_is_duration_but_held_frame_must_remain_inside(self):
        p=project();c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(hold=True,in_=2,out=100)
        r=run(p,fps=60);self.assertTrue(r['ok']);self.assertEqual(r['summary']['uses'][0]['duration'],98)
        c['in_']=3;r=run(p,fps=60);self.assertFalse(r['ok']);self.assertEqual(r['issues'][0]['code'],'held_frame_outside')

    def test_ramp_reverse_payload_and_duration_never_rewritten(self):
        p=project();c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(reverse=True,time_remap=[{'t':0,'v':1},{'t':1,'v':3}],note='Keep')
        before=copy.deepcopy(p);r=run(p);self.assertTrue(r['ok']);self.assertEqual(p,before)
        self.assertTrue(all(op['path'].startswith('/media/') for op in r['ops']))

    def test_recognized_marker_clocks_scale_and_opaque_markers_are_disclosed(self):
        p=project();p['media']['m']['markers']=[{'start':1,'end':2,'duration':1},{'t':2,'time':2},{'time':2,'clock':'opaque'}]
        r=run(p);self.assertEqual(r['media']['m']['markers'],[{'start':1.25,'end':2.5,'duration':1.25},{'t':2.5,'time':2.5},{'time':2,'clock':'opaque'}])
        self.assertTrue(any('unknown clock' in x for x in r['summary']['warnings']))

    def test_native_restore_roundtrip_and_noop_preserve_existing_previews(self):
        p=project();sub=child(p);r=run(p,'sub',24);p['media']['sub']=r['media']['sub']
        r=run(p,'sub','24/1');self.assertFalse(r['ops']);self.assertFalse(r['prepare_ids']);self.assertEqual(r['media']['sub'],p['media']['sub'])
        r=run(p,'sub',None);m=r['media']['sub'];self.assertEqual((m['sub_in'],m['duration']),(1,2));self.assertNotIn('interpret_fps',m)

    def test_exact_fraction_storage_decimal_alias_and_semantic_noop(self):
        p=project();r=run(p,fps='1000/41');self.assertEqual(r['media']['m']['interpret_fps'],'1000/41')
        self.assertEqual(r['media']['m']['duration'],6*30/(1000/41))
        p['media']['m'].update(interpret_fps=23.976,duration=6*30/(24000/1001))
        r=run(p,fps='24000/1001');self.assertFalse(r['ops']);self.assertEqual(r['settings']['fps'],'24000/1001')

    def test_invalid_rates_consent_and_missing_explicit_fps_refuse(self):
        p=project()
        for value in (True,False,0,-1,1001,'24garbage','1/0','','nan','1e2',float('nan'),float('inf')):
            with self.subTest(value=value),self.assertRaises(ValueError):run(p,fps=value)
        with self.assertRaises(ValueError):plan(p,{'_context':CONTEXT,'media_id':'m'})
        with self.assertRaises(ValueError):run(p,include_fullmix='true')

    def test_known_vfr_stills_generated_audio_only_and_custom_inputs_refuse(self):
        for fields in ({'vfr':True},{'is_image':True},{'synthetic':{'kind':'color'}},{'has_video':False},{'input_opts':['-ss','1']}):
            p=project();p['media']['m'].update(fields)
            with self.subTest(fields=fields),self.assertRaises(ValueError):run(p)
        p=project();p['media']['m'].update(sequence_frames=180,input_opts=['-framerate','30','-start_number','1'])
        self.assertTrue(run(p)['ok']);p['media']['m']['sequence_frames']=10001
        with self.assertRaises(ValueError):run(p)

    def test_numbered_source_cannot_hide_custom_decoder_options_or_wrong_clock(self):
        for options in (['-framerate','24','-start_number','1'],['-framerate','30','-start_number','1','-ss','3'],['-framerate','30','-start_number','-1']):
            p=project();p['media']['m'].update(sequence_frames=180,input_opts=options)
            with self.subTest(options=options),self.assertRaises(ValueError):run(p)
        p=project();p['media']['m'].update(sequence_frames=180,input_opts=['-framerate','30','-start_number','1'],duration=7)
        with self.assertRaises(ValueError):run(p)

    def test_native_metadata_mismatch_and_existing_native_overrun_refuse(self):
        p=project();c=child(p);c['frame_rate']='24/1'
        with self.assertRaisesRegex(ValueError,'native frame-rate'):run(p,'sub')
        c['frame_rate']='30/1';c['duration']=6
        self.assertFalse(run(p,'sub')['ok'])

    def test_escaped_id_paths_and_fingerprint_changes_with_source_basis(self):
        p=project();p['media']['a/b~c']=p['media'].pop('m');p['media']['a/b~c']['id']='a/b~c';p['sequences']=[]
        a=run(p,'a/b~c');self.assertEqual(a['ops'][0]['path'],'/media/a~1b~0c')
        p['media']['a/b~c']['note']='Changed';self.assertNotEqual(a['fingerprint'],run(p,'a/b~c')['fingerprint'])

    def test_offline_plan_is_explicit_and_has_no_preparation(self):
        r=plan(project(),{'_context':CONTEXT,'media_id':'m','fps':24},offline=True)
        self.assertTrue(r['ok']);self.assertTrue(r['ops']);self.assertFalse(r['prepare_ids']);self.assertTrue(any('offline' in w for w in r['summary']['warnings']))

if __name__=='__main__':unittest.main()
