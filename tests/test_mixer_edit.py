"""ID-addressed mixer edits, real project/history files and decoded PCM.

Request adapters are controlled; native HTTP/WebView/device acceptance is separate.
"""
import copy
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
import test_project_sync as store
import test_proposal_history as proposal_fixture
import test_audio_contract as audio_fixture
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import mixer_edit as mix


def op(track='A1',**changes):return {'op':'set_mix','sequence':'seq1','track':track,'changes':changes or {'gain_db':-6}}


def seed_audio(f):
    p=f.env['load_project']();p['sequences'][0]['tracks'].append({'id':'A1','kind':'audio','index':1,'clips':[]})
    f.env['save_project'](p);(f.root/'projects/b/project.json').write_text(json.dumps(p))


class Routes(store.ProjectStoreFixture):
    def setUp(self):super().setUp();seed_audio(self)
    def mix(self,ops=None,**extra):return self.invoke('patch_project',{'ops':ops or [op()],'_context':self.current(),**extra})
    def test_gain_keeps_overlapping_clips_and_undo_redo_restores_exact_bus(self):
        before=self.env['load_project']();clips=copy.deepcopy(before['sequences'][0]['tracks'])
        result=self.mix();after=self.env['load_project']();self.assertTrue(result['ok']);self.assertEqual(result['warnings'],[])
        for expected,actual in zip(clips,after['sequences'][0]['tracks']):self.assertEqual(actual['clips'],expected['clips'])
        self.assertEqual(next(t for t in after['sequences'][0]['tracks'] if t['id']=='A1')['gain_db'],-6)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])

    def test_reordered_tracks_and_sequences_resolve_ids_instead_of_old_indices(self):
        p=self.env['load_project']();p['sequences'][0]['tracks'].reverse();other=copy.deepcopy(p['sequences'][0]);other['id']='other';p['sequences'].insert(0,other);self.env['save_project'](p)
        before=copy.deepcopy(other);self.mix();p=self.env['load_project']();self.assertEqual(p['sequences'][0],before)
        self.assertEqual(next(t for t in p['sequences'][1]['tracks'] if t['id']=='A1')['gain_db'],-6)

    def test_master_field_edits_preserve_processing_extensions_and_gain(self):
        p=self.env['load_project']();p['sequences'][0]['master']={'gain_db':-3,'audio_fx':{'limiter':True},'vendor':42};self.env['save_project'](p)
        self.mix([op(None,gain_db=-9)]);master=self.env['load_project']()['sequences'][0]['master'];self.assertEqual(master,{'gain_db':-9,'audio_fx':{'limiter':True},'vendor':42})
        self.mix([op(None,audio_fx={'limiter':False,'vendor':{'kept':True}})]);master=self.env['load_project']()['sequences'][0]['master']
        self.assertEqual(master['gain_db'],-9);self.assertEqual(master['vendor'],42);self.assertEqual(master['audio_fx']['vendor'],{'kept':True})

    def test_missing_and_stale_context_never_modify_the_project(self):
        before=self.raw()
        with self.assertRaises(store.HTTPError):self.invoke('patch_project',{'ops':[op()]})
        with self.assertRaises(store.HTTPError):self.mix(_context={**self.current(),'revision':'stale'})
        self.assertEqual(self.raw(),before)

    def test_switch_to_project_with_same_document_and_track_ids_cannot_redirect_edit(self):
        context=self.current();before_a=self.raw();before_b=self.raw('b');self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.mix(_context=context)
        self.assertEqual(self.raw(),before_a);self.assertEqual(self.raw('b'),before_b)

    def test_typed_bounds_and_processing_validation_reject_without_partial_history(self):
        for changes in [{'gain_db':v} for v in (True,'-6',None,float('nan'),float('inf'),-97,25)]+[
            {'muted':1},{'solo':'false'},{'clips':[]},{'audio_fx':None},{'audio_fx':{'limiter':1}},
            {'audio_fx':{'comp':{'ratio':21}}},{'audio_fx':{'eq':{'low_db':float('nan')}}},
            {'audio_fx':{'denoise':{'db':0}}},{'audio_fx':{'comp':{'enabled':'true'}}}]:
            with self.subTest(changes=changes):
                before=self.raw();result=self.mix([op(**changes)]);self.assertEqual(result.status_code,422);self.assertEqual(self.raw(),before)
        self.assertEqual(self.env['read_undo_history']('a')['undo'],[])

    def test_removed_ambiguous_and_nonbus_targets_are_refused(self):
        for operation in (op('missing'),op('V1'),op(None,muted=True),{**op(),'sequence':'missing'},{k:v for k,v in op().items() if k!='track'}):
            before=self.raw();result=self.mix([operation]);self.assertEqual(result.status_code,422);self.assertEqual(self.raw(),before)
        p=self.env['load_project']();p['sequences'][0]['tracks'].append(copy.deepcopy(next(t for t in p['sequences'][0]['tracks'] if t['id']=='A1')));self.env['save_project'](p)
        self.assertEqual(self.mix().status_code,422)

    def test_standalone_video_bus_is_supported_when_no_audio_destination_exists(self):
        p=self.env['load_project']();p['sequences'][0]['tracks']=[t for t in p['sequences'][0]['tracks'] if t['kind']=='video'];self.env['save_project'](p)
        self.assertTrue(self.mix([op('V1',gain_db=-12,solo=True)])['ok'])

    def test_invalid_later_operation_or_disappearing_target_never_partially_commits(self):
        before=self.raw();reply=self.mix([op(),op(gain_db=100)]);self.assertEqual(reply.status_code,422);self.assertEqual(self.raw(),before)
        p=self.env['load_project']();ti=next(i for i,t in enumerate(p['sequences'][0]['tracks']) if t['id']=='A1')
        with self.assertRaises(store.HTTPError):self.mix([{'op':'remove','path':f'/sequences/0/tracks/{ti}'},op()])
        self.assertEqual(self.raw(),before)

    def test_commit_fault_recovers_project_and_history_together(self):
        before=self.raw();history=self.env['read_undo_history']('a')
        with patch.dict(self.env,save_project=lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full'))),self.assertRaises(OSError):self.mix()
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)

    def test_notification_failure_after_commit_returns_saved_with_warning(self):
        async def fail(event):raise OSError('WebSocket disconnected')
        with patch.dict(self.env,broadcast=fail,log_event=lambda *a,**kw:(_ for _ in ()).throw(OSError('log denied'))):reply=self.mix()
        self.assertTrue(reply['ok']);self.assertIn('refresh',reply['warning']);self.assertIn('Event history',reply['warning']);self.assertEqual(reply['context'],self.current())
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_direct_agent_edits_respect_proposals_only(self):
        (self.root/'settings.json').write_text(json.dumps({'agent_mode':'proposals_only'}));before=self.raw();result=self.mix(actor='agent')
        self.assertEqual(result.status_code,403);self.assertEqual(self.raw(),before)
        (self.root/'settings.json').write_text('{broken preferences');result=self.mix(actor='agent')
        self.assertEqual(result.status_code,403);self.assertEqual(self.raw(),before)

    def test_mix_before_record_is_bounded_to_changed_fields_and_legacy_inverse_restores_absence(self):
        for master in ('absent','null','populated'):
            p=self.env['load_project']();seq=p['sequences'][0];seq.pop('master',None)
            if master=='null':seq['master']=None
            if master=='populated':seq['master']={'vendor':7,'audio_fx':{'limiter':True}}
            before=copy.deepcopy(p);operation=op(None,gain_db=-6);record=mix.apply(p,operation);self.assertNotIn('clips',json.dumps(record));self.env['apply_ops'](p,mix.inverse(p,operation,record));self.assertEqual(p,before)
        p=self.env['load_project']();before=copy.deepcopy(p);operation=op(gain_db=-6,muted=True);records=self.env['apply_ops'](p,[operation]);self.env['apply_ops'](p,self.env['inverse_ops']([operation],records,p));self.assertEqual(p,before)


class Proposals(unittest.TestCase):
    def setUp(self):
        self.f=proposal_fixture.ProposalHistoryTests();self.f.setUp();self.addCleanup(self.f.tearDown);seed_audio(self.f)
        for name in ('env','current','invoke','propose','decide'):setattr(self,name,getattr(self.f,name))
    def test_mixer_proposal_preview_and_accept_never_normalize_clip_timing(self):
        with self.assertRaises(store.HTTPError) as rejected:self.propose([{'reason':'Missing basis','ops':[op()]}])
        self.assertEqual(rejected.exception.status_code,400)
        original=self.env['load_project']()['sequences'];proposal=self.propose([{'reason':'Mix','ops':[op()]}],_context=self.current())
        before=self.env['load_project']();candidate,ops,_,warnings=self.env['prepare_proposal'](before,proposal['id'],[proposal['items'][0]['id']])
        self.assertEqual(warnings,[])
        self.assertTrue(self.decide(proposal)['ok']);after=self.env['load_project']()
        for expected,actual in zip(original[0]['tracks'],after['sequences'][0]['tracks']):self.assertEqual(expected['clips'],actual['clips'])
        self.assertEqual(candidate['sequences'],after['sequences']);self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],original)


class PCM(unittest.TestCase):
    setUp=audio_fixture.Audio.setUp;render=audio_fixture.Audio.render;point=audio_fixture.Audio.point;close=audio_fixture.Audio.close
    def apply(self,track,**changes):mix.apply(self.project,{'op':'set_mix','sequence':'s','track':track,'changes':changes})
    def test_bus_and_master_gain_edit_reach_export_without_changing_clip_gain(self):
        before=copy.deepcopy(self.clip);self.apply('A1',gain_db=-9);self.apply(None,gain_db=-3)
        self.close(self.point(self.render(),.5),[v/32768*10**(-12/20) for v in (3276,6553)]);self.assertEqual(self.clip,before)
    def test_precise_small_bus_and_master_gains_are_not_rounded_or_ignored(self):
        self.apply('A1',gain_db=-.005);self.apply(None,gain_db=-.005)
        self.close(self.point(self.render(),.5),[v/32768*10**(-.01/20) for v in (3276,6553)],tolerance=1/32768)
    def test_mute_and_solo_edits_control_linked_video_audio_in_export(self):
        self.apply('A1',muted=True);self.close(self.point(self.render(),.5),[0,0]);self.apply('A1',muted=False,solo=True)
        self.close(self.point(self.render(),.5),[v/32768 for v in (3276,6553)])


if __name__=='__main__':unittest.main(verbosity=2)
