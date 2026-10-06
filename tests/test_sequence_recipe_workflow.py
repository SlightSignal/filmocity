"""Canonical saved Explainer/Variants queues, review and one guarded Undo."""
import ast
import copy
import json
from pathlib import Path
import sys
import threading
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
import recipe_workflow as recipe
from background_tasks import TaskManager,TaskContext
import test_recipe_workflow as recipes
import test_project_sync as store


class StoreSequenceRecipes(recipes.StoreRecipes):
    for _name in dir(recipes.StoreRecipes):
        if _name.startswith('test_'): locals()[_name]=None

    def setUp(self):
        super().setUp()
        names={'recipe_explainer','sequence_variants'}
        nodes=[n for n in ast.parse((ROOT/'backend/server.py').read_text()).body if getattr(n,'name',None) in names]
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        project=self.project();seq=project['sequences'][0]
        seq['tracks'][1]['clips']=[{'id':'hook','media_id':None,'start':0,'in_':0,'out':2,'speed':1,
            'graphic':{'name':'Authored Hook','layers':[{'kind':'text','text':'Original hook','size':20},
                {'kind':'text','text':'Speaker name','size':12}]},'custom':{'keep':True}}]
        self.env['save_project'](project)

    def body(self,mode='explainer',**kw):
        options={'lower_third':{'name':'Editor','role':'Filmocity','at':0,'duration':2},'chapters':False,'end_card':'Subscribe'} if mode=='explainer' else {
            'hooks':['First headline','Second headline'],'targets':[{'clip_id':'hook','layer':0}]}
        return {'_context':self.current(),'sequence':'s','request_id':'a'*32,**options,**kw}

    def ready(self,mode='explainer',**kw):
        name='recipe_explainer' if mode=='explainer' else 'sequence_variants'
        identity=self.route(name,self.body(mode,**kw))['task']['id'];value=self.manager.values[identity]
        self.env['_task_recipe_workflow'](value['payload'],TaskContext(self.manager,identity));self.manager.store.save(value)
        return identity

    def test_explainer_preserves_old_locked_overlaps_and_atomic_undo(self):
        project=self.project();seq=project['sequences'][0];track=seq['tracks'][0];track['locked']=True
        track['clips'].append({**copy.deepcopy(track['clips'][0]),'id':'overlap','start':1,'out':3})
        self.env['save_project'](project);before=self.project();identity=self.ready();review=self.review(identity)
        self.assertEqual(self.project(),before);self.assertTrue(review['plan']['ops'])
        self.apply(identity,review,actor='human');after=self.project()
        self.assertEqual(after['sequences'][0]['tracks'][:len(seq['tracks'])],seq['tracks'])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_variants_explicit_text_full_payload_and_one_history_commit(self):
        before=self.project();identity=self.ready('variants');review=self.review(identity);self.apply(identity,review)
        after=self.project();self.assertEqual(after['sequences'][0],before['sequences'][0]);self.assertEqual(len(after['sequences']),3)
        all_ids=[]
        for index,seq in enumerate(after['sequences'][1:]):
            clip=seq['tracks'][1]['clips'][0];self.assertEqual(clip['graphic']['layers'][0]['text'],('First\nheadline','Second\nheadline')[index])
            self.assertEqual(clip['graphic']['layers'][1]['text'],'Speaker name');self.assertEqual(clip['custom'],{'keep':True})
            all_ids.extend(c['id'] for t in seq['tracks'] for c in t['clips'])
        self.assertEqual(len(all_ids),len(set(all_ids)));self.assertNotIn('hook',all_ids)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],before['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.project()['sequences'],after['sequences'])

    def test_foreign_and_changed_target_fail_without_touching_either_owner(self):
        identity=self.ready('variants');review=self.review(identity);before=self.raw();old_context=self.current()
        other=self.project();other['sequences'][0]['tracks'][1]['locked']=True
        self.env['set_active_project']('b');self.env['save_project'](other);foreign=self.raw()
        for action in (lambda:self.review(identity),lambda:self.apply(identity,review),lambda:self.route('sequence_variants',self.body('variants',_context=old_context))):
            with self.assertRaises(store.HTTPError):action()
        self.assertEqual(self.raw(),foreign);self.assertEqual(self.raw('a'),before);self.assertFalse(self.env['read_undo_history']('b')['undo'])
        self.env['set_active_project']('a');project=self.project();project['sequences'][0]['tracks'][1]['locked']=True;self.env['save_project'](project)
        with self.assertRaises(store.HTTPError):self.review(identity)

    def test_nested_dependencies_and_fonts_are_captured_and_changed_input_refused(self):
        project=self.project();child=copy.deepcopy(project['sequences'][0]);child.update(id='child',name='Nested')
        project['sequences'][0]['tracks'][0]['clips']=[{'id':'nest','sequence_id':'child','start':0,'in_':0,'out':6,'speed':1}]
        project['sequences'].append(child);self.env['save_project'](project)
        identity=self.ready('variants');payload=self.manager.get(identity)['payload'];self.assertIn('child',payload['sequence_dependencies'])
        self.assertTrue(any(resource[0].lower().endswith(('.ttf','.otf')) for resource in payload['resources']))
        self.assertTrue(any(resource[0]==str(self.source) for resource in payload['resources']))
        project=self.project();project['sequences'][1]['tracks'][1]['clips'][0]['graphic']['layers'][1]['text']='Changed child';self.env['save_project'](project)
        with self.assertRaises(store.HTTPError):self.review(identity)

    def test_exact_review_revision_and_fingerprint_and_agent_policy(self):
        identity=self.ready();review=self.review(identity);project=self.project();project['name']='Unrelated edit';self.env['save_project'](project)
        with self.assertRaises(store.HTTPError):self.apply(identity,review)
        review=self.review(identity);before=self.raw()
        with self.assertRaises(store.HTTPError):self.apply(identity,review,fingerprint='wrong')
        (self.root/'settings.json').write_text(json.dumps({'agent_mode':'proposals_only'}))
        with self.assertRaises(store.HTTPError):self.apply(identity,review,actor='agent')
        self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo'])
        self.apply(identity,review,actor='human');self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_noop_explainer_never_creates_history(self):
        before=self.raw();identity=self.ready(lower_third=None,chapters=False,end_card='');review=self.review(identity)
        self.assertEqual(review['plan']['ops'],[]);result=self.apply(identity,review)
        self.assertFalse(result['changed']);self.assertEqual(self.raw(),before);self.assertFalse(self.env['read_undo_history']('a')['undo'])

    def test_lost_apply_receipt_and_restart_do_not_duplicate_variants(self):
        identity=self.ready('variants');review=self.review(identity);actual=self.manager.store.save
        def fail(value):
            if value['record']['status']=='applied':raise OSError('Injected lost task receipt')
            return actual(value)
        with patch.object(self.manager.store,'save',fail):self.apply(identity,review)
        after=self.project();restored=TaskManager(self.root,{'recipe':recipe.analyze},start=False);self.addCleanup(restored.shutdown);self.env['TASKS']=restored
        self.route('background_task_apply',{'_context':self.current()},identity)
        self.assertEqual(self.project(),after);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_cancelled_worker_and_restart_never_apply_or_replay(self):
        identity=self.route('sequence_variants',self.body('variants'))['task']['id'];self.manager.cancel(identity);before=self.raw()
        task=TaskContext(self.manager,identity);task.holder['cancelled']=True
        with self.assertRaises(RuntimeError):recipe.analyze(self.manager.get(identity)['payload'],task)
        restored=TaskManager(self.root,{'recipe':recipe.analyze},start=False);self.addCleanup(restored.shutdown)
        self.assertEqual(restored.get(identity)['record']['status'],'cancelled');self.assertEqual(self.raw(),before)
        reply=self.route('background_task_retry',{'_context':self.current(),'request_id':'b'*32},identity)
        self.assertNotEqual(reply['task']['id'],identity)

    def test_admission_capture_offloop_and_no_queue_after_project_switch(self):
        actual=recipe.capture;checks=[]
        def capture(*args):
            checks.append((threading.current_thread() is threading.main_thread(),self.env['LOCK'].locked()))
            value=actual(*args);self.env['set_active_project']('b');return value
        with patch.object(recipe,'capture',capture),self.assertRaises(store.HTTPError):self.route('recipe_explainer',self.body())
        self.assertEqual(checks,[(False,False)]);self.assertFalse(self.manager.values)

    def test_queue_limits_explicit_targets_and_resources_fail_early(self):
        before=self.raw()
        for patch_ in ({'hooks':['x']*21},{'targets':[]},{'targets':[{'clip_id':'hook','layer':9}]},{'hooks':['']},{'request_id':'no'}):
            with self.subTest(patch_=patch_),self.assertRaises(store.HTTPError):self.route('sequence_variants',self.body('variants',**patch_))
        self.assertEqual(self.raw(),before);self.assertFalse(self.manager.values)
        self.source.unlink()
        with self.assertRaises(store.HTTPError):self.route('recipe_explainer',self.body())


if __name__=='__main__':unittest.main()
