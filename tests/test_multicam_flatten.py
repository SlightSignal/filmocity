"""Pure direct-source Flatten clocks, processing boundaries and atomic plans."""
import copy
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import multicam_flatten as flat
from render import clip_dur


def project():
    media={'id':'m','path':'/offline/camera.mkv','duration':12,'fps':30,'frame_rate':'30/1','width':64,'height':48,'has_video':True,'has_audio':True,'channels':2}
    inner={'id':'inner','media_id':'m','start':0,'in_':1,'out':5,'speed':2,'audio':{'gain_db':0}}
    outer={'id':'outer','sequence_id':'mc','multicam_angle':0,'start':1,'in_':0,'out':2,'speed':1,'audio':{'gain_db':-6,'fade_in':.25,'fade_out':.25},'note':'Keep complete authored metadata','markers':[{'t':.5,'name':'mark'}]}
    seq={'id':'s','name':'Parent','width':64,'height':48,'fps':30,'master':{'gain_db':-3},'tracks':[{'id':'v','kind':'video','index':0,'clips':[outer]},{'id':'a','kind':'audio','index':0,'clips':[],'gain_db':2}]}
    child={'id':'mc','name':'Camera','width':64,'height':48,'fps':30,'multicam':True,'multicam_audio':'follow','master':{'gain_db':1},'tracks':[{'id':'camera','kind':'video','index':0,'clips':[inner]},{'id':'sound','kind':'audio','index':0,'clips':[],'gain_db':3}]}
    return {'media':{'m':media},'sequences':[seq,child]}

def request(**kw):return {'_context':{'workspace':'w','project':'p','revision':'r'},'sequence':'s','clip_ids':['outer'],**kw}

def planned(p=None,**kw):return flat.plan(p or project(),request(**kw))

class FlattenPlan(unittest.TestCase):
    def test_direct_speed_preserves_outer_envelope_metadata_and_originals(self):
        p=project();before=copy.deepcopy(p);r=planned(p);self.assertTrue(r['ok'],r['issues']);self.assertEqual(p,before)
        seq=r['ops'][0]['value'];picture=seq['tracks'][0]['clips'][0];sound=seq['tracks'][-1]['clips'][0]
        self.assertEqual((picture['media_id'],picture['in_'],picture['out'],picture['speed']),('m',1,5,2));self.assertEqual(clip_dur(picture),2)
        self.assertIsNone(picture['sequence_id']);self.assertFalse(picture['audio']['linked']);self.assertEqual(picture['note'],before['sequences'][0]['tracks'][0]['clips'][0]['note'])
        self.assertEqual(sound['audio']['gain_db'],-2);self.assertEqual(sound['audio']['fade_in'],.25);self.assertEqual(seq['tracks'][-1]['gain_db'],2)
        self.assertNotIn('duration',seq);self.assertEqual(len(r['ops']),1);self.assertEqual(r['summary']['resolved'][0]['audio_mode'],'follow');self.assertEqual(planned(p)['fingerprint'],r['fingerprint'])

    def test_multiple_edits_gap_and_tail_preserve_duration_and_unique_ids(self):
        p=project();child=p['sequences'][1];base=child['tracks'][0]['clips'][0];base.update(in_=0,out=1,speed=1)
        child['tracks'][0]['clips'].append({**copy.deepcopy(base),'id':'later','start':2,'in_':4,'out':5});child['duration']=4
        p['sequences'][0]['tracks'][0]['clips'][0].update(in_=0,out=4)
        r=planned(p);self.assertTrue(r['ok'],r['issues']);seq=r['ops'][0]['value'];self.assertEqual(seq['duration'],5);self.assertIn('explicit sequence duration for trailing gap',r['summary']['affected_fields']);self.assertTrue(any('duration is retained explicitly' in w for w in r['summary']['warnings']))
        self.assertEqual([(c['start'],c['in_'],c['out']) for c in seq['tracks'][0]['clips']],[(1,0,1),(3,4,5)])
        ids=r['summary']['replacement_clip_ids'];self.assertEqual(len(ids),len(set(ids)))

    def test_fixed_audio_is_resolved_independently_of_picture_gaps(self):
        p=project();child=p['sequences'][1];child['multicam_audio']='fixed';child['multicam_audio_track']='sound'
        child['tracks'][0]['clips'][0]['audio']['linked']=False;child['tracks'][1]['clips']=[{'id':'fixed','media_id':'m','start':0,'in_':6,'out':8,'speed':1}]
        r=planned(p);self.assertTrue(r['ok'],r['issues']);sound=r['ops'][0]['value']['tracks'][-1]['clips'][0];self.assertEqual((sound['in_'],sound['out']),(6,8));self.assertEqual(r['summary']['resolved'][0]['audio_mode'],'fixed')

    def test_locked_track_and_locked_routed_bus_refuse_without_ops(self):
        for index in (0,1):
            p=project();p['sequences'][0]['tracks'][index]['locked']=True;r=planned(p);self.assertFalse(r['ok']);self.assertEqual(r['ops'],[])

    def test_variable_ramp_and_intermediate_cfr_refuse(self):
        for mode in ('rate','ramp','reverse','offgrid'):
            p=project();inner=p['sequences'][1]['tracks'][0]['clips'][0];outer=p['sequences'][0]['tracks'][0]['clips'][0]
            if mode=='rate':p['sequences'][1]['fps']=15
            elif mode=='ramp':inner['time_remap']=[{'t':0,'v':1},{'t':1,'v':3}]
            elif mode=='reverse':outer['reverse']=True
            else:outer['in_']=.015
            r=planned(p);self.assertFalse(r['ok'],mode);self.assertTrue(any(i['code']=='source_sampling' for i in r['issues']),r['issues'])

    def test_inner_processing_and_canvas_stage_refuse(self):
        for mode in ('transform','canvas','transition'):
            p=project()
            if mode=='transform':p['sequences'][1]['tracks'][0]['clips'][0]['transform']={'scale':.5}
            elif mode=='canvas':p['sequences'][1]['width']=128
            else:p['sequences'][0]['tracks'][0]['clips'][0]['transition_in']={'duration':.5}
            r=planned(p);self.assertFalse(r['ok']);self.assertFalse(r['ops'])

    def test_unit_effective_interpretation_reverse_and_hold(self):
        p=project();p['media']['m']['interpret_fps']='15/1';outer=p['sequences'][0]['tracks'][0]['clips'][0];outer['reverse']=True
        r=planned(p);self.assertTrue(r['ok'],r['issues']);self.assertTrue(r['ops'][0]['value']['tracks'][0]['clips'][0]['reverse'])
        outer.update(hold=True,in_=1,out=3);r=planned(p);self.assertTrue(r['ok'],r['issues']);self.assertEqual(len(r['summary']['added_tracks']),0)
        c=r['ops'][0]['value']['tracks'][0]['clips'][0];self.assertTrue(c['hold']);self.assertEqual(c['in_'],3);self.assertEqual(clip_dur(c),2)

    def test_complete_parent_processed_bus_preserved_partial_refused(self):
        p=project();p['sequences'][0]['tracks'][1]['audio_fx']={'comp':{'enabled':True}};r=planned(p);self.assertTrue(r['ok'],r['issues'])
        self.assertEqual(r['ops'][0]['value']['tracks'][-1]['audio_fx'],p['sequences'][0]['tracks'][1]['audio_fx'])
        p['sequences'][0]['tracks'][1]['clips']=[{'id':'peer','media_id':'m','start':9,'in_':0,'out':1,'speed':1}]
        r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='audio_bus_boundary' for i in r['issues']))

    def test_complete_child_bus_effect_preserved_only_without_stage_crossing(self):
        p=project();outer=p['sequences'][0]['tracks'][0]['clips'][0];outer['audio']={'gain_db':-6}
        p['sequences'][1]['tracks'][1]['audio_fx']={'comp':{'enabled':True}};r=planned(p);self.assertTrue(r['ok'],r['issues'])
        self.assertTrue(r['ops'][0]['value']['tracks'][-1]['audio_fx']['comp']['enabled']);self.assertEqual(r['ops'][0]['value']['tracks'][-1]['gain_db'],0)
        outer['audio']['fade_in']=.2;r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='child_audio_processing' for i in r['issues']))

    def test_group_and_detached_boundary_refuse(self):
        p=project();outer=p['sequences'][0]['tracks'][0]['clips'][0];outer['group']='g'
        p['sequences'][0]['tracks'][0]['clips'].append({'id':'peer','media_id':'m','group':'g','start':5,'in_':0,'out':1})
        r=planned(p);self.assertFalse(r['ok']);self.assertEqual(r['issues'][0]['code'],'group_boundary')
        p=project();p['sequences'][0]['tracks'][0]['clips'][0]['audio_detached_id']='audio';self.assertFalse(planned(p)['ok'])

    def test_old_ramp_restoration_cleared_outer_automation_rebased(self):
        p=project();outer=p['sequences'][0]['tracks'][0]['clips'][0];outer.update(time_remap=[{'t':0,'v':1}],source_edit_window={'version':1,'ramp':{'points':[{'t':0,'v':1}],'offset':0}},keyframes={'transform.x':[{'t':0,'v':0,'e':'bezier'},{'t':2,'v':20}],'audio.gain_db':[{'t':0,'v':-6},{'t':2,'v':0}]})
        r=planned(p);self.assertTrue(r['ok'],r['issues']);clips=[c for t in r['ops'][0]['value']['tracks'] for c in t['clips']]
        self.assertTrue(all(not c.get('source_edit_window',{}).get('ramp') for c in clips));self.assertIsNone(clips[0]['time_remap']);self.assertEqual(clips[-1]['keyframes']['audio.gain_db'][0]['v'],-2)

    def test_parent_adjustment_and_ordered_audio_channel_stages_refuse(self):
        p=project();p['sequences'][0]['tracks'][0]['clips'][0]['adjustment']=True;self.assertFalse(planned(p)['ok'])
        for mode in ('pan','channels','media'):
            p=project();outer=p['sequences'][0]['tracks'][0]['clips'][0]
            if mode=='pan':outer['audio']['pan']=.8
            else:outer['audio']['channels']='right'
            if mode=='media':
                p['sequences'][1]['tracks'][0]['clips'][0]['speed']=1;p['media']['m']['channel_mode']='left'
            r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='audio_channel_stages' for i in r['issues']))

    def test_actual_legacy_inner_effects_and_outer_temporal_state_refuse(self):
        p=project();p['sequences'][1]['tracks'][0]['clips'][0]['effects']={'blur':4}
        r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='inner_picture_processing' for i in r['issues']))
        for kind in ('echo','posterize_time','replicate','timecode','stabilize','add_noise'):
            p=project();p['sequences'][0]['tracks'][0]['clips'][0]['fx_stack']=[{'type':kind}]
            r=planned(p);self.assertFalse(r['ok'],kind);self.assertTrue(any(i['code']=='outer_picture_processing' for i in r['issues']))
            p['sequences'][0]['tracks'][0]['clips'][0]['fx_stack'][0]['enabled']=False;self.assertTrue(planned(p)['ok'])
        p=project();p['sequences'][0]['tracks'][0]['clips'][0]['time_interpolation']='optical_flow';self.assertFalse(planned(p)['ok'])

    def test_gap_generated_canvas_refuses_but_continuous_stateless_grade_retained(self):
        p=project();outer=p['sequences'][0]['tracks'][0]['clips'][0];outer['color']={'exposure':.3};outer['transform']={'x':2,'scale':.9}
        r=planned(p);self.assertTrue(r['ok'],r['issues']);self.assertEqual(r['ops'][0]['value']['tracks'][0]['clips'][0]['color'],outer['color'])
        p['sequences'][1]['duration']=3;outer['out']=3;outer['fx_stack']=[{'type':'grid'}]
        r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='gap_picture_processing' for i in r['issues']))
        outer['fx_stack']=[];outer['color']={};outer['mask']={'type':'rect','x':.1,'y':.1,'w':.5,'h':.5};self.assertFalse(planned(p)['ok'])

    def test_cropped_audio_stretch_or_resampler_phase_is_explicitly_refused(self):
        for maintain_pitch in (True,False):
            p=project();p['sequences'][1]['tracks'][0]['clips'][0]['audio']['maintain_pitch']=maintain_pitch
            p['sequences'][0]['tracks'][0]['clips'][0].update(in_=.5,out=1.5)
            r=planned(p);self.assertFalse(r['ok']);self.assertFalse(r['ops']);self.assertTrue(any(i['code']=='audio_tempo_crop' for i in r['issues']))
        p=project();p['sequences'][1]['tracks'][0]['clips'][0]['time_remap']=[{'t':0,'v':2},{'t':1,'v':2}]
        r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='audio_tempo_stages' for i in r['issues']))

    def test_audio_overlap_comparisons_share_bounded_work_budget(self):
        p=project();p['sequences'][0]['tracks'][0]['clips'][0].update(in_=0,out=60)
        child=p['sequences'][1];child['tracks'][0]['clips'][0]['audio']['linked']=False
        child['multicam_audio']='fixed';child['multicam_audio_track']='sound';child['duration']=60
        child['tracks'][1]['clips']=[{'id':'audio'+str(i),'media_id':'m','start':i*.04,'in_':0,'out':.04,'speed':1} for i in range(1500)]
        with self.assertRaisesRegex(ValueError,'audio comparison exceeds one million'):planned(p)

    def test_known_variable_source_or_physical_parent_refuses_sampling_claim(self):
        for via_child in (False,True):
            p=project();p['media']['m']['vfr']=True
            if via_child:
                p['media']['sub']={**copy.deepcopy(p['media']['m']),'id':'sub','subclip_of':'m','sub_in':0,'vfr':False}
                p['sequences'][1]['tracks'][0]['clips'][0]['media_id']='sub'
            r=planned(p);self.assertFalse(r['ok']);self.assertFalse(r['ops']);self.assertTrue(any(i['code']=='source_sampling' for i in r['issues']))

    def test_fixed_nonpicture_camera_transition_cannot_shift_sound_silently(self):
        p=project();child=p['sequences'][1];child['multicam_audio']='fixed';child['multicam_audio_track']='sound'
        child['tracks'][0]['clips'][0]['audio']['linked']=False
        child['tracks'][1]['clips']=[{'id':'fixed','media_id':'m','start':.5,'in_':2,'out':3.5,'transition_in':{'duration':.5,'align':'end'}}]
        r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='audio_transition_stage' for i in r['issues']))

    def test_outer_fit_and_overlapping_parent_edit_refuse(self):
        p=project();p['sequences'][0]['tracks'][0]['clips'][0]['fit']='cover';r=planned(p)
        self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='outer_picture_fit' for i in r['issues']))
        p=project();p['sequences'][0]['tracks'][0]['clips'].append({'id':'peer','media_id':'m','start':2,'in_':0,'out':1})
        r=planned(p);self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='outer_overlap' for i in r['issues']))

    def test_fragment_metadata_budget_refuses_before_repeated_copy(self):
        p=project();outer=p['sequences'][0]['tracks'][0]['clips'][0];outer['note']='x'*(1024*1024)
        inner=p['sequences'][1]['tracks'][0]['clips'][0];inner.update(in_=0,out=.1,speed=1)
        p['sequences'][1]['tracks'][0]['clips']=[{**copy.deepcopy(inner),'id':'part'+str(i),'start':i*.1} for i in range(20)]
        with self.assertRaisesRegex(ValueError,'32 MiB'):planned(p)

    def test_invalid_request_bounds_fingerprint_and_finite_json(self):
        for change in ({'clip_ids':[]},{'clip_ids':['outer']*101},{'clip_ids':['outer','outer']},{'_context':None}):
            with self.assertRaises(ValueError):planned(**change)
        p=project();p['sequences'][0]['tracks'][0]['clips'][0]['start']=float('nan')
        with self.assertRaises(ValueError):planned(p)
        r=planned();p=project();p['sequences'][0]['master']['gain_db']=0;self.assertNotEqual(r['fingerprint'],planned(p)['fingerprint'])

if __name__=='__main__':unittest.main()
