"""Isolated, reviewable Windows build inputs and provenance (standard library only)."""
import ast
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import platform
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from runtime_policy import current_runtime, load_policy, validate_runtime, RuntimePolicyError

DATA = ('backend', 'frontend', 'assets', 'docs', 'licenses')
INPUT_DIRS = (*DATA, 'packaging', 'launcher', 'agent', 'tests', 'benchmarks', '.github')
ROOT_FILES = ('LICENSE', 'README.md', 'CHANGELOG.md', 'THIRD_PARTY_NOTICES.md',
              'requirements.txt', 'requirements-build.txt', 'requirements-bootstrap-windows.lock',
              'requirements-runtime-windows.lock', 'requirements-build-windows.lock', 'requirements-test-windows.lock',
              '.gitignore', '.gitattributes', 'Filmocity.bat', 'Filmocity (no console).vbs', 'Install Filmocity.bat',
              'Filmocity.command', 'Install Filmocity.command', 'filmocity.sh', 'install.sh')
PACKAGES = {'PyInstaller': 'pyinstaller', 'webview': 'pywebview', 'fastapi': 'fastapi', 'uvicorn': 'uvicorn',
            'starlette': 'starlette', 'websockets': 'websockets', 'multipart': 'python-multipart', 'PIL': 'Pillow'}
COLLECT = ('uvicorn', 'fastapi', 'starlette', 'websockets', 'multipart', 'PIL', 'webview')
HIDDEN = ('uvicorn.logging', 'uvicorn.loops.auto', 'uvicorn.loops.asyncio', 'uvicorn.protocols.http.auto',
          'uvicorn.protocols.http.h11_impl', 'uvicorn.protocols.websockets.auto',
          'uvicorn.protocols.websockets.websockets_impl', 'uvicorn.lifespan.on', 'uvicorn.lifespan.off')


class CandidateError(RuntimeError): pass


def command_path(path):
    """Native CLI parsers need drive/UNC paths, not the Win32 device prefix."""
    value = os.fspath(path)
    if os.name == 'nt':
        if value.startswith('\\\\?\\UNC\\'): value = '\\\\' + value[8:]
        elif value.startswith('\\\\?\\'): value = value[4:]
    return value


def filesystem_path(path):
    """Use extended paths for *all* filesystem operations on Windows.

    Path.rglob on ordinary Windows paths can silently lose deep directories.
    Keep this representation internal: PyInstaller's SOURCE:DEST parser cannot
    interpret a prefixed drive colon, even when the source exists.
    """
    value = os.path.abspath(os.path.expanduser(command_path(path)))
    if os.name == 'nt':
        value = ('\\\\?\\UNC\\' + value[2:]) if value.startswith('\\\\') else '\\\\?\\' + value
    return Path(value)


def linked(path):
    # Reject every reparse point, including junctions on Python 3.11.
    info = path.lstat()
    return path.is_symlink() or bool(getattr(info, 'st_file_attributes', 0) & 0x400)


def walk_files(base, *, exclude=lambda relative: False):
    """Enumerate explicitly; directory read failures must invalidate a build."""
    base = filesystem_path(base)
    if linked(base) or not base.is_dir(): raise CandidateError(f'Missing or linked build input: {command_path(base)}')
    def visit(directory):
        # Unlike rglob/os.walk, no inaccessible-directory error is suppressed.
        with os.scandir(directory) as entries:
            paths = sorted((Path(entry.path) for entry in entries), key=lambda p: p.name)
        for path in paths:
            relative = path.relative_to(base)
            if exclude(relative): continue
            if linked(path): raise CandidateError(f'Linked build input must be made explicit: {command_path(path)}')
            if path.is_dir(): yield from visit(path)
            elif path.is_file(): yield path
            else: raise CandidateError(f'Unsupported build input: {command_path(path)}')
    yield from visit(base)


def digest(path):
    value = hashlib.sha256()
    with filesystem_path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''): value.update(chunk)
    return value.hexdigest()


def atomic_json(path, document):
    path = filesystem_path(path); temporary = path.with_name('.' + path.name + '-' + uuid.uuid4().hex)
    try:
        with temporary.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(document, stream, indent=2, ensure_ascii=False, allow_nan=False)
            stream.write('\n'); stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary.exists(): temporary.unlink()


def pe_machine(path):
    with filesystem_path(path).open('rb') as stream:
        header = stream.read(64)
        if len(header) != 64 or header[:2] != b'MZ': raise CandidateError(f'Not a Windows executable: {path}')
        stream.seek(struct.unpack_from('<I', header, 60)[0]); pe = stream.read(6)
        if len(pe) != 6 or pe[:4] != b'PE\0\0': raise CandidateError(f'Invalid PE header: {path}')
        return {0x8664: 'x64', 0x14c: 'x86', 0xaa64: 'arm64'}.get(struct.unpack_from('<H', pe, 4)[0], 'unknown')


def source_files(root):
    root = filesystem_path(root); result = []
    if linked(root): raise CandidateError(f'Missing or linked build input: {command_path(root)}')
    for name in INPUT_DIRS:
        base = root / name
        if name == 'benchmarks':
            manifest = root / 'packaging/release-inputs.json'
            if linked(manifest): raise CandidateError('Release input manifest must not be linked')
            declared = json.loads(manifest.read_text(encoding='utf-8'))
            selected = declared.get('benchmarks')
            if declared.get('schema') != 'filmocity-release-inputs/v1' or not isinstance(selected, list):
                raise CandidateError('Invalid release input manifest')
            if any(not isinstance(value, str) for value in selected) or len(set(selected)) != len(selected):
                raise CandidateError('Duplicate or invalid release inputs')
            for value in selected:
                parts = value.split('/')
                if len(parts) < 2 or parts[0] != 'benchmarks' or any(not p or p.startswith('.') or ':' in p or '\\' in p for p in parts):
                    raise CandidateError('Benchmark input must be a plain relative file path')
                current = root
                for part in parts:
                    current = current / part
                    if linked(current): raise CandidateError(f'Linked build input: {command_path(current)}')
                if not current.is_file(): raise CandidateError(f'Missing release input: {value}')
                result.append(value)
            continue
        def excluded(relative):
            return any(part.startswith('.') or part == '__pycache__' for part in relative.parts)
        for path in walk_files(base, exclude=excluded):
            relative = path.relative_to(root)
            if path.suffix in ('.pyc', '.pyo', '.log'): continue
            if relative.as_posix() == 'launcher/installed.json' or (name == 'packaging' and path.suffix == '.spec'): continue
            result.append(relative.as_posix())
    for name in ROOT_FILES:
        path = root / name
        if not path.is_file() or linked(path): raise CandidateError(f'Missing or linked build input: {command_path(path)}')
        result.append(name)
    return sorted(result)


def package_versions():
    packages = {d.metadata['Name']: d.version for d in importlib.metadata.distributions() if d.metadata.get('Name')}
    errors = []
    for module, distribution in PACKAGES.items():
        try:
            if importlib.util.find_spec(module) is None: raise ImportError(module)
            packages[distribution] = importlib.metadata.version(distribution)
        except (ImportError, importlib.metadata.PackageNotFoundError, ValueError) as error:
            errors.append(f'Missing build dependency {distribution}: {error}')
    return packages, errors


def check_dependency_lock(root, versions, host):
    errors = []
    try:
        policy_path = root / 'packaging/runtime-windows.json'
        if linked(policy_path): raise CandidateError('Windows runtime policy must not be linked')
        validate_runtime(host, load_policy(policy_path))
    except (CandidateError, RuntimePolicyError, OSError) as error: errors.append(str(error))
    installed = {re.sub(r'[-_.]+', '-', name).lower(): version for name, version in versions.items()}
    lock = root / 'requirements-build-windows.lock'
    try:
        if linked(lock): raise CandidateError('Dependency lock must not be linked')
        lines = lock.read_text(encoding='utf-8').splitlines()
        declared = {}
        for line in lines:
            if not line.strip() or line.lstrip().startswith('#'): continue
            match = re.fullmatch(r'([A-Za-z0-9_.-]+)==([^\s]+) --hash=sha256:([0-9a-f]{64})', line)
            if not match: raise CandidateError('Invalid hash-locked build requirement')
            name, version = match.group(1), match.group(2)
            name = re.sub(r'[-_.]+', '-', name).lower()
            if name in declared: raise CandidateError('Duplicate locked build requirement')
            declared[name] = version
            if installed.get(name) != version:
                errors.append(f'Build dependency {name} must match the reviewed lock ({version}); found {installed.get(name, "missing")}')
        if not declared: raise CandidateError('Empty dependency lock')
    except (CandidateError, OSError) as error: errors.append(str(error))
    return errors


def preflight(root, ffmpeg_dir=None, *, host=None, packages=None, runner=subprocess.run):
    root = filesystem_path(root); binaries = filesystem_path(ffmpeg_dir or root / 'bin')
    errors = []; host = host or {**current_runtime(), 'system': platform.system(), 'executable': sys.executable}
    if host['system'] != 'Windows': errors.append('Build this executable on Windows; PyInstaller does not cross-compile this application.')
    if host['bits'] != 64 or host['machine'].lower() not in ('amd64', 'x86_64'): errors.append('This candidate recipe targets Windows x64 with a 64-bit x64 Python.')
    try:
        if tuple(int(n) for n in host['python'].split('.')[:2]) < (3, 11):
            errors.append('Python 3.11 or newer is required by the runtime.')
    except (KeyError, ValueError):
        errors.append('Cannot interpret the build Python version.')
    versions, dependency_errors = packages() if packages else package_versions(); errors.extend(dependency_errors)
    locked_errors = check_dependency_lock(root, versions, host); errors.extend(locked_errors)
    for name, floor in [('pyinstaller', 6), ('pywebview', 5)]:
        if name in versions:
            try:
                if int(versions[name].split('.')[0]) < floor: errors.append(f'{name} {floor} or newer is required for this build recipe.')
            except ValueError: errors.append(f'Cannot interpret installed {name} version: {versions[name]}')
    inputs = []
    try:
        for relative in source_files(root):
            path = root / relative; inputs.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': digest(path)})
        for record in inputs:
            if record['path'].endswith('.py'):
                try: ast.parse((root / record['path']).read_text(encoding='utf-8'), filename=record['path'])
                except (SyntaxError, UnicodeError) as error: errors.append(f"Invalid Python input {record['path']}: {error}")
        for required in ('backend/server.py', 'backend/runtime_identity.py', 'backend/workspace_lock.py', 'backend/render.py', 'frontend/index.html', 'packaging/filmocity_app.py', 'packaging/windows_lifetime.py', 'packaging/runtime_policy.py', 'packaging/runtime-windows.json'):
            if not (root / required).is_file(): errors.append(f'Required runtime file is missing: {required}')
        index = root / 'frontend/index.html'
        if index.is_file():
            for asset in re.findall(r'(?:src|href)="/static/([^"?]+)(?:\?[^"\s]+)?"', index.read_text(encoding='utf-8')):
                if not (root / 'frontend' / asset).is_file(): errors.append(f'Frontend references a missing file: {asset}')
    except (CandidateError, OSError, ValueError) as error: errors.append(str(error))
    tools = {}
    for name in ('ffmpeg.exe', 'ffprobe.exe'):
        path = binaries / name
        try:
            if linked(binaries) or linked(path): raise CandidateError(f'Linked binary input: {command_path(path)}')
            architecture = pe_machine(path)
            if architecture != 'x64': raise CandidateError(f'{name} must be a Windows x64 binary; found {architecture}')
            tools[name] = {'path': command_path(path), 'sha256': digest(path), 'bytes': path.stat().st_size, 'architecture': architecture}
            if host['system'] == 'Windows' and not locked_errors:
                result = runner([command_path(path), '-version'], capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=20)
                if result.returncode: raise CandidateError(f'{name} did not run successfully (exit {result.returncode})')
                tools[name]['version'] = result.stdout.splitlines()[0] if result.stdout else ''
                if not tools[name]['version'].startswith(name[:-4] + ' version '): raise CandidateError(f'{name} returned an unexpected version response')
        except (CandidateError, OSError, subprocess.SubprocessError) as error: errors.append(str(error))
    # Carry supplied DLLs and notices with the selected binary distribution.
    notices = []
    if binaries.is_dir():
        try:
            for path in walk_files(binaries):
                if path.name not in ('ffmpeg.exe', 'ffprobe.exe'):
                    relative = path.relative_to(binaries).as_posix()
                    if path.suffix.lower() in ('.dll', '.txt', '.md', '.html') or path.name.upper().startswith(('LICENSE', 'COPYING', 'NOTICE')):
                        notices.append({'path': relative, 'bytes': path.stat().st_size, 'sha256': digest(path)})
        except (CandidateError, OSError) as error: errors.append(str(error))
    source_hash = hashlib.sha256(json.dumps(inputs, sort_keys=True, separators=(',', ':')).encode()).hexdigest()
    return {'ok': not errors, 'errors': errors, 'host': host, 'packages': versions, 'inputs': inputs,
            'source_sha256': source_hash, 'tools': tools, 'binary_resources': notices, 'ffmpeg_directory': command_path(binaries)}


def reserve_output(root, output=None):
    root = filesystem_path(root).resolve()
    path = filesystem_path(output).resolve() if output else root / 'dist/candidates' / (time.strftime('%Y%m%d-%H%M%S', time.gmtime()) + '-' + uuid.uuid4().hex[:8])
    for name in (*INPUT_DIRS, 'bin', '.venv'):
        blocked = root / name
        if path == blocked or blocked in path.parents: raise CandidateError(f'Build output cannot be inside the input tree: {blocked}')
    if path == root or path in root.parents: raise CandidateError('Choose a new candidate directory outside the source inputs.')
    path.parent.mkdir(parents=True, exist_ok=True)
    path.mkdir(exist_ok=False)
    return Path(command_path(path))


def copy_verified(source, destination, expected):
    source, destination = filesystem_path(source), filesystem_path(destination)
    if linked(source): raise CandidateError(f'Build input became a link: {command_path(source)}')
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open('rb') as reader, destination.open('xb') as writer:
        shutil.copyfileobj(reader, writer)
    if digest(destination) != expected or digest(source) != expected: raise CandidateError(f'Build input changed while staging: {source}')


def stage_inputs(root, output, report, build_id):
    root, output = filesystem_path(root), filesystem_path(output); stage = output / 'inputs'; stage.mkdir()
    for record in report['inputs']: copy_verified(root / record['path'], stage / record['path'], record['sha256'])
    for name, record in report['tools'].items(): copy_verified(Path(record['path']), stage / 'bin' / name, record['sha256'])
    for record in report['binary_resources']:
        copy_verified(Path(report['ffmpeg_directory']) / record['path'], stage / 'bin' / record['path'], record['sha256'])
    atomic_json(stage / 'build-info.json', {'id': build_id, 'source_sha256': report['source_sha256']})
    return Path(command_path(stage))


def standard_imports(stage):
    """Backend source is data, so collect its standard-library imports explicitly."""
    modules = set()
    for path in walk_files(filesystem_path(stage) / 'backend'):
        if path.suffix != '.py': continue
        for node in ast.walk(ast.parse(path.read_text(encoding='utf-8'), filename=str(path))):
            if isinstance(node, ast.Import): modules.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level: modules.add(node.module)
    return sorted(name for name in modules if name.split('.')[0] in sys.stdlib_module_names and importlib.util.find_spec(name) is not None)


def build_command(stage, output, *, python=sys.executable):
    stage, output = Path(command_path(stage)), Path(command_path(output))
    command = [python, '-m', 'PyInstaller', '--noconfirm', '--clean', '--onefile', '--name', 'Filmocity',
               '--distpath', str(output / 'artifact'), '--workpath', str(output / 'work'), '--specpath', str(output / 'spec'),
               '--log-level', 'WARN', '--noupx', '--python-option', 'X utf8=1']
    for name in (*DATA, 'bin'):
        command.extend(['--add-data', str(stage / name) + ':' + name])
    for name in ('build-info.json', 'LICENSE', 'THIRD_PARTY_NOTICES.md'):
        command.extend(['--add-data', str(stage / name) + ':.'])
    for name in COLLECT: command.extend(['--collect-all', name])
    for name in sorted(set(HIDDEN) | set(standard_imports(stage))): command.extend(['--hidden-import', name])
    return command + ['--windowed', str(stage / 'packaging/filmocity_app.py')]


def build(root, output, report, *, runner=subprocess.run):
    """Output must already be exclusively reserved. No preexisting output is replaced."""
    if not report['ok']: raise CandidateError('Preflight failed; no build was started.')
    root, output = filesystem_path(root), filesystem_path(output)
    if any(output.iterdir()): raise CandidateError('The reserved candidate directory must be empty; previous evidence is never overwritten.')
    build_id = uuid.uuid4().hex
    receipt = {'format': 1, 'id': build_id, 'status': 'preparing', 'created': time.time(), 'preflight': report,
               'native_verification': 'not_run', 'artifact': None}
    atomic_json(output / 'build.json', receipt)
    try:
        stage = stage_inputs(root, output, report, build_id)
        command = build_command(stage, output); receipt['command'] = command; receipt['status'] = 'building'
        # Record exact installed versions, including transitive build dependencies.
        receipt['environment'] = sorted({f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions() if d.metadata.get('Name')})
        atomic_json(output / 'build.json', receipt)
        build_env = dict(os.environ)
        build_env.update(PYINSTALLER_CONFIG_DIR=command_path(output / 'cache'), PYTHONPYCACHEPREFIX=command_path(output / 'pycache'), PYTHONUTF8='1')
        receipt['build_environment'] = {key: build_env[key] for key in ('PYINSTALLER_CONFIG_DIR', 'PYTHONPYCACHEPREFIX', 'PYTHONUTF8')}
        atomic_json(output / 'build.json', receipt)
        with (output / 'build.log').open('x', encoding='utf-8') as log:
            result = runner(command, cwd=Path(command_path(stage)), stdout=log, stderr=subprocess.STDOUT, env=build_env)
        if result.returncode: raise CandidateError(f'PyInstaller failed (exit {result.returncode}); see build.log.')
        for record in report['inputs']:
            if digest(stage / record['path']) != record['sha256']: raise CandidateError(f"Staged input changed during the build: {record['path']}")
        for name, record in report['tools'].items():
            if digest(stage / 'bin' / name) != record['sha256']: raise CandidateError(f'Staged tool changed during the build: {name}')
        for record in report['binary_resources']:
            if digest(stage / 'bin' / record['path']) != record['sha256']: raise CandidateError(f"Staged binary resource changed during the build: {record['path']}")
        if json.loads(filesystem_path(stage / 'build-info.json').read_bytes()) != {'id': build_id, 'source_sha256': report['source_sha256']}:
            raise CandidateError('Staged build identity changed during the build')
        executable = output / 'artifact/Filmocity.exe'
        if pe_machine(executable) != 'x64': raise CandidateError('Build did not produce a Windows x64 executable.')
        receipt.update(status='built_unverified', finished=time.time(), artifact={'path': 'artifact/Filmocity.exe', 'bytes': executable.stat().st_size, 'sha256': digest(executable)})
        atomic_json(output / 'build.json', receipt)
        return receipt
    except BaseException as error:
        receipt.update(status='failed', finished=time.time(), error=str(error)[:1000]); atomic_json(output / 'build.json', receipt)
        raise
