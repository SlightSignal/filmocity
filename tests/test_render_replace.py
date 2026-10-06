"""Lossless owned bakes and actual guarded store/history apply contracts."""
import array
import ast
import asyncio
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import render_replace as bake
import render
from background_tasks import TaskManager, TaskContext, TaskError
from render_context import RenderContext
from test_media_collection import run_async_check
import test_project_sync as store


def source(root, seconds=6):
    path = Path(root) / 'stereo.wav'
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((2, 2, 48000, 0, 'NONE', '')); stream.writeframes(array.array('h', [3276, 6553] * 48000 * seconds).tobytes())
    return path


def project(path, audio=True):
    clip = {'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 1, 'out': 3, 'speed': 1,
            'audio': {'gain_db': -3, 'fade_in': .5, 'fade_out': .5}, 'markers': [{'t': .5, 'name': 'kept'}], 'group': 'group'}
    tracks = [{'id': 'a', 'kind': 'audio', 'index': 0, 'gain_db': -4, 'clips': [clip]}]
    return {'version': 3, 'media': {'m': {'id': 'm', 'path': str(path), 'duration': 6, 'has_audio': True,
             'has_video': not audio, 'fps': 30, 'frame_rate': '30/1', 'channels': 2, 'sample_rate': 48000}},
            'sequences': [{'id': 's', 'width': 64, 'height': 48, 'fps': 30, 'master': {'gain_db': -2}, 'tracks': tracks}]}


def apply_plan(proj, plan):
    result = copy.deepcopy(proj)
    for op in plan['ops']:
        parts = op['path'].strip('/').split('/'); owner = result
        for key in parts[:-1]: owner = owner[int(key)] if isinstance(owner, list) else owner[key]
        owner[int(parts[-1]) if isinstance(owner, list) else parts[-1]] = copy.deepcopy(op['value'])
    return result


class OwnedBakes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity bake É's "); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); self.path = source(self.root); self.proj = project(self.path)
        self.manager = TaskManager(self.root, {'render_replace': bake.bake}, start=False)
        self.addCleanup(self.manager.shutdown)
        self.context = {'workspace': self.manager.store.workspace, 'project': 'p', 'revision': 'initial'}

    def capture(self): return bake.capture(self.proj, {'sequence': 's', 'clip_id': 'c'}, self.context, self.root)

    def run_bake(self):
        payload = self.capture(); task = self.manager.submit('render_replace', 'Bake', self.context, payload)
        context = TaskContext(self.manager, task['id']); result = bake.bake(payload, context)
        return payload, result, task['id']

    def pcm(self, proj, name):
        path = self.root / name
        with RenderContext(scratch_parent=str(self.root)) as context:
            render.render(proj, 's', str(path), {'format': 'audio', 'acodec': 'wav'}, context=context)
        with wave.open(str(path), 'rb') as stream: return array.array('h', stream.readframes(stream.getnframes()))

    def test_real_audio_preserves_reverse_ramp_inherited_fades_and_track_master_gain(self):
        from overlap_normalization import slice_clip
        clip = self.proj['sequences'][0]['tracks'][0]['clips'][0]
        clip.update(in_=0, out=6, reverse=True, time_remap=[{'t': 0, 'v': 1.5}],
                    keyframes={'audio.gain_db': [{'t': 0, 'v': -8}, {'t': 4, 'v': -2}],
                               'audio.duck_db': [{'t': 0, 'v': 0}, {'t': 2, 'v': -3}]},
                    audio={'gain_db': -3, 'fade_in': 3, 'fade_out': 2}, source_edit_window={'version': 1, 'ramp': {'stale': True}})
        sliced = slice_clip(clip, 1, 3); sliced['start'] = 0
        self.proj['sequences'][0]['tracks'][0]['clips'] = [sliced]
        before = self.pcm(self.proj, 'before.wav'); digest = bake.file_hash(self.path)
        payload, result, identity = self.run_bake(); plan = bake.plan(self.proj, payload, result, identity)
        bake.publish(payload, result, identity); after_project = apply_plan(self.proj, plan)
        after = self.pcm(after_project, 'after.wav')
        self.assertEqual(len(before), len(after)); self.assertLessEqual(max(abs(a-b) for a,b in zip(before, after)), 1)
        self.assertEqual(bake.file_hash(self.path), digest)
        replacement = after_project['sequences'][0]['tracks'][0]['clips'][0]
        self.assertEqual(replacement['audio']['fade_window'], sliced['audio']['fade_window'])
        self.assertEqual(replacement['group'], 'group'); self.assertEqual(replacement['markers'], sliced['markers'])
        self.assertNotIn('time_remap', replacement); self.assertNotIn('ramp', replacement.get('source_edit_window', {}))
        self.assertEqual(result['preview']['kind'], 'audio'); self.assertTrue(Path(bake.preview_path(payload, result, identity)).is_file())

    def test_transparent_picture_bake_decodes_alpha_and_keeps_outer_transitions(self):
        from PIL import Image
        png = self.root / 'source.png'; Image.new('RGBA', (64, 48), (200, 20, 10, 128)).save(png)
        self.proj['media']['m'].update(path=str(png), has_video=True, has_audio=False, is_image=True)
        track = self.proj['sequences'][0]['tracks'][0]; track.update(kind='video')
        clip = track['clips'][0]; clip.update(in_=0, out=1, transition_in={'type': 'dissolve', 'duration': .2, 'align': 'start'}, transition_out={'type': 'dissolve', 'duration': .2})
        payload, result, identity = self.run_bake()
        self.assertEqual(result['media']['vcodec'], 'ffv1'); self.assertEqual(result['media']['pix_fmt'], 'bgra')
        decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', result['media']['path'], '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'])
        self.assertEqual(len(decoded), 64 * 48 * 4); self.assertTrue(all(126 <= a <= 130 for a in decoded[3::4]))
        replacement = bake.plan(self.proj, payload, result, identity)['ops'][1]['value']
        self.assertEqual(replacement['transition_in'], clip['transition_in']); self.assertEqual(replacement['transition_out'], clip['transition_out'])
        self.assertEqual(result['preview']['kind'], 'image')

    def test_title_graphics_and_self_contained_nested_picture_bake_without_flattening_alpha(self):
        original = copy.deepcopy(self.proj)
        for kind in ('title', 'graphic', 'nested'):
            self.proj = copy.deepcopy(original); track = self.proj['sequences'][0]['tracks'][0]; track['kind'] = 'video'
            clip = {'id': 'c', 'start': 0, 'in_': 0, 'out': .3}
            if kind == 'title': clip['title'] = {'text': 'Hi', 'size': 16, 'color': 'white'}
            if kind == 'graphic': clip['graphic'] = {'layers': [{'kind': 'box', 'x': .25, 'y': .25, 'w': .5, 'h': .5, 'color': 'red'}]}
            if kind == 'nested':
                child = copy.deepcopy(self.proj['sequences'][0]); child.update(id='child', name='Nested title', master={})
                child['tracks'] = [{'id': 'child-v', 'kind': 'video', 'index': 0, 'clips': [{'id': 'title', 'start': 0, 'in_': 0, 'out': .3, 'title': {'text': 'Hi', 'size': 16}}]}]
                self.proj['sequences'].append(child); clip['sequence_id'] = 'child'
            track['clips'] = [clip]
            with self.subTest(kind=kind):
                payload, result, identity = self.run_bake(); self.assertEqual(result['duration'], .3)
                decoded = subprocess.check_output(['ffmpeg', '-v', 'error', '-i', result['media']['path'], '-frames:v', '1', '-f', 'rawvideo', '-pix_fmt', 'rgba', '-'])
                self.assertIn(0, decoded[3::4]); self.assertGreater(max(decoded[3::4]), 200)
                replacement = bake.plan(self.proj, payload, result, identity)['ops'][1]['value']
                self.assertFalse(any(key in replacement for key in ('title', 'graphic', 'sequence_id')))

    def test_unlinked_source_audio_remains_available_when_enabled_after_bake_and_name_survives(self):
        video = self.root / 'picture.mkv'
        subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=red:s=64x48:r=30:d=2',
                        '-i', str(self.path), '-map', '0:v', '-map', '1:a', '-t', '2', '-c:v', 'ffv1', '-threads', '1', '-c:a', 'pcm_f32le', str(video)],
                       check=True, capture_output=True, timeout=30)
        self.proj['media']['m'].update(path=str(video), has_video=True, duration=2)
        track = self.proj['sequences'][0]['tracks'][0]; track['kind'] = 'video'
        clip = track['clips'][0]; clip.update(in_=0, out=1, name='Custom editorial name'); clip['audio']['linked'] = False
        payload, result, identity = self.run_bake(); plan = bake.plan(self.proj, payload, result, identity); bake.publish(payload, result, identity)
        changed = apply_plan(self.proj, plan); replacement = changed['sequences'][0]['tracks'][0]['clips'][0]
        self.assertFalse(replacement['audio']['linked']); self.assertEqual(replacement['name'], clip['name'])
        replacement['audio']['linked'] = True; clip['audio']['linked'] = True
        before, after = self.pcm(self.proj, 'enabled-before.wav'), self.pcm(changed, 'enabled-after.wav')
        self.assertGreater(max(after), 1000); self.assertEqual(before, after)

    def test_preview_checks_small_preview_hash_without_rescanning_complete_bake(self):
        payload, result, identity = self.run_bake(); actual = bake.file_hash; hashed = []
        def observe(path, *args, **kwargs):
            hashed.append(str(path)); return actual(path, *args, **kwargs)
        with patch.object(bake, 'file_hash', observe): self.assertTrue(bake.preview_path(payload, result, identity))
        self.assertEqual(hashed, [result['preview']['path']])

    def test_locks_disabled_detached_and_dependent_composition_reject_before_task(self):
        original = copy.deepcopy(self.proj)
        for mode in ('locked', 'disabled', 'detached', 'adjustment', 'blend', 'handle_transition'):
            self.proj = copy.deepcopy(original); track = self.proj['sequences'][0]['tracks'][0]; clip = track['clips'][0]
            if mode == 'locked': track['locked'] = True
            if mode == 'disabled': clip['enabled'] = False
            if mode == 'detached': clip['audio_detached_id'] = 'child'
            if mode == 'adjustment': clip['adjustment'] = True
            if mode == 'blend': clip['blend'] = 'screen'
            if mode == 'handle_transition': clip.update(start=2, transition_in={'type': 'dissolve', 'duration': .5})
            with self.subTest(mode=mode), self.assertRaises(ValueError): self.capture()

    def test_current_validation_allows_unrelated_edits_but_rejects_source_target_and_resource_change(self):
        payload = self.capture(); self.proj['name'] = 'Unrelated'; self.assertTrue(bake.validate_current(self.proj, payload, self.context, self.root))
        self.proj['sequences'][0]['tracks'][0]['clips'][0]['note'] = 'Changed'
        with self.assertRaises(ValueError): bake.validate_current(self.proj, payload, self.context, self.root)
        self.proj['sequences'][0]['tracks'][0]['clips'][0].pop('note')
        with self.path.open('ab') as stream: stream.write(b'changed')
        with self.assertRaises(ValueError): bake.validate_current(self.proj, payload, self.context, self.root)

    def test_cancelled_work_and_encode_failure_do_not_publish_replacement(self):
        payload = self.capture(); task = self.manager.submit('render_replace', 'Bake', self.context, payload)
        context = TaskContext(self.manager, task['id']); context.holder['cancelled'] = True
        with self.assertRaises(RuntimeError): bake.bake(payload, context)
        context.holder.clear()
        with patch.object(bake, '_run', side_effect=RuntimeError('encoder unavailable')), self.assertRaisesRegex(RuntimeError, 'encoder unavailable'): bake.bake(payload, context)
        self.assertFalse((bake.artifact_folder(self.root, task['id']) / 'bake.wav').exists())
        self.assertFalse((self.root / 'media').exists())

    def test_running_cancel_reaps_owned_ffmpeg_and_persists_without_ready_result(self):
        def slow(command, task, context, duration):
            render._run_ffmpeg(['ffmpeg', '-v', 'error', '-nostdin', '-re', '-f', 'lavfi', '-i', 'anullsrc=r=48000:cl=stereo',
                               '-t', '30', '-c:a', 'pcm_f32le', str(bake.artifact_folder(self.root, task.id) / 'pending.wav')],
                              total=30, context=context)
        manager = TaskManager(self.root, {'render_replace': bake.bake}, workers=1); self.addCleanup(manager.shutdown)
        with patch.object(bake, '_run', slow):
            identity = manager.submit('render_replace', 'Cancel real process', self.context, self.capture())['id']
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                context = manager.contexts.get(identity)
                if context and context.holder.get('proc'): break
                time.sleep(.01)
            self.assertIsNotNone(context); child = context.holder['proc']; manager.cancel(identity)
            while identity in manager.contexts and time.monotonic() < deadline: time.sleep(.01)
            self.assertNotIn(identity, manager.contexts); self.assertIsNotNone(child.poll())
            value = manager.get(identity); self.assertEqual(value['record']['status'], 'cancelled'); self.assertIsNone(value['result'])
            self.assertFalse((bake.artifact_folder(self.root, identity) / 'bake.wav').exists())

    def test_output_corruption_missing_preview_and_existing_publication_collision_refuse(self):
        payload, result, identity = self.run_bake()
        preview = Path(result['preview']['path']); preview.write_bytes(b'changed')
        with self.assertRaises(ValueError): bake.preview_path(payload, result, identity)
        output = self.root / 'media' / 'rendered'; output.mkdir(parents=True); (output / (identity + '.wav')).write_bytes(b'unrelated')
        with self.assertRaises(ValueError): bake.publish(payload, result, identity)
        Path(result['media']['path']).write_bytes(b'changed')
        with self.assertRaises(ValueError): bake.plan(self.proj, payload, result, identity)

    def test_copy_source_open_failure_removes_owned_stage_and_preserves_foreign_stage(self):
        payload, result, identity = self.run_bake(); actual = Path.open
        def missing_source(path, mode='r', *args, **kwargs):
            if str(path) == result['media']['path'] and mode == 'rb': raise PermissionError('source sharing violation')
            return actual(path, mode, *args, **kwargs)
        with patch.object(Path, 'open', missing_source), self.assertRaises(PermissionError): bake.publish(payload, result, identity)
        stage = self.root / 'media/rendered' / (identity + '.pending.wav')
        self.assertFalse(stage.exists())
        stage.write_bytes(b'preexisting unrelated stage')
        with self.assertRaises(FileExistsError): bake.publish(payload, result, identity)
        self.assertEqual(stage.read_bytes(), b'preexisting unrelated stage'); stage.unlink()
        self.assertTrue(Path(bake.publish(payload, result, identity)).is_file())


class StoreBakes(store.ProjectStoreFixture):
    def setUp(self):
        super().setUp(); self.path = source(self.root); project_ = self.env['load_project']()
        project_.update(project(self.path)); self.env['save_project'](project_)
        self.manager = TaskManager(self.root, {'render_replace': bake.bake}, start=False); self.addCleanup(self.manager.shutdown)
        self.prepared = []
        self.env.update(asyncio=asyncio, TASKS=self.manager, TaskError=TaskError,
                        FileResponse=lambda path, **kw: {'path': path, **kw},
                        finish_ingest=lambda *args: self.prepared.append(args) or {})
        names = {'render_replace', '_task_render_replace', '_review_render_replace', 'background_render_replace_review',
                 'background_render_replace_preview', '_apply_render_replace', '_owned_task', 'background_task_apply',
                 'background_task_retry', '_workflow_capture', '_workflow_commit', '_owned_render_thread'}
        nodes = [node for node in ast.parse((ROOT / 'backend/server.py').read_text()).body if getattr(node, 'name', None) in names]
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)

    def route(self, name, body=None, identity=None):
        args = ([identity] if identity else []) + ([store.Request(body)] if body is not None else [])
        return run_async_check(self.env[name](*args))

    def ready(self):
        body = {'_context': self.current(), 'sequence': 's', 'clip_id': 'c', 'request_id': 'a' * 32}
        queued = self.route('render_replace', body); identity = queued['task']['id']
        value = self.manager.values[identity]; value['result'] = bake.bake(value['payload'], TaskContext(self.manager, identity))
        value['record']['status'] = 'ready'; self.manager.store.save(value)
        return identity

    def review(self, identity): return self.route('background_render_replace_review', {'_context': self.current()}, identity)

    def apply(self, identity, review, **extra):
        return self.route('background_task_apply', {'_context': review['context'], 'fingerprint': review['plan']['fingerprint'], **extra}, identity)

    def test_acknowledged_submit_identity_and_ready_persist_without_editing_project(self):
        before = self.raw()
        with self.assertRaises(store.HTTPError): self.route('render_replace', {'sequence': 's', 'clip_id': 'c'})
        identity = self.ready(); self.assertEqual(self.raw(), before)
        repeated = self.route('render_replace', {'_context': self.current(), 'sequence': 's', 'clip_id': 'c', 'request_id': identity})
        self.assertEqual(repeated['task']['id'], identity)
        restored = TaskManager(self.root, {'render_replace': bake.bake}, start=False); self.addCleanup(restored.shutdown)
        self.assertEqual(restored.get(identity)['record']['status'], 'ready')

    def test_apply_is_atomic_undoable_idempotent_and_preparation_is_after_commit(self):
        identity = self.ready(); before = self.env['load_project'](); review = self.review(identity)
        self.assertTrue(review['plan']['ops']); result = self.apply(identity, review); self.assertTrue(result['ok'])
        saved = self.env['load_project'](); self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)
        self.assertEqual(saved['sequences'][0]['workflow']['render_replace'][identity]['clip_id'], 'c')
        self.assertEqual(self.prepared[0][3], str(self.root / 'projects/a/project.json'))
        self.assertTrue(Path(saved['media'][result['media_id']]['path']).is_file())
        retry = self.route('background_task_apply', {'_context': self.current()}, identity)
        self.assertTrue(retry['ok']); self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)
        self.invoke('undo', {'_context': self.current()}); self.assertEqual(self.env['load_project']()['sequences'], before['sequences'])
        self.invoke('redo', {'_context': self.current()}); self.assertEqual(self.env['load_project']()['sequences'], saved['sequences'])

    def test_stale_review_wrong_project_source_and_locked_target_cannot_apply(self):
        identity = self.ready(); review = self.review(identity); self.edit('Unrelated', self.current()); before = self.raw()
        with self.assertRaises(store.HTTPError): self.apply(identity, review)
        self.assertEqual(self.raw(), before); review = self.review(identity)
        self.env['set_active_project']('b'); other = self.raw('b')
        with self.assertRaises(store.HTTPError): self.apply(identity, review)
        self.assertEqual(self.raw('b'), other); self.env['set_active_project']('a')
        proj = self.env['load_project'](); proj['sequences'][0]['tracks'][0]['locked'] = True; self.env['save_project'](proj)
        with self.assertRaises(store.HTTPError): self.review(identity)

    def test_project_switch_while_bake_runs_cannot_redirect_apply(self):
        identity = self.ready(); self.env['set_active_project']('b'); before_a, before_b = self.raw('a'), self.raw('b')
        with self.assertRaises(store.HTTPError): self.route('background_task_apply', {'_context': self.current(), 'fingerprint': 'none'}, identity)
        self.assertEqual(self.raw('a'), before_a); self.assertEqual(self.raw('b'), before_b)

    def test_project_switch_during_publication_refuses_commit_and_can_be_retried_in_owner(self):
        identity = self.ready(); review = self.review(identity); before_a, before_b = self.raw('a'), self.raw('b'); publish = bake.publish
        def switch(*args, **kwargs):
            result = publish(*args, **kwargs); self.env['set_active_project']('b'); return result
        with patch.object(bake, 'publish', switch), self.assertRaises(store.HTTPError): self.apply(identity, review)
        self.assertEqual(self.raw('a'), before_a); self.assertEqual(self.raw('b'), before_b)
        self.assertEqual(self.manager.get(identity)['record']['status'], 'ready')
        self.env['set_active_project']('a'); self.assertTrue(self.apply(identity, review)['ok'])

    def test_lost_task_receipt_write_cannot_repeat_committed_replacement(self):
        identity = self.ready(); review = self.review(identity); save = self.manager.store.save
        def fail_final(value):
            if value['record']['status'] == 'applied': raise OSError('lost receipt')
            return save(value)
        with patch.object(self.manager.store, 'save', fail_final):
            applied = self.apply(identity, review)
        self.assertTrue(applied['ok']); self.assertIn('history could not be saved', applied['warning'])
        restored = TaskManager(self.root, {'render_replace': bake.bake}, start=False); self.addCleanup(restored.shutdown)
        self.env['TASKS'] = restored; self.assertEqual(restored.get(identity)['record']['status'], 'ready')
        before = self.raw(); retry = self.route('background_task_apply', {'_context': self.current()}, identity)
        self.assertTrue(retry['ok']); self.assertEqual(self.raw(), before); self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 1)

    def test_submit_capture_keeps_event_loop_alive_and_rechecks_context_before_queue(self):
        started, release = threading.Event(), threading.Event(); actual = bake.capture
        def slow(*args):
            started.set(); release.wait(5); return actual(*args)
        async def scenario():
            body = {'_context': self.current(), 'sequence': 's', 'clip_id': 'c', 'request_id': 'b' * 32}
            task = asyncio.create_task(self.env['render_replace'](store.Request(body)))
            for _ in range(500):
                if started.is_set(): break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set()); self.assertFalse(task.done())
            self.env['set_active_project']('b'); release.set()
            with self.assertRaises(store.HTTPError): await task
            self.assertEqual(self.manager.values, {})
        with patch.object(bake, 'capture', slow): run_async_check(scenario())

    def test_repeated_request_cancellation_joins_publication_and_removes_only_owned_stage(self):
        identity = self.ready(); review = self.review(identity); before = self.raw(); started, release = threading.Event(), threading.Event(); actual = bake.file_hash
        def blocked_hash(path, *args, **kwargs):
            if '.pending' in str(path): started.set(); release.wait(5)
            return actual(path, *args, **kwargs)
        async def scenario():
            body = {'_context': review['context'], 'fingerprint': review['plan']['fingerprint']}
            task = asyncio.create_task(self.env['background_task_apply'](identity, store.Request(body)))
            for _ in range(500):
                if started.is_set(): break
                await asyncio.sleep(.001)
            self.assertTrue(started.is_set()); task.cancel(); await asyncio.sleep(.01); task.cancel(); await asyncio.sleep(.01)
            self.assertFalse(task.done()); self.assertEqual(self.manager.get(identity)['record']['status'], 'applying')
            release.set()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertEqual(self.manager.get(identity)['record']['status'], 'ready')
            self.assertEqual(self.raw(), before); self.assertFalse(list((self.root / 'media/rendered').iterdir()))
        with patch.object(bake, 'file_hash', blocked_hash): run_async_check(scenario())

    def test_commit_failure_and_proposals_only_preserve_project_history_and_ready_task(self):
        identity = self.ready(); review = self.review(identity); before = self.raw()
        (self.root / 'settings.json').write_text(json.dumps({'agent_mode': 'proposals_only'}))
        with self.assertRaises(store.HTTPError): self.apply(identity, review, actor='agent')
        self.assertEqual(self.raw(), before); self.assertEqual(self.manager.get(identity)['record']['status'], 'ready')
        with patch.dict(self.env, commit_pair=lambda *a, **kw: (_ for _ in ()).throw(OSError('commit failed'))), self.assertRaises(OSError): self.apply(identity, review)
        self.assertEqual(self.raw(), before); self.assertEqual(len(self.env['read_undo_history']('a')['undo']), 0)
        self.assertEqual(self.manager.get(identity)['record']['status'], 'ready')
        self.assertTrue(self.apply(identity, review)['ok'])

    def test_preview_is_owned_and_hash_checked_and_preparation_failure_does_not_undo_save(self):
        identity = self.ready(); result = self.route('background_render_replace_preview', identity=identity)
        self.assertEqual(result['media_type'], 'audio/wav')
        self.env['finish_ingest'] = lambda *args: (_ for _ in ()).throw(OSError('proxy unavailable'))
        applied = self.apply(identity, self.review(identity)); self.assertTrue(applied['ok']); self.assertIn('saved', applied['warning'])
        self.assertEqual(self.manager.get(identity)['record']['status'], 'applied')


if __name__ == '__main__': unittest.main()
