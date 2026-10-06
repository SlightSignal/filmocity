"""Actual saved Nest review/Apply, exact history and decoded picture/audio checks."""
import array
import ast
import asyncio
import copy
import json
from pathlib import Path
import subprocess
import sys
import threading
import unittest
import wave
import math
from unittest.mock import patch
from PIL import Image
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import sequence_nesting as nesting
import render
from render_context import RenderContext
from test_media_collection import run_async_check
import test_project_sync as store
import test_recipe_workflow as recipes


class StoreSequenceNesting(recipes.StoreRecipes):
    for _name in dir(recipes.StoreRecipes):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'_sequence_nest_candidate','_commit_sequence_nest','sequence_nest_review','sequence_nest','_source_command_policy',
               '_commit_sequence_creation','sequence_create'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        p=self.project();p['sequences'][0]['tracks'][0]['clips'][0]['out']=2;self.env['save_project'](p)

    def review(self,ids=None,**kw):
        return self.route('sequence_nest_review',{'_context':self.current(),'sequence':'s','clip_ids':ids or ['c'],'name':'Owned Nest','actor':'human','client':'nest-test',**kw})
    def apply(self,report,**kw):
        return self.route('sequence_nest',{'_context':report['context'],'sequence':report['sequence'],'clip_ids':report['clip_ids'],
            **report['settings'],'fingerprint':report['fingerprint'],'actor':'human','client':'nest-test',**kw})
    def image(self,project,at,name):
        path=self.root/(name+'.png')
        with RenderContext(scratch_parent=str(self.root)) as context:render.render_frame(project,'s',at,str(path),context=context)
        with Image.open(path) as value:return value.convert('RGB').tobytes()
    def pcm(self,project,name):
        path=self.root/(name+'.wav')
        with RenderContext(scratch_parent=str(self.root)) as context:render.render(project,'s',str(path),{'format':'audio','acodec':'wav_float'},context=context)
        raw=subprocess.run(['ffmpeg','-v','error','-i',str(path),'-f','f32le','-c:a','pcm_f32le','-'],capture_output=True,check=True,timeout=30).stdout
        result=array.array('f',raw)
        if sys.byteorder!='little':result.byteswap()
        return result

    def test_readonly_review_policy_locked_selection_and_routed_bus(self):
        before=(self.raw(),self.raw('b'));history=copy.deepcopy(self.env['read_undo_history']('a'))
        (self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
        report=self.review(actor='agent');self.assertTrue(report['ok']);self.assertEqual((self.raw(),self.raw('b')),before)
        with self.assertRaises(store.HTTPError):self.apply(report,actor='agent')
        self.assertEqual(self.env['read_undo_history']('a'),history)
        for index in (0,2):
            p=self.project();p['sequences'][0]['tracks'][index]['locked']=True;self.env['save_project'](p);before=self.raw();report=self.review()
            self.assertFalse(report['ok']);self.assertTrue(any('locked' in i['code'] for i in report['issues']))
            with self.assertRaises(store.HTTPError):self.apply(report)
            self.assertEqual(self.raw(),before)

    def test_one_complete_saved_transaction_exact_undo_redo_and_payloads(self):
        p=self.project();c=p['sequences'][0]['tracks'][0]['clips'][0]
        c.update(in_=1,out=5,speed=2,reverse=True,time_remap=[{'t':0,'v':2,'e':'linear'}],source_edit_window={'ramp':{'offset':0,'points':[{'t':0,'v':2}]}},
                 keyframes={'x':[{'t':0,'v':4,'e':'bezier','cp1':[.3,.2],'cp2':[.7,.9]}]})
        self.env['save_project'](p);before=self.project();reply=self.apply(self.review());after=self.project()
        self.assertTrue(reply['changed']);self.assertEqual(reply['source_clip_ids'],['c']);self.assertEqual(reply['sequence'],'s');self.assertEqual(after['media'],before['media'])
        child=next(s for s in after['sequences'] if s['id']==reply['child_sequence']);self.assertEqual(child['tracks'][0]['clips'][0],c)
        h=self.env['read_undo_history']('a');self.assertEqual(len(h['undo']),1);self.assertEqual(h['undo'][0]['tool'],'workflow_nest')
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_disjoint_gap_survives_later_normalizing_save_and_all_history(self):
        p=self.project();track=p['sequences'][0]['tracks'][0];c=track['clips'][0]
        track['clips']=[{**copy.deepcopy(c),'id':cid,'start':start,'out':1} for cid,start in [('a',0),('b',2),('c',4)]]
        self.env['save_project'](p);before=self.project();pixels=self.image(before,2.5,'before-gap');self.assertGreater(pixels[2],240)
        reply=self.apply(self.review(['a','c']));nested=self.project();self.assertEqual(len(reply['wrapper_clip_ids']),3)
        parent=nested['sequences'][0];self.assertIn('b',[c['id'] for c in parent['tracks'][0]['clips']]);self.assertEqual(self.image(nested,2.5,'after-gap'),pixels)
        self.edit('Unrelated project title',self.current());saved=self.project();self.assertIn('b',[c['id'] for c in saved['sequences'][0]['tracks'][0]['clips']])
        self.assertEqual(self.image(saved,2.5,'later-gap'),pixels)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],nested['sequences'])
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],saved['sequences'])

    def test_actual_track_and_master_gain_no_longer_apply_twice(self):
        p=self.project();seq=p['sequences'][0];seq['tracks'][2]['gain_db']=6;seq['master']={'gain_db':-3};self.env['save_project'](p)
        before=self.pcm(self.project(),'before-gain');self.apply(self.review());after=self.pcm(self.project(),'after-gain')
        self.assertEqual(len(before),len(after));self.assertGreater(max(abs(v) for v in before),.1)
        self.assertLessEqual(max(abs(a-b) for a,b in zip(before,after)),1e-7)

    def test_fractional_picture_origin_retains_frame_phase_and_offgrid_clip(self):
        p=self.project();seq=p['sequences'][0];seq['fps']=30000/1001;seq['tracks'][0]['clips'][0].update(start=.0501,in_=0,out=.12)
        self.env['save_project'](p);before=self.project();report=self.review();self.assertAlmostEqual(report['summary']['range']['start'],1001/30000)
        self.apply(report);after=self.project()
        for frame in (0,1,2,4,5):
            at=frame*1001/30000
            self.assertEqual(self.image(before,at,'fractional-before-'+str(frame)),self.image(after,at,'fractional-after-'+str(frame)),frame)

    def test_fractional_audio_origin_matches_independent_absolute_sample_rounding(self):
        # Native 48k impulse positions avoid a resampling or envelope oracle.
        path=self.root/'impulses.wav';values=array.array('h',[0])*(48000*2)
        source_positions=(200,900,2100)
        for index in source_positions:values[index*2]=20000;values[index*2+1]=-20000
        if sys.byteorder!='little':values.byteswap()
        with wave.open(str(path),'wb') as stream:stream.setparams((2,2,48000,0,'NONE',''));stream.writeframes(values.tobytes())
        movie=self.root/'impulses.mkv'
        subprocess.run(['ffmpeg','-v','error','-y','-f','lavfi','-i','color=blue:s=64x48:r=30:d=1','-i',str(path),'-map','0:v','-map','1:a','-c:v','ffv1','-threads','1','-c:a','pcm_s16le',str(movie)],check=True,capture_output=True,timeout=30)
        p=self.project();p['media']['m'].update(path=str(movie),duration=1,sample_rate=48000);seq=p['sequences'][0];seq['fps']=30000/1001
        clip=seq['tracks'][0]['clips'][0];clip.update(start=.05011,in_=0,out=.15,audio={'gain_db':0})
        self.env['save_project'](p);before=self.pcm(self.project(),'sample-before');report=self.review();origin=report['summary']['range']['start']
        self.apply(report);after=self.pcm(self.project(),'sample-after');self.assertEqual(len(before),len(after))
        located=lambda values:[i for i,v in enumerate(values[0::2]) if abs(v)>.5]
        nearest=lambda value:math.floor(value*48000+.5)
        expected_before=[nearest(clip['start'])+p for p in source_positions]
        expected_after=[nearest(origin)+nearest(clip['start']-origin)+p for p in source_positions]
        self.assertEqual(located(before),expected_before);self.assertEqual(located(after),expected_after)
        self.assertLessEqual(max(abs(a-b) for a,b in zip(expected_before,expected_after)),1)

    def test_foreign_revision_tamper_missing_owner_or_fingerprint_never_writes(self):
        report=self.review();before=(self.raw(),self.raw('b'))
        for change in ({'name':'Tampered'},{'clip_ids':['missing']},{'fingerprint':'a'*64},{'_context':None}):
            with self.assertRaises(store.HTTPError):self.apply(report,**change)
        with self.assertRaises(store.HTTPError):self.route('sequence_nest',{'_context':self.current(),'sequence':'s','clip_ids':['c']})
        with self.assertRaises(store.HTTPError):self.route('sequence_nest_review',{'sequence':'s','clip_ids':['c']})
        self.assertEqual((self.raw(),self.raw('b')),before)
        self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual((self.raw(),self.raw('b')),before);self.env['set_active_project']('a');self.edit('Other edit',self.current());before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual(self.raw(),before)

    def test_offloop_review_detects_project_and_apply_policy_changes(self):
        original=nesting.plan
        for policy in (False,True):
            report=self.review();started,release=threading.Event(),threading.Event()
            def delayed(*a,**k):value=original(*a,**k);started.set();release.wait(5);return value
            async def scenario():
                body={'_context':report['context'],'sequence':'s','clip_ids':['c'],**report['settings'],'fingerprint':report['fingerprint'],'actor':'agent'}
                before=(self.raw(),self.raw('b'));pending=asyncio.create_task(self.env['sequence_nest'](store.Request(body)))
                for _ in range(1000):
                    if started.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(started.is_set())
                if policy:(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
                else:self.env['set_active_project']('b')
                release.set()
                with self.assertRaises(store.HTTPError):await pending
                self.assertEqual((self.raw(),self.raw('b')),before)
            with patch.object(nesting,'plan',delayed):run_async_check(scenario())
            self.env['set_active_project']('a')

    def test_lost_reply_joins_commit_and_old_review_cannot_replay(self):
        report=self.review();entered=asyncio.Event();release=asyncio.Event();original=self.env['broadcast']
        async def delayed(event):entered.set();await release.wait()
        self.env['broadcast']=delayed
        async def scenario():
            body={'_context':report['context'],'sequence':'s','clip_ids':['c'],**report['settings'],'fingerprint':report['fingerprint']}
            pending=asyncio.create_task(self.env['sequence_nest'](store.Request(body)));await entered.wait();pending.cancel();await asyncio.sleep(.02)
            self.assertFalse(pending.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        try:run_async_check(scenario())
        finally:self.env['broadcast']=original
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(report)
        self.assertEqual(self.raw(),before)

    def test_partial_processed_bus_and_external_group_refuse_atomically(self):
        p=self.project();seq=p['sequences'][0];peer=copy.deepcopy(seq['tracks'][0]['clips'][0]);peer.update(id='peer',start=3)
        seq['tracks'][2].update(audio_fx={'comp':{'enabled':True}},clips=[peer]);self.env['save_project'](p);before=self.raw();r=self.review()
        self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='audio_bus_boundary' for i in r['issues']))
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before);self.assertTrue(self.review(['c','peer'])['ok'])


if __name__=='__main__':unittest.main()
