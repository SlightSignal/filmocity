"""Actual saved selective transfer review, single Undo, resource and ownership guards."""
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import threading
import subprocess
import types
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import clip_attributes as attrs
from test_media_collection import run_async_check
import test_project_sync as store
import test_sequence_nesting_workflow as nesting

class StoreClipAttributes(nesting.StoreSequenceNesting):
    for _name in dir(nesting.StoreSequenceNesting):
        if _name.startswith('test_'):locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'_clip_attributes_candidate','_commit_clip_attributes','clip_attributes_review','clip_attributes_apply',
               '_stabilization_candidate','stabilization_inspect','stabilize'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.donor=self.bundle()
        self.donor['clip'].update(id='copied-donor',transform={'scale':1.25},audio={'gain_db':-6,'linked':False,'fade_in':.5,'constant_power':False})
    def bundle(self,clip=None):
        p=self.project();seq=p['sequences'][0];c=copy.deepcopy(clip or seq['tracks'][0]['clips'][0]);media=p['media'].get(c.get('media_id'))
        return {'version':1,'clip':c,'sequence':{k:seq[k] for k in ('id','width','height','fps')},'context':self.current(),'media':{k:copy.deepcopy(media[k]) for k in attrs.MEDIA_FIELDS if k in media} if media else None}
    def review(self,ids=None,**kw):
        return self.route('clip_attributes_review',{'_context':self.current(),'sequence':'s','clip_ids':ids or ['c'],'donor':copy.deepcopy(self.donor),'groups':list(attrs.DEFAULT_GROUPS),'include_animation':True,'timing':'seconds','actor':'human','client':'attribute-test',**kw})
    def apply(self,r,**kw):
        return self.route('clip_attributes_apply',{'_context':r['context'],'sequence':r['sequence'],'clip_ids':r['clip_ids'],'donor':r['donor'],**r['settings'],'fingerprint':r['fingerprint'],'actor':'human','client':'attribute-test',**kw})
    def clip(self):return self.project()['sequences'][0]['tracks'][0]['clips'][0]

    def test_saved_readonly_review_one_history_exact_undo_redo(self):
        before=self.project();raw=(self.raw(),self.raw('b'));r=self.review();self.assertTrue(r['ok'],r['issues']);self.assertEqual((self.raw(),self.raw('b')),raw)
        reply=self.apply(r);after=self.project();self.assertEqual(reply['summary'],r['summary']);self.assertEqual(reply['source_clip_id'],'copied-donor');self.assertEqual(reply['target_clip_ids'],['c']);self.assertNotEqual(reply['context'],r['context']);self.assertEqual(after['media'],before['media'])
        c=self.clip();self.assertNotEqual(c['audio'].get('linked'),False);self.assertEqual(c['audio']['gain_db'],-6)
        history=self.env['read_undo_history']('a')['undo'];self.assertEqual(len(history),1);self.assertEqual(history[0]['tool'],'workflow_clip_attributes')
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])
    def test_true_noop_has_no_commit_history_or_revision(self):
        self.donor=self.bundle();before=self.raw();r=self.review();self.assertEqual(r['summary']['changed_clip_ids'],[]);reply=self.apply(r);self.assertFalse(reply['changed']);self.assertEqual(reply['context'],r['context']);self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a')['undo'],[])
    def test_lock_refusal_and_agent_policy_review_only(self):
        p=self.project();p['sequences'][0]['tracks'][0]['locked']=True;self.env['save_project'](p);before=self.raw();r=self.review();self.assertFalse(r['ok'])
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before);p['sequences'][0]['tracks'][0]['locked']=False;self.env['save_project'](p)
        (self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}');r=self.review(actor='agent');self.assertTrue(r['ok'])
        with self.assertRaises(store.HTTPError):self.apply(r,actor='agent')
        self.assertEqual(self.env['read_undo_history']('a')['undo'],[])
    def test_missing_foreign_stale_tampered_owner_never_writes(self):
        r=self.review();before=(self.raw(),self.raw('b'))
        for change in ({'_context':None},{'fingerprint':'0'*64},{'donor':dict(self.donor,clip=dict(self.donor['clip'],transform={'scale':3}))}):
            with self.assertRaises(store.HTTPError):self.apply(r,**change)
        self.env['set_active_project']('b')
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual((self.raw('a'),self.raw('b')),before);self.env['set_active_project']('a');self.edit('new revision',self.current());before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before)
    def test_frozen_donor_edits_deletion_and_other_overlaps_untouched(self):
        p=self.project();tr=p['sequences'][0]['tracks'][0];tr['clips'].append(dict(copy.deepcopy(tr['clips'][0]),id='overlap',start=.5));self.env['save_project'](p);before=self.project();self.apply(self.review());after=self.project()
        self.assertEqual(after['sequences'][0]['tracks'][0]['clips'][1],before['sequences'][0]['tracks'][0]['clips'][1]);self.assertEqual(after['media'],before['media']);self.assertEqual(self.clip()['transform'],{'scale':1.25})
    def test_equal_visible_duck_clears_hidden_history_one_undo(self):
        p=self.project();c=p['sequences'][0]['tracks'][0]['clips'][0];curve=[{'t':0,'v':0,'e':'linear'}];c.update(keyframes={'audio.duck_db':curve},source_edit_window={'version':1,'duck':{'offset':2,'points':[{'t':0,'v':-24},{'t':2,'v':0}]},'ramp':{'offset':9}});self.env['save_project'](p)
        self.donor=self.bundle();self.donor['clip']['id']='deleted-donor';r=self.review(groups=['audio_gain']);self.assertEqual(r['summary']['changed_clip_ids'],['c']);self.apply(r);self.assertEqual(self.clip()['source_edit_window'],{'version':1,'ramp':{'offset':9}})
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.clip()['source_edit_window'],c['source_edit_window'])
    def test_lut_changed_since_review_refuses_and_unchanged_resource_saves(self):
        path=self.root/'owned.cube';path.write_text('TITLE owned');self.donor['clip']['color']={'lut':str(path)};r=self.review(groups=['color']);before=self.raw();path.write_text('TITLE altered')
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before);r=self.review(groups=['color']);self.apply(r);self.assertEqual(self.clip()['color']['lut'],str(path))
    def test_offloop_review_and_apply_recheck_owner_and_policy(self):
        original=attrs.inspect
        for policy in (False,True):
            r=self.review();started,release=threading.Event(),threading.Event()
            def delayed(*a,**kw):out=original(*a,**kw);started.set();release.wait(5);return out
            async def scenario():
                body={'_context':r['context'],'sequence':'s','clip_ids':['c'],'donor':r['donor'],**r['settings'],'fingerprint':r['fingerprint'],'actor':'agent'};before=(self.raw(),self.raw('b'))
                pending=asyncio.create_task(self.env['clip_attributes_apply'](store.Request(body)))
                for _ in range(1000):
                    if started.is_set():break
                    await asyncio.sleep(.001)
                self.assertTrue(started.is_set())
                if policy:(self.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
                else:self.env['set_active_project']('b')
                release.set()
                with self.assertRaises(store.HTTPError):await pending
                self.assertEqual((self.raw('a'),self.raw('b')),before)
            with patch.object(attrs,'inspect',delayed):run_async_check(scenario())
            self.env['set_active_project']('a');(self.root/'settings.json').write_text('{}')
    def test_actual_analyze_emits_ascii_consumable_by_stabilized_renderer(self):
        import stabilization
        calls=[]
        original=stabilization.subprocess.Popen
        def process(args,**kw):
            calls.append(list(args));return original(args,**kw)
        body={'media_id':'m','force':True,'shakiness':5,'_context':self.current(),'actor':'human'}
        review=self.route('stabilization_inspect',body)
        with patch.object(stabilization.subprocess,'Popen',process):
            result=self.route('stabilize',dict(body,fingerprint=review['fingerprint']))
        self.assertEqual(len(calls),1);self.assertIn('fileformat=ascii',calls[0][calls[0].index('-vf')+1]);data=Path(result['trf']).read_bytes();self.assertTrue(data.startswith(b'VID.STAB'))
        p=self.project();c=p['sequences'][0]['tracks'][0]['clips'][0];c.update(in_=2,out=4,reverse=True,fx_stack=[{'type':'stabilize','params':{'smoothing':0,'zoom':0,'crop':'keep'}}]);pixels=self.image(p,.5,'route-ascii-stabilized')
        self.assertGreater(max(pixels),200)

    def test_stabilization_known_source_identity_rejects_replaced_original(self):
        from task_inputs import source_stamp
        p=self.project();m=p['media']['m'];trf=self.root/'owned.trf';trf.write_text('captured analysis');m['stab_trf']=str(trf);m['proxy_info']={'source_signature':attrs.digest(source_stamp(m))};self.env['save_project'](p)
        self.donor=self.bundle();self.donor['clip']['fx_stack']=[{'type':'stabilize'}];r=self.review(groups=['video_effects']);self.assertTrue(r['ok']);before=self.raw()
        with open(m['path'],'ab') as stream:stream.write(b'changed')
        with self.assertRaises(store.HTTPError):self.review(groups=['video_effects'])
        self.assertEqual(self.raw(),before)

    def test_resource_change_after_candidate_before_commit_refuses(self):
        path=self.root/'late.cube';path.write_text('TITLE initial');self.donor['clip']['color']={'lut':str(path)};r=self.review(groups=['color']);before=self.raw();original=attrs.inspect
        def mutate(*a,**kw):value=original(*a,**kw);path.write_text('TITLE changed after inspection');return value
        with patch.object(attrs,'inspect',mutate):
            with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a')['undo'],[])

    def test_cancel_after_commit_joins_ack_without_duplicate_transaction(self):
        r=self.review();entered=asyncio.Event();release=asyncio.Event()
        async def delayed(event):entered.set();await release.wait()
        async def scenario():
            self.env['broadcast']=delayed
            body={'_context':r['context'],'sequence':'s','clip_ids':r['clip_ids'],'donor':r['donor'],**r['settings'],'fingerprint':r['fingerprint']}
            pending=asyncio.create_task(self.env['clip_attributes_apply'](store.Request(body)))
            await entered.wait();pending.cancel();await asyncio.sleep(.002);self.assertFalse(pending.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
            self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
            with self.assertRaises(store.HTTPError):await self.env['clip_attributes_apply'](store.Request(body))
        run_async_check(scenario())

    def test_cancellation_joins_review_and_lost_reply_does_not_replay(self):
        original=attrs.inspect;started,release,finished=threading.Event(),threading.Event(),threading.Event()
        def delayed(*a,**kw):started.set();release.wait(5);finished.set();return original(*a,**kw)
        async def cancel():
            body={'_context':self.current(),'sequence':'s','clip_ids':['c'],'donor':self.donor};before=self.raw();pending=asyncio.create_task(self.env['clip_attributes_review'](store.Request(body)))
            for _ in range(1000):
                if started.is_set():break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set());pending.cancel();await asyncio.sleep(.002);self.assertFalse(pending.done());release.set()
            with self.assertRaises(asyncio.CancelledError):await pending
            self.assertTrue(finished.is_set());self.assertEqual(self.raw(),before)
        with patch.object(attrs,'inspect',delayed):run_async_check(cancel())
        r=self.review();self.apply(r);after=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(r)
        self.assertEqual(self.raw(),after);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

if __name__=='__main__':unittest.main()
