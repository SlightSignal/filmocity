"""Owned media-cache access and honest cleanup of regenerable files.

Readers prepare derivatives concurrently. Cleanup takes an exclusive lease and
never traverses linked directories or deletes active scratch directories.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import stat
import threading
from cache_paths import linked

_CONDITION = threading.Condition(threading.RLock())
_READERS, _WRITERS = {}, set()


class CacheBusy(ValueError): pass


def key(root): return os.path.normcase(os.path.realpath(root))


@contextmanager
def preparing(root, check=lambda: None):
    identity = key(root)
    with _CONDITION:
        while identity in _WRITERS:
            check(); _CONDITION.wait(.1)
        check(); _READERS[identity] = _READERS.get(identity, 0) + 1
    try: yield
    finally:
        with _CONDITION:
            _READERS[identity] -= 1
            if not _READERS[identity]: del _READERS[identity]
            _CONDITION.notify_all()


@contextmanager
def cleaning(root):
    identity = key(root)
    with _CONDITION:
        if identity in _WRITERS or _READERS.get(identity): raise CacheBusy('Media preparation is active. Cancel it in Tasks or wait before clearing this cache.')
        _WRITERS.add(identity)
    try: yield
    finally:
        with _CONDITION: _WRITERS.remove(identity); _CONDITION.notify_all()


def _clear(root, what):
    if what not in ('proxies','thumbs'): raise ValueError('Unsupported media cache')
    directory = Path(root) / what
    report = {'category': what, 'removed': [], 'skipped': [], 'errors': [], 'bytes_removed': 0}
    targets = [directory] + ([directory/'tpl'] if what == 'thumbs' else [])
    for folder in targets:
        try:
            if any(linked(p) for p in (directory,folder)):
                report['errors'].append({'file':folder.relative_to(root).as_posix(), 'message':'Linked cache directories are not cleared'});continue
            if not folder.exists(): continue
            entries = list(folder.iterdir())
        except OSError as error:
            report['errors'].append({'file':folder.relative_to(root).as_posix(), 'message':str(error)});continue
        for path in entries:
            name = path.relative_to(root).as_posix()
            try:
                st = path.lstat()
                if not stat.S_ISREG(st.st_mode) or linked(path,st):
                    if path != directory/'tpl': report['skipped'].append(name)
                    continue
                path.unlink();report['removed'].append(name);report['bytes_removed'] += st.st_size
            except OSError as error: report['errors'].append({'file':name,'message':str(error)})
    return report


def clear_many(root, categories):
    if not categories: return []
    if any(category not in ('proxies', 'thumbs') for category in categories): raise ValueError('Unsupported media cache')
    with cleaning(root): return [_clear(root, category) for category in categories]


def clear(root, what): return clear_many(root, [what])[0]
