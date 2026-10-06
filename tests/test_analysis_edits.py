"""Authoritative reviewed plans, evaluated against production source/curve clocks."""
import copy
import json
import math
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'backend'))
import analysis_edits as edits
from overlap_normalization import _source_offset
from render import clip_dur, kf_eval
from audio_contract import fade_window


def fixture(**changes):
    clip = {'id':'clip', 'media_id':'m', 'start':10., 'in_':2., 'out':10., 'speed':1., **changes}
    sequence = {'id':'seq', 'fps':'30000/1001', 'tracks':[{'id':'V1','kind':'video','clips':[clip]}]}
    return {'media':{'m':{'duration':100,'has_video':True,'has_audio':True}},'sequences':[sequence]}, clip


def run(project, mode, values, identity='task-one'):
    seq = project['sequences'][0]; clip = seq['tracks'][0]['clips'][0]
    payload = {'mode':mode,'media_id':'m','sequence':'seq','clip_id':'clip','range':{'start':clip['in_'],'end':clip['out']}}
    result = {'version':1,'kind':mode,'clock':'media',**{k:v for k,v in payload.items() if k != 'mode'}, 'cuts' if mode == 'scenes' else 'silences':values}
    return edits.plan(project,payload,result,identity)


def apply(project, plan):
    p = copy.deepcopy(project)
    for op in plan['ops']:
        obj=p; parts=op['path'].strip('/').split('/')
        for key in parts[:-1]: obj=obj[int(key)] if isinstance(obj,list) else obj[key]
        obj[parts[-1]]=copy.deepcopy(op['value'])
    return p


def source(clip,t):
    return clip['out']-_source_offset(clip,t) if clip.get('reverse') else clip['in_']+_source_offset(clip,t)


class AnalysisPlans(unittest.TestCase):
    def test_reverse_scene_source_time_maps_to_the_correct_timeline_frame(self):
        p,c=fixture(reverse=True);p['sequences'][0]['fps']=30
        plan=run(p,'scenes',[4]);self.assertEqual(plan['summary']['cuts'],[16]);self.assertEqual([x['start'] for x in apply(p,plan)['sequences'][0]['tracks'][0]['clips']],[10,16])

    def test_ramped_scene_fragments_preserve_source_curves_and_signed_marker_ownership(self):
        for reverse in (False,True):
            p,c=fixture(reverse=reverse,time_remap=[{'t':0,'v':.5},{'t':2,'v':2},{'t':5,'v':1}],keyframes={'transform.x':[{'t':0,'v':0,'e':'ease'},{'t':8,'v':100}]},audio={'fade_in':3,'fade_out':4},markers=[{'t':-1,'name':'before'},{'t':100,'name':'after'}])
            plan=run(p,'scenes',[4,6]);pieces=apply(p,plan)['sequences'][0]['tracks'][0]['clips']
            self.assertEqual(len(pieces),3)
            self.assertEqual(pieces[0]['markers'][0]['name'],'before');self.assertEqual(pieces[-1]['markers'][-1]['name'],'after')
            for piece in pieces:
                offset=piece['start']-c['start']
                for local in (0,clip_dur(piece)/3,clip_dur(piece)*.9):
                    self.assertAlmostEqual(source(piece,local),source(c,offset+local),places=8)
                    self.assertAlmostEqual(kf_eval(piece['keyframes']['transform.x'],local),kf_eval(c['keyframes']['transform.x'],offset+local),places=7)
                self.assertAlmostEqual(fade_window(piece,clip_dur(piece))['offset'],offset)

    def test_scene_later_transition_failure_leaves_input_completely_unchanged(self):
        p,c=fixture(transition_out={'type':'dissolve','duration':2});p['sequences'][0]['fps']=30;before=copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError,'transition'):run(p,'scenes',[3,9])
        self.assertEqual(p,before)

    def test_scene_scene_boundaries_and_duplicates_are_safe_noops(self):
        p,c=fixture();plan=run(p,'scenes',[2,10]);self.assertEqual(plan['ops'],[])
        plan=run(p,'scenes',[4,4,4]);self.assertEqual(len(plan['summary']['cuts']),1)

    def test_fragment_ids_and_review_fingerprint_are_deterministic_and_input_is_immutable(self):
        p,c=fixture();before=copy.deepcopy(p);a=run(p,'scenes',[4,6]);b=run(p,'scenes',[4,6]);other=run(p,'scenes',[4,6],'task-two')
        self.assertEqual(a,b);self.assertNotEqual(a['fingerprint'],other['fingerprint']);self.assertEqual(p,before)
        used={x['id'] for x in apply(p,a)['sequences'][0]['tracks'][0]['clips']};self.assertEqual(len(used),3)

    def test_silence_union_removes_each_span_once_and_preserves_later_source_samples(self):
        p,c=fixture();p['sequences'][0]['fps']=30
        plan=run(p,'silences',[{'start':3,'end':4},{'start':3.5,'end':5},{'start':7,'end':8}]);after=apply(p,plan);pieces=after['sequences'][0]['tracks'][0]['clips']
        self.assertEqual(plan['summary']['ranges'],[[11,13],[15,16]]);self.assertEqual(plan['summary']['removed_duration'],3)
        self.assertEqual([(x['start'],x['in_'],x['out']) for x in pieces],[(10,2,3),(11,5,7),(13,8,10)])

    def test_reverse_ramped_silence_uses_source_inverse_before_inward_picture_alignment(self):
        p,c=fixture(reverse=True,time_remap=[{'t':0,'v':1},{'t':2,'v':3}]);plan=run(p,'silences',[{'start':4,'end':6}]);self.assertEqual(len(plan['summary']['ranges']),1)
        a,b=plan['summary']['ranges'][0];self.assertGreaterEqual(source(c,a-c['start']),4-1e-8);self.assertLessEqual(source(c,a-c['start']),6+1e-8)
        self.assertGreaterEqual(source(c,b-c['start']),4-1e-8);self.assertLessEqual(source(c,b-c['start']),6+1e-8)
        rate=30000/1001;self.assertAlmostEqual(a*rate,round(a*rate));self.assertAlmostEqual(b*rate,round(b*rate))

    def test_silence_curves_and_fade_windows_follow_the_surviving_original_clock(self):
        p,c=fixture(keyframes={'transform.x':[{'t':0,'v':0,'e':'ease'},{'t':8,'v':100}]},audio={'fade_in':5,'fade_out':3});p['sequences'][0]['fps']=30
        pieces=apply(p,run(p,'silences',[{'start':4,'end':6}]))['sequences'][0]['tracks'][0]['clips']
        self.assertEqual(pieces[1]['keyframes']['transform.x'][0]['t'],-4)
        self.assertEqual(fade_window(pieces[1],clip_dur(pieces[1]))['offset'],4)
        self.assertAlmostEqual(kf_eval(pieces[1]['keyframes']['transform.x'],1),kf_eval(c['keyframes']['transform.x'],5))

    def test_silence_respects_locked_and_excluded_peers_but_always_shifts_explicit_track(self):
        p,c=fixture();seq=p['sequences'][0];seq['fps']=30;seq['tracks'][0]['sync_lock']=False
        for name,flags in [('included',{}),('excluded',{'sync_lock':False}),('locked',{'locked':True})]:
            seq['tracks'].append({'id':name,'kind':'audio','clips':[{'id':name,'media_id':'m','start':20,'in_':0,'out':1,'speed':1}],**flags})
        after=apply(p,run(p,'silences',[{'start':4,'end':6}]))['sequences'][0]
        self.assertEqual([tr['clips'][0]['start'] for tr in after['tracks'][1:]],[18,20,20])
        self.assertEqual(len(after['tracks'][0]['clips']),2)

    def test_crossing_peer_rejects_every_change_before_any_clip_or_annotation_mutation(self):
        p,c=fixture();seq=p['sequences'][0];seq['tracks'].append({'id':'A1','kind':'audio','clips':[{'id':'music','media_id':'m','start':0,'in_':0,'out':30,'speed':1}]});seq['markers']=[{'time':20}];before=copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError,'Extract'):run(p,'silences',[{'start':4,'end':6}])
        self.assertEqual(p,before)

    def test_annotations_ranges_and_caption_fragments_follow_removed_union(self):
        p,c=fixture();seq=p['sequences'][0];seq['fps']=30;seq.update(markers=[{'id':'m','time':13,'duration':5}],captions=[{'id':'cap','start':11,'end':16,'text':'keep text'}],in_point=12,out_point=14)
        after=apply(p,run(p,'silences',[{'start':4,'end':6}]))['sequences'][0]
        self.assertEqual(after['markers'],[{'id':'m','time':12,'duration':4}]);self.assertIsNone(after['in_point']);self.assertIsNone(after['out_point'])
        self.assertEqual([(cp['start'],cp['end']) for cp in after['captions']],[(11,12),(12,14)]);self.assertNotEqual(after['captions'][0]['id'],after['captions'][1]['id'])

    def test_audio_only_ranges_keep_sample_precision_without_picture_participants(self):
        p,c=fixture();p['sequences'][0]['tracks'][0]['kind']='audio';plan=run(p,'silences',[{'start':3.0002,'end':4.0007}])
        self.assertAlmostEqual(plan['summary']['ranges'][0][0],11.0002);self.assertAlmostEqual(plan['summary']['removed_duration'],1.0005)

    def test_held_locked_missing_media_and_invalid_analysis_results_refuse(self):
        for change in ('hold','lock','media'):
            p,c=fixture();c['hold']=change=='hold';p['sequences'][0]['tracks'][0]['locked']=change=='lock'
            if change=='media':p['media']={}
            with self.assertRaises(ValueError):run(p,'silences',[{'start':3,'end':4}])
        for values in ([math.nan],[True],[-1],[101]):
            p,c=fixture()
            with self.assertRaises(ValueError):run(p,'scenes',values)

    def test_silence_cannot_create_a_picture_fragment_shorter_than_one_frame(self):
        p,c=fixture(start=.01);p['sequences'][0]['fps']=30;before=copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError,'less than one picture frame'):run(p,'silences',[{'start':2.001,'end':3}])
        self.assertEqual(p,before)

    def test_bounded_result_and_copy_limits_reject_without_mutating_input(self):
        p,c=fixture();before=copy.deepcopy(p)
        with patch.object(edits,'MAX_RESULTS',1),self.assertRaisesRegex(ValueError,'at most'):run(p,'scenes',[3,4])
        with patch.object(edits,'MAX_COPIED_BYTES',10),self.assertRaisesRegex(ValueError,'too large'):run(p,'scenes',[3])
        self.assertEqual(p,before)


if __name__=='__main__':unittest.main(verbosity=2)
