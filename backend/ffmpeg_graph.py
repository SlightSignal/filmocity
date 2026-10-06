"""File-backed filter graphs with a probed, executable-specific CLI contract.

Recent FFmpeg removed -filter_complex_script in favour of -/filter_complex.
Probe a tiny independent graph, never retry an export after it has begun.
"""
from collections import OrderedDict
import os
import shutil
import subprocess
import threading
import time

from work_budget import work

FILE_OPTIONS = ('-/filter_complex', '-filter_complex_script')
_CACHE = OrderedDict()
_LOCK = threading.Lock()


def _identity(executable):
    path = shutil.which(os.fspath(executable)) or os.fspath(executable)
    path = os.path.realpath(path)
    info = os.stat(path)
    return path, info.st_size, info.st_mtime_ns, info.st_ctime_ns


def _accepts(executable, option, graph, context):
    errors = context.new_file('.graph-probe-log')
    command = [executable, '-v', 'error', '-nostdin', '-f', 'lavfi', '-i',
               'anullsrc=r=8000:cl=mono', option, graph, '-map', '[a]',
               '-t', '0.001', '-f', 'null', '-']
    flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    with work(context.holder, 'probe', check=context.check_cancelled):
        with open(errors, 'wb') as stderr:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                     stdout=subprocess.DEVNULL, stderr=stderr, **flags)
            context.holder['proc'] = child
            try:
                deadline = time.monotonic() + 10
                while child.poll() is None:
                    context.check_cancelled()
                    if time.monotonic() >= deadline: raise ValueError('FFmpeg graph capability probe timed out')
                    if os.path.getsize(errors) > 65536: raise ValueError('FFmpeg graph capability diagnostics exceeded their limit')
                    time.sleep(.02)
                context.check_cancelled()
                if os.path.getsize(errors) > 65536: raise ValueError('FFmpeg graph capability diagnostics exceeded their limit')
                return child.returncode == 0
            finally:
                if child.poll() is None: child.kill()
                child.wait()
                if context.holder.get('proc') is child: context.holder.pop('proc', None)


def file_option(executable, context):
    """Cache only demonstrated support; invalidate when the binary changes."""
    context.check_cancelled()
    identity = _identity(executable)
    with _LOCK:
        if identity in _CACHE:
            _CACHE.move_to_end(identity)
            return _CACHE[identity]
    graph = context.write_text('[0:a]anull[a]', '.ffgraph')
    for option in FILE_OPTIONS:
        if _accepts(identity[0], option, graph, context):
            with _LOCK:
                _CACHE[identity] = option
                if len(_CACHE) > 64: _CACHE.popitem(last=False)
            return option
    raise ValueError('This FFmpeg supports neither file-backed filter graph option')


def externalize(command, context):
    """Keep small graphs inline; own large UTF-8 graphs outside argv limits."""
    if '-filter_complex' not in command: return command
    index = command.index('-filter_complex')
    if len(command[index + 1]) <= 4096: return command
    option = file_option(command[0], context)
    graph = context.write_text(command[index + 1], '.ffgraph')
    result = list(command)
    result[index:index + 2] = [option, graph]
    return result
