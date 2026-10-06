"""Production task routes, real stores/threads/history; framework and ASR adapters.

No HTTP listener, speech model, native browser or Windows acceptance is implied.
"""
import ast
import asyncio
import copy
import json
from pathlib import Path
import sys
import threading
import types
import unittest
from unittest.mock import patch

import test_project_sync as store
from test_background_tasks import wait_for
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
from background_tasks import TaskManager, TaskError
from media_collection import MediaCollectionError
import task_inputs


class Response(store.Response):
    def __init__(self,body,status_code=200,headers=None):super().__init__(body,status_code);self.headers=headers or {}


class Routes(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        async def in_thread(fn,*args,**kwargs):return fn(*args,**kwargs)
        names={'_workflow_capture','_workflow_commit','_task_transcribe','_task_package','_queue_package','background_package','background_package_import','_owned_task','background_task_list',
               'background_transcribe','background_task_cancel','background_task_result','background_task_retry',
               'background_media_prepare','background_task_apply','_task_media_current','_task_media_update','finish_ingest','_update_ingested_media'}
        tree=ast.parse((ROOT/'backend/server.py').read_text());nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        self.env.update(task_inputs=task_inputs,TaskError=TaskError,MediaCollectionError=MediaCollectionError,sys=sys,asyncio=types.SimpleNamespace(to_thread=in_thread),JSONResponse=Response,broadcast_threadsafe=lambda *a:None)
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        self.manager=TaskManager(self.root,{'transcribe':self.env['_task_transcribe'],'media':lambda p,c:{'prepared':True},'package':self.env['_task_package'],'package_import':self.env['_task_package']})
        self.env['TASKS']=self.manager
        self.speech_calls=[]
        def speech(*args,**kw):self.speech_calls.append(args);return [{'w':'Hello.', 's':.1,'e':.5}],[]
        self.env['_transcribe_sequence']=speech
        proj=self.env['load_project']()
        for identity,media in proj['media'].items():
            path=self.root/(identity+'.wav');path.write_bytes(b'source '+identity.encode());media['path']=str(path);media['ingest_token']='source-'+identity
        self.env['save_project'](proj)
        self.speech_patch=patch.dict(sys.modules,{'faster_whisper':types.ModuleType('faster_whisper')});self.speech_patch.start()
    def tearDown(self):self.manager.shutdown(5);self.speech_patch.stop();super().tearDown()
    def request(self,route,identity=None,**body):
        req=store.Request({'_context':self.current(),**body})
        return asyncio.run(self.env[route](identity,req) if identity else self.env[route](req))
    def submit(self,**body):return self.request('background_transcribe',sequence='seq1',**body)['task']['id']
    def ready(self):
        identity=self.submit();self.assertEqual(wait_for(self.manager,identity)['record']['status'],'ready');return identity
    def mutate(self,fn):
        p=self.env['load_project']();fn(p);self.env['save_project'](p)

    def test_analysis_returns_saved_result_without_project_history_mutation(self):
        before=self.raw();identity=self.ready();self.assertEqual(self.raw(),before)
        self.assertFalse((self.root/'projects/a/undo_stack.json').exists());self.assertEqual(len(self.speech_calls),1)
        result=self.env['background_task_result'](identity);self.assertEqual(result['result']['words'][0]['w'],'Hello.')
        self.assertIn('attachment',result.headers['Content-Disposition'])

    def test_apply_is_one_undo_step_and_preserves_manual_captions(self):
        self.mutate(lambda p:p['sequences'][0].update(captions=[{'id':'manual','text':'A rewrite','start':0,'end':1}]))
        before=json.loads(self.raw());identity=self.ready();reply=self.request('background_task_apply',identity)
        self.assertTrue(reply['ok']);self.assertEqual(reply['context'],self.current());after=json.loads(self.raw())
        self.assertEqual(after['sequences'][0]['captions'],before['sequences'][0]['captions']);self.assertEqual(after['sequences'][0]['workflow']['caption_review_ids'],['manual'])
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        self.invoke('undo',{'_context':self.current()});self.assertNotIn('transcript',json.loads(self.raw())['sequences'][0])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(json.loads(self.raw())['sequences'][0]['transcript'],after['sequences'][0]['transcript'])

    def test_apply_and_receipt_reply_retries_are_idempotent(self):
        identity=self.ready()
        with patch.object(self.manager.store,'save',wraps=self.manager.store.save) as save:
            # Fail the final receipt, after the applying checkpoint and project commit.
            original=self.manager.store.save
            def fail_final(value):
                if value['record']['status']=='applied':raise OSError('sharing denied')
                return save._mock_wraps(value)
            save.side_effect=fail_final
            reply=self.request('background_task_apply',identity);self.assertIn('history',reply['warning'])
        before=self.raw();reply=self.request('background_task_apply',identity)
        self.assertEqual(reply['message'],'Transcript already applied');self.assertEqual(self.raw(),before)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)

    def test_unrelated_edits_and_derived_media_do_not_invalidate_result_or_dedup(self):
        identity=self.ready()
        self.mutate(lambda p:(p.update(name='New title'),p['sequences'][0].update(markers=[{'id':'m','time':1}],captions=[]),p['media']['A'].update(proxy='/proxies/new.mp4',task_id='other')))
        self.assertEqual(self.submit(),identity);self.assertEqual(len(self.speech_calls),1)
        self.assertTrue(self.request('background_task_apply',identity)['ok'])

    def test_target_edit_rejects_apply_without_losing_downloadable_result(self):
        identity=self.ready();self.mutate(lambda p:p['sequences'][0]['tracks'][0]['clips'][0]['audio'].update(gain_db=9));before=self.raw()
        with self.assertRaises(store.HTTPError) as error:self.request('background_task_apply',identity)
        self.assertEqual(error.exception.status_code,409);self.assertEqual(self.raw(),before)
        self.assertEqual(self.manager.get(identity)['record']['status'],'ready');self.assertTrue(self.env['background_task_result'](identity)['result']['words'])

    def test_correction_or_external_source_change_rejects_apply(self):
        for change in (lambda p:p['sequences'][0].update(transcript=[{'w':'Mine','s':0,'e':.2}]),lambda p:Path(p['media']['A']['path']).write_bytes(b'changed file')):
            identity=self.ready();self.mutate(change);before=self.raw()
            with self.assertRaises(store.HTTPError):self.request('background_task_apply',identity)
            self.assertEqual(self.raw(),before)

    def test_project_switch_blocks_cancel_apply_retry_and_download(self):
        identity=self.ready();self.env['set_active_project']('b');before=self.raw('b')
        for route in ('background_task_cancel','background_task_result'):
            with self.assertRaises(store.HTTPError):self.env[route](identity)
        for route in ('background_task_apply','background_task_retry'):
            with self.assertRaises(store.HTTPError):self.request(route,identity)
        self.assertEqual(self.env['background_task_list']()['tasks'],[]);self.assertEqual(self.raw('b'),before)

    def test_missing_model_and_invalid_model_never_queue(self):
        with patch.dict(sys.modules,{'faster_whisper':None}),self.assertRaises(store.HTTPError) as error:self.submit()
        self.assertEqual(error.exception.status_code,501)
        with self.assertRaises(store.HTTPError):self.submit(model='arbitrary/path')
        self.assertEqual(self.manager.catalog()['tasks'],[])

    def test_failed_task_explicit_retry_and_changed_input_refusal(self):
        self.env['_transcribe_sequence']=lambda *a,**k:(_ for _ in ()).throw(ValueError('speech failure'))
        identity=self.submit();self.assertEqual(wait_for(self.manager,identity)['record']['status'],'error')
        self.env['_transcribe_sequence']=lambda *a,**k:([{'w':'Retry','s':.1,'e':.5}],[])
        retry=self.request('background_task_retry',identity)['task']['id'];self.assertNotEqual(identity,retry);self.assertEqual(wait_for(self.manager,retry)['record']['status'],'ready')
        self.mutate(lambda p:p['sequences'][0]['tracks'][0]['clips'][0].update(out=2))
        with self.assertRaises(store.HTTPError):self.request('background_task_retry',identity)

    def test_commit_conflict_returns_result_to_ready_and_keeps_project(self):
        identity=self.ready();before=self.raw()
        async def conflict(*a,**k):raise store.HTTPError(409,'project changed')
        self.env['_workflow_commit']=conflict
        with self.assertRaises(store.HTTPError):self.request('background_task_apply',identity)
        self.assertEqual(self.manager.get(identity)['record']['status'],'ready');self.assertEqual(self.raw(),before)

    def test_admission_failure_keeps_imported_original_and_can_be_prepared_again(self):
        before=Path(self.env['load_project']()['media']['A']['path']).read_bytes()
        with patch.object(self.manager.store,'save',side_effect=OSError('disk full')),self.assertRaises(store.HTTPError):self.request('background_media_prepare','A')
        media=self.env['load_project']()['media']['A'];self.assertIn('disk full',media['ingest_error']);self.assertEqual(Path(media['path']).read_bytes(),before)
        reply=self.request('background_media_prepare','A');self.assertEqual(wait_for(self.manager,reply['task']['id'])['record']['status'],'done')

    def test_queued_media_cancel_updates_only_its_captured_source(self):
        self.manager.shutdown();self.manager=TaskManager(self.root,{'media':lambda p,c:{}},start=False);self.env['TASKS']=self.manager
        reply=self.request('background_media_prepare','A');identity=reply['task']['id']
        result=self.env['background_task_cancel'](identity);self.assertEqual(result['task']['status'],'cancelled')
        media=self.env['load_project']()['media']['A'];self.assertEqual(media['status'],'cancelled');self.assertEqual(media['task_id'],identity)

    def test_proxy_preferences_are_captured_and_competing_rebuild_cannot_replace_active_work(self):
        self.manager.shutdown();self.manager=TaskManager(self.root,{'media':lambda p,c:{}},start=False);self.env['TASKS']=self.manager
        settings=self.root/'settings.json';settings.write_text(json.dumps({'prefs':{'proxy':{'max_edge':640,'quality':'draft'}}}))
        first=self.request('background_media_prepare','A')['task']['id'];before=self.raw()
        settings.write_text(json.dumps({'prefs':{'proxy':{'max_edge':1920,'quality':'high'}}}))
        with self.assertRaises(store.HTTPError):self.request('background_media_prepare','A')
        self.assertEqual(self.raw(),before)
        self.assertEqual(self.manager.get(first)['payload']['proxy_settings'],{'max_edge':640,'quality':'draft'})
        self.env['background_task_cancel'](first)
        second=self.request('background_media_prepare','A')['task']['id']
        self.assertEqual(self.manager.get(second)['payload']['proxy_settings'],{'max_edge':1920,'quality':'high'})
        with self.assertRaises(store.HTTPError):self.request('background_task_retry',first)

    def test_changed_prepared_source_requires_relink_before_new_preparation(self):
        from media_preview import digest
        self.mutate(lambda p:p['media']['A'].update(proxy_info={'source_signature':digest(task_inputs.source_stamp(p['media']['A']))}))
        source=Path(self.env['load_project']()['media']['A']['path']);source.write_bytes(b'changed source bytes')
        with self.assertRaises(store.HTTPError) as error:self.request('background_media_prepare','A')
        self.assertIn('Relink',str(error.exception.detail));self.assertEqual(self.manager.catalog()['tasks'],[])
        self.assertEqual(source.read_bytes(),b'changed source bytes')

    def test_invalid_proxy_preferences_leave_a_visible_recoverable_import_error(self):
        (self.root/'settings.json').write_text(json.dumps({'prefs':{'proxy':{'max_edge':99}}}))
        before=Path(self.env['load_project']()['media']['A']['path']).read_bytes()
        with self.assertRaises(store.HTTPError):self.request('background_media_prepare','A')
        media=self.env['load_project']()['media']['A'];self.assertIn('proxy preferences',media['ingest_error'])
        self.assertEqual(Path(media['path']).read_bytes(),before)

    def test_source_changed_during_speech_is_failed_and_not_applied(self):
        before=self.raw()
        def changed(*a,**kw):Path(self.env['load_project']()['media']['A']['path']).write_bytes(b'replaced');return [{'w':'late','s':.1,'e':.5}],[]
        self.env['_transcribe_sequence']=changed;identity=self.submit();self.assertEqual(wait_for(self.manager,identity)['record']['status'],'error');self.assertEqual(self.raw(),before)


    def test_package_routes_capture_project_and_import_to_new_folder_without_switch(self):
        before=self.raw();dest=self.root/'transfer'
        reply=self.request('background_package',path=str(dest));task=wait_for(self.manager,reply['task']['id'])
        self.assertEqual(task['record']['status'],'done',task);self.assertEqual(self.raw(),before)
        exported=task['result'];self.assertTrue(Path(exported['manifest']).is_file())
        reply=self.request('background_package_import',path=exported['manifest']);task=wait_for(self.manager,reply['task']['id'])
        self.assertEqual(task['record']['status'],'done',task);self.assertEqual(self.raw(),before)
        self.assertEqual(self.env['active_id'](),'a');self.assertTrue((Path(task['result']['folder'])/'project.json').is_file())
        response=self.env['background_task_result'](reply['task']['id']);self.assertIn('package-receipt',response.headers['Content-Disposition'])

    def test_package_admission_rejects_stale_context_and_relative_paths(self):
        self.mutate(lambda p:p.update(name='Changed'))
        for body in ({'path':'relative'}, {'path':str(self.root),'_context':{'workspace':'wrong','project':'a','revision':'wrong'}}):
            with self.assertRaises(store.HTTPError):self.request('background_package',**body)
        self.assertEqual(self.manager.catalog()['tasks'],[])

    def test_package_retry_keeps_captured_edit_and_uses_a_fresh_publication_folder(self):
        entered=threading.Event();release=threading.Event();self.addCleanup(release.set)
        real=self.env['_task_package']
        def fail(payload,task):entered.set();release.wait(3);raise OSError('temporary offline disk')
        self.manager.handlers['package']=fail
        identity=self.request('background_package',path=str(self.root/'transfer'))['task']['id'];self.assertTrue(entered.wait(2))
        old_name=self.env['load_project']()['name'];self.mutate(lambda p:p.update(name='Later edit'));release.set();wait_for(self.manager,identity)
        self.manager.handlers['package']=real
        retry=self.request('background_task_retry',identity)['task']['id'];self.assertNotEqual(retry,identity)
        task=wait_for(self.manager,retry);self.assertEqual(task['record']['status'],'done',task)
        captured=json.loads((Path(task['result']['folder'])/'project.json').read_text());self.assertEqual(captured['name'],old_name)
        self.assertEqual(self.env['load_project']()['name'],'Later edit')


class Inputs(unittest.TestCase):
    def test_nested_dependencies_and_transcript_are_frozen_and_checked(self):
        p={'version':3,'media':{'m':{'id':'m','synthetic':True}},'sequences':[
            {'id':'parent','tracks':[{'clips':[{'sequence_id':'nested'}]}]},
            {'id':'nested','tracks':[{'clips':[{'media_id':'m','start':0}]}]},
            {'id':'other','tracks':[]}]}
        snapshot,signature=task_inputs.capture(p,'parent');self.assertEqual(len(snapshot['sequences']),2)
        p['sequences'][2]['name']='unrelated';self.assertEqual(task_inputs.capture(p,'parent')[1],signature)
        p['sequences'][1]['tracks'][0]['clips'][0]['start']=1
        self.assertNotEqual(task_inputs.capture(p,'parent')[1],signature);self.assertEqual(snapshot['sequences'][1]['tracks'][0]['clips'][0]['start'],0)
        p['sequences'][1]['tracks'][0]['clips'].append({'sequence_id':'parent'})
        with self.assertRaisesRegex(ValueError,'cycle'):task_inputs.capture(p,'parent')



if __name__=='__main__':unittest.main()
