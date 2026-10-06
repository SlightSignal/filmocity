"""Pure bounded edit plans for reviewed managed scene/silence analysis."""
import bisect
import copy
import hashlib
import json
import math

from overlap_normalization import _clock, slice_clip, MAX_CLIPS, MAX_COPIED_BYTES
from render import remap_segments
from timeline_time import frame_rate, from_frames, to_frames

MAX_RESULTS = 10_000
MAX_ITEMS = 100_000
MAX_VISITS = 1_000_000


def _json(value):
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError) as error:
        raise ValueError('Analysis edits require valid finite project data') from error


def _number(value, name):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(name + ' must be a finite number')
    return value


def _positive(value, name):
    value = _number(value, name)
    if value < 0: raise ValueError(name + ' must be nonnegative')
    return value


def _normalize(ranges):
    output = []
    for start, end in sorted(ranges):
        if output and start <= output[-1][1] + max(1e-12, math.ulp(start) * 8): output[-1][1] = max(end, output[-1][1])
        else: output.append([start, end])
    return output


class _RangeMap:
    def __init__(self, ranges):
        self.ranges = ranges
        self.starts = [a for a, _ in ranges]
        self.ends = [b for _, b in ranges]
        self.prefix = [0]
        for a, b in ranges: self.prefix.append(self.prefix[-1] + b-a)

    def time(self, value):
        index = bisect.bisect_left(self.starts, value)
        if not index: return value
        start, end = self.ranges[index-1]
        return max(0, value-self.prefix[index-1]-min(value-start, end-start))

    def intersects(self, start, end):
        index = bisect.bisect_right(self.ends, start+1e-10)
        return index < len(self.ranges) and self.ranges[index][0] < end-1e-10

    def kept(self, start, end):
        cursor = start
        for index in range(bisect.bisect_right(self.ends, start), len(self.ranges)):
            a, b = self.ranges[index]
            if a >= end: break
            if a > cursor: yield cursor, min(a, end)
            cursor = max(cursor, min(b, end))
            if cursor >= end: break
        if cursor < end: yield cursor, end


class _SourceClock:
    def __init__(self, clip, duration):
        self.clip, self.duration = clip, duration
        self.segments = remap_segments(clip) if clip.get('time_remap') else [(0, None, clip.get('speed', 1), clip.get('speed', 1), 0)]
        self.ends = [math.inf if end is None else sigma+(end-start)*(a+b)/2 for start, end, a, b, sigma in self.segments]

    def local(self, value):
        c = self.clip
        offset = c['out']-value if c.get('reverse') else value-c['in_']
        length = c['out']-c['in_']; epsilon = max(1e-10, math.ulp(length)*8)
        if offset < -epsilon or offset > length+epsilon: raise ValueError('Analysis returned a source time outside the analyzed clip')
        if offset <= 0: return 0
        if offset >= length: return self.duration
        index = bisect.bisect_left(self.ends, offset)
        start, end, a, b, sigma = self.segments[index]; need = offset-sigma
        if end is None or abs(b-a) < 1e-12: dt = need/a
        else:
            slope = (b-a)/(end-start)
            dt = 2*need/(a+math.sqrt(max(0, a*a+2*slope*need)))
        return min(self.duration, max(0, start+dt))


class _Budget:
    def __init__(self, project, identity):
        self.reserved = set()
        count = 0
        for seq in project.get('sequences', []):
            for tr in seq.get('tracks', []):
                for clip in tr.get('clips', []):
                    self.reserved.add(clip['id']); count += 1
            for key in ('markers', 'captions'):
                for item in seq.get(key) or []:
                    if item.get('id') is not None: self.reserved.add(item['id'])
        if count > MAX_CLIPS: raise ValueError('The project has too many clips for an analysis edit')
        self.count = count; self.copied = 0; self.visits = 0; self.identity = identity; self.index = 0

    def cost(self, item):
        self.copied += len(_json(item).encode('utf-8'))
        if self.copied > MAX_COPIED_BYTES: raise ValueError('This analysis would duplicate too much clip or annotation data')

    def clone(self, item):
        self.cost(item)
        return copy.deepcopy(item)

    def work(self, count=1):
        self.visits += count
        if self.visits > MAX_VISITS: raise ValueError('This analysis edit is too large; analyze a shorter clip')

    def id(self):
        while True:
            self.index += 1
            value = 'analysis_' + hashlib.sha256(f'{self.identity}:{self.index}'.encode()).hexdigest()[:24]
            if value not in self.reserved: self.reserved.add(value); return value
            self.work()


def _slice(clip, begin, end, duration):
    result = slice_clip(clip, begin, end, duration)
    # Keep recoverable offscreen source markers at original outer edges; each
    # interior boundary marker belongs only to the fragment on its right.
    if clip.get('markers'):
        result['markers'] = [dict(copy.deepcopy(marker), t=marker['t']-begin) for marker in clip['markers']
                             if (begin == 0 or marker['t'] >= begin) and (end == duration or marker['t'] < end)]
    return result


def _annotations(sequence, mapping, budget):
    result = {}
    for key in ('markers', 'captions'):
        if key not in sequence: continue
        items = sequence[key]
        if not isinstance(items, list) or len(items) > MAX_ITEMS: raise ValueError('Too many or invalid sequence annotations')
        output = []
        for item in items:
            budget.work()
            if not isinstance(item, dict): raise ValueError('Sequence annotations must be objects')
            if key == 'markers':
                start = _positive(item.get('time'), 'Marker time'); value = budget.clone(item); value['time'] = mapping.time(start)
                if 'duration' in item:
                    end = _positive(start+_positive(item['duration'], 'Marker duration'), 'Marker end')
                    value['duration'] = max(0, mapping.time(end)-value['time'])
                output.append(value)
            else:
                start, end = _positive(item.get('start'), 'Caption start'), _positive(item.get('end'), 'Caption end')
                if end <= start: raise ValueError('Caption end must follow its start')
                for index, (a, b) in enumerate(mapping.kept(start, end)):
                    budget.work(); value = budget.clone(item); value.update(start=mapping.time(a), end=mapping.time(b))
                    if index: value['id'] = budget.id()
                    output.append(value)
            if len(output) > MAX_ITEMS: raise ValueError('This analysis creates too many annotations')
        if output != items: result[key] = output
    for key in ('in_point', 'out_point'):
        if sequence.get(key) is not None: result[key] = mapping.time(_positive(sequence[key], 'Sequence range'))
    if sequence.get('in_point') is not None and sequence.get('out_point') is not None and sequence['out_point'] > sequence['in_point'] and result['out_point'] <= result['in_point']:
        result['in_point'] = result['out_point'] = None
    return {key: value for key, value in result.items() if value != sequence.get(key)}


def plan(project, payload, result, identity=''):
    """Return authoritative complete ops and a deterministic review fingerprint.

    Source signatures/file stamps and receipt/context state are verified by the
    task route. This planner validates source windows and all timeline changes.
    """
    if not isinstance(payload, dict) or not isinstance(result, dict): raise ValueError('Invalid analysis result')
    mode = payload.get('mode')
    if mode == 'remix':
        from audio_remix import edit_plan
        return edit_plan(project, payload, result, identity)
    if mode not in ('scenes', 'silences') or result.get('version') != 1 or result.get('kind') != mode or result.get('clock') != 'media': raise ValueError('Unsupported analysis result format')
    for key in ('media_id', 'sequence', 'clip_id', 'range'):
        if result.get(key) != payload.get(key): raise ValueError('Analysis result does not match its captured source')
    sequences = project.get('sequences') or []
    si = next((i for i, seq in enumerate(sequences) if seq['id'] == payload.get('sequence')), None)
    if si is None: raise ValueError('Open the analyzed sequence before applying this result')
    sequence = sequences[si]
    if len(_json(sequence).encode('utf-8')) > MAX_COPIED_BYTES: raise ValueError('Sequence data is too large for an analysis edit')
    found = [(ti, tr, c) for ti, tr in enumerate(sequence['tracks']) for c in tr['clips'] if c['id'] == payload.get('clip_id')]
    if len(found) != 1: raise ValueError('The analyzed clip is missing or has a duplicate ID')
    ti, track, clip = found[0]
    if track.get('locked'): raise ValueError('Unlock the analyzed clip’s track before applying this result')
    if clip.get('hold'): raise ValueError('Source analysis cannot establish timeline edits for a held frame; release the frame hold first')
    if clip.get('media_id') != payload.get('media_id') or clip.get('sequence_id'): raise ValueError('The analyzed clip source changed')
    media = project.get('media', {}).get(clip['media_id'])
    if not media or not media.get('has_video' if mode == 'scenes' else 'has_audio'): raise ValueError('The analyzed source has no required media stream')
    if mode == 'scenes' and track.get('kind') != 'video': raise ValueError('Scene detection needs a video-track clip')
    duration = _clock(clip)
    if clip['out'] > _positive(media.get('duration'), 'Media duration')+1e-9: raise ValueError('The analyzed clip exceeds its media source')
    if payload.get('range') != {'start': clip['in_'], 'end': clip['out']}: raise ValueError('The analyzed source range changed; run analysis again')
    budget = _Budget(project, identity or _json(payload)); clock = _SourceClock(clip, duration)
    fps = sequence.get('fps') or 30; rate = float(frame_rate(fps)); minimum = from_frames(1, fps) if track.get('kind') == 'video' else 1/48000
    if duration < minimum-1e-10: raise ValueError('Analysis editing needs at least one picture frame or audio sample')
    values = result.get('cuts' if mode == 'scenes' else 'silences')
    if not isinstance(values, list) or len(values) > MAX_RESULTS: raise ValueError(f'Analysis supports at most {MAX_RESULTS} results')
    replacements, cuts, ranges = {}, [], []
    if mode == 'scenes':
        for value in values:
            at = from_frames(to_frames(clip['start']+clock.local(_positive(value, 'Scene cut')), fps), fps)
            if at >= clip['start']+minimum-1e-9 and at <= clip['start']+duration-minimum+1e-9: cuts.append(at)
        cuts = sorted(set(cuts))
        if cuts:
            points = [clip['start'], *cuts, clip['start']+duration]; pieces = []
            for index, (a, b) in enumerate(zip(points, points[1:])):
                budget.work(); budget.cost(clip)
                piece = _slice(clip, 0 if index == 0 else a-clip['start'], duration if index == len(points)-2 else b-clip['start'], duration)
                piece['start'] = a
                if index: piece['id'] = budget.id()
                pieces.append(piece)
            replacements[ti] = [piece for c in track['clips'] for piece in (pieces if c['id'] == clip['id'] else [budget.clone(c)])]
        tracks = [track]
    else:
        tracks = [tr for tr in sequence['tracks'] if tr is track or not tr.get('locked') and tr.get('sync_lock') is not False]
        participants = {tr['id'] for tr in tracks}
        picture = any(tr.get('kind') == 'video' for tr in tracks)
        for item in values:
            if not isinstance(item, dict): raise ValueError('Silence ranges must have a source start and end')
            start, end = _positive(item.get('start'), 'Silence start'), _positive(item.get('end'), 'Silence end')
            if end <= start: raise ValueError('Silence end must follow its start')
            start, end = max(start, clip['in_']), min(end, clip['out'])
            if end <= start: continue
            a, b = sorted((clock.local(start), clock.local(end))); a += clip['start']; b += clip['start']
            if picture:
                a = from_frames(math.ceil(a*rate-1e-7), fps); b = from_frames(math.floor(b*rate+1e-7), fps)
            if b-a >= (from_frames(1, fps) if picture else 1/48000)-1e-10: ranges.append([a, b])
        ranges = _normalize(ranges); mapping = _RangeMap(ranges)
        for index, tr in enumerate(sequence['tracks']):
            if tr['id'] not in participants: continue
            output = []
            for c in tr['clips']:
                budget.work(); d = _clock(c); end = c['start']+d
                if c['id'] != clip['id']:
                    if mapping.intersects(c['start'], end): raise ValueError('Silence removal would cut other material on a participating track. Use Extract to cut through it, or adjust track sync locks')
                    moved = budget.clone(c); moved['start'] = mapping.time(c['start']); output.append(moved); continue
                for part, (a, b) in enumerate(mapping.kept(c['start'], end)):
                    if b-a < minimum-1e-10: raise ValueError('Detected silence would leave less than one picture frame or audio sample; adjust the analysis settings')
                    budget.work(); budget.cost(c)
                    piece = _slice(c, 0 if a == c['start'] else a-c['start'], d if b == end else b-c['start'], d)
                    piece['start'] = mapping.time(a)
                    if part: piece['id'] = budget.id()
                    output.append(piece)
            if output != tr['clips']: replacements[index] = output
    count_after = budget.count + sum(len(value)-len(sequence['tracks'][index]['clips']) for index, value in replacements.items())
    if count_after > MAX_CLIPS: raise ValueError('This analysis creates too many clips')
    ops = [{'op': 'set', 'path': f'/sequences/{si}/tracks/{index}/clips', 'value': value} for index, value in sorted(replacements.items())]
    if ranges:
        ops.extend({'op': 'set', 'path': f'/sequences/{si}/{key}', 'value': value} for key, value in _annotations(sequence, mapping, budget).items())
    if len(_json(ops).encode('utf-8')) > MAX_COPIED_BYTES: raise ValueError('This analysis edit is too large to apply')
    removed = sum(b-a for a, b in ranges)
    summary = {'kind': mode, 'cuts': cuts, 'ranges': ranges, 'removed_duration': removed, 'tracks': [tr['id'] for tr in tracks], 'clips_before': budget.count, 'clips_after': count_after,
               'message': f'{len(cuts)} scene cut(s)' if mode == 'scenes' else f'{len(ranges)} silence range(s), {removed:.3f} seconds removed'}
    fingerprint = hashlib.sha256(_json({'identity': identity, 'ops': ops, 'summary': summary}).encode('utf-8')).hexdigest()
    return {'ops': ops, 'summary': summary, 'fingerprint': fingerprint}
