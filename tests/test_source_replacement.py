"""Canonical replacement plans, real UI commands, saved history and decoded audio."""
import array
import ast
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import wave
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'backend'))
import source_replacement as replacement
from overlap_normalization import _clock, _source_offset, slice_clip
from audio_contract import fade_window
import render
from render_context import RenderContext
import test_project_sync as store


def sample(**extra):
    clip = {'id': 'a', 'media_id': 'm', 'start': 0, 'in_': 5, 'out': 8, 'speed': 1,
            'name': 'Editor name', 'label': '#cc8833', 'group': 'edit group', 'note': 'Keep this moment',
            'audio': {'gain_db': -3, 'fade_in': 1, 'fade_out': .5, 'linked': False},
            'keyframes': {'audio.gain_db': [{'t': 0, 'v': -3}, {'t': 3, 'v': -6}], 'audio.duck_db': [{'t': 0, 'v': 0}, {'t': 3, 'v': -8}]},
            'markers': [{'t': -1, 'name': 'hidden'}, {'t': 1, 'name': 'editor annotation'}, {'t': 8, 'name': 'hidden tail'}],
            'source_edit_window': {'version': 1}, 'rendered_from': {'media_id': 'old'}, 'render_replace_task': 'old-task', **extra}
    media = {k: {'id': k, 'path': '/fixtures/'+k+'.mov', 'duration': 100, 'has_video': True, 'has_audio': True, 'fps': 30} for k in ('m', 'n')}
    sequence = {'id': 's1', 'width': 64, 'height': 48, 'fps': 30, 'tracks': [{'id': 'v1', 'kind': 'video', 'index': 0, 'clips': [clip]}]}
    return {'media': media, 'sequences': [sequence]}, clip


def command(project, **extra):
    return {'sequence': 's1', 'clip_id': 'a', 'media_id': 'n', 'in': 2, **extra}


def actual_ui(project, **extra):
    return json.loads(subprocess.check_output(['node', str(ROOT/'tests/helpers/source-replacement-fixture.cjs')], text=True,
        input=json.dumps({'sequence': project['sequences'][0], 'media': project['media'], 'in': 2, **extra}), cwd=ROOT))


class SourceReplacementPlans(unittest.TestCase):
    def test_frontend_and_backend_complete_clip_parity_for_constant_reverse_ramp_and_hold(self):
        for extra in ({'time_remap': None}, {'speed': 2}, {'speed': 2, 'reverse': True}, {'time_remap': [{'t': 0, 'v': .5}, {'t': 2, 'v': 2}, {'t': 4, 'v': 1}], 'reverse': True}, {'hold': True, 'speed': 3}):
            with self.subTest(extra=extra):
                p, c = sample(**extra); before = copy.deepcopy(p); ui = actual_ui(p)
                self.assertIsNotNone(ui['body'], ui['messages']); plan = replacement.plan(p, ui['body']); result = plan['clip']
                self.assertEqual(result, ui['planned']['clip']); self.assertEqual(p, before)
                self.assertAlmostEqual(_clock(c), _clock(result), places=12)
                for key in ('name', 'note', 'group', 'label', 'audio', 'keyframes'): self.assertEqual(result[key], c[key])
                self.assertEqual(result['markers'], [c['markers'][1]])
                for key in ('rendered_from', 'render_replace_task', 'source_edit_window'): self.assertNotIn(key, result)
                for t in (0, _clock(c)/3, _clock(c)):
                    self.assertAlmostEqual(_source_offset(c, t), _source_offset(result, t))

    def test_inherited_fade_clock_and_trimmed_ramp_curve_survive_but_foreign_history_does_not(self):
        p, original = sample(in_=2, out=12, time_remap=[{'t': 0, 'v': .5}, {'t': 3, 'v': 2}, {'t': 7, 'v': 1}])
        clipped = slice_clip(original, 1, 4); p['sequences'][0]['tracks'][0]['clips'] = [clipped]
        plan = replacement.plan(p, command(p)); result = plan['clip']
        self.assertEqual(result['time_remap'], clipped['time_remap']); self.assertEqual(fade_window(result, _clock(result)), fade_window(clipped, _clock(clipped)))
        self.assertEqual(result['keyframes'], clipped['keyframes']); self.assertNotIn('source_edit_window', result)

    def test_held_frame_near_source_end_and_virtual_still_preserve_duration(self):
        p, c = sample(hold=True); p['media']['n']['duration'] = 1
        held = replacement.plan(p, command(p, **{'in': .9, 'out': .95}))['clip']; self.assertEqual(held['out'], 3.9); self.assertEqual(_clock(held), 3)
        with self.assertRaisesRegex(ValueError, 'held frame'): replacement.plan(p, command(p, **{'in': 1}))
        p['media']['n'].update(is_image=True, has_audio=False); still = replacement.plan(p, command(p, **{'in': 100}))['clip']; self.assertEqual(still['out'], 103)

    def test_source_extent_and_marked_out_cannot_shorten_a_retained_speed_edit(self):
        p, c = sample(speed=2); p['media']['n']['duration'] = 4
        for body in (command(p, **{'in': 2}), command(p, **{'in': 0, 'out': 2}), command(p, **{'in': -1}), command(p, **{'in': True})):
            before = copy.deepcopy(p)
            with self.assertRaises(ValueError): replacement.plan(p, body)
            self.assertEqual(p, before)

    def test_noop_preserves_original_history_and_stabilization_only_blocks_foreign_source(self):
        p, c = sample(fx_stack=[{'type': 'stabilize'}])
        no = replacement.plan(p, command(p, media_id='m', **{'in': 5})); self.assertFalse(no['changed']); self.assertEqual(no['clip'], c)
        self.assertTrue(replacement.plan(p, command(p, media_id='m'))['changed'])
        with self.assertRaisesRegex(ValueError, 'stabilization'): replacement.plan(p, command(p))

    def test_invalid_ownership_track_stream_links_ramps_and_metadata_are_atomic(self):
        mutations = [lambda p,c: p['sequences'][0]['tracks'][0].update(locked=True), lambda p,c: p['media']['n'].update(has_video=False),
            lambda p,c: c.update(audio_detached_id='detached'), lambda p,c: p['sequences'][0]['tracks'][0]['clips'].append({'id': 'child', 'unlinked_from': 'a'}),
            lambda p,c: c.update(time_remap=[{'t': 0, 'v': -1}]), lambda p,c: c.update(markers=[{'t': float('nan')}]),
            lambda p,c: p['sequences'][0]['tracks'][0]['clips'].append(copy.deepcopy(c)), lambda p,c: p['media']['n'].update(synthetic={'tone_hz': 500}),
            lambda p,c: p['media'].pop('m'), lambda p,c: c.update(fx_stack='broken')]
        for mutation in mutations:
            p,c=sample();mutation(p,c)
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):replacement.plan(p,command(p))

    def test_subclip_parent_extent_interpretation_cycles_and_missing_parent_are_rejected(self):
        for mode in ('missing', 'cycle', 'extent', 'factor'):
            p,c=sample();p['media']['n'].update(subclip_of='parent',sub_in=2,duration=20)
            if mode!='missing':p['media']['parent']={**p['media']['m'],'id':'parent'}
            if mode=='cycle':p['media']['parent']['subclip_of']='n'
            if mode=='extent':p['media']['n']['sub_in']=90
            if mode=='factor':p['media']['n']['interpret_fps']=15
            with self.subTest(mode=mode),self.assertRaises(ValueError):replacement.plan(p,command(p))

    def test_old_generated_media_can_be_replaced_with_an_ordinary_source(self):
        p,c=sample();p['media']['m'].update(synthetic={'tone_hz':1000})
        self.assertTrue(replacement.plan(p,command(p))['changed'])

    def test_disabled_stabilization_settings_never_reuse_the_old_media_analysis(self):
        p,c=sample(fx_stack=[{'type':'stabilize','enabled':False,'params':{'smoothing':10}}]);p['media']['m']['stab_trf']='/old-source.trf'
        result=replacement.plan(p,command(p))['clip'];self.assertEqual(result['fx_stack'],c['fx_stack'])
        result['fx_stack'][0]['enabled']=True
        # Renderer resolves the transform from the new media identity, not from
        # the retained effect settings. No old transform path enters the graph.
        self.assertNotIn('stab_trf',p['media'][result['media_id']])
        p['sequences'][0]['tracks'][0]['clips']=[result]
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'source.mov';path.write_bytes(b'graph-only fixture')
            p['media']['n']['path']=str(path)
            old_transform=Path(directory)/'old-source.trf';old_transform.write_text('old analysis')
            p['media']['m']['stab_trf']=str(old_transform)
            with RenderContext(scratch_parent=directory) as context:
                _,graph=render.build_command(p,'s1',str(Path(directory)/'replacement.mp4'),{},context=context)
            self.assertNotIn(str(old_transform),graph);self.assertNotIn('vidstabtransform',graph)

    def test_existing_overlap_is_rejected_instead_of_normalizing_another_edit(self):
        p,c=sample();p['sequences'][0]['tracks'][0]['clips'].append({**copy.deepcopy(c),'id':'neighbor','start':2})
        with self.assertRaisesRegex(ValueError,'overlap'):replacement.plan(p,command(p))


class SourceReplacementStore(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        tree=ast.parse((ROOT/'backend/server.py').read_text());names={'_workflow_capture','_workflow_commit','clip_replace_source'}
        nodes=[n for n in tree.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name in names]
        self.assertEqual({n.name for n in nodes},names)
        for node in nodes:node.decorator_list=[]
        exec(compile(ast.Module(body=nodes,type_ignores=[]),'actual-source-replacement-route','exec'),self.env)
        p,_=sample();self.install(p)
    def install(self, p):
        project=self.env['load_project']();project.update(copy.deepcopy(p));self.env['save_project'](project);return self.env['load_project']()
    def replace(self, **extra):return self.invoke('clip_replace_source',{'_context':self.current(),**command(self.env['load_project']()),**extra})


class SourceReplacementRoutes(SourceReplacementStore):
    def test_actual_frontend_request_saves_complete_clip_as_one_undo_and_redo(self):
        p=self.env['load_project']();ui=actual_ui(p);self.assertIsNotNone(ui['body'],ui['messages']);history=len(self.env['read_undo_history']('a')['undo'])
        result=self.invoke('clip_replace_source',{**ui['body'],'_context':self.current()});self.assertTrue(result['ok'])
        after=self.env['load_project']();self.assertEqual(after['sequences'][0]['tracks'][0]['clips'][0],ui['planned']['clip']);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),history+1)
        self.invoke('undo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],p['sequences'])
        self.invoke('redo',{'_context':self.current()});self.assertEqual(self.env['load_project']()['sequences'],after['sequences'])
    def test_explicit_null_ramp_saves_as_constant_speed_without_deleting_the_null_field(self):
        p=self.env['load_project']();p['sequences'][0]['tracks'][0]['clips'][0]['time_remap']=None;self.install(p)
        ui=actual_ui(p);self.assertIsNotNone(ui['body'],ui['messages'])
        self.invoke('clip_replace_source',{**ui['body'],'_context':self.current()})
        after=self.env['load_project']()['sequences'][0]['tracks'][0]['clips'][0]
        self.assertIn('time_remap',after);self.assertIsNone(after['time_remap']);self.assertEqual(after,ui['planned']['clip'])

    def test_stale_missing_context_and_foreign_project_cannot_replace_reused_id(self):
        context=self.current();self.edit('another edit',context)
        for body in (command({},_context=context),command({}),command({},_context={**self.current(),'project':'b'})):
            before=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
            with self.assertRaises(store.HTTPError):self.invoke('clip_replace_source',body)
            self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_failed_commit_never_writes_partial_project_or_history(self):
        before=self.raw();history=copy.deepcopy(self.env['read_undo_history']('a'))
        with patch.dict(self.env,commit_pair=lambda *a,**k:(_ for _ in ()).throw(OSError('isolated failure'))),self.assertRaises(OSError):self.replace()
        self.assertEqual(self.raw(),before);self.assertEqual(self.env['read_undo_history']('a'),history)
    def test_noop_does_not_add_history_and_guards_are_checked_by_backend(self):
        count=len(self.env['read_undo_history']('a')['undo']);self.assertFalse(self.replace(media_id='m',**{'in':5})['changed']);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),count)
        p=self.env['load_project']();p['sequences'][0]['tracks'][0]['locked']=True;self.install(p);before=self.raw()
        with self.assertRaisesRegex(store.HTTPError,'Unlock'):self.replace()
        self.assertEqual(self.raw(),before)
    def test_nonhuman_preferences_are_enforced_and_unreadable_configuration_fails_closed(self):
        settings=self.root/'settings.json'
        for value in ('{"agent_mode":"proposals_only"}','broken json','[]'):
            settings.write_text(value);before=self.raw()
            with self.assertRaises(store.HTTPError) as caught:self.replace(actor='agent')
            self.assertEqual(caught.exception.status_code,403);self.assertEqual(self.raw(),before)
        settings.write_text('{"agent_mode":"direct"}');self.assertTrue(self.replace(actor='agent')['ok'])
    def test_successful_replacement_followed_by_same_request_has_no_duplicate_history(self):
        body={'_context':self.current(),**command({})};self.invoke('clip_replace_source',body);count=len(self.env['read_undo_history']('a')['undo'])
        with self.assertRaises(store.HTTPError):self.invoke('clip_replace_source',body)
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']),count)
        result=self.replace();self.assertFalse(result['changed']);self.assertEqual(len(self.env['read_undo_history']('a')['undo']),count)


class SourceReplacementDecoded(SourceReplacementStore):
    def test_replaced_interpreted_subclip_reverse_and_gain_export_the_expected_native_samples(self):
        path=self.root/'replacement.wav';samples=array.array('h',(1000*(i//48000+1) for i in range(5*48000)))
        if sys.byteorder!='little':samples.byteswap()
        with wave.open(str(path),'wb') as out:out.setparams((1,2,48000,0,'NONE',''));out.writeframes(samples.tobytes())
        for reverse in (False,True):
            with self.subTest(reverse=reverse):
                p,c=sample(in_=0,out=4,speed=2,reverse=reverse,audio={'gain_db':-6.020599913,'maintain_pitch':False,'fade_in':.5,'fade_out':.5,'constant_power':False},keyframes={})
                p['sequences'][0]['tracks'][0]['kind']='audio'
                parent={'id':'parent','path':str(path),'duration':10,'has_audio':True,'has_video':False,'channels':1,'sample_rate':48000,'fps':20,'frame_rate':'20/1','interpret_fps':10}
                p['media'].update(parent=parent,n={**parent,'id':'n','subclip_of':'parent','sub_in':2,'duration':6});self.install(p)
                ui=actual_ui(p,**{'in':1});self.assertIsNotNone(ui['body'],ui['messages']);self.invoke('clip_replace_source',{**ui['body'],'_context':self.current()});edited=self.env['load_project']()
                out=self.root/f'replaced-{reverse}.wav'
                with RenderContext(scratch_parent=str(self.root)) as context:render.render(edited,'s1',str(out),{'format':'audio','acodec':'wav'},context=context)
                with wave.open(str(out),'rb') as stream:self.assertEqual(stream.getnframes(),2*48000);actual=array.array('h',stream.readframes(stream.getnframes()))
                if sys.byteorder!='little':actual.byteswap()
                expected=(2000,3000,3000,4000) if not reverse else (4000,3000,3000,2000)
                for at,level in zip((.25,.75,1.25,1.75),expected):self.assertAlmostEqual(actual[round(at*48000)*2],level*.5*(.5 if at in (.25,1.75) else 1),delta=3)


if __name__=='__main__':unittest.main()
