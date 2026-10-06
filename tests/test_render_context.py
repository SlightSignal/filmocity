"""Production ownership/lifetime tests, including real bundled FFmpeg.

Fixtures and per-case evidence are retained under the selected temporary root.
Missing binaries fail, never skip. No native app, network listener or client data.
"""
import asyncio
import copy
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
import uuid
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
RUN = Path(tempfile.gettempdir()) / 'filmocity-render-context-tests' / ('run-' + uuid.uuid4().hex)
RUN.mkdir(parents=True)
os.environ.update(TEMP=str(RUN), TMP=str(RUN), FILMOCITY_ROOT=str(RUN / 'data'), PYTHONUTF8='1')
tempfile.tempdir = str(RUN)
sys.path.insert(0, str(ROOT / 'backend'))
import render as engine
import render_context
import server
from PIL import Image


def project(duration=.5, color='red', title=False):
    c = {'id': 'c', 'media_id': 'm', 'start': 0, 'in_': 0, 'out': duration}
    if title:
        c = dict(c, media_id=None, title={'text': '42%: Émile', 'size': 12})
    return {'media': {'m': {'id': 'm', 'synthetic': {'kind': 'color', 'color': color},
            'has_video': True, 'has_audio': False}}, 'sequences': [
            {'id': 's', 'name': 'Fixture', 'width': 64, 'height': 48, 'fps': 24,
             'duration': duration, 'captions': [], 'markers': [], 'tracks': [
             {'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [c]}]}]}


def nested_project(duration=.5, source=None):
    p = project(duration)
    child = copy.deepcopy(p['sequences'][0]); child['id'] = 'child'
    if source:
        p['media']['m'] = {'id': 'm', 'path': str(source), 'has_video': True,
                          'has_audio': False}
    p['sequences'].append(child)
    p['sequences'][0]['tracks'][0]['clips'] = [
        {'id': 'nest', 'sequence_id': 'child', 'start': 0, 'in_': 0, 'out': duration}]
    return p


@contextmanager
def paced_nested_encode():
    # Pacing is a controlled test process adapter, never serialized media argv.
    # The ordinary half-second file is finite; no infinite input loop is used.
    run = engine._run_ffmpeg
    def paced(cmd, **kwargs):
        if kwargs.get('context') and kwargs['context'].holder.get('phase') == 'nested':
            cmd = cmd.copy(); index = cmd.index('-i')
            cmd[index:index] = ['-readrate', '1']
        return run(cmd, **kwargs)
    with patch.object(engine, '_run_ffmpeg', side_effect=paced):
        yield


class RenderContexts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        for binary in ('ffmpeg', 'ffprobe'):
            if not shutil.which(binary): raise RuntimeError(f'{binary} is required; put existing bundled binaries on PATH')

    def setUp(self):
        self.root = RUN / ('case-' + uuid.uuid4().hex[:8])
        self.root.mkdir()
        self.contexts = []
        self.receipt = {'test': self.id(), 'scratch': [], 'processes': []}

    def context(self, **kwargs):
        ctx = engine.RenderContext(scratch_parent=str(self.root), **kwargs)
        self.contexts.append(ctx)
        return ctx

    def tearDown(self):
        for ctx in self.contexts:
            root = ctx.root
            ctx.close()
            self.receipt['scratch'].append({'path': root, 'absent': root is None or not Path(root).exists(),
                'holder_has_process': 'proc' in ctx.holder, 'diagnostics': ctx.holder.get('scratch_diagnostics', [])})
            self.assertTrue(root is None or not Path(root).exists())
            self.assertNotIn('proc', ctx.holder)
        self.receipt['files'] = {str(p.relative_to(self.root)): hashlib.sha256(p.read_bytes()).hexdigest()
                                for p in self.root.iterdir() if p.is_file()}
        (self.root / 'evidence.json').write_text(json.dumps(self.receipt, indent=2), encoding='utf-8')

    def source(self, duration=.5):
        source = self.root / "Émile's source.mp4"
        subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'lavfi', '-i',
                        f'color=c=red:s=64x48:r=24:d={duration}', '-c:v', 'libx264', str(source)], check=True)
        return source

    def decode(self, path):
        data = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-f', 'rawvideo',
                               '-pix_fmt', 'rgb24', 'pipe:1'], capture_output=True, check=True).stdout
        return data

    def test_import_has_no_global_directory(self):
        before = set(RUN.glob('filmocity-*'))
        subprocess.run([sys.executable, '-c', "import sys; sys.path.insert(0, 'backend'); import render; assert not hasattr(render, '_TMP')"],
                       cwd=ROOT, check=True)
        self.assertEqual(set(RUN.glob('filmocity-*')), before)

    def test_generated_text_preserves_lf_unicode_and_blank_lines(self):
        ctx = self.context()
        with ctx:
            text = 'Apple\n\ngig Émile\n'
            path = ctx.write_text(text)
            self.assertEqual(Path(path).read_bytes(), text.encode('utf-8'))

    def test_direct_helpers_require_context_and_are_unique_complete_files(self):
        with self.assertRaises(TypeError): engine.textfile('direct')
        ctx = self.context()
        with ctx:
            paths = [engine.shape_png({'color': 'red'}, 64, 48, context=ctx) for _ in range(2)]
            paths += [engine.mask_png({'type': 'ellipse'}, 64, 48, context=ctx),
                      engine.opacity_cmds([{'t': 0, 'v': 1}], .5, 24, context=ctx)]
            self.assertEqual(len(paths), len(set(paths)))
            for path in paths: self.assertGreater(Path(path).stat().st_size, 0)
            with Image.open(paths[0]) as im: self.assertEqual(im.size, (64, 48))
            option = engine.textfile('42%: Émile', context=ctx)
            self.assertIn('textfile=', option)
            self.assertIn('42%: Émile', ''.join(p.read_text(encoding='utf-8') for p in Path(ctx.root).glob('*.txt')))

    def test_returned_command_keeps_files_until_explicit_close_and_runs_later(self):
        out = self.root / 'inspection.mp4'
        cmd, _ = engine.build_command(project(title=True), 's', str(out))
        ctx = cmd.context; self.contexts.append(ctx)
        root = Path(ctx.root)
        self.assertTrue(root.is_dir())
        self.assertTrue(cmd.owns_context)
        subprocess.run(cmd, capture_output=True, check=True)
        self.assertTrue(root.is_dir())
        self.assertEqual(len(self.decode(out)) // (64*48*3), 12)
        cmd.close(); cmd.close()
        self.assertFalse(root.exists())
        with self.assertRaisesRegex(RuntimeError, 'closed'): engine._run_ffmpeg(cmd)

    def test_borrowed_command_close_leaves_explicit_scope_alive(self):
        ctx = self.context()
        with ctx:
            cmd, _ = engine.build_command(project(title=True), 's', str(self.root / 'borrowed.mp4'), context=ctx)
            cmd.close()
            self.assertTrue(Path(ctx.root).is_dir())
            engine._run_ffmpeg(cmd)

    def test_derived_command_retains_lifetime_after_original_is_dropped(self):
        import gc
        cmd, _ = engine.build_command(project(title=True), 's', str(self.root / 'original.mp4'))
        derived = cmd[:-1] + [str(self.root / 'derived.mp4')]
        self.contexts.append(derived.context); root = Path(derived.context.root)
        del cmd; gc.collect()
        self.assertTrue(root.exists())
        with derived: subprocess.run(derived, capture_output=True, check=True)
        self.assertFalse(root.exists())
        self.assertEqual(len(self.decode(self.root / 'derived.mp4')) // (64*48*3), 12)

    def test_concurrent_builders_and_external_encoders_have_disjoint_scratch(self):
        barrier = threading.Barrier(4)
        def run(i):
            out = self.root / f'command-{i}.mp4'
            cmd, graph = engine.build_command(project(title=True), 's', str(out))
            root = Path(cmd.context.root)
            with cmd:
                barrier.wait(timeout=10)
                subprocess.run(cmd, check=True, capture_output=True)
                self.assertTrue(root.exists())
            self.assertFalse(root.exists())
            return str(root), graph
        with ThreadPoolExecutor(max_workers=4) as pool: results = list(pool.map(run, range(4)))
        self.assertEqual(len({r[0] for r in results}), 4)
        for i in range(4): self.assertEqual(len(self.decode(self.root / f'command-{i}.mp4')) // (64*48*3), 12)
        self.receipt['command_roots'] = [r[0] for r in results]

    def test_real_nested_success_reuses_only_completed_record_and_preserves_json(self):
        p = nested_project(); original = copy.deepcopy(p)
        p['sequences'][0]['tracks'][0]['clips'].append(dict(p['sequences'][0]['tracks'][0]['clips'][0], id='duplicate'))
        original = copy.deepcopy(p)
        ctx = self.context(); run = engine._run_ffmpeg; nested_calls = []
        def observe(cmd, **kwargs):
            if ctx.holder.get('phase') == 'nested': nested_calls.append(cmd[-1])
            return run(cmd, **kwargs)
        with ctx, patch.object(engine, '_run_ffmpeg', side_effect=observe):
            out = self.root / 'nested.mp4'
            engine.render(p, 's', str(out), context=ctx)
            self.assertEqual(len(nested_calls), 1)
            self.assertEqual(len(ctx.nested_completed), 1)
            self.assertTrue(all(r['completed'] is True for r in ctx.nested_completed.values()))
            self.assertEqual(len(self.decode(out)) // (64*48*3), 12)
            self.assertGreater(self.decode(out)[0], 200)
        self.assertEqual(p, original)

    def test_partial_nested_output_is_removed_and_retry_reencodes_same_context(self):
        ctx = self.context(); run = engine._run_ffmpeg; attempts = []
        def fail_once(cmd, **kwargs):
            attempts.append(cmd[-1])
            if len(attempts) == 1:
                Path(cmd[-1]).write_bytes(b'partial nested output')
                raise RuntimeError('injected nested encoder failure')
            return run(cmd, **kwargs)
        with ctx, patch.object(engine, '_run_ffmpeg', side_effect=fail_once):
            with self.assertRaisesRegex(RuntimeError, 'nested render failed'):
                engine.build_command(nested_project(), 's', str(self.root / 'out.mp4'), context=ctx)
            self.assertFalse(Path(attempts[0]).exists())
            self.assertEqual(ctx.nested_completed, {})
            cmd, _ = engine.build_command(nested_project(), 's', str(self.root / 'out.mp4'), context=ctx)
            self.assertEqual(len(attempts), 2)
            self.assertNotEqual(attempts[0], attempts[1])
            self.assertTrue(Path(attempts[1]).is_file())

    def test_real_nested_ffmpeg_failure_preserves_master_and_retires_scratch(self):
        bad = self.root / 'bad.mp4'; bad.write_bytes(b'nonempty invalid container')
        out = self.root / 'master.mp4'; out.write_bytes(b'previous master')
        ctx = self.context()
        with self.assertRaisesRegex(RuntimeError, 'nested render failed'):
            with ctx: engine.render(nested_project(source=bad), 's', str(out), context=ctx)
        self.assertEqual(out.read_bytes(), b'previous master')
        self.assertEqual(ctx.nested_completed, {})
        self.assertFalse(list(Path(ctx.root).glob('*.mp4')) if Path(ctx.root).exists() else [])

    def test_real_nested_stall_is_supervised_and_scratch_retires(self):
        ctx = self.context(stall_timeout=.02)
        with self.assertRaisesRegex(RuntimeError, 'nested render failed.*stalled'), ctx, paced_nested_encode():
            engine.build_command(nested_project(120, self.source()), 's', str(self.root / 'stalled.mp4'), context=ctx)
        self.assertIn('stalled', ctx.holder['failure'])
        self.assertEqual(ctx.nested_completed, {})

    def test_supervisor_thread_start_failure_reaps_actual_encoder(self):
        ctx = self.context()
        cmd, _ = engine.build_command(project(title=True), 's', str(self.root / 'thread-failed.mp4'), context=ctx)
        spawn = engine.subprocess.Popen; processes = []
        def observe(*args, **kwargs):
            proc = spawn(*args, **kwargs); processes.append(proc); return proc
        with patch.object(engine.subprocess, 'Popen', side_effect=observe), \
             patch.object(threading.Thread, 'start', side_effect=RuntimeError('thread start failure')):
            with self.assertRaisesRegex(RuntimeError, 'thread start failure'):
                engine._run_ffmpeg(cmd, context=ctx)
        self.assertEqual(len(processes), 1)
        self.assertIsNotNone(processes[0].poll()); self.assertNotIn('proc', ctx.holder)
        self.receipt['processes'] = [{'pid': proc.pid, 'exit': proc.poll()} for proc in processes]

    def test_real_queue_nested_cancellation_retires_then_successor_finishes(self):
        class Stop(Exception): pass
        class BoundedQueue(queue.Queue):
            def get(self, *args, **kwargs):
                if self.empty(): raise Stop()
                return super().get(*args, **kwargs)
        jobs = {}; holders = {}; q = BoundedQueue(); events = []
        contexts = self.contexts; allocation = engine.RenderContext.new_file
        def observe(ctx, suffix):
            if ctx not in contexts: contexts.append(ctx)
            return allocation(ctx, suffix)
        def worker():
            try: server._render_worker()
            except Stop: pass
        with patch.object(server, 'ROOT', str(self.root)), patch.object(server, 'JOBS', jobs), \
             patch.object(server, 'RENDER_PROCS', holders), patch.object(server, 'RENDER_Q', q), \
             patch.object(server, 'render_workers'), patch.object(server, 'log_event', side_effect=lambda event: events.append(event)), \
             patch.object(engine.RenderContext, 'new_file', observe), paced_nested_encode():
            first = server.start_render(nested_project(120, self.source()), 's', {}, 'cancelled', 'fixture')
            second = server.start_render(project(title=True), 's', {'x264_preset': 'ultrafast'}, 'second', 'fixture')
            t = threading.Thread(target=worker); t.start()
            try:
                deadline = time.monotonic() + 8
                while not holders.get(first['id'], {}).get('proc') and t.is_alive() and time.monotonic() < deadline: time.sleep(.01)
                self.assertIn('proc', holders[first['id']])
                self.assertEqual(asyncio.run(server.render_cancel(first['id'])), {'ok': True})
            finally:
                if first['id'] in holders: holders[first['id']]['cancelled'] = True
                t.join(timeout=10)
            self.assertFalse(t.is_alive()); self.assertEqual(first['status'], 'error')
            self.assertIn('cancelled', first['error']); self.assertEqual(second['status'], 'done')
            self.assertIn(second['qa']['status'], ('checked', 'warnings'))
            self.assertNotIn('error', second['qa'])
            output = self.root / second['out'].lstrip('/')
            self.assertTrue(output.resolve().is_relative_to(self.root.resolve()))
            self.assertEqual(second['qa']['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
            self.assertEqual(q.unfinished_tasks, 0)
            self.assertEqual(holders, {}); self.assertEqual(len(events), 2)
            self.assertTrue(contexts); self.assertTrue(all(not Path(ctx.root).exists() for ctx in contexts if ctx.root))
            self.receipt['jobs'] = jobs

    def test_real_cli_nested_success_has_owned_lifetime(self):
        source = self.root / 'source.json'; source.write_text(json.dumps(nested_project()), encoding='utf-8')
        previous = source.read_bytes(); out = self.root / 'cli.mp4'
        before = set(RUN.glob('filmocity-render-*'))
        result = subprocess.run([sys.executable, str(ROOT / 'backend/render.py'), str(source), 's', str(out)],
                                capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(source.read_bytes(), previous)
        self.assertEqual(len(self.decode(out)) // (64*48*3), 12)
        self.assertEqual(set(RUN.glob('filmocity-render-*')), before)

    def test_failed_owned_builder_retires_generated_helpers(self):
        roots = []; original = engine.textfile
        def broken(*args, **kwargs):
            value = original(*args, **kwargs)
            roots.append(kwargs['context'].root)
            raise RuntimeError('after helper build failure')
        with patch.object(engine, 'textfile', side_effect=broken):
            with self.assertRaisesRegex(RuntimeError, 'after helper build failure'):
                engine.build_command(project(title=True), 's', str(self.root / 'not-built.mp4'))
        self.assertEqual(len(roots), 1); self.assertFalse(Path(roots[0]).exists())

    def test_queued_cancellation_never_allocates_context(self):
        job = {'status': 'queued', 'preset': {}}
        with patch.object(server, 'JOBS', {'queued': job}), patch.object(server, 'RENDER_PROCS', {}), \
             patch.object(engine.RenderContext, 'new_file') as allocation:
            self.assertEqual(asyncio.run(server.render_cancel('queued')), {'ok': True})
            self.assertEqual(job['status'], 'error'); allocation.assert_not_called()

    def test_external_command_block_is_a_consumer_until_exit(self):
        cmd, _ = engine.build_command(project(title=True), 's', str(self.root / 'external.mp4'))
        ctx = cmd.context; self.contexts.append(ctx)
        entered = threading.Event(); release = threading.Event(); retired = threading.Event()
        def external():
            with cmd:
                entered.set(); release.wait(5)
                subprocess.run(cmd, check=True, capture_output=True)
        user = threading.Thread(target=external); user.start()
        self.assertTrue(entered.wait(5))
        closer = threading.Thread(target=lambda: (ctx.close(), retired.set())); closer.start()
        try:
            self.assertFalse(retired.wait(.1)); self.assertTrue(Path(ctx.root).exists())
        finally:
            release.set(); user.join(8); closer.join(8)
        self.assertFalse(user.is_alive()); self.assertFalse(closer.is_alive()); self.assertTrue(retired.is_set())
        self.assertFalse(Path(ctx.root).exists())

    def test_exclusive_helper_reservation_never_overwrites_collision(self):
        ctx = self.context(); ctx.write_text('initialize')
        a, b = uuid.UUID(int=0), uuid.UUID(int=1 << 100)
        with patch.object(render_context.uuid, 'uuid4', side_effect=[a, a, b]):
            first = ctx.write_text('first')
            second = ctx.write_text('second')
        self.assertNotEqual(first, second)
        self.assertEqual(Path(first).read_text(), 'first'); self.assertEqual(Path(second).read_text(), 'second')

    def test_cleanup_failure_in_queue_is_error_with_diagnostic_and_successor_runs(self):
        class Stop(Exception): pass
        class Q(queue.Queue):
            def get(self, *args, **kwargs):
                if self.empty(): raise Stop()
                return super().get(*args, **kwargs)
        jobs = {}; holders = {}; q = Q(); roots = []; contexts = self.contexts
        allocate = engine.RenderContext.new_file; remove = render_context.shutil.rmtree
        def observe(ctx, suffix):
            if ctx not in contexts: contexts.append(ctx)
            path = allocate(ctx, suffix)
            if ctx.root not in roots: roots.append(ctx.root)
            return path
        def denied(path, *args, **kwargs):
            if roots and str(path) == roots[0]: raise PermissionError('injected retirement sharing denial')
            return remove(path, *args, **kwargs)
        with patch.object(server, 'ROOT', str(self.root)), patch.object(server, 'JOBS', jobs), \
             patch.object(server, 'RENDER_PROCS', holders), patch.object(server, 'RENDER_Q', q), \
             patch.object(server, 'render_workers'), patch.object(server, 'log_event'), \
             patch.object(engine.RenderContext, 'new_file', observe), patch.object(render_context.shutil, 'rmtree', side_effect=denied):
            first = server.start_render(project(title=True), 's', {'x264_preset': 'ultrafast'}, 'denied', 'fixture')
            second = server.start_render(project(title=True), 's', {'x264_preset': 'ultrafast'}, 'successor', 'fixture')
            try: server._render_worker()
            except Stop: pass
        self.assertEqual(first['status'], 'error'); self.assertIn('cleanup failed', first['error'])
        self.assertEqual(first['diagnostics'][0]['phase'], 'scratch_retirement')
        self.assertTrue(Path(roots[0]).exists()); self.assertFalse(Path(roots[1]).exists())
        self.assertEqual(second['status'], 'done'); self.assertEqual(holders, {}); self.assertEqual(q.unfinished_tasks, 0)
        output = self.root / first['out'].lstrip('/')
        self.assertTrue(output.resolve().is_relative_to(self.root.resolve()))
        self.assertTrue(output.exists())
        self.receipt['jobs'] = jobs

    def test_command_api_cleanup_failure_keeps_release_scope_for_retry(self):
        with patch.object(server, 'load_project', return_value=project(title=True)):
            result = server.render_command('s')
        scope = result['scope']['id']; command = server.COMMAND_CONTEXTS[scope]
        self.contexts.append(command.context)
        with patch.object(render_context.shutil, 'rmtree', side_effect=PermissionError('denied')):
            with self.assertRaises(server.HTTPException) as failure: server.release_render_command(scope)
        self.assertEqual(failure.exception.status_code, 500); self.assertIn(scope, server.COMMAND_CONTEXTS)
        self.assertTrue(Path(command.context.root).exists())
        self.assertEqual(server.release_render_command(scope), {'ok': True})

    def test_real_nested_cancel_through_server_during_build_retires_all_consumers(self):
        self.cancel_nested(frame=False)

    def test_real_nested_frame_cancel_retires_all_consumers(self):
        self.cancel_nested(frame=True)

    def cancel_nested(self, frame):
        holder = {}; ctx = self.context(proc_holder=holder)
        p = nested_project(120, self.source())
        out = self.root / ('frame.png' if frame else 'master.mp4'); out.write_bytes(b'previous')
        errors = []; child = []
        def work():
            try:
                with ctx, paced_nested_encode():
                    if frame: engine.render_frame(p, 's', .1, str(out), context=ctx)
                    else: engine.render(p, 's', str(out), context=ctx)
            except Exception as error: errors.append(str(error))
        t = threading.Thread(target=work); t.start()
        try:
            deadline = time.monotonic() + 8
            while 'proc' not in holder and t.is_alive() and time.monotonic() < deadline: time.sleep(.01)
            self.assertIn('proc', holder, errors)
            child.append(holder['proc']); self.assertEqual(holder['phase'], 'nested')
            with patch.object(server, 'JOBS', {'nested-cancel': {'status': 'running', 'preset': {}}}), \
                 patch.object(server, 'RENDER_PROCS', {'nested-cancel': holder}):
                self.assertEqual(asyncio.run(server.render_cancel('nested-cancel')), {'ok': True})
        finally:
            holder['cancelled'] = True; t.join(timeout=8)
        self.assertFalse(t.is_alive())
        self.assertTrue(errors and 'cancelled' in errors[0], errors)
        self.assertEqual(out.read_bytes(), b'previous')
        self.assertTrue(all(proc.poll() is not None for proc in child))
        self.assertFalse(Path(ctx.root).exists())
        self.receipt['processes'] = [{'pid': proc.pid, 'exit': proc.poll()} for proc in child]
        self.assertFalse(list(self.root.glob('*.part.*')))

    def test_build_cancellation_without_encoder_is_accepted(self):
        holder = {}; ctx = self.context(proc_holder=holder)
        real = engine._build_command
        def cancel_build(*args, **kwargs):
            ctx.write_text('building')
            with patch.object(server, 'JOBS', {'build': {'status': 'running', 'preset': {}}}), \
                 patch.object(server, 'RENDER_PROCS', {'build': holder}):
                self.assertEqual(asyncio.run(server.render_cancel('build')), {'ok': True})
            return real(*args, **kwargs)
        with self.assertRaisesRegex(RuntimeError, 'cancelled'), ctx, \
             patch.object(engine, '_build_command', side_effect=cancel_build), patch.object(engine.subprocess, 'Popen') as spawn:
            engine.render(project(), 's', str(self.root / 'out.mp4'), context=ctx)
        spawn.assert_not_called()

    def test_pre_cancelled_entry_points_allocate_nothing_and_spawn_nothing(self):
        for function, args in ((engine.render, ('x.mp4',)), (engine.render_incremental, ('x.mp4',)),
                               (engine.render_frame, (0, 'x.png'))):
            with self.subTest(function=function.__name__), patch.object(engine.subprocess, 'Popen') as spawn:
                with self.assertRaisesRegex(RuntimeError, 'cancelled'):
                    function(project(title=True), 's', *args, proc_holder={'cancelled': True})
                spawn.assert_not_called()
        self.assertFalse(list(RUN.glob('filmocity-render-*')))

    def test_scope_retirement_waits_for_real_encoder_and_pipe_readers(self):
        ctx = self.context(); cmd, _ = engine.build_command(nested_project(2, self.source(duration=2)), 'child',
            str(self.root / 'paced.mp4'), context=ctx)
        cmd[cmd.index('-i'):cmd.index('-i')] = ['-readrate', '1']
        # child, rather than its parent, keeps the actual encode running in this scope.
        errors = []
        def encode():
            try: engine._run_ffmpeg(cmd, context=ctx)
            except Exception as error: errors.append(error)
        worker = threading.Thread(target=encode); worker.start()
        deadline = time.monotonic() + 5
        while 'proc' not in ctx.holder and time.monotonic() < deadline: time.sleep(.01)
        self.assertIn('proc', ctx.holder)
        proc = ctx.holder['proc']
        scratch = ctx.write_text('must stay alive')
        retired = threading.Event()
        closer = threading.Thread(target=lambda: (ctx.close(), retired.set())); closer.start()
        try:
            self.assertFalse(retired.wait(.1)); self.assertTrue(Path(scratch).exists())
        finally:
            ctx.holder['cancelled'] = True; worker.join(8); closer.join(8)
        self.assertFalse(worker.is_alive()); self.assertFalse(closer.is_alive())
        self.assertTrue(retired.is_set()); self.assertIsNotNone(proc.poll())
        self.assertNotIn('proc', ctx.holder); self.assertFalse(Path(scratch).exists())
        self.assertTrue(errors and 'cancelled' in str(errors[0]))

    def test_cleanup_denial_reports_retained_path_and_retry(self):
        ctx = self.context(); path = ctx.write_text('owned')
        with patch.object(render_context.shutil, 'rmtree', side_effect=PermissionError('sharing denial')):
            with self.assertRaisesRegex(engine.RenderCleanupError, 'retained at'):
                ctx.close()
        self.assertTrue(Path(path).exists())
        self.assertEqual(ctx.holder['scratch_diagnostics'][0]['path'], ctx.root)
        ctx.close(); self.assertFalse(Path(path).exists())

    def test_cleanup_denial_preserves_primary_error_in_diagnostic(self):
        ctx = self.context(); ctx.write_text('owned')
        with patch.object(render_context.shutil, 'rmtree', side_effect=PermissionError('sharing denial')):
            with self.assertRaisesRegex(engine.RenderCleanupError, 'encoder failure.*cleanup failed'):
                with ctx: raise RuntimeError('encoder failure')
        ctx.close()

    def test_multicam_angles_nested_depth_and_frame_success(self):
        p = nested_project(); child = p['sequences'][1]; child['multicam'] = True
        p['media']['blue'] = {'id': 'blue', 'synthetic': {'kind': 'color', 'color': 'blue'}, 'has_audio': False}
        child['tracks'].append({'id': 'V2', 'kind': 'video', 'index': 2, 'clips': [
            dict(child['tracks'][0]['clips'][0], media_id='blue', id='blueclip')]})
        p['sequences'][0]['tracks'][0]['clips'][0]['multicam_angle'] = 1
        top = copy.deepcopy(p['sequences'][0]); top['id'] = 'top'; top['tracks'][0]['clips'][0]['sequence_id'] = 's'
        top['tracks'][0]['clips'][0].pop('multicam_angle')
        p['sequences'].append(top); original = copy.deepcopy(p)
        ctx = self.context()
        with ctx:
            out = self.root / 'multicam.png'; engine.render_frame(p, 'top', .1, str(out), context=ctx)
            with Image.open(out) as im:
                r, g, b = im.convert('RGB').getpixel((32, 24)); self.assertGreater(b, 200); self.assertLess(r, 20)
            self.assertEqual(len(ctx.nested_completed), 2)
        self.assertEqual(p, original)

    def test_real_incremental_reuse_and_nested_scratch_retirement(self):
        p = nested_project(); cache = self.root / 'cache'; preset = {'x264_preset': 'ultrafast', 'chapters': True}
        p['sequences'][0]['markers'] = [{'time': 0, 'name': 'Start', 'type': 'chapter'}]
        for i in range(2):
            ctx = self.context()
            with ctx:
                out = self.root / f'incremental-{i}.mp4'
                _, stats = engine.render_incremental(p, 's', str(out), preset, cache_dir=str(cache), context=ctx)
                self.assertEqual(stats['reused'], i)
                self.assertEqual(len(self.decode(out)) // (64*48*3), 12)
        self.assertEqual(len(list(cache.glob('*.mp4'))), 1)
        self.assertFalse(list(cache.glob('*.part.mp4')))

    def test_command_inspection_api_retains_scope_until_release(self):
        pinned = self.root / 'É pinned font.ttf'; shutil.copyfile(engine.bundled_font(), pinned)
        document = project(title=True); document['sequences'][0]['tracks'][0]['clips'][0]['title']['font'] = str(pinned)
        with patch.object(server, 'load_project', return_value=document):
            result = server.render_command('s')
        scope = result['scope']['id']; command = server.COMMAND_CONTEXTS[scope]
        self.contexts.append(command.context); root = Path(command.context.root)
        self.assertTrue(root.exists()); self.assertEqual(result['cmd'], command)
        self.assertEqual(result['cwd'], command.cwd)
        if os.name == 'nt': self.assertEqual(result['cwd'], str(root))
        serialized = json.loads(json.dumps(result))
        options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
        subprocess.run(serialized['cmd'], cwd=serialized['cwd'], capture_output=True, check=True, timeout=15, **options)
        self.assertTrue(root.exists())
        self.assertEqual(len(self.decode(Path(command[-1]))) // (64*48*3), 12)
        self.assertEqual(server.release_render_command(scope), {'ok': True})
        self.assertFalse(root.exists()); self.assertEqual(server.release_render_command(scope), {'ok': False})

    def test_async_request_cancellation_joins_real_nested_worker(self):
        p = nested_project(120, self.source()); holder = {}
        out = self.root / 'request.mp4'
        async def request():
            task = asyncio.create_task(server._owned_render_thread(engine.render, p, 's', str(out), proc_holder=holder))
            deadline = time.monotonic() + 8
            while 'proc' not in holder and not task.done() and time.monotonic() < deadline: await asyncio.sleep(.01)
            self.assertIn('proc', holder)
            proc = holder['proc']; task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            self.assertIsNotNone(proc.poll()); self.assertNotIn('proc', holder)
            self.receipt['processes'].append({'pid': proc.pid, 'exit': proc.poll()})
        with paced_nested_encode():
            asyncio.run(request())
        self.assertFalse(out.exists()); self.assertFalse(list(RUN.glob('filmocity-render-*')))

    def test_transcription_private_wav_lives_through_lazy_readers(self):
        paths = []
        class Model:
            def __init__(self, *args, **kwargs): pass
            def transcribe(self, wav, **kwargs):
                paths.append(wav)
                def segments():
                    self_test.assertTrue(Path(wav).is_file())
                    yield types.SimpleNamespace(start=0, end=.5, text='words', words=[])
                    self_test.assertTrue(Path(wav).is_file())
                return segments(), None
        self_test = self
        with patch.dict(sys.modules, {'faster_whisper': types.SimpleNamespace(WhisperModel=Model)}):
            words, caps = server._transcribe_sequence(project(), 's', 'fixture', word_timestamps=True)
        self.assertEqual(caps[0]['text'], 'words'); self.assertFalse(Path(paths[0]).exists())


if __name__ == '__main__': unittest.main(verbosity=2)
