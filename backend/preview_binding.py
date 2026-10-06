"""Bind rendered playback to one saved project, range and source-byte revision."""
import copy
import hashlib
import json
import math

from render import chunk_key, seq_total
from timeline_time import frame_range, from_frames

PREVIEW_PRESET = {'crf': 20, 'x264_preset': 'veryfast', 'vcodec': 'libx264',
                  'color_processing': 'rgb', 'incremental': False, 'loudnorm': False}


def preview_binding(project, context, sequence_id, ranged=False, revisions=None):
    sequence = next((s for s in project['sequences'] if s['id'] == sequence_id), None)
    if sequence is None:
        raise ValueError('Preview sequence no longer exists.')
    duration = float(seq_total(sequence))
    start, end = (sequence.get('in_point'), sequence.get('out_point')) if ranged else (0, duration)
    if (not isinstance(start, (int, float)) or not isinstance(end, (int, float)) or
            not math.isfinite(start) or not math.isfinite(end) or start < 0 or end <= start or end > duration):
        raise ValueError('Set a valid In and Out within the sequence before rendering a preview.')
    if ranged:
        first, last = frame_range(start, end, sequence['fps'])
        start, end = from_frames(first, sequence['fps']), from_frames(last, sequence['fps'])
    preset = dict(PREVIEW_PRESET, range=bool(ranged))
    identity = {'context': copy.deepcopy(context), 'sequence': sequence_id,
                'range': [start, end], 'ranged': bool(ranged)}
    # The project revision covers audio/mixer values as well as video edits.
    # chunk_key covers external sources, graphics, fonts, LUTs and renderer bytes.
    digest = json.dumps([identity, preset, chunk_key(project, sequence, preset, revisions)], sort_keys=True)
    return {**identity, 'signature': hashlib.sha256(digest.encode()).hexdigest()}
