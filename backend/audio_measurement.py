"""Bounded measurements of Filmocity's isolated, rendered stereo clip audio.

Capture, saved-project ownership and edit planning belong to audio_workflow.
This worker never changes a project or publishes media into its library.
"""
import array
import copy
import json
import math
import os
import statistics
import sys
import time

import render
import subprocesses as subprocess
from render_context import RenderContext
from work_budget import work
from ffmpeg_graph import externalize

SAMPLE_RATE = 48000
CHANNELS = 2
ENVELOPE_RATE = 200
MAX_SECONDS = 600
MAX_TOTAL_SECONDS = 3600
MAX_ITEMS = 50
MAX_RENDER_SEQUENCES = 256
SEEK_PREROLL = 2.
DECODE_POSTROLL = .1
MAX_BEATS = 8192
MAX_LOG_BYTES = 2 * 1024 * 1024
PROCESS_TIMEOUT = 900


def _number(value, label):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value):
        raise ValueError(label + ' must be finite')
    return value


def _tail(path, limit=4000):
    with open(path, 'rb') as stream:
        stream.seek(max(0, os.path.getsize(path)-limit))
        return stream.read().decode('utf-8', 'replace')


def _run(command, context, *, watched=(), timeout=PROCESS_TIMEOUT):
    """One admitted leaf process; bounded disk logs and joined cancellation."""
    command = externalize(command, context)
    output, errors = context.new_file('.measurement-out'), context.new_file('.measurement-log')
    with work(context.holder, 'encode', check=context.check_cancelled):
        with open(output, 'wb') as stdout, open(errors, 'wb') as stderr:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            context.holder['proc'] = child
            deadline = time.monotonic()+timeout
            try:
                while child.poll() is None:
                    context.check_cancelled()
                    if time.monotonic() >= deadline: raise ValueError('Audio measurement timed out; choose a shorter range')
                    for path, maximum in ((output, MAX_LOG_BYTES), (errors, MAX_LOG_BYTES), *watched):
                        if os.path.exists(path) and os.path.getsize(path) > maximum: raise ValueError('Audio measurement output exceeds its bounded limit')
                    time.sleep(.02)
                context.check_cancelled()
                if child.returncode: raise ValueError('Audio measurement failed: '+_tail(errors, 800))
            finally:
                if child.poll() is None: child.kill()
                child.wait()
                if context.holder.get('proc') is child: context.holder.pop('proc', None)
    for path, maximum in ((output, MAX_LOG_BYTES), (errors, MAX_LOG_BYTES), *watched):
        if os.path.exists(path) and os.path.getsize(path) > maximum: raise ValueError('Audio measurement output exceeds its bounded limit')
    return output, errors


def input_windows(project, sequence, *, aligned=False):
    """Native windows for each shared FFmpeg input, with bounded seek preroll.

    One input can feed several clips, so the bounding span (including gaps),
    rather than the sum of those clips, is the actual decoder workload.
    """
    library = project.get('media') or {}; windows = {}
    for track in sequence.get('tracks', []):
        for clip in track.get('clips', []):
            if clip.get('hold') or clip.get('enabled') is False or track.get('muted'): continue
            if render.audio_route(sequence, track, clip) is None: continue
            mid = clip.get('media_id'); media = library.get(mid)
            if not mid and clip.get('sequence_id'):
                child = next((value for value in project.get('sequences', []) if value.get('id') == clip['sequence_id']), None)
                if child is None: raise ValueError('Nested audio source is missing')
                angle = clip.get('multicam_angle', 0) if child.get('multicam') else None
                mid = 'nested:'+child['id']+(f':angle{angle}' if angle is not None else '')+':audio'
                media = {'has_audio':True}
            if not mid: continue
            if not isinstance(media, dict): raise ValueError('Measured audio source is missing')
            parent = library.get(media.get('subclip_of')) if media.get('subclip_of') else media
            if not isinstance(parent, dict): raise ValueError('Measured subclip parent is missing')
            # The renderer inherits these physical stream flags from the
            # parent before building audio; stale child flags cannot bypass
            # native-window admission or measurement-only seeking.
            if not parent.get('has_audio', media.get('has_audio')) or parent.get('is_image') or media.get('synthetic'): continue
            if parent.get('input_opts'): raise ValueError('Custom decoder input options are not supported for bounded audio measurement')
            factor = render.interpretation_factor(media)
            offset = _number(media.get('sub_in', 0) or 0, 'Measured subclip offset')
            begin = _number(clip.get('in_'), 'Measured source In')
            end = _number(clip.get('out'), 'Measured source Out')
            if not aligned:
                # Match the existing incoming-transition source extension before
                # admission, including nested media converted by prerender_nested.
                transition = clip.get('transition_in') or {}
                alignment = transition.get('align') or ('end' if transition.get('type', 'dissolve') in ('dissolve','fade') else 'start')
                length = _number(transition.get('duration', 0) or 0, 'Transition duration')
                if length > 0 and alignment in ('center','end') and not media.get('is_image'):
                    speed = _number(clip.get('speed', 1), 'Measured speed')
                    shift = min(length/2 if alignment == 'center' else length, begin/max(speed, 1e-6), clip.get('start', 0))
                    fps = float(render.frame_rate(sequence.get('fps', 30)))
                    shift = math.floor(shift*fps+1e-6)/fps
                    if shift > 1e-4: begin -= shift*speed
            if begin < 0 or offset < 0 or end <= begin: raise ValueError('Measured source range must be positive and nonempty')
            begin, end = (begin+offset)/factor, (end+offset)/factor
            if not math.isfinite(begin) or not math.isfinite(end): raise ValueError('Measured native source range must be finite')
            # Use the render mix sample lattice for exact origin subtraction.
            first, last = render.sample_index(begin), render.sample_index(end)
            if last <= first: raise ValueError('Measured native source range needs at least one sample')
            previous = windows.get(mid)
            windows[mid] = (min(first, previous[0]), max(last, previous[1])) if previous else (first,last)
    result = {}
    for mid, (first, last) in windows.items():
        span = (last-first)/SAMPLE_RATE
        if span > MAX_SECONDS: raise ValueError('Native audio source window exceeds ten minutes; shorten the source range or reduce speed')
        # Whole native seconds share every integer decoder sample lattice.
        # A fractional 48k-only seek changes 44.1k resampling phase. Round
        # forward to retain between one and two seconds of warm-up.
        origin = max(0, math.ceil(first/SAMPLE_RATE-SEEK_PREROLL))*SAMPLE_RATE
        # Retain the default resampler's right-hand context; truncating input
        # exactly at Source Out changes its final filter taps before atrim.
        result[mid] = {'seek':origin/SAMPLE_RATE, 'duration':(last-origin)/SAMPLE_RATE+DECODE_POSTROLL, 'span':span}
    return result


def estimated_work(project, sequence):
    """Conservative whole-child render cost, including repeated/angle variants."""
    if not isinstance(project, dict) or not isinstance(sequence, str): raise ValueError('Missing isolated measurement project')
    values = project.get('sequences')
    if not isinstance(values, list) or len(values) > 10000: raise ValueError('Too many or missing audio sequence dependencies')
    sequences = {}
    for value in values:
        if not isinstance(value, dict) or not isinstance(value.get('id'), str) or value['id'] in sequences:
            raise ValueError('Nested audio sequence identities must be unique')
        sequences[value['id']] = value
    pending = [(sequence, ())]; total = 0.; native = 0.; visits = 0
    while pending:
        sid, path = pending.pop()
        if sid in path or len(path) > 16 or sid not in sequences: raise ValueError('Nested audio has a cycle, missing source or excessive depth')
        current = sequences[sid]; visits += 1
        native += sum(value['duration'] for value in input_windows(project,current).values())
        if native > MAX_TOTAL_SECONDS:
            raise ValueError('Captured native audio exceeds the one-hour decoder budget; select fewer or shorter ranges')
        duration = _number(render.seq_total(current), 'Nested measurement duration'); total += duration
        if not 0 < duration <= MAX_SECONDS or total > MAX_TOTAL_SECONDS or visits > MAX_RENDER_SEQUENCES:
            raise ValueError('Captured nested audio exceeds the bounded render budget; render or shorten the nested source first')
        for track in current.get('tracks', []):
            for clip in track.get('clips', []):
                child = clip.get('sequence_id')
                if child:
                    pending.append((child, path+(sid,)))
                    if len(pending)+visits > MAX_RENDER_SEQUENCES:
                        raise ValueError('Captured nested audio exceeds the bounded render budget; render or simplify the nested source first')
    return {'seconds':total,'sequences':visits,'native_seconds':native}


def _pcm(item, context):
    duration = _number(item.get('duration'), 'Measured duration')
    frames = math.floor(duration*SAMPLE_RATE+.5)
    if not 1 <= frames <= MAX_SECONDS*SAMPLE_RATE: raise ValueError('Measure between one sample and ten minutes per item')
    project, sid = item.get('project'), item.get('sequence')
    if not isinstance(project, dict) or not isinstance(sid, str): raise ValueError('Audio measurement needs its captured isolated project')
    seq = next((s for s in project.get('sequences', []) if s.get('id') == sid), None)
    if seq is None or abs(render.seq_total(seq)-duration) > 1/SAMPLE_RATE: raise ValueError('The isolated audio duration does not match the captured range')
    raw = context.new_file('.f32le')
    # This opt-in follows the owned context into nested audio prerenders only.
    context.audio_measurement_seek = True
    command, _ = render.build_command(project, sid, raw, {'format':'audio', 'acodec':'wav_float'}, context=context)
    # The renderer provides the source/retime/effect graph. Only the lossless
    # output container and a redundant range/byte ceiling differ from export.
    command = list(command[:-1])+['-t', f'{frames/SAMPLE_RATE:.12f}', '-ac', str(CHANNELS), '-f', 'f32le', '-fs', str(frames*CHANNELS*4+65536), raw]
    command[1:1] = ['-nostdin', '-nostats', '-v', 'error', '-threads', '2', '-filter_complex_threads', '1']
    _run(command, context, watched=((raw, frames*CHANNELS*4+65536),))
    size = os.path.getsize(raw)
    if size != frames*CHANNELS*4:
        raise ValueError('The rendered measurement did not contain the complete captured sample range')
    return raw, frames


def _scan(path, frames, check):
    """Float sample peak/RMS and a non-cancelling stereo power envelope."""
    peak = 0.; squares = 0.; count = 0; envelope = []; window = SAMPLE_RATE//ENVELOPE_RATE
    current = 0.; in_window = 0
    with open(path, 'rb') as stream:
        while True:
            check(); block = stream.read(window*CHANNELS*4*128)
            if not block: break
            if len(block) % (CHANNELS*4): raise ValueError('Measurement PCM is truncated')
            samples = array.array('f'); samples.frombytes(block)
            if sys.byteorder != 'little': samples.byteswap()
            for index in range(0, len(samples), CHANNELS):
                left, right = samples[index], samples[index+1]
                if not math.isfinite(left) or not math.isfinite(right): raise ValueError('Measured audio contains nonfinite samples')
                peak = max(peak, abs(left), abs(right)); power = left*left+right*right
                squares += power; count += CHANNELS; current += power; in_window += 1
                if in_window == window:
                    envelope.append(math.sqrt(current/(window*CHANNELS))); current = 0.; in_window = 0
    if count != frames*CHANNELS: raise ValueError('Measurement PCM sample count changed')
    if in_window: envelope.append(math.sqrt(current/(in_window*CHANNELS)))
    return {'peak_db':20*math.log10(peak) if peak > 0 else None,
            'rms_db':10*math.log10(squares/count) if squares > 0 else None,
            'silent':peak == 0, 'envelope':envelope}


def _loudness(path, frames, context):
    # EBU R128 metadata retains LUFS precision without loudnorm's coarser
    # integrated histogram bins. Its input true-peak field is independently
    # useful; never use normalized output or the recommended target gain.
    command = ['ffmpeg','-hide_banner','-nostdin','-nostats','-v','info','-threads','2','-filter_threads','1',
        '-f','f32le','-ar',str(SAMPLE_RATE),'-ac',str(CHANNELS),'-i',path,
        '-af','ebur128=metadata=1:framelog=quiet,ametadata=print:key=lavfi.r128.I:file=-,loudnorm=I=-23:TP=-1:LRA=7:print_format=json','-f','null','-']
    output, errors = _run(command, context)
    text = _tail(errors, MAX_LOG_BYTES); decoder = json.JSONDecoder(); parsed = None
    for index, character in enumerate(text):
        if character != '{': continue
        try: value, _ = decoder.raw_decode(text[index:])
        except ValueError: continue
        if isinstance(value, dict) and all(key in value for key in ('input_i','input_tp','input_lra')): parsed = value
    if parsed is None: raise ValueError('FFmpeg returned no complete loudness measurement')
    def measurement(key):
        try: value = float(parsed[key])
        except (TypeError, ValueError) as error: raise ValueError('FFmpeg returned invalid loudness data') from error
        if value == -math.inf: return None
        if not math.isfinite(value): raise ValueError('FFmpeg returned nonfinite loudness data')
        return value
    integrated = None
    with open(output, encoding='utf-8') as stream:
        for line in stream:
            if line.startswith('lavfi.r128.I='):
                value = float(line.split('=', 1)[1])
                if not math.isfinite(value): raise ValueError('FFmpeg returned invalid integrated loudness')
                integrated = value if value > -70 else None
    if measurement('input_i') is not None and integrated is None and frames >= .4*SAMPLE_RATE:
        raise ValueError('The loudness scanners did not agree that this range passed the measurement gate')
    return {'integrated_lufs':integrated,'true_peak_dbtp':measurement('input_tp'),'loudness_range_lu':measurement('input_lra')}


def _beats(envelope, duration, check=lambda:None):
    """Measured onset candidates, only with a strong regular interval pattern.

    No missing beats are invented and no bar/downbeat inference is attempted.
    The median onset interval can still be half/double a listener's musical beat.
    """
    if duration < 3 or not envelope: raise ValueError('Beat measurement needs at least three seconds of distinctive rhythmic audio')
    maximum = max(envelope)
    if maximum < 1e-4: raise ValueError('Audio is silent or too quiet for reliable beat candidates')
    levels = [value/maximum for value in envelope]
    flux = [levels[0]]+[max(0.,b-a) for a,b in zip(levels,levels[1:])]
    peak = max(flux)
    if peak < .025: raise ValueError('Audio has no reliable changing beat onsets')
    ordered = sorted(flux)
    noise = ordered[math.floor(.75*(len(ordered)-1))]
    threshold = max(.04, peak*.2, noise*4)
    candidates = []
    for index, value in enumerate(flux):
        if index % 1024 == 0: check()
        if value < threshold or index and value < flux[index-1] or index+1 < len(flux) and value < flux[index+1]: continue
        if candidates and index-candidates[-1] < round(.12*ENVELOPE_RATE):
            if value > flux[candidates[-1]]: candidates[-1] = index
        else: candidates.append(index)
        if len(candidates) > MAX_BEATS: raise ValueError('Too many onset candidates; choose a shorter or less noisy passage')
    if len(candidates) < 5 or (candidates[-1]-candidates[0])/ENVELOPE_RATE < 2:
        raise ValueError('Too few distinct beat candidates; select a longer rhythmic passage')
    intervals = [(b-a)/ENVELOPE_RATE for a,b in zip(candidates,candidates[1:])]
    period = statistics.median(intervals)
    if not .25 <= period <= 1.5: raise ValueError('Onset spacing is outside the supported 40–240 BPM estimate range')
    relative = [abs(value-period)/period for value in intervals]
    regular = sum(value <= .10 for value in relative)/len(relative)
    deviation = statistics.median(relative)
    if regular < .90 or deviation > .06:
        raise ValueError('Onsets are irregular or ambiguous; no reliable beat marker sequence was found')
    beats = [index/ENVELOPE_RATE for index in candidates if index/ENVELOPE_RATE < duration]
    return {'beats':beats,'bpm':60/period,'tempo_confidence':max(0.,min(1.,regular*(1-deviation))),
            'resolution':1/ENVELOPE_RATE,'method':'measured-onset-regularity-v1'}


def _check_resources(items):
    # Capture owns resource identities and this engine only verifies immutable
    # source bytes/stats. Import lazily to avoid a workflow/worker cycle.
    from render_replace import stamp
    for item in items:
        resources = item.get('resources') or []
        if [stamp(resource[0]) for resource in resources] != resources:
            raise ValueError('A source or effect resource changed during audio measurement')


def analyze(payload, task=None, *, scratch_parent=None):
    if not isinstance(payload, dict) or payload.get('mode') not in ('peak','loudness','beats'):
        raise ValueError('Choose peak, loudness or beat measurement')
    items = payload.get('items')
    if not isinstance(items, list) or not 1 <= len(items) <= MAX_ITEMS: raise ValueError('Measure one to fifty captured items at a time')
    durations = [_number(item.get('duration'), 'Measured duration') for item in items]
    if any(not 0 < value <= MAX_SECONDS for value in durations) or sum(durations) > MAX_TOTAL_SECONDS:
        raise ValueError('Measure at most ten minutes per item and one hour in total')
    # Count nested references conservatively, including multicam variants.
    estimates = [estimated_work(item.get('project'), item.get('sequence')) for item in items]
    if sum(value['seconds'] for value in estimates) > MAX_TOTAL_SECONDS or sum(value['sequences'] for value in estimates) > MAX_RENDER_SEQUENCES:
        raise ValueError('Captured nested audio exceeds the task render budget; select fewer or shorter items')
    if sum(value['native_seconds'] for value in estimates) > MAX_TOTAL_SECONDS:
        raise ValueError('Captured native audio exceeds the task decoder budget; select fewer or shorter ranges')
    holder = task.holder if task else {}; check = task.check if task else lambda:None
    check(); _check_resources(items); measurements = []
    for index, item in enumerate(items):
        check()
        if task: task.progress(f'Rendering isolated audio {index+1}/{len(items)}',index/len(items))
        # Retire each item's PCM before starting the next, keeping disk/RAM use
        # bounded independently of the number of selected clips.
        with RenderContext(proc_holder=holder, scratch_parent=scratch_parent) as context:
            path, frames = _pcm(item, context)
            with work(holder,'probe',check=check): values = _scan(path, frames, context.check_cancelled)
            envelope = values.pop('envelope'); warnings = list(item.get('warnings') or [])
            warnings.append('Measured the isolated Filmocity stereo clip output, including clip processing and excluding track/master processing. Effect tails beyond the captured clip end are excluded.')
            clip = item.get('clip') or {}
            if clip.get('time_remap'):
                warnings.append('Variable-speed audio follows the current renderer’s piecewise average-speed approximation; this is not exact continuous retiming.')
            if (clip.get('keyframes') or {}).get('audio.gain_db'):
                warnings.append('Manual gain automation uses the renderer’s 128-sample control blocks.')
            row = {'id':item.get('id'),'media_id':item.get('media_id'),'duration':frames/SAMPLE_RATE,
                'sample_rate':SAMPLE_RATE,'channels':CHANNELS,**values,'integrated_lufs':None,'true_peak_dbtp':None,
                'loudness_range_lu':None,'beats':[],'bpm':None,'tempo_confidence':0.,'resolution':1/SAMPLE_RATE,
                'method':'rendered-float-pcm-v1','warnings':warnings}
            if values['silent']:
                if payload['mode'] == 'beats': raise ValueError('The captured audio is silent; no beat markers were estimated')
                warnings.append('The captured audio is digitally silent; no finite normalization measurement is available.')
            else:
                if payload['mode'] == 'loudness':
                    if task: task.progress(f'Measuring loudness {index+1}/{len(items)}',(index+.6)/len(items))
                    row.update(_loudness(path,frames,context));row['method']='rendered-pcm-ebu-r128-v1'
                    warnings.append('Integrated loudness uses FFmpeg EBU R128 metadata (0.001 LU reporting); true peak uses its loudnorm input scanner (0.01 dB reporting). Reporting precision is not guaranteed accuracy for every signal or playback DAC.')
                    if row['integrated_lufs'] is None: warnings.append('This audio is too short or below the loudness gate for a finite integrated LUFS measurement.')
                elif payload['mode'] == 'beats':
                    with work(holder,'probe',check=check): row.update(_beats(envelope,frames/SAMPLE_RATE,context.check_cancelled))
                    warnings.append('Markers are measured onset candidates, not an extrapolated grid. Tempo is an interval estimate that may be half or double the musical beat; bar and downbeat positions are not inferred.')
                    warnings.append('The 5 ms onset grid describes search spacing, not guaranteed musical beat accuracy. Audition the candidates before applying them.')
            check(); _check_resources([item]); measurements.append(row)
    check(); _check_resources(items)
    return {'version':1,'kind':'audio_analysis','mode':payload['mode'],'scope':payload.get('scope'),'clock':'clip-local',
        'sequence':payload.get('sequence'),'clip_ids':copy.deepcopy(payload.get('clip_ids') or []),
        'media_id':payload.get('media_id'),'range':copy.deepcopy(payload.get('range')),
        'signature':payload.get('signature'),'context':copy.deepcopy(payload.get('context')),'measurements':measurements}
