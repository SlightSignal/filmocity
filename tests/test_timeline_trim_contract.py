"""Production keyboard source edits through real project validation/storage/history.

Only DOM, HTTP wrappers and the live media element are controlled adapters.
"""
import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import render
import test_project_sync as store
from audio_contract import fade_spec


def keyboard_plan(clips, *, side='l', command='trimEditPoint', args=None, selection=None, time=0):
    tracks=[{'id':'v1','kind':'video','index':0,'clips':copy.deepcopy(clips)}]
    media={'m':{'duration':12,'has_video':True,'has_audio':True,'fps':30}}
    body={'tracks':tracks,'media':media,'editPoint':{'clipId':clips[0]['id'],'side':side},
          'selection':selection or [clips[0]['id']],'command':command,'args':args if args is not None else [15,False],'time':time}
    result=json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/keyboard-trim-fixture.cjs')],input=json.dumps(body),text=True,cwd=ROOT))
    return tracks,media,result


def base_clip(**patch):
    return {'id':'a','media_id':'m','start':2,'in_':3,'out':9,'speed':1,
      'vendor':{'payload':['preserve',42]},'audio':{'fade_in':3,'fade_out':4,'gain_db':-5},
      'keyframes':{'transform.x':[{'t':0,'v':0,'e':'bezier','o':[.25,2]},{'t':7,'v':30,'i':[.45,-3]}],
        'audio.duck_db':[{'t':0,'v':0,'e':'hold'},{'t':.5,'v':-9},{'t':1.25,'v':-18},{'t':10,'v':0}]},
      'markers':[{'t':.25,'name':'earlier'},{'t':1.25,'name':'inside'},{'t':7,'name':'later'}],**patch}


def at(c,t):
    if c.get('hold'):return c['in_']
    offset=t*c.get('speed',1)
    if c.get('time_remap'):
        points=c['time_remap'];offset=min(t,points[0]['t'])*points[0]['v']
        for i,p in enumerate(points):
            if t<=p['t']:break
            q=points[i+1] if i+1<len(points) else None
            dt=min(t,q['t'])-p['t'] if q else t-p['t']
            acceleration=0 if q is None or p.get('e')=='hold' else (q['v']-p['v'])/(q['t']-p['t'])
            offset+=dt*p['v']+acceleration*dt*dt/2
    return c['out']-offset if c.get('reverse') else c['in_']+offset


class TrimTransactions(store.ProjectStoreFixture):
    def install_plan(self,clips,**kwargs):
        tracks,media,value=keyboard_plan(clips,**kwargs)
        self.assertIsNotNone(value['request'],value['messages'])
        project=self.env['load_project']();project['media']=media
        project['sequences']=[{'id':'s1','width':64,'height':48,'fps':30,'tracks':tracks,'markers':[],'captions':[]}]
        self.env['save_project'](project)
        return value,self.env['load_project']()
    def commit(self,value):
        result=self.invoke('patch_project',{**value['request'],'_context':self.current()})
        self.assertTrue(result['ok']);return self.env['load_project']()
    def test_keyboard_advanced_head_trims_preserve_backend_source_automation_and_exact_history(self):
        for patch in ({},{'reverse':True},{'hold':True},{'speed':2},
                      {'time_remap':[{'t':0,'v':.5},{'t':2,'v':2},{'t':4,'v':.75}]},
                      {'reverse':True,'time_remap':[{'t':0,'v':1,'e':'hold'},{'t':1.25,'v':3}]}):
            with self.subTest(patch=patch):
                c=base_clip(**patch);plan,before=self.install_plan([c]);old_count=len(self.env['read_undo_history']('a')['undo'])
                after=self.commit(plan);result=after['sequences'][0]['tracks'][0]['clips'][0]
                self.assertEqual(result['vendor'],c['vendor']);self.assertEqual(result['start'],2.5)
                duration=render.clip_dur(c);self.assertAlmostEqual(render.clip_dur(result),duration-.5,delta=1e-9)
                for t in [0,.1,(duration-.5)/2,duration-.51]:
                    self.assertAlmostEqual(at(result,t),at(c,t+.5),delta=1e-8)
                    for key,points in c['keyframes'].items():
                        self.assertAlmostEqual(render.kf_eval(result['keyframes'][key],t),render.kf_eval(points,t+.5),delta=1e-7)
                self.assertEqual(len(self.env['read_undo_history']('a')['undo']),old_count+1)
                self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
                self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])
    def test_held_frame_duration_may_exceed_media_but_frame_must_remain_inside(self):
        c=base_clip(hold=True,in_=11.5,out=12)
        plan,_=self.install_plan([c],side='r',args=[150,False]);after=self.commit(plan)
        result=after['sequences'][0]['tracks'][0]['clips'][0]
        self.assertEqual(result['in_'],11.5);self.assertEqual(result['out'],17);self.assertEqual(render.clip_dur(result),5.5)
        for invalid in [12,12.5]:
            raw=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
            rejected=self.invoke('patch_project',{'_context':self.current(),'ops':[{'op':'set_clip','sequence':'s1','track':'v1','clip':{'id':'a','in_':invalid,'out':20}}]});self.assertEqual(rejected.status_code,422)
            self.assertEqual(self.raw(),raw);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_nonheld_source_end_guard_remains_enforced(self):
        plan,_=self.install_plan([base_clip()]);body=copy.deepcopy(plan['request']);body['ops'][0]['clip']['out']=13
        raw=self.raw()
        rejected=self.invoke('patch_project',{**body,'_context':self.current()});self.assertEqual(rejected.status_code,422)
        self.assertEqual(self.raw(),raw)
    def test_saved_trim_can_extend_back_without_losing_ramp_duck_marker_or_fade_clock(self):
        c=base_clip(time_remap=[{'t':0,'v':.5},{'t':2,'v':2},{'t':4,'v':.75}])
        first,_=self.install_plan([c]);trimmed=self.commit(first)['sequences'][0]['tracks'][0]['clips'][0]
        _,_,plan=keyboard_plan([trimmed],args=[-15,False]);restored=self.commit(plan)['sequences'][0]['tracks'][0]['clips'][0]
        for key in ['start','in_','out']:self.assertAlmostEqual(restored[key],c[key],delta=1e-9)
        for t in [0,.1,.49,.5,1,2,3]:
            self.assertAlmostEqual(at(restored,t),at(c,t),delta=1e-8)
            for key,points in c['keyframes'].items():self.assertAlmostEqual(render.kf_eval(restored['keyframes'][key],t),render.kf_eval(points,t),delta=1e-7)
        self.assertEqual(restored['markers'],c['markers']);self.assertEqual(fade_spec(restored,render.clip_dur(restored)),fade_spec(c,render.clip_dur(c)))
    def test_stale_advanced_trim_rejects_entire_payload_and_preserves_files(self):
        plan,_=self.install_plan([base_clip(reverse=True)]);context=self.current();self.edit('Another client',context);raw=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with self.assertRaises(store.HTTPError):self.invoke('patch_project',{**plan['request'],'_context':context})
        self.assertEqual(self.raw(),raw);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_keyboard_nudge_wins_overwrite_and_undo_restores_relative_order(self):
        a=base_clip(start=0,in_=3,out=6);b=base_clip(id='b',start=3,in_=6,out=9)
        plan,before=self.install_plan([a,b],command='nudge',args=[120]);after=self.commit(plan)
        clips=after['sequences'][0]['tracks'][0]['clips'];byid={c['id']:c for c in clips}
        self.assertEqual(byid['a']['start'],4);self.assertEqual(render.clip_dur(byid['a']),3)
        self.assertEqual([c['id'] for c in clips if c['start']<=4.5<c['start']+render.clip_dur(c)],['a'])
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])

    def test_mouse_source_edits_survive_actual_save_and_exact_undo_redo(self):
        for mode in ['reverse_head','hold_tail','ramp_head','ramp_slip','fade_head','reverse_roll','ripple_head']:
            with self.subTest(mode=mode):
                value=json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/source-edit-plan.cjs')],input=json.dumps({'mode':mode}),text=True,cwd=ROOT))
                p=self.env['load_project']();p.update(media=value['before']['media'],sequences=value['before']['sequences']);self.env['save_project'](p)
                before=self.env['load_project']();result=self.invoke('patch_project',{**value['body'],'_context':self.current()});self.assertTrue(result.get('ok'),result)
                after=self.env['load_project']();self.assertEqual(after['sequences'],value['optimistic']['sequences'])
                self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
                self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])
    def test_mouse_canceled_or_invalid_neighbor_plans_never_emit_an_edit(self):
        for options in [{'mode':'ramp_head','cancel':'escape'},{'mode':'fade_head','cancel':'context'},{'mode':'invalid_neighbor'}]:
            with self.subTest(options=options):
                value=json.loads(subprocess.check_output(['node',str(ROOT/'tests/helpers/source-edit-plan.cjs')],input=json.dumps(options),text=True,cwd=ROOT))
                self.assertIsNone(value['body']);self.assertEqual(value['before'],value['optimistic']);self.assertEqual(value['retainedDrafts'],0)

    def test_manual_curve_removal_and_recreation_clear_saved_history_with_exact_undo(self):
        c=base_clip(time_remap=[{'t':0,'v':.5},{'t':2,'v':2},{'t':4,'v':.75}])
        for category in ['ramp','duck']:
            with self.subTest(category=category):
                plan,_=self.install_plan([c]);before=self.commit(plan);current=before['sequences'][0]['tracks'][0]['clips'][0]
                patch={'time_remap':[]} if category=='ramp' else {'keyframes':{}}
                operation={'op':'set_clip','sequence':'s1','track':'v1','clip':{'id':'a',**patch}}
                result=self.invoke('patch_project',{'_context':self.current(),'ops':[operation]});self.assertTrue(result['ok'])
                removed=self.env['load_project']();after=removed['sequences'][0]['tracks'][0]['clips'][0]
                self.assertNotIn(category,after['source_edit_window']);other='duck' if category=='ramp' else 'ramp';self.assertEqual(after['source_edit_window'][other],current['source_edit_window'][other])
                self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],before['sequences'])
                self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],removed['sequences'])
                key='time_remap' if category=='ramp' else 'keyframes';operation['clip']={'id':'a',key:copy.deepcopy(current[key])}
                result=self.invoke('patch_project',{'_context':self.current(),'ops':[operation]});self.assertTrue(result['ok'])
                self.assertNotIn(category,self.env['load_project']()['sequences'][0]['tracks'][0]['clips'][0]['source_edit_window'])
    def test_equal_curve_and_unrelated_partial_edits_keep_restoration_history(self):
        plan,_=self.install_plan([base_clip(time_remap=[{'t':0,'v':1},{'t':3,'v':2}])]);before=self.commit(plan)
        current=before['sequences'][0]['tracks'][0]['clips'][0];patch={'id':'a','note':'manual note','time_remap':copy.deepcopy(current['time_remap']),'keyframes':{**copy.deepcopy(current['keyframes']),'transform.y':[{'t':0,'v':2}]}}
        result=self.invoke('patch_project',{'_context':self.current(),'ops':[{'op':'set_clip','sequence':'s1','track':'v1','clip':patch}]});self.assertTrue(result['ok'])
        self.assertEqual(self.env['load_project']()['sequences'][0]['tracks'][0]['clips'][0]['source_edit_window'],current['source_edit_window'])


if __name__=='__main__':unittest.main()
