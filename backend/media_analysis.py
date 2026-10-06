"""Bounded source-clock scene and silence analysis; results never edit a project."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import time

from render_context import RenderContext
import subprocesses as subprocess
from task_inputs import source_stamp
from timeline_time import interpretation_factor
from work_budget import work

MAX_SECONDS = 3600
MAX_RESULTS = 10000
MAX_OUTPUT = 32 * 1024 * 1024
SAMPLE_RATE = 8000
BLOCK = 160


def number(value, name, *, minimum=None, maximum=None):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'{name} must be a finite number')
    if minimum is not None and value < minimum or maximum is not None and value > maximum:
        raise ValueError(f'{name} must be between {minimum} and {maximum}')
    return float(value)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def _factor(media):
    return interpretation_factor(media)


def source_inputs(project, mid):
    from audio_source_channels import channel_index
    library = project.get('media', {})
    media = library.get(mid)
    if not isinstance(media, dict): raise ValueError('Choose existing source media')
    source = media
    if media.get('subclip_of'):
        source = library.get(media['subclip_of'])
        if not isinstance(source, dict): raise ValueError('The subclip parent is missing')
        if source.get('subclip_of'): raise ValueError('Nested subclip sources must be flattened before analysis')
    if source.get('synthetic') or source.get('is_image') or source.get('sequence_frames'):
        raise ValueError('Analysis currently supports original video or audio files')
    if not isinstance(source.get('path'), str) or not os.path.isabs(source['path']): raise ValueError('The source needs an absolute file path')
    offset = number(media.get('sub_in', 0) or 0, 'Subclip offset', minimum=0)
    parent_factor = _factor(source)
    selected_channel = channel_index(media, source)
    factor = _factor(media) if selected_channel is not None else parent_factor
    if selected_channel is None and media is not source and abs(_factor(media) - factor) > 1e-9:
        raise ValueError('Subclip interpretation differs from its parent; match interpretation before analysis')
    if selected_channel is not None:
        native_end = (offset + number(media.get('duration'), 'Channel source duration', minimum=1e-9)) / factor
        native_total = number(number(source.get('duration'), 'Parent duration', minimum=1e-9) / parent_factor,
                              'Parent native duration', minimum=1e-9)
        if not math.isfinite(native_end) or native_end > native_total + max(1e-12, math.ulp(native_total)*8):
            raise ValueError('The complete selected channel window extends beyond its physical source')
    # Presentation and generated-proxy changes do not change original source timing.
    fields = ('id', 'path', 'duration', 'native_duration', 'fps', 'frame_rate', 'interpret_fps', 'has_video',
              'has_audio', 'is_image', 'channels', 'sample_rate', 'subclip_of', 'sub_in', 'sub_out',
              'synthetic', 'sequence_frames', 'video_start', 'audio_start', 'start_time', 'audio_alias')
    identity = {'media': {key: media.get(key) for key in fields},
                'source': {key: source.get(key) for key in fields}, 'stamp': source_stamp(source)}
    return source, offset, factor, identity


def capture(project, body, mode, context):
    if mode not in ('scenes', 'silences', 'remix'): raise ValueError('Choose scene, silence, or remix analysis')
    if not isinstance(body, dict): raise ValueError('Analysis input must be an object')
    mid = body.get('media_id')
    if not isinstance(mid, str) or not mid: raise ValueError('Choose source media')
    source, offset, factor, identity = source_inputs(project, mid)
    media = project['media'][mid]
    if not media.get('has_video' if mode == 'scenes' else 'has_audio'):
        raise ValueError('The source has no picture stream' if mode == 'scenes' else 'The source has no audio stream')
    duration = number(media.get('duration'), 'Source duration', minimum=1e-9)
    begin = number(body.get('in', 0), 'Source In', minimum=0)
    end = number(duration if body.get('out') is None else body['out'], 'Source Out', minimum=0)
    if not begin < end or end > duration + 1e-9: raise ValueError('Choose a nonempty source range within the available media')
    end = min(end, duration)
    if end - begin > MAX_SECONDS or (end - begin) / factor > MAX_SECONDS:
        raise ValueError('Analyze at most one hour at a time; choose a shorter source range')
    if mode == 'scenes': settings = {'threshold': number(body.get('threshold', .35), 'Scene threshold', minimum=0, maximum=1)}
    elif mode == 'remix':
        from audio_remix import settings as remix_settings, MAX_ANALYSIS_SECONDS
        settings = remix_settings(body)
        if end-begin > MAX_ANALYSIS_SECONDS or (end-begin)/factor > MAX_ANALYSIS_SECONDS:
            raise ValueError('Remix analyzes at most ten minutes; select a shorter source window')
    else:
        settings = {'threshold_db': number(body.get('threshold_db', -38), 'Silence threshold', minimum=-120, maximum=0),
                    'min_gap': number(body.get('min_gap', .45), 'Minimum silence', minimum=0, maximum=60),
                    'pad': number(body.get('pad', .08), 'Speech padding', minimum=0, maximum=10)}
    sequence, clip_id = body.get('sequence'), body.get('clip_id')
    payload = {'version': 1, 'mode': mode, 'media_id': mid, 'range': {'start': begin, 'end': end}, 'settings': settings,
               'context': copy.deepcopy(context), 'source': copy.deepcopy(source), 'source_identity': identity,
               'signature': digest(identity), 'offset': offset, 'factor': factor,
               'sequence': sequence, 'clip_id': clip_id}
    if sequence is not None or clip_id is not None:
        if not isinstance(sequence, str) or not isinstance(clip_id, str): raise ValueError('Choose both a sequence and clip for analysis editing')
        matches = [(track, clip) for seq in project.get('sequences', []) if seq.get('id') == sequence
                   for track in seq.get('tracks', []) for clip in track.get('clips', []) if clip.get('id') == clip_id]
        if len(matches) != 1 or matches[0][1].get('media_id') != mid: raise ValueError('The analysis clip is missing or references another source')
        track, clip = matches[0]
        if clip.get('hold'): raise ValueError('Held frames cannot be edited from source analysis')
        if mode == 'remix' and (track.get('kind') != 'audio' or track.get('locked')):
            raise ValueError('Remix needs one clip on an unlocked audio track')
        if clip.get('in_') != begin or clip.get('out') != end: raise ValueError('Analysis editing must cover the selected clip source range')
        payload.update(clip=copy.deepcopy(clip), track_id=track['id'])
    if mode == 'remix':
        from audio_remix import target_duration
        from overlap_normalization import _clock
        target_duration(_clock(payload['clip']) if payload.get('clip') else end-begin, settings['target'])
    return payload


def validate_current(project, payload, context):
    original = payload['context']
    if any(original.get(key) != context.get(key) for key in ('workspace', 'project')):
        raise ValueError('Open the original analysis project first')
    if digest(source_inputs(project, payload['media_id'])[3]) != payload['signature']:
        raise ValueError('The source changed after analysis; analyze the current source again')
    if payload.get('clip_id') is not None:
        matches = [(track, clip) for seq in project.get('sequences', []) if seq.get('id') == payload['sequence']
                   for track in seq.get('tracks', []) for clip in track.get('clips', []) if clip.get('id') == payload['clip_id']]
        if len(matches) != 1 or matches[0][0]['id'] != payload['track_id'] or matches[0][1] != payload['clip']:
            raise ValueError('The selected clip changed after analysis; analyze its current source range again')
    return True


def _source_unchanged(payload):
    if source_stamp(payload['source']) != payload['source_identity']['stamp']:
        raise ValueError('The source file changed during analysis; start a new analysis')


def _tail(path, count=4000):
    with open(path, 'rb') as stream:
        stream.seek(max(0, os.path.getsize(path) - count))
        return stream.read().decode('utf-8', 'replace')


def _run(command, context, duration, progress):
    output, errors = context.new_file('.analysis'), context.new_file('.log')
    with work(context.holder, 'encode', check=context.check_cancelled):
        with open(output, 'wb') as stdout, open(errors, 'wb') as stderr:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr)
            context.holder['proc'] = child; deadline = time.monotonic() + 900; previous = -1.0
            try:
                while child.poll() is None:
                    context.check_cancelled()
                    if time.monotonic() > deadline: raise ValueError('Analysis timed out; choose a shorter range')
                    if os.path.getsize(output) > MAX_OUTPUT or os.path.getsize(errors) > MAX_OUTPUT:
                        raise ValueError('Analysis output exceeds the bounded result limit')
                    if progress:
                        stamps = re.findall(r'pts_time:([-+\deE.]+)', _tail(output))
                        if stamps:
                            fraction = min(.999, max(0, float(stamps[-1]) / duration))
                            if fraction > previous + .01: progress('Analyzing source', fraction); previous = fraction
                    time.sleep(.05)
                context.check_cancelled()
                if child.returncode: raise ValueError('Source analysis failed: ' + _tail(errors, 600))
            finally:
                if child.poll() is None: child.kill()
                child.wait()
                if context.holder.get('proc') is child: context.holder.pop('proc', None)
    if os.path.getsize(output) > MAX_OUTPUT or os.path.getsize(errors) > MAX_OUTPUT: raise ValueError('Analysis output exceeds the bounded result limit')
    return output


def _records(path, key, check):
    timestamp = None
    with open(path, encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            if index % 1000 == 0: check()
            match = re.search(r'pts_time:([-+\deE.]+)', line)
            if match: timestamp = float(match.group(1))
            elif line.startswith(key + '='):
                if timestamp is None: raise ValueError('Analysis returned a value without a timestamp')
                value = float(line.split('=', 1)[1])
                if not math.isfinite(timestamp) or math.isnan(value) or value == math.inf: raise ValueError('Analysis returned invalid timing or levels')
                yield timestamp, value


def silence_ranges(records, payload):
    """Convert measured RMS windows to clamped, padded media-clock quiet ranges."""
    begin, end = payload['range']['start'], payload['range']['end']; factor = payload['factor']
    settings = payload['settings']; records = list(records)
    if not records: raise ValueError('The source produced no measurable audio samples')
    finite_levels = [level for _, level in records if math.isfinite(level)]
    reference = max(finite_levels) if finite_levels else 0.0
    threshold = reference + settings['threshold_db']; raw = []; quiet_start = None; previous = -1
    for stamp, level in records:
        current = max(begin, min(end, begin + stamp * factor))
        if current < previous: raise ValueError('Analysis audio timestamps are out of order')
        previous = current
        quiet = level == -math.inf or level < threshold
        if quiet and quiet_start is None: quiet_start = current
        elif not quiet and quiet_start is not None:
            raw.append((quiet_start, current)); quiet_start = None
    if quiet_start is not None: raw.append((quiet_start, end))
    gaps = []
    for a, b in raw:
        if b - a + 1e-12 < settings['min_gap']: continue
        # Padding retains material beside speech, not at silent window edges.
        left = a + (settings['pad'] if a > begin else 0)
        right = b - (settings['pad'] if b < end else 0)
        if right > left: gaps.append({'start': max(begin, left), 'end': min(end, right)})
    if len(gaps) > MAX_RESULTS: raise ValueError('Too many silent ranges; choose a shorter analysis range')
    return gaps


def analyze(payload, task=None, *, ffmpeg='ffmpeg', scratch_parent=None):
    holder = task.holder if task else {}; progress = task.progress if task else None
    if progress: progress('Preparing source analysis', 0)
    _source_unchanged(payload)
    begin, end = payload['range']['start'], payload['range']['end']; factor = payload['factor']; offset = payload['offset']
    native_begin, native_end = (begin + offset) / factor, (end + offset) / factor
    duration = native_end - native_begin
    with RenderContext(proc_holder=holder, scratch_parent=scratch_parent) as context:
        command = [ffmpeg, '-hide_banner', '-nostdin', '-nostats', '-v', 'error', '-threads', '2', '-filter_threads', '1',
                   '-copyts', '-start_at_zero']
        # Seek with a short decoder preroll, retaining the source's common PTS.
        if native_begin > 2: command += ['-ss', f'{native_begin - 2:.12f}']
        command += ['-i', payload['source']['path']]
        if payload['mode'] == 'scenes':
            filters = f"trim=start={native_begin:.12f}:end={native_end:.12f},setpts=PTS-({native_begin:.12f})/TB,scale=320:-2,select='gt(scene,{payload['settings']['threshold']:.12f})',metadata=print:key=lavfi.scene_score:file=-"
            command += ['-map', '0:V:0', '-an', '-vf', filters, '-fps_mode', 'passthrough', '-f', 'null', '-']
        else:
            from audio_source_channels import channel_index, input_chain
            selected = {**payload['source'], 'audio_alias':payload['source_identity']['media'].get('audio_alias')}
            channel_filters = input_chain(selected) if channel_index(selected) is not None else []
            samples = max(1, round(duration * SAMPLE_RATE))
            # Relative PTS precedes resampling so delayed audio retains silence.
            filters = (f"asetpts=PTS-({native_begin:.12f})/TB,aresample={SAMPLE_RATE}:async=1:first_pts=0:min_hard_comp=0.001,"
                       f"apad=whole_len={samples},atrim=end_sample={samples},asetpts=N/SR/TB,"
                       f"asetnsamples=n={BLOCK}:p=0,astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-")
            if channel_filters: filters = ','.join(channel_filters)+','+filters
            command += ['-map', '0:a:0', '-vn', '-af', filters, '-f', 'null', '-']
        output = _run(command, context, duration, progress)
        result = {'version': 1, 'kind': payload['mode'], 'clock': 'media', 'media_id': payload['media_id'],
                  'sequence': payload.get('sequence'), 'clip_id': payload.get('clip_id'), 'range': copy.deepcopy(payload['range']),
                  'signature': payload['signature'], 'context': copy.deepcopy(payload['context'])}
        if payload['mode'] == 'scenes':
            cuts = sorted({begin + stamp * factor for stamp, score in _records(output, 'lavfi.scene_score', context.check_cancelled)
                           if begin < begin + stamp * factor < end})
            if len(cuts) > MAX_RESULTS: raise ValueError('Too many scene cuts; choose a shorter range')
            result.update(cuts=cuts, threshold=payload['settings']['threshold'])
        elif payload['mode'] == 'remix':
            from audio_remix import measured_plan
            result.update(measured_plan(payload, _records(output, 'lavfi.astats.Overall.RMS_level', context.check_cancelled)))
        else:
            gaps = silence_ranges(_records(output, 'lavfi.astats.Overall.RMS_level', context.check_cancelled), payload)
            result.update(silences=gaps, removed=sum(gap['end'] - gap['start'] for gap in gaps),
                          threshold_db=payload['settings']['threshold_db'], resolution=BLOCK / SAMPLE_RATE * factor)
        context.check_cancelled(); _source_unchanged(payload)
        return result
