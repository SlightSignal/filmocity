"""Real process ownership and mount ordering; no listener or application library.

The native adapter is exercised on the host OS. Windows adapter fixtures on
Linux check only the API contract, not Windows locking or filesystem behavior.
"""
import errno
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
import workspace_lock as lock


def windows_creation(handle):
    """Read creation identity from an already-owned native process handle."""
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.GetProcessTimes.restype = wintypes.BOOL
    times = [wintypes.FILETIME() for _ in range(4)]
    if not kernel.GetProcessTimes(handle, *(ctypes.byref(value) for value in times)):
        raise ctypes.WinError(ctypes.get_last_error())
    return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime


CHILD = '''
import ast, atexit, json, os, shutil, sys, queue, threading
from pathlib import Path
from types import SimpleNamespace
if os.name == 'nt':
    # Keep the admitted test environment's dependencies while starting its
    # real interpreter directly instead of the Windows venv redirector.
    import site
    site.addsitedir(sys.argv[4])
sys.path.insert(0, str(Path(sys.argv[1]) / 'backend'))
from workspace_lock import hold_workspace, WorkspaceInUse, WorkspaceLockError
from background_tasks import TaskManager
root, mode = sys.argv[2], sys.argv[3]
def answer(message):
    message['pid'] = os.getpid()
    if os.name == 'nt':
        import ctypes
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
        kernel.GetProcessTimes.restype = wintypes.BOOL
        times = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(kernel.GetCurrentProcess(), *(ctypes.byref(value) for value in times)):
            raise ctypes.WinError(ctypes.get_last_error())
        message['creationFiletime'] = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    print(json.dumps(message), flush=True)
if mode == 'race':
    answer({'status': 'ready'})
    sys.stdin.readline()
try:
    if mode == 'mount':
        source = ast.parse((Path(sys.argv[1]) / 'backend/server.py').read_text(encoding='utf-8'))
        nodes = [node for node in source.body if isinstance(node, ast.FunctionDef) and node.name in ('P', 'migrate_legacy', 'restore_render_history', 'mount')]
        env = dict(ROOT=root, os=os, shutil=shutil, hold_workspace=hold_workspace,
                   TaskManager=TaskManager, _task_cover_workflow=lambda *a: None, _task_recipe_workflow=lambda *a: None, _task_audio_workflow=lambda *a: None, _task_audio_sync=lambda *a: None, _task_render_replace=lambda *a: None, _task_media_analysis=lambda *a: None, _task_prepare_media=lambda *a: None, _task_transcribe=lambda *a: None, _task_package=lambda *a: None, _task_collect=lambda *a: None,
                   JOBS={}, RENDER_WORKERS=[], RENDER_Q=queue.Queue(), RENDER_STATE_LOCK=threading.RLock(),
                   FRONT=str(Path(sys.argv[1]) / 'frontend'), ASSETS='', DOCS='',
                   app=SimpleNamespace(mount=lambda *a, **k: None), StaticFiles=lambda **kw: kw)
        exec(compile(ast.Module(body=nodes, type_ignores=[]), 'backend/server.py', 'exec'), env)
        env['mount']()
    else:
        lease = hold_workspace(root)
    answer({'status': 'owned', 'root': env['ROOT'] if mode == 'mount' else lease.root,
                      'render_root': os.environ.get('FILMOCITY_ROOT'),
                      'history_jobs': list(env['JOBS']) if mode == 'mount' else []})
except WorkspaceInUse as error:
    answer({'status': 'busy', 'message': str(error)})
    sys.exit(42)
except WorkspaceLockError as error:
    answer({'status': 'error', 'message': str(error)})
    sys.exit(43)
if mode in ('hold', 'race'):
    command = sys.stdin.readline().strip()
    if command == 'crash': os._exit(73)
if mode == 'exit_pause':
    def pause_during_shutdown():
        answer({'status': 'shutting_down'})
        sys.stdin.readline()
    atexit.register(pause_during_shutdown)
'''


class WorkspaceLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity ownership É's ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'data'
        self.root.mkdir()
        self.project = self.root / 'project.json'
        self.project.write_bytes(b'{"version":1,"name":"Legacy project"}')
        self.history = self.root / 'undo_stack.json'
        self.history.write_bytes(b'{"undo":[],"redo":[]}')

    def command(self, mode, root=None):
        # A Windows venv executable is a redirector. Its real Python child holds
        # the lock, so killing/waiting the redirector is not holder retirement.
        interpreter = sys._base_executable if os.name == 'nt' else sys.executable
        command = [interpreter, '-u', '-c', CHILD, str(ROOT), str(root or self.root), mode]
        if os.name == 'nt':
            command.append(str(Path(sys.prefix) / 'Lib' / 'site-packages'))
        return command

    def start(self, mode='hold', root=None):
        proc = subprocess.Popen(self.command(mode, root), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True, encoding='utf-8', env=dict(os.environ, PYTHONUTF8='1'))
        self.addCleanup(self.stop, proc)
        if os.name == 'nt':
            proc._filmocity_test_creation = windows_creation(int(proc._handle))
        return proc

    def stop(self, proc):
        try:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            self.assertIsNotNone(proc.returncode)
            if os.name == 'nt':
                import ctypes
                from ctypes import wintypes
                self.assertEqual(windows_creation(int(proc._handle)), proc._filmocity_test_creation)
                kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
                kernel.WaitForSingleObject.restype = wintypes.DWORD
                self.assertEqual(kernel.WaitForSingleObject(int(proc._handle), 0), 0)
        finally:
            for stream in (proc.stdin, proc.stdout, proc.stderr):
                stream.close()

    def line(self, proc):
        output = queue.Queue()
        threading.Thread(target=lambda: output.put(proc.stdout.readline()), daemon=True).start()
        try:
            raw = output.get(timeout=10)
        except queue.Empty:
            self.fail('Child did not answer within the process-test deadline')
        if not raw:
            proc.wait(timeout=10)
            self.fail(f'Child exited {proc.returncode}: {proc.stderr.read()}')
        reply = json.loads(raw)
        self.assertEqual(reply['pid'], proc.pid, 'Lock holder must be the owned Popen child')
        if os.name == 'nt':
            self.assertEqual(reply['creationFiletime'], proc._filmocity_test_creation)
        return reply

    def probe(self, root=None, mode='once', cwd=None):
        result = subprocess.run(self.command(mode, root), capture_output=True, text=True, encoding='utf-8',
                                timeout=10, cwd=cwd, env=dict(os.environ, PYTHONUTF8='1'))
        self.assertIn(result.returncode, (0, 42, 43), result.stderr)
        return result.returncode, json.loads(result.stdout)

    def snapshot(self):
        return {p.relative_to(self.root).as_posix(): p.read_bytes() for p in self.root.rglob('*')
                if p.is_file() and p.name != lock.MARKER}

    def test_second_process_is_refused_without_changing_project_or_lock_bytes(self):
        marker = self.root / lock.MARKER
        marker.write_bytes(b'An old marker is not a PID authority.')
        before = self.snapshot()
        with lock.WorkspaceLease(self.root):
            code, reply = self.probe()
            self.assertEqual(code, 42)
            self.assertIn('--data', reply['message'])
            self.assertEqual(self.snapshot(), before)
        self.assertEqual(marker.read_bytes(), b'An old marker is not a PID authority.')
        self.assertEqual(self.probe()[0], 0)

    def test_separate_folders_can_be_owned_at_the_same_time(self):
        first = self.start()
        self.assertEqual(self.line(first)['status'], 'owned')
        second_root = Path(self.temp.name) / 'other data'
        second = self.start(root=second_root)
        self.assertEqual(self.line(second)['status'], 'owned')
        self.assertEqual(self.probe()[0], 42)
        self.assertEqual(self.probe(second_root)[0], 42)

    def test_holder_identity_and_retirement_precede_marker_cleanup(self):
        proc = self.start()
        reply = self.line(proc)
        self.assertEqual(reply['status'], 'owned')
        self.assertEqual(reply['pid'], proc.pid)
        self.stop(proc)
        marker = self.root / lock.MARKER
        self.assertTrue(marker.exists())
        self.assertEqual(self.probe()[0], 0)
        marker.unlink()
        self.assertFalse(marker.exists())

    def test_normal_process_exit_releases_ownership_without_deleting_the_marker(self):
        proc = self.start()
        self.assertEqual(self.line(proc)['status'], 'owned')
        proc.stdin.write('exit\n'); proc.stdin.flush()
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertTrue((self.root / lock.MARKER).exists())
        self.assertEqual(self.probe()[0], 0)

    def test_abrupt_exit_and_forced_termination_release_ownership(self):
        before = self.snapshot()
        for how in ('crash', 'kill'):
            with self.subTest(how=how):
                proc = self.start()
                self.assertEqual(self.line(proc)['status'], 'owned')
                if how == 'crash':
                    proc.stdin.write('crash\n'); proc.stdin.flush()
                    self.assertEqual(proc.wait(timeout=10), 73)
                else:
                    proc.kill(); proc.wait(timeout=10)
                self.assertEqual(self.probe()[0], 0)
                self.assertEqual(self.snapshot(), before)

    def test_ownership_remains_held_while_process_exit_handlers_are_still_running(self):
        proc = self.start('exit_pause')
        self.assertEqual(self.line(proc)['status'], 'owned')
        self.assertEqual(self.line(proc)['status'], 'shutting_down')
        self.assertEqual(self.probe()[0], 42)
        proc.stdin.write('finish\n'); proc.stdin.flush()
        self.assertEqual(proc.wait(timeout=10), 0)
        self.assertEqual(self.probe()[0], 0)

    def test_simultaneous_contenders_have_exactly_one_owner(self):
        contenders = [self.start('race') for _ in range(6)]
        for proc in contenders:
            self.assertEqual(self.line(proc)['status'], 'ready')
        for proc in contenders:
            proc.stdin.write('start\n'); proc.stdin.flush()
        replies = [self.line(proc) for proc in contenders]
        self.assertEqual(sum(r['status'] == 'owned' for r in replies), 1)
        self.assertEqual(sum(r['status'] == 'busy' for r in replies), 5)
        for proc, reply in zip(contenders, replies):
            if reply['status'] == 'owned':
                proc.stdin.write('exit\n'); proc.stdin.flush()
            self.assertEqual(proc.wait(timeout=10), 0 if reply['status'] == 'owned' else 42)
        self.assertEqual(self.probe()[0], 0)

    def test_process_lease_reuses_canonical_folder_and_descriptor_is_not_inherited(self):
        lease = lock.hold_workspace(self.root)
        self.addCleanup(lease.close)
        self.assertIs(lease, lock.hold_workspace(self.root / 'child/..'))
        self.assertFalse(os.get_inheritable(lease.fd))
        self.assertEqual(self.probe(self.root / 'child/..')[0], 42)
        lease.close(); lease.close()
        replacement = lock.hold_workspace(self.root)
        self.addCleanup(replacement.close)
        self.assertIsNot(replacement, lease)

    @unittest.skipUnless(os.name == 'posix', 'POSIX symlink alias; Windows junctions need native acceptance')
    def test_alias_paths_cannot_bypass_the_same_folder_owner(self):
        alias = Path(self.temp.name) / 'alias'
        alias.symlink_to(self.root, target_is_directory=True)
        with lock.WorkspaceLease(self.root):
            self.assertEqual(self.probe(alias)[0], 42)

    @unittest.skipUnless(hasattr(os, 'fork'), 'POSIX fork lifecycle only')
    def test_forked_child_cannot_claim_parent_ownership_or_unlock_it(self):
        lease = lock.hold_workspace(self.root)
        self.addCleanup(lease.close)
        pid = os.fork()
        if pid == 0:
            try:
                lock.hold_workspace(self.root)
            except lock.WorkspaceInUse:
                os._exit(42)
            except BaseException:
                os._exit(43)
            os._exit(0)
        _, status = os.waitpid(pid, 0)
        self.assertEqual(os.waitstatus_to_exitcode(status), 42)
        self.assertEqual(self.probe()[0], 42)

    def test_refused_mount_does_not_create_runtime_folders_or_migrate_legacy_data(self):
        before = self.snapshot()
        with lock.WorkspaceLease(self.root):
            code, reply = self.probe(mode='mount')
            self.assertEqual(code, 42)
            self.assertEqual(reply['status'], 'busy')
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.root / 'projects').exists())
        self.assertFalse((self.root / 'renders').exists())

    def test_owned_mount_migrates_legacy_data_and_later_mount_can_reopen_it(self):
        old = self.project.read_bytes()
        self.assertEqual(self.probe(mode='mount')[0], 0)
        self.assertFalse(self.project.exists())
        migrated = self.root / 'projects/default/project.json'
        self.assertEqual(migrated.read_bytes(), old)
        self.assertTrue((self.root / 'renders').is_dir())
        self.assertEqual(self.probe(mode='mount')[0], 0)
        self.assertEqual(migrated.read_bytes(), old)

    def test_fresh_owned_mount_restores_export_history_after_competing_owner_exits(self):
        import job_history
        from export_storage import reserve_export
        destination = reserve_export(self.root/'renders', 'kept', {})
        Path(destination['path']).write_bytes(b'completed output fixture')
        job = {'id':destination['id'], 'name':destination['name'], 'out':destination['url'],
               'status':'done', 'started':1, 'sequence':'s', 'preset':{},
               'review_url':'/review/job-'+destination['id'],
               'command_log':destination['url'].rsplit('.',1)[0]+'.cmd.txt'}
        receipt = job_history.save(self.root,job)
        with lock.WorkspaceLease(self.root): self.assertEqual(self.probe(mode='mount')[0],42)
        code,reply = self.probe(mode='mount')
        self.assertEqual(code,0);self.assertEqual(reply['history_jobs'],[job['id']])
        self.assertEqual((Path(destination['directory'])/job_history.PRIMARY).read_bytes(),receipt)
        self.assertEqual(Path(destination['path']).read_bytes(),b'completed output fixture')

    def test_mount_normalizes_relative_paths_and_rendering_uses_the_owned_root(self):
        other = Path(self.temp.name) / 'wrong inherited folder'
        with patch.dict(os.environ, FILMOCITY_ROOT=str(other), FILMOCITY_DATA=str(other)):
            code, reply = self.probe(root=Path('data/../data'), mode='mount', cwd=self.temp.name)
        self.assertEqual(code, 0)
        self.assertEqual(reply['root'], str(self.root.resolve()))
        self.assertEqual(reply['render_root'], str(self.root.resolve()))
        self.assertFalse(other.exists())
        self.assertTrue((self.root / 'projects/default/project.json').is_file())

    def test_lock_errors_stop_startup_and_close_unowned_descriptors(self):
        for error in (OSError(errno.EACCES, 'busy'), OSError(errno.ENOTSUP, 'unsupported filesystem')):
            with self.subTest(error=error), patch.object(lock, '_lock', side_effect=error), patch.object(lock.os, 'close', wraps=os.close) as close:
                with self.assertRaises(lock.WorkspaceLockError) as caught:
                    lock.WorkspaceLease(self.root)
                self.assertEqual(isinstance(caught.exception, lock.WorkspaceInUse), error.errno == errno.EACCES)
                self.assertEqual(close.call_count, 1)
        with patch.object(lock.os, 'open', side_effect=PermissionError('read-only folder')):
            with self.assertRaisesRegex(lock.WorkspaceLockError, 'Could not secure'):
                lock.WorkspaceLease(self.root)

    def test_nonregular_marker_is_refused_and_cannot_be_mistaken_for_a_stale_lock(self):
        (self.root / lock.MARKER).mkdir()
        before = self.snapshot()
        with self.assertRaises(lock.WorkspaceLockError):
            lock.WorkspaceLease(self.root)
        self.assertEqual(self.snapshot(), before)

    @unittest.skipUnless(os.name == 'posix', 'Symlink/hardlink fixtures require native Windows privilege acceptance')
    def test_linked_marker_is_refused_without_touching_its_target(self):
        marker = self.root / lock.MARKER
        for kind in ('symlink', 'hardlink'):
            with self.subTest(kind=kind):
                if kind == 'symlink': marker.symlink_to(self.project)
                else: os.link(self.project, marker)
                before = self.project.read_bytes()
                with self.assertRaises(lock.WorkspaceLockError): lock.WorkspaceLease(self.root)
                self.assertEqual(self.project.read_bytes(), before)
                marker.unlink()

    def test_windows_adapter_locks_and_unlocks_the_same_byte_without_retry_mode(self):
        calls = []
        msvcrt = SimpleNamespace(LK_NBLCK=2, LK_UNLCK=0, locking=lambda *args: calls.append(args))
        with patch.object(lock, '_PLATFORM', 'nt'), patch.dict(sys.modules, msvcrt=msvcrt), patch.object(lock.os, 'lseek') as seek:
            lock._lock(71); lock._lock(71, release=True)
        self.assertEqual(calls, [(71, 2, 1), (71, 0, 1)])
        self.assertEqual(seek.call_args_list, [unittest.mock.call(71, 0, os.SEEK_SET)] * 2)

    def test_failed_unlock_still_closes_its_descriptor_and_is_idempotent(self):
        lease = lock.WorkspaceLease(self.root)
        with patch.object(lock, '_lock', side_effect=OSError('unlock denied')):
            with self.assertRaises(OSError): lease.close()
        self.assertIsNone(lease.fd)
        lease.close()
        self.assertEqual(self.probe()[0], 0)


if __name__ == '__main__': unittest.main(verbosity=2)
