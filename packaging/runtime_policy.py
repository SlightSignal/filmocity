"""Declared Windows runtime admission; standard library only, no installation."""
import json
from pathlib import Path
import platform
import re
import struct
import subprocess
import sys
import sysconfig


class RuntimePolicyError(RuntimeError):
    pass


def load_policy(path=None):
    path = Path(path) if path is not None else Path(__file__).with_name('runtime-windows.json')
    try:
        policy = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as error:
        raise RuntimePolicyError(f'Cannot read the declared Windows runtime: {error}') from error
    if not isinstance(policy, dict) or policy.get('schema') != 'filmocity-windows-runtime/v1' or \
            policy.get('implementation') != 'CPython' or policy.get('architecture') != 'x64' or policy.get('gil') != 'standard' or \
            not isinstance(policy.get('minimum_version'), str) or not re.fullmatch(r'3\.[0-9]+\.[0-9]+', policy['minimum_version']):
        raise RuntimePolicyError('Invalid Windows runtime policy; restore packaging/runtime-windows.json from the reviewed source.')
    return policy


def requirement(policy):
    version = policy['minimum_version']
    branch = '.'.join(version.split('.')[:2])
    return f'CPython {version} or a newer security patch on Python {branch} (standard GIL, x64)'


def current_runtime():
    return {'python': '.'.join(str(value) for value in sys.version_info[:3]),
            'implementation': platform.python_implementation(),
            'releaselevel': getattr(sys.version_info, 'releaselevel', 'final'),
            'bits': struct.calcsize('P') * 8, 'machine': platform.machine(),
            'gil_disabled': bool(sysconfig.get_config_var('Py_GIL_DISABLED')),
            'gil_enabled': getattr(sys, '_is_gil_enabled', lambda: True)()}


def validate_runtime(runtime, policy):
    floor = tuple(int(value) for value in policy['minimum_version'].split('.'))
    valid = isinstance(runtime, dict)
    version = runtime.get('python', '') if valid else ''
    valid = valid and isinstance(version, str) and re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+', version) is not None
    parsed = tuple(int(value) for value in version.split('.')) if valid else ()
    valid = valid and parsed[:2] == floor[:2] and parsed >= floor and \
        runtime.get('implementation') == 'CPython' and runtime.get('releaselevel') == 'final' and \
        runtime.get('bits') == 64 and isinstance(runtime.get('machine'), str) and runtime['machine'].lower() in ('amd64', 'x86_64') and \
        runtime.get('gil_disabled') is False and runtime.get('gil_enabled') is True
    if not valid:
        found = f"{runtime.get('implementation', 'unknown')} {version or 'unknown'}; bits={runtime.get('bits', 'unknown')}; machine={runtime.get('machine', 'unknown')}" if isinstance(runtime, dict) else 'unreadable interpreter identity'
        raise RuntimePolicyError(f'The reviewed Windows runtime requires {requirement(policy)}; found {found}.')
    return runtime


# Executed in an isolated interpreter: never import project, user/site modules,
# create a library, install packages, or rely on a venv directory's name.
_PROBE = "import json,platform,struct,sys,sysconfig; print(json.dumps({'python':'.'.join(str(v) for v in sys.version_info[:3]),'implementation':platform.python_implementation(),'releaselevel':sys.version_info.releaselevel,'bits':struct.calcsize('P')*8,'machine':platform.machine(),'gil_disabled':bool(sysconfig.get_config_var('Py_GIL_DISABLED')),'gil_enabled':getattr(sys,'_is_gil_enabled',lambda:True)()}))"


def probe_interpreter(executable, policy, *, runner=None):
    options = {'capture_output': True, 'text': True, 'encoding': 'utf-8', 'timeout': 15}
    if sys.platform == 'win32': options['creationflags'] = subprocess.CREATE_NO_WINDOW
    try:
        result = (runner or subprocess.run)([str(executable), '-I', '-S', '-B', '-c', _PROBE], **options)
        if result.returncode or len(result.stdout) > 65536:
            raise RuntimePolicyError('The environment interpreter did not return a bounded runtime identity.')
        identity = json.loads(result.stdout)
    except (OSError, subprocess.SubprocessError, ValueError, TypeError) as error:
        raise RuntimePolicyError(f'Cannot verify the environment interpreter {executable}: {error}') from error
    return validate_runtime(identity, policy)


def main():
    try:
        validate_runtime(current_runtime(), load_policy())
        return 0
    except RuntimePolicyError as error:
        if sys.stderr is not None:
            print(str(error), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
