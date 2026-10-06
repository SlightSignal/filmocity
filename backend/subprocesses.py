"""Native editor child processes must not open console windows or steal focus.

This adapter is local to the media engine; it does not monkeypatch Python or
change subprocess defaults elsewhere in the user's desktop session.
"""
import os
import subprocess as _subprocess


def _options(options, command=None):
    result = dict(options)
    cwd = getattr(command, 'cwd', None)
    if cwd is not None:
        if result.get('cwd') is not None and os.path.normcase(os.path.abspath(result['cwd'])) != os.path.normcase(os.path.abspath(cwd)):
            raise ValueError('Render command must use its owned working directory')
        result['cwd'] = cwd
    if os.name == 'nt':
        result['creationflags'] = result.get('creationflags', 0) | _subprocess.CREATE_NO_WINDOW
    return result


def Popen(command, **options):
    return _subprocess.Popen(command, **_options(options, command))


def run(command, **options):
    return _subprocess.run(command, **_options(options, command))


def check_output(command, **options):
    return _subprocess.check_output(command, **_options(options, command))


def __getattr__(name):
    return getattr(_subprocess, name)
