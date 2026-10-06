"""Exclusive export destinations with portable, Windows-safe leaf names.

Queued jobs never reuse a prior job's files. Direct renderer callers still own
their explicit destinations and the renderer's existing publication contract.
"""
import os
from pathlib import Path
import re
import uuid


def filesystem_path(path, *, force=False):
    """Private Windows I/O spelling; public destinations and URLs stay ordinary.

    Directory creation has a smaller legacy limit than file opening. Use the
    extended namespace from 240 UTF-16 units, also for child frames, locks and
    rollback copies. Resolve aliases at the publication boundary separately.
    """
    value = os.path.abspath(os.fspath(path))
    if os.name == 'nt' and not value.startswith('\\\\?\\') and (force or len(value.encode('utf-16-le')) // 2 >= 240):
        value = '\\\\?\\UNC\\' + value[2:] if value.startswith('\\\\') else '\\\\?\\' + value
    return value


def export_static_files(render_root):
    """Serve public render URLs with Starlette's existing containment policy."""
    from starlette.staticfiles import StaticFiles
    return StaticFiles(directory=filesystem_path(render_root, force=True))


def output_extension(preset):
    kind = preset.get('format', 'h264')
    if kind in ('h264', 'hevc'):
        container = preset.get('container', 'mp4')
        if container not in ('mp4', 'mov'):
            raise ValueError('H.264/HEVC exports require an MP4 or MOV container.')
        return container
    if kind == 'audio':
        codec = preset.get('acodec', 'wav')
        if codec not in ('wav', 'mp3', 'aac'):
            raise ValueError('Audio export must use WAV, MP3 or AAC.')
        return codec
    formats = {'av1': 'mp4', 'prores': 'mov', 'gif': 'gif', 'png_sequence': 'png', 'webm': 'webm'}
    if kind not in formats:
        raise ValueError('Unknown export format. Choose an available output format.')
    return formats[kind]


def safe_name(value):
    name = ''.join(c for c in str(value) if c.isalnum() or c in '-_ ').strip().replace(' ', '_')
    # Budget UTF-16 units, including astral letters, rather than assuming every
    # Unicode code point occupies one Windows filename character.
    name = name.encode('utf-16-le')[:160].decode('utf-16-le', 'ignore') or 'export'
    if re.fullmatch(r'CON|PRN|AUX|NUL|(?:COM|LPT)[1-9¹²³]', name, re.I):
        name = '_' + name
    return name


def reserve_export(render_root, name, preset):
    """mkdir is the reservation: collisions never adopt or clear an old folder."""
    extension, name = output_extension(preset), safe_name(name)
    root = Path(render_root)
    Path(filesystem_path(root)).mkdir(parents=True, exist_ok=True)
    for _ in range(32):
        jid = uuid.uuid4().hex[:16]
        folder = root / ('job-' + jid)
        try:
            Path(filesystem_path(folder)).mkdir()
        except FileExistsError:
            continue
        return {'id': jid, 'name': name, 'directory': str(folder),
                'path': str(folder / (name + '.' + extension)),
                'url': '/renders/' + folder.name + '/' + name + '.' + extension}
    raise OSError('Could not reserve a unique export folder. No existing output was changed.')


def output_path(render_root, url):
    """Resolve a stored output URL, accepting legacy flat and new nested paths."""
    if not isinstance(url, str) or not url.startswith('/renders/'):
        raise ValueError('Invalid render output URL.')
    relative = url[len('/renders/'):]
    if (any(c in relative for c in '\\:%?#') or any(ord(c) < 32 for c in relative) or
            any(part in ('', '.', '..') for part in relative.split('/'))):
        raise ValueError('Invalid render output path.')
    root = Path(render_root).resolve()
    path = root.joinpath(*relative.split('/')).resolve()
    if os.path.commonpath((root, path)) != str(root):
        raise ValueError('Render output escapes its data folder.')
    return filesystem_path(path)
