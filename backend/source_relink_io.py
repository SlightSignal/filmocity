"""Bounded, owned I/O for source Relink; planning never writes the project."""
import json
import os
import subprocess
import time

from media_metadata import summarize
from render_context import RenderContext
from task_inputs import source_stamp, portable_source_stamp

MAX_OUTPUT = 4 * 1024 * 1024
TIMEOUT = 60


def inspect(project, body, path, *, proc_holder=None, scratch_parent=None, ffprobe='ffprobe'):
    import source_relink
    from work_budget import work
    holder = proc_holder if proc_holder is not None else {}
    # Bound admission before starting a decoder or copying a proposed plan.
    source_relink.bounded(project)
    before = source_stamp({'path': path})
    with RenderContext(proc_holder=holder, scratch_parent=scratch_parent) as context:
        output, errors = context.new_file('.json'), context.new_file('.log')
        with work(holder, 'probe', check=context.check_cancelled):
            options = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with open(output, 'wb') as stdout, open(errors, 'wb') as stderr:
                context.check_cancelled()
                child = subprocess.Popen([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path],
                    stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **options)
                holder['proc'] = child
                deadline = time.monotonic() + TIMEOUT
                try:
                    while child.poll() is None:
                        context.check_cancelled()
                        if time.monotonic() > deadline: raise ValueError('Replacement inspection timed out')
                        if any(os.path.getsize(p) > MAX_OUTPUT for p in (output, errors)):
                            raise ValueError('Replacement inspection metadata exceeds four MiB')
                        time.sleep(.025)
                    context.check_cancelled()
                    if any(os.path.getsize(p) > MAX_OUTPUT for p in (output, errors)):
                        raise ValueError('Replacement inspection metadata exceeds four MiB')
                    if child.returncode:
                        with open(errors, 'rb') as stream:
                            stream.seek(max(0, os.path.getsize(errors)-400))
                            message = stream.read(400).decode('utf-8', 'replace')
                        raise ValueError('Replacement cannot be read: '+message)
                finally:
                    if child.poll() is None: child.kill()
                    child.wait()
                    if holder.get('proc') is child: holder.pop('proc', None)
        with open(output, 'rb') as stream: raw = stream.read(MAX_OUTPUT+1)
        if len(raw) > MAX_OUTPUT: raise ValueError('Replacement inspection metadata exceeds four MiB')
        document = json.loads(raw)
        if not isinstance(document, dict): raise ValueError('Replacement inspection returned invalid metadata')
        info = summarize(document)
        if source_stamp({'path':path}) != before: raise ValueError('Replacement changed while being inspected')
        context.check_cancelled()
        planned = source_relink.plan(project, body, info, path, before)
        context.check_cancelled()
        return {'plan':planned, 'stamp':before, 'info':info, 'path':path}


def check(path, stamp):
    if source_stamp({'path':path}) != stamp:
        raise ValueError('Replacement changed after inspection; inspect it again')


def check_accepted(media):
    """Validate an existing Relink acceptance; legacy unbound media returns None.

    Persisted stat values are canonical decimal strings so ordinary browser JSON
    edits cannot round filesystem integers. Call with the resolved physical
    source (or an alias's effective current-parent metadata), not a content hash.
    """
    basis = media.get('source_relink_basis')
    if basis is None: return None
    path = media.get('path')
    if (not isinstance(basis, dict) or basis.get('version') != 1 or not isinstance(path, str)
            or basis.get('path') != os.path.abspath(path)):
        raise ValueError('Accepted Relink source identity is invalid; inspect and Relink the source again')
    stamp = basis.get('stamp')
    if (not isinstance(stamp, list) or len(stamp) != 1 or not isinstance(stamp[0], list)
            or len(stamp[0]) != 4 or stamp[0][0] != basis['path'] or stamp != portable_source_stamp(source_stamp(media))):
        raise ValueError('The accepted source changed on disk. Inspect and Relink it again before preparing or exporting.')
    return basis
