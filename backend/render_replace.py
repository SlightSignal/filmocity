"""Captured, lossless clip bakes; rendering never mutates a saved project."""
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil

import render
import subprocesses as subprocess
from audio_contract import fade_window
from media_metadata import summarize
from preflight import inspect_resources
from render_context import RenderContext
from task_inputs import DERIVED
from timeline_time import frame_rate

MAX_SECONDS = 3600
MAX_DEPENDENCIES = 10000


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def file_hash(path, check=lambda: None):
    result = hashlib.sha256()
    with open(path, 'rb') as stream:
        while True:
            check(); block = stream.read(1024 * 1024)
            if not block: break
            result.update(block)
    return result.hexdigest()


def stamp(path):
    stat = os.stat(path)
    if not os.path.isfile(path) or stat.st_size <= 0: raise ValueError('A bake dependency is empty or unavailable: ' + str(path))
    return [os.path.abspath(path), stat.st_size, stat.st_mtime_ns, stat.st_ino]


def _target(project, sid, cid):
    matches = [(sequence, track, clip) for sequence in project.get('sequences', []) if sequence.get('id') == sid
               for track in sequence.get('tracks', []) for clip in track.get('clips', []) if clip.get('id') == cid]
    if len(matches) != 1: raise ValueError('Choose one existing clip in the current sequence')
    sequence, track, clip = matches[0]
    if track.get('locked'): raise ValueError('Unlock the target track before rendering and replacing')
    if clip.get('enabled') is False: raise ValueError('Enable the target clip before rendering and replacing')
    if track.get('kind') not in ('video', 'audio'): raise ValueError('Choose a picture or audio clip')
    if clip.get('audio_detached_id') or clip.get('unlinked_from') or any(
            c.get('unlinked_from') == cid or c.get('audio_detached_id') == cid
            for t in sequence['tracks'] for c in t.get('clips', [])):
        raise ValueError('Relink detached audio before Render and Replace; independent pairs cannot be baked safely')
    if clip.get('adjustment') or (clip.get('blend') or 'normal').lower() != 'normal':
        raise ValueError('Nest dependent adjustment/blend composition before Render and Replace')
    from effects import track_matte_params
    if track_matte_params(clip): raise ValueError('Nest the picture and its track matte before Render and Replace')
    incoming = clip.get('transition_in') or {}
    align = incoming.get('align') or ('end' if incoming.get('type', 'dissolve') in ('dissolve', 'fade') else 'start')
    media = project.get('media', {}).get(clip.get('media_id'), {})
    length = float(incoming.get('duration', 0) or 0)
    if length > 0 and align in ('center', 'end') and clip.get('media_id') and not media.get('is_image'):
        shift = min(length / 2 if align == 'center' else length, clip.get('in_', 0) / max(clip.get('speed', 1), 1e-6), clip.get('start', 0))
        rate = float(frame_rate(sequence['fps']))
        if math.floor(shift * rate + 1e-6) / rate > 1e-4:
            raise ValueError('This incoming transition needs source handles. Use start alignment or bake a nest containing the transition')
    return sequence, track, clip


def _snapshot(project, sequence, track, clip):
    """Keep intrinsic picture/retiming; outer sound processing remains editable."""
    baked = copy.deepcopy(clip); baked['start'] = 0
    for key in ('transition_in', 'transition_out', 'audio_transition_in', 'audio_transition_out',
                'audio_fx', 'afx_stack', 'source_edit_window'):
        baked.pop(key, None)
    baked['audio'] = {key: copy.deepcopy(value) for key, value in (clip.get('audio') or {}).items()
                      if key in ('linked', 'channels', 'pan', 'maintain_pitch')}
    baked['audio']['linked'] = True  # Keep the replacement's live link switch usable.
    baked['keyframes'] = {key: copy.deepcopy(value) for key, value in (clip.get('keyframes') or {}).items() if not key.startswith('audio.')}
    isolated = {key: copy.deepcopy(sequence[key]) for key in ('id', 'width', 'height', 'fps')}
    isolated.update(master={}, captions=[], tracks=[{'id': track['id'], 'kind': track['kind'], 'index': 0, 'clips': [baked]}])
    snapshot = {'version': project.get('version', 3), 'media': {}, 'sequences': [isolated]}
    visiting, included = set(), {sequence['id']}
    def media(mid):
        if mid in snapshot['media']: return
        source = project.get('media', {}).get(mid)
        if not isinstance(source, dict): raise ValueError('A source media item is missing')
        snapshot['media'][mid] = {key: copy.deepcopy(value) for key, value in source.items() if key not in DERIVED}
        if source.get('subclip_of'): media(source['subclip_of'])
    def visit(seq, depth=0):
        if seq['id'] in visiting or depth > 16: raise ValueError('Nested sequence cycle or excessive depth')
        visiting.add(seq['id'])
        for tr in seq.get('tracks', []):
            for item in tr.get('clips', []):
                if item.get('media_id'): media(item['media_id'])
                sid = item.get('sequence_id')
                if sid:
                    if sid in visiting: raise ValueError('Nested sequence cycle')
                    if sid not in included:
                        child = next((s for s in project.get('sequences', []) if s.get('id') == sid), None)
                        if child is None: raise ValueError('A nested sequence is missing')
                        included.add(sid); child = copy.deepcopy(child); snapshot['sequences'].append(child); visit(child, depth + 1)
                if len(snapshot['media']) + sum(len(t.get('clips', [])) for s in snapshot['sequences'] for t in s.get('tracks', [])) > MAX_DEPENDENCIES:
                    raise ValueError('The bake contains too many dependencies; nest or simplify the selection')
        visiting.remove(seq['id'])
    visit(isolated)
    return snapshot


def capture(project, body, context, root):
    if not isinstance(body, dict): raise ValueError('Render and Replace input must be an object')
    sid, cid = body.get('sequence'), body.get('clip_id')
    if not isinstance(sid, str) or not isinstance(cid, str): raise ValueError('Choose a sequence and clip')
    sequence, track, clip = _target(project, sid, cid)
    duration = render.clip_dur(clip)
    if not math.isfinite(duration) or not 0 < duration <= MAX_SECONDS: raise ValueError('Bake a positive clip duration of at most one hour')
    if track['kind'] == 'video' and duration * float(frame_rate(sequence['fps'])) < .5:
        raise ValueError('The picture clip must contain at least one output frame')
    snapshot = _snapshot(project, sequence, track, clip)
    preset = {'format': 'audio', 'acodec': 'wav_float'} if track['kind'] == 'audio' else {'format': 'lossless', 'vcodec': 'ffv1', 'acodec': 'pcm_f32le'}
    report = inspect_resources(snapshot, sid, preset)
    errors = [item['message'] for item in report['issues'] if item['severity'] == 'error']
    if errors: raise ValueError('; '.join(errors[:8]))
    files = sorted({resource['path'] for resource in report['resources']})
    if len(files) > MAX_DEPENDENCIES: raise ValueError('The bake contains too many external resources')
    stamps = [stamp(path) for path in files]
    signature = digest({'project': snapshot, 'clip': clip, 'track_id': track['id'], 'resources': stamps})
    return {'version': 1, 'sequence': sid, 'clip_id': cid, 'track_id': track['id'], 'kind': track['kind'],
            'clip': copy.deepcopy(clip), 'project': snapshot, 'duration': duration, 'preset': preset,
            'resources': stamps, 'signature': signature, 'context': copy.deepcopy(context), 'root': os.path.abspath(root)}


def validate_current(project, payload, context, root):
    if os.path.abspath(root) != payload['root'] or any(context.get(key) != payload['context'].get(key) for key in ('workspace', 'project')):
        raise ValueError('Open the original Render and Replace project first')
    current = capture(project, {'sequence': payload['sequence'], 'clip_id': payload['clip_id']}, context, root)
    if current['signature'] != payload['signature']: raise ValueError('The clip, source or render resources changed; create a fresh bake')
    return True


def _check_sources(payload):
    if [stamp(item[0]) for item in payload['resources']] != payload['resources']:
        raise ValueError('A source or render resource changed during the bake')


def _folder(root, *parts):
    path = Path(root).resolve()
    for part in parts:
        path = path / part
        if path.is_symlink() or getattr(path, 'is_junction', lambda: False)(): raise ValueError('Linked bake artifact folders are not supported')
        path.mkdir(exist_ok=True)
    return path


def _identity(identity):
    if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{32}', identity): raise ValueError('Invalid bake task identity')
    return identity


def artifact_folder(root, identity):
    return _folder(root, 'tasks', 'render-replace', _identity(identity))


def _probe(path):
    result = subprocess.run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)], capture_output=True, text=True, timeout=60)
    if result.returncode: raise ValueError('Baked media cannot be inspected: ' + result.stderr[-400:])
    document = json.loads(result.stdout)
    return summarize(document), document


def _run(command, task, context, duration):
    return render._run_ffmpeg(command, total=duration, context=context,
        progress=lambda fraction: task.progress('Baking clip', min(.95, fraction * .95)))


def bake(payload, task):
    task.check(); _check_sources(payload)
    folder = artifact_folder(payload['root'], task.id)
    suffix = '.wav' if payload['kind'] == 'audio' else '.mkv'
    output, stage = folder / ('bake' + suffix), folder / ('pending' + suffix)
    if output.exists() or stage.exists(): raise ValueError('This task already owns an output; retry as a new task')
    try:
        with RenderContext(proc_holder=task.holder, scratch_parent=str(folder)) as context:
            command, graph = render.build_command(payload['project'], payload['sequence'], str(stage), payload['preset'],
                                                  context=context, alpha_output=payload['kind'] == 'video')
            (folder / 'command.json').write_text(json.dumps(list(command), indent=2))
            (folder / 'filter-graph.txt').write_text(graph)
            _run(command, task, context, payload['duration'])
            task.check(); info, document = _probe(stage)
            streams = document.get('streams', []); video = next((s for s in streams if s.get('codec_type') == 'video'), None)
            audio = next((s for s in streams if s.get('codec_type') == 'audio'), None)
            if not audio or audio.get('codec_name') != 'pcm_f32le': raise ValueError('The bake requires working float PCM audio encoding')
            sequence = payload['project']['sequences'][0]
            if payload['kind'] == 'video' and (not video or video.get('codec_name') != 'ffv1' or video.get('pix_fmt') not in ('bgra', 'rgba') or
                    (video.get('width'), video.get('height')) != (sequence['width'], sequence['height'])):
                raise ValueError('The bake requires working lossless FFV1 with alpha at the sequence dimensions')
            tolerance = 1 / float(frame_rate(sequence['fps'])) if video else 1 / 48000
            if abs(info['duration'] - payload['duration']) > tolerance + .002: raise ValueError('The baked duration does not match the selected clip')
            # Decode all produced streams; a metadata-only success is insufficient.
            _run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-i', str(stage), '-map', '0', '-f', 'null', '-'], task, context, payload['duration'])
            _check_sources(payload); task.check()
            os.replace(stage, output)
            info.update(id='bake_' + task.id, name='Rendered ' + payload['clip_id'], path=str(output), duration=payload['duration'],
                        status='ready', ingest_token=task.id, thumb=None, strip=None, wave=None)
            result = {'version': 1, 'kind': 'render_replace', 'sequence': payload['sequence'], 'clip_id': payload['clip_id'],
                      'track_id': payload['track_id'], 'signature': payload['signature'], 'duration': payload['duration'],
                      'media': info, 'sha256': file_hash(output, task.check), 'bytes': output.stat().st_size, 'stamp': stamp(output), 'probe': document,
                      'message': 'Picture and source timing baked losslessly; transitions, clip audio processing and track/master processing remain editable.'}
            preview = folder / ('preview.wav' if payload['kind'] == 'audio' else 'preview.png')
            try:
                args = ['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-y', '-i', str(output)]
                if payload['kind'] == 'audio': args += ['-map', '0:a:0', '-c:a', 'pcm_s16le', str(preview)]
                else: args += ['-ss', str(payload['duration'] / 2), '-frames:v', '1', '-pix_fmt', 'rgba', '-threads', '1', str(preview)]
                _run(args, task, context, payload['duration']); task.check()
                result['preview'] = {'kind': 'audio' if payload['kind'] == 'audio' else 'image',
                    'url': '/api/tasks/' + task.id + '/render-replace/preview', 'path': str(preview), 'sha256': file_hash(preview),
                    'time': payload['duration'] / 2 if payload['kind'] == 'video' else 0,
                    'description': 'Unmixed source listening copy' if payload['kind'] == 'audio' else 'Representative baked picture at the clip midpoint; transparency preserved'}
            except Exception as error:
                task.check(); result['preview_warning'] = 'Lossless bake verified; preview unavailable: ' + str(error)[-300:]
            _check_sources(payload); task.check(); task.progress('Ready to review', 1)
            return result
    finally:
        if stage.exists(): stage.unlink()


def verify_result(payload, result, identity, check=lambda: None, *, full_hash=True):
    folder = artifact_folder(payload['root'], identity)
    suffix = '.wav' if payload['kind'] == 'audio' else '.mkv'
    path = folder / ('bake' + suffix)
    if (result.get('kind') != 'render_replace' or result.get('signature') != payload['signature'] or
            result.get('sequence') != payload['sequence'] or result.get('clip_id') != payload['clip_id'] or
            result.get('media', {}).get('path') != str(path) or path.is_symlink() or not path.is_file() or
            result.get('bytes') != path.stat().st_size or result.get('stamp') != stamp(path) or
            full_hash and result.get('sha256') != file_hash(path, check)):
        raise ValueError('The verified bake output changed or is missing; retry the task')
    return path


def plan(project, payload, result, identity):
    verify_result(payload, result, identity)
    sequence, track, original = _target(project, payload['sequence'], payload['clip_id'])
    mid = result['media']['id']
    if mid in project.get('media', {}): raise ValueError('The replacement media ID is already used')
    media = copy.deepcopy(result['media']); suffix = Path(media['path']).suffix
    media['path'] = str(Path(payload['root']) / 'media' / 'rendered' / (identity + suffix))
    media['status'] = 'ingesting'
    keep = ('id', 'name', 'start', 'label', 'group', 'markers', 'transition_in', 'transition_out',
            'audio_transition_in', 'audio_transition_out', 'audio_fx', 'afx_stack', 'enabled', 'note')
    clip = {key: copy.deepcopy(original[key]) for key in keep if key in original}
    audio = copy.deepcopy(original.get('audio') or {})
    # Channel selection/balance were baked before the time transform, exactly
    # where the renderer processes them. Gain/duck/fade/effects remain local.
    audio.pop('channels', None); audio.pop('pan', None); audio.pop('maintain_pitch', None)
    audio['fade_window'] = fade_window(original, payload['duration'])
    clip.update(media_id=mid, in_=0.0, out=payload['duration'], speed=1.0, fit='contain',
                transform={'x': 0, 'y': 0, 'scale': 1, 'rotation': 0, 'opacity': 1}, audio=audio,
                keyframes={key: copy.deepcopy(value) for key, value in (original.get('keyframes') or {}).items() if key.startswith('audio.')},
                rendered_from=original['id'], render_replace_task=identity)
    history = original.get('source_edit_window')
    if isinstance(history, dict) and history.get('duck'):
        clip['source_edit_window'] = {'version': 1, 'duck': copy.deepcopy(history['duck'])}
    si, ti, ci = project['sequences'].index(sequence), sequence['tracks'].index(track), track['clips'].index(original)
    ops = [{'op': 'set', 'path': '/media/' + mid, 'value': media},
           {'op': 'set', 'path': f'/sequences/{si}/tracks/{ti}/clips/{ci}', 'value': clip}]
    summary = {'kind': 'render_replace', 'sequence': sequence['id'], 'track_id': track['id'], 'clip_id': clip['id'],
               'duration': payload['duration'], 'format': 'Float PCM WAV' if payload['kind'] == 'audio' else 'Lossless FFV1 BGRA + float PCM',
               'message': result['message'], 'media_id': mid, 'tracks': [track['id']],
               'baked': ['Source timing, channel selection and balance', 'Intrinsic picture effects and transform'] if payload['kind'] == 'video' else ['Source timing, channel selection and balance'],
               'retained': ['Outer transitions', 'Clip audio gain, fades, automation and effects', 'Track and master processing', 'Group and clip markers'],
               'warnings': [result['preview_warning']] if result.get('preview_warning') else []}
    return {'ops': ops, 'summary': summary, 'fingerprint': digest({'ops': ops, 'summary': summary, 'output': result['sha256']})}


def publish(payload, result, identity, *, proc_holder=None):
    holder = proc_holder if proc_holder is not None else {}
    def check():
        if holder.get('cancelled'): raise RuntimeError('cancelled')
    check(); source = verify_result(payload, result, identity, check)
    folder = _folder(payload['root'], 'media', 'rendered'); output = folder / (identity + source.suffix)
    if output.is_symlink(): raise ValueError('Replacement output must not be a link')
    if output.exists():
        if file_hash(output, check) != result['sha256']: raise ValueError('A different replacement output already exists')
        return str(output)
    stage = folder / (identity + '.pending' + source.suffix); owned = False
    try:
        with stage.open('xb') as target:
            owned = True
            with source.open('rb') as original:
                while True:
                    check(); block = original.read(1024 * 1024)
                    if not block: break
                    target.write(block)
            target.flush(); os.fsync(target.fileno())
        if file_hash(stage, check) != result['sha256']: raise ValueError('Replacement copy verification failed')
        check()
        if output.exists(): raise ValueError('Replacement output appeared during publication')
        os.replace(stage, output)
    finally:
        if owned and stage.exists(): stage.unlink()
    return str(output)


def preview_path(payload, result, identity):
    # Preview is independently verified. Its small request checks the bake's
    # captured file identity, while Review and Apply verify all bake bytes.
    verify_result(payload, result, identity, full_hash=False)
    preview = result.get('preview') or {}; folder = artifact_folder(payload['root'], identity)
    path = folder / ('preview.wav' if payload['kind'] == 'audio' else 'preview.png')
    if preview.get('path') != str(path) or path.is_symlink() or not path.is_file() or file_hash(path) != preview.get('sha256'):
        raise ValueError('The task preview is unavailable or changed')
    return str(path)
