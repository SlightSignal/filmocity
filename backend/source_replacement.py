"""Atomic source replacement while retaining the existing clip-local edit clock.

Source In is the lower logical media bound, including for reverse clips. Out is
an optional available-window cap, never a request to change timeline duration.
"""
import copy
import json
import math

from overlap_normalization import _clock, _source_offset
from timeline_time import interpretation_factor, from_frames

MAX_POINTS = 8192
MAX_BYTES = 4 * 1024 * 1024


def number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    return value


def source(project, identity, *, replacement=False):
    library = project.get('media', {})
    media = library.get(identity) if isinstance(identity, str) else None
    if not isinstance(media, dict) or media.get('id') != identity:
        raise ValueError('Choose existing media from this project')
    parent = media
    if media.get('subclip_of'):
        parent = library.get(media['subclip_of'])
        if not isinstance(parent, dict) or parent.get('id') != media['subclip_of']:
            raise ValueError('The source subclip parent is missing')
        if parent.get('subclip_of'):
            raise ValueError('Flatten nested subclip sources before replacement')
        offset = number(media.get('sub_in', 0), 'Subclip offset')
        duration = number(media.get('duration'), 'Subclip duration')
        if offset < 0 or duration <= 0 or offset + duration > number(parent.get('duration'), 'Parent duration') + 1e-9:
            raise ValueError('The subclip extends outside its parent source')
        if abs(interpretation_factor(media) - interpretation_factor(parent)) > 1e-9:
            raise ValueError('Match subclip and parent interpretation before replacement')
    else:
        interpretation_factor(media)
        if media.get('sub_in', 0): raise ValueError('A media offset requires an existing subclip parent')
    if replacement and parent.get('synthetic'):
        raise ValueError('Generated sources need a rendered media file before source replacement')
    return media


def replace(clip, media, track_kind, source_in=0, source_out=None):
    if not isinstance(clip, dict) or not clip.get('media_id') or any(clip.get(k) for k in ('sequence_id', 'title', 'graphic', 'adjustment')):
        raise ValueError('Select an existing media clip to replace')
    if clip.get('audio_detached_id') or clip.get('unlinked_from'):
        raise ValueError('Relink detached audio before replacing its source')
    if track_kind not in ('video', 'audio') or not (media.get('has_audio') if track_kind == 'audio' else media.get('has_video') or media.get('is_image')):
        raise ValueError('The replacement source does not match the selected track')
    duration = _clock(clip)
    points = clip.get('time_remap')
    if points is None: points = []
    if not isinstance(points, list) or len(points) > MAX_POINTS: raise ValueError('Invalid or excessive speed ramp points')
    previous = -1
    for point in points:
        if not isinstance(point, dict): raise ValueError('Speed ramp points must be objects')
        t, v = number(point.get('t'), 'Ramp time'), number(point.get('v'), 'Ramp speed')
        if t < 0 or t <= previous or v < 1e-6: raise ValueError('Repair the selected speed ramp before replacement')
        previous = t
    if not clip.get('hold'):
        span = clip['out'] - clip['in_']
        if abs(_source_offset(clip, 0)) > 1e-9 or abs(_source_offset(clip, duration)-span) > max(1e-9, span*2.220446049250313e-16*32):
            raise ValueError('The source clock does not match the clip duration')
    source_in = number(source_in, 'Replacement Source In')
    if source_in < 0: raise ValueError('Replacement Source In must be nonnegative')
    limit = math.inf if media.get('is_image') else number(media.get('duration'), 'Replacement duration')
    if limit <= 0: raise ValueError('The replacement source has no available duration')
    if source_out is not None:
        source_out = number(source_out, 'Replacement Source Out')
        if source_out <= source_in: raise ValueError('Replacement Source Out must follow its In')
        limit = min(limit, source_out)
    span = duration if clip.get('hold') else clip['out']-clip['in_']
    out = source_in + span
    if not math.isfinite(out) or out <= source_in: raise ValueError('Replacement range cannot represent the edit accurately')
    if (source_in >= limit if clip.get('hold') else out > limit + max(1e-9, abs(limit)*2.220446049250313e-16*8)):
        raise ValueError('The chosen held frame is outside the replacement source' if clip.get('hold') else 'Replacement is too short for the retained speed and source range')
    changed = clip['media_id'] != media['id'] or source_in != clip['in_'] or out != clip['out']
    try: encoded = json.dumps(clip, ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError) as error: raise ValueError('Clip settings must be finite JSON data') from error
    if len(encoded) > MAX_BYTES: raise ValueError('This clip has too much metadata for source replacement')
    effects = clip.get('fx_stack') or []
    if not isinstance(effects, list) or any(not isinstance(fx, dict) for fx in effects): raise ValueError('Repair invalid clip effects before replacement')
    if changed and clip['media_id'] != media['id'] and any(fx.get('type') == 'stabilize' and fx.get('enabled') is not False for fx in effects):
        raise ValueError('Remove stabilization before replacing this source, then analyze the replacement')
    result = copy.deepcopy(clip)
    if not changed: return {'clip': result, 'changed': False, 'discardedMarkers': 0}
    result.update(media_id=media['id'], in_=source_in, out=out)
    for key in ('source_edit_window', 'rendered_from', 'render_replace_task'): result.pop(key, None)
    discarded = 0
    if result.get('markers') is not None:
        markers = result['markers']
        if not isinstance(markers, list) or len(markers) > MAX_POINTS or any(not isinstance(m, dict) for m in markers): raise ValueError('Repair invalid clip markers before replacement')
        for marker in markers: number(marker.get('t'), 'Clip marker time')
        result['markers'] = [m for m in markers if 0 <= m['t'] <= duration]
        discarded = len(markers)-len(result['markers'])
    # Addition at large source positions can lose span bits. Refuse instead of
    # changing the timeline edge or silently relying on overlap normalization.
    if abs(_clock(result)-duration) > max(1e-10, math.ulp(duration)*16):
        raise ValueError('Replacement range cannot preserve this duration accurately')
    return {'clip': result, 'changed': True, 'discardedMarkers': discarded}


def plan(project, body):
    if not isinstance(body, dict): raise ValueError('Replacement command must be an object')
    seqs = [(si, seq) for si, seq in enumerate(project.get('sequences', [])) if seq.get('id') == body.get('sequence')]
    if len(seqs) != 1: raise ValueError('Choose one existing sequence')
    si, sequence = seqs[0]
    matches = [(ti, ci, tr, c) for ti, tr in enumerate(sequence.get('tracks', [])) for ci, c in enumerate(tr.get('clips', [])) if c.get('id') == body.get('clip_id')]
    if len(matches) != 1: raise ValueError('The target clip is missing or its ID is ambiguous')
    ti, ci, track, clip = matches[0]
    if track.get('locked'): raise ValueError('Unlock the selected clip track before replacement')
    for seq in project.get('sequences', []):
        for tr in seq.get('tracks', []):
            if any(other is not clip and (other.get('unlinked_from') == clip['id'] or other.get('audio_detached_id') == clip['id']) for other in tr.get('clips', [])):
                raise ValueError('Relink detached audio before replacing its source')
    old = source(project, clip.get('media_id'))
    # Validate the old range as well; repairing malformed clocks is separate.
    replace(clip, old, track.get('kind'), clip.get('in_'))
    media = source(project, body.get('media_id'), replacement=True)
    value = replace(clip, media, track.get('kind'), body.get('in', 0), body.get('out'))
    duration = _clock(clip)
    minimum = from_frames(1, sequence.get('fps', 30)) if track.get('kind') == 'video' else 1/48000
    if duration < minimum-1e-10: raise ValueError('Keep at least one video frame or audio sample')
    if value['changed']:
        for other in track['clips']:
            if other is not clip and max(other['start'], clip['start']) < min(other['start']+_clock(other), clip['start']+duration)-1e-10:
                raise ValueError('Resolve the existing clip overlap before replacing its source')
    return {**value, 'path': f'/sequences/{si}/tracks/{ti}/clips/{ci}', 'summary': {
        'message': 'Source replaced; duration, effects and active clip markers kept. Review markers against the new source.' if value['changed'] else 'This clip already uses the selected source range.',
        'changed': value['changed'], 'discarded_markers': value['discardedMarkers'], 'sequence': sequence['id'], 'clip_id': clip['id']}}
