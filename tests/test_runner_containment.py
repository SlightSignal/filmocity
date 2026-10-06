"""Real benign child processes prove focused-runner data isolation and cleanup."""
import ast
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch
import uuid

import run_checks as runner

SOURCE = Path(__file__).resolve().parents[1]


class RunnerContainment(unittest.TestCase):
    def setUp(self):
        parent = Path(os.environ.get('TEMP') or tempfile.gettempdir())
        self.root = parent / ('runner-fixture-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.addCleanup(shutil.rmtree, self.root)
        self.checkout = self.root / 'checkout'
        (self.checkout / 'tests').mkdir(parents=True)
        self.production = self.root / 'caller-production'
        self.production.mkdir()
        self.sentinel = self.production / 'project.json'
        self.sentinel.write_bytes(b'caller project must survive byte-identical')
        self.previous = self.sentinel.read_bytes()
        self.temp = self.root / 'parent-temp'
        self.temp.mkdir()
        self.bytecode = self.root / 'captured-bytecode'
        self.environment = {'TEMP': str(self.temp), 'TMP': str(self.temp), 'TMPDIR': str(self.temp),
                            'FILMOCITY_ROOT': str(self.production), 'FILMOCITY_DATA': str(self.production),
                            'PYTHONPYCACHEPREFIX': str(self.bytecode)}

    def script(self, name, exit_code=0):
        # Both this Python-only frontend substitute and the Python suite execute
        # as actual children. No application imports, listener or encoder.
        body = """import json, os, pathlib, sys
keys = ('FILMOCITY_ROOT','FILMOCITY_DATA','TEMP','TMP','TMPDIR','PYTHONPYCACHEPREFIX')
observed = {key: os.environ[key] for key in keys}
root = pathlib.Path(observed['FILMOCITY_ROOT']); root.mkdir()
(root/'benign-test-marker').write_bytes(b'owned child files')
with (pathlib.Path.cwd()/'observations.jsonl').open('a', encoding='utf-8') as output:
    output.write(json.dumps(observed)+'\\n')
sys.exit(EXIT_CODE)
""".replace('EXIT_CODE', str(exit_code))
        (self.checkout / 'tests' / name).write_text(body, encoding='utf-8')

    def run_fixture(self, suites):
        with patch.dict(os.environ, self.environment), patch.object(runner, 'ROOT', self.checkout), \
                patch.object(runner, 'SUITES', suites), patch.object(runner.shutil, 'which', return_value=sys.executable):
            return runner.run_checks()

    def observations(self):
        return [json.loads(line) for line in (self.checkout / 'observations.jsonl').read_text(encoding='utf-8').splitlines()]

    def test_real_children_get_distinct_private_libraries_and_preserve_outer_bytecode(self):
        self.script('run_frontend.cjs')
        self.script('test_fixture.py')
        self.assertEqual(self.run_fixture(('test_fixture.py',)), 0)
        observations = self.observations()
        self.assertEqual(len(observations), 2)
        self.assertNotEqual(observations[0]['FILMOCITY_ROOT'], observations[1]['FILMOCITY_ROOT'])
        for observed in observations:
            self.assertEqual(observed['FILMOCITY_ROOT'], observed['FILMOCITY_DATA'])
            self.assertEqual(observed['TEMP'], observed['TMP'])
            self.assertEqual(observed['TEMP'], observed['TMPDIR'])
            self.assertTrue(Path(observed['TEMP']).is_relative_to(self.temp))
            self.assertTrue(Path(observed['FILMOCITY_ROOT']).is_relative_to(self.temp))
            self.assertNotEqual(observed['FILMOCITY_ROOT'], str(self.production))
            self.assertEqual(observed['PYTHONPYCACHEPREFIX'], str(self.bytecode))
            self.assertFalse(Path(observed['TEMP']).exists())
            self.assertFalse(Path(observed['FILMOCITY_ROOT']).exists())
        self.assertEqual(self.sentinel.read_bytes(), self.previous)
        self.assertEqual({path.name for path in self.production.iterdir()}, {'project.json'})
        self.assertEqual(list(self.temp.iterdir()), [])

    def test_failure_stops_before_later_suite_and_still_cleans_private_library(self):
        self.script('run_frontend.cjs')
        self.script('test_fail.py', exit_code=23)
        self.script('test_later.py')
        self.assertEqual(self.run_fixture(('test_fail.py', 'test_later.py')), 23)
        self.assertEqual(len(self.observations()), 2)
        self.assertEqual(list(self.temp.iterdir()), [])
        self.assertEqual(self.sentinel.read_bytes(), self.previous)

    @unittest.skipUnless(os.name == 'nt', 'Actual Windows open-file cleanup denial')
    def test_real_open_file_refuses_cleanup_visibly_and_does_not_report_pass(self):
        held = []
        private = []
        def owned_open_file(command, *, cwd, env):
            target = Path(env['TEMP']) / 'held-benign-file'
            held.append(target.open('wb'))
            private.append(Path(env['TEMP']).parent.parent)
            return type('Completed', (), {'returncode': 0})()
        error = io.StringIO()
        output = io.StringIO()
        try:
            with patch.dict(os.environ, self.environment), patch.object(runner, 'ROOT', self.checkout), \
                    patch.object(runner, 'SUITES', ()), patch.object(runner.shutil, 'which', return_value=sys.executable), \
                    patch.object(runner.subprocess, 'run', side_effect=owned_open_file), \
                    contextlib.redirect_stderr(error), contextlib.redirect_stdout(output):
                self.assertEqual(runner.run_checks(), 3)
            self.assertIn('CLEANUP_FAILED', error.getvalue())
            self.assertNotIn('All focused checks passed', output.getvalue())
            self.assertTrue(private[0].is_relative_to(self.temp))
            self.assertTrue(private[0].exists())
            self.assertEqual(self.sentinel.read_bytes(), self.previous)
        finally:
            for handle in held: handle.close()
            for directory in private:
                if directory.exists(): shutil.rmtree(directory)

    def test_declared_suites_are_unique_existing_plain_python_tests(self):
        self.assertEqual(len(runner.SUITES), len(set(runner.SUITES)))
        for name in runner.SUITES:
            self.assertEqual(Path(name).name, name)
            self.assertTrue(name.startswith('test_') and name.endswith('.py'))
            source = SOURCE / 'tests' / name
            self.assertTrue(source.is_file(), name)
            ast.parse(source.read_text(encoding='utf-8'), filename=name)


if __name__ == '__main__': unittest.main(verbosity=2)
