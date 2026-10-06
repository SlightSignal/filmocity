"""Owned source-creation SDK transport; saved-store cases added below separately."""
import copy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'agent'))
from filmocity_client import Filmocity, FilmocityError


class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='agent',client='source-creation-test')
        self.owner={'workspace':'workspace / A','project':'project & one','revision':'saved-1'}
        self.document={'media':{'m':{'id':'m','has_audio':True},'sub/sl~ç':{'id':'sub/sl~ç','has_audio':True},'existing':{'id':'existing'}}}
        self.calls=[];self.switch=False;self.failure=False;self.change=lambda value:None
    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':
            value={'project':copy.deepcopy(self.document),'context':copy.deepcopy(self.owner)}
            if self.switch:self.owner['project']='new project'
            return value
        if self.failure:raise FilmocityError('Committed reply may be lost; inspect saved state')
        mode=path.rsplit('/',1)[-1];assert mode in ('subclip','breakout','duplicate')
        sources=body['media_ids'] if mode=='duplicate' else [body['media_id']]
        count=len(sources) if mode=='duplicate' else 2 if mode=='breakout' else 1
        ids=['created-'+str(n) for n in range(count)]
        value={'ok':True,'changed':True,'project':body['_context']['project'],'context':{**body['_context'],'revision':'saved-2'},
            'kind':'source_creation','mode':mode,'media_ids':ids,'media':[{'id':mid,'name':mid,'status':'unprepared'} for mid in ids],
            'summary':{'kind':'source_creation','mode':mode,'source_media_ids':sources,'created_media_ids':list(ids),'count':count,'message':'Created'},
            'warnings':[],'preparation':{'tasks':[],'warnings':[]}}
        self.change(value);return value


class SourceCreationSDK(unittest.TestCase):
    def test_capture_once_and_dispatch_exact_selected_relative_subclip_window(self):
        client=Client();owner=copy.deepcopy(client.owner);client.switch=True
        result=client.create_subclip('sub/sl~ç',.000123456789,.9987654321,name='Interview É')
        self.assertEqual(result['media_ids'],['created-0'])
        self.assertEqual([c[0] for c in client.calls],['/api/project/state','/api/media/subclip'])
        self.assertEqual(client.calls[-1][1],{'media_id':'sub/sl~ç','in':.000123456789,'out':.9987654321,'name':'Interview É',
            '_context':owner,'actor':'agent','client':'source-creation-test'})

    def test_explicit_context_never_fetches_or_rebinds_owner(self):
        for method,args in [('create_subclip',('captured-source',0,1)),('breakout_audio',('captured-source',)),('duplicate_media',(['captured-source'],))]:
            client=Client();owner=copy.deepcopy(client.owner)
            result=getattr(client,method)(*args,context=owner)
            self.assertEqual(len(client.calls),1);self.assertEqual(client.calls[0][1]['_context'],owner)
            self.assertEqual(result['context']['project'],owner['project'])

    def test_duplicate_preserves_order_without_frontend_records_or_derived_payload(self):
        client=Client();result=client.duplicate_media(('sub/sl~ç','m'))
        self.assertEqual(result['media_ids'],['created-0','created-1'])
        body=client.calls[-1][1];self.assertEqual(body['media_ids'],['sub/sl~ç','m'])
        self.assertNotIn('ops',body);self.assertNotIn('media',body)
        self.assertEqual([c[0] for c in client.calls],['/api/project/state','/api/media/duplicate'])

    def test_breakout_captures_only_selected_audio_source(self):
        client=Client();result=client.breakout_audio('m')
        self.assertEqual(len(result['media']),2);self.assertEqual(client.calls[-1][1]['media_id'],'m')
        self.assertEqual(result['summary']['source_media_ids'],['m'])

    def test_invalid_local_settings_refuse_before_any_read_or_write(self):
        calls=[('create_subclip',('m',-1,1),{}),('create_subclip',('m',1,1),{}),('create_subclip',('m',True,2),{}),
            ('create_subclip',('m',0,float('inf')),{}),('create_subclip',('m',float('nan'),2),{}),
            ('create_subclip',('',0,1),{}),('create_subclip',('m',0,1),{'name':' '}),
            ('create_subclip',('m',0,1),{'name':'x'*257}),('create_subclip',('m',0,1),{'name':'nul\0name'}),
            ('create_subclip',('m',0,1),{'name':[]}),('create_subclip',('m',0,1),{'name':'line\nname'}),('breakout_audio',(None,),{}),
            ('duplicate_media',('m',),{}),('duplicate_media',([],),{}),('duplicate_media',(['m','m'],),{}),
            ('duplicate_media',([str(n) for n in range(51)],),{}),('duplicate_media',(['m',None],),{})]
        for method,args,kw in calls:
            client=Client()
            with self.subTest(method=method,args=args,kw=kw),self.assertRaises(FilmocityError):getattr(client,method)(*args,**kw)
            self.assertEqual(client.calls,[])

    def test_missing_source_and_missing_audio_refuse_after_single_capture(self):
        for method,args in [('create_subclip',('missing',0,1)),('breakout_audio',('existing',)),('duplicate_media',(['m','missing'],))]:
            client=Client()
            with self.assertRaises(FilmocityError):getattr(client,method)(*args)
            self.assertEqual([c[0] for c in client.calls],['/api/project/state'])

    def test_invalid_explicit_owner_never_falls_back_to_new_capture(self):
        for owner in ({},{'workspace':'w','project':'p'},{'workspace':'w','project':'p','revision':False}):
            client=Client()
            with self.assertRaises(FilmocityError):client.duplicate_media(['m'],context=owner)
            self.assertEqual(client.calls,[])

    def test_uncertain_reply_is_never_retried_or_reread(self):
        client=Client();client.failure=True
        with self.assertRaises(FilmocityError):client.breakout_audio('m')
        self.assertEqual([c[0] for c in client.calls],['/api/project/state','/api/media/breakout'])

    def test_wrong_owner_mode_or_creation_identity_is_uncertain_without_replay(self):
        variants=[lambda r:r.update(ok=False),lambda r:r.update(changed=False),lambda r:r.update(kind='other'),lambda r:r.update(project='foreign'),
            lambda r:r['context'].update(workspace='foreign'),lambda r:r['context'].update(project='foreign'),
            lambda r:r['context'].update(revision=''),lambda r:r.update(mode='subclip'),
            lambda r:r.update(media_ids=['created-0','created-0']),lambda r:r['media'][0].update(id='wrong'),
            lambda r:r['summary'].update(source_media_ids=['foreign']),lambda r:r['summary'].update(created_media_ids=[]),
            lambda r:r.update(media=[]),lambda r:r.update(media_ids=[])]
        for change in variants:
            client=Client();client.change=change
            with self.subTest(change=change),self.assertRaisesRegex(FilmocityError,'uncertain'):client.duplicate_media(['m','sub/sl~ç'])
            self.assertEqual(len(client.calls),2)

    def test_recycled_existing_id_is_not_reported_as_created(self):
        def recycle(reply):
            reply['media_ids']=['existing'];reply['media'][0]['id']='existing';reply['summary']['created_media_ids']=['existing']
        client=Client();client.change=recycle
        with self.assertRaisesRegex(FilmocityError,'uncertain'):client.create_subclip('m',0,1)
        self.assertEqual(len(client.calls),2)

    def test_saved_creation_with_preparation_warning_remains_confirmed(self):
        client=Client();client.change=lambda r:r['preparation']['warnings'].append('Source offline; prepare after Relink')
        reply=client.duplicate_media(['m']);self.assertTrue(reply['ok']);self.assertTrue(reply['preparation']['warnings'])
        self.assertEqual(len(client.calls),2)


class SavedSourceCreationSDK(unittest.TestCase):
    def setUp(self):
        import test_source_creation_workflow as routes
        self.fixture=routes.StoreSourceCreation('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        fixture=self.fixture
        class SavedClient(Filmocity):
            def __init__(self):
                super().__init__(actor='human',client='actual-source-creation-sdk');self.calls=[];self.lose_reply=False
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                if path=='/api/project/state':return {'project':fixture.project(),'context':fixture.current()}
                route={'/api/media/subclip':'media_subclip','/api/media/breakout':'media_breakout','/api/media/duplicate':'media_duplicate'}[path]
                try:result=fixture.route(route,body)
                except routes.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                if self.lose_reply:raise FilmocityError('Saved, but acknowledgement was lost')
                return result
        self.client=SavedClient()

    def test_actual_selected_subclip_clock_and_exact_audio_one_undo_redo(self):
        f=self.fixture;before=f.children(interpreted=True)
        reply=self.client.create_subclip('sub',.25,2.75,name='SDK range É');mid=reply['media_ids'][0];after=f.project()
        self.assertEqual(after['media'][mid]['sub_in'],1.25);self.assertEqual(after['media'][mid]['duration'],2.5)
        self.assertEqual(after['media'][mid]['subclip_of'],'m');self.assertFalse(reply['preparation']['tasks'])
        self.assertEqual(after['sequences'],before['sequences']);self.assertEqual(after['media']['m'],before['media']['m'])
        self.assertEqual(f.audio(after,mid,'sdk-child',0,2.5),f.audio(before,'sub','sdk-selected',.25,2.75))
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['media'],before['media'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['media'],after['media'])

    def test_actual_breakout_prepares_selected_channels_and_one_complete_undo(self):
        import media_preview
        f=self.fixture;before=f.project();reply=self.client.breakout_audio('m')
        self.assertEqual(len(reply['media_ids']),2)
        self.assertEqual([m['audio_alias']['channel_index'] for m in reply['media']],[0,1])
        self.assertEqual(len(reply['preparation']['tasks']),2)
        for item in reply['preparation']['tasks']:f.prepare(item['task']['id'])
        after=f.project()
        for index,mid in enumerate(reply['media_ids']):
            self.assertEqual(after['media'][mid]['status'],'ready')
            self.assertEqual(after['media'][mid]['proxy_info']['channel_index'],index)
            self.assertEqual(media_preview.describe(f.root,after,mid)['proxy_state'],'ready')
        self.assertEqual(after['media']['m'],before['media']['m']);self.assertEqual(after['sequences'],before['sequences'])
        self.assertNotEqual(f.audio(after,reply['media_ids'][0],'sdk-left'),f.audio(after,reply['media_ids'][1],'sdk-right'))
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['media'],before['media'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['media'],after['media'])
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)

    def test_actual_ordered_duplicate_keeps_shared_subclip_and_independent_owned_jobs(self):
        f=self.fixture;before=f.children();reply=self.client.duplicate_media(['alias','sub','m']);ids=reply['media_ids']
        self.assertEqual(reply['summary']['source_media_ids'],['alias','sub','m'])
        self.assertEqual([m.get('subclip_of') for m in reply['media']],['m','m',None])
        self.assertEqual({t['media_id'] for t in reply['preparation']['tasks']},{ids[0],ids[2]})
        for item in reply['preparation']['tasks']:f.prepare(item['task']['id'])
        after=f.project();self.assertEqual(after['media'][ids[1]]['subclip_of'],'m')
        for source in ('m','sub','alias'):self.assertEqual(after['media'][source],before['media'][source])
        for mid in ids:self.assertEqual(after['media'][mid]['status'],'ready')
        self.assertEqual(len({after['media'][mid]['ingest_token'] for mid in ids}),3)
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)

    def test_actual_foreign_owner_never_retargets_identical_source_ids(self):
        f=self.fixture;owner=f.current();original=f.project();f.env['set_active_project']('b');f.env['save_project'](original)
        before=(f.raw('a'),f.raw('b'))
        for method,args in [('create_subclip',('m',0,1)),('breakout_audio',('m',)),('duplicate_media',(['m'],))]:
            with self.subTest(method=method),self.assertRaises(FilmocityError):getattr(self.client,method)(*args,context=owner)
        self.assertEqual((f.raw('a'),f.raw('b')),before);self.assertFalse(f.manager.values)
        self.assertEqual(len(self.client.calls),3);self.assertTrue(all(path!='/api/project/state' for path,_,_ in self.client.calls))

    def test_actual_agent_proposal_policy_refuses_all_creation_without_history(self):
        f=self.fixture;self.client.actor='agent';(f.root/'settings.json').write_text('{"agent_mode":"proposals_only"}');before=f.raw()
        for method,args in [('create_subclip',('m',0,1)),('breakout_audio',('m',)),('duplicate_media',(['m'],))]:
            with self.subTest(method=method),self.assertRaises(FilmocityError):getattr(self.client,method)(*args)
        self.assertEqual(f.raw(),before);self.assertFalse(f.manager.values);self.assertFalse(f.env['read_undo_history']('a')['undo'])

    def test_actual_saved_lost_reply_has_one_mutation_and_no_automatic_replay(self):
        f=self.fixture;self.client.lose_reply=True
        with self.assertRaisesRegex(FilmocityError,'acknowledgement was lost'):self.client.duplicate_media(['m'])
        self.assertEqual([path for path,_,_ in self.client.calls],['/api/project/state','/api/media/duplicate'])
        self.assertEqual(len(f.project()['media']),2);self.assertEqual(len(f.manager.values),1)
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)


if __name__=='__main__':unittest.main()
