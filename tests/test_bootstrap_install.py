"""Runtime admission and installation safeguards; pip/app launch is controlled."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('bootstrap_install_fixture', ROOT / 'launcher/bootstrap.py')
bootstrap = importlib.util.module_from_spec(spec); spec.loader.exec_module(bootstrap)
QUALIFIED = {'python':'3.13.16', 'implementation':'CPython', 'releaselevel':'final',
             'bits':64, 'machine':'AMD64', 'gil_disabled':False, 'gil_enabled':True}


class BootstrapInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity runtime É's "); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name); (self.root / 'packaging').mkdir(); (self.root / 'launcher').mkdir()
        (self.root / 'packaging/runtime-windows.json').write_bytes((ROOT / 'packaging/runtime-windows.json').read_bytes())
        self.venv = self.root / '.venv'; self.python = self.venv / 'Scripts/python.exe'
        for name, value in (('ROOT',str(self.root)), ('VENV',str(self.venv)), ('PY',str(self.python)), ('IS_WIN',True)):
            patcher = patch.object(bootstrap, name, value); patcher.start(); self.addCleanup(patcher.stop)
    def existing(self):
        self.python.parent.mkdir(parents=True); self.python.write_bytes(b'preserved interpreter marker')
        (self.venv / 'pyvenv.cfg').write_bytes(b'preserved environment config')
        return {str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
    def assert_preserved(self, before):
        self.assertEqual(before, {str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file()})
    def test_missing_media_tools_stop_before_any_python_installation(self):
        with patch.object(bootstrap, 'have_ffmpeg', return_value=False), \
             patch.object(bootstrap, 'ensure_python'), patch.object(bootstrap, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Supply a reviewed distribution'):
                bootstrap.install()
            run.assert_not_called()

    def test_wrong_windows_python_or_old_patch_does_not_create_or_modify_environment(self):
        for change in ({'python':'3.13.15'}, {'python':'3.12.10'}, {'bits':32}, {'gil_disabled':True}, {'implementation':'PyPy'}):
            with self.subTest(change=change), patch.object(bootstrap, 'current_runtime', return_value={**QUALIFIED, **change}), patch.object(bootstrap, 'run') as run:
                with self.assertRaisesRegex(SystemExit, '3.13.16'): bootstrap.ensure_venv()
                run.assert_not_called(); self.assertFalse(self.venv.exists())
    def test_stale_existing_interpreter_is_probed_and_refused_before_pip(self):
        before = self.existing(); old = {**QUALIFIED, 'python':'3.12.10'}
        with patch.object(bootstrap, 'current_runtime', return_value=QUALIFIED), \
             patch.object(bootstrap.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps(old))) as probe, \
             patch.object(bootstrap, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'Existing environment.*preserved'): bootstrap.ensure_venv()
            run.assert_not_called(); self.assertEqual(probe.call_args.args[0][:5], [str(self.python), '-I', '-S', '-B', '-c'])
        self.assert_preserved(before)
    def test_old_patch_existing_environment_cannot_be_launched_even_when_marked_installed(self):
        self.existing(); data = self.root / 'must-not-create-data'
        (self.root / 'launcher/installed.json').write_text('{"installed":true}')
        before = {str(path.relative_to(self.root)):path.read_bytes() for path in self.root.rglob('*') if path.is_file()}
        with patch.object(bootstrap, 'current_runtime', return_value=QUALIFIED), \
             patch.object(bootstrap.subprocess, 'run', return_value=SimpleNamespace(returncode=0, stdout=json.dumps({**QUALIFIED, 'python':'3.13.15'}))), \
             patch.object(bootstrap.subprocess, 'Popen') as launch, patch.object(bootstrap, 'install') as install, \
             patch.object(bootstrap.webbrowser, 'open') as browser:
            with self.assertRaisesRegex(SystemExit, '3.13.16'): bootstrap.serve(data=str(data))
            launch.assert_not_called(); install.assert_not_called(); browser.assert_not_called(); self.assertFalse(data.exists())
        self.assert_preserved(before)
    def test_incomplete_existing_environment_is_preserved_instead_of_recreated(self):
        self.venv.mkdir(); marker = self.venv / 'keep.txt'; marker.write_bytes(b'keep')
        with patch.object(bootstrap, 'current_runtime', return_value=QUALIFIED), patch.object(bootstrap, 'run') as run:
            with self.assertRaisesRegex(SystemExit, 'no usable interpreter.*preserved'): bootstrap.ensure_venv()
            run.assert_not_called(); self.assertEqual(marker.read_bytes(), b'keep'); self.assertFalse(self.python.exists())
    def test_compatible_existing_environment_is_checked_before_hash_locked_install(self):
        before = self.existing(); order = []
        def probe(*args, **kwargs):
            order.append('probe'); return SimpleNamespace(returncode=0, stdout=json.dumps(QUALIFIED))
        with patch.object(bootstrap, 'current_runtime', return_value=QUALIFIED), patch.object(bootstrap.subprocess, 'run', side_effect=probe), \
             patch.object(bootstrap, 'run', side_effect=lambda *args, **kwargs: order.append(Path(args[0][-1]).name)):
            bootstrap.ensure_venv()
        self.assertEqual(order, ['probe', 'requirements-bootstrap-windows.lock', 'requirements-runtime-windows.lock']); self.assert_preserved(before)
    def test_actual_existing_environment_interpreter_identity_is_qualified_without_installing_or_launching(self):
        # The isolated child only prints stdlib identity for this qualified test
        # environment; no pip, backend, browser or GUI process is started.
        with patch.object(bootstrap, 'PY', sys.executable), patch.object(bootstrap, 'run') as install, patch.object(bootstrap.subprocess, 'Popen', wraps=subprocess.Popen) as launches:
            identity = bootstrap.ensure_environment_runtime()
            self.assertEqual(identity, bootstrap.current_runtime()); self.assertFalse(identity['gil_disabled']); self.assertTrue(identity['gil_enabled'])
            install.assert_not_called(); self.assertEqual(launches.call_count, 1)

    def test_hash_install_failure_stops_without_an_install_success_record(self):
        self.existing()
        with patch.object(bootstrap, 'current_runtime', return_value=QUALIFIED), patch.object(bootstrap, 'ensure_ffmpeg'), \
             patch.object(bootstrap, 'ensure_environment_runtime'), patch.object(bootstrap, 'say'), \
             patch.object(bootstrap, 'run', side_effect=subprocess.CalledProcessError(1, ['pip'])) as run:
            with self.assertRaises(subprocess.CalledProcessError): bootstrap.install()
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn('--require-hashes', command)
            self.assertEqual(Path(command[-1]).name, 'requirements-bootstrap-windows.lock')
            self.assertFalse((self.root / 'launcher/installed.json').is_file())


if __name__ == '__main__': unittest.main(verbosity=2)
