"""Pure Nest dependency, full-payload and bounded deterministic-plan regressions."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import sequence_nesting as nesting


def project():
    return {'id':'p','media':{'m':{'id':'m','path':'offline.mov','duration':30,'frame_rate':'30/1','fps':30,'has_video':True,'has_audio':True}},
        'sequences':[{'id':'s','name':'Main','width':64,'height':48,'fps':'30/1','markers':[{'id':'mark','time':1,'name':'Keep'}],
            'captions':[{'id':'caption','start':0,'end':2,'text':'Keep'}],'master':{'gain_db':-3,'audio_fx':{'limiter':True}},
            'tracks':[{'id':'v','kind':'video','index':0,'clips':[clip('a',0,2)]},
                      {'id':'a','kind':'audio','index':0,'gain_db':6,'clips':[]}]}]}


def clip(cid,start,end):
    return {'id':cid,'media_id':'m','start':start,'in_':0,'out':end-start,'speed':1,'audio':{'gain_db':-4,'fade_in':.2},'note':'Preserve'}


def body(ids=None,**kw):
    return {'_context':{'workspace':'w','project':'p','revision':'r'},'sequence':'s','clip_ids':ids or ['a'],'name':'Nested',**kw}


class Plans(unittest.TestCase):
    def test_deterministic_complete_payload_move_and_neutral_parent_mix(self):
        p=project();c=p['sequences'][0]['tracks'][0]['clips'][0]
        c.update(in_=2,out=6,speed=2,reverse=True,time_remap=[{'t':0,'v':2,'e':'linear'}],markers=[{'t':.2,'name':'Source'}],
            keyframes={'audio.gain_db':[{'t':0,'v':-4,'e':'bezier','cp1':[.2,.1],'cp2':[.8,.9]}]},source_edit_window={'ramp':{'offset':1,'points':[]}})
        old=copy.deepcopy(p);a=nesting.plan(p,body());b=nesting.plan(p,body());self.assertTrue(a['ok'],a['issues']);self.assertEqual(a,b);self.assertEqual(p,old)
        parent=a['ops'][0]['value'];child=a['ops'][1]['value'];moved=child['tracks'][0]['clips'][0]
        self.assertEqual(moved,c);self.assertEqual(child['tracks'][1]['gain_db'],6);self.assertEqual(child['master'],{'gain_db':0})
        self.assertEqual(parent['master'],old['sequences'][0]['master']);self.assertFalse(parent['tracks'][0]['clips'][0]['audio']['linked'])
        self.assertEqual(parent['tracks'][-1]['gain_db'],0);self.assertEqual(parent['tracks'][-1]['audio_fx'],{})
        self.assertEqual(parent['captions'],old['sequences'][0]['captions']);self.assertEqual(child['captions'],[])
        self.assertNotEqual(child['tracks'][0]['id'],'v');self.assertEqual(parent['tracks'][0]['clips'][0]['group'],parent['tracks'][-1]['clips'][0]['group'])

    def test_locked_selection_and_locked_routed_bus_return_review_issues(self):
        for index in (0,1):
            p=project();p['sequences'][0]['tracks'][index]['locked']=True;r=nesting.plan(p,body());self.assertFalse(r['ok']);self.assertFalse(r['ops'])
            self.assertTrue(any('locked' in i['code'] for i in r['issues']))

    def test_link_group_and_detached_boundary_are_explicit_refusals(self):
        for field,peer in [('group','g'),('audio_detached_id','b')]:
            p=project();a=p['sequences'][0]['tracks'][0]['clips'][0];b=clip('b',3,4);a[field]=peer
            if field=='group':b['group']='g'
            else:b['unlinked_from']='a'
            p['sequences'][0]['tracks'][1]['clips']=[b]
            self.assertFalse(nesting.plan(p,body())['ok']);self.assertTrue(nesting.plan(p,body(['a','b']))['ok'])

    def test_partial_processed_bus_refuses_even_nonoverlapping_earlier_input(self):
        p=project();seq=p['sequences'][0];seq['tracks'][1].update(audio_fx={'comp':{'enabled':True}},clips=[clip('prior',8,10)])
        r=nesting.plan(p,body());self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='audio_bus_boundary' for i in r['issues']))
        complete=nesting.plan(p,body(['a','prior']));self.assertTrue(complete['ok'],complete['issues'])
        self.assertEqual(complete['ops'][1]['value']['tracks'][1]['audio_fx'],seq['tracks'][1]['audio_fx'])

    def test_partial_linear_gain_bus_retains_peer_and_existing_routes(self):
        p=project();p['sequences'][0]['tracks'][1]['clips']=[clip('peer',0,2)]
        r=nesting.plan(p,body());self.assertTrue(r['ok'],r['issues']);self.assertEqual(r['ops'][0]['value']['tracks'][1]['clips'],p['sequences'][0]['tracks'][1]['clips'])
        self.assertGreater(r['summary']['added_tracks'][0]['index'],0)

    def test_existing_parent_solo_state_remains_and_neutral_wrapper_is_audible(self):
        p=project();p['sequences'][0]['tracks'][1]['solo']=True;r=nesting.plan(p,body());self.assertTrue(r['ok'],r['issues']);self.assertTrue(r['summary']['added_tracks'][0]['solo'])
        self.assertTrue(r['ops'][1]['value']['tracks'][1]['solo'])

    def test_no_audio_bus_refuses_changed_peer_processing_but_all_selected_supported(self):
        p=project();seq=p['sequences'][0];seq['tracks']=seq['tracks'][:1];seq['tracks'][0]['gain_db']=6;seq['tracks'][0]['clips'].append(clip('b',3,4))
        r=nesting.plan(p,body());self.assertTrue(any(i['code']=='routing_change' for i in r['issues']))
        self.assertTrue(nesting.plan(p,body(['a','b']))['ok'])
        seq['tracks'][0]['gain_db']=0;r=nesting.plan(p,body());self.assertTrue(r['ok'],r['issues']);self.assertTrue(any('fallback' in w for w in r['summary']['warnings']))

    def test_picture_interleaving_refuses_but_unselected_gap_is_preserved(self):
        p=project();seq=p['sequences'][0];seq['tracks'][0]['clips']=[clip('a',0,1),clip('b',2,3),clip('c',4,5)]
        r=nesting.plan(p,body(['a','c']));self.assertTrue(r['ok'],r['issues']);self.assertIn('b',[c['id'] for c in r['ops'][0]['value']['tracks'][0]['clips']])
        seq['tracks'][0]['clips']=[clip('a',0,2)];seq['tracks'] += [{'id':'mid','kind':'video','index':1,'clips':[clip('b',0,2)]},{'id':'top','kind':'video','index':2,'clips':[clip('c',0,2)]}]
        r=nesting.plan(p,body(['a','c']));self.assertTrue(any(i['code']=='picture_order' for i in r['issues']))

    def test_actual_blend_property_requires_lower_picture_dependency(self):
        p=project();seq=p['sequences'][0];seq['tracks'].append({'id':'top','kind':'video','index':1,'clips':[clip('b',0,2)]});seq['tracks'][-1]['clips'][0]['blend']='multiply'
        self.assertTrue(any(i['code']=='compositing_boundary' for i in nesting.plan(p,body(['b']))['issues']))
        self.assertTrue(nesting.plan(p,body(['a','b']))['ok'])

    def test_track_matte_closure_and_reference_remap(self):
        p=project();seq=p['sequences'][0];seq['tracks'].append({'id':'matte','kind':'video','index':1,'muted':True,'clips':[clip('mask',0,2)]})
        seq['tracks'][0]['clips'][0]['fx_stack']=[{'type':'track_matte','params':{'track':'matte','type':'alpha'}}]
        r=nesting.plan(p,body());self.assertTrue(any(i['code']=='matte_boundary' for i in r['issues']))
        r=nesting.plan(p,body(['a','mask']));self.assertTrue(r['ok'],r['issues']);child=r['ops'][1]['value']
        self.assertEqual(child['tracks'][0]['clips'][0]['fx_stack'][0]['params']['track'],child['tracks'][2]['id'])

    def test_incoming_transition_handle_cannot_be_lost(self):
        p=project();c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(start=2,in_=2,out=4,transition_in={'type':'dissolve','duration':.5})
        self.assertTrue(any(i['code']=='transition_boundary' for i in nesting.plan(p,body())['issues']))
        p['sequences'][0]['tracks'][0]['clips'].insert(0,clip('prior',0,2));self.assertTrue(nesting.plan(p,body(['prior','a']))['ok'])

    def test_audio_only_and_fractional_picture_origin(self):
        p=project();seq=p['sequences'][0];seq['fps']='30000/1001';seq['tracks'][0]['clips'][0].update(start=.0501,out=1)
        r=nesting.plan(p,body());self.assertTrue(r['ok'],r['issues']);self.assertAlmostEqual(r['summary']['range']['start'],1001/30000)
        self.assertAlmostEqual(r['ops'][1]['value']['tracks'][0]['clips'][0]['start'],.0501-1001/30000)
        c=seq['tracks'][0]['clips'].pop();seq['tracks'][1]['clips']=[c];r=nesting.plan(p,body());self.assertTrue(r['ok'],r['issues']);self.assertEqual(len(r['summary']['wrapper_clip_ids']),1)
        self.assertLessEqual(r['summary']['range']['start'],.0501);self.assertLess(.0501-r['summary']['range']['start'],1/48000)

    def test_external_live_reference_refuses_while_historical_clock_is_preserved(self):
        p=project();p['sequences'][0]['markers'][0]['clip_id']='a';r=nesting.plan(p,body());self.assertTrue(any(i['code']=='reference_boundary' for i in r['issues']))
        del p['sequences'][0]['markers'][0]['clip_id'];p['sequences'][0]['tracks'][0]['clips'][0]['rendered_from']={'clip_id':'old','sequence':'elsewhere'}
        self.assertTrue(nesting.plan(p,body())['ok'])

    def test_nested_cycle_duplicate_identity_invalid_source_or_clock_fail(self):
        for mutation in (lambda p:p['sequences'].append(copy.deepcopy(p['sequences'][0])),
                         lambda p:p['sequences'][0]['tracks'][0]['clips'][0].update(media_id=None,sequence_id='s'),
                         lambda p:p['sequences'][0]['tracks'][0]['clips'][0].update(speed=float('nan')),
                         lambda p:p['media'].clear()):
            p=project();mutation(p)
            with self.assertRaises((ValueError,KeyError)):nesting.plan(p,body())

    def test_nested_hold_and_moved_external_clip_reference_are_bounded(self):
        p=project();child=copy.deepcopy(p['sequences'][0]);child['id']='child';p['sequences'].append(child)
        c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(media_id=None,sequence_id='child',hold=True,in_=2,out=20)
        self.assertTrue(any(i['code']=='nested_range' for i in nesting.plan(p,body())['issues']))
        c['in_']=1;self.assertTrue(nesting.plan(p,body())['ok'])
        c.pop('sequence_id');c.update(media_id='m',hold=False,in_=0,out=2,custom={'clip_id':'peer'})
        p['sequences'][0]['tracks'][1]['clips']=[clip('peer',3,4)]
        self.assertTrue(any(i['code']=='reference_boundary' for i in nesting.plan(p,body())['issues']))
        c['custom']['sequence']='child';self.assertTrue(nesting.plan(p,body())['ok'])

    def test_dependency_work_and_malformed_effect_containers_fail_visibly(self):
        p=project();seq=p['sequences'][0];seq['tracks'].append({'id':'top','kind':'video','index':2,'clips':[clip('b',0,1),clip('c',2,3)]})
        with patch.object(nesting,'MAX_PAIRS',1):
            with self.assertRaisesRegex(ValueError,'million pairs'):nesting.plan(p,body())
        for field,value in [('audio',[]),('keyframes','broken'),('fx_stack',['wrong'])]:
            p=project();p['sequences'][0]['tracks'][0]['clips'][0][field]=value
            with self.assertRaises(ValueError):nesting.plan(p,body())

    def test_malformed_selection_context_and_bounded_metadata_reject_before_copy(self):
        for change in ({'_context':None},{'clip_ids':['a','a']},{'clip_ids':['missing']},{'name':'\nBAD'},{'clip_ids':['a']*1001}):
            with self.assertRaises(ValueError):nesting.plan(project(),body(**change))
        p=project();p['sequences'][0]['tracks'][0]['clips'][0]['extension']='x'*(33*1024*1024)
        with self.assertRaisesRegex(ValueError,'byte limit'):nesting.plan(p,body())


if __name__=='__main__':unittest.main()
