"""Bounded editorial music retargeting; measured rhythm is advisory, never a beat guarantee."""
import copy
import math

from overlap_normalization import _clock, _source_offset

SAMPLE_RATE = 48000
MAX_SECONDS = 3600
MAX_ANALYSIS_SECONDS = 600
MAX_SEGMENTS = 128
MAX_RATIO = 8


def settings(body):
    from media_analysis import number
    target = number(body.get('target'), 'Remix target', minimum=1 / SAMPLE_RATE, maximum=MAX_SECONDS)
    bars = body.get('bars_per_phrase', 4)
    if isinstance(bars, bool) or not isinstance(bars, int) or not 1 <= bars <= 16:
        raise ValueError('Choose an integer phrase length from 1 to 16 bars')
    return {'target': target, 'bars_per_phrase': bars}


def target_duration(duration, target):
    from media_analysis import number
    duration = number(duration, 'Remix source duration', minimum=1 / SAMPLE_RATE, maximum=MAX_ANALYSIS_SECONDS)
    target = number(target, 'Remix target', minimum=1 / SAMPLE_RATE, maximum=MAX_SECONDS)
    target = math.floor(target * SAMPLE_RATE + .5) / SAMPLE_RATE
    if target > duration * MAX_RATIO + 1e-10:
        raise ValueError('Remix can repeat at most eight times the selected duration; choose a longer source window')
    return target


def design(duration, target, *, beat=None, phase=0, bars=4):
    """Clip-local intervals concatenate to target within one 48 kHz sample."""
    target = target_duration(duration, target)
    sample = 1 / SAMPLE_RATE
    warnings = []
    if abs(target - duration) < sample:
        return [(0, duration)], ['Target is within one audio sample of the original; no source edit is needed.']
    phrase = beat * 4 * bars if beat else None
    if target < duration:
        if target < 2 * sample:
            return [(0, target)], ['Target is too short to retain both ends; only the opening sample is retained.']
        lead = target / 2
        if beat:
            candidate = phase + round((lead - phase) / beat) * beat
            if sample <= candidate <= target - sample: lead = candidate
        lead = max(sample, min(target - sample, round(lead * SAMPLE_RATE) / SAMPLE_RATE))
        intervals = [(0, lead), (duration - (target - lead), duration)]
        warnings.append('Opening and ending are retained; the middle is removed to meet the requested duration.')
        if not phrase or phrase > duration or abs((duration-target)/phrase-round((duration-target)/phrase)) > 1e-6:
            warnings.append('Available phrase handles do not fit the exact target; the join is not guaranteed to land on matching musical phrases.')
    else:
        if duration < 4 * sample: raise ValueError('Repeating audio needs at least four source samples')
        a, b = duration / 4, duration * 3 / 4
        if phrase:
            candidate = phase + math.ceil((a - phase) / beat) * beat
            if candidate > 0 and candidate + phrase < duration:
                a, b = candidate, candidate + phrase
            else: warnings.append('There is no complete estimated phrase with both edge handles; a middle-section loop is used.')
        else: warnings.append('No reliable beat grid is available; a middle-section loop is used.')
        width, remaining = b-a, target-duration
        count = math.ceil(remaining / width)
        if count + 2 > MAX_SEGMENTS: raise ValueError('The requested remix would need too many repeated segments')
        lengths = [width] * max(0, count-1) + [remaining-width*max(0, count-1)]
        if len(lengths) > 1 and lengths[-1] < sample:
            lengths[-2] -= sample; lengths[-1] += sample
        intervals = [(0, b)] + [(a, a+length) for length in lengths] + [(b, duration)]
        warnings.append('The middle section is repeated while the original opening and ending are retained.')
        if lengths[-1] < width-1e-10: warnings.append('The last repeat is partial to meet the exact target; audition that join.')
    if len(intervals) > MAX_SEGMENTS or any(not 0 <= a < b <= duration or b-a < sample-1e-10 for a,b in intervals):
        raise ValueError('The remix cannot fit valid source handles within its bounded segment budget')
    if abs(sum(b-a for a,b in intervals)-target) > sample:
        raise ValueError('The remix did not reach the requested duration')
    warnings.append('Segments use butt joins, not crossfades. Audition each join; musical phrase structure is not detected.')
    return intervals, warnings


def _tempo(records, payload, clip, duration):
    """Normalized onset autocorrelation on a bounded 50 Hz timeline envelope."""
    from analysis_edits import _SourceClock
    clock = _SourceClock(clip, duration)
    finite = [level for _,level in records if math.isfinite(level)]
    silent = not finite
    if silent: return {'bpm': None, 'tempo_confidence': 0, 'beat': None, 'phase': 0, 'silent': True}
    if clip.get('reverse') or clip.get('time_remap'):
        return {'bpm': None, 'tempo_confidence': 0, 'beat': None, 'phase': 0, 'silent': False}
    rate = 50; maximum = max(finite); points = []
    for stamp, level in records:
        source = min(clip['out'], max(clip['in_'], payload['range']['start'] + stamp * payload['factor']))
        at = clock.local(source); amplitude = 10 ** ((level-maximum)/20) if math.isfinite(level) else 0.
        if points and at == points[-1][0]: points[-1] = (at, max(points[-1][1], amplitude))
        else: points.append((at, amplitude))
    # Resample the measured envelope continuously. Sparse interpreted timestamps
    # must not fabricate onsets by leaving zero bins between real measurements.
    envelope=[]; cursor=0
    for index in range(math.ceil(duration * rate)+1):
        at=index/rate
        while cursor+1 < len(points) and points[cursor+1][0] <= at: cursor+=1
        left=points[cursor]
        if cursor+1 == len(points) or at <= left[0]: value=left[1]
        else:
            right=points[cursor+1]; value=left[1]+(right[1]-left[1])*(at-left[0])/(right[0]-left[0])
        envelope.append(value)
    onsets = [0.] + [max(0., current-previous) for previous,current in zip(envelope,envelope[1:])]
    peak = max(onsets)
    if duration < 3 or peak < .02 or sum(x > peak*.2 for x in onsets) < 4:
        return {'bpm': None, 'tempo_confidence': 0, 'beat': None, 'phase': 0, 'silent': False}
    best, best_lag = 0., None
    for lag in range(math.ceil(rate*60/180), math.floor(rate*60/60)+1):
        energy_a = sum(x*x for x in onsets[lag:]); energy_b = sum(x*x for x in onsets[:-lag])
        score = sum(a*b for a,b in zip(onsets[lag:],onsets[:-lag])) / math.sqrt(energy_a*energy_b) if energy_a*energy_b else 0
        if score > best: best, best_lag = score, lag
    if best < .3: return {'bpm': None, 'tempo_confidence': best, 'beat': None, 'phase': 0, 'silent': False}
    phase = max(range(best_lag), key=lambda i:sum(onsets[i::best_lag])) / rate
    return {'bpm': 60*rate/best_lag, 'tempo_confidence': best, 'beat': best_lag/rate, 'phase': phase, 'silent': False}


def measured_plan(payload, records):
    clip = payload.get('clip') or {'start':0,'in_':payload['range']['start'],'out':payload['range']['end'],'speed':1}
    if clip.get('hold'): raise ValueError('Release frame hold before remixing audio')
    duration = _clock(clip); records = list(records)
    if not records: raise ValueError('The source produced no measurable audio samples')
    measured = _tempo(records, payload, clip, duration)
    intervals, warnings = design(duration, payload['settings']['target'], beat=measured['beat'], phase=measured['phase'], bars=payload['settings']['bars_per_phrase'])
    if measured['silent']: warnings.insert(0, 'The analyzed source window is silent; no tempo was estimated.')
    elif measured['bpm'] is None: warnings.insert(0, 'No reliable tempo was estimated; this is an editorial duration plan.')
    else: warnings.insert(0, 'Tempo is an onset-based estimate and can be half or double the musical beat; audition the result.')
    if clip.get('time_remap') or clip.get('reverse'):
        warnings.insert(0, 'Reversed or ramped timing is preserved per segment; tempo alignment is not estimated for this timing.')
    segments = []
    for begin,end in intervals:
        left = 0 if begin == 0 else _source_offset(clip, begin)
        right = clip['out']-clip['in_'] if end == duration else _source_offset(clip, end)
        a,b = (clip['out']-right,clip['out']-left) if clip.get('reverse') else (clip['in_']+left,clip['in_']+right)
        segments.append({'in':a,'out':b,'begin':begin,'end':end})
    achieved = sum(end-begin for begin,end in intervals)
    return {'segments':segments,'requested':payload['settings']['target'],'achieved':achieved,
            'bpm':measured['bpm'],'tempo_confidence':measured['tempo_confidence'],'silent':measured['silent'],
            'phrase':measured['beat']*4*payload['settings']['bars_per_phrase'] if measured['beat'] else None,
            'warnings':warnings,'sample_rate':SAMPLE_RATE,'joins':'butt','method':'bounded-editorial-v1'}


def edit_plan(project, payload, result, identity=''):
    """Canonical source-clock-preserving replacements, with no implicit ripple."""
    from analysis_edits import _json, _positive, _Budget, _slice
    import hashlib
    from overlap_normalization import MAX_CLIPS, MAX_COPIED_BYTES
    if result.get('version') != 1 or result.get('kind') != 'remix' or result.get('clock') != 'media' or result.get('method') != 'bounded-editorial-v1':
        raise ValueError('Unsupported remix result format')
    for key in ('media_id','sequence','clip_id','range'):
        if result.get(key) != payload.get(key): raise ValueError('Remix result does not match the captured source')
    found = [(si,ti,seq,tr,c) for si,seq in enumerate(project.get('sequences',[])) if seq['id'] == payload.get('sequence')
             for ti,tr in enumerate(seq['tracks']) for c in tr['clips'] if c['id'] == payload.get('clip_id')]
    if len(found) != 1: raise ValueError('The remix target clip is missing or duplicated')
    si,ti,sequence,track,clip = found[0]
    if track.get('locked') or track.get('kind') != 'audio': raise ValueError('Remix needs one clip on an unlocked audio track')
    if clip.get('hold') or clip.get('sequence_id'): raise ValueError('Remix needs ordinary source audio, without frame hold or nesting')
    if clip.get('media_id') != payload['media_id']: raise ValueError('The remix target source changed')
    media = project.get('media',{}).get(clip['media_id'])
    if not media or not media.get('has_audio'): raise ValueError('The remix source has no audio stream')
    duration = _clock(clip)
    if payload.get('range') != {'start':clip['in_'],'end':clip['out']} or clip['out'] > _positive(media.get('duration'),'Media duration')+1e-9:
        raise ValueError('The remix source range changed or exceeds available media')
    target = target_duration(duration,payload['settings']['target'])
    if result.get('requested') != payload['settings']['target']: raise ValueError('The remix target changed')
    segments = result.get('segments')
    if not isinstance(segments,list) or not 1 <= len(segments) <= MAX_SEGMENTS: raise ValueError('Invalid remix segment count')
    budget = _Budget(project,identity or _json(payload)); pieces=[]; cursor=clip['start']
    if len(_json(sequence).encode()) > MAX_COPIED_BYTES: raise ValueError('Sequence is too large for this remix')
    for index,segment in enumerate(segments):
        if not isinstance(segment,dict): raise ValueError('Invalid remix source window')
        a,b = _positive(segment.get('begin'),'Segment begin'),_positive(segment.get('end'),'Segment end')
        if not 0 <= a < b <= duration or b-a < 1/SAMPLE_RATE-1e-10: raise ValueError('Remix source window is outside the original clip')
        budget.work(); budget.cost(clip); piece = _slice(clip,a,b,duration)
        if abs(piece['in_']-_positive(segment.get('in'),'Source In')) > 1e-8 or abs(piece['out']-_positive(segment.get('out'),'Source Out')) > 1e-8:
            raise ValueError('Remix source coordinates do not match the clip clock')
        piece['start']=cursor
        if index: piece['id']=budget.id()
        pieces.append(piece); cursor += _clock(piece)
    achieved = cursor-clip['start']
    if abs(achieved-target) > 1/SAMPLE_RATE+1e-10 or abs(achieved-_positive(result.get('achieved'),'Achieved duration')) > 1e-8:
        raise ValueError('Remix segments do not reach the reviewed target duration')
    for other in track['clips']:
        if other is not clip and other['start'] < cursor-1e-10 and other['start']+_clock(other) > clip['start']+1e-10:
            raise ValueError('The remix would overlap another clip on this track; make room before applying')
    output = [piece for c in track['clips'] for piece in (pieces if c is clip else [budget.clone(c)])]
    if budget.count+len(pieces)-1 > MAX_CLIPS: raise ValueError('Remix would create too many clips')
    ops=[] if output==track['clips'] else [{'op':'set','path':f'/sequences/{si}/tracks/{ti}/clips','value':output}]
    if len(_json(ops).encode()) > MAX_COPIED_BYTES: raise ValueError('Remix edit is too large')
    warnings = result.get('warnings')
    if not isinstance(warnings,list) or len(warnings)>16 or any(not isinstance(w,str) or len(w)>1000 for w in warnings): raise ValueError('Invalid remix review warnings')
    summary={'kind':'remix','requested':result['requested'],'achieved':achieved,'segments':copy.deepcopy(segments),
             'bpm':result.get('bpm'),'tempo_confidence':result.get('tempo_confidence'),'warnings':copy.deepcopy(warnings),
             'tracks':[track['id']],'cuts':[p['start'] for p in pieces[1:]],'ranges':[],
             'message':f'Remix to {achieved:.6f} seconds in {len(pieces)} segment(s); audition joins before delivery.'}
    fingerprint=hashlib.sha256(_json({'identity':identity,'ops':ops,'summary':summary}).encode()).hexdigest()
    return {'ops':ops,'summary':summary,'fingerprint':fingerprint}
