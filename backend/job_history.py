"""Bounded export receipts, written at state transitions and restored at mount.

No project or source media is copied here and no interrupted work is requeued.
Checksums protect receipt integrity; startup checks output metadata, not a full
media decode/hash. The workspace owner must be held before calling restore().
"""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import threading
import time

from export_storage import output_path, filesystem_path
from project_sync import workspace_id
from project_transaction import write_atomic

PRIMARY = '.export-job.json'
PREVIOUS = '.export-job.previous.json'
MAX_BYTES = 4 * 1024 * 1024
STATES = {'queued', 'running', 'cancelling', 'done', 'error'}
FIELDS = ('id', 'name', 'status', 'out', 'started', 'started_run', 'finished', 'progress',
          'preset', 'sequence', 'context', 'preview', 'preview_request', 'output_kind',
          'frames', 'command_log', 'review_url', 'review_start', 'qa', 'error',
          'diagnostics', 'event_persistence', 'mode', 'reused', 'segments', 'actor')
_LOCK = threading.RLock()


class HistoryError(ValueError): pass


def _bytes(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


def _folder(root, jid):
    if not isinstance(jid, str) or not re.fullmatch('[a-f0-9]{16}', jid):
        raise HistoryError('Invalid export identity.')
    folder = Path(filesystem_path(Path(root) / 'renders' / ('job-' + jid), force=True))
    if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)():
        raise HistoryError('Linked export history folders are not adopted.')
    return folder


def _owned_output(root, jid, url):
    if not isinstance(url, str) or not url.startswith('/renders/job-' + jid + '/'):
        raise HistoryError('Export output does not belong to this job.')
    try:
        path = Path(filesystem_path(output_path(Path(root) / 'renders', url), force=True))
        folder = _folder(root, jid).resolve()
    except RuntimeError as error:
        raise HistoryError('Export output path could not be resolved.') from error
    if folder not in path.parents:
        raise HistoryError('Export output escapes its job folder.')
    return path


def _finite(value):
    return type(value) in (float, int) and math.isfinite(value)


def _validate(root, job, jid):
    if not isinstance(job, dict) or job.get('id') != jid or job.get('status') not in STATES:
        raise HistoryError('Invalid export state.')
    for key in ('name', 'sequence'):
        if not isinstance(job.get(key), str) or not job[key]: raise HistoryError('Missing export ' + key + '.')
    if not _finite(job.get('started')) or not isinstance(job.get('preset'), dict):
        raise HistoryError('Invalid export time or settings.')
    for key in ('finished', 'started_run', 'progress', 'review_start'):
        if key in job and not _finite(job[key]): raise HistoryError('Invalid export ' + key + '.')
    _owned_output(root, jid, job.get('out'))
    _owned_output(root, jid, job.get('command_log'))
    if job.get('review_url') != '/review/job-' + jid: raise HistoryError('Invalid review link.')
    if job.get('output_kind') == 'png_sequence':
        frames = job.get('frames')
        if not isinstance(frames, dict): raise HistoryError('Missing PNG sequence locations.')
        for key in ('directory', 'first_frame'): _owned_output(root, jid, frames.get(key))
        if job['out'] != frames['directory'] + '/sequence.json': raise HistoryError('Invalid PNG manifest location.')
    for key in ('qa', 'preview', 'event_persistence'):
        if key in job and not isinstance(job[key], dict): raise HistoryError('Invalid export ' + key + '.')
    if 'error' in job and not isinstance(job['error'], str): raise HistoryError('Invalid export error.')
    for context in (job.get('context'), (job.get('preview') or {}).get('context')):
        if context is not None and (not isinstance(context, dict) or context.get('workspace') != workspace_id(root)
                                    or not isinstance(context.get('project'), str)):
            raise HistoryError('Export project identity does not belong to this workspace.')


def _read(root, path, jid):
    if path.is_symlink(): raise HistoryError('Linked export receipts are not adopted.')
    with path.open('rb') as stream: raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES: raise HistoryError('Export receipt is too large.')
    try:
        envelope = json.loads(raw)
        if type(envelope.get('version')) is not int or envelope['version'] != 1:
            raise HistoryError('Unsupported export receipt version.')
        payload = {k: envelope[k] for k in ('version', 'workspace', 'job', 'output_metadata')}
        if envelope.get('sha256') != hashlib.sha256(_bytes(payload)).hexdigest():
            raise HistoryError('Export receipt checksum does not match.')
        if envelope['workspace'] != workspace_id(root): raise HistoryError('Export receipt belongs to another data folder.')
        _validate(root, envelope['job'], jid)
        metadata = envelope['output_metadata']
        if metadata is not None:
            if not isinstance(metadata, list) or not 1 <= len(metadata) <= 2: raise HistoryError('Invalid output metadata.')
            for item in metadata:
                _owned_output(root, jid, item['url'])
                if type(item['size']) is not int or item['size'] <= 0 or type(item['mtime_ns']) is not int:
                    raise HistoryError('Invalid output metadata values.')
        return envelope, raw
    except (KeyError, TypeError, AttributeError, ValueError, OverflowError, RecursionError) as error:
        raise HistoryError(str(error)) from error


def _output_metadata(root, job):
    urls = [job['out']]
    if job.get('output_kind') == 'png_sequence': urls.append(job['frames']['first_frame'])
    metadata = []
    for url in urls:
        path = _owned_output(root, job['id'], url)
        info = path.stat()
        if not stat.S_ISREG(info.st_mode) or info.st_size <= 0:
            raise HistoryError('Completed export output is empty or is not a regular file.')
        metadata.append({'url': url, 'size': info.st_size, 'mtime_ns': info.st_mtime_ns})
    return metadata


def save(root, job):
    """Return exact receipt bytes; retain the previous valid transition."""
    with _LOCK:
        value = copy.deepcopy({key: job[key] for key in FIELDS if key in job})
        jid = value.get('id'); _validate(root, value, jid)
        folder = _folder(root, jid)
        primary = folder / PRIMARY
        previous = None
        if primary.is_symlink(): raise HistoryError('Linked export receipts are not overwritten.')
        if primary.exists():
            old, previous = _read(root, primary, jid)
            if old['job']['status'] in ('done', 'error') and old['job']['status'] != value['status']:
                raise HistoryError('A terminal export cannot return to an earlier queue state.')
        metadata = _output_metadata(root, value) if value['status'] == 'done' else None
        payload = {'version': 1, 'workspace': workspace_id(root), 'job': value, 'output_metadata': metadata}
        raw = _bytes({**payload, 'sha256': hashlib.sha256(_bytes(payload)).hexdigest()})
        if len(raw) > MAX_BYTES: raise HistoryError('Export receipt is too large.')
        if previous is not None:
            backup = folder / PREVIOUS
            if backup.is_symlink(): raise HistoryError('Linked previous receipt is not overwritten.')
            write_atomic(backup, previous)
        write_atomic(primary, raw)
        return raw


def remember(root, job, *, required=False):
    try:
        raw = save(root, job)
    except (OSError, ValueError, TypeError, RecursionError) as error:
        job['history'] = {'status': 'error', 'message': 'Export history could not be saved. Reopening may show an earlier state. ' + str(error)[:300]}
        if required: raise
        return None
    job['history'] = {'status': 'saved'}
    return raw


def discard_unqueued(root, job, receipt):
    """Undo only our exact first write if queue insertion failed."""
    if receipt is None:
        # A directory flush can fail after the first atomic rename. Retire that
        # exact queued receipt too; an unsuccessful submission must not revive
        # as queued work after restarting. Unknown bytes remain untouched.
        if job.get('status') != 'queued': return
        value = copy.deepcopy({key: job[key] for key in FIELDS if key in job})
        payload = {'version': 1, 'workspace': workspace_id(root), 'job': value, 'output_metadata': None}
        receipt = _bytes({**payload, 'sha256': hashlib.sha256(_bytes(payload)).hexdigest()})
    with _LOCK:
        folder = _folder(root, job['id']); primary = folder / PRIMARY
        if not primary.is_symlink() and primary.read_bytes() == receipt and not (folder / PREVIOUS).exists():
            primary.unlink()


def restore(root):
    """Read receipts independently; never infer success from media filenames."""
    jobs = {}
    with _LOCK:
        render_root = Path(filesystem_path(Path(root) / 'renders', force=True))
        if not render_root.is_dir(): return jobs
        for folder in sorted(render_root.iterdir()):
            match = re.fullmatch(r'job-([a-f0-9]{16})', folder.name)
            if not match: continue
            jid = match[1]
            if folder.is_symlink() or getattr(folder, 'is_junction', lambda: False)(): continue
            if not folder.is_dir(): continue
            primary, backup = folder / PRIMARY, folder / PREVIOUS
            if not primary.exists() and not backup.exists(): continue  # earlier versions had no receipts
            loaded = None; errors = []
            for path in (primary, backup):
                try: loaded, _ = _read(root, path, jid); break
                except (OSError, ValueError) as error: errors.append(str(error)[:200])
            if loaded is None:
                jobs[jid] = {'id': jid, 'name': 'Unrestored export ' + jid[:8], 'started': time.time(), 'status': 'error',
                             'error': 'Export history could not be read. Its files were left unchanged.',
                             'history': {'status': 'unreadable', 'message': 'Export history needs inspection: ' + '; '.join(errors)}}
                continue
            job = copy.deepcopy(loaded['job']); original = job['status']
            job['history'] = {'status': 'recovered' if errors else 'restored',
                              'message': 'Recovered the previous export record; the unreadable record was preserved.' if errors else 'Restored after restart.'}
            if original in ('queued', 'running', 'cancelling'):
                job.update(status='error', finished=time.time(), error='Export was interrupted when Filmocity stopped. Start a new export when ready.')
                job['recovery'] = {'previous_status': original, 'interrupted': True}
            elif original == 'done':
                try:
                    if _output_metadata(root, job) != loaded['output_metadata']:
                        raise HistoryError('Output size or modification time changed.')
                    job['output_check'] = 'metadata_only'
                except (OSError, ValueError) as error:
                    job.update(status='error', error='The saved export output is missing or changed. Its files were left unchanged. ' + str(error)[:200])
                    job['recovery'] = {'previous_status': original, 'output_unavailable': True}
                    if job.get('qa'): job['qa']['status'] = 'output unavailable'
            # Prior previews remain downloadable history, but only a new render
            # can reactivate monitor playback or a previous request identity.
            if job.get('preview'):
                job.setdefault('context', job['preview'].get('context'))
                job['restored_preview'] = True
                job.pop('preview', None); job.pop('preview_request', None)
            jobs[jid] = job
    return jobs
