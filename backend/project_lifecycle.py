"""Contained project files and private preparation for guarded project actions.

The caller holds the project lock for validation and publication. Expensive copy
preparation runs through the single owned worker below, outside that lock.
"""
import asyncio
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from cache_paths import linked
from project_recovery import parse_project, RecoveryError
from project_transaction import write_atomic

MAX_PROJECT_BYTES = 32 * 1024 * 1024


class ProjectActionError(ValueError): pass
class ProjectActionBusy(ProjectActionError): pass


def project_file(root, identity):
    if (not isinstance(identity, str) or not identity or identity in ('.', '..')
            or any(c in identity for c in '/\\:\x00') or identity.rstrip(' .') != identity
            or any(ord(c) < 32 for c in identity)):
        raise ProjectActionError('Choose a project identity from the project list')
    folder = Path(root) / 'projects' / identity
    if any(linked(p) for p in (folder.parent, folder, folder/'project.json')):
        raise ProjectActionError('Linked project folders/files are not supported')
    return folder/'project.json'


def read(path):
    with open(path, 'rb') as stream: raw = stream.read(MAX_PROJECT_BYTES + 1)
    if len(raw) > MAX_PROJECT_BYTES: raise ProjectActionError('Project exceeds the 32 MiB action limit')
    return raw


def project_name(value, fallback):
    value = fallback if value is None else value
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 240 or any(ord(c)<32 for c in value):
        raise ProjectActionError('Enter a project name of 1–240 characters without control characters')
    return value.strip()


def catalog(root, active):
    folder = Path(root)/'projects'; result = []
    if linked(folder): raise ProjectActionError('Linked project libraries are not supported')
    for item in sorted(folder.iterdir()) if folder.is_dir() else []:
        if item.name.startswith('.') or not item.is_dir(): continue
        row = {'id': item.name, 'name': item.name, 'active': item.name == active, 'sequences': 0, 'media': 0, 'updated': None}
        try:
            path = project_file(root,item.name); raw = read(path)
            row['file_sha256'] = hashlib.sha256(raw).hexdigest()
            doc = parse_project(raw)
            row.update(name=str(doc.get('name') or item.name), sequences=len(doc['sequences']), media=len(doc['media']), updated=path.stat().st_mtime)
        except FileNotFoundError:
            if not (item/'backups').exists() and not (item/'project.json.tmp').exists(): continue
            row['error'] = 'Project file is missing; inspect Recovery for saved copies.'
        except (OSError, ValueError, RecoveryError) as error: row['error'] = str(error)[:500]
        result.append(row)
    return sorted(result,key=lambda row: (-(row['updated'] or 0),row['id']))


class PreparedProject:
    def __init__(self, root, document, identity, source=None, check=lambda:None):
        self.path = project_file(root,identity); self.stage = None; self.published = False; self.retain = False
        self.document = document; self.check = check
        self.path.parent.parent.mkdir(parents=True,exist_ok=True)
        if self.path.parent.exists(): raise ProjectActionError('A project with this identity already exists')
        self.stage = Path(tempfile.mkdtemp(prefix='.project-',dir=self.path.parent.parent))
        try:
            if source:
                source = Path(source)
                def copied(src,dst):
                    self.check()
                    if linked(src) or not os.path.isfile(src): raise ProjectActionError('Linked or nonregular saved-project files cannot be copied')
                    from media_collection import _verified_file
                    if os.path.getsize(src): _verified_file(src,dst,check=check)
                    else:
                        with open(dst,'xb'): pass
                    return dst
                def failed(error): raise ProjectActionError('Saved-project folder cannot be read: '+str(error))
                for base,dirs,files in os.walk(source,followlinks=False,onerror=failed):
                    self.check(); relative=Path(base).relative_to(source)
                    if linked(base) or any(linked(Path(base)/d) for d in dirs): raise ProjectActionError('Linked saved-project folders cannot be copied')
                    target=self.stage/relative;target.mkdir(parents=True,exist_ok=True)
                    for name in files:
                        if name.endswith('.tmp') or name in ('.project-transaction.json',): continue
                        copied(str(Path(base)/name),str(target/name))
            raw=json.dumps(document,ensure_ascii=False,allow_nan=False,indent=1).encode('utf-8');parse_project(raw)
            if len(raw)>MAX_PROJECT_BYTES: raise ProjectActionError('Project exceeds the 32 MiB action limit')
            write_atomic(self.stage/'project.json',raw)
        except BaseException:
            self.close();raise

    def publish(self):
        self.check(); final=self.path.parent
        final.mkdir()  # Never merge into an existing folder, even an empty one.
        try:
            # A project becomes discoverable only after its document is present.
            for item in self.stage.iterdir():
                if item.name != 'project.json': os.rename(item,final/item.name)
            os.rename(self.stage/'project.json',self.path)
            self.published=True;self.stage.rmdir();self.stage=None
        except BaseException as error:
            self.retain = True
            raise ProjectActionError(f'Project publication failed. Original retained; inspect {final} and {self.stage}: {error}') from error
        return self.path

    def close(self):
        if self.stage and self.stage.exists() and not self.retain: shutil.rmtree(self.stage)
        self.stage=None


class ActionWorker:
    """One admitted preparation; cancellation remains joined to its owned work."""
    def __init__(self):
        self.lock=threading.Lock();self.busy=False;self.stopping=False;self.cancel=None
        self.executor=concurrent.futures.ThreadPoolExecutor(max_workers=1,thread_name_prefix='Filmocity project copy')
    async def run(self,function):
        with self.lock:
            if self.stopping: raise ProjectActionBusy('Project actions are shutting down')
            if self.busy: raise ProjectActionBusy('Another project copy is running; wait for it to finish')
            self.busy=True
        cancelled=threading.Event()
        holder={}
        def cancel(): cancelled.set();holder['cancelled']=True
        self.cancel=cancel
        def check():
            if cancelled.is_set(): raise ProjectActionError('Project copy cancelled before publication')
        check.holder=holder
        try:
            def work():
                try: return True, function(check)
                except BaseException as error: return False, error
            future=asyncio.wrap_future(self.executor.submit(work))
            try:
                ok,value=await asyncio.shield(future)
                if not ok: raise value
                return value
            except asyncio.CancelledError:
                cancel()
                while not future.done():
                    try: await asyncio.shield(future)
                    except asyncio.CancelledError: cancel()
                    except Exception: break
                if not future.cancelled(): future.exception()
                raise
        finally:
            with self.lock:self.busy=False;self.cancel=None

    def shutdown(self, timeout=10):
        with self.lock:
            self.stopping=True
            if self.cancel: self.cancel()
        self.executor.shutdown(wait=False, cancel_futures=True)
        # ThreadPoolExecutor has no timed shutdown API. Retain and join only
        # this executor's handles; a timed-out copy is reported, never killed
        # or detached as a claimed successful shutdown.
        threads = tuple(self.executor._threads)
        deadline = time.monotonic() + max(0, timeout)
        for thread in threads: thread.join(max(0, deadline-time.monotonic()))
        return all(not thread.is_alive() for thread in threads)


def sample_file(path, command, check):
    """Stage generated tour media using the shared owned FFmpeg executor."""
    from render_context import RenderContext
    from render import _run_ffmpeg
    path=Path(path);check()
    if path.exists(): return
    try:
        with RenderContext(proc_holder=check.holder,scratch_parent=str(path.parent),stall_timeout=300) as context:
            staged=context.new_file(path.suffix)
            _run_ffmpeg([*command,staged],context=context)
            check()
            if path.exists(): raise ProjectActionError('Sample media appeared during generation; it was preserved')
            os.rename(staged,path)
    except (RuntimeError,OSError) as error:
        raise ProjectActionError('Sample generation failed: '+str(error)[-800:]) from error


COPY_WORKER=ActionWorker()
