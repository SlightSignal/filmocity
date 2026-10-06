"""Atomic, list-priority track overlap resolution with source-clock slicing.

A sweep selects the last original list entry at each instant. Fragments never
become new priority winners. The caller owns project persistence and history.
"""
import copy
import heapq
import json
import math
from audio_contract import fade_spec, fade_window
from audio_ducking import curve as duck_curve
from render import clip_dur, remap_segments, kf_eval

MAX_CLIPS = 100_000
MAX_COPIED_POINTS = 1_000_000
MAX_COPIED_BYTES = 32 * 1024 * 1024


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{label} must be a finite number')
    return value


def _clock(clip):
    for key in ('start', 'in_', 'out'):
        _number(clip.get(key), 'Clip ' + key)
    if clip['start'] < 0 or clip['in_'] < 0 or clip['out'] <= clip['in_']:
        raise ValueError('Clips need nonnegative placement and a positive source range')
    speed = _number(clip.get('speed', 1), 'Clip speed')
    if speed < 1e-6: raise ValueError('Clip speed must be at least 0.000001; use the reverse setting for reverse playback')
    points = clip.get('time_remap') or []
    if not isinstance(points, list): raise ValueError('Speed ramp must be a list')
    if points and not clip.get('hold'):
        previous = -1
        for point in points:
            if not isinstance(point, dict): raise ValueError('Speed ramp points must be objects')
            t = _number(point.get('t'), 'Speed ramp time'); v = _number(point.get('v'), 'Speed ramp value')
            if t < 0 or t <= previous or v < 1e-6: raise ValueError('Speed ramps need increasing nonnegative times and speeds of at least 0.000001')
            previous = t
    duration = clip_dur(clip)
    if not math.isfinite(duration) or duration <= 0 or not math.isfinite(clip['start'] + duration):
        raise ValueError('Clip duration must be positive and finite')
    if clip['start']+duration-clip['start'] <= max(math.ulp(clip['start']+duration)*8, 1e-14):
        raise ValueError('Clip duration is too small to represent accurately at this timeline position')
    return duration


def _source_offset(clip, at):
    if not clip.get('time_remap'): return at * clip.get('speed', 1)
    for t0, t1, a, b, sigma in remap_segments(clip):
        if t1 is None or at <= t1:
            dt = at - t0
            return sigma + a * dt + (0 if t1 is None else (b-a) * dt*dt / (2*(t1-t0)))
    raise ValueError('Invalid speed ramp')


def _speed_at(clip, at):
    for t0, t1, a, b, _ in remap_segments(clip):
        if t1 is None or at < t1:
            return a if t1 is None else a + (b-a)*(at-t0)/(t1-t0)
    raise ValueError('Invalid speed ramp')



def _duck_value(points, at):
    # Ducking uses exact linear intervals; generic keyframes intentionally clamp
    # very short ease intervals and are not the ducking clock contract.
    if at <= points[0]['t']: return points[0]['v']
    for left, right in zip(points, points[1:]):
        if at < right['t']:
            if left.get('e') == 'hold': return left['v']
            return left['v'] + (right['v']-left['v'])*(at-left['t'])/(right['t']-left['t'])
    return points[-1]['v']


def _rebase_history(points, offset, kind):
    if not offset or not points: return copy.deepcopy(points)
    previous = next((p for p in reversed(points) if p['t'] <= offset), None)
    if kind == 'duck': value = _duck_value(points, offset)
    else: value = points[0]['v'] if offset < 0 else _speed_at({'time_remap':points}, offset)
    return [{'t':0, 'v':value, 'e':(previous or {}).get('e') or 'linear'}] + [dict(copy.deepcopy(p), t=p['t']-offset) for p in points if p['t'] > offset]


def _shift_source_history(clip, result, begin):
    original = clip.get('source_edit_window')
    if not isinstance(original, dict) or original.get('version') != 1: return
    window = copy.deepcopy(original)
    for kind in ('ramp', 'duck'):
        saved = original.get(kind)
        if saved is None: continue
        try:
            if not isinstance(saved, dict): raise ValueError('Invalid source edit history')
            offset = _number(saved.get('offset'), 'Source edit history offset'); points = saved.get('points')
            if not isinstance(points, list) or len(points) > 8192: raise ValueError('Source edit history is too large')
            if kind == 'duck': duck_curve(points); actual = (clip.get('keyframes') or {}).get('audio.duck_db')
            else:
                previous = -1
                for point in points:
                    t = _number(point.get('t'), 'History ramp time'); value = _number(point.get('v'), 'History ramp speed')
                    if t < 0 or t <= previous or value < 1e-6: raise ValueError('Invalid history ramp')
                    previous = t
                actual = clip.get('time_remap')
            if _rebase_history(points, offset, kind) != actual: raise ValueError('History was superseded by a manual edit')
            window[kind] = {'points':copy.deepcopy(points), 'offset':offset+begin}
        except (ValueError, TypeError, KeyError, AttributeError):
            # Never revive superseded knots merely because a later crop happens
            # to make the stale history look equal to its visible plateau.
            window.pop(kind, None)
    result['source_edit_window'] = window

def slice_clip(clip, begin, end, duration=None):
    """Keep a clip-local half-open timeline range without shifting its curves."""
    duration = _clock(clip) if duration is None else duration
    if not (0 <= begin < end <= duration): raise ValueError('Slice must be inside the clip')
    if begin == 0 and end == duration: return copy.deepcopy(clip)
    for side in ('in', 'out'):
        transition = clip.get('transition_'+side) or {}
        if not isinstance(transition, dict): raise ValueError('Picture transition must be an object')
        length = _number(transition.get('duration', 0), 'Picture transition duration')
        if length < 0: raise ValueError('Picture transition duration must not be negative')
        for at in (begin, end):
            if 0 < at < duration and ((side == 'in' and at < length) or (side == 'out' and at > duration-length)):
                raise ValueError('Overlap cuts inside a picture transition; move the overlap outside the transition or remove the transition first')
    if not isinstance(clip.get('keyframes') or {}, dict): raise ValueError('Clip keyframes must be an object')
    if not isinstance(clip.get('audio') or {}, dict): raise ValueError('Clip audio settings must be an object')
    if not isinstance(clip.get('markers') or [], list): raise ValueError('Clip markers must be a list')
    result = copy.deepcopy(clip); result['start'] = clip['start'] + begin
    if clip.get('hold'):
        result['out'] = clip['in_'] + end-begin
    else:
        first = 0 if begin == 0 else _source_offset(clip, begin)
        last = clip['out']-clip['in_'] if end == duration else _source_offset(clip, end)
        if not all(math.isfinite(v) for v in (first, last)) or not 0 <= first < last <= clip['out']-clip['in_']:
            raise ValueError('Overlap slice falls outside the source range')
        if clip.get('reverse'):
            result['in_'], result['out'] = clip['out']-last, clip['out']-first
        else:
            result['in_'], result['out'] = clip['in_']+first, clip['in_']+last
        if clip.get('time_remap') and begin:
            points = clip['time_remap']; previous = next((p for p in reversed(points) if p['t'] <= begin), None)
            result['time_remap'] = [{'t':0, 'v':_speed_at(clip, begin), 'e':(previous or {}).get('e', 'linear')}] + [dict(copy.deepcopy(p), t=p['t']-begin) for p in points if p['t'] > begin]
    if begin: result['transition_in'] = None
    if end < duration: result['transition_out'] = None
    for key, points in (clip.get('keyframes') or {}).items():
        if not isinstance(points, list) or any(not isinstance(p, dict) for p in points): raise ValueError('Keyframes must be lists of points')
        for p in points: _number(p.get('t'), 'Keyframe time'); _number(p.get('v'), 'Keyframe value')
        if key == 'audio.duck_db' and points:
            duck_curve(points)
            if begin:
                previous = next((p for p in reversed(points) if p['t'] <= begin), None)
                shifted = [{'t':0, 'v':_duck_value(points, begin), 'e':(previous or {}).get('e', 'linear')}] + [dict(copy.deepcopy(p), t=p['t']-begin) for p in points if p['t'] > begin]
                duck_curve(shifted); result['keyframes'][key] = shifted
        else:
            # Offscreen interpolation anchors preserve ease and Bezier curves.
            result['keyframes'][key] = [dict(copy.deepcopy(p), t=p['t']-begin) for p in points]
    if clip.get('markers'):
        for marker in clip['markers']:
            if not isinstance(marker, dict): raise ValueError('Clip markers must be objects')
            _number(marker.get('t'), 'Clip marker time')
        result['markers'] = [dict(copy.deepcopy(m), t=m['t']-begin) for m in clip['markers'] if begin <= m['t'] < end or (end == duration and m['t'] == end)]
    if any(f['duration'] > 0 for f in fade_spec(clip, duration)):
        window = fade_window(clip, duration); window['offset'] += begin
        result['audio'] = {**(result.get('audio') or {}), 'fade_window':window}
    _shift_source_history(clip, result, begin)
    return result


def _visible_spans(clips, durations):
    events = []
    for index, (clip, duration) in enumerate(zip(clips, durations)):
        events.extend(((clip['start'], 1, index), (clip['start']+duration, -1, index)))
    events.sort(); active = set(); priority = []; spans = [[] for _ in clips]; pos = 0
    while pos < len(events):
        at = events[pos][0]
        # Shared fractional-frame boundaries can differ by floating-point ULPs.
        # Coalesce only numerical noise, never the old one-microsecond edit gap.
        limit = at + max(math.ulp(at)*4, 1e-14)
        ending = []
        while pos < len(events) and events[pos][0] <= limit:
            _, kind, index = events[pos]
            if kind == 1: active.add(index); heapq.heappush(priority, -index)
            else: ending.append(index)
            pos += 1
        # Ends win within a numerical-noise cluster, including intervals wholly
        # inside it; an expired clip must never become a later phantom winner.
        active.difference_update(ending)
        while priority and -priority[0] not in active: heapq.heappop(priority)
        if not priority or pos == len(events): continue
        end = events[pos][0]; index = -priority[0]
        if spans[index] and spans[index][-1][1] == at: spans[index][-1][1] = end
        else: spans[index].append([at, end])
    return spans


def normalize_tracks(project):
    """Prepare every changed track before publishing any list into the project."""
    tracks = [(seq, track) for seq in project['sequences'] for track in seq['tracks']]
    output_count = sum(len(track['clips']) for _, track in tracks)
    if output_count > MAX_CLIPS:
        raise ValueError(f'Overlap normalization supports at most {MAX_CLIPS:,} clips per project')
    reserved = {clip['id'] for _, track in tracks for clip in track['clips']}
    planned = []; warnings = []; copied_points = 0; copied_bytes = 0
    for seq, track in tracks:
        if track.get('locked'): continue
        clips = track['clips']; durations = [_clock(c) for c in clips]
        visible = _visible_spans(clips, durations); output = []; changed = False
        for clip, duration, spans in zip(clips, durations, visible):
            local = [(max(0, start-clip['start']), min(duration, end-clip['start'])) for start, end in spans]
            # Normalize ULP differences back to the source clip's exact bounds.
            epsilon = max(math.ulp(clip['start']+duration)*4, 1e-14)
            local = [(0 if start <= epsilon else start, duration if duration-end <= epsilon else end) for start, end in local if end > start]
            if local == [(0, duration)]: output.append(clip); continue
            changed = True
            if not isinstance(clip.get('keyframes') or {}, dict): raise ValueError('Clip keyframes must be an object')
            if not isinstance(clip.get('markers') or [], list): raise ValueError('Clip markers must be a list')
            if local:
                copied_bytes += len(json.dumps(clip, ensure_ascii=False, allow_nan=False).encode('utf-8')) * len(local)
                if copied_bytes > MAX_COPIED_BYTES:
                    raise ValueError('Overlap would duplicate too much clip data; simplify clip data or resolve fewer overlaps at once')
            count = sum(len(v) for v in (clip.get('keyframes') or {}).values() if isinstance(v, list)) + len(clip.get('time_remap') or []) + len(clip.get('markers') or [])
            inherited = clip.get('source_edit_window')
            if isinstance(inherited, dict):
                count += sum(len(record['points']) for record in inherited.values() if isinstance(record, dict) and isinstance(record.get('points'), list))
            copied_points += count * len(local)
            if copied_points > MAX_COPIED_POINTS:
                raise ValueError('Overlap would duplicate too many automation points; simplify curves or resolve fewer overlaps at once')
            suffix = 1
            for part, (begin, end) in enumerate(local):
                fragment = slice_clip(clip, begin, end, duration)
                if part:
                    while True:
                        identifier = clip['id'] + ('_r' if suffix == 1 else f'_r{suffix}'); suffix += 1
                        if identifier not in reserved: break
                    reserved.add(identifier); fragment['id'] = identifier
                output.append(fragment)
            action = 'removed (fully covered)' if not local else ('trimmed' if len(local) == 1 else f'split into {len(local)} surviving pieces')
            warnings.append(f"{seq['id']}/{track['id']}: clip {clip['id']} {action} by later clips")
        if changed:
            output_count += len(output) - len(clips)
            planned.append((track, output))
    if output_count > MAX_CLIPS:
        raise ValueError(f'Overlap would create more than {MAX_CLIPS:,} clips; resolve fewer overlaps at once')
    for track, output in planned: track['clips'] = output
    return warnings
