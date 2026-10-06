"""Real saved Flatten review/Apply, stale ownership, policies and exact history."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import multicam_flatten as flat
from test_media_collection import run_async_check
import test_project_sync as store
import test_sequence_nesting_workflow as nesting
from test_multicam_flatten import project as pure_project


class StoreMulticamFlatten(nesting.StoreSequenceNesting):
    for _name in dir(nesting.StoreSequenceNesting):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'_multicam_flatten_candidate','_commit_multicam_flatten','multicam_flatten_review','multicam_flatten_apply'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        p=self.project();new=pure_project();new['media']['m'].update(p['media']['m']);p.update(new);self.env['save_project'](p)

    def review(self,ids=None,**kw):return self.route('multicam_flatten_review',{'_context':self.current(),'sequence':'s','clip_ids':ids or ['outer'],'actor':'human','client':'flatten-test',**kw})
    def apply(self,r,**kw):return self.route('multicam_flatten_apply',{'_context':r['context'],'sequence':r['sequence'],'clip_ids':r['clip_ids'],'fingerprint':r['fingerprint'],'actor':'human','client':'flatten-test',**kw})

    def test_readonly_review_policy_and_lock_refusal_no_history(self):
        before=(self.raw(),self.raw('b'));h=copy.deepcopy(self.env['read_undo_history']('a'));r=self.review();self.assertTrue(r['ok'],r['issues']);self.assertEqual((self.raw(),self.raw('b')),before)
        (self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}');self.assertTrue(self.review(actor='agent')['ok'])
        with self.assertRaises(store.HTTPError):self.apply(r,actor='agent')
        self.assertEqual(self.env['read_undo_history']('a'),h)
        for index in (0,1):
            p=self.project();p['sequences'][0]['tracks'][index]['locked']=True;self.env['save_project'](p);before=self.raw();r=self.review();self.assertFalse(r['ok'])
            with self.assertRaises(store.HTTPError):self.apply(r)
            self.assertEqual(self.raw(),before)

    def test_one_saved_transaction_undo_redo_and_source_untouched(self):
        before=self.project();r=self.review();reply=self.apply(r);after=self.project()
        self.assertEqual(reply['summary'],r['summary']);self.assertEqual(reply['sequence'],'s');self.assertEqual(reply['source_clip_ids'],['outer'])
        self.assertEqual(after['media'],before['media']);self.assertEqual(after['sequences'][1:],before['sequences'][1:])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);self.assertEqual(self.env['read_undo_history']('a')['undo'][0]['tool'],'workflow_multicam_flatten')
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_actual_pcm_outer_gain_fades_parent_bus_master_and_duration(self):
        before=self.pcm(self.project(),'flatten-before');self.apply(self.review());after=self.pcm(self.project(),'flatten-after')
        self.assertEqual(len(before),len(after));self.assertGreater(max(abs(v) for v in before),.01)
        self.assertLessEqual(max(abs(a-b) for a,b in zip(before,after)),1e-6)

    def test_camera_gap_survives_later_patch_and_saved_undo(self):
        p=self.project();inner=p['sequences'][1]['tracks'][0]['clips'][0];inner.update(in_=0,out=1,speed=1)
        p['sequences'][1]['tracks'][0]['clips'].append({**copy.deepcopy(inner),'id':'later','start':2,'in_':3,'out':4});p['sequences'][0]['tracks'][0]['clips'][0].update(start=0,in_=0,out=3)
        self.env['save_project'](p);before=self.project();pix=self.image(before,1.5,'before-multicam-gap');self.assertEqual(max(pix),0)
        self.apply(self.review());after=self.project();self.assertEqual(self.image(after,1.5,'after-multicam-gap'),pix)
        self.edit('Unrelated saved edit',self.current());later=self.project();self.assertEqual(self.image(later,1.5,'later-multicam-gap'),pix)
        self.invoke('undo',{'_context':self.current()});self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],later['sequences'])

    def test_foreign_missing_owner_tamper_and_revision_never_write(self):
        r=self.review();before=(self.raw(),self.raw('b'))
        for changed in ({'_context':None},{'fingerprint':'a'*64},{'clip_ids':['missing']}):
            with self.assertRaises(store.HTTPError):self.apply(r,**changed)
        with self.assertRaises(store.HTTPError):self.route('multicam_flatten_review',{'sequence':'s','clip_ids':['outer']})
        with self.assertRaises(store.HTTPError):self.route('multicam_flatten_apply',{'_context':self.current(),'sequence':'s','clip_ids':['outer']})
        self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual((self.raw(),self.raw('b')),before);self.env['set_active_project']('a');self.edit('New edit',self.current());before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before)

    def test_offloop_review_owner_and_apply_policy_rechecked(self):
        original=flat.plan
        for policy in (False,True):
            r=self.review();started,release=threading.Event(),threading.Event()
            def delayed(*a,**k):value=original(*a,**k);started.set();release.wait(5);return value
            async def scenario():
                body={'_context':r['context'],'sequence':'s','clip_ids':['outer'],'fingerprint':r['fingerprint'],'actor':'agent'}
                before=(self.raw(),self.raw('b'));pending=asyncio.create_task(self.env['multicam_flatten_apply'](store.Request(body)))
                for _ in range(1000):
                    if started.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(started.is_set())
                if policy:(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
                else:self.env['set_active_project']('b')
                release.set()
                with self.assertRaises(store.HTTPError):await pending
                self.assertEqual((self.raw(),self.raw('b')),before)
            with patch.object(flat,'plan',delayed):run_async_check(scenario())
            self.env['set_active_project']('a')

    def test_lost_reply_joins_saved_commit_and_never_replays(self):
        r=self.review();entered=asyncio.Event();release=asyncio.Event();original=self.env['broadcast']
        async def delayed(event):entered.set();await release.wait()
        self.env['broadcast']=delayed
        async def scenario():
            body={'_context':r['context'],'sequence':'s','clip_ids':['outer'],'fingerprint':r['fingerprint']}
            pending=asyncio.create_task(self.env['multicam_flatten_apply'](store.Request(body)));await entered.wait();pending.cancel();await asyncio.sleep(.02)
            self.assertFalse(pending.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
        try:run_async_check(scenario())
        finally:self.env['broadcast']=original
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1);before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before)

    def test_malformed_or_unrepresentable_result_is_readonly(self):
        p=self.project();p['sequences'][0]['tracks'][0]['clips'][0]['reverse']=True;self.env['save_project'](p);before=self.raw();r=self.review()
        self.assertFalse(r['ok']);self.assertTrue(any(i['code']=='source_sampling' for i in r['issues']))
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo'])

if __name__=='__main__':unittest.main()
