"""Pure selective attribute transfer; source identities and clocks remain immutable."""
import copy
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import clip_attributes as attrs
from audio_contract import fade_window

CONTEXT={'workspace':'w','project':'p','revision':'r'}
def project():
    return {'id':'p','media':{'m':{'id':'m','name':'Source','path':'source.mov','duration':20,'fps':30,'has_audio':True,'has_video':True}},'sequences':[{'id':'s','name':'Seq','width':1920,'height':1080,'fps':30,'tracks':[{'id':'v','kind':'video','index':0,'clips':[{'id':'target','media_id':'m','start':2,'in_':3,'out':7,'speed':1,'audio':{'linked':False,'channels':'right'},'group':'g','audio_detached_id':'child','note':'keep'}]}]}]}
def request(p=None,**kw):
    p=p or project();clip={'id':'donor','media_id':'m','start':0,'in_':0,'out':2,'speed':1,'transform':{'scale':2},'audio':{'gain_db':-6,'linked':True}}
    return {'_context':copy.deepcopy(CONTEXT),'sequence':'s','clip_ids':['target'],'donor':{'version':1,'clip':clip,'sequence':{'id':'s','width':1920,'height':1080,'fps':30},'context':copy.deepcopy(CONTEXT),'media':copy.deepcopy(p['media']['m'])},'groups':list(attrs.DEFAULT_GROUPS),'include_animation':True,'timing':'seconds',**kw}
def target(p):return p['sequences'][0]['tracks'][0]['clips'][0]
def output(p,b):
    r=attrs.plan(p,b)
    if not r['ok']:raise AssertionError(r['issues'])
    return r['ops'][0]['value'] if r['ops'] else target(p)

class ClipAttributes(unittest.TestCase):
    def test_whole_preserved_clock_routing_and_selective_groups(self):
        p=project();old=copy.deepcopy(p);b=request(p,groups=['motion']);c=target(p);c.update(reverse=True,time_remap=[{'t':0,'v':1}],source_edit_window={'version':1,'ramp':{'offset':3}},markers=[{'t':1,'name':'x'}]);old=copy.deepcopy(p)
        out=output(p,b);expected=copy.deepcopy(c);expected['transform']={'scale':2};self.assertEqual(out,expected);self.assertEqual(p,old)
    def test_absent_empty_existing_audio_consistent_and_link_never_copied(self):
        for audio in (None,{}, {'linked':False,'channels':'right','pan':.5}):
            p=project();c=target(p)
            if audio is None:c.pop('audio')
            else:c['audio']=audio
            b=request(p,groups=['audio_gain']);o=output(p,b);self.assertEqual(o['audio'],{**(audio or {}),'gain_db':-6})
    def test_explicit_controls_preserve_linkage_and_timing(self):
        p=project();b=request(p,groups=['audio_controls']);b['donor']['clip']['audio'].update(channels='left',pan=-.5,maintain_pitch=False);out=output(p,b)
        self.assertEqual(out['audio'],{'linked':False,'channels':'left','pan':-.5,'maintain_pitch':False});self.assertEqual(out['out'],7)
    def test_actual_legacy_and_stack_order_and_blend(self):
        p=project();b=request(p,groups=['video_effects','audio_effects']);s=b['donor']['clip'];s.update(effects={'blur':2},fx_stack=[{'type':'invert'},{'type':'gaussian_blur','params':{'blurriness':4}}],blend='screen',audio_fx={'eq':{'low_db':-3}},afx_stack=[{'type':'amplify','params':{'gain_db':2}}]);o=output(p,b)
        for k in ('effects','fx_stack','blend','audio_fx','afx_stack'):self.assertEqual(o[k],s[k])
    def test_seconds_and_scaled_bezier_preserve_offscreen_anchors(self):
        for mode,factor in [('seconds',1),('scale',2)]:
            p=project();target(p)['keyframes']={'g0.x':[{'t':1,'v':9}],'audio.gain_db':[{'t':0,'v':-8}]};b=request(p,groups=['motion'],timing=mode)
            points=[{'t':-1,'v':0,'e':'bezier','o':[.25,3]},{'t':3,'v':10,'i':[.4,-2]}];b['donor']['clip']['keyframes']={'transform.x':points};o=output(p,b)
            self.assertEqual(o['keyframes']['transform.x'],[dict(x,t=x['t']*factor) for x in points]);self.assertEqual(o['keyframes']['g0.x'],[{'t':1,'v':9}]);self.assertEqual(o['keyframes']['audio.gain_db'],[{'t':0,'v':-8}])
    def test_values_only_keeps_target_curves_and_history(self):
        p=project();target(p).update(keyframes={'audio.duck_db':[{'t':0,'v':0}]},source_edit_window={'version':1,'duck':{'offset':4}});b=request(p,groups=['audio_gain'],include_animation=False);o=output(p,b)
        self.assertEqual(o['keyframes'],target(p)['keyframes']);self.assertEqual(o['source_edit_window'],target(p)['source_edit_window']);self.assertTrue(attrs.plan(p,b)['summary']['warnings'])
    def test_explicit_identical_curve_clears_only_duck_restoration(self):
        p=project();target(p).update(keyframes={'audio.duck_db':[{'t':0,'v':0}]},source_edit_window={'version':1,'duck':{'offset':4},'ramp':{'offset':2}});b=request(p,groups=['audio_gain']);b['donor']['clip']['keyframes']=copy.deepcopy(target(p)['keyframes']);b['donor']['clip']['audio']={}
        o=output(p,b);self.assertEqual(o['source_edit_window'],{'version':1,'ramp':{'offset':2}});self.assertEqual(o['keyframes'],target(p)['keyframes']);self.assertEqual(attrs.plan(p,b)['summary']['changed_clip_ids'],['target'])
    def test_inherited_fades_replace_target_clock_seconds_and_scale(self):
        for mode,ratio in [('seconds',1),('scale',2)]:
            p=project();b=request(p,groups=['audio_fades'],timing=mode);b['donor']['clip']['audio']={'fade_out':2,'constant_power':False,'fade_window':{'duration':10,'offset':8,'settings':[0,2,False,'',0,'',0]}};target(p)['audio'].update(fade_out=2,fade_window={'duration':5,'offset':1,'settings':[0,2,False,'',0,'',0]})
            o=output(p,b);w=fade_window(o,4);self.assertEqual((w['duration'],w['offset']),(10*ratio,8*ratio));self.assertEqual(o['audio']['fade_out'],2*ratio);self.assertFalse(o['audio']['linked'])
    def test_source_history_is_never_imported_and_absent_selected_curves_clear(self):
        p=project();target(p)['keyframes']={'transform.x':[{'t':0,'v':5}],'mask.x':[{'t':0,'v':.2}]};b=request(p,groups=['motion']);b['donor']['clip']['source_edit_window']={'version':1,'duck':{'offset':44}};o=output(p,b)
        self.assertEqual(o['keyframes'],{'mask.x':[{'t':0,'v':.2}]});self.assertNotIn('source_edit_window',o)
    def test_frozen_donor_need_not_exist_and_true_noop(self):
        p=project();b=request(p,groups=['motion']);target(p)['transform']={'scale':2};p['media']={};r=attrs.plan(p,b);self.assertTrue(r['ok']);self.assertEqual(r['ops'],[]);self.assertEqual(r['summary']['changed_clip_ids'],[])
    def test_malformed_and_projected_resource_limits_atomic(self):
        p=project();before=copy.deepcopy(p)
        for update in ({'clip_ids':['target','target']},{'groups':['fake']},{'include_animation':1},{'timing':'stretch'}):
            with self.assertRaises(ValueError):attrs.plan(p,request(p,**update))
        b=request(p);b['donor']['clip']['transform']['scale']=float('nan')
        with self.assertRaises(ValueError):attrs.plan(p,b)
        b=request(p);b['donor']['clip']['fx_stack']=[{'type':'unknown_plugin'}]
        with self.assertRaisesRegex(ValueError,'Unregistered'):attrs.plan(p,b)
        self.assertEqual(p,before)
    def test_locks_all_targets_refuse_without_partial_ops(self):
        p=project();p['sequences'][0]['tracks'][0]['locked']=True;r=attrs.plan(p,request(p));self.assertFalse(r['ok']);self.assertEqual(r['ops'],[])
    def test_live_same_sequence_matte_and_cycle_or_foreign_refusal(self):
        p=project();p['sequences'][0]['tracks'].append({'id':'matte','kind':'video','index':1,'clips':[]});b=request(p,groups=['video_effects']);b['donor']['clip']['fx_stack']=[{'type':'track_matte','params':{'track':'matte','type':'alpha','invert':0}}];r=attrs.plan(p,b);self.assertTrue(r['ok'],r['issues']);self.assertIn('live',r['summary']['warnings'][0])
        b['donor']['clip']['fx_stack'][0]['params']['track']='v';self.assertFalse(attrs.plan(p,b)['ok']);b['donor']['sequence']['id']='other';self.assertEqual(attrs.plan(p,b)['issues'][0]['code'],'matte_dependency')
    def test_lut_identity_binding_and_cross_owner_refusal(self):
        p=project();b=request(p,groups=['color'])
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'grade.cube';path.write_text('TITLE test');b['donor']['clip']['color']={'lut':str(path)};first=attrs.inspect(p,b);path.write_text('TITLE changed');second=attrs.inspect(p,b);self.assertNotEqual(first['fingerprint'],second['fingerprint'])
            b['donor']['context']['project']='other';self.assertFalse(attrs.plan(p,b)['ok'])
    def test_stabilization_requires_exact_physical_source_analysis(self):
        p=project();p['media']['m']['stab_trf']='a.trf';b=request(p,groups=['video_effects']);b['donor']['clip']['fx_stack']=[{'type':'stabilize'}];self.assertTrue(attrs.plan(p,b)['ok']);p['media']['m']['path']='changed.mov';r=attrs.plan(p,b);self.assertFalse(r['ok']);self.assertEqual(r['issues'][0]['code'],'source_analysis')
    def test_large_repeated_copy_and_scaled_clock_limits_refuse_before_publication(self):
        p=project();track=p['sequences'][0]['tracks'][0];track['clips']=[dict(copy.deepcopy(target(p)),id=str(i)) for i in range(100)]
        b=request(p,clip_ids=[str(i) for i in range(100)]);b['donor']['clip']['opaque']='x'*400000
        with self.assertRaisesRegex(ValueError,'copied metadata'):attrs.plan(p,b)
        p=project();b=request(p,groups=['motion'],timing='scale');b['donor']['clip']['out']=1e-8;b['donor']['clip']['keyframes']={'transform.x':[{'t':1e12,'v':0}]}
        with self.assertRaisesRegex(ValueError,'Curve time'):attrs.plan(p,b)

    def test_current_inspector_blur_fill_supported_and_invalid_legacy_values_refuse(self):
        p=project();b=request(p,groups=['motion']);b['donor']['clip'].update(fit='blur_fill',blur_fill_sigma=40)
        self.assertEqual(output(p,b)['fit'],'blur_fill')
        for group,key,value in [('color','color',{'exposure':'nan'}),('video_effects','effects',{'blur':float('inf')}),('audio_effects','audio_fx',{'comp':{'ratio':0}}),('mask','mask',{'w':-1})]:
            b=request(p,groups=[group]);b['donor']['clip'][key]=value
            with self.assertRaises(ValueError):attrs.plan(p,b)
        b=request(p,groups=['audio_fades']);b['donor']['clip']['audio']=None;self.assertTrue(attrs.plan(p,b)['ok'])

    def test_transition_duration_and_ramp_handle_refusal(self):
        p=project();b=request(p,groups=['picture_transitions']);b['donor']['clip']['transition_in']={'type':'dissolve','duration':6};self.assertFalse(attrs.plan(p,b)['ok']);b['donor']['clip']['transition_in']['duration']=1;target(p)['reverse']=True;self.assertFalse(attrs.plan(p,b)['ok']);b['donor']['clip']['transition_in']['align']='start';self.assertTrue(attrs.plan(p,b)['ok'])

if __name__=='__main__':unittest.main()
