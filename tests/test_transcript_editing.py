"""Word corrections, caption provenance, optimized cuts and transactional history."""
import ast
import copy
import importlib.util
import json
from pathlib import Path
import random
import sys
import unittest
from unittest.mock import patch
import asyncio

import test_project_sync as store
from test_editing_workflow import project
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
sys.path.insert(0, str(ROOT / 'benchmarks/story-editing'))
import editing_workflow as workflow
from measure import load_baseline, semantic, fixture


class Corrections(unittest.TestCase):
    def setUp(self): self.p = project()
    def correction(self, p=None, **body):
        return workflow.build(p or self.p, {'action': 'transcript_edit', 'sequence': 's', 'edits': [{'index': 0, 'expected': 'One.', 'text': 'Émile.'}], **body})

    def test_word_text_changes_without_changing_audio_timings_confidence_or_source(self):
        before = copy.deepcopy(self.p)
        self.p['sequences'][0]['transcript'][0]['p'] = .2; before = copy.deepcopy(self.p)
        result, reply = self.correction(); seq = result['sequences'][0]
        self.assertEqual(seq['transcript'][0], {'w': 'Émile.', 's': .2, 'e': .8, 'p': .2})
        self.assertEqual(seq['tracks'], before['sequences'][0]['tracks']); self.assertEqual(self.p, before)
        self.assertEqual(reply['words_changed'], 1)

    def test_generated_captions_update_but_manually_corrected_captions_are_preserved(self):
        p, _ = workflow.build(self.p, {'action': 'captions', 'sequence': 's', 'max_words': 7})
        seq = p['sequences'][0]; seq['captions'][1]['text'] = 'A human rewrite'; original_ids = [c['id'] for c in seq['captions']]
        result, reply = self.correction(p, edits=[{'index':0,'expected':'One.','text':'Émile.'},{'index':1,'expected':'Remove.','text':'Keep.'}])
        caps = result['sequences'][0]['captions']
        self.assertEqual([c['text'] for c in caps], ['Émile.','A human rewrite','Three.'])
        self.assertEqual(reply['captions_updated'], 1); self.assertEqual(reply['captions_to_review'], [original_ids[1]])
        self.assertEqual([c['id'] for c in caps], original_ids)

    def test_timing_changes_to_captions_require_review_even_when_text_still_matches(self):
        p, _ = workflow.build(self.p, {'action': 'captions', 'sequence': 's'})
        p['sequences'][0]['captions'][0]['start'] += .02
        result, reply = self.correction(p)
        self.assertEqual(result['sequences'][0]['captions'][0]['text'], 'One.')
        self.assertEqual(len(reply['captions_to_review']), 1)

    def test_untracked_legacy_captions_are_never_silently_rewritten(self):
        result, reply = self.correction()
        self.assertEqual(result['sequences'][0]['captions'], self.p['sequences'][0]['captions'])
        self.assertEqual(reply['captions_to_review'], ['cap'])

    def test_caption_moved_away_from_its_linked_word_still_requires_review(self):
        p, _ = workflow.build(self.p, {'action':'captions','sequence':'s'})
        caption = p['sequences'][0]['captions'][0]; caption.update(start=10,end=11)
        result, reply = self.correction(p)
        self.assertEqual(result['sequences'][0]['captions'][0],caption)
        self.assertIn(caption['id'],reply['captions_to_review']); self.assertEqual(reply['captions_updated'],0)

    def test_invalid_indices_expected_text_unicode_controls_and_batches_are_rejected(self):
        invalid = [[], [{'index':True,'expected':'One.','text':'a'}], [{'index':9,'expected':'One.','text':'a'}],
            [{'index':0,'expected':'stale','text':'a'}], [{'index':0,'expected':'One.','text':'two words'}],
            [{'index':0,'expected':'One.','text':'\x00'}], [{'index':0,'expected':'One.','text':'x'*121}],
            [{'index':0,'expected':'One.','text':' '}], [{'index':0,'expected':'One.','text':'a'}]*2,
            [{'index':0,'expected':'One.','text':'a'}]*501]
        before = copy.deepcopy(self.p)
        for edits in invalid:
            with self.subTest(edits=edits[:2]), self.assertRaises(ValueError): self.correction(edits=edits)
            self.assertEqual(self.p, before)

    def test_typographic_punctuation_and_literal_markup_are_data(self):
        for text in ('O’Neill', '東京', '<name>', 'naïve', 'Filmocity!'):
            result, _ = self.correction(edits=[{'index':0,'expected':'One.','text':text}])
            self.assertEqual(result['sequences'][0]['transcript'][0]['w'], text)

    def test_stale_timeline_or_nested_timing_prevents_correction(self):
        seq = self.p['sequences'][0]; seq['transcript_basis'] = workflow.transcript_basis(seq,self.p)
        seq['tracks'][0]['clips'][0]['start'] = 1
        with self.assertRaisesRegex(ValueError,'timeline changed'): self.correction()

    def test_review_acknowledgement_only_clears_named_pending_captions(self):
        p, _ = self.correction()
        with self.assertRaises(ValueError): workflow.build(p, {'action':'caption_review','sequence':'s','caption_ids':['other']})
        result, _ = workflow.build(p, {'action':'caption_review','sequence':'s','caption_ids':['cap']})
        self.assertEqual(result['sequences'][0]['workflow']['caption_review_ids'], [])
        self.assertEqual(result['sequences'][0]['captions'], self.p['sequences'][0]['captions'])

    def test_version_copies_keep_their_own_caption_review_ids(self):
        p, _ = self.correction()
        result, reply = workflow.build(p, {'action':'versions','sequence':'s','formats':['square','portrait']})
        for sid in reply['sequences']:
            seq = workflow.sequence(result,sid)
            self.assertEqual(seq['workflow']['caption_review_ids'], [seq['captions'][0]['id']])
            content = workflow.sequence(result,seq['workflow']['content_sequence'])
            self.assertEqual(content['workflow']['caption_review_ids'], [])
        a,b = [workflow.sequence(result,sid) for sid in reply['sequences']]
        self.assertNotEqual(a['workflow']['caption_review_ids'],b['workflow']['caption_review_ids'])

    def test_regeneration_clears_review_flags_and_builds_links_for_later_corrections(self):
        p, _ = self.correction(); result, _ = workflow.build(p, {'action':'captions','sequence':'s'})
        self.assertEqual(result['sequences'][0]['workflow']['caption_review_ids'], [])
        corrected, reply = workflow.build(result, {'action':'transcript_edit','sequence':'s','edits':[{'index':0,'expected':'Émile.','text':'Émile!'}]})
        self.assertEqual(reply['captions_updated'],1)
        self.assertEqual(corrected['sequences'][0]['captions'][0]['text'],'Émile!')


class OptimizedCuts(unittest.TestCase):
    def test_random_cut_results_match_frozen_builder_with_same_source_unchanged(self):
        old = load_baseline(); rng = random.Random(28371)
        for trial in range(25):
            p, action = fixture(1); seq = p['sequences'][0]
            seq['markers'] = [{'id':str(i),'time':rng.uniform(0,59),'duration':.4,'name':'Cue'} for i in range(30)]
            seq['tracks'][0]['clips'][0].update(group='linked',keyframes={'transform.x':[{'t':0,'v':0},{'t':20,'v':10}]})
            action['words'] = sorted(rng.sample(range(150),rng.randint(1,80))); action['padding'] = rng.choice([0,.08,.2,.5])
            before = copy.deepcopy(p)
            a = old.build(p,action)[0]['sequences'][-1]; b = workflow.build(p,action)[0]['sequences'][-1]
            with self.subTest(trial=trial): self.assertEqual(semantic(a),semantic(b)); self.assertEqual(p,before)

    def test_slices_do_not_copy_the_whole_transcript_or_discarded_captions(self):
        p, action = fixture(20); original = workflow.chunk_sequence; lengths = []
        def counted(*args,**kwargs):
            result = original(*args,**kwargs); lengths.append((len(result.get('transcript',[])),len(result.get('captions',[])))); return result
        with patch.object(workflow,'chunk_sequence',counted): result, reply = workflow.build(p,action)
        self.assertGreater(len(lengths),80); self.assertTrue(all(x==(0,0) for x in lengths))
        self.assertEqual(len(result['sequences'][-1]['transcript']),len(action['words']))

    def test_overlap_with_excluded_speech_is_not_silently_truncated_or_duplicated(self):
        p=project(); p['sequences'][0]['transcript'][0]['e']=1.5
        with self.assertRaisesRegex(ValueError,'overlaps excluded speech'):
            workflow.build(p,{'action':'story','sequence':'s','words':[0,2]})
        result, _ = workflow.build(p,{'action':'story','sequence':'s','words':[0,1]})
        self.assertEqual(len(result['sequences'][-1]['transcript']),2)

    def test_markers_at_cut_boundaries_and_unsorted_markers_keep_original_order_per_cut(self):
        p=project(); p['sequences'][0]['markers']=[{'id':'end','time':.8},{'id':'inside','time':.6},{'id':'start','time':.2},{'id':'last','time':2.4}]
        result,_=workflow.build(p,{'action':'story','sequence':'s','words':[0,2],'padding':0})
        self.assertEqual([m['time'] for m in result['sequences'][-1]['markers']],[.4,0,.6])


class RouteCorrections(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        tree=ast.parse((ROOT/'backend/server.py').read_text()); names={'_workflow_capture','_workflow_commit','workflow_action'}
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        for n in nodes:n.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'backend/server.py','exec'),self.env)
        p=project();p['sequences'][0]['id']='seq1'; self.env['save_project'](p)
    def correction(self,**extra):
        return self.invoke('workflow_action',{'action':'transcript_edit','sequence':'seq1','_context':self.current(),'edits':[{'index':0,'expected':'One.','text':'Émile.'}],**extra})
    def test_word_and_caption_review_change_are_one_undo_redo_step(self):
        original=json.loads(self.raw()); reply=self.correction(); after=json.loads(self.raw())
        self.assertEqual(reply['words_changed'],1);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),1)
        asyncio.run(self.env['project_history_action']('undo',store.Request({'_context':self.current()})))
        undone=json.loads(self.raw());original.pop('updated',None);undone.pop('updated',None);self.assertEqual(original,undone)
        asyncio.run(self.env['project_history_action']('redo',store.Request({'_context':self.current()})))
        redone=json.loads(self.raw());after.pop('updated',None);redone.pop('updated',None);self.assertEqual(after,redone)
    def test_stale_or_foreign_context_rejected_without_mutating_either_project(self):
        expected=self.current();self.env['set_active_project']('b');a,b=self.raw(),self.raw('b')
        with self.assertRaises(store.HTTPError) as e:self.correction(_context=expected)
        self.assertEqual(e.exception.status_code,409);self.assertEqual(self.raw(),a);self.assertEqual(self.raw('b'),b)
    def test_invalid_batch_is_all_or_nothing(self):
        before=self.raw()
        with self.assertRaises(store.HTTPError):self.correction(edits=[{'index':0,'expected':'One.','text':'Émile.'},{'index':1,'expected':'bad','text':'Other.'}])
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a')['undo'],[])
    def test_failed_history_commit_does_not_publish_a_correction(self):
        before=self.raw()
        self.env['commit_edit']=lambda *a,**k:(_ for _ in ()).throw(OSError('disk fixture failure'))
        with self.assertRaises(OSError):self.correction()
        self.assertEqual(self.raw(),before)


if __name__=='__main__':unittest.main()
