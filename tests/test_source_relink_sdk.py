"""Relink SDK requires one captured inspection and explicit exact reviewed Apply."""
import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from filmocity_client import Filmocity,FilmocityError


class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='agent',client='relink-sdk')
        self.calls=[];self.context={'workspace':'workspace / a','project':'A & one','revision':'r1'}
        self.fail=None;self.inspect_override={};self.reply_override={};self.switch=False
    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':
            result={'project':{'media':{'m':{'id':'m'},'sub':{'id':'sub','subclip_of':'m'}}},'context':copy.deepcopy(self.context)}
            if self.switch:self.context={**self.context,'project':'B'}
            return result
        if self.fail==path:raise FilmocityError('unknown response; inspect saved state')
        if path=='/api/media/relink/inspect':
            return {'ok':True,'context':copy.deepcopy(body['_context']),'media_id':'m','requested_media_id':body['media_id'],
                'path':r'C:\Footage\Replacement É.mov','fingerprint':'reviewed-fingerprint','expectedSource':'source-signature',
                'info':{'duration':10},'issues':[],'summary':{'affected_media_ids':['m','sub']},**copy.deepcopy(self.inspect_override)}
        if path=='/api/media/relink':
            return {'ok':True,'context':{**body['_context'],'revision':'r2'},'media_id':'m','changed':True,**copy.deepcopy(self.reply_override)}
        raise AssertionError(path)


class RelinkSDK(unittest.TestCase):
    def test_inspect_captures_once_and_never_rebinds_after_switch(self):
        c=Client();owner=copy.deepcopy(c.context);c.switch=True
        report=c.inspect_relink('sub','relative/selected.mov')
        self.assertEqual([p for p,_,_ in c.calls],['/api/project/state','/api/media/relink/inspect'])
        self.assertEqual(report['context'],owner);self.assertEqual(c.calls[-1][1],{'media_id':'sub','path':'relative/selected.mov','_context':owner,'actor':'agent','client':'relink-sdk'})
        self.assertEqual(report['media_id'],'m')
    def test_explicit_owner_does_not_read_current_project(self):
        c=Client();owner=copy.deepcopy(c.context);c.inspect_relink('captured','source.mov',context=owner)
        self.assertEqual([p for p,_,_ in c.calls],['/api/media/relink/inspect']);self.assertEqual(c.calls[0][1]['_context'],owner)
    def test_invalid_input_owner_and_missing_source_never_inspect(self):
        for media,path,options in [('', 'file',{}),('m','',{}),('m',None,{}),('m','nul\0file',{}),('m','file',{'context':{}})]:
            c=Client()
            with self.subTest(media=media,path=path),self.assertRaises(FilmocityError):c.inspect_relink(media,path,**options)
            self.assertFalse(c.calls)
        c=Client()
        with self.assertRaises(FilmocityError):c.inspect_relink('absent','file')
        self.assertEqual([p for p,_,_ in c.calls],['/api/project/state'])
    def test_inspection_rejects_foreign_source_owner_and_malformed_review(self):
        for override in [{'context':{'workspace':'w','project':'B','revision':'r1'}},{'requested_media_id':'other'},
                         {'ok':None},{'path':None},{'media_id':None},{'fingerprint':None}]:
            c=Client();c.inspect_override=override
            with self.subTest(override=override),self.assertRaises(FilmocityError):c.inspect_relink('m','file')
            self.assertEqual(len(c.calls),2)
        c=Client();c.inspect_override={'ok':False,'fingerprint':None,'issues':[{'severity':'error','message':'Too short'}]}
        result=c.inspect_relink('m','file');self.assertFalse(result['ok']);self.assertTrue(result['issues'])
    def test_apply_uses_exact_review_without_read_or_reinspection(self):
        c=Client();report=c.inspect_relink('sub','relative.mov');c.calls.clear();c.context['project']='foreign'
        original=copy.deepcopy(report);reply=c.apply_relink(report);self.assertTrue(reply['changed']);self.assertEqual(report,original)
        self.assertEqual([p for p,_,_ in c.calls],['/api/media/relink']);body=c.calls[0][1]
        self.assertEqual(body,{'media_id':'sub','path':report['path'],'_context':report['context'],'fingerprint':report['fingerprint'],'actor':'agent','client':'relink-sdk'})
        self.assertNotIn('ops',body)
    def test_old_expected_source_alone_and_changed_review_cannot_mutate(self):
        c=Client();report=c.inspect_relink('m','file');c.calls.clear()
        with self.assertRaisesRegex(FilmocityError,'expected_source alone'):c.relink('m',report['path'],'source-signature')
        for changed in [{'ok':False},{'context':None},{'context':{}},{'fingerprint':''},{'media_id':None}]:
            with self.subTest(changed=changed),self.assertRaises(FilmocityError):c.apply_relink({**report,**changed})
        for args,options in [(('other',report['path']),{}),(('m','unreviewed.mov'),{}),(('m',report['path']),{'context':{**report['context'],'revision':'r2'}}),(('m',report['path'],'different-signature'),{})]:
            with self.subTest(args=args),self.assertRaises(FilmocityError):c.relink(*args,review=report,**options)
        self.assertEqual(c.calls,[])
    def test_optional_legacy_signature_checks_and_sends_reviewed_owner(self):
        c=Client();report=c.inspect_relink('m','file');c.calls.clear()
        c.relink('m',report['path'],report['expectedSource'],review=report,context=report['context'])
        self.assertEqual(c.calls[0][1]['expectedSource'],'source-signature');self.assertEqual(len(c.calls),1)
    def test_uncertain_apply_or_wrong_acknowledgement_is_never_replayed(self):
        for mode in ('network','foreign','physical','malformed'):
            c=Client();report=c.inspect_relink('m','file');c.calls.clear()
            if mode=='network':c.fail='/api/media/relink'
            elif mode=='foreign':c.reply_override={'context':{**report['context'],'project':'B'}}
            elif mode=='physical':c.reply_override={'media_id':'other'}
            else:c.reply_override={'context':{**report['context'],'revision':None}}
            with self.subTest(mode=mode),self.assertRaises(FilmocityError):c.apply_relink(report)
            self.assertEqual([p for p,_,_ in c.calls],['/api/media/relink'])
    def test_inspection_failure_never_retries_or_applies(self):
        c=Client();c.fail='/api/media/relink/inspect'
        with self.assertRaises(FilmocityError):c.inspect_relink('m','file')
        self.assertEqual([p for p,_,_ in c.calls],['/api/project/state','/api/media/relink/inspect'])


class SavedRelinkSDK(unittest.TestCase):
    def setUp(self):
        import test_source_relink_workflow as routes
        self.fixture=routes.StoreSourceRelink('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        fixture=self.fixture
        class SavedClient(Filmocity):
            def __init__(self):
                super().__init__(actor='human',client='actual-relink-sdk');self.calls=[];self.lose_reply=False
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                if path=='/api/project/state':return {'project':fixture.project(),'context':fixture.current()}
                route={'/api/media/relink/inspect':'media_relink_inspect','/api/media/relink':'media_relink'}[path]
                try:result=fixture.route(route,body)
                except routes.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                if route=='media_relink' and self.lose_reply:raise FilmocityError('Saved, but acknowledgement was lost')
                return result
        self.client=SavedClient()
    def test_actual_alias_inspection_apply_owned_preparation_and_one_undo_redo(self):
        f=self.fixture;before=f.children(interpreted=True);report=self.client.inspect_relink('alias',str(f.replacement))
        self.assertTrue(report['ok']);self.assertEqual(report['media_id'],'m');self.assertEqual(f.project(),before)
        self.assertFalse(f.manager.values);self.assertFalse(f.env['read_undo_history']('a')['undo'])
        self.client.calls.clear();reply=self.client.apply_relink(report)
        self.assertEqual([p for p,_,_ in self.client.calls],['/api/media/relink'])
        self.assertEqual(set(reply['affected_media_ids']),{'m','sub','alias'})
        for identity in list(f.manager.values):f.prepare(identity)
        after=f.project();self.assertEqual(after['sequences'],before['sequences'])
        self.assertEqual(after['media']['alias']['status'],'ready');self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        self.assertEqual(f.audio(after,'sub','sdk-original'),f.audio(after,'alias','sdk-alias'))
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['media'],before['media'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['media'],after['media'])
    def test_actual_foreign_owner_cannot_apply_an_identical_descriptor(self):
        f=self.fixture;report=self.client.inspect_relink('m',str(f.replacement));original=f.project()
        f.env['set_active_project']('b');f.env['save_project'](original);before=(f.raw('a'),f.raw('b'));self.client.calls.clear()
        with self.assertRaises(FilmocityError):self.client.apply_relink(report)
        self.assertEqual([p for p,_,_ in self.client.calls],['/api/media/relink'])
        self.assertEqual((f.raw('a'),f.raw('b')),before);self.assertFalse(f.manager.values)
    def test_actual_unused_alias_range_refusal_never_applies(self):
        f=self.fixture;f.children();p=f.project();p['sequences'][0]['tracks'][0]['clips'][0]['out']=1;f.env['save_project'](p)
        path=f.short(2);before=f.raw();report=self.client.inspect_relink('m',str(path));self.assertFalse(report['ok'])
        self.client.calls.clear()
        with self.assertRaises(FilmocityError):self.client.apply_relink(report)
        self.assertEqual(self.client.calls,[]);self.assertEqual(f.raw(),before);self.assertFalse(f.manager.values)
    def test_actual_repeated_accepted_relink_is_noop_without_new_job_or_history(self):
        f=self.fixture;first=self.client.inspect_relink('m',str(f.replacement));self.client.apply_relink(first)
        before=f.raw();tasks=len(f.manager.values);history=copy.deepcopy(f.env['read_undo_history']('a'))
        again=self.client.inspect_relink('m',str(f.replacement));reply=self.client.apply_relink(again)
        self.assertFalse(reply['changed']);self.assertEqual(f.raw(),before);self.assertEqual(len(f.manager.values),tasks)
        self.assertEqual(f.env['read_undo_history']('a'),history)
    def test_actual_saved_but_lost_reply_is_not_automatically_replayed(self):
        f=self.fixture;report=self.client.inspect_relink('m',str(f.replacement));self.client.calls.clear();self.client.lose_reply=True
        with self.assertRaisesRegex(FilmocityError,'acknowledgement was lost'):self.client.apply_relink(report)
        self.assertEqual([p for p,_,_ in self.client.calls],['/api/media/relink'])
        self.assertEqual(f.project()['media']['m']['path'],str(f.replacement));self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)


if __name__=='__main__':unittest.main()
