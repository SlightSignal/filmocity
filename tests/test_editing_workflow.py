"""Workflow semantics, real file transactions, controlled speech and real renders.

Framework/speech adapters do not constitute live HTTP, model or Windows tests.
"""
import ast
import asyncio
import array
import copy
import io
import json
import math
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch
import wave

import test_project_sync as store
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import editing_workflow as workflow
import render as engine
from render_context import RenderContext
from PIL import Image


def project():
    return {'version': 3, 'id': 'p', 'name': 'Interview', 'media': {
        'a': {'id': 'a', 'name': 'Interview', 'has_audio': True, 'has_video': True, 'width': 64, 'height': 32, 'duration': 4},
        'b': {'id': 'b', 'name': 'Voice', 'has_audio': True, 'has_video': False, 'duration': 2}},
        'sequences': [{'id': 's', 'name': 'Story', 'width': 64, 'height': 32, 'fps': 25, 'markers': [{'id': 'mark', 'time': 3, 'name': 'Keep'}],
        'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [{'id': 'c', 'media_id': 'a', 'start': 0, 'in_': 0, 'out': 4, 'speed': 1, 'keyframes': {'transform.x': [{'t': 0, 'v': 0}, {'t': 4, 'v': 40}]}}]}],
        'transcript': [{'w': 'One.', 's': .2, 'e': .8}, {'w': 'Remove.', 's': 1.2, 'e': 1.8}, {'w': 'Three.', 's': 2.4, 'e': 3.2}],
        'captions': [{'id': 'cap', 'start': 0, 'end': 4, 'text': 'One Remove Three'}]}]}


class Builders(unittest.TestCase):
    def setUp(self): self.p = project(); self.before = copy.deepcopy(self.p)
    def tearDown(self): self.assertEqual(self.p, self.before, 'Builder mutated source project')
    def action(self, action, **body): return workflow.build(self.p, {'action': action, 'sequence': 's', **body})

    def test_assemble_order_mixed_streams_and_no_source_changes(self):
        result, reply = self.action('assemble', media_ids=['b', 'a'], format='portrait', name='My cut', fps=29.97)
        seq = workflow.sequence(result, reply['sequence'])
        self.assertEqual((seq['width'], seq['height'], seq['fps']), (1080, 1920, 30000/1001))
        self.assertEqual(seq['tracks'][1]['clips'][0]['media_id'], 'b')
        self.assertEqual(seq['tracks'][0]['clips'][0]['start'], 2)
        self.assertEqual(result['sequences'][0], self.p['sequences'][0])

    def test_invalid_actions_and_empty_or_duplicate_media_are_atomic(self):
        for body in ({'action': 'unknown'}, {'action': 'assemble', 'media_ids': []}, {'action': 'assemble', 'media_ids': ['a', 'a']},
                     {'action': 'assemble', 'media_ids': ['gone']}, {'action': 'assemble', 'media_ids': ['a'], 'fps': float('nan')}):
            with self.subTest(body=body), self.assertRaises(ValueError): workflow.build(self.p, body)

    def test_story_cut_retimes_source_words_keyframes_and_markers(self):
        result, reply = self.action('story', words=[0, 2], padding=0)
        seq = workflow.sequence(result, reply['sequence']); clips = seq['tracks'][0]['clips']
        self.assertEqual([c['in_'] for c in clips], [.2, 2.4])
        self.assertEqual([round(c['start'], 4) for c in clips], [0, .6])
        self.assertEqual([round(c['out'], 6) for c in clips], [.8, 3.2])
        self.assertEqual([w['w'] for w in seq['transcript']], ['One.', 'Three.'])
        self.assertEqual(seq['transcript'][1]['s'], .6)
        self.assertEqual(seq['captions'], [])
        self.assertAlmostEqual(seq['markers'][0]['time'], 1.2)
        self.assertEqual(seq['tracks'][0]['clips'][1]['keyframes']['transform.x'][0]['t'], -2.4)
        self.assertEqual(len({c['id'] for c in clips}), 2)
        self.assertEqual(workflow.words_of(seq), seq['transcript'])

    def test_padding_does_not_reintroduce_an_excluded_adjacent_word(self):
        self.p['sequences'][0]['transcript'][1]['s'] = .81; self.before = copy.deepcopy(self.p)
        result, reply = self.action('story', words=[0], padding=.5)
        self.assertLessEqual(workflow.sequence(result, reply['sequence'])['tracks'][0]['clips'][0]['out'], .81)

    def test_empty_bad_indices_and_complex_retiming_rejected(self):
        for selected in ([], [-1], [100], [True], ['0']):
            with self.subTest(selected=selected), self.assertRaises(ValueError): self.action('story', words=selected)
        for field in ('reverse', 'hold', 'time_remap'):
            p = copy.deepcopy(self.p); p['sequences'][0]['tracks'][0]['clips'][0][field] = True
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'constant-speed'):
                workflow.build(p, {'action': 'story', 'sequence': 's', 'words': [0]})

    def test_stale_transcript_rejected_but_audio_cleanup_keeps_timing_valid(self):
        p = copy.deepcopy(self.p); seq = p['sequences'][0]; seq['transcript_basis'] = workflow.transcript_basis(seq)
        clean, _ = workflow.build(p, {'action': 'cleanup', 'sequence': 's', 'clip_ids': ['c']})
        workflow.words_of(clean['sequences'][0])
        seq['tracks'][0]['clips'][0]['start'] = 1
        with self.assertRaisesRegex(ValueError, 'timeline changed'):
            workflow.build(p, {'action': 'story', 'sequence': 's', 'words': [0]})

    def test_cleanup_preserves_other_effects_and_replaces_its_own_recipe(self):
        p = copy.deepcopy(self.p); original = {'type': 'amplify', 'params': {'gain_db': 2}}
        p['sequences'][0]['tracks'][0]['clips'][0]['afx_stack'] = [original]
        p, _ = workflow.build(p, {'action': 'cleanup', 'sequence': 's', 'clip_ids': ['c'], 'denoise': 9})
        p, _ = workflow.build(p, {'action': 'cleanup', 'sequence': 's', 'clip_ids': ['c'], 'denoise': 0, 'highpass': 100})
        fx = p['sequences'][0]['tracks'][0]['clips'][0]['afx_stack']
        self.assertEqual([f['type'] for f in fx], ['amplify', 'highpass', 'compressor'])
        self.assertEqual(fx[0], original)
        self.assertEqual(fx[1]['params']['frequency'], 100)

    def test_cleanup_rejects_locked_missing_and_nonfinite_values(self):
        for body in ({'clip_ids': ['no']}, {'clip_ids': []}, {'clip_ids': ['c'], 'ratio': 'nan'}):
            with self.subTest(body=body), self.assertRaises(ValueError): self.action('cleanup', **body)
        p = copy.deepcopy(self.p); p['sequences'][0]['tracks'][0]['locked'] = True
        with self.assertRaisesRegex(ValueError, 'unlocked'):
            workflow.build(p, {'action': 'cleanup', 'sequence': 's', 'clip_ids': ['c']})

    def test_captions_preserve_literal_text_and_word_timings(self):
        p = copy.deepcopy(self.p); p['sequences'][0]['transcript'][0]['w'] = '<image & name>.'
        result, _ = workflow.build(p, {'action': 'captions', 'sequence': 's', 'max_words': 3, 'style': 'boxed'})
        seq = result['sequences'][0]
        self.assertEqual(seq['captions'][0]['text'], '<image & name>.')
        self.assertEqual((seq['captions'][0]['start'], seq['captions'][0]['end']), (.2, .8))
        self.assertTrue(seq['caption_style']['box'])

    def test_versions_have_separate_editable_content_and_legible_caption_layer(self):
        result, reply = self.action('versions', formats=['portrait', 'square'], fit='cover')
        first, second = [workflow.sequence(result, sid) for sid in reply['sequences']]
        one, two = [workflow.sequence(result, s['workflow']['content_sequence']) for s in (first, second)]
        self.assertNotEqual(one['id'], two['id'])
        self.assertEqual(workflow.words_of(first, result), first['transcript'])
        one['tracks'][0]['clips'][0]['out'] = 1
        self.assertEqual(two['tracks'][0]['clips'][0]['out'], 4)
        self.assertEqual(first['tracks'][0]['clips'][0]['fit'], 'cover')
        self.assertEqual(one['captions'], [])
        self.assertEqual(first['captions'][0]['text'], 'One Remove Three')

    def test_nested_dependencies_are_cloned_recursively_and_cycles_rejected(self):
        p = copy.deepcopy(self.p); nested = copy.deepcopy(p['sequences'][0]); nested['id'] = 'nested'; p['sequences'].append(nested)
        clip = p['sequences'][0]['tracks'][0]['clips'][0]; clip.update(media_id=None, sequence_id='nested')
        result, reply = workflow.build(p, {'action': 'versions', 'sequence': 's', 'formats': ['square']})
        output = workflow.sequence(result, reply['sequence']); content = workflow.sequence(result, output['workflow']['content_sequence'])
        dependency = content['tracks'][0]['clips'][0]['sequence_id']
        self.assertNotEqual(dependency, 'nested')
        nested['tracks'][0]['clips'][0].update(media_id=None, sequence_id='s')
        with self.assertRaisesRegex(ValueError, 'itself'):
            workflow.build(p, {'action': 'versions', 'sequence': 's', 'formats': ['square']})

    def test_versions_do_not_certify_stale_word_timings(self):
        p = copy.deepcopy(self.p); seq = p['sequences'][0]; seq['transcript_basis'] = workflow.transcript_basis(seq)
        seq['tracks'][0]['clips'][0]['in_'] = 1
        result, reply = workflow.build(p, {'action': 'versions', 'sequence': 's', 'formats': ['square']})
        self.assertEqual(workflow.sequence(result, reply['sequence'])['transcript'], [])
        self.assertIn('Outdated word timings', reply['message'])

    def test_delivery_versions_and_snapshots_retain_timecode_display_preference(self):
        p = copy.deepcopy(self.p)
        p['sequences'][0].update(fps=30000/1001, timecode_format='df')
        result, reply = workflow.build(p, {'action': 'versions', 'sequence': 's', 'formats': ['square']})
        output = workflow.sequence(result, reply['sequence'])
        content = workflow.sequence(result, output['workflow']['content_sequence'])
        self.assertEqual((output['fps'], output['timecode_format']), (30000/1001, 'df'))
        self.assertEqual(content['timecode_format'], 'df')

    def test_nested_content_edit_invalidates_delivery_transcript(self):
        result, reply = self.action('versions', formats=['square'])
        output = workflow.sequence(result, reply['sequence'])
        content = workflow.sequence(result, output['workflow']['content_sequence'])
        content['tracks'][0]['clips'][0]['start'] = 1
        with self.assertRaisesRegex(ValueError, 'timeline changed'):
            workflow.build(result, {'action': 'captions', 'sequence': output['id']})

    def test_cleanup_rejects_unlinked_and_soloed_out_video_audio(self):
        p = copy.deepcopy(self.p); seq = p['sequences'][0]; seq['tracks'][0]['clips'][0]['audio'] = {'linked': False}
        with self.assertRaises(ValueError): workflow.build(p, {'action': 'cleanup', 'sequence': 's', 'clip_ids': ['c']})
        seq['tracks'][0]['clips'][0]['audio']['linked'] = True
        seq['tracks'].append({'id': 'A1', 'kind': 'audio', 'index': 1, 'solo': True, 'clips': []})
        with self.assertRaises(ValueError): workflow.build(p, {'action': 'cleanup', 'sequence': 's', 'clip_ids': ['c']})


class Routes(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp()
        # Socket wakeups for asyncio.to_thread are unavailable on this managed
        # host. Execute the production copy/probe callable eagerly in this adapter.
        async def in_thread(fn, *args, **kwargs): return fn(*args, **kwargs)
        self.env.update(sys=types.SimpleNamespace(frozen=False), asyncio=types.SimpleNamespace(to_thread=in_thread), _owned_render_thread=in_thread, math=math, UploadFile=object, File=lambda *a: None)
        tree = ast.parse((ROOT / 'backend/server.py').read_text())
        names = {'_workflow_capture', '_workflow_commit', 'workflow_action', 'workflow_transcribe', '_workflow_transcription',
                 'workflow_import', 'transcript', 'captions_auto', '_update_ingested_media', 'install_whisper'}
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)
        self.assertEqual(len(nodes), len(names))
        self.starts = []
        class Thread:
            def __init__(self, **kw): self.kw = kw
            def start(thread): self.starts.append(thread.kw)
        self.env['threading'] = types.SimpleNamespace(Thread=Thread)
        self.env['finish_ingest'] = lambda *args: self.starts.append(args)
        self.env['broadcast_threadsafe'] = lambda event: None
        self.env['probe'] = lambda path: {'has_video': True, 'has_audio': False, 'duration': 1, 'width': 64, 'height': 32}

    def act(self, **body):
        return self.invoke('workflow_action', {'_context': self.current(), 'action': 'versions', 'sequence': 'seq1', 'formats': ['portrait'], **body})

    def test_context_required_stale_and_cross_project_requests_do_not_write(self):
        before = self.raw()
        with self.assertRaises(store.HTTPError) as err: self.invoke('workflow_action', {'action': 'captions'})
        self.assertEqual(err.exception.status_code, 400)
        expected = self.current(); self.env['set_active_project']('b'); before_b = self.raw('b')
        with self.assertRaises(store.HTTPError) as err:
            self.invoke('workflow_action', {'action': 'versions', 'sequence': 'seq1', 'formats': ['square'], '_context': expected})
        self.assertEqual(err.exception.status_code, 409)
        self.assertEqual(self.raw(), before); self.assertEqual(self.raw('b'), before_b)

    def test_complete_action_is_one_undo_step_and_redo_restores_it(self):
        before = json.loads(self.raw()); result = self.act(); after = json.loads(self.raw())
        self.assertEqual(result['context'], self.current())
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)
        asyncio.run(self.env['project_history_action']('undo', store.Request({'_context': self.current()})))
        restored = json.loads(self.raw()); restored.pop('updated', None); before.pop('updated', None)
        self.assertEqual(restored, before)
        asyncio.run(self.env['project_history_action']('redo', store.Request({'_context': self.current()})))
        redone = json.loads(self.raw()); redone.pop('updated', None); after.pop('updated', None)
        self.assertEqual(redone, after)

    def test_event_failure_does_not_report_failed_commit(self):
        self.env['log_event'] = lambda *a, **k: (_ for _ in ()).throw(OSError('fixture history error'))
        result = self.act()
        self.assertTrue(result['ok']); self.assertIn('history unavailable', result['warning'])
        self.assertEqual(result['context'], self.current())

    def test_missing_speech_tools_never_start_render_or_write(self):
        before = self.raw()
        with patch.dict(sys.modules, {'faster_whisper': None}):
            reply = self.invoke('workflow_transcribe', {'_context': self.current(), 'sequence': 'seq1'})
        self.assertEqual(reply.status_code, 501); self.assertEqual(self.raw(), before)

    def test_frozen_build_does_not_launch_itself_as_a_pip_installer(self):
        self.env['sys'].frozen = True
        reply = asyncio.run(self.env['install_whisper']())
        self.assertFalse(reply['ok']); self.assertIn('source installer', reply['message'])
        with patch.dict(sys.modules, {'faster_whisper': None}):
            reply = self.invoke('workflow_transcribe', {'_context': self.current(), 'sequence': 'seq1'})
        self.assertEqual(reply.status_code, 501); self.assertIn('packaged build', reply['error'])

    def speech(self, change=None):
        async def analysis(*a, **kw):
            if change: change()
            return [{'w': 'Hello.', 's': .1, 'e': .5}], [{'id': 'cap', 'start': .1, 'end': .5, 'text': 'Hello.'}]
        self.env['_owned_render_thread'] = analysis; self.env['_transcribe_sequence'] = object()
        return patch.dict(sys.modules, {'faster_whisper': types.ModuleType('faster_whisper')})

    def test_transcription_is_undoable_and_keeps_existing_captions_until_requested(self):
        before = json.loads(self.raw())
        with self.speech(): reply = self.invoke('workflow_transcribe', {'_context': self.current(), 'sequence': 'seq1'})
        self.assertEqual(reply['words'], 1)
        self.assertEqual(json.loads(self.raw())['sequences'][0].get('captions'), before['sequences'][0].get('captions'))
        self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)
        asyncio.run(self.env['project_history_action']('undo', store.Request({'_context': self.current()})))
        self.assertNotIn('transcript', json.loads(self.raw())['sequences'][0])

    def test_slow_legacy_and_workflow_transcriptions_never_redirect_to_another_project(self):
        for route in ('workflow_transcribe', 'transcript', 'captions_auto'):
            with self.subTest(route=route):
                self.env['set_active_project']('a'); before_a, before_b = self.raw(), self.raw('b'); expected = self.current()
                with self.speech(lambda: self.env['set_active_project']('b')):
                    with self.assertRaises(store.HTTPError) as err: self.invoke(route, {'_context': expected, 'sequence': 'seq1'})
                self.assertEqual(err.exception.status_code, 409)
                self.assertEqual(self.raw(), before_a); self.assertEqual(self.raw('b'), before_b)

    def test_retranscription_preserves_manual_captions_and_requests_review(self):
        proj = self.env['load_project'](); seq = proj['sequences'][0]
        seq['captions'] = [{'id':'manual','text':'A deliberate rewrite','start':0,'end':1}]
        seq['transcript'] = [{'w':'Previous.','s':.1,'e':.5}]; self.env['save_project'](proj)
        with self.speech(): self.invoke('workflow_transcribe', {'_context':self.current(),'sequence':'seq1'})
        seq = json.loads(self.raw())['sequences'][0]
        self.assertEqual(seq['captions'][0]['text'],'A deliberate rewrite')
        self.assertEqual(seq['workflow']['caption_review_ids'],['manual'])
        with self.speech(): self.invoke('workflow_transcribe', {'_context':self.current(),'sequence':'seq1','captions':True})
        seq = json.loads(self.raw())['sequences'][0]
        self.assertEqual(seq['captions'][0]['text'],'Hello.'); self.assertEqual(seq['workflow']['caption_review_ids'],[])

    def test_edit_during_transcription_is_preserved(self):
        def edit():
            proj = self.env['load_project'](); proj['name'] = 'Changed during analysis'; self.env['save_project'](proj)
        with self.speech(edit), self.assertRaises(store.HTTPError):
            self.invoke('workflow_transcribe', {'_context': self.current(), 'sequence': 'seq1'})
        proj = json.loads(self.raw()); self.assertEqual(proj['name'], 'Changed during analysis'); self.assertNotIn('transcript', proj['sequences'][0])

    def upload(self, names=('a.mp4', 'b.mp4'), context=None):
        req = store.Request({}); req.headers = {'x-filmocity-context': json.dumps(context or self.current())}
        files = [types.SimpleNamespace(filename=name, file=io.BytesIO(b'fixture media')) for name in names]
        return asyncio.run(self.env['workflow_import'](req, files))

    def test_batch_import_is_atomic_safe_for_windows_names_and_preserves_history_through_ingest(self):
        reply = self.upload(('..\\..\\outside.mp4', '../another.mp4'))
        self.assertEqual(len(reply['added']), 2); self.assertEqual(len(self.starts), 2)
        mid = reply['added'][0]; proj = json.loads(self.raw()); media = proj['media'][mid]
        self.assertEqual(media['name'], 'outside.mp4'); self.assertEqual(Path(media['path']).parent, self.root / 'media')
        self.assertTrue(self.env['_update_ingested_media'](mid, media['path'], {'status': 'ready', 'thumb': '/thumb.png'}, str(self.root / 'projects/a/project.json'), media['ingest_token']))
        asyncio.run(self.env['project_history_action']('undo', store.Request({'_context': self.current()})))
        self.assertNotIn(mid, json.loads(self.raw())['media'])
        asyncio.run(self.env['project_history_action']('redo', store.Request({'_context': self.current()})))
        self.assertEqual(json.loads(self.raw())['media'][mid]['status'], 'ready')
        self.assertTrue(Path(media['path']).is_file())

    def test_bad_second_file_leaves_no_partial_import_or_threads(self):
        calls = []
        def probe(path):
            calls.append(path)
            return {'has_video': True, 'duration': 1} if len(calls) == 1 else {}
        self.env['probe'] = probe; before = self.raw()
        with self.assertRaises(store.HTTPError): self.upload()
        self.assertEqual(self.raw(), before); self.assertEqual(self.starts, [])
        self.assertEqual(list((self.root / 'media').iterdir()), [])

    def test_project_switch_during_probe_rejects_import_and_cleans_copies(self):
        def probe(path): self.env['set_active_project']('b'); return {'has_video': True, 'duration': 1}
        self.env['probe'] = probe; before_a, before_b = self.raw(), self.raw('b')
        with self.assertRaises(store.HTTPError) as err: self.upload(('clip.mp4',))
        self.assertEqual(err.exception.status_code, 409)
        self.assertEqual(self.raw(), before_a); self.assertEqual(self.raw('b'), before_b)
        self.assertEqual(list((self.root / 'media').iterdir()), []); self.assertEqual(self.starts, [])


class RenderedWorkflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='Filmocity workflow É '); self.root = Path(self.temp.name)
        self.p = project(); seq = self.p['sequences'][0]; seq['captions'] = []; seq['tracks'][0]['clips'][0]['keyframes'] = {}
        source = self.root / 'source.png'; Image.new('RGB', (64, 32), (210, 70, 20)).save(source)
        self.p['media']['a'].update(path=str(source), is_image=True, has_audio=False)
        audio = self.root / 'voice.wav'
        samples = array.array('h', (round(12000 * math.sin(2 * math.pi * 50 * i / 48000)) for i in range(192000)))
        if sys.byteorder != 'little': samples.byteswap()
        with wave.open(str(audio), 'wb') as writer: writer.setparams((1, 2, 48000, 0, 'NONE', 'not compressed')); writer.writeframes(samples.tobytes())
        self.p['media']['b'].update(path=str(audio), duration=4)
        seq['tracks'].append({'id': 'A1', 'kind': 'audio', 'index': 1, 'clips': [{'id': 'audio', 'media_id': 'b', 'start': 0, 'in_': 0, 'out': 4, 'speed': 1}]})
    def tearDown(self): self.temp.cleanup()

    def test_story_cut_renders_retained_video_and_exact_audio_duration(self):
        result, reply = workflow.build(self.p, {'action': 'story', 'sequence': 's', 'words': [0, 2], 'padding': 0})
        out = self.root / 'story.wav'
        engine.render(result, reply['sequence'], str(out), {'format': 'audio', 'acodec': 'wav'})
        with wave.open(str(out)) as audio: self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), 1.4, delta=.03)
        image = self.root / 'frame.png'; engine.render_frame(result, reply['sequence'], .8, str(image))
        with Image.open(image) as frame: self.assertEqual(frame.convert('RGB').getpixel((32, 16)), (210, 70, 20))

    def test_cleanup_reduces_low_frequency_energy_in_rendered_audio(self):
        before = self.root / 'before.wav'; after = self.root / 'after.wav'
        engine.render(self.p, 's', str(before), {'format': 'audio', 'acodec': 'wav'})
        result, _ = workflow.build(self.p, {'action': 'cleanup', 'sequence': 's', 'clip_ids': ['audio'], 'highpass': 150, 'ratio': 1, 'denoise': 0})
        engine.render(result, 's', str(after), {'format': 'audio', 'acodec': 'wav'})
        def rms(path):
            with wave.open(str(path)) as reader: values = array.array('h', reader.readframes(reader.getnframes()))
            if sys.byteorder != 'little': values.byteswap()
            return math.sqrt(sum(x*x for x in values) / len(values))
        self.assertLess(rms(after), rms(before) * .2)

    def test_subsecond_story_does_not_add_a_second_of_dead_time(self):
        result, reply = workflow.build(self.p, {'action': 'story', 'sequence': 's', 'words': [0], 'padding': 0})
        out = self.root / 'short.wav'
        engine.render(result, reply['sequence'], str(out), {'format': 'audio', 'acodec': 'wav'})
        with wave.open(str(out)) as audio: self.assertAlmostEqual(audio.getnframes() / audio.getframerate(), .6, delta=.025)

    def test_version_renders_fit_letterbox_and_separate_caption_layer(self):
        # Full production nested render; small fixture canvas avoids a 4s HD intermediate.
        self.p['sequences'][0]['tracks'][0]['clips'][0]['out'] = .2
        self.p['sequences'][0]['tracks'][1]['clips'][0]['out'] = .2
        result, reply = workflow.build(self.p, {'action': 'versions', 'sequence': 's', 'formats': ['square'], 'fit': 'contain'})
        seq = workflow.sequence(result, reply['sequence']); seq['width'] = seq['height'] = 64
        out = self.root / 'version.png'; engine.render_frame(result, seq['id'], .1, str(out))
        with Image.open(out) as frame:
            rgb = frame.convert('RGB'); self.assertEqual(rgb.getpixel((32, 32)), (210, 70, 20)); self.assertEqual(rgb.getpixel((32, 2)), (0, 0, 0))

    def test_assembled_order_survives_real_picture_rendering(self):
        blue = self.root / 'blue.png'; Image.new('RGB', (64, 32), (20, 70, 210)).save(blue)
        self.p['media']['blue'] = dict(self.p['media']['a'], id='blue', path=str(blue), duration=.4)
        self.p['media']['a']['duration'] = .4
        result, reply = workflow.build(self.p, {'action': 'assemble', 'media_ids': ['blue', 'a']})
        seq = workflow.sequence(result, reply['sequence']); seq.update(width=64, height=32)
        for time, color in ((.1, (20, 70, 210)), (.6, (210, 70, 20))):
            out = self.root / 'order.png'; engine.render_frame(result, seq['id'], time, str(out))
            with Image.open(out) as frame: self.assertEqual(frame.convert('RGB').getpixel((32, 16)), color)


if __name__ == '__main__': unittest.main()
