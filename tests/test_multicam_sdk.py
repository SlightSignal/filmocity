"""Captured Multicam Flatten SDK contracts and saved-store integration."""
import copy
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'agent'))
from filmocity_client import Filmocity,FilmocityError

class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='human',client='multicam-sdk');self.owner={'workspace':'w','project':'p','revision':'r'}
        self.document={'sequences':[{'id':'s','tracks':[{'clips':[{'id':'a'},{'id':'b'}]}]}]}
        self.calls=[];self.change=lambda r:None;self.lose=False;self.refuse=False;self.switch=False
    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':
            value={'context':copy.deepcopy(self.owner),'project':copy.deepcopy(self.document)}
            if self.switch:self.owner['project']='foreign'
            return value
        if self.lose:raise FilmocityError('Acknowledgement lost')
        summary={'kind':'multicam_flatten','sequence':body['sequence'],'selected_count':len(body['clip_ids']),
            'replacement_clip_ids':[body['clip_ids'][0],'audio-new'],'warnings':['Original cameras remain available.']}
        if path.endswith('/review'):
            value={'ok':not self.refuse,'kind':'multicam_flatten','context':body['_context'],'sequence':body['sequence'],
                'clip_ids':body['clip_ids'],'settings':{},'issues':[{'severity':'error','code':'source_sampling'}] if self.refuse else [],
                'fingerprint':'a'*64,'summary':{} if self.refuse else summary}
        else:
            value={'ok':True,'changed':True,'kind':'multicam_flatten','context':{**body['_context'],'revision':'next'},'project':body['_context']['project'],
                'sequence':body['sequence'],'source_clip_ids':body['clip_ids'],'replacement_clip_ids':summary['replacement_clip_ids'],
                'summary':summary,'warnings':summary['warnings']}
        value=copy.deepcopy(value);self.change(value);return value

class MulticamSDK(unittest.TestCase):
    def test_ordered_capture_review_apply_uses_exact_owner_once(self):
        c=Client();c.switch=True;r=c.review_multicam_flatten(['b','a']);owner=copy.deepcopy(r['context']);c.apply_multicam_flatten(r)
        self.assertEqual([v[0] for v in c.calls],['/api/project/state','/api/multicam/flatten/review','/api/multicam/flatten'])
        self.assertEqual(c.calls[-1][1],{'_context':owner,'sequence':'s','clip_ids':['b','a'],'fingerprint':'a'*64,'actor':'human','client':'multicam-sdk'})
        self.assertEqual(owner['project'],'p')
    def test_explicit_context_never_rereads_and_refused_review_never_applies(self):
        c=Client();c.refuse=True;r=c.review_multicam_flatten(['a'],seq_id='s',context=c.owner)
        self.assertFalse(r['ok'])
        with self.assertRaises(FilmocityError):c.apply_multicam_flatten(r)
        self.assertEqual(len(c.calls),1)
    def test_invalid_selection_and_context_refuse_before_dispatch(self):
        for ids,kw in [('a',{}),([],{}),(['a','a'],{}),([True],{}),([f'c{i}' for i in range(101)],{}),(['a'],{'context':{}}),(['a'],{'seq_id':'s','context':{}})]:
            c=Client()
            with self.subTest(ids=ids[:3],kw=kw),self.assertRaises(FilmocityError):c.review_multicam_flatten(ids,**kw)
            self.assertFalse(c.calls)
        c=Client()
        with self.assertRaises(FilmocityError):c.review_multicam_flatten(['missing'])
        self.assertEqual(len(c.calls),1)
    def test_foreign_review_or_selection_is_not_accepted(self):
        for change in (lambda r:r['context'].update(project='elsewhere'),lambda r:r.update(sequence='elsewhere'),lambda r:r.update(clip_ids=['b'])):
            c=Client();c.change=change
            with self.assertRaises(FilmocityError):c.review_multicam_flatten(['a'])
            self.assertEqual(len(c.calls),2)
    def test_malformed_or_tampered_report_cannot_submit(self):
        for change in (lambda r:r.update(kind='nest'),lambda r:r.update(fingerprint='bad'),lambda r:r['settings'].update(force=True),
            lambda r:r['summary'].update(selected_count=9),lambda r:r['summary'].update(kind='other'),lambda r:r['summary'].update(replacement_clip_ids=['a','a']),
            lambda r:r.update(issues=[{'severity':'error'}]),lambda r:r.update(issues=['invalid']),lambda r:r['summary'].update(warnings=[{}]),lambda r:r['context'].update(revision=None)):
            c=Client();r=c.review_multicam_flatten(['a']);change(r)
            with self.assertRaises(FilmocityError):c.apply_multicam_flatten(r)
            self.assertEqual(len(c.calls),2)
    def test_malformed_or_foreign_saved_receipt_is_uncertain_without_retry(self):
        for change in (lambda r:r.update(project='foreign'),lambda r:r['context'].update(workspace='foreign'),lambda r:r.update(changed=False),
            lambda r:r.update(source_clip_ids=['b']),lambda r:r.update(replacement_clip_ids=['wrong']),lambda r:r['summary'].update(message='Changed'),lambda r:r.update(warnings=[])):
            c=Client();r=c.review_multicam_flatten(['a']);c.change=change
            with self.assertRaisesRegex(FilmocityError,'uncertain'):c.apply_multicam_flatten(r)
            self.assertEqual(len(c.calls),3)
    def test_lost_acknowledgement_is_never_replayed(self):
        c=Client();r=c.review_multicam_flatten(['a']);c.lose=True
        with self.assertRaisesRegex(FilmocityError,'lost'):c.apply_multicam_flatten(r)
        self.assertEqual(len(c.calls),3)

class SavedMulticamSDK(unittest.TestCase):
    def setUp(self):
        import test_multicam_flatten_workflow as routes
        self.fixture=routes.StoreMulticamFlatten('runTest');self.fixture.setUp();self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        f=self.fixture
        class SavedClient(Filmocity):
            def __init__(self):super().__init__(actor='human',client='saved-multicam-sdk');self.calls=[];self.lose=False
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                if path=='/api/project/state':return {'project':f.project(),'context':f.current()}
                route={'/api/multicam/flatten/review':'multicam_flatten_review','/api/multicam/flatten':'multicam_flatten_apply'}[path]
                try:reply=f.route(route,body)
                except routes.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                if self.lose:raise FilmocityError('Saved acknowledgement lost')
                return reply
        self.client=SavedClient()

    def test_actual_review_apply_exact_summary_and_complete_undo_redo(self):
        f=self.fixture;before=f.project();review=self.client.review_multicam_flatten(['outer']);reply=self.client.apply_multicam_flatten(review);after=f.project()
        self.assertEqual(reply['summary'],review['summary']);self.assertEqual(after['sequences'][1:],before['sequences'][1:]);self.assertEqual(after['media'],before['media'])
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],before['sequences'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],after['sequences'])

    def test_actual_locked_and_sampling_refusals_never_apply(self):
        f=self.fixture
        for kind in ('lock','sampling'):
            p=f.project();p['sequences'][0]['tracks'][0]['locked']=kind=='lock'
            if kind=='sampling':p['sequences'][1]['fps']=15
            f.env['save_project'](p);before=f.raw();r=self.client.review_multicam_flatten(['outer']);self.assertFalse(r['ok'])
            with self.assertRaises(FilmocityError):self.client.apply_multicam_flatten(r)
            self.assertEqual(f.raw(),before);self.assertFalse(f.env['read_undo_history']('a')['undo'])

    def test_actual_foreign_owner_and_proposals_policy_never_retarget(self):
        f=self.fixture;r=self.client.review_multicam_flatten(['outer']);f.env['set_active_project']('b');before=(f.raw('a'),f.raw('b'))
        with self.assertRaises(FilmocityError):self.client.apply_multicam_flatten(r)
        self.assertEqual((f.raw('a'),f.raw('b')),before);f.env['set_active_project']('a');(f.root/'settings.json').write_text('{"agent_mode":"proposals_only"}');self.client.actor='agent'
        r=self.client.review_multicam_flatten(['outer']);self.assertTrue(r['ok'])
        with self.assertRaises(FilmocityError):self.client.apply_multicam_flatten(r)
        self.assertEqual((f.raw('a'),f.raw('b')),before)

    def test_actual_saved_lost_reply_is_one_commit_without_automatic_replay(self):
        f=self.fixture;r=self.client.review_multicam_flatten(['outer']);self.client.lose=True
        with self.assertRaisesRegex(FilmocityError,'lost'):self.client.apply_multicam_flatten(r)
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1);self.assertEqual(len(self.client.calls),3)
        before=f.raw();self.client.lose=False
        with self.assertRaises(FilmocityError):self.client.apply_multicam_flatten(r)
        self.assertEqual(f.raw(),before)


if __name__=='__main__':unittest.main()
