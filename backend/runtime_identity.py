"""Identify a launched backend without trusting an occupied port or version label."""
import json
import os
import logging
import sys
import time
import traceback
import urllib.error
import urllib.request

from project_sync import workspace_id as workspace_key


class StartupError(RuntimeError):
    pass


class ShutdownError(RuntimeError):
    pass


REQUEST_SHUTDOWN_GRACE = 5
BACKEND_SHUTDOWN_TIMEOUT = 20


def read_build_info(base):
    path = os.path.join(base, 'build-info.json')
    if not os.path.exists(path): return None
    with open(path, encoding='utf-8') as stream: result = json.load(stream)
    if not isinstance(result, dict) or not all(isinstance(result.get(key), str) and result[key] for key in ('id', 'source_sha256')):
        raise StartupError('The packaged build identity is unreadable. Rebuild this candidate.')
    return {key: result[key] for key in ('id', 'source_sha256')}


def wait_for_backend(url, *, instance, root, pid=None, alive=lambda: True, timeout=60,
                     opener=None, clock=time.monotonic, sleep=time.sleep):
    """Only accept the expected process, launch nonce and selected workspace."""
    opener = opener or urllib.request.urlopen
    deadline = clock() + timeout; other = False
    while clock() < deadline:
        if not alive(): raise StartupError('The new backend exited before it became ready. See filmocity.log or the startup output.')
        try:
            with opener(url + '/api/version', timeout=min(1, max(.01, deadline - clock()))) as response:
                raw = response.read(65537)
            if len(raw) > 65536: raise ValueError('Oversized readiness response')
            result = json.loads(raw)
            if (isinstance(result, dict) and result.get('app') == 'Filmocity' and result.get('instance') == instance
                    and result.get('workspace') == workspace_key(root) and (pid is None or result.get('pid') == pid)):
                if not alive(): raise StartupError('The new backend exited during startup.')
                return result
            other = True
        except (OSError, urllib.error.URLError, ValueError, TypeError):
            pass
        sleep(min(.1, max(0, deadline - clock())))
    detail = ' Another process answered at that address.' if other else ''
    raise StartupError(f'The new backend did not become ready at {url} within {timeout:g} seconds.{detail}')


def stop_server(server, thread, *, timeout=BACKEND_SHUTDOWN_TIMEOUT):
    """Drain requests and run lifespan cleanup before retiring the owned thread.

    Uvicorn's force_exit skips ASGI shutdown. Its finite graceful-request
    timeout must instead cancel connections and continue through worker cleanup.
    A failed retirement remains a failure with the actual thread stack logged;
    never force-exit Python or pretend a closed port proves worker retirement.
    """
    if timeout <= 0: raise ValueError('Shutdown timeout must be positive')
    server.should_exit = True
    thread.join(timeout)
    if thread.is_alive():
        ident = getattr(thread, 'ident', None)
        frame = sys._current_frames().get(ident)
        stack = ''.join(traceback.format_stack(frame)) if frame is not None else '(thread stack unavailable)'
        state = getattr(server, 'server_state', None)
        logging.getLogger(__name__).error('Backend shutdown deadline (%ss) exceeded; thread=%s ident=%s connections=%s requests=%s\n%s',
            timeout, getattr(thread, 'name', 'backend'), ident,
            len(getattr(state, 'connections', ())), len(getattr(state, 'tasks', ())), stack)
        raise ShutdownError('The backend did not stop within the shutdown deadline. Check the application log before reopening.')
    lifespan = getattr(server, 'lifespan', None)
    if getattr(lifespan, 'shutdown_failed', False):
        raise ShutdownError('Application cleanup failed while closing. Check the application log before reopening.')
