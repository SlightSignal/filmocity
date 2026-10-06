"""Sequence SDK transport contracts and actual saved route integration."""
import copy
from pathlib import Path
import sys
import unittest

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'agent'));sys.path.insert(0,str(ROOT/'backend'))
from filmocity_client import Filmocity, FilmocityError
import sequence_creation
import sequence_nesting
import test_sequence_nesting as pure


class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='human',client='sdk-test');self.owner={'workspace':'w','project':'p','revision':'r'}
        self.document=pure.project();self.document['sequences'][0]['fps']=30
        self.document['media']['m'].update(width=64,height=48)
        self.calls=[];self.change=lambda r:None;self.switch=False;self.failure=False

    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':
            result={'context':copy.deepcopy(self.owner),'project':copy.deepcopy(self.document)}
            if self.switch:self.owner['project']='foreign'
            return result
        if self.failure:raise FilmocityError('Acknowledgement lost')
        if path=='/api/sequence/create':
            planned=sequence_creation.plan(self.document,body,identity='a'*32)
            result={'ok':True,'changed':True,'kind':'sequence_creation','project':body['_context']['project'],
                'context':{**body['_context'],'revision':'next'},'sequence':planned['sequence']['id'],
                **{key:planned[key] for key in ('mode','source_sequence','media_id','clip_id','summary','warnings')}}
        else:
            planned=sequence_nesting.plan(self.document,body)
            if path.endswith('/review'):result={k:v for k,v in planned.items() if k!='ops'};result['context']=body['_context']
            else:
                result={'ok':True,'changed':True,'kind':'sequence_nesting','project':body['_context']['project'],
                    'context':{**body['_context'],'revision':'next'},'sequence':body['sequence'],
                    'source_clip_ids':body['clip_ids'],'child_sequence':planned['summary']['child_sequence'],
                    'wrapper_clip_ids':planned['summary']['wrapper_clip_ids'],'summary':planned['summary'],'warnings':planned['summary']['warnings']}
        self.change(result);return result


class SequenceSDK(unittest.TestCase):
    def test_create_captures_owner_once_and_sends_one_whole_source_command(self):
        client=Client();client.switch=True;owner=copy.deepcopy(client.owner)
        result=client.sequence_from_source('m',name='  Source É  ')
        self.assertEqual(result['project'],'p');self.assertEqual([x[0] for x in client.calls],['/api/project/state','/api/sequence/create'])
        self.assertEqual(client.calls[-1][1],{'_context':owner,'mode':'source','media_id':'m','sequence':'s','name':'Source É','actor':'human','client':'sdk-test'})

    def test_empty_and_explicit_source_capture_do_not_fetch_new_owner(self):
        for source in (False,True):
            client=Client();owner=copy.deepcopy(client.owner)
            result=(client.sequence_from_source('m',seq_id='s',context=owner) if source else client.create_sequence(seq_id='s',context=owner))
            self.assertEqual(len(client.calls),1);self.assertEqual(result['mode'],'source' if source else 'empty')
            self.assertNotIn('ops',client.calls[0][1])

    def test_invalid_names_ids_duration_and_explicit_owner_refuse_locally(self):
        commands=[('create_sequence',(),{'name':'\n'}),('create_sequence',(),{'name':'x'*257}),
            ('create_sequence',(),{'context':{'workspace':'w','project':'p','revision':'r'}}),
            ('sequence_from_source',('',),{}),('sequence_from_source',('m',),{'still_duration':True}),
            ('sequence_from_source',('m',),{'still_duration':float('nan')}),('sequence_from_source',('m',),{'still_duration':86401}),
            ('review_nest',('a',),{}),('review_nest',([],),{}),('review_nest',(['a','a'],),{}),
            ('review_nest',(['a'],),{'name':'x'*121}),('review_nest',(['a'],),{'seq_id':'s','context':{}})]
        for method,args,kw in commands:
            client=Client()
            with self.subTest(method=method,kw=kw),self.assertRaises(FilmocityError):getattr(client,method)(*args,**kw)
            self.assertFalse(client.calls)

    def test_missing_captured_source_sequence_or_clip_does_not_send_command(self):
        for method,args,kw in [('sequence_from_source',('missing',),{}),('create_sequence',(),{'seq_id':'missing'}),('review_nest',(['missing'],),{})]:
            client=Client()
            with self.assertRaises(FilmocityError):getattr(client,method)(*args,**kw)
            self.assertEqual([c[0] for c in client.calls],['/api/project/state'])

    def test_nest_review_order_context_and_exact_apply_without_reread(self):
        client=Client();client.document['sequences'][0]['tracks'][0]['clips'].append(pure.clip('b',3,4))
        report=client.review_nest(['b','a'],name='Captured');owner=copy.deepcopy(report['context']);client.owner['project']='elsewhere'
        result=client.apply_nest(report);self.assertTrue(result['changed'])
        self.assertEqual(client.calls[-1][1],{'_context':owner,'sequence':'s','clip_ids':['b','a'],'name':'Captured',
            'fingerprint':report['fingerprint'],'actor':'human','client':'sdk-test'})
        self.assertEqual([c[0] for c in client.calls],['/api/project/state','/api/sequence/nest/review','/api/sequence/nest'])

    def test_locked_review_is_readable_but_cannot_apply(self):
        client=Client();client.document['sequences'][0]['tracks'][0]['locked']=True
        review=client.review_nest(['a']);self.assertFalse(review['ok'])
        with self.assertRaises(FilmocityError):client.apply_nest(review)
        self.assertEqual(len(client.calls),2)

    def test_invalid_review_identity_or_settings_never_applies(self):
        variants=[lambda r:r.update(kind='other'),lambda r:r.update(fingerprint='bad'),lambda r:r['settings'].update(name=''),
            lambda r:r.update(clip_ids=['a','a']),lambda r:r['context'].update(revision=None),lambda r:r['summary'].update(wrapper_clip_ids=['a']),
            lambda r:r['summary'].update(child_sequence='s'),lambda r:r['summary'].update(selected_count=9)]
        for change in variants:
            client=Client();review=client.review_nest(['a']);change(review)
            with self.assertRaises(FilmocityError):client.apply_nest(review)
            self.assertEqual(len(client.calls),2)

    def test_foreign_created_or_nested_acknowledgement_is_uncertain_without_replay(self):
        for nesting in (False,True):
            for change in (lambda r:r.update(project='foreign'),lambda r:r['context'].update(workspace='foreign'),
                lambda r:r.update(changed=False),lambda r:r.update(sequence='recycled'),lambda r:r['summary'].update(kind='wrong')):
                client=Client();review=client.review_nest(['a']) if nesting else None;client.change=change
                with self.assertRaisesRegex(FilmocityError,'uncertain'):
                    client.apply_nest(review) if nesting else client.create_sequence()
                self.assertEqual(len(client.calls),3 if nesting else 2)

    def test_lost_creation_or_nest_reply_is_sent_once(self):
        for nesting in (False,True):
            client=Client();review=client.review_nest(['a']) if nesting else None;client.failure=True
            with self.assertRaisesRegex(FilmocityError,'lost'):
                client.apply_nest(review) if nesting else client.sequence_from_source('m')
            self.assertEqual(len(client.calls),3 if nesting else 2)


class SavedSequenceSDK(unittest.TestCase):
    def setUp(self):
        import test_sequence_nesting_workflow as routes
        self.fixture=routes.StoreSequenceNesting('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        fixture=self.fixture
        class SavedClient(Filmocity):
            def __init__(self):super().__init__(actor='human',client='saved-sequence-sdk');self.calls=[];self.lose=False
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                if path=='/api/project/state':return {'project':fixture.project(),'context':fixture.current()}
                route={'/api/sequence/create':'sequence_create','/api/sequence/nest/review':'sequence_nest_review','/api/sequence/nest':'sequence_nest'}[path]
                try:result=fixture.route(route,body)
                except routes.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                if self.lose:raise FilmocityError('Saved acknowledgement lost')
                return result
        self.client=SavedClient()

    def test_actual_create_source_nest_and_two_exact_undo_redo(self):
        f=self.fixture;before=f.project();created=self.client.sequence_from_source('m',name='SDK sequence');middle=f.project()
        report=self.client.review_nest([created['clip_id']],seq_id=created['sequence'],name='SDK nest');reply=self.client.apply_nest(report);after=f.project()
        self.assertEqual(len(after['sequences']),3);self.assertEqual(after['media'],before['media']);self.assertEqual(reply['child_sequence'],after['sequences'][-1]['id'])
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),2)
        for expected in (middle,before):f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],expected['sequences'])
        for expected in (middle,after):f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],expected['sequences'])

    def test_actual_pending_source_and_locked_nest_leave_no_empty_child(self):
        f=self.fixture;doc=f.project();doc['media']['m']['duration']=None;f.env['save_project'](doc);before=f.raw()
        with self.assertRaises(FilmocityError):self.client.sequence_from_source('m')
        self.assertEqual(f.raw(),before)
        doc['media']['m']['duration']=6;doc['sequences'][0]['tracks'][0]['locked']=True;f.env['save_project'](doc);before=f.raw()
        review=self.client.review_nest(['c']);self.assertFalse(review['ok'])
        with self.assertRaises(FilmocityError):self.client.apply_nest(review)
        self.assertEqual(f.raw(),before);self.assertFalse(f.env['read_undo_history']('a')['undo'])

    def test_actual_foreign_owner_and_proposals_policy_do_not_retarget(self):
        f=self.fixture;owner=f.current();review=self.client.review_nest(['c']);f.env['set_active_project']('b');before=(f.raw('a'),f.raw('b'))
        with self.assertRaises(FilmocityError):self.client.create_sequence(seq_id='s',context=owner)
        with self.assertRaises(FilmocityError):self.client.apply_nest(review)
        self.assertEqual((f.raw('a'),f.raw('b')),before);f.env['set_active_project']('a')
        (f.root/'settings.json').write_text('{"agent_mode":"proposals_only"}');self.client.actor='agent'
        with self.assertRaises(FilmocityError):self.client.create_sequence()
        report=self.client.review_nest(['c']);self.assertTrue(report['ok'])
        with self.assertRaises(FilmocityError):self.client.apply_nest(report)
        self.assertEqual((f.raw('a'),f.raw('b')),before)

    def test_actual_lost_creation_reply_has_one_commit_and_no_replay(self):
        f=self.fixture;self.client.lose=True
        with self.assertRaisesRegex(FilmocityError,'lost'):self.client.create_sequence()
        self.assertEqual(len(f.project()['sequences']),2);self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        self.assertEqual([c[0] for c in self.client.calls],['/api/project/state','/api/sequence/create'])

    def test_actual_lost_nest_reply_has_one_commit_and_stale_report_refuses(self):
        f=self.fixture;review=self.client.review_nest(['c']);self.client.lose=True
        with self.assertRaisesRegex(FilmocityError,'lost'):self.client.apply_nest(review)
        self.assertEqual(len(f.project()['sequences']),2);self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1);before=f.raw();self.client.lose=False
        with self.assertRaises(FilmocityError):self.client.apply_nest(review)
        self.assertEqual(f.raw(),before)


if __name__=='__main__':unittest.main()
