"""Leases for generated segment files: readers/writers exclude cleanup.

These locks coordinate this server process. The workspace lock excludes other
Filmocity instances; external directory replacement is not an authenticated FS.
Only canonical completed segment names are candidates for manual/size cleanup.
"""
from contextlib import contextmanager
import os
from pathlib import Path
import re
import stat
import threading

LIMIT_BYTES = 4_000_000_000
_CONDITION = threading.Condition(threading.RLock())
_READERS, _CLEANERS, _WRITERS = {}, set(), set()


class CacheBusy(ValueError): pass


from cache_paths import linked


def validate(directory):
    directory=Path(directory)
    if any(linked(p) for p in (directory.parent,directory)): raise ValueError('Linked segment cache directories are not supported')
    return directory


def identity(directory): return os.path.normcase(os.path.realpath(directory))


@contextmanager
def reading(directory, check=lambda:None):
    validate(directory);key=identity(directory)
    with _CONDITION:
        while key in _CLEANERS: check();_CONDITION.wait(.05)
        check();validate(directory);_READERS[key]=_READERS.get(key,0)+1
    try: yield
    finally:
        with _CONDITION:
            _READERS[key]-=1
            if not _READERS[key]: del _READERS[key]
            _CONDITION.notify_all()


@contextmanager
def writing(directory, key, check=lambda:None):
    if not re.fullmatch('[a-f0-9]{32}',key): raise ValueError('Invalid segment key')
    # Public writer scopes own a reader lease too, so they cannot bypass cleanup
    # exclusion. A full export keeps its outer lease through the final concat.
    with reading(directory,check):
        token=(identity(directory),key)
        with _CONDITION:
            while token in _WRITERS:check();_CONDITION.wait(.05)
            check();_WRITERS.add(token)
        try:yield
        finally:
            with _CONDITION:_WRITERS.remove(token);_CONDITION.notify_all()


@contextmanager
def cleaning(directory):
    validate(directory);key=identity(directory)
    with _CONDITION:
        if key in _CLEANERS or _READERS.get(key):raise CacheBusy('Segment cache is in use. Wait for its render or preview work to finish before clearing it.')
        _CLEANERS.add(key)
    try:yield
    finally:
        with _CONDITION:_CLEANERS.remove(key);_CONDITION.notify_all()


def usable(path):
    path=Path(path);validate(path.parent)
    try:info=path.lstat()
    except FileNotFoundError:return False
    if linked(path,info) or not stat.S_ISREG(info.st_mode):raise ValueError('Segment cache entry is not a regular unlinked file')
    return info.st_size>0


def report():return {'category':'segments','removed':[],'skipped':[],'errors':[],'bytes_removed':0,'status':'complete'}


def _cleanup(directory, limit):
    directory=validate(directory);result=report();entries=[]
    if not directory.exists():return result
    for path in directory.iterdir():
        try:
            info=path.lstat()
            if not re.fullmatch('[a-f0-9]{32}\\.mp4',path.name) or linked(path,info) or not stat.S_ISREG(info.st_mode):
                result['skipped'].append(path.name);continue
            entries.append((info.st_mtime_ns,path.name,info.st_size,path))
        except OSError as error:result['errors'].append({'file':path.name,'message':str(error)})
    size=sum(row[2] for row in entries);result['managed_bytes_before']=size
    for _,name,length,path in sorted(entries):
        if limit is not None and size<=limit:break
        try:
            validate(directory)
            info=path.lstat()
            if linked(path,info) or not stat.S_ISREG(info.st_mode):
                result['skipped'].append(name);continue
            path.unlink();result['removed'].append(name);result['bytes_removed']+=info.st_size;size-=length
        except (OSError,ValueError) as error:result['errors'].append({'file':name,'message':str(error)})
    result['managed_bytes_after']=size
    if result['errors'] or result['skipped']:result['status']='partial'
    return result


def cleanup(directory, *, limit=None):
    if limit is not None and (type(limit) is not int or limit<0):raise ValueError('Cache retention bytes must be a nonnegative integer')
    result=report()
    try:
        with cleaning(directory):return _cleanup(directory,limit)
    except CacheBusy as error:result.update(status='deferred',reason=str(error))
    except (OSError,ValueError) as error:result.update(status='partial',errors=[{'file':'renders/cache','message':str(error)}])
    return result


def state(directory):
    key=identity(directory)
    with _CONDITION:return {'read_leases':_READERS.get(key,0),'cleaning':key in _CLEANERS,'writers':sum(k==key for k,_ in _WRITERS),'retention_bytes':LIMIT_BYTES}
