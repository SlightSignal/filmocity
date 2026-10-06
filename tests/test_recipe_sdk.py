"""Owned recipe SDK request/receipt behavior; real saved routes are added below."""
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
        self.document={'media':{'shot':{},'broll':{},'music':{}},'sequences':[{'id':'s','tracks':[{'id':'v','clips':[{'id':'speech','media_id':'shot'}]}]}]}
        self.task={'id':'t','kind':'recipe','status':'ready'}
        self.result={'version':1,'kind':'recipe','mode':'talking_head','sequence':'s'}
        self.plan={'fingerprint':'reviewed-recipe','ops':[{'op':'set','path':'/sequences/0','value':{}}]}
    def _call(self,path,body=None,method=None):
        self.calls.append((path,copy.deepcopy(body),method))
        if path=='/api/project':return copy.deepcopy(self.document)
        if path=='/api/project/state':return {'project':copy.deepcopy(self.document),'context':copy.deepcopy(self.context)}
        if path.startswith('/api/recipes/'):
            self.result['mode']=path.rsplit('/',1)[-1];self.result['sequence']=body['sequence']
            return {'ok':True,'task':copy.deepcopy(self.task),'context':copy.deepcopy(body['_context'])}
        if path=='/api/tasks':return {'context':copy.deepcopy(self.context),'tasks':[copy.deepcopy(self.task)]}
        if path=='/api/tasks/t/recipe':return {'task':copy.deepcopy(self.task),'context':copy.deepcopy(self.context),'result':copy.deepcopy(self.result),'plan':copy.deepcopy(self.plan)}
        if path=='/api/tasks/t/apply':return {'ok':True,'context':{**body['_context'],'revision':'r2'}}
        raise AssertionError(path)
    def applied(self):return [body for path,body,_ in self.calls if path.endswith('/apply')]
    def submitted(self):return [body for path,body,_ in self.calls if path.startswith('/api/recipes/')]


class RecipeSDK(unittest.TestCase):
    def test_talking_head_defaults_to_review_and_captures_one_saved_owner(self):
        c=Client();review=c.talking_head('speech',broll=['broll','shot'],punch_every=0)
        self.assertEqual(sum(p=='/api/project/state' for p,_,_ in c.calls),1)
        body=c.submitted()[0];self.assertEqual((body['sequence'],body['clip_id'],body['broll']),('s','speech',['broll','shot']))
        self.assertEqual((body['punch_every'],body['silences'],body['voice_preset'],body['captions']),(0,True,True,True))
        self.assertEqual(body['_context']['project'],'a');self.assertRegex(body['request_id'],r'^[a-f0-9]{32}$')
        self.assertEqual(review['plan']['fingerprint'],'reviewed-recipe');self.assertEqual(c.applied(),[])

    def test_reel_preserves_order_repetitions_and_visible_destination_settings(self):
        c=Client();review=c.reel(['broll','shot','broll'],music='music',name='Campaign',canvas='current',rhythm='onsets',framing='contain',captions=True,caption_text='Actual cue',sfx=False)
        body=c.submitted()[0];self.assertEqual(body['shots'],['broll','shot','broll'])
        self.assertEqual((body['name'],body['canvas'],body['rhythm'],body['framing']),('Campaign','current','onsets','contain'))
        self.assertEqual((body['captions'],body['caption_text'],body['sfx']),(True,'Actual cue',False))
        self.assertEqual(review['result']['mode'],'reel');self.assertEqual(c.applied(),[])
        c=Client();c.reel(['shot']);self.assertEqual((c.submitted()[0]['canvas'],c.submitted()[0]['rhythm'],c.submitted()[0]['music']),('portrait','even',None))

    def test_explicit_apply_uses_original_review_context_and_no_client_ops(self):
        c=Client();review=c.talking_head('speech');c.context['project']='foreign'
        c.apply_recipe('t',review)
        self.assertEqual(c.applied(),[{'_context':review['context'],'fingerprint':'reviewed-recipe','actor':c.actor,'client':c.client}])
        self.assertEqual(c.applied()[0]['_context']['project'],'a')
        for recipe,args in (('talking_head',('speech',)),('reel',(['shot'],))):
            c=Client();self.assertTrue(getattr(c,recipe)(*args,preview=False)['ok']);self.assertEqual(len(c.applied()),1)

    def test_supplied_context_requires_explicit_sequence_and_never_reads_foreign_state(self):
        c=Client()
        with self.assertRaises(FilmocityError):c.start_recipe('reel',shots=['shot'],context=c.context)
        self.assertEqual(c.calls,[])
        c.start_recipe('talking_head',clip_id='captured',sequence='captured-seq',context=c.context,request_id='a'*32)
        self.assertEqual(len(c.calls),1);self.assertEqual(c.submitted()[0]['sequence'],'captured-seq')
        self.assertEqual(c.submitted()[0]['request_id'],'a'*32)

    def test_invalid_settings_reject_before_queue_without_coercing_strings_or_bools(self):
        cases=[('talking_head',{'clip_id':'speech','punch_every':-1}),('talking_head',{'clip_id':'speech','punch_every':float('inf')}),
               ('talking_head',{'clip_id':'speech','silences':'false'}),('talking_head',{'clip_id':'speech','captions':0}),
               ('talking_head',{'clip_id':'speech','broll':'shot'}),('talking_head',{'clip_id':'speech','pad':True}),
               ('reel',{'shots':['shot'],'target':'inf'}),('reel',{'shots':['shot'],'target':float('nan')}),
               ('reel',{'shots':[]}),('reel',{'shots':['shot']*101}),('reel',{'shots':['shot'],'canvas':'square'}),
               ('reel',{'shots':['shot'],'rhythm':'onsets'}),('reel',{'shots':['shot'],'name':''}),
               ('reel',{'shots':['shot'],'caption_style':{'size':float('nan')}}),('reel',{'shots':['shot'],'sfx':1}),
               ('reel',{'shots':['shot'],'_context':{}}),('reel',{'shots':['shot'],'actor':'human'})]
        for mode,settings in cases:
            c=Client()
            with self.subTest(mode=mode,settings=settings),self.assertRaises(FilmocityError):c.start_recipe(mode,**settings)
            self.assertEqual(c.calls,[])

    def test_missing_duplicate_locked_targets_and_unknown_sequences_never_queue(self):
        for failure in ('missing','duplicate','locked','sequence'):
            c=Client();track=c.document['sequences'][0]['tracks'][0]
            if failure=='missing':track['clips']=[]
            if failure=='duplicate':track['clips'].append(copy.deepcopy(track['clips'][0]))
            if failure=='locked':track['locked']=True
            with self.subTest(failure=failure),self.assertRaises(FilmocityError):c.talking_head('speech',seq_id='missing' if failure=='sequence' else 's')
            self.assertEqual(c.submitted(),[])
        c=Client()
        with self.assertRaises(FilmocityError):c.reel(['missing'])
        self.assertEqual(c.submitted(),[])

    def test_sequence_helper_never_substitutes_first_for_explicit_unknown_or_duplicate(self):
        c=Client();self.assertEqual(c.sequence()['id'],'s');self.assertEqual(c.sequence('s')['id'],'s')
        with self.assertRaises(FilmocityError):c.sequence('missing')
        c.document['sequences'].append(copy.deepcopy(c.document['sequences'][0]))
        with self.assertRaises(FilmocityError):c.sequence('s')
        c.document['sequences']=[];self.assertIsNone(c.sequence())

    def test_noop_result_does_not_create_an_apply_request(self):
        c=Client();c.plan['ops']=[];result=c.talking_head('speech',preview=False)
        self.assertFalse(result['changed']);self.assertEqual(c.applied(),[])

    def test_wrong_kind_id_mode_context_and_empty_review_refuse_apply(self):
        for failure in ('kind','id','mode','context','empty','result'):
            c=Client();review=c.talking_head('speech')
            if failure=='kind':review['task']['kind']='analysis'
            if failure=='id':review['task']['id']='other'
            if failure=='mode':review['result']['mode']='unknown'
            if failure=='context':review.pop('context')
            if failure=='empty':review['plan']['ops']=[]
            if failure=='result':review['result']['kind']='audio_analysis'
            with self.subTest(failure=failure),self.assertRaises(FilmocityError):c.apply_recipe('t',review)
            self.assertEqual(c.applied(),[])

    def test_wrong_owner_or_terminal_task_fails_without_replay(self):
        for state in ('owner','kind','cancelled','error','interrupted','applied','done'):
            c=Client();owner=copy.deepcopy(c.context)
            if state=='owner':c.context['project']='b'
            elif state=='kind':c.task['kind']='analysis'
            else:c.task['status']=state
            with self.subTest(state=state),self.assertRaises(FilmocityError):c.wait_recipe('t',context=owner)
            self.assertEqual([p for p,_,_ in c.calls],['/api/tasks'])

    def test_timeout_and_invalid_wait_do_not_resubmit_or_apply(self):
        c=Client();c.task['status']='running'
        with patch('filmocity_client.time.monotonic',side_effect=[0,2]),self.assertRaisesRegex(FilmocityError,'not finished'):
            c.wait_recipe('t',timeout=1,context=c.context)
        self.assertEqual([p for p,_,_ in c.calls],['/api/tasks'])
        for timeout in (0,-1,float('inf'),True):
            c=Client()
            with self.subTest(timeout=timeout),self.assertRaises(FilmocityError):c.talking_head('speech',timeout=timeout)
            self.assertEqual(c.calls,[])

    def test_uncertain_apply_response_does_not_automatically_retry(self):
        c=Client();review=c.talking_head('speech');original=c._call
        def lost(path,body=None,method=None):
            response=original(path,body,method)
            if path.endswith('/apply'):raise OSError('response lost after server accepted')
            return response
        c._call=lost
        with self.assertRaisesRegex(OSError,'response lost'):c.apply_recipe('t',review)
        self.assertEqual(len(c.applied()),1)

    def test_invalid_mode_request_identity_or_preview_refuses_without_network(self):
        for mode,options in (('unknown',{}),('reel',{'shots':['shot'],'request_id':'x'})):
            c=Client()
            with self.assertRaises(FilmocityError):c.start_recipe(mode,**options)
            self.assertEqual(c.calls,[])
        c=Client()
        with self.assertRaises(FilmocityError):c.reel(['shot'],preview='false')
        self.assertEqual(c.calls,[])


class SavedRecipeSDK(unittest.TestCase):
    def setUp(self):
        import test_recipe_workflow as fixtures
        self.fixture=fixtures.StoreRecipes(methodName='runTest');self.fixture.setUp();self.addCleanup(self.fixture.tearDown);self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        class ServerClient(Filmocity):
            def __init__(self):super().__init__();self.calls=[]
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                try:
                    if path=='/api/project/state':return {'project':f.project(),'context':f.current()}
                    if path=='/api/tasks':return {'context':f.current(),'tasks':[copy.deepcopy(v['record']) for v in f.manager.values.values()]}
                    if path.startswith('/api/recipes/'):
                        response=f.route('recipe_'+path.rsplit('/',1)[-1],body);identity=response['task']['id'];task=f.manager.values[identity]
                        task['result']=f.env['_task_recipe_workflow'](task['payload'],fixtures.TaskContext(f.manager,identity))
                        task['record']['status']='ready';f.manager.store.save(task)
                        return response
                    if path.startswith('/api/tasks/'):
                        identity=path.split('/')[3];action=path.rsplit('/',1)[-1]
                        return f.route('background_recipe_review' if action=='recipe' else 'background_task_apply',body,identity)
                except fixtures.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                raise AssertionError(path)
        self.client=ServerClient()

    def test_real_sdk_silence_review_is_readonly_and_apply_saves_one_history_step(self):
        f=self.fixture;before=f.project();review=self.client.talking_head('c',silences=True,punch_every=0,voice_preset=False,captions=False,seq_id='s')
        self.assertEqual(f.project(),before);self.assertGreater(review['plan']['summary']['removed_duration'],0)
        self.assertFalse(any(p.endswith('/apply') for p,_,_ in self.client.calls))
        self.client.apply_recipe(review['task']['id'],review);after=f.project()
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        self.assertTrue(all(c['audio']['gain_db']==-4 for c in after['sequences'][0]['tracks'][0]['clips']))
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],before['sequences'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],after['sequences'])

    def test_real_sdk_reel_publishes_generated_sound_with_new_sequence_atomically(self):
        f=self.fixture;before=f.project()
        result=self.client.reel(['m','m'],target=6,hook='',cta='',canvas='current',name='SDK Reel',captions=False,sfx=True,seq_id='s',preview=False)
        after=f.project();self.assertTrue(result['ok']);self.assertNotEqual(result['sequence'],'s')
        self.assertEqual(after['sequences'][0],before['sequences'][0]);seq=after['sequences'][1]
        self.assertEqual((seq['name'],seq['width'],seq['height']),('SDK Reel',64,48))
        created=set(after['media'])-set(before['media']);self.assertEqual(len(created),1)
        self.assertTrue(Path(after['media'][created.pop()]['path']).is_file())
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['media'],before['media'])
        self.assertEqual(f.project()['sequences'],before['sequences'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['sequences'],after['sequences'])

    def test_real_sdk_foreign_apply_refuses_without_rebinding_or_retry(self):
        f=self.fixture;review=self.client.talking_head('c',silences=False,punch_every=3,voice_preset=False,captions=False,seq_id='s')
        original=f.raw();f.env['set_active_project']('b');foreign=f.raw()
        with self.assertRaises(FilmocityError):self.client.apply_recipe(review['task']['id'],review)
        self.assertEqual(f.raw(),foreign);self.assertEqual(f.raw('a'),original)
        self.assertEqual(sum(p.endswith('/apply') for p,_,_ in self.client.calls),1)
        self.assertEqual(sum(p.startswith('/api/recipes/') for p,_,_ in self.client.calls),1)



if __name__=='__main__':unittest.main()
