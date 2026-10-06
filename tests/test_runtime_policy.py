"""Declared runtime floor, actual isolated identity and source entrypoint gates."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
import runtime_policy as policy

IDENTITY = {'python':'3.13.16', 'implementation':'CPython', 'releaselevel':'final',
            'bits':64, 'machine':'AMD64', 'gil_disabled':False, 'gil_enabled':True}


class RuntimePolicyTests(unittest.TestCase):
    def setUp(self): self.declared = policy.load_policy(ROOT / 'packaging/runtime-windows.json')
    def test_declared_policy_selects_reviewed_branch_floor_and_standard_gil(self):
        self.assertEqual(self.declared['minimum_version'], '3.13.16'); self.assertEqual(self.declared['gil'], 'standard')
        self.assertIs(policy.validate_runtime(IDENTITY, self.declared), IDENTITY)
        self.assertEqual(policy.validate_runtime({**IDENTITY,'python':'3.13.17'}, self.declared)['python'], '3.13.17')
    def test_old_patch_other_branches_prereleases_and_incomplete_version_identity_are_refused(self):
        for version in ('3.13.15','3.12.10','3.14.0','3.13','3.13.16rc1','not-python'):
            with self.subTest(version=version), self.assertRaisesRegex(policy.RuntimePolicyError, '3.13.16'):
                policy.validate_runtime({**IDENTITY, 'python':version}, self.declared)
        with self.assertRaises(policy.RuntimePolicyError): policy.validate_runtime({**IDENTITY, 'releaselevel':'candidate'}, self.declared)
    def test_free_threaded_disabled_gil_other_implementation_or_architecture_is_refused(self):
        for change in ({'gil_disabled':True}, {'gil_enabled':False}, {'implementation':'PyPy'}, {'bits':32}, {'machine':'ARM64'}):
            with self.subTest(change=change), self.assertRaises(policy.RuntimePolicyError): policy.validate_runtime({**IDENTITY, **change}, self.declared)
    def test_missing_runtime_proof_is_not_assumed_compatible(self):
        for key in IDENTITY:
            value = dict(IDENTITY); del value[key]
            with self.subTest(key=key), self.assertRaises(policy.RuntimePolicyError): policy.validate_runtime(value, self.declared)
        for value in (None, [], '3.13.16'):
            with self.assertRaises(policy.RuntimePolicyError): policy.validate_runtime(value, self.declared)
    def test_malformed_missing_or_incompatible_policy_is_actionable(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'runtime-windows.json'
            with self.assertRaisesRegex(policy.RuntimePolicyError, 'Cannot read'): policy.load_policy(path)
            for value in ('not-json', '{}', json.dumps({**self.declared,'gil':'free-threaded'}), json.dumps({**self.declared,'minimum_version':'3.13'})):
                path.write_text(value)
                with self.assertRaises(policy.RuntimePolicyError): policy.load_policy(path)
    def test_probe_is_isolated_bounded_read_only_and_uses_exact_environment_executable(self):
        calls = []
        def capture(command, **kwargs): calls.append((command, kwargs)); return SimpleNamespace(returncode=0, stdout=json.dumps(IDENTITY))
        policy.probe_interpreter("C:/private É's/.venv/Scripts/python.exe", self.declared, runner=capture)
        command, options = calls[0]; self.assertEqual(command[:5], ["C:/private É's/.venv/Scripts/python.exe", '-I', '-S', '-B', '-c'])
        self.assertEqual(options['timeout'], 15); self.assertTrue(options['capture_output']); self.assertNotIn('cwd', options)
        self.assertNotIn('launcher', command[-1]); self.assertNotIn('pip', command[-1]); self.assertNotIn('server', command[-1])
        if sys.platform == 'win32': self.assertEqual(options['creationflags'], subprocess.CREATE_NO_WINDOW)
    def test_failed_unreadable_or_oversized_probe_never_passes(self):
        for result in (SimpleNamespace(returncode=1,stdout=''), SimpleNamespace(returncode=0,stdout='not json'), SimpleNamespace(returncode=0,stdout='x'*65537)):
            with self.subTest(result=result), self.assertRaises(policy.RuntimePolicyError):
                policy.probe_interpreter('private-python.exe', self.declared, runner=lambda *a,**k: result)
        with self.assertRaises(policy.RuntimePolicyError):
            policy.probe_interpreter('private-python.exe', self.declared, runner=lambda *a,**k: (_ for _ in ()).throw(subprocess.TimeoutExpired('private',15)))
    def test_actual_current_interpreter_matches_actual_isolated_child_identity(self):
        current = policy.validate_runtime(policy.current_runtime(), self.declared)
        self.assertEqual(policy.probe_interpreter(sys.executable,self.declared), current)
    def test_current_cli_admission_handles_missing_gui_standard_streams_without_false_success(self):
        with patch.object(policy,'current_runtime',return_value={**IDENTITY,'python':'3.13.15'}), patch.object(sys,'stderr',None): self.assertEqual(policy.main(),1)
        with patch.object(policy,'current_runtime',return_value=IDENTITY): self.assertEqual(policy.main(),0)
    def test_both_batch_launchers_gate_selected_and_path_python_before_bootstrap(self):
        for name in ('Filmocity.bat','Install Filmocity.bat'):
            text=(ROOT/name).read_text()
            for interpreter in ('py -3.13','python'):
                gate=text.index(interpreter+' packaging\\runtime_policy.py')
                launch=text.index(interpreter+' launcher\\bootstrap.py')
                self.assertLess(gate,launch); self.assertIn('if errorlevel 1 goto python_failed',text[gate:launch])
            self.assertNotIn('-3.12',text)
    def test_hidden_launcher_waits_for_environment_guard_and_refuses_before_bootstrap(self):
        text=(ROOT/'Filmocity (no console).vbs').read_text()
        guard=text.index('\\packaging\\runtime_policy.py'); launch=text.index('\\launcher\\bootstrap.py')
        self.assertLess(guard,launch); self.assertIn('0, True) <> 0 Then',text[guard:launch]); self.assertIn('WScript.Quit 1',text[guard:launch]); self.assertIn('existing .venv was preserved',text[guard:launch])


if __name__ == '__main__': unittest.main(verbosity=2)
