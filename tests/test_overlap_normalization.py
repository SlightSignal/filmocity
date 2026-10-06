"""Production overlap resolution and actual guarded project/history persistence."""
import copy
import json
import math
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import overlap_normalization as overlap
from render import clip_dur, kf_eval
from audio_contract import fade_spec, fade_window
import test_project_sync as store
import test_proposal_history as proposals


def clip(identifier='base', start=0, duration=10, **extra):
    return {'id':identifier,'start':start,'in_':0,'out':duration,**extra}


def project(clips):
    return {'sequences':[{'id':'seq','tracks':[{'id':'V1','kind':'video','index':0,'clips':clips}]}]}


def clips(p): return p['sequences'][0]['tracks'][0]['clips']


def owner(collection, at):
    hits=[c['id'] for c in collection if c['start'] <= at < c['start']+clip_dur(c)]
    if len(hits)>1: raise AssertionError(f'Overlapping results at {at}: {hits}')
    return hits[0] if hits else None


class Sweep(unittest.TestCase):
    def test_all_75_fully_covered_clips_are_removed_in_one_pass(self):
        p=project([clip(f'old{i}') for i in range(75)]+[clip('winner')])
        self.assertEqual(len(overlap.normalize_tracks(p)),75);self.assertEqual([c['id'] for c in clips(p)],['winner'])

    def test_fragments_keep_original_priority_against_every_later_clip(self):
        p=project([clip(),clip('middle',2,2),clip('later',3,3)])
        overlap.normalize_tracks(p)
        self.assertEqual([(owner(clips(p),t),t) for t in (1,2.5,3.5,5,7)],[('base',1),('middle',2.5),('later',3.5),('later',5),('base_r',7)])
        self.assertAlmostEqual(clip_dur(next(c for c in clips(p) if c['id']=='middle')),1)

    def test_many_holes_preserve_original_source_and_unique_ids(self):
        p=project([clip(duration=101)]+[clip(f'new{i}',2*i+1,1) for i in range(50)])
        overlap.normalize_tracks(p);self.assertEqual(len(clips(p)),101)
        self.assertEqual(len({c['id'] for c in clips(p)}),101)
        for c in clips(p):
            if c['id'].startswith('base'):self.assertAlmostEqual(c['in_'],c['start'])
        before=copy.deepcopy(p);self.assertEqual(overlap.normalize_tracks(p),[]);self.assertEqual(p,before)

    def test_ids_are_reserved_across_tracks_and_sequences(self):
        p=project([clip(),clip('new',2,2)])
        p['sequences'][0]['tracks'].append({'id':'V2','clips':[clip('base_r')]})
        p['sequences'].append({'id':'other','tracks':[{'id':'V1','clips':[clip('base_r2')]}]})
        overlap.normalize_tracks(p);self.assertIn('base_r3',[c['id'] for c in clips(p)])

    def test_no_overlap_is_byte_equivalent_and_preserves_object_identity(self):
        a=clip(audio={'fade_in':.5},vendor={'extension':42});b=clip('next',10)
        p=project([a,b]);before=json.dumps(p);collection=clips(p)
        self.assertEqual(overlap.normalize_tracks(p),[]);self.assertEqual(json.dumps(p),before)
        self.assertIs(clips(p),collection);self.assertIs(clips(p)[0],a)

    def test_nearby_real_submicrosecond_overlap_is_not_discarded(self):
        p=project([clip(duration=1),clip('new',1-1e-7,1)])
        overlap.normalize_tracks(p);self.assertAlmostEqual(clips(p)[0]['out'],1-1e-7,places=12)

    def test_fractional_frame_boundaries_are_idempotent(self):
        rate=30000/1001;p=project([clip(duration=40/rate),clip('new',5/rate,5/rate),clip('other',20/rate,7/rate)])
        overlap.normalize_tracks(p);before=copy.deepcopy(p)
        for _ in range(10):self.assertEqual(overlap.normalize_tracks(p),[]);self.assertEqual(p,before)
        for at,expected in ((4,'base'),(6,'new'),(11,'base_r'),(22,'other'),(30,'base_r2')):self.assertEqual(owner(clips(p),at/rate),expected)

    def test_work_limits_and_errors_do_not_publish_earlier_track_changes(self):
        p=project([clip(),clip('new',2,2)]);p['sequences'][0]['tracks'].append({'id':'bad','clips':[clip('invalid',speed=0)]});before=copy.deepcopy(p)
        with self.assertRaises(ValueError):overlap.normalize_tracks(p)
        self.assertEqual(p,before)
        p=project([clip(),clip('new',2,2)]);before=copy.deepcopy(p)
        with patch.object(overlap,'MAX_CLIPS',1),self.assertRaisesRegex(ValueError,'at most'):overlap.normalize_tracks(p)
        self.assertEqual(p,before)

    def test_fragment_output_limit_is_atomic_and_success_remains_normalizable(self):
        p=project([clip(),clip('one',1,1),clip('two',3,1)]);before=copy.deepcopy(p)
        with patch.object(overlap,'MAX_CLIPS',3),self.assertRaisesRegex(ValueError,'would create more than'):overlap.normalize_tracks(p)
        self.assertEqual(p,before)
        with patch.object(overlap,'MAX_CLIPS',5):
            overlap.normalize_tracks(p);self.assertEqual(len(clips(p)),5);after=copy.deepcopy(p)
            self.assertEqual(overlap.normalize_tracks(p),[]);self.assertEqual(p,after)
        # Locked and unchanged tracks also consume the project-wide output budget.
        p=project([clip(),clip('one',1,1)])
        p['sequences'][0]['tracks'].append({'id':'locked','locked':True,'clips':[clip('locked')]});before=copy.deepcopy(p)
        with patch.object(overlap,'MAX_CLIPS',3),self.assertRaisesRegex(ValueError,'would create more than'):overlap.normalize_tracks(p)
        self.assertEqual(p,before)

    def test_curve_copy_work_limit_rejects_before_mutation(self):
        p=project([clip(keyframes={'transform.x':[{'t':0,'v':0},{'t':10,'v':1}]}),clip('new',2,2)]);before=copy.deepcopy(p)
        with patch.object(overlap,'MAX_COPIED_POINTS',3),self.assertRaisesRegex(ValueError,'too many'):overlap.normalize_tracks(p)
        self.assertEqual(p,before)

    def test_large_payload_copy_budget_is_checked_before_fragment_copies(self):
        p=project([clip(vendor={'payload':'x'*1000}),clip('new',2,2)]);before=copy.deepcopy(p)
        with patch.object(overlap,'MAX_COPIED_BYTES',1500),self.assertRaisesRegex(ValueError,'too much clip data'):overlap.normalize_tracks(p)
        self.assertEqual(p,before)

    def test_tiny_duration_at_large_time_is_rejected_without_a_phantom_winner(self):
        p=project([clip('long',1e12,10),clip('tiny',1e12,.0001)]);before=copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError,'too small'):overlap.normalize_tracks(p)
        self.assertEqual(p,before)
        # Exercise the event sweep independently to guard its cluster ordering.
        tiny=clip('tiny',1e12+.0001,.0001)
        spans=overlap._visible_spans([clip('long',1e12,10),tiny],[10,.0001])
        self.assertEqual(spans[1],[]);self.assertEqual(spans[0],[[1e12,1e12+10]])

    def test_locked_track_overlaps_are_preserved_during_unrelated_normalization(self):
        p=project([clip(),clip('cover',2,2)]);track=p['sequences'][0]['tracks'][0];track['locked']=True;before=copy.deepcopy(p)
        self.assertEqual(overlap.normalize_tracks(p),[]);self.assertEqual(p,before);self.assertIs(clips(p),track['clips'])

    def test_picture_transition_cut_rejection_is_atomic_across_tracks(self):
        p=project([clip(),clip('new',2,2)])
        p['sequences'][0]['tracks'].append({'id':'V2','clips':[clip('fade',transition_in={'duration':2}),clip('cover',1,2)]});before=copy.deepcopy(p)
        with self.assertRaisesRegex(ValueError,'picture transition'):overlap.normalize_tracks(p)
        self.assertEqual(p,before)

    def test_twenty_thousand_endpoint_events_keep_last_list_priority(self):
        p=project([clip(f'c{i}',i/100,1) for i in range(10_000)])
        overlap.normalize_tracks(p);self.assertEqual(len(clips(p)),10_000)
        for i in (0,1,19,1999,9999):self.assertEqual(owner(clips(p),i/100+.005),f'c{i}')


class Slicing(unittest.TestCase):
    def test_constant_forward_and_reverse_source_windows(self):
        c=clip(in_=5,out=25,speed=2,start=4)
        for reverse,expected in ((False,(9,17)),(True,(13,21))):
            c['reverse']=reverse;part=overlap.slice_clip(c,2,6)
            self.assertEqual((part['in_'],part['out']),expected);self.assertEqual(part['start'],6);self.assertEqual(clip_dur(part),4)

    def test_linear_ramp_and_hold_knots_preserve_integrated_source_clock(self):
        for points in ([{'t':0,'v':1},{'t':4,'v':3},{'t':8,'v':1}],[{'t':0,'v':1,'e':'hold'},{'t':4,'v':3,'e':'hold'},{'t':8,'v':1}]):
            for reverse in (False,True):
                c=clip(out=30,time_remap=points,reverse=reverse);part=overlap.slice_clip(c,2,7)
                self.assertAlmostEqual(clip_dur(part),5)
                for at in (0,.5,1.5,2,4.9):
                    offset=overlap._source_offset(c,at+2);source=c['out']-offset if reverse else c['in_']+offset
                    poffset=overlap._source_offset(part,at);psource=part['out']-poffset if reverse else part['in_']+poffset
                    self.assertAlmostEqual(psource,source,places=10)

    def test_subfloor_source_rates_reject_and_floor_rates_preserve_slice_duration(self):
        for extra in ({'speed':1e-7},{'time_remap':[{'t':0,'v':1e-7}]},{'time_remap':[{'t':0,'v':1},{'t':2,'v':1e-7}]}):
            p=project([clip(out=1e-5,**extra),clip('cover',1,1)]);before=copy.deepcopy(p)
            with self.assertRaisesRegex(ValueError,'at least 0.000001'):overlap.normalize_tracks(p)
            self.assertEqual(p,before)
        for extra in ({'speed':1e-6},{'time_remap':[{'t':0,'v':1e-6}]}):
            c=clip(out=1e-5,**extra);part=overlap.slice_clip(c,2,5)
            self.assertAlmostEqual(clip_dur(part),3);self.assertAlmostEqual(part['in_'],2e-6);self.assertAlmostEqual(part['out'],5e-6)

    def test_hold_retains_freeze_frame_and_changes_only_visible_duration(self):
        c=clip(in_=5,out=15,hold=True,reverse=True,time_remap=[{'t':0,'v':2}]);part=overlap.slice_clip(c,3,7)
        self.assertEqual(part['in_'],5);self.assertEqual(part['out'],9);self.assertEqual(clip_dur(part),4)

    def test_eased_bezier_and_hold_automation_keep_offscreen_anchors(self):
        for easing in ('linear','hold','ease','ease_in','ease_out','bezier'):
            points=[{'t':0,'v':1,'e':easing,'o':[.2,4]},{'t':10,'v':20,'i':[.2,-5]}]
            c=clip(keyframes={'transform.x':points});part=overlap.slice_clip(c,3,7)
            self.assertEqual(part['keyframes']['transform.x'][0]['t'],-3)
            for at in (0,.5,1,3.9):self.assertAlmostEqual(kf_eval(part['keyframes']['transform.x'],at),kf_eval(points,at+3),places=8)

    def test_ducking_is_rebased_without_invalid_negative_times(self):
        points=[{'t':0,'v':0},{'t':2,'v':-12,'e':'hold'},{'t':7,'v':-12},{'t':9,'v':0}]
        c=clip(keyframes={'audio.duck_db':points});part=overlap.slice_clip(c,3,8);shifted=part['keyframes']['audio.duck_db']
        self.assertEqual(shifted[0],{'t':0,'v':-12,'e':'hold'})
        for at in (0,.5,3,4.9):self.assertAlmostEqual(kf_eval(shifted,at),kf_eval(points,at+3))

    def test_fades_keep_original_clock_across_repeated_slices(self):
        c=clip(audio={'fade_in':4,'fade_out':4,'constant_power':False})
        part=overlap.slice_clip(c,2,9);part=overlap.slice_clip(part,1,5)
        self.assertEqual(fade_window(part,4)['duration'],10);self.assertEqual(fade_window(part,4)['offset'],3)
        spec=fade_spec(part,4);self.assertEqual([p['start'] for p in spec],[-3,3]);self.assertEqual([p['duration'] for p in spec],[4,4])
        part['audio']['fade_in']=1;self.assertEqual(fade_window(part,4)['offset'],0)

    def test_markers_survive_only_in_their_interval_and_transitions_only_at_edges(self):
        c=clip(markers=[{'t':0},{'t':2},{'t':4},{'t':8},{'t':10}],transition_in={'duration':1},transition_out={'duration':1})
        p=overlap.slice_clip(c,2,8);self.assertEqual([m['t'] for m in p['markers']],[0,2]);self.assertIsNone(p['transition_in']);self.assertIsNone(p['transition_out'])
        self.assertEqual(overlap.slice_clip(c,0,2)['transition_in'],c['transition_in']);self.assertEqual(overlap.slice_clip(c,8,10)['markers'],[{'t':0},{'t':2}])

    def test_invalid_ramps_or_curves_never_silently_corrupt_a_clip(self):
        for points in ([{'t':0,'v':0}],[{'t':1,'v':1},{'t':0,'v':2}],[{'t':0,'v':True}],[{'t':float('nan'),'v':1}]):
            with self.subTest(points=points),self.assertRaises(ValueError):overlap.slice_clip(clip(time_remap=points),1,2)
        for field,value in (('keyframes',[{'t':0,'v':1}]),('audio','invalid'),('markers',{'t':1})):
            with self.assertRaises(ValueError):overlap.slice_clip(clip(**{field:value}),1,2)
        for points in ([{'t':1,'v':float('inf')}],None):
            with self.assertRaises(ValueError):overlap.slice_clip(clip(keyframes={'transform.x':points}),1,2)


class Persistence(store.ProjectStoreFixture):
    def seed(self,**extra):
        p=self.env['load_project']();p['sequences'][0]['tracks'][0]['clips']=[clip('base',media_id='A',**extra)];p['sequences'][0]['tracks'][1]['clips']=[];self.env['save_project'](p)
    def edit_overlap(self):
        return self.invoke('patch_project',{'_context':self.current(),'ops':[{'op':'set_clip','sequence':'seq1','track':'V1','clip':clip('cover',2,3,media_id='A')}]})
    def test_guarded_edit_undo_and_redo_keep_every_fragment_and_curve_exact(self):
        self.seed(audio={'fade_in':4},keyframes={'transform.x':[{'t':0,'v':0},{'t':10,'v':100}]});before=self.env['load_project']()['sequences']
        result=self.edit_overlap();after=self.env['load_project']()['sequences'];self.assertTrue(result['ok']);self.assertEqual(len(after[0]['tracks'][0]['clips']),3)
        self.assertEqual(result['applied']['seq1']['V1'],after[0]['tracks'][0]['clips']);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before)
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after)
    def test_unsupported_transition_and_storage_fault_leave_saved_history_unchanged(self):
        self.seed(transition_in={'duration':3});before=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with self.assertRaises(store.HTTPError) as error:self.edit_overlap()
        self.assertEqual(error.exception.status_code,422);self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)
        self.seed();before=self.raw()
        with patch.dict(self.env,save_project=lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full'))),self.assertRaises(OSError):self.edit_overlap()
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)


class Proposal(unittest.TestCase):
    def test_review_accept_and_undo_use_identical_deterministic_fragments(self):
        f=proposals.ProposalHistoryTests();f.setUp();self.addCleanup(f.tearDown)
        p=f.env['load_project']();p['sequences'][0]['tracks'][0]['clips']=[clip(media_id='A')];p['sequences'][0]['tracks'][1]['clips']=[];f.env['save_project'](p)
        pr=f.propose([{'reason':'Insert replacement','ops':[{'op':'set_clip','sequence':'seq1','track':'V1','clip':clip('cover',2,3,media_id='A')}]}],_context=f.current())
        before=f.env['load_project']()['sequences'];candidate,_,_,_=f.env['prepare_proposal'](f.env['load_project'](),pr['id'],[pr['items'][0]['id']]);again,_,_,_=f.env['prepare_proposal'](f.env['load_project'](),pr['id'],[pr['items'][0]['id']])
        self.assertEqual(candidate['sequences'],again['sequences']);self.assertTrue(f.decide(pr)['ok']);self.assertEqual(f.env['load_project']()['sequences'],candidate['sequences'])
        f.undo();self.assertEqual(f.env['load_project']()['sequences'],before);f.redo();self.assertEqual(f.env['load_project']()['sequences'],candidate['sequences'])


if __name__=='__main__':unittest.main(verbosity=2)
