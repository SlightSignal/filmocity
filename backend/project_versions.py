"""Saved project copies; callers hold the server's project lock throughout use."""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from project_recovery import parse_project, RecoveryError, RecoveryConflict
from project_transaction import _sync_parent


def _filesystem_path(path):
    """Keep Windows long-path syntax private to saved-version filesystem work.

    Use lexical absolute paths, not resolved targets: version_path still checks
    linked storage before it reads, creates or removes any saved document.
    """
    value = os.path.abspath(os.fspath(path))
    if os.name == 'nt' and not value.startswith('\\\\?\\'):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return Path(value)


def version_path(project_file, kind, name):
    if kind not in ('snapshots', 'backups'):
        raise RecoveryError('Unknown saved-version kind')
    if (not isinstance(name, str) or not name or name in ('.', '..')
            or re.search(r'[\\/:\x00-\x1f]', name) or not name.endswith('.json')):
        raise RecoveryError('Choose a saved-version filename from the list')
    if kind == 'backups' and not re.fullmatch(r'project_\d+\.json', name):
        raise RecoveryError('Unknown automatic backup')
    base = _filesystem_path(project_file).parent / kind
    path = base / name
    if (base.is_symlink() or path.is_symlink()
            or os.path.normcase(os.path.realpath(base)) != os.path.normcase(os.path.join(os.path.realpath(base.parent), kind))):
        raise RecoveryError('Linked saved versions cannot be restored')
    return path


def read_version(project_file, kind, name, expected_sha256=None):
    path = version_path(project_file, kind, name)
    if not path.is_file(): raise FileNotFoundError('The saved version is no longer available')
    raw = path.read_bytes()
    sha = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None and expected_sha256 != sha:
        raise RecoveryConflict('The selected saved version changed. Refresh the list before restoring.')
    return parse_project(raw), sha


def version_details(path, document, sha):
    name = path.name
    parts = name[:-5].split('_', 1)
    try: timestamp = float(parts[0] if not name.startswith('project_') else parts[1])
    except (ValueError, IndexError): timestamp = path.stat().st_mtime
    if not math.isfinite(timestamp): timestamp = path.stat().st_mtime
    label = parts[1] if len(parts) > 1 else name[:-5]
    # New files include a collision-resistant component before the label.
    label = re.sub(r'^[a-f0-9]{8}_', '', label)
    if name.startswith('project_'): label = 'Automatic backup'
    return {'file': name, 'ts': timestamp, 'label': label, 'sha256': sha,
            'name': str(document.get('name') or 'Untitled'), 'sequences': len(document['sequences']),
            'clips': sum(len(track['clips']) for seq in document['sequences'] for track in seq['tracks'])}


def list_versions(project_file, kind, label=None):
    base = version_path(project_file, kind, 'project_0.json').parent
    versions, unavailable = [], []
    if base.exists():
        for path in sorted(base.iterdir()):
            if not path.name.endswith('.json') or path.name.startswith('.'): continue
            if kind == 'backups' and not re.fullmatch(r'project_\d+\.json', path.name): continue
            if label is not None and not path.name.endswith('_' + label + '.json'): continue
            try:
                document, sha = read_version(project_file, kind, path.name)
                versions.append(version_details(path, document, sha))
            except (RecoveryError, OSError) as error:
                unavailable.append({'file': path.name, 'reason': str(error)[:300]})
    return {'versions': sorted(versions, key=lambda v: (v['ts'], v['file']), reverse=True), 'unavailable': unavailable}


def write_snapshot(project_file, document, label='snapshot'):
    if not isinstance(label, str) or not label.strip() or len(label) > 120:
        raise RecoveryError('Snapshot labels must contain 1–120 characters')
    slug = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', label).strip(' .') or 'snapshot'
    slug = slug.encode('utf-8')[:160].decode('utf-8', errors='ignore')
    raw = json.dumps(document, indent=1, ensure_ascii=False, allow_nan=False).encode('utf-8')
    parse_project(raw)
    name = f'{time.time():.6f}_{uuid.uuid4().hex[:8]}_{slug}.json'
    path = version_path(project_file, 'snapshots', name)
    path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    try:
        with path.open('xb') as stream:
            created = True
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if path.read_bytes() != raw: raise OSError('The snapshot could not be verified')
        _sync_parent(path)
    except BaseException:
        if created:
            try: path.unlink()
            except OSError: pass
        raise
    return version_details(path, document, hashlib.sha256(raw).hexdigest())
