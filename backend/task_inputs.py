"""Captured analysis dependencies; derived ingest metadata does not invalidate audio."""
import copy
import hashlib
import json
import os

from editing_workflow import sequence
from preflight import media_files

DERIVED = {'thumb', 'strip', 'wave', 'proxy', 'status', 'proxy_status', 'proxy_error', 'proxy_info', 'ingest_error', 'task_id', 'added', 'ingest_token', 'workflow_import'}
PRESENTATION = {'name', 'markers', 'captions', 'caption_style', 'guides', 'workflow'}


def source_stamp(media):
    if media.get('synthetic'): return []
    result = []
    for path in media_files(media):
        st = os.stat(path)
        if not os.path.isfile(path) or st.st_size <= 0: raise ValueError('Source media is empty or unavailable')
        result.append([os.path.abspath(path), st.st_size, st.st_mtime_ns, st.st_ino])
    return result


def portable_source_stamp(stamp):
    """Encode stat integers losslessly for persisted browser-facing metadata.

    File timestamps and Windows file IDs can exceed JavaScript's exact integer
    range. Internal task/probe stamps remain numeric; accepted source records
    use decimal strings so ordinary full-media edits retain the exact identity.
    """
    result = []
    for row in stamp:
        if (not isinstance(row, list) or len(row) != 4 or not isinstance(row[0], str)
                or any(type(value) is not int or value < 0 for value in row[1:])):
            raise ValueError('Invalid source file identity')
        result.append([row[0], *(str(value) for value in row[1:])])
    return result


def capture(project, sid):
    sequences, media, visiting = {}, {}, set()
    def visit(identity):
        if identity in visiting: raise ValueError('Nested sequence cycle')
        if identity in sequences: return
        visiting.add(identity); seq = sequence(project, identity)
        sequences[identity] = copy.deepcopy(seq)
        for track in seq['tracks']:
            for clip in track['clips']:
                if clip.get('sequence_id'): visit(clip['sequence_id'])
                mid = clip.get('media_id')
                if mid:
                    if mid not in project.get('media', {}): raise ValueError('A source media item is missing')
                    media[mid] = copy.deepcopy(project['media'][mid])
        visiting.remove(identity)
    visit(sid)
    snapshot = {'version': project.get('version', 3), 'id': project.get('id'), 'name': project.get('name'),
                'sequences': list(sequences.values()), 'media': media}
    # Keep the existing transcript in the signature so another correction or
    # transcription cannot be overwritten by a late analysis result.
    identity = {'sequences': {i: {k: v for k, v in seq.items() if k not in PRESENTATION} for i, seq in sequences.items()},
                'media': {i: {k: v for k, v in m.items() if k not in DERIVED} for i, m in media.items()},
                'files': {i: source_stamp(m) for i, m in media.items()}}
    signature = hashlib.sha256(json.dumps(identity, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return snapshot, signature
