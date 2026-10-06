"""Managed synchronization SDK keeps source matching read-only and edits explicitly reviewed."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from filmocity_client import Filmocity, FilmocityError


class Client(Filmocity):
    def __init__(self):
        super().__init__(); self.calls=[]
        self.context={'workspace':'w','project':'p','revision':'r1'}
        self.project_doc={'media':{'m':{'has_audio':True},'n':{'has_audio':True}},'sequences':[{'id':'s','tracks':[
            {'id':'a','clips':[{'id':'first','media_id':'m'}]}, {'id':'b','clips':[{'id':'second','media_id':'n'}]}]}]}
        self.task={'id':'task','kind':'sync','status':'ready'}
        self.result={'kind':'sync','mode':'timeline','offsets':{'first':0,'second':.125}}
        self.plan={'fingerprint':'sync-f','ops':[{'op':'set'}]}

    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':return {'context':copy.deepcopy(self.context),'project':copy.deepcopy(self.project_doc)}
        if path=='/api/audio/sync':return {'ok':True,'task':copy.deepcopy(self.task),'context':copy.deepcopy(body['_context'])}
        if path=='/api/tasks':return {'context':copy.deepcopy(self.context),'tasks':[self.task]}
        if path=='/api/tasks/task/sync':return {'ok':True,'task':copy.deepcopy(self.task),'context':copy.deepcopy(self.context),'result':copy.deepcopy(self.result),'plan':copy.deepcopy(self.plan)}
        if path=='/api/tasks/task/apply':return {'ok':True,'context':{**self.context,'revision':'r2'}}
        if path=='/api/clip/replace-source':return {'ok':True,'context':{**body['_context'],'revision':'r2'}}
        raise AssertionError(path)


class SyncSDK(unittest.TestCase):
    def test_source_replacement_uses_captured_context_and_does_not_construct_local_clip_ops(self):
        c=Client();self.assertTrue(c.replace_source('first','n',in_=2,out=8)['ok'])
        self.assertEqual([path for path,_,_ in c.calls],['/api/project/state','/api/clip/replace-source'])
        body=c.calls[-1][1];self.assertEqual((body['sequence'],body['clip_id'],body['media_id'],body['in'],body['out']),('s','first','n',2,8))
        self.assertEqual(body['_context']['revision'],'r1');self.assertNotIn('ops',body)
        c.calls.clear();context={**c.context,'revision':'captured'};c.replace_source('first','n',seq_id='explicit',context=context)
        self.assertEqual([path for path,_,_ in c.calls],['/api/clip/replace-source']);self.assertEqual(c.calls[-1][1]['_context'],context)

    def test_invalid_source_replacement_ranges_refuse_before_network(self):
        for params in ({'in_':True},{'in_':float('nan')},{'in_':-1},{'in_':2,'out':1},{'out':float('inf')}):
            c=Client()
            with self.subTest(params=params),self.assertRaises(FilmocityError):c.replace_source('first','n',**params)
            self.assertEqual(c.calls,[])

    def test_raw_sources_queue_once_and_return_read_only_quality_result(self):
        c=Client();c.result['mode']='media';c.plan['ops']=[]
        self.assertEqual(c.sync(['n','m']),c.result)
        request=next(body for path,body,_ in c.calls if path=='/api/audio/sync')
        self.assertEqual(request['media_ids'],['n','m']);self.assertNotIn('clip_ids',request)
        self.assertEqual(request['_context'],c.context);self.assertRegex(request['request_id'],r'^[a-f0-9]{32}$')
        self.assertFalse(any(path.endswith('/apply') for path,_,_ in c.calls))

    def test_timeline_default_review_captures_one_project_and_reference_order(self):
        c=Client();reviewed=c.synchronize(['second','first'])
        request=next(body for path,body,_ in c.calls if path=='/api/audio/sync')
        self.assertEqual((request['sequence'],request['clip_ids']),('s',['second','first']))
        self.assertEqual(sum(path=='/api/project/state' for path,_,_ in c.calls),1)
        self.assertEqual(reviewed['plan']['fingerprint'],'sync-f');self.assertFalse(any(path.endswith('/apply') for path,_,_ in c.calls))

    def test_explicit_apply_sends_only_reviewed_context_and_fingerprint(self):
        c=Client();self.assertTrue(c.synchronize(['first','second'],preview=False)['ok'])
        applied=[body for path,body,_ in c.calls if path.endswith('/apply')];self.assertEqual(len(applied),1)
        self.assertEqual(applied[0]['fingerprint'],'sync-f');self.assertEqual(applied[0]['_context']['revision'],'r1');self.assertNotIn('ops',applied[0])

    def test_an_unrelated_later_revision_cannot_replace_review_context(self):
        c=Client();reviewed=c.synchronize(['first','second']);c.context['revision']='later';c.apply_sync('task',reviewed)
        self.assertEqual(c.calls[-1][1]['_context']['revision'],'r1')

    def test_noop_and_raw_or_wrong_task_reviews_cannot_mutate(self):
        c=Client();c.plan['ops']=[];result=c.synchronize(['first','second'],preview=False)
        self.assertFalse(result['changed']);self.assertFalse(any(path.endswith('/apply') for path,_,_ in c.calls))
        for mode in ('raw','wrong-kind','wrong-task','missing-context'):
            c=Client();r=c.synchronize(['first','second']);count=len(c.calls)
            if mode=='raw':r['result']['mode']='media'
            if mode=='wrong-kind':r['task']['kind']='analysis'
            if mode=='wrong-task':r['task']['id']='other'
            if mode=='missing-context':r.pop('context')
            with self.subTest(mode=mode),self.assertRaises(FilmocityError):c.apply_sync('task',r)
            self.assertEqual(len(c.calls),count)

    def test_missing_locked_disabled_and_silent_stream_targets_refuse_before_queue(self):
        for mode in ('missing','locked','disabled','no-audio','duplicate'):
            c=Client();track=c.project_doc['sequences'][0]['tracks'][1]
            if mode=='missing':track['clips']=[]
            if mode=='locked':track['locked']=True
            if mode=='disabled':track['clips'][0]['enabled']=False
            if mode=='no-audio':c.project_doc['media']['n']['has_audio']=False
            if mode=='duplicate':track['clips'].append(copy.deepcopy(track['clips'][0]))
            with self.subTest(mode=mode),self.assertRaises(FilmocityError):c.synchronize(['first','second'])
            self.assertEqual([path for path,_,_ in c.calls],['/api/project/state'])

    def test_invalid_id_sets_and_mixed_modes_refuse_without_requests(self):
        for ids in (None,[],['m'],['m','m'],['m',None],list('123456789'),'media'):
            c=Client()
            with self.subTest(ids=ids),self.assertRaises(FilmocityError):c.start_sync(media_ids=ids)
            self.assertEqual(c.calls,[])
        c=Client()
        with self.assertRaises(FilmocityError):c.start_sync(media_ids=['m','n'],sequence='s',clip_ids=['first','second'])
        self.assertEqual(c.calls,[])

    def test_timeout_foreign_owner_and_wrong_kind_never_replay_or_apply(self):
        c=Client();c.task['status']='running'
        with patch('filmocity_client.time.monotonic',side_effect=[0,2]),self.assertRaisesRegex(FilmocityError,'not finished'):c.wait_sync('task',timeout=1,context=c.context)
        self.assertEqual([path for path,_,_ in c.calls],['/api/tasks'])
        for mode in ('owner','kind','failed'):
            c=Client();context=copy.deepcopy(c.context)
            if mode=='owner':c.context['project']='other'
            if mode=='kind':c.task['kind']='analysis'
            if mode=='failed':c.task['status']='error';c.task['message']='Ambiguous match'
            with self.subTest(mode=mode),self.assertRaises(FilmocityError):c.wait_sync('task',context=context)
            self.assertEqual([path for path,_,_ in c.calls],['/api/tasks'])


if __name__=='__main__':unittest.main()
