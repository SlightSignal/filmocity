"""Bounded remix plans, actual source decoding and canonical guarded application."""
import array
import ast
import copy
import math
from pathlib import Path
import sys
import tempfile
import unittest
import wave

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import audio_remix as remix
import media_analysis as analysis
import analysis_edits as edits
from overlap_normalization import _clock, _source_offset
from render import kf_eval
from audio_contract import fade_window
from render_context import RenderContext
import render
from test_analysis_edits import apply, source
import test_media_analysis as analysis_fixtures
import test_project_sync as store

CONTEXT={'workspace':'work','project':'project','revision':'one'}


def synthetic(clip=None,target=2):
    c={'id':'c','media_id':'m','start':2.,'in_':1.,'out':5.,'speed':1.,**(clip or {})}
    project={'media':{'m':{'has_audio':True,'duration':20}},'sequences':[{'id':'s','fps':30,'tracks':[{'id':'a','kind':'audio','clips':[c]}]}]}
    payload={'mode':'remix','media_id':'m','sequence':'s','clip_id':'c','range':{'start':c['in_'],'end':c['out']},'settings':{'target':target,'bars_per_phrase':4},'factor':1,'clip':copy.deepcopy(c)}
    result={'version':1,'kind':'remix','clock':'media',**{k:copy.deepcopy(payload[k]) for k in ('media_id','sequence','clip_id','range')},**remix.measured_plan(payload,[(i*.02,-10.) for i in range(200)])}
    return project,c,payload,result


class RemixPlans(unittest.TestCase):
    def test_short_source_has_valid_exact_ranges_instead_of_out_of_bounds_phrases(self):
        for duration,target in ((4,2),(4,7.1234),(4,32),(1,.00003),(4,4),(4,3.99999)):
            with self.subTest(duration=duration,target=target):
                ranges,warnings=remix.design(duration,target,beat=.5,bars=4)
                self.assertLessEqual(len(ranges),128)
                self.assertTrue(all(0<=a<b<=duration for a,b in ranges))
                self.assertAlmostEqual(sum(b-a for a,b in ranges),round(target*48000)/48000,delta=1/48000)
                if target!=duration:self.assertTrue(warnings)
        self.assertEqual(remix.design(4,2)[0],[(0,1.),(3.,4)])
        with self.assertRaisesRegex(ValueError,'eight times'):remix.design(4,32.01)

    def test_reverse_ramp_source_automation_fades_and_markers_follow_each_kept_piece(self):
        for reverse in (False,True):
            p,c,payload,result=synthetic({'reverse':reverse,'time_remap':[{'t':0,'v':.5},{'t':2,'v':2},{'t':4,'v':1}],
                'keyframes':{'audio.gain_db':[{'t':0,'v':-4,'e':'ease'},{'t':4,'v':-14}]},'audio':{'fade_in':1,'fade_out':1},
                'markers':[{'t':.2,'name':'opening'}]},target=6)
            before=copy.deepcopy(p);plan=edits.plan(p,payload,result,'task');pieces=apply(p,plan)['sequences'][0]['tracks'][0]['clips']
            self.assertEqual(p,before);self.assertLessEqual(abs(sum(_clock(x) for x in pieces)-6),1/48000)
            for piece,sg in zip(pieces,result['segments']):
                for t in (0,_clock(piece)/3,_clock(piece)*.9):
                    self.assertAlmostEqual(source(piece,t),source(c,sg['begin']+t),places=8)
                    self.assertAlmostEqual(kf_eval(piece['keyframes']['audio.gain_db'],t),kf_eval(c['keyframes']['audio.gain_db'],sg['begin']+t),places=7)
                self.assertAlmostEqual(fade_window(piece,_clock(piece))['offset'],sg['begin'])
            self.assertIsNone(result['bpm']);self.assertEqual(len({c['id'] for c in pieces}),len(pieces))

    def test_collision_locked_hold_and_later_transition_fail_without_mutation(self):
        for mode in ('collision','locked','hold','transition'):
            p,c,payload,result=synthetic(target=6);tr=p['sequences'][0]['tracks'][0]
            if mode=='collision':tr['clips'].append({'id':'neighbor','media_id':'m','start':7,'in_':8,'out':10})
            if mode=='locked':tr['locked']=True
            if mode=='hold':c['hold']=True
            if mode=='transition':c['transition_out']={'type':'dissolve','duration':3}
            before=copy.deepcopy(p)
            with self.subTest(mode=mode),self.assertRaises(ValueError):edits.plan(p,payload,result,'task')
            self.assertEqual(p,before)

    def test_malformed_segments_target_and_unbounded_repeat_reject(self):
        for patch in ({'segments':[]},{'segments':[{'in':0,'out':99,'begin':0,'end':99}]},{'achieved':99},{'requested':99}):
            p,c,payload,result=synthetic();result.update(patch)
            with self.subTest(patch=patch),self.assertRaises(ValueError):edits.plan(p,payload,result,'task')
        for target in (True,float('nan'),float('inf'),-1,0,99999):
            with self.subTest(target=target),self.assertRaises(ValueError):remix.settings({'target':target})
        for bars in (True,0,17,1.5):
            with self.subTest(bars=bars),self.assertRaises(ValueError):remix.settings({'target':2,'bars_per_phrase':bars})

    def test_fingerprint_is_deterministic_and_equal_duration_is_a_noop(self):
        p,c,payload,result=synthetic(target=4);a=edits.plan(p,payload,result,'task')
        self.assertEqual(a['ops'],[]);self.assertEqual(a,edits.plan(p,payload,result,'task'))
        self.assertNotEqual(a['fingerprint'],edits.plan(p,payload,result,'other')['fingerprint'])


class RemixDecoding(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='Filmocity remix É ');self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)
    def wav(self,name,seconds,amplitude):
        path=self.root/name;rate=8000;pcm=array.array('h',(int(amplitude(i/rate)) for i in range(round(seconds*rate))))
        if sys.byteorder!='little':pcm.byteswap()
        with wave.open(str(path),'wb') as stream:stream.setparams((1,2,rate,0,'NONE',''));stream.writeframes(pcm.tobytes())
        return {'id':'m','path':str(path),'duration':seconds,'has_audio':True,'has_video':False,'channels':1,'sample_rate':rate}
    def test_silence_and_constant_tone_have_no_invented_bpm(self):
        for name,amplitude in (('silent',lambda t:0),('tone',lambda t:10000)):
            m=self.wav(name+'.wav',4,amplitude);p={'media':{'m':m},'sequences':[]}
            payload=analysis.capture(p,{'media_id':'m','target':2},'remix',CONTEXT)
            result=analysis.analyze(payload,scratch_parent=str(self.root))
            self.assertIsNone(result['bpm']);self.assertEqual(result['silent'],name=='silent');self.assertEqual(result['achieved'],2)
            self.assertEqual([(s['in'],s['out']) for s in result['segments']],[(0,1),(3,4)])
    def test_interpreted_envelope_is_resampled_without_fabricated_pulses(self):
        m=self.wav('constant.wav',4,lambda t:10000);m.update(frame_rate='20/1',fps=20,interpret_fps=10,duration=8)
        p={'media':{'m':m},'sequences':[]};payload=analysis.capture(p,{'media_id':'m','target':4},'remix',CONTEXT)
        result=analysis.analyze(payload,scratch_parent=str(self.root));self.assertIsNone(result['bpm']);self.assertFalse(result['silent'])
    def test_real_pulses_have_qualified_tempo_and_bounded_exact_repeat(self):
        m=self.wav('pulses.wav',8,lambda t:10000 if t%.5<.08 else 0);p={'media':{'m':m},'sequences':[]}
        payload=analysis.capture(p,{'media_id':'m','target':12},'remix',CONTEXT);result=analysis.analyze(payload,scratch_parent=str(self.root))
        self.assertAlmostEqual(result['bpm'],120,delta=3);self.assertGreater(result['tempo_confidence'],.3)
        self.assertEqual(result['achieved'],12);self.assertTrue(any('estimate' in x for x in result['warnings']))
    def test_real_pcm_shortening_and_repeats_follow_reviewed_source_ranges(self):
        m=self.wav('levels.wav',4,lambda t:1000*(int(t)+1));c={'id':'c','media_id':'m','start':0,'in_':0,'out':4}
        p={'media':{'m':m},'sequences':[{'id':'s','width':64,'height':48,'fps':30,'tracks':[{'id':'a','kind':'audio','index':1,'clips':[c]}]}]}
        for target,points in ((2,[(.5,1000),(1.5,4000)]),(6,[(.5,1000),(2.5,3000),(3.5,2000),(4.5,3000),(5.5,4000)])):
            payload=analysis.capture(p,{'media_id':'m','sequence':'s','clip_id':'c','target':target},'remix',CONTEXT)
            result=analysis.analyze(payload,scratch_parent=str(self.root));edited=apply(p,edits.plan(p,payload,result,'pcm-task'))
            out=self.root/f'pcm-{target}.wav'
            with RenderContext(scratch_parent=str(self.root)) as context:render.render(edited,'s',str(out),{'format':'audio','acodec':'wav'},context=context)
            with wave.open(str(out),'rb') as stream:
                self.assertEqual(stream.getnframes(),target*48000);samples=array.array('h',stream.readframes(target*48000))
            for at,expected in points:self.assertAlmostEqual(samples[round(at*48000)*2],expected,delta=3)

    def test_nonzero_interpreted_subclip_analysis_and_pcm_export_use_the_same_source_window(self):
        m=self.wav('offset.wav',3,lambda t:10000 if t>=1 else 0);m.update(frame_rate='20/1',fps=20,interpret_fps=10,duration=6)
        sub={**m,'id':'sub','subclip_of':'m','sub_in':2,'duration':2};c={'id':'c','media_id':'sub','start':0,'in_':0,'out':2}
        p={'media':{'m':m,'sub':sub},'sequences':[{'id':'s','width':64,'height':48,'fps':30,'tracks':[{'id':'a','kind':'audio','index':1,'clips':[c]}]}]}
        payload=analysis.capture(p,{'media_id':'sub','sequence':'s','clip_id':'c','in':0,'out':2,'target':1},'remix',CONTEXT)
        result=analysis.analyze(payload,scratch_parent=str(self.root));self.assertFalse(result['silent'])
        edited=apply(p,edits.plan(p,payload,result,'sub-task'));out=self.root/'out.wav'
        with RenderContext(scratch_parent=str(self.root)) as context:render.render(edited,'s',str(out),{'format':'audio','acodec':'wav'},context=context)
        with wave.open(str(out),'rb') as stream:
            self.assertEqual(stream.getnframes(),48000);samples=array.array('h',stream.readframes(48000))
        self.assertGreater(abs(samples[48000]),8000)
    def test_recipe_helper_uses_captured_project_never_active_lookup(self):
        import asyncio
        m=self.wav('recipe.wav',4,lambda t:1000);p={'media':{'m':m},'sequences':[]}
        tree=ast.parse((ROOT/'backend/server.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='audio_remix_segments')
        scope={'copy':copy,'load_project':lambda:(_ for _ in ()).throw(AssertionError('active lookup'))};exec(compile(ast.Module(body=[node],type_ignores=[]),'recipe-remix','exec'),scope)
        ranges=asyncio.run(scope['audio_remix_segments'](m,2,project=p));self.assertEqual(ranges,[{'in':0,'out':1},{'in':3,'out':4}])
        with self.assertRaises(ValueError):asyncio.run(scope['audio_remix_segments'](m,2))


class RemixRoutes(unittest.TestCase):
    def setUp(self):
        self.fixture=analysis_fixtures.AnalysisRoutes('runTest');self.fixture.setUp();self.addCleanup(self.fixture.tearDown);self.addCleanup(self.fixture.doCleanups)
        self.f=self.fixture;tree=ast.parse((ROOT/'backend/server.py').read_text());node=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='audio_remix');node.decorator_list=[]
        exec(compile(ast.Module(body=[node],type_ignores=[]),'actual-remix-route','exec'),self.f.env)
    def ready(self):
        response=self.f.invoke('audio_remix',{'_context':self.f.current(),'media_id':'m','sequence':'s','clip_id':'c','target':1})
        identity=response['task']['id'];value=self.f.tasks.values[identity];value['result']=analysis.analyze(value['payload'],scratch_parent=str(self.f.root));value['record']['status']='ready';return identity
    def test_review_apply_has_one_history_record_and_undo_restores_original(self):
        before=self.f.env['load_project']();identity=self.ready();self.assertEqual(self.f.env['load_project'](),before)
        review=self.f.review(identity);self.assertEqual(review['plan']['summary']['achieved'],1)
        count=len(self.f.env['read_undo_history']('a')['undo']);self.f.apply(identity,{'_context':review['context'],'fingerprint':review['plan']['fingerprint']})
        self.assertEqual(len(self.f.env['read_undo_history']('a')['undo']),count+1)
        self.f.invoke('undo',{'_context':self.f.current()});self.assertEqual(self.f.env['load_project']()['sequences'],before['sequences'])
    def test_stale_review_lock_context_and_source_changes_refuse_without_edits(self):
        identity=self.ready();review=self.f.review(identity);self.f.edit('Unrelated',self.f.current());before=self.f.raw()
        with self.assertRaises(store.HTTPError):self.f.apply(identity,{'_context':review['context'],'fingerprint':review['plan']['fingerprint']})
        self.assertEqual(self.f.raw(),before)
        project=self.f.env['load_project']();project['sequences'][0]['tracks'][0]['locked']=True;self.f.env['save_project'](project)
        with self.assertRaisesRegex(store.HTTPError,'unlocked'):self.f.review(identity)
    def test_submission_requires_context_and_cancelled_result_cannot_apply(self):
        with self.assertRaises(store.HTTPError):self.f.invoke('audio_remix',{'media_id':'m','target':1})
        identity=self.ready();review=self.f.review(identity);self.f.tasks.cancel(identity);before=self.f.raw()
        with self.assertRaises(store.HTTPError):self.f.apply(identity,{'_context':review['context'],'fingerprint':review['plan']['fingerprint']})
        self.assertEqual(self.f.raw(),before)


if __name__=='__main__':unittest.main()
