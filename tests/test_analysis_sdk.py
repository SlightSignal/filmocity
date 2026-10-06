"""SDK analysis waits and applies only the reviewed, context-bound server plan."""
import copy
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'agent'))
from filmocity_client import Filmocity, FilmocityError

class Client(Filmocity):
    def __init__(self):
        super().__init__();self.calls=[];self.context={'workspace':'w','project':'p','revision':'r1'}
        self.project_doc={'sequences':[{'id':'s','tracks':[{'id':'v','clips':[{'id':'c','media_id':'m','in_':2,'out':8,'time_remap':[{'t':0,'v':2}]}]}]}]}
        self.task={'id':'t','kind':'analysis','status':'ready'};self.result={'cuts':[3,7],'silences':[{'start':3,'end':4}],'removed':1}
        self.plan={'fingerprint':'reviewed','ops':[{'op':'set','path':'/sequences/0/tracks/0/clips','value':[]}]}
    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':return {'context':copy.deepcopy(self.context),'project':copy.deepcopy(self.project_doc)}
        if path in ['/api/media/scenes','/api/audio/silences']:return {'ok':True,'task':self.task,'context':copy.deepcopy(body['_context'])}
        if path=='/api/tasks':return {'context':copy.deepcopy(self.context),'tasks':[self.task]}
        if path=='/api/tasks/t/analysis':return {'ok':True,'context':copy.deepcopy(self.context),'task':self.task,'result':self.result,'plan':self.plan}
        if path=='/api/tasks/t/apply':return {'ok':True,'removed':1,'context':{**self.context,'revision':'r2'}}
        raise AssertionError(path)

class AnalysisSDK(unittest.TestCase):
    def test_raw_analysis_uses_captured_context_and_null_default_out_without_applying(self):
        for kind in ['scenes','silences']:
            client=Client();result=getattr(client,kind)('m')
            self.assertEqual(result,client.result['cuts'] if kind=='scenes' else client.result)
            body=next(body for path,body,_ in client.calls if path in ['/api/media/scenes','/api/audio/silences'])
            self.assertEqual(body['_context'],client.context);self.assertIsNone(body['out']);self.assertEqual(len(body['request_id']),32)
            self.assertFalse(any(path.endswith('/apply') or method=='PATCH' for path,_,method in client.calls))
    def test_remove_uses_selected_ramped_window_and_one_reviewed_server_apply(self):
        client=Client();self.assertTrue(client.remove_silences('c')['ok'])
        submission=next(body for path,body,_ in client.calls if path=='/api/audio/silences')
        self.assertEqual((submission['sequence'],submission['clip_id'],submission['in'],submission['out']),('s','c',2,8))
        apply=[body for path,body,_ in client.calls if path.endswith('/apply')];self.assertEqual(len(apply),1)
        self.assertEqual(apply[0]['fingerprint'],'reviewed');self.assertEqual(apply[0]['_context'],client.context)
        self.assertNotIn('ops',apply[0]);self.assertFalse(any(method=='PATCH' for _,_,method in client.calls))
    def test_preview_never_applies_and_explicit_apply_uses_retained_context(self):
        client=Client();review=client.remove_silences('c',preview=True);client.context['revision']='changed'
        self.assertFalse(any(path.endswith('/apply') for path,_,_ in client.calls))
        client.apply_analysis('t',review);self.assertEqual(client.calls[-1][1]['_context']['revision'],'r1')
    def test_locked_held_missing_source_and_missing_target_reject_before_submission(self):
        for mode in ['locked','held','source','missing']:
            client=Client();track=client.project_doc['sequences'][0]['tracks'][0]
            if mode=='locked':track['locked']=True
            if mode=='held':track['clips'][0]['hold']=True
            if mode=='source':track['clips'][0]['media_id']=None
            with self.assertRaises(FilmocityError):client.remove_silences('other' if mode=='missing' else 'c')
            self.assertEqual([path for path,_,_ in client.calls],['/api/project/state'])
    def test_cancelled_failed_and_foreign_owner_never_review_or_apply(self):
        for status in ['error','cancelled','interrupted','applied','done','foreign']:
            client=Client();client.task['status']=status if status!='foreign' else 'ready'
            with self.assertRaises(FilmocityError):client.wait_analysis('t',context={**client.context,'project':'other'} if status=='foreign' else client.context)
            self.assertEqual([path for path,_,_ in client.calls],['/api/tasks'])
    def test_wrong_review_and_invalid_poll_bounds_reject_without_network_or_replay(self):
        client=Client()
        for review in [{},{'task':{'id':'other'},'plan':client.plan,'context':client.context}]:
            with self.assertRaises(FilmocityError):client.apply_analysis('t',review)
        for timeout in [0,-1,float('inf'),True]:
            with self.assertRaises(FilmocityError):client.wait_analysis('t',timeout=timeout)
        self.assertEqual(client.calls,[])
    def test_noop_analysis_does_not_create_an_apply_or_project_edit(self):
        client=Client();client.plan['ops']=[];self.assertEqual(client.remove_silences('c')['removed'],0)
        self.assertFalse(any(path.endswith('/apply') for path,_,_ in client.calls))

if __name__=='__main__':unittest.main()
