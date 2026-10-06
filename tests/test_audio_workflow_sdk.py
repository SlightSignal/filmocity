"""Audio SDK ownership, read-only defaults and explicit reviewed mutation contracts."""
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'agent'))
from filmocity_client import Filmocity, FilmocityError


class Client(Filmocity):
    def __init__(self):
        super().__init__(); self.calls=[]
        self.context={'workspace':'w','project':'a','revision':'r1'}
        self.document={'sequences':[{'id':'s','tracks':[{'id':'a','clips':[{'id':'first'}]}, {'id':'b','clips':[{'id':'second'}]}]}]}
        self.task={'id':'t','kind':'audio_analysis','status':'ready'}
        self.result={'version':1,'kind':'audio_analysis','mode':'loudness','scope':'timeline','sequence':'s','clip_ids':['first']}
        self.plan={'fingerprint':'measured-f','ops':[{'op':'set'}]}
    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project/state':return {'context':copy.deepcopy(self.context),'project':copy.deepcopy(self.document)}
        if path in ('/api/audio/peak','/api/audio/measure','/api/audio/beats'):
            self.result['sequence']=body.get('sequence'); self.result['scope']='timeline' if body.get('sequence') else 'media'
            if not body.get('sequence'):self.plan['ops']=[]
            return {'ok':True,'task':copy.deepcopy(self.task),'context':copy.deepcopy(body['_context'])}
        if path=='/api/tasks':return {'context':copy.deepcopy(self.context),'tasks':[self.task]}
        if path=='/api/tasks/t/audio':return {'task':copy.deepcopy(self.task),'context':copy.deepcopy(self.context),'result':copy.deepcopy(self.result),'plan':copy.deepcopy(self.plan)}
        if path in ('/api/tasks/t/apply','/api/audio/gain'):return {'ok':True,'context':{**body['_context'],'revision':'r2'}}
        raise AssertionError(path)
    def applied(self):return [body for path,body,_ in self.calls if path.endswith('/apply')]


class AudioSDK(unittest.TestCase):
    def test_normalization_defaults_to_review_captures_once_and_keeps_batch_order(self):
        c=Client();r=c.normalize_audio(['second','first'],target=-21.3)
        self.assertEqual(sum(path=='/api/project/state' for path,_,_ in c.calls),1)
        body=next(b for p,b,_ in c.calls if p=='/api/audio/measure')
        self.assertEqual((body['sequence'],body['clip_ids'],body['target']),('s',['second','first'],-21.3))
        self.assertEqual(body['_context']['project'],'a');self.assertRegex(body['request_id'],r'^[0-9a-f]{32}$')
        self.assertEqual(r['plan']['fingerprint'],'measured-f');self.assertEqual(c.applied(),[])

    def test_explicit_apply_keeps_exact_reviewed_context_without_local_ops(self):
        c=Client();r=c.normalize_audio(['first']);c.context={'workspace':'w','project':'b','revision':'foreign'}
        c.apply_audio_analysis('t',r)
        self.assertEqual(c.applied(),[{'_context':r['context'],'fingerprint':'measured-f','actor':c.actor,'client':c.client}])
        self.assertEqual(c.applied()[0]['_context']['project'],'a')

    def test_peak_and_beat_conveniences_apply_only_on_explicit_false(self):
        c=Client();self.assertTrue(c.normalize_audio(['first'],target=-3.125,mode='peak',preview=False)['ok'])
        self.assertEqual(len(c.applied()),1);self.assertTrue(any(p=='/api/audio/peak' for p,_,_ in c.calls))
        c=Client();c.add_beat_markers('first',every=3)
        request=next(b for p,b,_ in c.calls if p=='/api/audio/beats');self.assertEqual(request['every'],3)
        self.assertEqual(c.applied(),[])
        c=Client();self.assertTrue(c.add_beat_markers('first',every=2,preview=False)['ok']);self.assertEqual(len(c.applied()),1)

    def test_raw_ranges_are_read_only_and_not_reinterpreted_by_client(self):
        for name in ('peak','loudness','beats'):
            c=Client();r=getattr(c,name)('sub',in_=2.125,out=4.5)
            requests=[b for p,b,_ in c.calls if p.startswith('/api/audio/')]
            with self.subTest(name=name):
                self.assertEqual(len(requests),1);self.assertEqual((requests[0]['media_id'],requests[0]['in'],requests[0]['out']),('sub',2.125,4.5))
                self.assertNotIn('clip_ids',requests[0]);self.assertEqual(c.applied(),[]);self.assertEqual(r['scope'],'media')

    def test_manual_gain_one_captured_transaction_and_explicit_sequence_required(self):
        c=Client();c.set_gain(['first','second'],-.005,mode='adjust')
        self.assertEqual([p for p,_,_ in c.calls],['/api/project/state','/api/audio/gain'])
        body=c.calls[-1][1];self.assertEqual((body['mode'],body['value'],body['clip_ids']),('adjust',-.005,['first','second']));self.assertNotIn('ops',body)
        c=Client()
        with self.assertRaises(FilmocityError):c.set_gain(['first'],0,context=c.context)
        self.assertEqual(c.calls,[])
        c.set_gain(['first'],0,context=c.context,seq_id='captured');self.assertEqual(len(c.calls),1)

    def test_manual_gain_invalid_numbers_modes_ids_refuse_before_request(self):
        cases=[([],0,{}),(['a','a'],0,{}),(['a'],float('nan'),{}),(['a'],True,{}),(['a'],25,{}),(['a'],0,{'mode':'add'}),(['a'],float('inf'),{'mode':'adjust'})]
        for ids,value,options in cases:
            c=Client()
            with self.subTest(ids=ids,value=value,options=options),self.assertRaises(FilmocityError):c.set_gain(ids,value,**options)
            self.assertEqual(c.calls,[])

    def test_queue_rejects_mixed_ambiguous_invalid_input_before_network(self):
        cases=[('other',{}),('peak',{'sequence':'s'}),('beats',{'sequence':'s','clip_ids':['a','b']}),('peak',{'sequence':'s','clip_ids':['a'],'media_id':'m'}),('loudness',{'media_id':'m','in_':2,'out':1}),('peak',{'media_id':'m','in_':True}),('peak',{'media_id':'m','target':float('inf')}),('beats',{'media_id':'m','every':0}),('beats',{'media_id':'m','every':True}),('beats',{'media_id':'m','target':-3}),('peak',{'media_id':'m','request_id':'wrong'})]
        for mode,options in cases:
            c=Client()
            with self.subTest(mode=mode,options=options),self.assertRaises(FilmocityError):c.start_audio_analysis(mode,**options)
            self.assertEqual(c.calls,[])

    def test_batch_capture_refuses_missing_duplicate_locked_before_queue(self):
        for mode in ('missing','duplicate','locked'):
            c=Client();track=c.document['sequences'][0]['tracks'][0]
            if mode=='missing':track['clips']=[]
            if mode=='duplicate':track['clips'].append({'id':'first'})
            if mode=='locked':track['locked']=True
            with self.subTest(mode=mode),self.assertRaises(FilmocityError):c.normalize_audio(['first'])
            self.assertEqual([p for p,_,_ in c.calls],['/api/project/state'])

    def test_raw_noop_wrong_kind_and_task_reviews_refuse_mutation(self):
        for mode in ('raw','empty','kind','id','context'):
            c=Client();r=c.normalize_audio(['first']);before=len(c.calls)
            if mode=='raw':r['result']['sequence']=None
            if mode=='empty':r['plan']['ops']=[]
            if mode=='kind':r['task']['kind']='sync'
            if mode=='id':r['task']['id']='other'
            if mode=='context':r.pop('context')
            with self.subTest(mode=mode),self.assertRaises(FilmocityError):c.apply_audio_analysis('t',r)
            self.assertEqual(len(c.calls),before)
        c=Client();c.plan['ops']=[];self.assertFalse(c.normalize_audio(['first'],preview=False)['changed']);self.assertEqual(c.applied(),[])

    def test_timeout_wrong_owner_kind_and_terminal_failures_do_not_replay(self):
        c=Client();c.task['status']='running'
        with patch('filmocity_client.time.monotonic',side_effect=[0,2]),self.assertRaisesRegex(FilmocityError,'not finished'):c.wait_audio_analysis('t',timeout=1,context=c.context)
        self.assertEqual([p for p,_,_ in c.calls],['/api/tasks'])
        for mode in ('owner','kind','cancelled','interrupted','error','applied'):
            c=Client();ctx=copy.deepcopy(c.context)
            if mode=='owner':c.context['project']='other'
            elif mode=='kind':c.task['kind']='sync'
            else:c.task['status']=mode
            with self.subTest(mode=mode),self.assertRaises(FilmocityError):c.wait_audio_analysis('t',context=ctx)
            self.assertEqual([p for p,_,_ in c.calls],['/api/tasks'])

    def test_explicit_capture_and_request_id_do_not_reread_active_project(self):
        c=Client();ctx={**c.context,'project':'captured'}
        c.start_audio_analysis('peak',sequence='old-sequence',clip_ids=['first'],context=ctx,request_id='c'*32,target=-2.345)
        self.assertEqual(len(c.calls),1);self.assertEqual(c.calls[0][1]['_context'],ctx);self.assertEqual(c.calls[0][1]['request_id'],'c'*32)


class SavedRouteSDK(unittest.TestCase):
    def setUp(self):
        import test_audio_workflow as routes
        self.fixture=routes.StoreAudio(methodName='runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        fixture=self.fixture
        class ServerClient(Filmocity):
            def __init__(self):super().__init__(actor='human');self.calls=[]
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                if path=='/api/project/state':return {'project':fixture.project(),'context':fixture.current()}
                if path=='/api/tasks':
                    for identity,value in fixture.manager.values.items():
                        if value['record']['status']=='queued':
                            value['result']=fixture.env['_task_audio_workflow'](value['payload'],routes.TaskContext(fixture.manager,identity))
                            value['record']['status']='ready';fixture.manager.store.save(value)
                    return {'context':fixture.current(),'tasks':[copy.deepcopy(v['record']) for v in fixture.manager.values.values()]}
                mapping={'/api/audio/peak':'audio_peak','/api/audio/measure':'audio_measure','/api/audio/beats':'audio_beats','/api/audio/gain':'audio_gain'}
                try:
                    if path in mapping:return fixture.route(mapping[path],body)
                    parts=path.split('/')
                    if len(parts)==5 and parts[:3]==['','api','tasks']:
                        return fixture.route({'audio':'background_audio_review','apply':'background_task_apply'}[parts[4]],body,parts[3])
                except routes.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                raise AssertionError(path)
        self.client=ServerClient()

    def test_manual_sdk_command_saves_actual_curve_and_undo_once(self):
        f=self.fixture;f.edit_clip(audio={'gain_db':-6},keyframes={'audio.gain_db':[{'t':-2,'v':-8},{'t':4,'v':-4}]})
        before=f.project();self.client.set_gain(['ca'],-.125,mode='adjust',seq_id='s');after=f.project()
        self.assertEqual(f.clip(after)['audio']['gain_db'],-6.125)
        self.assertEqual([p['v'] for p in f.clip(after)['keyframes']['audio.gain_db']],[-8.125,-4.125])
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],before['sequences'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],after['sequences'])

    def test_real_normalization_review_cannot_apply_after_project_switch(self):
        f=self.fixture;before=f.raw();review=self.client.normalize_audio(['ca'],target=-3,mode='peak',seq_id='s')
        self.assertEqual(f.raw(),before);self.assertEqual(review['result']['scope'],'timeline')
        f.env['set_active_project']('b');foreign=f.raw()
        with self.assertRaises(FilmocityError):self.client.apply_audio_analysis(review['task']['id'],review)
        self.assertEqual(f.raw(),foreign);self.assertEqual(f.raw('a'),before)
        f.env['set_active_project']('a');self.assertTrue(self.client.apply_audio_analysis(review['task']['id'],review)['ok'])
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)

    def test_real_raw_sdk_measurement_keeps_readonly_result_and_requested_range(self):
        f=self.fixture;before=f.raw();result=self.client.peak('a',in_=1,out=3)
        self.assertEqual((result['scope'],result['clock'],result['range']),('media','clip-local',{'start':1,'end':3}))
        self.assertEqual(result['measurements'][0]['duration'],2);self.assertIsInstance(result['measurements'][0]['peak_db'],float)
        self.assertEqual(f.raw(),before);self.assertEqual(len(f.env['read_undo_history']('a')['undo']),0)
        self.assertFalse(any(path.endswith('/apply') for path,_,_ in self.client.calls))


if __name__=='__main__':unittest.main()
