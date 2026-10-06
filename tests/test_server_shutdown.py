"""Actual server drain functions, owned queues and project-copy executor handles.

Framework/store wrappers and long-running tasks are controlled. Native listener
retirement is covered separately by test_native_shutdown.py.
"""
import ast
import asyncio
from pathlib import Path
import queue
import threading
import time
import unittest
from unittest.mock import patch

import test_project_sync as store
import project_lifecycle
from test_media_collection import run_async_check


class ServerShutdown(store.ProjectStoreFixture):
    for _name in dir(store.ProjectStoreFixture):
        if _name.startswith('test_'): locals()[_name] = None

    def setUp(self):
        super().setUp()
        self.copy = project_lifecycle.ActionWorker()
        self.addCleanup(self.copy.shutdown)
        self.stop = threading.Event()
        self.env.update(threading=threading, asyncio=asyncio, _queue=queue, SHUTDOWN=self.stop, RENDER_STATE_LOCK=threading.RLock(),
            RENDER_Q=queue.Queue(), RENDER_PROCS={}, RENDER_WORKERS=[], JOBS={}, TASKS=None)
        tree = ast.parse((store.ROOT / 'backend/server.py').read_text(encoding='utf-8'))
        names = {'_shutdown_workers', 'shutdown_background_tasks', '_autosave_loop', 'render_workers'}
        nodes = [n for n in tree.body if getattr(n, 'name', None) in names]
        self.assertEqual({n.name for n in nodes}, names)
        for node in nodes: node.decorator_list = []
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), self.env)
        self.env['autosave_tick'] = lambda: self.fail('Shutdown must not start another autosave')
        autosave = threading.Thread(target=self.env['_autosave_loop'], name='owned autosave')
        self.env['AUTOSAVE_THREAD'] = autosave; autosave.start()
        self.addCleanup(lambda: (self.stop.set(), autosave.join(2)))

    def drain(self, timeout=2):
        with patch.object(project_lifecycle, 'COPY_WORKER', self.copy):
            return self.env['_shutdown_workers'](timeout)

    def test_idle_queue_workers_and_autosave_retire_and_admission_refuses(self):
        workers = []
        def waiting():
            self.assertIsNone(self.env['RENDER_Q'].get()); self.env['RENDER_Q'].task_done()
        for i in range(2):
            worker = threading.Thread(target=waiting, name=f'owned render {i}'); worker.start(); workers.append(worker)
        self.env['RENDER_WORKERS'] = workers
        self.assertTrue(self.drain()['ok'])
        self.assertTrue(all(not w.is_alive() for w in workers))
        self.assertEqual(self.env['RENDER_Q'].unfinished_tasks, 0)
        with self.assertRaises(store.HTTPError): self.env['render_workers']()

    def test_queued_receipts_stop_and_active_holder_is_cancelled_before_join(self):
        holder = {}; self.env['RENDER_PROCS']['active'] = holder
        self.env['JOBS']['queued'] = {'id': 'queued', 'status': 'queued'}
        self.env['RENDER_Q'].put(('queued',))
        def active():
            while not holder.get('cancelled'): time.sleep(.001)
            self.assertIsNone(self.env['RENDER_Q'].get()); self.env['RENDER_Q'].task_done()
        worker = threading.Thread(target=active, name='owned active render'); worker.start()
        self.env['RENDER_WORKERS'] = [worker]
        with patch('job_history.remember') as remember:
            self.assertTrue(self.drain()['ok']); self.assertEqual(remember.call_count, 1)
        self.assertTrue(holder['cancelled']); self.assertFalse(worker.is_alive())
        self.assertEqual(self.env['JOBS']['queued']['status'], 'error')
        self.assertEqual(self.env['RENDER_Q'].unfinished_tasks, 0)

    def test_copy_executor_cancellation_is_joined_to_real_owned_non_daemon_thread(self):
        started = threading.Event()
        async def scenario():
            def copy(check):
                started.set()
                while True: check(); time.sleep(.005)
            pending = asyncio.create_task(self.copy.run(copy))
            while not started.is_set(): await asyncio.sleep(.001)
            with patch.object(project_lifecycle, 'COPY_WORKER', self.copy):
                reply = await self.env['shutdown_background_tasks']()
            self.assertTrue(reply['ok'])
            with self.assertRaises(project_lifecycle.ProjectActionError): await pending
        run_async_check(scenario())
        self.assertTrue(all(not t.is_alive() for t in self.copy.executor._threads))

    def test_one_shared_budget_and_explicit_failure_for_unretired_worker(self):
        release = threading.Event()
        worker = threading.Thread(target=release.wait, name='owned deliberately uncooperative render')
        worker.start(); self.env['RENDER_WORKERS'] = [worker]
        self.addCleanup(lambda: (release.set(), worker.join(2)))
        class Tasks:
            def shutdown(self, timeout):
                time.sleep(min(.08, timeout)); return False
        self.env['TASKS'] = Tasks(); started = time.monotonic()
        with self.assertRaisesRegex(RuntimeError, 'still stopping'): self.drain(.1)
        self.assertLess(time.monotonic()-started, .5)
        self.assertTrue(worker.is_alive())


if __name__ == '__main__': unittest.main()
