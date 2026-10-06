"""Build isolation/provenance with real files and a controlled compiler fixture.

The tiny PE headers are test data, not runnable binaries or native build evidence.
"""
import copy
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'packaging'))
import candidate as c


def pe(path, machine=0x8664):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    header = bytearray(128); header[:2] = b'MZ'; struct.pack_into('<I', header, 60, 64); header[64:68] = b'PE\0\0'; struct.pack_into('<H', header, 68, machine)
    path.write_bytes(header); return path


class CandidateBuildTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Filmocity candidate É's "); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'source'; self.root.mkdir(); self.tools = Path(self.temp.name) / 'portable tools'; self.tools.mkdir()
        # Cleanup must see the deep fixture as well, instead of repeating the
        # ordinary-path enumeration bug after a successful test.
        self.temp.name = str(c.filesystem_path(self.temp.name))
        for name in c.INPUT_DIRS:
            (self.root / name).mkdir(); (self.root / name / 'source.txt').write_text('fixture')
        for name in c.ROOT_FILES:
            (self.root / name).write_text('fixture')
        (self.root / 'packaging/release-inputs.json').write_text(json.dumps({'schema':'filmocity-release-inputs/v1', 'benchmarks':['benchmarks/source.txt']}))
        for name in ('runtime-windows.json', 'runtime_policy.py'):
            (self.root / 'packaging' / name).write_bytes((ROOT / 'packaging' / name).read_bytes())
        (self.root / 'backend/server.py').write_text('import json\nfrom pathlib import Path\nfrom project_sync import workspace_id\n')
        (self.root / 'backend/runtime_identity.py').write_text('import uuid\n')
        (self.root / 'backend/workspace_lock.py').write_text('import os\n')
        (self.root / 'backend/render.py').write_text('import math\n')
        (self.root / 'frontend/index.html').write_text('<script src="/static/app.js?v=1"></script>')
        (self.root / 'frontend/app.js').write_text('/* source */')
        for name in ['filmocity_app.py', 'windows_lifetime.py']: (self.root / 'packaging' / name).write_text('# entry')
        pe(self.tools / 'ffmpeg.exe'); pe(self.tools / 'ffprobe.exe')
        (self.tools / 'avcodec.dll').write_bytes(b'dll fixture'); (self.tools / 'LICENSE.txt').write_text('supplied tool license')
        self.versions = {name: '9.0.0' for name in c.PACKAGES.values()}
        (self.root / 'requirements-build-windows.lock').write_text(''.join(f'{name}=={version} --hash=sha256:{"a"*64}\n' for name,version in self.versions.items()))
        self.host = {'system': 'Windows', 'machine': 'AMD64', 'bits': 64, 'python': '3.13.16', 'executable': 'python.exe',
                     'implementation': 'CPython', 'releaselevel': 'final', 'gil_disabled': False, 'gil_enabled': True}
        self.calls = []
    def report(self, **extra):
        def probe(args, **kwargs):
            self.calls.append((args, kwargs)); return SimpleNamespace(returncode=0, stdout=Path(args[0]).stem + ' version fixture\n')
        return c.preflight(self.root, self.tools, host=extra.pop('host', self.host), packages=lambda: (self.versions, []), runner=extra.pop('runner', probe), **extra)
    def compiler(self, command, **kwargs):
        self.calls.append((command, kwargs)); out = Path(command[command.index('--distpath') + 1]); pe(out / 'Filmocity.exe')
        return SimpleNamespace(returncode=0)
    def test_preflight_collects_versioned_inputs_and_tools_without_creating_output(self):
        report = self.report(); self.assertTrue(report['ok'], report['errors']); self.assertFalse((self.root / 'dist').exists())
        self.assertEqual(report['tools']['ffmpeg.exe']['architecture'], 'x64'); self.assertEqual(report['packages'], self.versions)
        self.assertIn('frontend/app.js', {r['path'] for r in report['inputs']}); self.assertEqual(len(self.calls), 2)
    def test_deep_declared_inputs_and_binary_resources_are_hashed_and_copied(self):
        # Create through extended paths, then call the recipe with ordinary
        # drive paths. This reproduces the original silent 81-file omission.
        relative = Path('benchmarks') / ('long evidence ' * 7) / ('controlled ' * 8) / ('frame ' * 12) / 'reference.json'
        original = c.filesystem_path(self.root / relative)
        original.parent.mkdir(parents=True); original.write_bytes(b'complete evidence')
        manifest = self.root / 'packaging/release-inputs.json'
        declared = json.loads(manifest.read_text()); declared['benchmarks'].append(relative.as_posix()); manifest.write_text(json.dumps(declared))
        notice_relative = Path('licenses') / ('distribution ' * 10) / ('third party ' * 10) / 'NOTICE.txt'
        notice = c.filesystem_path(self.tools / notice_relative)
        notice.parent.mkdir(parents=True); notice.write_bytes(b'license evidence')
        self.assertGreater(len(str(self.root / relative)), 260)
        report = self.report(); self.assertTrue(report['ok'], report['errors'])
        self.assertEqual(next(r for r in report['inputs'] if r['path'] == relative.as_posix())['sha256'], c.digest(original))
        self.assertEqual(next(r for r in report['binary_resources'] if r['path'] == notice_relative.as_posix())['sha256'], c.digest(notice))
        output = c.reserve_output(self.root); result = c.build(self.root, output, report, runner=self.compiler)
        self.assertEqual(result['status'], 'built_unverified')
        self.assertEqual(c.filesystem_path(output / 'inputs' / relative).read_bytes(), b'complete evidence')
        self.assertEqual(c.filesystem_path(output / 'inputs/bin' / notice_relative).read_bytes(), b'license evidence')
    def test_directory_read_errors_are_never_treated_as_complete_capture(self):
        original = c.os.scandir
        def scan(path):
            if Path(path).name == 'assets': raise PermissionError('evidence directory denied')
            return original(path)
        with patch.object(c.os, 'scandir', side_effect=scan): report = self.report()
        self.assertFalse(report['ok']); self.assertIn('evidence directory denied', report['errors'])
    def test_only_declared_benchmark_fixtures_enter_release_inputs(self):
        private = self.root / 'benchmarks/historical-project.json'; private.write_text('private old run')
        report = self.report(); self.assertTrue(report['ok'], report['errors'])
        names = {row['path'] for row in report['inputs']}
        self.assertIn('benchmarks/source.txt', names); self.assertNotIn('benchmarks/historical-project.json', names)
        self.assertIn('requirements-build-windows.lock', names); self.assertIn('THIRD_PARTY_NOTICES.md', names)
        self.assertEqual(private.read_text(), 'private old run')
    def test_invalid_missing_or_traversing_declared_inputs_fail_capture(self):
        manifest = self.root / 'packaging/release-inputs.json'
        for selected in (['benchmarks/../LICENSE'], ['benchmarks/missing.txt'], ['benchmarks/source.txt'] * 2, ['backend/server.py']):
            with self.subTest(selected=selected):
                manifest.write_text(json.dumps({'schema':'filmocity-release-inputs/v1', 'benchmarks':selected}))
                self.assertFalse(self.report()['ok'])
    def test_mismatched_or_unhashed_build_dependencies_block_compilation(self):
        self.versions['uvicorn'] = 'different'
        report = self.report(); self.assertFalse(report['ok'])
        self.assertTrue(any('uvicorn must match' in e for e in report['errors']))
        (self.root / 'requirements-build-windows.lock').write_text('uvicorn>=1\n')
        report = self.report(); self.assertFalse(report['ok'])
        self.assertIn('Invalid hash-locked build requirement', report['errors'])
    def test_device_prefix_is_kept_out_of_pyinstaller_source_destination_parser(self):
        stage = c.filesystem_path(self.root); output = c.filesystem_path(Path(self.temp.name) / 'candidate')
        args = c.build_command(stage, output)
        self.assertFalse(any('\\\\?\\' in value for value in args))
        try:
            from PyInstaller.__main__ import generate_parser
        except ImportError:
            # The source suite requires no installed compiler. The actual
            # Windows build environment exercises the real parser below.
            parsed = None
        else: parsed = generate_parser().parse_args(args[3:])
        if parsed is not None:
            self.assertEqual(Path(dict((destination, source) for source, destination in parsed.datas)['backend']), self.root / 'backend')
    def test_unsupported_hosts_missing_tools_wrong_architecture_and_broken_probes_fail(self):
        report = self.report(host={**self.host, 'system': 'Linux'}); self.assertFalse(report['ok']); self.assertEqual(self.calls, [])
        pe(self.tools / 'ffmpeg.exe', 0x14c); (self.tools / 'ffprobe.exe').unlink(); report = self.report()
        self.assertFalse(report['ok']); self.assertTrue(any('must be a Windows x64' in e for e in report['errors']))
        pe(self.tools / 'ffmpeg.exe'); pe(self.tools / 'ffprobe.exe'); report = self.report(runner=lambda *a, **k: SimpleNamespace(returncode=7))
        self.assertFalse(report['ok']); self.assertTrue(any('exit 7' in e for e in report['errors']))
    def test_missing_frontend_references_and_old_builder_are_reported_before_staging(self):
        (self.root / 'frontend/app.js').unlink(); self.versions['pyinstaller'] = '5.0'
        report = self.report(); self.assertFalse(report['ok']); self.assertTrue(any('missing file' in e for e in report['errors'])); self.assertTrue(any('pyinstaller 6' in e for e in report['errors']))
    def test_python_without_runtime_hashing_support_is_rejected(self):
        report = self.report(host={**self.host, 'python': '3.10.16'})
        self.assertFalse(report['ok']); self.assertTrue(any('Python 3.11' in e for e in report['errors']))
    def test_old_security_patch_wrong_branch_and_nonstandard_runtime_fail_before_tool_processes(self):
        for change in ({'python':'3.13.15'}, {'python':'3.12.10'}, {'python':'3.14.0'},
                       {'gil_disabled':True}, {'gil_enabled':False}, {'implementation':'PyPy'}, {'releaselevel':'candidate'}):
            with self.subTest(change=change):
                self.calls.clear(); report = self.report(host={**self.host, **change})
                self.assertFalse(report['ok']); self.assertEqual(self.calls, [])
                self.assertTrue(any('3.13.16' in message for message in report['errors']))
                self.assertFalse((self.root / 'dist').exists())
    def test_declared_runtime_policy_and_helper_are_captured_once_and_unchanged(self):
        report = self.report(); self.assertTrue(report['ok'], report['errors'])
        for name in ('packaging/runtime-windows.json', 'packaging/runtime_policy.py'):
            rows = [row for row in report['inputs'] if row['path'] == name]
            self.assertEqual(len(rows), 1); self.assertEqual(rows[0]['sha256'], c.digest(self.root / name))
        output = c.reserve_output(self.root); stage = c.stage_inputs(self.root, output, report, 'runtime-fixture')
        self.assertEqual((stage / 'packaging/runtime-windows.json').read_bytes(), (self.root / 'packaging/runtime-windows.json').read_bytes())
    def test_missing_invalid_runtime_policy_or_missing_helper_blocks_preflight(self):
        policy = self.root / 'packaging/runtime-windows.json'; original = policy.read_bytes()
        for payload in (b'not json', b'{}'):
            policy.write_bytes(payload); self.calls.clear(); self.assertFalse(self.report()['ok']); self.assertEqual(self.calls, [])
        policy.unlink(); self.assertFalse(self.report()['ok']); policy.write_bytes(original)
        (self.root / 'packaging/runtime_policy.py').unlink(); self.assertFalse(self.report()['ok'])
    def test_existing_outputs_and_input_directories_are_never_overwritten(self):
        old = self.root / 'dist/Filmocity.exe'; old.parent.mkdir(); old.write_bytes(b'verified release')
        with self.assertRaises(FileExistsError): c.reserve_output(self.root, old.parent)
        for name in ['backend/build', 'frontend/candidate', 'packaging/build', 'tests/output', '.venv/artifact']:
            with self.assertRaises(c.CandidateError): c.reserve_output(self.root, self.root / name)
        with self.assertRaises(c.CandidateError): c.reserve_output(self.root, self.root)
        output = c.reserve_output(self.root); self.assertTrue(output.is_dir()); self.assertEqual(old.read_bytes(), b'verified release')
    def test_snapshot_omits_runtime_libraries_hidden_files_and_bytecode(self):
        (self.root / 'filmocity_data').mkdir(); (self.root / 'filmocity_data/project.json').write_text('private library')
        (self.root / 'launcher/installed.json').write_text('local install'); (self.root / 'launcher/install.log').write_text('install log'); (self.root / 'packaging/old.spec').write_text('old build');
        (self.root / 'backend/.env').write_text('secret'); (self.root / 'backend/__pycache__').mkdir(); (self.root / 'backend/__pycache__/server.pyc').write_bytes(b'cache')
        report = self.report(); output = c.reserve_output(self.root); stage = c.stage_inputs(self.root, output, report, 'build-id')
        self.assertFalse((stage / 'launcher/installed.json').exists()); self.assertFalse((stage / 'launcher/install.log').exists()); self.assertFalse((stage / 'packaging/old.spec').exists());
        self.assertFalse((stage / 'filmocity_data').exists()); self.assertFalse((stage / 'backend/.env').exists()); self.assertFalse((stage / 'backend/__pycache__').exists())
        self.assertEqual((stage / 'bin/LICENSE.txt').read_text(), 'supplied tool license'); self.assertEqual((stage / 'bin/avcodec.dll').read_bytes(), b'dll fixture')
    def test_changes_between_inspection_and_copy_fail_without_touching_original_data(self):
        report = self.report(); (self.root / 'frontend/app.js').write_text('changed later'); output = c.reserve_output(self.root)
        with self.assertRaisesRegex(c.CandidateError, 'changed while staging'): c.build(self.root, output, report, runner=self.compiler)
        self.assertEqual((self.root / 'frontend/app.js').read_text(), 'changed later')
        self.assertEqual(json.loads((output / 'build.json').read_bytes())['status'], 'failed')
    def test_linked_inputs_are_rejected_instead_of_copying_untracked_resources(self):
        if os.name == 'nt':
            # Directory junctions require no symlink privilege and must be
            # rejected before any contents are traversed.
            subprocess.run(['cmd.exe', '/c', 'mklink', '/J', str(self.root / 'backend/link'), str(self.root / 'frontend')],
                           check=True, capture_output=True)
        else: (self.root / 'backend/link.py').symlink_to(self.root / 'backend/server.py')
        report = self.report(); self.assertFalse(report['ok']); self.assertTrue(any('Linked build input' in e for e in report['errors']))
    def test_success_records_exact_snapshot_output_hash_environment_and_no_native_pass(self):
        report = self.report(); output = c.reserve_output(self.root); result = c.build(self.root, output, report, runner=self.compiler)
        self.assertEqual(result['status'], 'built_unverified'); self.assertEqual(result['native_verification'], 'not_run')
        self.assertEqual(result['artifact']['sha256'], c.digest(output / 'artifact/Filmocity.exe'))
        self.assertEqual(json.loads((output / 'inputs/build-info.json').read_bytes())['id'], result['id'])
        args, options = self.calls[-1]; self.assertEqual(options['cwd'], output / 'inputs'); self.assertEqual(options['env']['PYINSTALLER_CONFIG_DIR'], str(output / 'cache'))
        self.assertEqual(args[args.index('--python-option') + 1], 'X utf8=1'); self.assertIn('--windowed', args)
        self.assertIn('json', args); self.assertNotIn('project_sync', args); self.assertTrue(any(str(output / 'inputs/backend') + ':backend' == a for a in args))
        self.assertNotIn(str(self.root / 'backend') + ':backend', args)
    def test_live_source_edits_after_staging_cannot_change_the_compiler_inputs(self):
        report = self.report(); output = c.reserve_output(self.root)
        def compiler(*a, **kw):
            (self.root / 'frontend/app.js').write_text('later working edit'); return self.compiler(*a, **kw)
        result = c.build(self.root, output, report, runner=compiler)
        self.assertEqual(result['status'], 'built_unverified'); self.assertEqual((output / 'inputs/frontend/app.js').read_text(), '/* source */')
    def test_modified_staged_source_tool_resource_or_identity_prevents_a_success_receipt(self):
        for relative in ['frontend/app.js', 'bin/ffmpeg.exe', 'bin/avcodec.dll', 'build-info.json']:
            with self.subTest(relative=relative):
                report = self.report(); output = c.reserve_output(self.root)
                def compiler(*a, **kw):
                    result = self.compiler(*a, **kw); (output / 'inputs' / relative).write_bytes(b'changed'); return result
                with self.assertRaises((c.CandidateError, ValueError)): c.build(self.root, output, report, runner=compiler)
                self.assertEqual(json.loads((output / 'build.json').read_bytes())['status'], 'failed')
    def test_compiler_failure_or_missing_output_preserves_failure_evidence(self):
        for code in [7, 0]:
            report = self.report(); output = c.reserve_output(self.root)
            with self.assertRaises((c.CandidateError, OSError)): c.build(self.root, output, report, runner=lambda *a, **k: SimpleNamespace(returncode=code))
            receipt = json.loads((output / 'build.json').read_bytes()); self.assertEqual(receipt['status'], 'failed'); self.assertIsNone(receipt['artifact']); self.assertTrue((output / 'build.log').exists())
    def test_cannot_reuse_a_candidate_directory_or_build_failed_preflight(self):
        output = c.reserve_output(self.root); existing = output / 'build.json'; existing.write_bytes(b'existing evidence')
        with self.assertRaises(c.CandidateError): c.build(self.root, output, self.report(), runner=self.compiler)
        self.assertEqual(existing.read_bytes(), b'existing evidence')
        with self.assertRaises(c.CandidateError): c.build(self.root, output, {**self.report(), 'ok': False}, runner=self.compiler)


if __name__ == '__main__': unittest.main(verbosity=2)
