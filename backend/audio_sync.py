"""Bounded, source-owned audio alignment with explicit quality and review."""
import array
import bisect
import cmath
import copy
import math
import os
import sys

import media_analysis
from render_context import RenderContext
from task_inputs import source_stamp
from timeline_time import frame_rate, to_frames, from_frames
from work_budget import work

RATE = 50
PCM_RATE = 8000
MAX_SECONDS = 120
MAX_NATIVE_SECONDS = 180
MAX_SOURCES = 8
MIN_OVERLAP = 2


def _ids(values, label):
    if not isinstance(values, list) or not 2 <= len(values) <= MAX_SOURCES or any(not isinstance(x, str) or not x for x in values) or len(set(values)) != len(values):
        raise ValueError(f'Choose 2–{MAX_SOURCES} distinct {label}; the first is the reference')
    return values


def capture(project, body, context):
    from audio_source_channels import channel_index
    if not isinstance(body, dict): raise ValueError('Synchronization input must be an object')
    if body.get('clip_ids') is not None and body.get('media_ids') is not None: raise ValueError('Choose timeline clips or raw sources, not both')
    mode = 'timeline' if body.get('clip_ids') is not None else 'media'
    identities = _ids(body.get('clip_ids') if mode == 'timeline' else body.get('media_ids'), 'clips' if mode == 'timeline' else 'media items')
    sequence = None
    if mode == 'timeline':
        sequence = next((s for s in project.get('sequences', []) if s.get('id') == body.get('sequence')), None)
        if sequence is None: raise ValueError('Choose an existing sequence')
        frame_rate(sequence.get('fps'))
    items = []
    for identity in identities:
        clip = track = None
        if sequence is not None:
            matches = [(t, c) for t in sequence.get('tracks', []) for c in t.get('clips', []) if c.get('id') == identity]
            if len(matches) != 1: raise ValueError('A selected synchronization clip is missing or duplicated')
            track, clip = matches[0]
            if track.get('kind') not in ('audio', 'video'): raise ValueError('Synchronization needs audio or video tracks')
            media_analysis.number(clip.get('start'), 'Clip start', minimum=0)
            if track.get('locked'): raise ValueError('Unlock every selected track before synchronization')
            if clip.get('enabled') is False: raise ValueError('Enable the selected clips before synchronization')
            if clip.get('hold') or clip.get('reverse') or clip.get('time_remap'):
                raise ValueError('Audio synchronization needs forward constant-speed clips. Render and Replace retimed clips first')
            mid = clip.get('media_id')
        else: mid = identity
        source, sub_in, factor, source_identity = media_analysis.source_inputs(project, mid)
        factor = media_analysis.number(factor, 'Source interpretation factor', minimum=1e-12, maximum=1e12)
        media = project['media'][mid]
        if not media.get('has_audio'): raise ValueError('Every selected source needs an audio stream')
        if channel_index(media, source) is None and source.get('channels') not in (None, 1, 2):
            raise ValueError('Synchronization supports mono/stereo sources or an explicitly selected source channel')
        total = media_analysis.number(media.get('duration'), 'Media duration', minimum=0)
        begin = media_analysis.number(clip.get('in_', 0) if clip else 0, 'Source In', minimum=0)
        end = media_analysis.number(clip.get('out') if clip else total, 'Source Out', minimum=0)
        speed = media_analysis.number(clip.get('speed', 1) if clip else 1, 'Clip speed', minimum=.01, maximum=100)
        if not begin < end <= total + 1e-8: raise ValueError('Synchronization requires a valid source range')
        source_total = media_analysis.number(source.get('duration'), 'Parent source duration', minimum=0)
        if (end + sub_in) / factor > source_total / media_analysis._factor(source) + 1e-8:
            raise ValueError('The synchronization window extends beyond its parent source')
        effective = media_analysis.number(speed / factor, 'Effective source rate', minimum=1e-12, maximum=1e12)
        duration = min((end - begin) / speed, MAX_SECONDS, MAX_NATIVE_SECONDS / effective)
        if duration < MIN_OVERLAP: raise ValueError('Each synchronization window needs at least two seconds of audio')
        if duration * effective < 2/RATE: raise ValueError('Source playback is too slow to provide enough native audio for synchronization')
        items.append({'id': identity, 'media_id': mid, 'source': copy.deepcopy(source), 'source_identity': source_identity,
                      'sub_in': sub_in, 'factor': factor, 'speed': speed, 'effective_rate': effective,
                      'begin': begin, 'end': end, 'duration': duration,
                      'clip': copy.deepcopy(clip), 'track_id': track['id'] if track else None,
                      'track_kind': track['kind'] if track else None})
    if any(abs(item['effective_rate'] / items[0]['effective_rate'] - 1) > 1e-8 for item in items):
        raise ValueError('Selected sources play at different effective rates. Match interpretation/speed or Render and Replace before synchronization')
    captured = {'mode': mode, 'sequence': sequence['id'] if sequence else None,
                'fps': sequence.get('fps') if sequence else None, 'items': items}
    # The decode snapshot contains harmless preparation/presentation fields;
    # only the explicitly captured source identity affects review validity.
    identity = {**captured, 'items': [{k:v for k,v in item.items() if k != 'source'} for item in items]}
    return {'version': 1, **captured, 'signature': media_analysis.digest(identity), 'context': copy.deepcopy(context)}


def validate_current(project, payload, context):
    if any(context.get(key) != payload['context'].get(key) for key in ('workspace', 'project')):
        raise ValueError('Open the original synchronization project first')
    body = {'sequence': payload['sequence'], 'clip_ids' if payload['mode'] == 'timeline' else 'media_ids': [item['id'] for item in payload['items']]}
    if capture(project, body, context)['signature'] != payload['signature']:
        raise ValueError('A selected clip, source or timing setting changed; analyze the current selection again')
    return True


def _sources_unchanged(payload):
    if any(source_stamp(item['source']) != item['source_identity']['stamp'] for item in payload['items']):
        raise ValueError('A source file changed during synchronization')


def _decode(item, context):
    from audio_source_channels import channel_index, input_chain
    start = (item['begin'] + item['sub_in']) / item['factor']
    duration = item['duration'] * item['effective_rate']; count = round(duration * PCM_RATE)
    pcm_path = context.new_file('.sync.f32le')
    command = ['ffmpeg', '-hide_banner', '-nostdin', '-nostats', '-v', 'error', '-threads', '2', '-filter_threads', '1', '-copyts', '-start_at_zero']
    if start > 2: command += ['-ss', f'{start - 2:.12f}']
    filters = (f'asetpts=PTS-({start:.12f})/TB,aresample={PCM_RATE}:async=1:first_pts=0:min_hard_comp=0.001,'
               f'apad=whole_len={count},atrim=end_sample={count},asetpts=N/SR/TB,asetnsamples=n=160:p=0,'
               'astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-')
    selected = {**item['source'], 'audio_alias':item['source_identity']['media'].get('audio_alias')}
    if channel_index(selected) is not None: filters = ','.join(input_chain(selected))+','+filters
    command += ['-i', item['source']['path'], '-map', '0:a:0', '-vn', '-af', filters, '-ac', '2', '-ar', str(PCM_RATE), '-c:a', 'pcm_f32le', '-f', 'f32le', '-y', pcm_path]
    metadata = media_analysis._run(command, context, duration, None)
    records = list(media_analysis._records(metadata, 'lavfi.astats.Overall.RMS_level', context.check_cancelled))
    levels = [level for _, level in records if math.isfinite(level)]
    if not levels or max(levels) < -70: raise ValueError('A selected source is silent or below the reliable analysis level')
    peak = max(levels); times = [stamp / item['effective_rate'] for stamp, _ in records]
    amplitudes = [10 ** ((level - peak) / 20) if math.isfinite(level) else 0 for _, level in records]
    envelope = []
    for index in range(math.floor(item['duration'] * RATE)):
        t = index / RATE; right = bisect.bisect_right(times, t)
        if right == 0: value = amplitudes[0]
        elif right == len(times): value = amplitudes[-1]
        else:
            left = right - 1; f = (t - times[left]) / max(1e-12, times[right] - times[left]); value = amplitudes[left] * (1-f) + amplitudes[right] * f
        envelope.append(value)
    mean = sum(envelope) / len(envelope)
    if sum((x-mean)**2 for x in envelope) / len(envelope) < .0004:
        raise ValueError('A selected source has too little changing audio to establish a unique synchronization point')
    if os.path.getsize(pcm_path) != count * 2 * 4: raise ValueError('Synchronization PCM output is incomplete or has unexpected channels')
    pcm = array.array('f')
    with open(pcm_path, 'rb') as stream: pcm.fromfile(stream, count * 2)
    if sys.byteorder != 'little': pcm.byteswap()
    if any(not math.isfinite(x) for x in pcm): raise ValueError('Synchronization audio contains nonfinite samples')
    return {'envelope': envelope, 'pcm': pcm, 'effective_rate': item['effective_rate']}


def _fft(values, inverse, check):
    n = len(values); j = 0
    for i in range(1, n):
        bit = n >> 1
        while j & bit: j ^= bit; bit >>= 1
        j ^= bit
        if i < j: values[i], values[j] = values[j], values[i]
    size = 2
    while size <= n:
        check(); root = cmath.exp((2j if inverse else -2j) * math.pi / size)
        for begin in range(0, n, size):
            value = 1 + 0j
            for i in range(begin, begin + size // 2):
                a, b = values[i], values[i+size//2] * value
                values[i], values[i+size//2] = a+b, a-b; value *= root
        size *= 2
    if inverse:
        for i in range(n): values[i] /= n


def correlate(a, b, check=lambda: None):
    """Overlap-normalized Pearson correlation via bounded FFT convolution."""
    if max(len(a), len(b)) > MAX_SECONDS * RATE or min(len(a), len(b)) < MIN_OVERLAP * RATE:
        raise ValueError('Synchronization correlation window is outside the supported bounds')
    n = 1 << (len(a) + len(b) - 2).bit_length()
    aa, bb = [complex(x) for x in reversed(a)] + [0j] * (n-len(a)), [complex(x) for x in b] + [0j] * (n-len(b))
    _fft(aa, False, check); _fft(bb, False, check)
    for i in range(n): aa[i] *= bb[i]
    _fft(aa, True, check)
    def sums(xs):
        out, squares = [0.], [0.]
        for x in xs: out.append(out[-1]+x); squares.append(squares[-1]+x*x)
        return out, squares
    sa, qa = sums(a); sb, qb = sums(b); scores = []
    minimum = max(MIN_OVERLAP * RATE, math.ceil(min(len(a), len(b)) * .5))
    for lag in range(-len(a) + minimum, len(b)-minimum+1):
        if lag % 256 == 0: check()
        first, last = max(0, -lag), min(len(a), len(b)-lag); count = last-first
        suma, sumb = sa[last]-sa[first], sb[last+lag]-sb[first+lag]
        vara = qa[last]-qa[first]-suma*suma/count; varb = qb[last+lag]-qb[first+lag]-sumb*sumb/count
        if min(vara, varb) < .0001 * count: continue
        score = (aa[len(a)-1+lag].real-suma*sumb/count) / math.sqrt(vara*varb)
        scores.append((min(1., score), lag, count))
    if not scores: raise ValueError('No usable changing-audio overlap was found')
    best = max(scores); runner = max([score for score, lag, count in scores if abs(lag-best[1]) > RATE*.2] or [-1])
    if best[0] < .65 or best[0]-runner < .06:
        raise ValueError('Audio match is weak or ambiguous; choose a longer, distinctive shared passage and align manually if needed')
    return {'offset': -best[1]/RATE, 'correlation': best[0], 'runner_up': runner,
            'overlap_seconds': best[2]/RATE, 'confidence': min(best[0], max(0., (best[0]-runner)*3)),
            'resolution': 1/RATE, 'method': 'envelope'}


def refine(reference, target, match, check=lambda: None):
    """Small, distributed waveform probes; uncertain phase falls back explicitly."""
    a, b = reference['pcm'], target['pcm']; scale = reference['effective_rate']
    def channel(pcm): return max((0, 1), key=lambda c: sum(pcm[i+c]**2 for i in range(0, len(pcm)-2, 160)))
    ac, bc = channel(a), channel(b)
    lag = round(-match['offset'] * PCM_RATE); radius = round(.04 * PCM_RATE)
    first = max(0, -lag + radius) + 2; last = min((len(a)//2-2)/scale, (len(b)//2-2)/scale-lag-radius) - 2
    if last-first < PCM_RATE: return match
    probes = [round(first+(last-first)*fraction)+i for fraction in (.15, .45, .75) for i in range(256)]
    if probes[-1] >= last: return match
    def sample(pcm, index, channel_):
        position = index * scale; left = int(position); fraction = position-left
        return pcm[2*left+channel_]*(1-fraction)+pcm[2*(left+1)+channel_]*fraction
    x = [sample(a, i, ac) for i in probes]; xm = sum(x)/len(x); x = [v-xm for v in x]; xx = sum(v*v for v in x)
    if xx < 1e-10: return match
    candidates = []
    for delta in range(-radius, radius+1):
        if delta % 32 == 0: check()
        y = [sample(b, i+lag+delta, bc) for i in probes]; ym = sum(y)/len(y)
        yy = sum(v*v for v in y)-len(y)*ym*ym
        if yy > 1e-10:
            score = sum(v*w for v,w in zip(x,y)) / math.sqrt(xx*yy)
            candidates.append((abs(score), delta, 1 if score >= 0 else -1))
    if not candidates: return match
    best = max(candidates); second = max([value for value,delta,_ in candidates if abs(delta-best[1]) > 2] or [0])
    if best[0] < .9 or best[0]-second < .03: return match
    return {**match, 'offset': -(lag+best[1])/PCM_RATE, 'resolution': max(1/PCM_RATE, 1/(PCM_RATE*scale)),
            'method': 'pcm', 'waveform_correlation': best[0], 'polarity': best[2]}


def analyze(payload, task=None, *, scratch_parent=None):
    holder = task.holder if task else {}; check = task.check if task else lambda: None
    _sources_unchanged(payload); decoded = []; matches = []
    with RenderContext(proc_holder=holder, scratch_parent=scratch_parent) as context:
        for i, item in enumerate(payload['items']):
            check()
            if task: task.progress('Decoding synchronization audio', i/len(payload['items'])*.5)
            decoded.append(_decode(item, context))
        for i, item in enumerate(payload['items']):
            if i == 0: match = {'offset': 0., 'correlation': 1., 'runner_up': None, 'overlap_seconds': item['duration'], 'confidence': 1., 'resolution': 0., 'method': 'reference'}
            else:
                if task: task.progress('Comparing shared audio', .5+i/len(payload['items'])*.45)
                with work(holder, 'probe', check=check):
                    match = correlate(decoded[0]['envelope'], decoded[i]['envelope'], check)
                    match['resolution'] = max(1/RATE, 1/(RATE*decoded[0]['effective_rate']))
                    match = refine(decoded[0], decoded[i], match, check)
            matches.append({'id': item['id'], 'media_id': item['media_id'], **match})
        check(); _sources_unchanged(payload)
    warnings = ['Alignment measures the captured opening windows, at most 120 timeline seconds each; it does not correct recorder clock drift. Resolution describes the analysis grid, not guaranteed alignment accuracy.']
    if any(m['method'] == 'envelope' for m in matches): warnings.append('Some matches use the envelope estimate (20 ms or coarser at slower source rates) because no unique fine waveform match was found. Audition synchronization before finishing.')
    return {'version': 1, 'kind': 'sync', 'mode': payload['mode'], 'clock': 'timeline-local', 'sequence': payload['sequence'],
            'clip_ids': [i['id'] for i in payload['items']] if payload['mode'] == 'timeline' else [],
            'media_ids': [i['media_id'] for i in payload['items']], 'reference': payload['items'][0]['id'],
            'offsets': {m['id']: m['offset'] for m in matches}, 'matches': matches, 'warnings': warnings,
            'signature': payload['signature'], 'context': copy.deepcopy(payload['context'])}


def plan(project, payload, result, context, identity):
    validate_current(project, payload, context)
    if result.get('kind') != 'sync' or result.get('signature') != payload['signature'] or result.get('mode') != payload['mode']:
        raise ValueError('Synchronization result does not match its captured sources')
    ops, moves, tracks = [], [], []; warnings = list(result.get('warnings') or [])
    if payload['mode'] == 'timeline':
        sequence = next(s for s in project['sequences'] if s['id'] == payload['sequence']); reference = payload['items'][0]['clip']
        starts = {}; deltas = {}
        for item in payload['items']:
            offset = media_analysis.number(result['offsets'].get(item['id']), 'Sync offset', minimum=-MAX_SECONDS, maximum=MAX_SECONDS)
            clip = item['clip']; raw = reference['start'] + offset
            if raw < -1e-9: raise ValueError('Alignment would move a clip before timeline zero; move the reference later and analyze again')
            track = next(t for t in sequence['tracks'] if t['id'] == item['track_id'])
            start = from_frames(to_frames(max(0,raw), sequence['fps']), sequence['fps']) if track['kind'] == 'video' else round(max(0,raw)*48000)/48000
            if item is payload['items'][0]: start = clip['start']
            starts[clip['id']], deltas[clip['id']] = start, start-clip['start']
            if abs(start-clip['start']) > 1e-10:
                ops.append({'op': 'set_clip', 'sequence': sequence['id'], 'track': track['id'], 'clip': {'id': clip['id'], 'start': start}})
                moves.append({'clip_id': clip['id'], 'from': clip['start'], 'to': start, 'residual': start-raw})
                if track['id'] not in tracks: tracks.append(track['id'])
        all_clips = [c for t in sequence['tracks'] for c in t.get('clips', [])]
        for item in payload['items']:
            c = item['clip']
            peers = [p for p in all_clips if p['id'] != c['id'] and (c.get('group') and p.get('group') == c['group'] or p.get('unlinked_from') == c['id'] or c.get('unlinked_from') == p['id'] or p.get('audio_detached_id') == c['id'] or c.get('audio_detached_id') == p['id'])]
            if any(abs(deltas.get(peer['id'], 0)-deltas[c['id']]) > 1e-9 for peer in peers):
                raise ValueError('Synchronization would separate grouped or detached audio partners; select/move the complete association together')
        from render import clip_dur
        for track in sequence['tracks']:
            clips = track.get('clips', [])
            for c in clips:
                if c['id'] not in starts or abs(deltas[c['id']]) <= 1e-10: continue
                start, end = starts[c['id']], starts[c['id']]+clip_dur(c)
                for other in clips:
                    if other['id'] == c['id']: continue
                    left = starts.get(other['id'], other['start']); right = left+clip_dur(other)
                    if max(start,left) < min(end,right)-1e-9: raise ValueError('Synchronization would overlap another clip; move the clips to clear tracks first')
    summary = {'kind': 'sync', 'tracks': tracks, 'moves': moves, 'warnings': warnings,
               'message': f'Review {len(moves)} clip movement(s); source ranges and effects remain unchanged.' if payload['mode'] == 'timeline' else 'Source offsets are ready for explicit multicam/merge creation; this result does not edit a timeline.'}
    return {'ops': ops, 'summary': summary, 'fingerprint': media_analysis.digest({'task': identity, 'signature': payload['signature'], 'context': context, 'result': result, 'ops': ops})}
