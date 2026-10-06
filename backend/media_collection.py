"""Verified collection of original media; project publication belongs to the caller.

Each run owns a fresh staging directory. Existing collected files are reused,
never overwritten. A published generation is retained even if project saving
subsequently fails, so a saved project or its recovery file cannot lose media.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import uuid

from preflight import media_files
from cache_paths import linked


class MediaCollectionError(RuntimeError):
    pass


def _identity(path):
    return os.path.normcase(os.path.realpath(path))


def _within(path, directory):
    try:
        return os.path.commonpath([_identity(path), _identity(directory)]) == _identity(directory)
    except ValueError:
        return False


def _portable_name(name):
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', name).rstrip(' .') or 'media'
    if name.split('.')[0].upper() in {'CON', 'PRN', 'AUX', 'NUL', *(f'COM{i}' for i in range(1, 10)), *(f'LPT{i}' for i in range(1, 10))}:
        name = '_' + name
    suffix = Path(name).suffix
    return name[:120 - len(suffix)] + suffix if len(name) > 120 else name


def _signature(stat):
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _digest(path, *, check=None):
    digest = hashlib.sha256()
    with open(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            if check: check()
            digest.update(block)
    return digest.hexdigest()


def _verified_file(source, target=None, *, check=None):
    """Read stable source bytes; close and verify the copy before admitting it."""
    if check: check()
    before = os.stat(source)
    if not os.path.isfile(source) or not before.st_size:
        raise MediaCollectionError(f'Original media is empty or not a file: {source}')
    if target is None:
        digest = _digest(source, check=check) if check else _digest(source)
    else:
        digest = hashlib.sha256()
        with open(source, 'rb') as reader, open(target, 'xb') as writer:
            for block in iter(lambda: reader.read(1024 * 1024), b''):
                if check: check()
                writer.write(block)
                digest.update(block)
            writer.flush()
            os.fsync(writer.fileno())
        digest = digest.hexdigest()
        copied_digest = _digest(target, check=check) if check else _digest(target)
        if os.path.getsize(target) != before.st_size or copied_digest != digest:
            raise MediaCollectionError(f'Copy verification failed: {source}')
        os.utime(target, ns=(before.st_atime_ns, before.st_mtime_ns))
    if check: check()
    if _signature(before) != _signature(os.stat(source)):
        raise MediaCollectionError(f'Original media changed during collection: {source}')
    return {'bytes': before.st_size, 'sha256': digest}


def rebase_source_acceptance(media, path, *, verified_path=None):
    """Move an already validated acceptance after a byte-verified relocation.

    The caller owns copy verification. During staging the final path does not
    exist yet; rename preserves the staged file's identity. Probe metadata is
    retained because the copied bytes were verified against the accepted file.
    """
    if media.get('source_relink_basis') is None: return
    from task_inputs import source_stamp, portable_source_stamp
    basis = copy.deepcopy(media['source_relink_basis'])
    stamp = source_stamp({'path': str(verified_path or path)})
    path = os.path.abspath(path)
    basis.update(path=path, stamp=portable_source_stamp([[path, *stamp[0][1:]]]))
    media['source_relink_basis'] = basis


class MediaCollection:
    def __init__(self, project, destination, *, check=None, progress=None):
        self.project = copy.deepcopy(project)
        self.destination = Path(destination).absolute()
        self.check = check or (lambda: None)
        self.copy_check = check
        self.progress = progress or (lambda *args: None)
        self.stage = None
        self.published = None
        self.final = None
        self.files = []
        self.copied = 0
        self.reused = 0
        self.accepted_sources = []

    def __enter__(self):
        try:
            self._prepare()
            return self
        except BaseException:
            self.discard()
            raise

    def __exit__(self, exc_type, exc, tb):
        self.discard()

    def discard(self):
        if self.stage is not None:
            try:
                shutil.rmtree(self.stage)
            except OSError as error:
                raise MediaCollectionError(f'Cannot remove temporary media collection {self.stage}: {error}') from error
            self.stage = None

    def _check_accepted(self):
        from source_relink_io import check_accepted
        for item in self.accepted_sources:
            self.check()
            try: check_accepted(item)
            except (OSError, ValueError, TypeError) as error:
                raise MediaCollectionError('Cannot collect an unreviewed source change: ' + str(error)) from error

    def _prepare(self):
        self.check()
        self._unlinked(self.destination)
        media = self.project.get('media') or {}
        originals = {key: item for key, item in media.items() if not item.get('synthetic') and not item.get('subclip_of')}
        self.accepted_sources = [copy.deepcopy(item) for item in originals.values()
                                 if item.get('source_relink_basis') is not None]
        self._check_accepted()
        groups = []
        # Refuse incomplete originals before copying anything or changing paths.
        for key, item in originals.items():
            self.check()
            try:
                paths = media_files(item)
                for path in paths:
                    self.check(); self._unlinked(path)
                if not paths or any(not path or not os.path.isfile(path) or not os.path.getsize(path) for path in paths):
                    raise ValueError('one or more original files are missing or empty')
            except (OSError, ValueError, TypeError, IndexError) as error:
                raise MediaCollectionError(f'Cannot collect {item.get("name", key)}: {error}') from error
            groups.append((key, item, paths))
        parents = {}
        for key, item in media.items():
            self.check()
            if not item.get('subclip_of'):
                continue
            seen = {key}
            parent = item['subclip_of']
            while True:
                if parent in seen or parent not in media:
                    raise MediaCollectionError(f'Subclip {item.get("name", key)} has a missing or cyclic parent')
                seen.add(parent)
                if not media[parent].get('subclip_of'):
                    break
                parent = media[parent]['subclip_of']
            parents[key] = parent

        self.destination.mkdir(parents=True, exist_ok=True)
        self.stage = Path(tempfile.mkdtemp(prefix='.collect-', dir=self.destination))
        self.final = self.destination / ('collection-' + uuid.uuid4().hex[:16])
        destinations = {}
        total = sum(len(paths) for _, _, paths in groups)
        for index, (key, item, paths) in enumerate(groups):
            # Different ranges of the same numbered sequence are separate groups.
            identity = (_identity(item['path']), tuple(paths) if item.get('sequence_frames') else ())
            if identity in destinations:
                relocated, verified_path = destinations[identity]
                rebase_source_acceptance(item, relocated, verified_path=verified_path)
                item['path'] = relocated
                continue
            reuse = all(_within(path, self.destination) for path in paths)
            relative = Path(f'media-{index + 1:04d}')
            if reuse:
                relocated = os.path.abspath(item['path'])
            else:
                (self.stage / relative).mkdir()
                basename = ('frame%09d' + Path(item['path']).suffix) if item.get('sequence_frames') else _portable_name(Path(item['path']).name)
                relocated = str(self.final / 'media' / relative / basename)
            new_item = {**item, 'path': relocated}
            targets = media_files(new_item)
            for source, target in zip(paths, targets):
                self.check()
                self.progress('Copying and verifying originals', len(self.files) / max(1, total), Path(source).name)
                copied_path = None if reuse else self.stage / Path(target).relative_to(self.final / 'media')
                try:
                    self._unlinked(source)
                    details = _verified_file(source, copied_path, check=self.copy_check) if self.copy_check else _verified_file(source, copied_path)
                except OSError as error:
                    raise MediaCollectionError(f'Cannot collect {source}: {error}') from error
                self.files.append({'source': os.path.abspath(source), 'destination': target, **details})
                if reuse: self.reused += 1
                else: self.copied += 1
            verified_path = paths[0] if reuse else self.stage / Path(targets[0]).relative_to(self.final / 'media')
            rebase_source_acceptance(item, relocated, verified_path=verified_path)
            item['path'] = relocated
            destinations[identity] = (relocated, verified_path)
        self._check_accepted()
        for key, parent in parents.items():
            if media[parent].get('path'):
                media[key]['path'] = media[parent]['path']
                if 'source_relink_basis' in media[parent]:
                    media[key]['source_relink_basis'] = copy.deepcopy(media[parent]['source_relink_basis'])
                else:
                    media[key].pop('source_relink_basis', None)
        with (self.stage / 'manifest.json').open('x', encoding='utf-8') as stream:
            json.dump({'version': 1, 'scope': 'original media', 'copied': self.copied, 'reused': self.reused, 'files': self.files}, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())

    @staticmethod
    def _unlinked(path):
        path = Path(path).absolute()
        if any(linked(part) for part in (path, *path.parents)):
            raise MediaCollectionError('Linked collection files/folders are not supported: ' + str(path))

    def publish(self):
        self.check()
        self._unlinked(self.destination)
        if self.published is not None:
            return self.published
        if self.stage is None:
            raise MediaCollectionError('Media collection has not been prepared')
        self._check_accepted()
        # An exclusive parent prevents accidentally replacing any existing files.
        self.final.mkdir()
        try:
            os.rename(self.stage, self.final / 'media')
        except BaseException:
            self.final.rmdir()
            raise
        self.stage = None
        self.published = self.final
        return self.published

    def result(self):
        if self.published is None:
            raise MediaCollectionError('Media collection has not been published')
        return {'dest': str(self.destination), 'copied': self.copied, 'reused': self.reused,
                'verified': len(self.files), 'manifest': str(self.published / 'media' / 'manifest.json')}
