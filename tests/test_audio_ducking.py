"""Additive ducking, actual saved-history routes, and real FFmpeg PCM.

Controlled request adapters are not HTTP/browser/native acceptance.
"""
import array
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

import test_project_sync as store
from test_media_collection import run_async_check
import test_audio_contract as audio_fixture
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import audio_ducking as duck
import project_lifecycle as lifecycle
import render as engine
from render_context import RenderContext


def project():
    def clip(identity,start,end):return {'id':identity,'media_id':'m','start':start,'in_':0,'out':end-start,'audio':{}}
    bed=clip('bed',0,6);bed['keyframes']={'audio.gain_db':[{'t':0,'v':-3},{'t':6,'v':-6}],'transform.x':[{'t':0,'v':2}]}
    return {'id':'p','media':{'m':{'has_audio':True}},'sequences':[{'id':'s','name':'Sequence','width':64,'height':48,'fps':30,'tracks':[
        {'id':'V1','kind':'video','index':1,'clips':[clip('d1',1,2),clip('d2',1.5,3)]},
        {'id':'A1','kind':'audio','index':1,'clips':[]},
        {'id':'A2','kind':'audio','index':2,'clips':[bed]}]}]}


def request(**extra):return {'sequence':'s','music_tracks':['A2'],'dialogue_tracks':['V1'],'amount':-12,'attack':.25,'release':.5,'hold':0,**extra}
def bed(p):return p['sequences'][0]['tracks'][2]['clips'][0]
def db(points,t):return duck.value([(p['t'],p['v']) for p in points],[p['t'] for p in points],t)


class Planner(unittest.TestCase):
    def test_overlap_stays_down_and_manual_automation_is_byte_equivalent(self):
        p=project();before=copy.deepcopy(p);after,summary=duck.build(p,request())
        self.assertEqual(p,before);self.assertEqual(summary['dialogue_spans'],1)
        self.assertEqual(bed(after)['keyframes']['audio.gain_db'],bed(p)['keyframes']['audio.gain_db'])
        self.assertEqual(bed(after)['keyframes']['transform.x'],bed(p)['keyframes']['transform.x'])
        keys=bed(after)['keyframes'][duck.KEY]
        for time,expected in [(0,0),(.875,-6),(1,-12),(2.25,-12),(3,-12),(3.25,-6),(4,0)]:self.assertAlmostEqual(db(keys,time),expected)
        self.assertEqual(duck.build(after,request())[1]['ducked'],0)

    def test_short_gap_uses_ramp_intersection_instead_of_unity_or_stacked_gain(self):
        points,_=duck.envelope([(1,2),(2.3,3)],-12,.2,.4,0)
        self.assertAlmostEqual(duck.value(points,[t for t,_ in points],2.2),-6)
        self.assertTrue(all(-12<=v<=0 for _,v in points))
        self.assertAlmostEqual(duck.value(points,[t for t,_ in points],2.1),-9)

    def test_hold_merges_adjacent_spans_and_long_gaps_return_to_zero(self):
        points,merged=duck.envelope([(1,2),(2.1,3),(5,6)],-9,.1,.2,.2)
        self.assertEqual(merged,[[1,3.2],[5,6.2]]);self.assertEqual(duck.value(points,[t for t,_ in points],4),0)

    def test_partial_clip_boundary_retains_exact_fractional_gain(self):
        p=project();c=bed(p);c.update(start=.812345,out=.4)
        after,_=duck.build(p,request());keys=bed(after)['keyframes'][duck.KEY]
        self.assertEqual(keys[0]['t'],0);self.assertAlmostEqual(keys[0]['v'],-12*(.812345-.75)/.25)
        self.assertAlmostEqual(keys[-1]['t'],.4);self.assertEqual(keys[-1]['v'],-12)

    def test_remove_deletes_only_ducking_even_from_disabled_or_muted_clips(self):
        p,_=duck.build(project(),request());bed(p)['enabled']=False;p['sequences'][0]['tracks'][2]['muted']=True
        after,summary=duck.build(p,request(mode='remove'));self.assertEqual(summary['ducked'],1)
        expected=copy.deepcopy(bed(p));del expected['keyframes'][duck.KEY];self.assertEqual(bed(after),expected)
        for existing in ([],{},None):
            bed(p)['keyframes'][duck.KEY]=existing
            removed,summary=duck.build(p,request(mode='remove'))
            self.assertNotIn(duck.KEY,bed(removed)['keyframes']);self.assertEqual(summary['ducked'],1)

    def test_muted_solo_unlinked_disabled_and_hold_dialogue_cannot_drive_ducking(self):
        for kind in ('muted','solo','unlinked','disabled','hold'):
            with self.subTest(kind=kind):
                p=project();tracks=p['sequences'][0]['tracks'];clips=tracks[0]['clips']
                if kind=='muted':tracks[1]['muted']=True
                elif kind=='solo':tracks[2]['solo']=True
                else:
                    for c in clips:
                        if kind=='unlinked':c['audio']['linked']=False
                        elif kind=='disabled':c['enabled']=False
                        else:c['hold']=True
                with self.assertRaisesRegex(ValueError,'no audible'):duck.build(p,request())

    def test_inaudible_music_is_skipped_and_locked_music_is_rejected(self):
        p=project();bed(p)['enabled']=False;after,summary=duck.build(p,request());self.assertEqual(after,p);self.assertEqual(summary['skipped'],1)
        p['sequences'][0]['tracks'][2]['locked']=True
        with self.assertRaisesRegex(ValueError,'unlocked'):duck.build(p,request())

    def test_nested_audio_uses_parent_span_and_rejects_cycles(self):
        p=project();child=copy.deepcopy(p['sequences'][0]);child['id']='child';child['tracks']=child['tracks'][:2];p['sequences'].append(child)
        p['sequences'][0]['tracks'][0]['clips']=[{'id':'n','sequence_id':'child','start':.3,'in_':0,'out':4}]
        after,summary=duck.build(p,request());self.assertEqual(summary['dialogue_clips'],1);self.assertEqual(db(bed(after)['keyframes'][duck.KEY],4),-12)
        child['tracks'][0]['clips']=[{'sequence_id':'child'}]
        with self.assertRaisesRegex(ValueError,'cycle'):duck.build(p,request())

    def test_reverse_and_speed_keep_timeline_relative_automation(self):
        p=project();c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(reverse=True,speed=2);p['sequences'][0]['tracks'][0]['clips']=[c]
        after,_=duck.build(p,request());keys=bed(after)['keyframes'][duck.KEY]
        self.assertEqual(db(keys,1.4),-12);self.assertEqual(db(keys,2.1),0)

    def test_defaults_exclude_tagged_dialogue_audio_from_music(self):
        p=project();tracks=p['sequences'][0]['tracks'];c=copy.deepcopy(tracks[0]['clips'][0]);c['audio_tag']='dialogue';tracks[1]['clips']=[c]
        _,summary=duck.build(p,{'sequence':'s'});self.assertEqual(summary['music_tracks'],['A2']);self.assertEqual(summary['dialogue_tracks'],['V1','A1'])

    def test_invalid_choices_and_settings_never_mutate_input(self):
        p=project();before=copy.deepcopy(p)
        for kw in ({'music_tracks':[]},{'music_tracks':['A2','A2']},{'dialogue_tracks':['A2']},{'music_tracks':['V1']},{'attack':0},{'release':True},{'hold':float('nan')},{'amount':4},{'mode':'guess'}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):duck.build(p,request(**kw))
        self.assertEqual(p,before)

    def test_curve_limits_and_malformed_points_fail_clearly(self):
        for points in ({},[None],[{'t':0,'v':1}],[{'t':0,'v':-3,'e':'bezier'}],[{'t':0,'v':-3}]*2,[{'t':i,'v':-3} for i in range(8193)]):
            with self.subTest(points=str(points)[:40]),self.assertRaises(ValueError):duck.filters({'keyframes':{duck.KEY:points}})

    def test_cancellation_and_point_budget_do_not_change_project(self):
        p=project();before=copy.deepcopy(p)
        with self.assertRaisesRegex(RuntimeError,'cancelled'):duck.build(p,request(),lambda:(_ for _ in ()).throw(RuntimeError('cancelled')))
        with patch.object(duck,'MAX_POINTS',2),self.assertRaisesRegex(ValueError,'too many'):duck.build(p,request())
        self.assertEqual(p,before)

    def test_sequence_point_limit_rejects_whole_plan_without_partial_publication(self):
        p=project();p['sequences'][0]['tracks'][2]['clips']=[{**copy.deepcopy(bed(p)),'id':str(i)} for i in range(9)];before=copy.deepcopy(p)
        points=[{'t':i/8192,'v':-12 if i%2 else 0} for i in range(8192)]
        with patch.object(duck,'clip_curve',return_value=points),self.assertRaisesRegex(ValueError,'65,536'):duck.build(p,request())
        self.assertEqual(p,before)


class Routes(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp();names={'_workflow_capture','_workflow_commit','duck_all'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.worker=lifecycle.ActionWorker();self.worker_patch=patch.object(lifecycle,'COPY_WORKER',self.worker);self.worker_patch.start()
        p=self.env['load_project']();p.update(project());self.env['save_project'](p)
    def tearDown(self):self.worker.shutdown();self.worker_patch.stop();super().tearDown()
    def call(self,**extra):return run_async_check(self.env['duck_all'](store.Request(request(_context=self.current(),**extra))))
    def test_review_changes_no_files_then_apply_undo_redo_preserve_manual_gains(self):
        before=self.raw();review=self.call(preview=True);self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a')['undo'],[])
        reply=self.call(preview_plan=review['plan']);self.assertTrue(reply['ok']);after=self.env['load_project']()
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(bed(self.env['load_project']()),bed(json.loads(before)))
        self.invoke('redo',{'_context':self.current()});self.assertEqual(bed(self.env['load_project']()),bed(after))
        self.call(mode='remove');self.assertEqual(bed(self.env['load_project']()),bed(json.loads(before)))
    def test_missing_stale_and_changed_plan_rejected_before_commit(self):
        before=self.raw()
        for body in (request(),request(_context={**self.current(),'revision':'old'})):
            with self.assertRaises(store.HTTPError):run_async_check(self.env['duck_all'](store.Request(body)))
        review=self.call(preview=True)
        with self.assertRaises(store.HTTPError):self.call(preview_plan=review['plan'],amount=-6)
        self.assertEqual(self.raw(),before)
    def test_noop_and_lost_reply_cannot_duplicate_history(self):
        old=self.current();self.call();self.call();self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        with self.assertRaises(store.HTTPError):run_async_check(self.env['duck_all'](store.Request(request(_context=old))))
    def test_project_changed_during_planning_is_preserved(self):
        real=duck.build
        def edited(*a,**kw):
            result=real(*a,**kw);p=self.env['load_project']();p['name']='Concurrent edit';self.env['save_project'](p);return result
        with patch.object(duck,'build',side_effect=edited),self.assertRaises(store.HTTPError):self.call()
        self.assertEqual(self.env['load_project']()['name'],'Concurrent edit');self.assertNotIn(duck.KEY,bed(self.env['load_project']())['keyframes'])
    def test_commit_failure_rolls_back_project_and_history(self):
        before=self.raw();history=self.env['read_undo_history']('a')
        with patch.dict(self.env,save_project=lambda *a,**kw:(_ for _ in ()).throw(OSError('disk full'))),self.assertRaises(OSError):self.call()
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_postcommit_notification_failure_reports_saved_with_warning(self):
        async def fail(event):raise OSError('broadcast lost')
        with patch.dict(self.env,broadcast=fail,log_event=lambda *a,**kw:(_ for _ in ()).throw(OSError('event failed'))):reply=self.call()
        self.assertTrue(reply['ok']);self.assertIn('Event history',reply['warning']);self.assertIn('refresh',reply['warning'])
    def test_agent_governance_allows_review_but_refuses_direct_apply(self):
        (self.root/'settings.json').write_text(json.dumps({'agent_mode':'proposals_only'}));before=self.raw()
        review=self.call(preview=True,actor='agent');self.assertTrue(review['preview'])
        with self.assertRaises(store.HTTPError) as error:self.call(actor='agent')
        self.assertEqual(error.exception.status_code,403);self.assertEqual(self.raw(),before)
    def test_repeated_cancellation_joins_planner_before_releasing_ownership(self):
        entered=threading.Event();release=threading.Event();real=duck.build
        def delayed(*args):entered.set();release.wait(3);return real(*args)
        async def run():
            with patch.object(duck,'build',side_effect=delayed):
                task=asyncio.create_task(self.env['duck_all'](store.Request(request(_context=self.current()))))
                try:
                    while not entered.is_set():await asyncio.sleep(.01)
                    task.cancel();await asyncio.sleep(.02);task.cancel();await asyncio.sleep(.02);self.assertFalse(task.done())
                finally:release.set()
                with self.assertRaises(asyncio.CancelledError):await task
        before=self.raw();run_async_check(run());self.assertEqual(self.raw(),before)


class PCM(unittest.TestCase):
    setUp=audio_fixture.Audio.setUp;render=audio_fixture.Audio.render;point=audio_fixture.Audio.point;close=audio_fixture.Audio.close
    def test_manual_gain_plus_duck_and_fade_match_independent_samples(self):
        self.clip['keyframes']={'audio.gain_db':[{'t':0,'v':-6},{'t':1,'v':-6}],duck.KEY:[{'t':0,'v':0},{'t':.2,'v':-12},{'t':.6,'v':-12},{'t':.8,'v':0}]}
        self.clip['audio']={'fade_in':.2,'constant_power':False}
        samples=self.render()
        for time,db_,fade in [(.1,-6,.5),(.4,-12,1),(.7,-6,1),(.9,0,1)]:
            expected=[v/32768*10**((-6+db_)/20)*fade for v in (3276,6553)]
            self.close(self.point(samples,time),expected,tolerance=.0007)
    def test_synthetic_tone_receives_same_additive_attenuation(self):
        self.project['media']['m']['synthetic']={'tone_hz':1000};reference=self.render()
        self.clip['keyframes']={duck.KEY:[{'t':0,'v':-12}]};ducks=self.render()
        for index in range(9600,9800):self.assertAlmostEqual(ducks[index],reference[index]*10**(-12/20),delta=2)
    def test_ducking_is_clip_relative_after_trim_speed_reverse_and_placement(self):
        self.clip.update(start=.25,in_=.1,out=.9,speed=2,reverse=True,keyframes={duck.KEY:[{'t':0,'v':-12},{'t':.4,'v':-12}]})
        samples=self.render();self.close(self.point(samples,.1),[0,0]);self.close(self.point(samples,.4),[v/32768*10**(-12/20) for v in (3276,6553)])
    def test_nested_parent_and_child_ducking_both_apply(self):
        self.clip['keyframes']={duck.KEY:[{'t':0,'v':-3}]};self.seq.update(id='child',name='Child')
        self.project['sequences'].append({'id':'s','width':64,'height':48,'fps':30,'tracks':[{'id':'A','kind':'audio','index':1,'clips':[{'id':'n','sequence_id':'child','start':0,'in_':0,'out':1,'keyframes':{duck.KEY:[{'t':0,'v':-9}]}}]}]})
        self.close(self.point(self.render(),.4),[v/32768*10**(-12/20) for v in (3276,6553)])
    def test_large_graph_is_externalized_and_owned_file_is_removed_after_render(self):
        self.clip['keyframes']={duck.KEY:[{'t':i/1000,'v':-12 if i%2 else 0} for i in range(1001)]}
        execute=engine._execute_ffmpeg;seen=[]
        def capture(cmd,**kw):
            option=next((option for option in ('-/filter_complex', '-filter_complex_script') if option in cmd),None);self.assertIsNotNone(option);path=Path(cmd[cmd.index(option)+1]);self.assertGreater(path.stat().st_size,32768);seen.append(path)
            return execute(cmd,**kw)
        with patch.object(engine,'_execute_ffmpeg',side_effect=capture):samples=self.render()
        self.assertEqual(len(samples),96000);self.assertTrue(seen);self.assertTrue(all(not p.exists() for p in seen))
        self.assertTrue(all(abs(v)<=6554 for v in samples))
    def test_graph_failure_and_cancel_keep_no_owned_scratch(self):
        self.clip['keyframes']={duck.KEY:[{'t':i/1000,'v':-12 if i%2 else 0} for i in range(500)]}
        with patch.object(engine,'_execute_ffmpeg',side_effect=RuntimeError('encoder failed')),self.assertRaisesRegex(RuntimeError,'encoder failed'):self.render()
        self.assertFalse(list(self.root.glob('filmocity-render-*')))
        with RenderContext(scratch_parent=str(self.root)) as owned:
            owned.holder['cancelled']=True
            with self.assertRaises(RuntimeError):engine._run_ffmpeg(['ffmpeg','-filter_complex','x'*5000],context=owned)
        self.assertFalse(list(self.root.glob('filmocity-render-*')))


if __name__=='__main__':unittest.main(verbosity=2)
