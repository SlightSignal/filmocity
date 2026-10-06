"""Recoverable two-file commits for project.json and undo_stack.json.

The caller holds the project-store lock. A durable prepared journal records both
byte generations before publication. Normal exceptions roll back; interrupted
processes roll back when the project is next loaded. A committed journal only
needs retirement. Unknown bytes are never overwritten during recovery. Explicit restores use a
version-2 journal with the exact superseded journal embedded for rollback;
ordinary version-1 edit journals retain their existing contract.
"""
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
import time
import threading
import uuid
import re
from functools import wraps


class TransactionRecoveryRequired(RuntimeError):
    pass


class TransactionConflict(TransactionRecoveryRequired):
    pass


JOURNAL = '.project-transaction.json'
NAMES = ('project.json', 'undo_stack.json')
_LOCK = threading.RLock()


def _serialized(function):
    @wraps(function)
    def call(*args, **kwargs):
        with _LOCK:
            return function(*args, **kwargs)
    return call


def _read(path):
    try:
        return Path(path).read_bytes()
    except FileNotFoundError:
        return None


def _sync_parent(path):
    if os.name == 'nt':
        return  # Windows file contents are flushed; native durability needs acceptance.
    descriptor = os.open(Path(path).parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _replace(source, destination):
    for attempt in range(6):
        try:
            os.replace(source, destination)
            return
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 5:
                raise
            time.sleep(min(.02 * 2 ** attempt, .2))


def write_atomic(path, raw):
    path = Path(path)
    descriptor, temporary = tempfile.mkstemp(prefix='.commit-', suffix='.tmp', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'wb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        _replace(temporary, path)
        _sync_parent(path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _packed(raw):
    return None if raw is None else {'data': base64.b64encode(raw).decode('ascii'), 'sha256': hashlib.sha256(raw).hexdigest()}


def _unpacked(value):
    if value is None:
        return None
    raw = base64.b64decode(value['data'], validate=True)
    if hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise ValueError('Journal checksum does not match')
    return raw


def _journal_bytes(journal):
    return json.dumps(journal, separators=(',', ':'), allow_nan=False).encode('utf-8')


def _load_journal(path):
    try:
        journal = json.loads(path.read_bytes())
        if (type(journal['version']) is not int or journal['version'] not in (1, 2) or journal['phase'] not in ('prepared', 'committed')
                or not re.fullmatch(r'[a-f0-9]{32}', journal['id']) or set(journal['files']) != set(NAMES)):
            raise ValueError('Unknown transaction journal')
        if journal['version'] == 1 and any(key in journal for key in ('kind', 'previous_journal')):
            raise ValueError('Restore metadata cannot be read as a version-1 edit journal')
        if journal['version'] == 2:
            if journal.get('kind') != 'restore': raise ValueError('Unknown transaction kind')
            # An explicit restore may supersede even an unreadable old journal.
            # Its exact bytes are evidence; do not interpret them here.
            _unpacked(journal['previous_journal'])
        created = journal.get('created')
        if isinstance(created, bool) or not isinstance(created, (int, float)) or not math.isfinite(created):
            raise ValueError('Invalid transaction time')
        files = {name: {phase: _unpacked(entry[phase]) for phase in ('before', 'after')}
                 for name, entry in journal['files'].items()}
        if any(entry['after'] is None for entry in files.values()):
            raise ValueError('Missing transaction output')
        return journal, files
    except (ValueError, KeyError, TypeError, OverflowError, OSError) as error:
        raise TransactionRecoveryRequired(f'Cannot read the project transaction at {path}: {error}') from error


def _preserve_interrupted(parent, journal, files):
    archive = parent / 'recovery' / journal['id']
    archive.mkdir(parents=True, exist_ok=True)
    copies = {'project': files['project.json']['before'], 'editor': files['project.json']['after'],
              'undo': files['undo_stack.json']['before'], 'transaction': _journal_bytes(journal)}
    if journal['version'] == 2:
        copies['previous_transaction'] = _unpacked(journal['previous_journal'])
    records = {}
    for name, raw in copies.items():
        if raw is None:
            continue
        destination = archive / (name + '.json')
        if not destination.exists():
            with destination.open('xb') as stream:
                stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        if destination.read_bytes() != raw:
            raise TransactionRecoveryRequired(f'Cannot verify interrupted transaction copy: {destination}')
        records[name] = {'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    write_atomic(archive / 'manifest.json', json.dumps({'version': 1, 'source': 'interrupted_transaction',
                  'created': journal['created'], 'preserved': records}).encode('utf-8'))
    _sync_parent(archive); _sync_parent(archive.parent)


@_serialized
def recover_transaction(project_file):
    parent = Path(project_file).parent; path = parent / JOURNAL
    if not path.exists():
        return None
    journal, files = _load_journal(path)
    try:
        current = {name: _read(parent / name) for name in NAMES}
        # Inspect both before touching either. External or corrupt edits need
        # explicit recovery, even if only the other file needs restoration.
        for name in NAMES:
            if current[name] not in (files[name]['before'], files[name]['after']):
                raise TransactionRecoveryRequired(f'{name} changed outside the interrupted transaction; copies remain at {path}')
        if journal['phase'] == 'committed':
            if any(current[name] != files[name]['after'] for name in NAMES):
                raise TransactionRecoveryRequired(f'A committed transaction has inconsistent files; copies remain at {path}')
        else:
            _preserve_interrupted(parent, journal, files)
            for name in reversed(NAMES):
                raw = files[name]['before']
                if current[name] == raw:
                    continue
                if raw is None:
                    os.unlink(parent / name); _sync_parent(parent / name)
                else:
                    write_atomic(parent / name, raw)
            if journal['version'] == 2 and journal['previous_journal'] is not None:
                # Do not let a failed explicit restore silently dismiss an older
                # transaction or expose its possibly inconsistent files as saved.
                write_atomic(path, _unpacked(journal['previous_journal']))
                raise TransactionRecoveryRequired('The interrupted restore was rolled back. Its previous transaction was reinstated; open Recovery to review the preserved versions.')
        os.unlink(path); _sync_parent(path)
        return journal['phase']
    except OSError as error:
        raise TransactionRecoveryRequired(f'Cannot finish project transaction recovery at {path}: {error}') from error


@_serialized
def commit_pair(project_file, project_raw, history_raw, publish_project):
    """Publish exact bytes using the existing project writer/backup policy.

    A return value is a post-commit cleanup warning, never a retry instruction.
    No result is reported until both files and the commit marker are present.
    """
    parent = Path(project_file).parent
    if Path(project_file).name != 'project.json':
        raise ValueError('The transaction target must be project.json')
    recover_transaction(project_file)
    journal = {'version': 1, 'id': uuid.uuid4().hex, 'created': time.time(), 'phase': 'prepared', 'files': {
        name: {'before': _packed(_read(parent / name)), 'after': _packed(raw)}
        for name, raw in zip(NAMES, (project_raw, history_raw))}}
    return _publish_pair(project_file, journal, publish_project)


@_serialized
def commit_restore_pair(project_file, project_raw, history_raw, publish_project, *, expected):
    """Explicit recovery after the caller has verified preserved evidence.

    Unlike an ordinary edit, this may supersede an inconsistent old journal.
    Check all captured bytes again before replacing it. Rollback reinstates that
    journal; only a committed restore is allowed to retire it permanently.
    """
    parent = Path(project_file).parent
    if Path(project_file).name != 'project.json' or set(expected) != {*NAMES, JOURNAL}:
        raise ValueError('Invalid explicit restore target')
    if any(_read(parent / name) != raw for name, raw in expected.items()):
        raise TransactionConflict('Project, undo history or interrupted transaction changed during preservation. Refresh Recovery before restoring.')
    journal = {'version': 2, 'kind': 'restore', 'id': uuid.uuid4().hex, 'created': time.time(), 'phase': 'prepared',
               'previous_journal': _packed(expected[JOURNAL]), 'files': {
        name: {'before': _packed(expected[name]), 'after': _packed(raw)}
        for name, raw in zip(NAMES, (project_raw, history_raw))}}
    return _publish_pair(project_file, journal, publish_project)


def _publish_pair(project_file, journal, publish_project):
    parent = Path(project_file).parent; path = parent / JOURNAL
    project_raw = _unpacked(journal['files']['project.json']['after'])
    history_raw = _unpacked(journal['files']['undo_stack.json']['after'])
    write_atomic(path, _journal_bytes(journal))
    try:
        publish_project()
        if _read(project_file) != project_raw:
            raise TransactionRecoveryRequired('The project writer did not publish the prepared bytes')
        _sync_parent(project_file)
        write_atomic(parent / 'undo_stack.json', history_raw)
        journal['phase'] = 'committed'
        write_atomic(path, _journal_bytes(journal))
    except Exception as error:
        # A late directory-sync failure can occur after the commit marker's
        # replacement. Inspect it before deciding between rollback and success.
        stored, files = _load_journal(path)
        if stored['phase'] != 'committed':
            try:
                recover_transaction(project_file)
            except TransactionRecoveryRequired as recovery_error:
                raise TransactionRecoveryRequired(f'{error}; {recovery_error}') from error
            raise
        if any(_read(parent / name) != files[name]['after'] for name in NAMES):
            raise TransactionRecoveryRequired('Commit marker exists but its files do not match') from error
        return 'Project and undo saved; transaction cleanup is pending: ' + str(error)[:200]
    try:
        os.unlink(path); _sync_parent(path)
    except OSError as error:
        return 'Project and undo saved; transaction cleanup is pending: ' + str(error)[:200]
    return ''
