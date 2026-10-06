"""Contained, exclusively owned uploads and validated font publication.

Client filenames are display labels. New media never overwrites a prior file;
font replacement happens only after a private complete file has been validated.
"""
import os
from pathlib import Path
import re
import threading
import uuid

from cache_paths import linked

FONT_LOCK = threading.Lock()


class UploadError(ValueError):
    pass


def display_name(value, fallback='Media', *, limit=240):
    name = str(value or fallback).replace('\\', '/').split('/')[-1]
    name = ''.join(c for c in name if ord(c) >= 32 and ord(c) != 127).strip()
    return (name[:limit] if limit is not None else name) or fallback


def suffix(name):
    value = Path(name).suffix
    return value if len(value) <= 12 and all(c.isascii() and (c.isalnum() or c == '.') for c in value) else ''


def font_name(value):
    name = display_name(value, 'font.ttf', limit=None)
    if not name.lower().endswith(('.ttf', '.otf', '.ttc')):
        raise UploadError('TTF, OTF or TTC only')
    name = re.sub(r'[<>:"/\\|?*]', '_', name).rstrip(' .')
    if re.fullmatch(r'CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]', name.split('.')[0].rstrip(' '), re.I):
        name = '_' + name
    # Reserve the extension while keeping the full Windows leaf under 255
    # UTF-16 units, including astral Unicode characters.
    extension = name[-4:]
    stem = name[:-len(extension)].encode('utf-16-le')[:240].decode('utf-16-le', 'ignore')
    return (stem or 'font') + extension


def _identity(info):
    return info.st_dev, info.st_ino


def directory(root, folder):
    if folder not in ('media', 'fonts'):
        raise UploadError('Unknown upload destination')
    root = Path(root).absolute()
    destination = root / folder
    if any(linked(part) for part in (destination, *destination.parents)):
        raise UploadError('Linked upload folders are not supported')
    destination.mkdir(exist_ok=True)
    if linked(destination) or not destination.is_dir():
        raise UploadError('Upload destination is not a regular folder')
    return destination


class OwnedUpload:
    def __init__(self, root, folder, stream, extension='', *, check=lambda: None):
        self.folder = directory(root, folder)
        self.parent_identity = _identity(self.folder.stat())
        self.path = None
        self.identity = None
        self.retained = False
        if extension and (not extension.startswith('.') or len(extension) > 12
                          or not all(c.isascii() and (c.isalnum() or c == '.') for c in extension)):
            raise UploadError('Invalid upload extension')
        try:
            for _ in range(32):
                path = self.folder / ('upload-' + uuid.uuid4().hex + extension)
                check()
                self._check_folder()
                try:
                    writer = path.open('xb')
                except FileExistsError:
                    continue
                self.path = path
                with writer:
                    self.identity = _identity(os.fstat(writer.fileno()))
                    for block in iter(lambda: stream.read(1024 * 1024), b''):
                        check()
                        writer.write(block)
                    writer.flush()
                    os.fsync(writer.fileno())
                check()
                self._check_owned()
                break
            else:
                raise UploadError('Cannot reserve a fresh upload file; existing files were preserved')
        except BaseException:
            self.close()
            raise

    def _check_folder(self):
        if (any(linked(part) for part in (self.folder, *self.folder.parents))
                or _identity(self.folder.stat()) != self.parent_identity):
            raise UploadError('Upload destination changed; refresh before retrying')

    def _check_owned(self):
        self._check_folder()
        info = self.path.lstat()
        if (linked(self.path, info) or not self.path.is_file()
                or _identity(info) != self.identity or info.st_nlink != 1):
            raise UploadError('Upload file changed; it was retained for inspection')

    def retain(self):
        self._check_owned()
        self.retained = True

    def publish_font(self, name):
        if font_name(name) != name:
            raise UploadError('Invalid font destination filename')
        self._check_owned()
        target = self.folder / name
        if linked(target) or target.exists() and not target.is_file():
            raise UploadError('Font destination is linked or is not a regular file')
        # os.replace leaves the prior valid font intact on failure. No existing
        # target is ever truncated or removed before successful publication.
        os.replace(self.path, target)
        self.retained = True
        self.path = target
        return target

    def close(self):
        if self.path is not None and not self.retained:
            try:
                self._check_owned()
            except FileNotFoundError:
                return
            self.path.unlink()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
