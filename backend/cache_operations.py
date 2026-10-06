"""Bounded cache maintenance off the event loop, with an owned final receipt.

One manual cleanup and one size scan may run at a time. Request cancellation
waits for an admitted cleanup to finish; it cannot undo deletions. The latest
receipt is process-local and workspace-bound, not durable task history.
"""
import asyncio
import copy
import os
from pathlib import Path
import stat
import threading
import time
import uuid

from cache_paths import linked
import media_cache
import segment_cache

_CLEANUP = threading.BoundedSemaphore(1)
_SCAN = threading.BoundedSemaphore(1)
_STATE = threading.Lock()
_active = _last = None


class CacheBusy(ValueError): pass


def identity(root): return os.path.normcase(os.path.realpath(root))


def activity(root):
    key = identity(root)
    with _STATE:
        return {'cleanup_active': copy.deepcopy(_active['receipt']) if _active and _active['root'] == key else None,
                'last_cleanup': copy.deepcopy(_last['receipt']) if _last and _last['root'] == key else None}


def _size(root, relative, exclude=None):
    directory = Path(root) / relative
    result = {'bytes': 0, 'files': 0, 'errors': [], 'skipped': [], 'complete': True}
    def error(exc): result['errors'].append(str(exc))
    try:
        if any(linked(path) for path in (Path(root), directory.parent, directory)):
            result['skipped'].append(relative)
        else:
            try: info = directory.lstat()
            except FileNotFoundError: return result
            if not stat.S_ISDIR(info.st_mode): raise OSError('Cache path is not a directory: ' + str(directory))
            for folder, dirs, files in os.walk(directory, followlinks=False, onerror=error):
                kept = []
                for name in dirs:
                    path = Path(folder) / name
                    if exclude is not None and path == directory / exclude: continue
                    try:
                        if linked(path): result['skipped'].append(path.relative_to(root).as_posix())
                        else: kept.append(name)
                    except OSError as exc: error(exc)
                dirs[:] = kept
                for name in files:
                    path = Path(folder) / name
                    try:
                        info = path.lstat()
                        if linked(path, info): result['skipped'].append(path.relative_to(root).as_posix())
                        elif stat.S_ISREG(info.st_mode): result['bytes'] += info.st_size; result['files'] += 1
                        else: result['skipped'].append(path.relative_to(root).as_posix())
                    except OSError as exc: error(exc)
    except OSError as exc: error(exc)
    # A never-created cache is empty; a directory that cannot be read is unknown.
    result['complete'] = not (result['errors'] or result['skipped'])
    return result


def inspect(root, *, include_receipt=True):
    if not _SCAN.acquire(blocking=False): raise CacheBusy('A cache size scan is already running. Try again shortly.')
    try:
        reports = {name: _size(root, folder, exclude) for name, folder, exclude in (
            ('proxies', 'proxies', None), ('thumbs', 'thumbs', None),
            ('segments', 'renders/cache', None), ('renders', 'renders', 'cache'), ('sfx', 'sfx', None))}
        return {**{name + '_mb': round(row['bytes'] / 1e6, 1) if row['complete'] else None for name, row in reports.items()},
                'size_reports': reports, 'segment_activity': segment_cache.state(Path(root) / 'renders/cache'),
                **(activity(root) if include_receipt else {})}
    finally: _SCAN.release()


def _clear_and_scan(root, what, receipt):
    global _last
    kinds = [kind for kind in ('proxies', 'thumbs') if what in (kind, 'all')]
    reports, cleared, failure, busy = [], [], None, None
    try:
        reports.extend(media_cache.clear_many(root, kinds))
        cleared.extend(row['category'] for row in reports if not row['errors'] and not row['skipped'])
        if what in ('segments', 'all'):
            row = segment_cache.cleanup(Path(root) / 'renders/cache'); reports.append(row)
            if row['status'] == 'complete': cleared.append('segments')
    except media_cache.CacheBusy as exc: busy = str(exc)
    except Exception as exc: failure = str(exc) or type(exc).__name__
    result = {**receipt, 'finished': time.time(), 'cleared': cleared, 'reports': reports}
    if busy: result.update(status='not_started', operation_error=busy)
    elif failure: result.update(status='incomplete', operation_error=failure)
    # Keep deletion results before the optional scan, which may fail separately.
    with _STATE: _last = {'root': identity(root), 'receipt': copy.deepcopy(result)}
    if busy: raise CacheBusy(busy)
    try: result.update(inspect(root, include_receipt=False))
    except (OSError, ValueError) as exc: result['inventory_error'] = str(exc)
    return result


async def clear(root, what):
    """Own the worker to completion even if its HTTP waiter is cancelled twice."""
    global _active
    if what not in ('segments', 'proxies', 'thumbs', 'all'): raise ValueError('Choose a known cache category')
    root = os.fspath(root); key = identity(root)
    receipt = {'id': uuid.uuid4().hex, 'category': what, 'started': time.time()}
    if not _CLEANUP.acquire(blocking=False): raise CacheBusy('A cache cleanup is already running. Refresh its result before trying again.')
    try:
        with _STATE: _active = {'root': key, 'receipt': receipt}
        # A private executor Future is not independently cancelled by task-group
        # cancellation; only this coroutine owns it and releases the admission.
        future = asyncio.get_running_loop().run_in_executor(None, _clear_and_scan, root, what, receipt)
        try:
            result = await asyncio.shield(future)
            result['cleanup_active'] = None
            return result
        except asyncio.CancelledError:
            while not future.done():
                try: await asyncio.shield(future)
                except asyncio.CancelledError: continue
                except Exception: break
            if not future.cancelled(): future.exception()
            raise
    finally:
        with _STATE: _active = None
        _CLEANUP.release()
