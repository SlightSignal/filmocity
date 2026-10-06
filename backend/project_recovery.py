"""Inspect and explicitly restore saved project versions without discarding evidence.

Callers serialize inspection/restore with project writes. No candidate is applied
automatically. Checksums bind both the chosen version and the current file.
"""
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time
import uuid

from timeline_time import timecode_mode
from input_options import validated_input_options
from project_transaction import (JOURNAL, commit_restore_pair, TransactionConflict,
                                 _sync_parent)


class RecoveryError(ValueError):
    pass


class RecoveryConflict(RecoveryError):
    pass


class ProjectRecoveryRequired(RecoveryError):
    pass


def _invalid_constant(value):
    raise RecoveryError(f'Project contains an invalid number: {value}')


def parse_project(raw):
    try:
        project = json.loads(raw.decode('utf-8-sig'), parse_constant=_invalid_constant)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise RecoveryError('Project file is not readable JSON') from error
    if not isinstance(project, dict) or not isinstance(project.get('media'), dict):
        raise RecoveryError('Project has no readable media library')
    if any(not isinstance(item, dict) for item in project['media'].values()):
        raise RecoveryError('Project has an unreadable media entry')
    for identity, item in project['media'].items():
        try:
            validated_input_options(item.get('input_opts'))
        except ValueError as error:
            raise RecoveryError(f'Invalid source input options for {identity}: {error}') from error
    version = project.get('version', 1)
    if not isinstance(version, int) or isinstance(version, bool) or not 1 <= version <= 3:
        raise RecoveryError('Project schema is not supported by this version of Filmocity')
    sequences = project.get('sequences')
    if not isinstance(sequences, list) or not sequences:
        raise RecoveryError('Project has no sequences')
    for sequence in sequences:
        if not isinstance(sequence, dict) or not sequence.get('id') or not isinstance(sequence.get('tracks'), list):
            raise RecoveryError('Project has an unreadable sequence')
        for field in ('width', 'height', 'fps'):
            value = sequence.get(field)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0:
                raise RecoveryError(f'Project has an invalid sequence {field}')
        try: timecode_mode(sequence['fps'], sequence.get('timecode_format', 'ndf'))
        except ValueError as error: raise RecoveryError(str(error)) from error
        for track in sequence['tracks']:
            if not isinstance(track, dict) or not track.get('id') or track.get('kind') not in ('video', 'audio') or not isinstance(track.get('clips'), list):
                raise RecoveryError('Project has an unreadable track')
            if any(not isinstance(clip, dict) or not clip.get('id') for clip in track['clips']):
                raise RecoveryError('Project has an unreadable clip')
    return project


def fingerprint(raw):
    return hashlib.sha256(raw).hexdigest() if raw is not None else 'missing'


def _read_optional(path):
    try: return Path(path).read_bytes()
    except FileNotFoundError: return None


def _candidate_path(project_file, candidate):
    parent = Path(project_file).parent
    if not isinstance(candidate, str): raise RecoveryError('Unknown recovery version')
    if candidate == 'current': return Path(project_file)
    if candidate == 'pending': return Path(str(project_file) + '.tmp')
    if re.fullmatch(r'backups/project_\d+\.json', candidate or ''):
        return parent / candidate
    if re.fullmatch(r'recovery/[a-f0-9]{32}/(project|pending|editor)\.json', candidate or ''):
        return parent / candidate
    raise RecoveryError('Unknown recovery version')


def has_recovery_files(project_file):
    path = Path(project_file)
    return Path(str(path) + '.tmp').exists() or any((path.parent / 'backups').glob('project_*.json')) or any((path.parent / 'recovery').glob('*/manifest.json'))


def inspect_recovery(project_file):
    path = Path(project_file)
    current = _read_optional(path)
    report = {'current_sha256': fingerprint(current), 'current_valid': False, 'current_error': '', 'candidates': [], 'unavailable': []}
    try:
        if current is None: raise RecoveryError('Project file is missing')
        parse_project(current)
        report['current_valid'] = True
    except RecoveryError as error:
        report['current_error'] = str(error)
    candidates = [('pending', Path(str(path) + '.tmp'), 'Interrupted save')]
    if report['current_valid']: candidates.append(('current', path, 'Current saved project'))
    backups = sorted((path.parent / 'backups').glob('project_*.json'), reverse=True)[:30]
    candidates.extend((f'backups/{item.name}', item, 'Automatic backup') for item in backups if re.fullmatch(r'project_\d+\.json', item.name))
    histories = sorted((path.parent / 'recovery').glob('*/manifest.json'), key=lambda item: item.stat().st_mtime, reverse=True)[:20]
    for manifest in histories:
        if not re.fullmatch(r'[a-f0-9]{32}', manifest.parent.name): continue
        for name, label in [('project', 'Before recovery'), ('pending', 'Preserved interrupted save'), ('editor', 'Preserved editor copy')]:
            candidates.append((f'recovery/{manifest.parent.name}/{name}.json', manifest.parent / f'{name}.json', label))
    for key, file, kind in candidates:
        try:
            raw = _read_optional(file)
            if raw is None: continue
            project = parse_project(raw)
            report['candidates'].append({'id': key, 'kind': kind, 'sha256': fingerprint(raw), 'name': str(project.get('name') or 'Untitled'),
                'saved_at': file.stat().st_mtime, 'sequences': len(project['sequences']), 'media': len(project['media']),
                'clips': sum(len(track['clips']) for sequence in project['sequences'] for track in sequence['tracks'])})
        except (OSError, RecoveryError) as error:
            report['unavailable'].append({'id': key, 'reason': str(error)[:300]})
    report['candidates'].sort(key=lambda item: item['saved_at'], reverse=True)
    return report


def _write_exclusive(path, raw):
    with Path(path).open('xb') as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())
    if Path(path).read_bytes() != raw:
        raise RecoveryError(f'Cannot verify preserved recovery file: {path}')
    _sync_parent(path)


def preserve_editor_draft(project_file, document):
    """Stage an explicitly submitted browser draft as a verified candidate."""
    raw = json.dumps(document, ensure_ascii=False, allow_nan=False, indent=1).encode('utf-8')
    parse_project(raw)
    archive = Path(project_file).parent / 'recovery' / uuid.uuid4().hex
    archive.mkdir(parents=True, exist_ok=False)
    _sync_parent(archive); _sync_parent(archive.parent)
    _write_exclusive(archive / 'editor.json', raw)
    _write_exclusive(archive / 'manifest.json', json.dumps({'version': 1, 'created': time.time(),
        'source': 'browser_draft', 'preserved': {'editor': {'sha256': fingerprint(raw), 'bytes': len(raw)}}}).encode('utf-8'))
    return f'recovery/{archive.name}/editor.json', fingerprint(raw)


def restore_version(project_file, candidate, expected_sha256, current_sha256, save, editor_project=None):
    path = Path(project_file)
    source = _candidate_path(path, candidate)
    current = _read_optional(path)
    if fingerprint(current) != current_sha256:
        raise RecoveryConflict('The current project changed. Refresh recovery versions before restoring.')
    raw = _read_optional(source)
    if raw is None or fingerprint(raw) != expected_sha256:
        raise RecoveryConflict('The selected version changed or disappeared. Refresh recovery versions.')
    project = parse_project(raw)
    pending = _read_optional(str(path) + '.tmp')
    undo_path = path.parent / 'undo_stack.json'
    undo = _read_optional(undo_path)
    journal_path = path.parent / JOURNAL
    journal = _read_optional(journal_path)
    # Serialize the editor copy before creating any recovery files or replacing
    # the project. It is evidence only, never silently used as a replacement.
    editor = None if editor_project is None else json.dumps(editor_project, ensure_ascii=False, allow_nan=False, indent=1).encode('utf-8')
    archive = path.parent / 'recovery' / uuid.uuid4().hex
    archive.mkdir(parents=True, exist_ok=False)
    _sync_parent(archive); _sync_parent(archive.parent)
    records = {}
    for name, contents in [('project', current), ('pending', pending), ('editor', editor), ('undo', undo), ('transaction', journal)]:
        if contents is None: continue
        _write_exclusive(archive / f'{name}.json', contents)
        records[name] = {'sha256': fingerprint(contents), 'bytes': len(contents)}
    _write_exclusive(archive / 'manifest.json', json.dumps({'version': 1, 'created': time.time(), 'selected': candidate,
        'selected_sha256': expected_sha256, 'preserved': records}, indent=2).encode('utf-8'))
    # Freeze the precise bytes the existing writer will publish. Both the chosen
    # project and empty history are now one recoverable transaction, including an
    # older journal that this explicit user decision is allowed to supersede.
    project.setdefault('version', 3)
    project['updated'] = updated = time.time()
    project_raw = json.dumps(project, indent=1, allow_nan=False).encode('utf-8')
    try:
        warning = commit_restore_pair(path, project_raw, b'{"undo":[],"redo":[]}\n',
            lambda: save(project, str(path), updated=updated),
            expected={'project.json': current, 'undo_stack.json': undo, JOURNAL: journal})
    except TransactionConflict as error:
        raise RecoveryConflict(str(error)) from error
    except Exception as error:
        raise RecoveryError(f'Restoring failed; previous files were preserved in {archive}: {error}') from error
    result = {'ok': True, 'name': project.get('name', 'Untitled'), 'preserved': str(archive), 'selected_sha256': expected_sha256}
    if warning: result['warning'] = warning
    return result
