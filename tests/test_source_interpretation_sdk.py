"""Captured interpretation Review/Apply transport and saved-source integration."""
import copy
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'agent'))
from filmocity_client import Filmocity, FilmocityError


class Client(Filmocity):
    def __init__(self):
        super().__init__(actor='agent', client='interpretation-sdk')
        self.owner = {'workspace':'workspace / A', 'project':'project & one', 'revision':'saved-1'}
        self.calls = []; self.switch = False; self.fail = None
        self.review_change = lambda r: None; self.apply_change = lambda r: None

    def _call(self, path, body=None, method=None):
        self.calls.append((path, copy.deepcopy(body), method))
        if path == '/api/project/state':
            result = {'project':{'media':{'m':{'id':'m'}, 'sub/sl~ç':{'id':'sub/sl~ç'}}}, 'context':copy.deepcopy(self.owner)}
            if self.switch: self.owner['project'] = 'foreign'
            return result
        if path == self.fail: raise FilmocityError('Response lost; inspect saved state')
        ids = [body['media_id']] + (['fullmix'] if body['include_fullmix'] else [])
        if path == '/api/media/interpret/review':
            result = {'ok':True, 'context':copy.deepcopy(body['_context']), 'kind':'source_interpretation',
                'media_id':body['media_id'], 'requested_media_id':body['media_id'], 'affected_media_ids':ids,
                'settings':{k:body[k] for k in ('fps','include_fullmix')}, 'fingerprint':'a'*64,
                'summary':{'native_rate':'30','target_rate':body['fps'],'windows':[],'uses':[]}, 'issues':[]}
            self.review_change(result); return result
        assert path == '/api/media/interpret'
        result = {'ok':True, 'changed':True, 'context':{**body['_context'],'revision':'saved-2'},
            'project':body['_context']['project'], 'kind':'source_interpretation', 'media_id':body['media_id'],
            'requested_media_id':body['media_id'], 'affected_media_ids':ids, 'media':{'id':body['media_id']},
            'summary':{}, 'warnings':[], 'preparation':{'tasks':[],'warnings':[]}}
        self.apply_change(result); return result


class InterpretationSDK(unittest.TestCase):
    def test_review_captures_once_without_following_active_switch(self):
        c=Client(); owner=copy.deepcopy(c.owner); c.switch=True
        review=c.review_interpretation('sub/sl~ç','30000/1001')
        self.assertEqual(review['context'],owner)
        self.assertEqual([p for p,_,_ in c.calls],['/api/project/state','/api/media/interpret/review'])
        self.assertEqual(c.calls[-1][1],{'media_id':'sub/sl~ç','fps':'30000/1001','include_fullmix':False,
            '_context':owner,'actor':'agent','client':'interpretation-sdk'})

    def test_explicit_owner_never_reads_and_exact_custom_rational_survives(self):
        c=Client(); c.review_interpretation('captured','127/7',context=c.owner)
        self.assertEqual(len(c.calls),1); self.assertEqual(c.calls[0][1]['fps'],'127/7')

    def test_native_restore_and_fullmix_consent_are_explicit(self):
        c=Client(); review=c.review_interpretation('m',None,include_fullmix=True)
        self.assertEqual(review['settings'],{'fps':None,'include_fullmix':True})
        self.assertEqual(review['affected_media_ids'],['m','fullmix'])
        c.calls.clear(); c.apply_interpretation(review)
        self.assertIsNone(c.calls[0][1]['fps']); self.assertTrue(c.calls[0][1]['include_fullmix'])

    def test_invalid_rate_or_consent_refuses_before_any_read(self):
        for rate in ('not-a-rate','24garbage','',True,False,0,-1,1001,'1/0','1/','1/2/3',float('nan'),float('inf'),[],{},'9'*81):
            c=Client()
            with self.subTest(rate=rate), self.assertRaises(FilmocityError): c.review_interpretation('m',rate)
            self.assertEqual(c.calls,[])
        for consent in (1,0,'yes',None):
            c=Client()
            with self.assertRaises(FilmocityError): c.review_interpretation('m',24,include_fullmix=consent)
            self.assertEqual(c.calls,[])

    def test_decimal_ntsc_alias_matches_canonical_fraction(self):
        c=Client(); result=c.review_interpretation('m',29.97)
        self.assertEqual(result['settings']['fps'],'30000/1001')

    def test_missing_source_or_invalid_owner_never_dispatches_review(self):
        c=Client()
        with self.assertRaises(FilmocityError): c.review_interpretation('missing',24)
        self.assertEqual([p for p,_,_ in c.calls],['/api/project/state'])
        c=Client()
        with self.assertRaises(FilmocityError): c.review_interpretation('m',24,context={})
        self.assertEqual(c.calls,[])

    def test_review_must_match_requested_owner_source_settings_and_structure(self):
        variants=[lambda r:r['context'].update(project='foreign'),lambda r:r.update(requested_media_id='other'),
            lambda r:r.update(media_id='other'),lambda r:r['settings'].update(fps='25'),
            lambda r:r['settings'].update(include_fullmix=True),lambda r:r.update(kind='other'),
            lambda r:r.update(affected_media_ids=['m','m']),lambda r:r.update(fingerprint='bad'),
            lambda r:r.update(issues=None),lambda r:r.update(summary=None),lambda r:r.update(ok=1)]
        for change in variants:
            c=Client(); c.review_change=change
            with self.subTest(change=change),self.assertRaises(FilmocityError): c.review_interpretation('m',24)
            self.assertEqual(len(c.calls),2)

    def test_refused_review_is_returned_for_inspection_and_cannot_apply(self):
        c=Client(); c.review_change=lambda r:r.update(ok=False,fingerprint=None,issues=[{'message':'Source range invalid'}])
        review=c.review_interpretation('m',24); self.assertFalse(review['ok']); c.calls.clear()
        with self.assertRaises(FilmocityError): c.apply_interpretation(review)
        self.assertEqual(c.calls,[])

    def test_apply_uses_exact_review_without_new_capture_or_mutating_it(self):
        c=Client(); review=c.review_interpretation('m','127/7',include_fullmix=True)
        saved=copy.deepcopy(review); c.calls.clear(); c.owner['project']='foreign'
        result=c.apply_interpretation(review); self.assertTrue(result['changed']); self.assertEqual(review,saved)
        self.assertEqual(c.calls,[('/api/media/interpret',{'media_id':'m','fps':'127/7','include_fullmix':True,
            '_context':saved['context'],'fingerprint':saved['fingerprint'],'actor':'agent','client':'interpretation-sdk'},None)])

    def test_malformed_apply_reviews_do_not_fall_back_to_current_project(self):
        c=Client(); review=c.review_interpretation('m',24); c.calls.clear()
        for override in ({'context':None},{'context':{}},{'settings':{}},{'fingerprint':''},{'affected_media_ids':[]},{'kind':'other'}):
            with self.subTest(override=override),self.assertRaises(FilmocityError): c.apply_interpretation({**review,**override})
        self.assertEqual(c.calls,[])

    def test_unknown_apply_reply_is_never_retried_or_reread(self):
        variants=[lambda r:r.update(ok=False),lambda r:r.update(changed=None),lambda r:r.update(kind='other'),
            lambda r:r.update(project='foreign'),lambda r:r['context'].update(project='foreign'),
            lambda r:r['context'].update(revision=None),lambda r:r.update(requested_media_id='other'),
            lambda r:r.update(media_id='other'),lambda r:r.update(affected_media_ids=[]),
            lambda r:r['media'].update(id='other'),lambda r:r.update(media=[]),lambda r:r.update(preparation=None)]
        for change in variants:
            c=Client(); review=c.review_interpretation('m',24); c.calls.clear(); c.apply_change=change
            with self.subTest(change=change),self.assertRaisesRegex(FilmocityError,'uncertain'): c.apply_interpretation(review)
            self.assertEqual([p for p,_,_ in c.calls],['/api/media/interpret'])
        c=Client(); review=c.review_interpretation('m',24); c.calls.clear(); c.fail='/api/media/interpret'
        with self.assertRaises(FilmocityError): c.apply_interpretation(review)
        self.assertEqual([p for p,_,_ in c.calls],['/api/media/interpret'])

    def test_noop_and_saved_preparation_warning_are_valid_acknowledgements(self):
        c=Client(); review=c.review_interpretation('m',None)
        c.apply_change=lambda r:r.update(changed=False)
        self.assertFalse(c.apply_interpretation(review)['changed'])
        c.apply_change=lambda r:r['preparation']['warnings'].append('Saved; prepare after Relink')
        self.assertTrue(c.apply_interpretation(review)['preparation']['warnings'])


class SavedInterpretationSDK(unittest.TestCase):
    def setUp(self):
        import test_source_interpretation_workflow as routes
        self.fixture=routes.StoreSourceInterpretation('runTest');self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups);self.addCleanup(self.fixture.tearDown)
        fixture=self.fixture
        class SavedClient(Filmocity):
            def __init__(self):
                super().__init__(actor='human',client='actual-interpretation-sdk');self.calls=[];self.lose_reply=False
            def _call(self,path,body=None,method=None):
                self.calls.append((path,copy.deepcopy(body),method))
                if path=='/api/project/state':return {'project':fixture.project(),'context':fixture.current()}
                route={'/api/media/interpret/review':'media_interpret_review','/api/media/interpret':'media_interpret'}[path]
                try:reply=fixture.route(route,body)
                except routes.store.HTTPError as error:raise FilmocityError(str(error.detail)) from error
                if route=='media_interpret' and self.lose_reply:raise FilmocityError('Saved, but acknowledgement was lost')
                return reply
        self.client=SavedClient()

    def test_actual_review_apply_fullmix_prepare_and_exact_history(self):
        f=self.fixture;before=f.children();review=self.client.review_interpretation('m',24,include_fullmix=True)
        self.assertTrue(review['ok'],review['issues']);self.assertEqual(f.project(),before)
        self.assertFalse(f.manager.values);self.assertFalse(f.env['read_undo_history']('a')['undo'])
        self.client.calls.clear();reply=self.client.apply_interpretation(review)
        self.assertEqual([p for p,_,_ in self.client.calls],['/api/media/interpret'])
        for item in reply['preparation']['tasks']:f.prepare(item['task']['id'])
        after=f.project();self.assertEqual(after['sequences'],before['sequences'])
        self.assertEqual(after['media']['m']['interpret_fps'],'24/1');self.assertEqual(after['media']['m']['duration'],7.5)
        self.assertEqual(after['media']['alias']['sub_in'],1.25);self.assertEqual(after['media']['alias']['duration'],3.75)
        self.assertEqual(after['media']['sub']['sub_in'],1);self.assertEqual(after['media']['sub']['duration'],3)
        self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)
        f.invoke('undo',{'_context':f.current()});self.assertEqual(f.project()['media'],before['media'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['media'],after['media'])

    def test_actual_selected_channel_keeps_tail_native_window_and_decoded_reference(self):
        f=self.fixture;sub=f.create('subclip',**{'in':4,'out':6})['media_ids'][0]
        mid=f.create('breakout',media_id=sub)['media_ids'][0];before=f.project()
        review=self.client.review_interpretation(mid,60);self.assertTrue(review['ok'],review['issues'])
        reply=self.client.apply_interpretation(review)
        for item in reply['preparation']['tasks']:f.prepare(item['task']['id'])
        after=f.project();channel=after['media'][mid]
        self.assertEqual(channel['sub_in'],2);self.assertEqual(channel['duration'],1);self.assertEqual(channel['interpret_fps'],'60/1')
        self.assertEqual(after['media']['m'],before['media']['m']);self.assertEqual(after['sequences'],before['sequences'])
        # Independently authored expected window: original native 4..6 seconds
        # at 60 fps (native 30) occupies logical 2..3, exactly one second.
        oracle=copy.deepcopy(before);oracle['media'][mid].update(sub_in=2,duration=1,interpret_fps='60/1')
        actual=f.audio(after,mid,'sdk-interpreted-tail');expected=f.audio(oracle,mid,'sdk-tail-oracle')
        self.assertEqual(actual,expected);self.assertEqual(actual[0].nframes,48000)
        self.assertTrue(any(actual[1]));f.invoke('undo',{'_context':f.current()})
        self.assertEqual(f.project()['media'],before['media'])
        f.invoke('redo',{'_context':f.current()});self.assertEqual(f.project()['media'],after['media'])

    def test_actual_custom_rational_noop_and_explicit_native_restore(self):
        f=self.fixture;reply=self.client.apply_interpretation(self.client.review_interpretation('m','127/7'))
        self.assertTrue(reply['changed']);self.assertEqual(f.project()['media']['m']['interpret_fps'],'127/7')
        before=f.raw();tasks=len(f.manager.values);history=copy.deepcopy(f.env['read_undo_history']('a'))
        reply=self.client.apply_interpretation(self.client.review_interpretation('m','127/7'))
        self.assertFalse(reply['changed']);self.assertEqual(f.raw(),before);self.assertEqual(len(f.manager.values),tasks)
        self.assertEqual(f.env['read_undo_history']('a'),history)
        self.client.apply_interpretation(self.client.review_interpretation('m',None))
        self.assertNotIn('interpret_fps',f.project()['media']['m']);self.assertAlmostEqual(f.project()['media']['m']['duration'],6)

    def test_actual_foreign_matching_source_and_policy_cannot_apply(self):
        f=self.fixture;review=self.client.review_interpretation('m',24);project=f.project()
        f.env['set_active_project']('b');f.env['save_project'](project);before=(f.raw('a'),f.raw('b'));self.client.calls.clear()
        with self.assertRaises(FilmocityError):self.client.apply_interpretation(review)
        self.assertEqual((f.raw('a'),f.raw('b')),before);self.assertFalse(f.manager.values);self.assertEqual(len(self.client.calls),1)
        f.env['set_active_project']('a');(f.root/'settings.json').write_text('{"agent_mode":"proposals_only"}')
        self.client.actor='agent';review=self.client.review_interpretation('m',24)
        with self.assertRaises(FilmocityError):self.client.apply_interpretation(review)
        self.assertEqual((f.raw('a'),f.raw('b')),before)

    def test_actual_locked_invalid_use_returns_review_without_any_edit(self):
        f=self.fixture;project=f.project();project['sequences'][0]['tracks'][0].update(locked=True)
        project['sequences'][0]['tracks'][0]['clips'][0]['out']=6;f.env['save_project'](project);before=f.raw()
        review=self.client.review_interpretation('m',60);self.assertFalse(review['ok']);self.assertTrue(review['issues'])
        self.client.calls.clear()
        with self.assertRaises(FilmocityError):self.client.apply_interpretation(review)
        self.assertEqual(self.client.calls,[]);self.assertEqual(f.raw(),before);self.assertFalse(f.manager.values)

    def test_actual_fullmix_consent_does_not_silently_expand_selection(self):
        f=self.fixture;before=f.children();review=self.client.review_interpretation('m',24)
        self.assertFalse(review['ok']);self.assertTrue(review['issues']);self.assertEqual(f.project(),before)
        self.client.calls.clear()
        with self.assertRaises(FilmocityError):self.client.apply_interpretation(review)
        self.assertEqual(self.client.calls,[]);self.assertFalse(f.manager.values)

    def test_actual_saved_lost_reply_does_not_replay(self):
        f=self.fixture;review=self.client.review_interpretation('m',24);self.client.calls.clear();self.client.lose_reply=True
        with self.assertRaisesRegex(FilmocityError,'acknowledgement was lost'):self.client.apply_interpretation(review)
        self.assertEqual([p for p,_,_ in self.client.calls],['/api/media/interpret'])
        self.assertEqual(f.project()['media']['m']['interpret_fps'],'24/1');self.assertEqual(len(f.env['read_undo_history']('a')['undo']),1)


if __name__ == '__main__': unittest.main()
