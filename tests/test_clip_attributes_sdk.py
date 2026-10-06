"""Captured Paste Attributes SDK ownership, payload and acknowledgement checks."""
import copy
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'agent'))
from filmocity_client import Filmocity,FilmocityError

class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='human',client='attributes-sdk')
        self.owner={'workspace':'w','project':'p','revision':'r'}
        self.document={'media':{'m':{'id':'m','path':'original.mp4','duration':8,'transcript':'large derivative'}},
            'sequences':[{'id':'s','fps':30,'width':1920,'height':1080,'tracks':[{'id':'v','clips':[
                {'id':'source','media_id':'m','in_':1,'out':5,'transform':{'scale':1.5},'audio':{'linked':False,'gain_db':-6}},
                {'id':'a','in_':0,'out':3},{'id':'b','in_':2,'out':5}]}]}]}
        self.calls=[];self.change=lambda r:None;self.lose=False;self.refuse=False;self.noop=False;self.switch=False
    def donor(self):
        return self.capture_clip_attributes('source',state={'project':self.document,'context':self.owner})
    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':
            result={'context':copy.deepcopy(self.owner),'project':copy.deepcopy(self.document)}
            if self.switch:self.owner['project']='elsewhere'
            return result
        if self.lose:raise FilmocityError('Acknowledgement lost')
        changed=[] if self.noop else list(body['clip_ids'])
        summary={'kind':'clip_attributes','sequence':body['sequence'],'selected_count':len(body['clip_ids']),
            'changed_clip_ids':changed,'targets':[{'clip_id':i,'track':'v','changed':i in changed,'curves':[]} for i in body['clip_ids']],
            'warnings':['Review live dependencies.']}
        if path.endswith('/review'):
            result={'ok':not self.refuse,'kind':'clip_attributes','context':body['_context'],'sequence':body['sequence'],
                'clip_ids':body['clip_ids'],'donor':body['donor'],
                'settings':{k:body[k] for k in ('groups','include_animation','timing')},
                'issues':[{'code':'locked_track'}] if self.refuse else [],'fingerprint':'a'*64,'summary':summary}
        else:
            result={'ok':True,'changed':bool(changed),'kind':'clip_attributes','context':{**body['_context'],'revision':body['_context']['revision'] if self.noop else 'next'},
                'project':body['_context']['project'],'sequence':body['sequence'],'target_clip_ids':body['clip_ids'],
                'source_clip_id':body['donor']['clip']['id'],'changed_clip_ids':changed,'summary':summary,'warnings':summary['warnings']}
        result=copy.deepcopy(result);self.change(result);return result

class AttributesSDK(unittest.TestCase):
    def test_capture_is_detached_minimal_and_reads_once(self):
        c=Client();d=c.capture_clip_attributes('source');self.assertEqual(len(c.calls),1)
        self.assertNotIn('transcript',d['media']);self.assertEqual(d['media']['path'],'original.mp4')
        d['clip']['transform']['scale']=99;d['media']['path']='changed';d['context']['revision']='old'
        self.assertEqual(c.document['sequences'][0]['tracks'][0]['clips'][0]['transform']['scale'],1.5)
        self.assertEqual(c.document['media']['m']['path'],'original.mp4');self.assertEqual(c.owner['revision'],'r')
        c.donor();self.assertEqual(len(c.calls),1)
    def test_invalid_capture_never_falls_back_to_second_read(self):
        for state in ({},{'project':{},'context':{}},{'project':{'sequences':[]},'context':{'workspace':'w','project':'p','revision':'r'}}):
            c=Client()
            with self.subTest(state=state),self.assertRaises(FilmocityError):c.capture_clip_attributes('source',state=state)
            self.assertEqual(c.calls,[])
    def test_review_apply_keeps_one_captured_owner_and_exact_donor(self):
        c=Client();d=c.donor();c.switch=True;r=c.review_clip_attributes(d,['b','a']);owner=copy.deepcopy(r['context'])
        d['clip']['transform']['scale']=9
        c.apply_clip_attributes(r)
        self.assertEqual([v[0] for v in c.calls],['/api/project/state','/api/clip/attributes/review','/api/clip/attributes'])
        body=c.calls[-1][1];self.assertEqual(body['_context'],owner);self.assertEqual(body['donor'],r['donor']);self.assertEqual(body['clip_ids'],['b','a'])
        self.assertEqual(body['donor']['clip']['transform']['scale'],1.5);self.assertEqual(body['groups'],list(c._ATTRIBUTE_DEFAULTS))
        self.assertTrue(body['include_animation']);self.assertEqual(body['timing'],'seconds')
    def test_explicit_owner_and_values_only_scale_need_no_capture(self):
        c=Client();r=c.review_clip_attributes(c.donor(),['a'],'s',context=c.owner,groups=['audio_fades'],include_animation=False,timing='scale')
        c.apply_clip_attributes(r);self.assertEqual(len(c.calls),2);self.assertFalse(c.calls[-1][1]['include_animation'])
        self.assertEqual(c.calls[-1][1]['timing'],'scale')
    def test_noop_requires_exact_context_and_empty_changes(self):
        c=Client();c.noop=True;r=c.review_clip_attributes(c.donor(),['a']);reply=c.apply_clip_attributes(r)
        self.assertFalse(reply['changed']);self.assertEqual(reply['context'],r['context'])
        c.change=lambda value:value['context'].update(revision='different')
        with self.assertRaisesRegex(FilmocityError,'uncertain'):c.apply_clip_attributes(r)
    def test_refusal_never_dispatches_apply(self):
        c=Client();c.refuse=True;r=c.review_clip_attributes(c.donor(),['a'])
        with self.assertRaises(FilmocityError):c.apply_clip_attributes(r)
        self.assertEqual(len(c.calls),2)
    def test_invalid_options_fail_before_network(self):
        for ids,kw in [('a',{}),([],{}),(['a','a'],{}),([True],{}),([str(i) for i in range(101)],{}),
                (['a'],{'groups':[]}),(['a'],{'groups':['motion','motion']}),(['a'],{'groups':[{}]}),
                (['a'],{'include_animation':1}),(['a'],{'timing':'frames'}),(['a'],{'context':{}})]:
            c=Client()
            with self.subTest(ids=ids,kw=kw),self.assertRaises(FilmocityError):c.review_clip_attributes(c.donor(),ids,**kw)
            self.assertFalse(c.calls)
    def test_invalid_donor_and_nonfinite_data_fail_before_network(self):
        for mutate in (lambda d:d.update(version=True),lambda d:d['sequence'].update(fps='nan'),lambda d:d['sequence'].update(width=False),
                lambda d:d['context'].update(revision=None),lambda d:d['clip'].update(gain=float('nan')),lambda d:d.update(media=[]),
                lambda d:d['clip'].update(note='x'*(2*1024*1024))):
            c=Client();d=c.donor();mutate(d)
            with self.assertRaises(FilmocityError):c.review_clip_attributes(d,['a'])
            self.assertFalse(c.calls)
    def test_missing_target_checked_in_captured_sequence(self):
        c=Client()
        with self.assertRaises(FilmocityError):c.review_clip_attributes(c.donor(),['missing'])
        self.assertEqual(len(c.calls),1)
    def test_altered_review_envelope_is_rejected(self):
        for mutate in (lambda r:r['context'].update(project='foreign'),lambda r:r.update(sequence='foreign'),
                lambda r:r.update(clip_ids=['b']),lambda r:r['donor']['clip'].update(id='foreign'),
                lambda r:r['settings'].update(timing='scale'),lambda r:r.update(fingerprint='invalid'),
                lambda r:r.update(kind='other'),lambda r:r['summary'].update(changed_clip_ids=['a','a']),
                lambda r:r['summary']['targets'][0].update(track=None),lambda r:r['summary']['targets'][0].update(changed=False),
                lambda r:r['summary'].update(warnings=[{}]),lambda r:r.update(issues=[{'code':'locked_track'}])):
            c=Client();c.change=mutate
            with self.subTest(mutate=mutate),self.assertRaises(FilmocityError):c.review_clip_attributes(c.donor(),['a'])
            self.assertEqual(len(c.calls),2)
    def test_mutated_report_invalid_before_apply(self):
        for mutate in (lambda r:r['settings'].update(force=True),lambda r:r['summary'].update(selected_count=True),
                lambda r:r['summary'].update(changed_count=99),lambda r:r['summary'].update(changed_count=1,changed_clip_ids=1),
                lambda r:r['summary']['targets'][0].update(curves=[{}]),lambda r:r.update(issues=['bad'])):
            c=Client();r=c.review_clip_attributes(c.donor(),['a']);mutate(r)
            with self.assertRaises(FilmocityError):c.apply_clip_attributes(r)
            self.assertEqual(len(c.calls),2)
    def test_foreign_or_incomplete_receipt_is_uncertain_without_retry(self):
        for mutate in (lambda r:r.update(project='elsewhere'),lambda r:r['context'].update(workspace='elsewhere'),
                lambda r:r['context'].update(revision='r'),lambda r:r.update(target_clip_ids=['b']),
                lambda r:r.update(source_clip_id='elsewhere'),lambda r:r.update(changed_clip_ids=[]),lambda r:r.update(changed=False),
                lambda r:r['summary'].update(message='different'),lambda r:r.update(warnings=[])):
            c=Client();r=c.review_clip_attributes(c.donor(),['a']);c.change=mutate
            with self.assertRaisesRegex(FilmocityError,'uncertain'):c.apply_clip_attributes(r)
            self.assertEqual(len(c.calls),3)
    def test_lost_acknowledgement_is_not_replayed(self):
        c=Client();r=c.review_clip_attributes(c.donor(),['a']);c.lose=True
        with self.assertRaisesRegex(FilmocityError,'lost'):c.apply_clip_attributes(r)
        self.assertEqual(len(c.calls),3)

class SavedAttributesSDK(unittest.TestCase):
    def setUp(self):
        import test_clip_attributes_workflow as routes
        self.fixture=routes.StoreClipAttributes('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown);f=self.fixture
        class SavedClient(Filmocity):
            def __init__(self):super().__init__(actor='human',client='saved-attributes-sdk');self.calls=[];self.lose=False
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                if path=='/api/project/state':return {'project':f.project(),'context':f.current()}
                route={'/api/clip/attributes/review':'clip_attributes_review','/api/clip/attributes':'clip_attributes_apply'}[path]
                try:reply=f.route(route,body)
                except routes.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                if self.lose:raise FilmocityError('Saved acknowledgement lost')
                return reply
        self.client=SavedClient()
    def test_actual_review_apply_one_history_and_exact_undo_redo(self):
        f=self.fixture;before=f.project();r=self.client.review_clip_attributes(f.donor,['c']);reply=self.client.apply_clip_attributes(r);after=f.project()
        self.assertEqual(reply['summary'],r['summary']);self.assertEqual(after['media'],before['media'])
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],before['sequences'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],after['sequences'])
    def test_actual_capture_and_noop_leave_bytes_history_and_revision(self):
        f=self.fixture;d=self.client.capture_clip_attributes('c');before=f.raw();owner=f.current()
        r=self.client.review_clip_attributes(d,['c'],'s',context=owner);reply=self.client.apply_clip_attributes(r)
        self.assertFalse(reply['changed']);self.assertEqual(f.raw(),before);self.assertEqual(f.current(),owner)
        self.assertEqual(f.env['read_undo_history']('a')['undo'],[]);self.assertEqual(len(self.client.calls),3)
    def test_actual_locked_refusal_never_applies(self):
        f=self.fixture;p=f.project();p['sequences'][0]['tracks'][0]['locked']=True;f.env['save_project'](p);before=f.raw()
        r=self.client.review_clip_attributes(f.donor,['c']);self.assertFalse(r['ok'])
        with self.assertRaises(FilmocityError):self.client.apply_clip_attributes(r)
        self.assertEqual(f.raw(),before);self.assertEqual(len(self.client.calls),2)
    def test_actual_foreign_owner_and_proposals_policy_never_retarget(self):
        f=self.fixture;r=self.client.review_clip_attributes(f.donor,['c']);f.env['set_active_project']('b');before=(f.raw('a'),f.raw('b'))
        with self.assertRaises(FilmocityError):self.client.apply_clip_attributes(r)
        self.assertEqual((f.raw('a'),f.raw('b')),before);f.env['set_active_project']('a')
        (f.root/'settings.json').write_text('{"agent_mode":"proposals_only"}');self.client.actor='agent'
        r=self.client.review_clip_attributes(f.donor,['c']);self.assertTrue(r['ok'])
        with self.assertRaises(FilmocityError):self.client.apply_clip_attributes(r)
        self.assertEqual((f.raw('a'),f.raw('b')),before)
    def test_actual_saved_lost_reply_is_one_commit_without_replay(self):
        f=self.fixture;r=self.client.review_clip_attributes(f.donor,['c']);self.client.lose=True
        with self.assertRaisesRegex(FilmocityError,'lost'):self.client.apply_clip_attributes(r)
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1);self.assertEqual(len(self.client.calls),3)
        before=f.raw();self.client.lose=False
        with self.assertRaises(FilmocityError):self.client.apply_clip_attributes(r)
        self.assertEqual(f.raw(),before)

if __name__=='__main__':unittest.main()
