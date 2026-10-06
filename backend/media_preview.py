"""Read-only original/proxy availability and source-bound playback requests.

File stamps detect ordinary replacement, not adversarial byte edits preserving
all stat fields. No source hashing or filesystem mutation occurs during reads.
"""
import hashlib
import json
import os
from pathlib import Path
import stat

from task_inputs import source_stamp, portable_source_stamp


class PreviewError(ValueError):
    def __init__(self, message, status=409): super().__init__(message); self.status = status


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_stamp(path):
    value = os.stat(path)
    if not stat.S_ISREG(value.st_mode) or value.st_size <= 0: raise OSError('Media is empty or is not a regular file')
    return [value.st_size, value.st_mtime_ns, value.st_ino]


def original(project, identity):
    visited = set()
    while True:
        if identity in visited: raise PreviewError('Source subclip cycle', 422)
        visited.add(identity); media = project.get('media', {}).get(identity)
        if not isinstance(media, dict): raise PreviewError('Source media is unavailable', 404)
        if media.get('audio_alias'):
            from source_commands import alias_source
            try: return identity, alias_source(project, media)
            except (ValueError, KeyError, TypeError) as error: raise PreviewError(str(error), 409) from error
        if not media.get('subclip_of'): return identity, media
        identity = media['subclip_of']


def proxy_path(root, reference):
    if not isinstance(reference, str) or not reference.startswith('/proxies/'):
        raise PreviewError('Proxy reference is invalid')
    name = reference[len('/proxies/'):]
    if not name or name in ('.', '..') or '/' in name or '\\' in name or ':' in name:
        raise PreviewError('Proxy reference is invalid')
    directory = Path(root) / 'proxies'; path = directory / name
    for item in (directory, path):
        if item.is_symlink() or getattr(item, 'is_junction', lambda: False)():
            raise PreviewError('Linked proxy files or directories are not supported')
    return str(path)


def describe(root, project, identity, *, memo=None):
    sid, media = original(project, identity)
    if memo is not None and sid in memo: return dict(memo[sid])
    generation = digest([sid, media.get('path'), media.get('ingest_token'), media.get('added'), media.get('input_opts'), media.get('sequence_frames'), media.get('audio_alias_basis')])
    stamp = None
    try:
        stamp = source_stamp(media)
        online = bool(media.get('synthetic')) or bool(stamp) and all(s[1] > 0 and os.path.isfile(s[0]) for s in stamp)
    except (OSError, ValueError, TypeError, IndexError): online = False
    if not online: stamp = None
    info = media.get('proxy_info') or {}; state, available, px_stamp = 'none', False, None
    if media.get('proxy'):
        try:
            path = proxy_path(root, media['proxy']); px_stamp = file_stamp(path)
            state = 'unverified'; available = True
            if info.get('file_stamp') and info['file_stamp'] != px_stamp:
                state, available = 'changed', False
            elif online and info.get('source_signature') and info['source_signature'] != digest(stamp):
                state, available = 'stale_source', False
            elif media.get('audio_alias') and info.get('audio_alias_basis') != media.get('audio_alias_basis'):
                state, available = 'stale_source', False
            elif isinstance(media.get('audio_alias'), dict) and 'channel_index' in media['audio_alias'] and info.get('channel_index') != media['audio_alias']['channel_index']:
                state, available = 'invalid', False
            elif info.get('file_stamp') and info.get('source_signature') and info.get('validation') in ('all_picture_timestamps_and_primary_audio_metadata', 'audio_alias_common_clock_metadata'): state = 'ready'
        except PreviewError: state = 'invalid'
        except FileNotFoundError: state = 'missing'
        except OSError: state = 'unreadable'
    accepted = media.get('source_relink_basis')
    if online and accepted is not None and (not isinstance(accepted, dict) or accepted.get('version') != 1 or accepted.get('stamp') != portable_source_stamp(stamp)):
        state, available = 'stale_source', False
    result = {'source_id': sid, 'generation': generation, 'original_online': online,
              'proxy_state': state, 'proxy_available': available,
              'original_file_stamp': stamp[0][1:] if stamp and not media.get('sequence_frames') else None,
              'proxy_file_stamp': px_stamp if available else None,
              'original_lease': digest([generation, stamp]) if online else None,
              'proxy_lease': digest([generation, media.get('proxy'), px_stamp, stamp]) if available else None}
    if memo is not None: memo[sid] = result
    return dict(result)


def catalog(root, project):
    memo, result = {}, {}
    for identity in project.get('media', {}):
        try: result[identity] = describe(root, project, identity, memo=memo)
        except PreviewError as error: result[identity] = {'original_online': False, 'proxy_available': False, 'proxy_state': 'invalid', 'message': str(error)}
    return result


def resolve(root, project, identity, query, *, workspace, project_id):
    """Bound URLs never change source/project or silently substitute another file."""
    sid, media = original(project, identity); status = describe(root, project, identity)
    bound = any(key in query for key in ('workspace', 'project', 'generation', 'lease'))
    want_proxy = query.get('proxy') == '1'
    if bound:
        if query.get('workspace') != workspace or query.get('project') != project_id or query.get('generation') != status.get('generation'):
            raise PreviewError('The preview belongs to another project or source generation. Refresh the editor.')
        lease = status.get('proxy_lease' if want_proxy else 'original_lease')
        if not lease or query.get('lease') != lease:
            raise PreviewError('The original or proxy changed or is unavailable. Refresh media status, then rebuild the proxy or relink the original.')
    from audio_source_channels import channel_index
    if channel_index(media) is not None and (not want_proxy or status['proxy_state'] != 'ready' or not status['proxy_available']):
        raise PreviewError('Selected-channel audio requires its verified channel preview; prepare this alias before auditioning it', 409)
    use_proxy = want_proxy and status['proxy_available']
    path = proxy_path(root, media['proxy']) if use_proxy else media.get('path')
    if not path or not os.path.isfile(path): raise PreviewError('Original media is missing. Relink it, or select an available proxy for offline preview.', 404)
    if media.get('sequence_frames') and not use_proxy: raise PreviewError('Numbered images require a prepared video proxy for playback.', 422)
    expected_stamp = status['proxy_file_stamp' if use_proxy else 'original_file_stamp']
    if file_stamp(path) != expected_stamp: raise PreviewError('Media changed while resolving the preview')
    return path, expected_stamp, {'X-Filmocity-Media': 'proxy' if use_proxy else 'original',
                  'X-Filmocity-Proxy-State': status['proxy_state'],
                  'X-Filmocity-Fallback': 'original' if want_proxy and not use_proxy else 'none',
                  'ETag': '"'+digest([status['generation'], expected_stamp, 'proxy' if use_proxy else 'original'])+'"',
                  'Cache-Control': 'private, no-cache', 'Accept-Ranges': 'bytes'}


def byte_range(header, size):
    """Single byte range for browser seeks, including suffix and open-ended forms."""
    if header is None: return None
    import re
    match = re.fullmatch(r'bytes=(\d*)-(\d*)', header.strip())
    if not match or not any(match.groups()) or size <= 0: raise PreviewError('Invalid or unsatisfiable media byte range', 416)
    first, last = match.groups()
    if not first:
        length = int(last)
        if not length: raise PreviewError('Unsatisfiable media byte range', 416)
        return max(0, size-length), size-1
    start = int(first); end = min(int(last), size-1) if last else size-1
    if start >= size or end < start: raise PreviewError('Unsatisfiable media byte range', 416)
    return start, end
