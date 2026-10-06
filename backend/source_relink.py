"""Pure reviewed physical-source relinking; timelines are never normalized.

All range arithmetic uses the renderer's logical source clock. Ordinary subclips
retain their own interpretation and logical window; audio aliases must use the
physical parent's interpretation. File inspection and commit ownership are the
caller's responsibility. No files are opened or written by this planner.
"""
import copy
import hashlib
import json
import math
import os

from overlap_normalization import _clock
from task_inputs import portable_source_stamp
from timeline_time import frame_rate, interpretation_factor, display_frame

MAX_BYTES = 32 * 1024 * 1024
MAX_NODES = 500_000
MAX_MEDIA = 10_000
MAX_CLIPS = 100_000
MAX_POINTS = 8192
MEASURED = {
    'duration', 'fps', 'frame_rate', 'width', 'height', 'is_image', 'has_video',
    'has_audio', 'codec', 'vcodec', 'acodec', 'pix_fmt', 'rotation',
    'sample_aspect_ratio', 'sample_rate', 'channels', 'channel_layout',
    'audio_streams', 'vfr', 'color_transfer', 'color_primaries', 'color_space',
    'color_range', 'hdr', 'dynamic_range', 'wide_gamut', 'hdr_max_cll',
    'hdr_mastering_peak_nits',
}
CLEAR = {
    'thumb', 'strip', 'wave', 'proxy', 'proxy_info', 'proxy_status', 'proxy_error',
    'ingest_error', 'task_id', 'stab_trf', 'transcript', 'rendered_from',
    'render_replace_task', 'source_edit_window', 'audio_alias_basis',
    'audio_alias_source_generation', 'audio_alias_native_duration',
}
VIDEO_ONLY = {'vcodec','pix_fmt','rotation','sample_aspect_ratio','color_transfer',
    'color_primaries','color_space','color_range','hdr','dynamic_range','wide_gamut',
    'hdr_max_cll','hdr_mastering_peak_nits'}


def bounded(value, label='Relink data', limit=MAX_BYTES):
    """Bound before cloning/serialization, including large preserved metadata."""
    stack = [(value, 0)]; nodes = size = 0
    while stack:
        item, depth = stack.pop(); nodes += 1
        if nodes > MAX_NODES or depth > 32: raise ValueError(label + ' exceeds the metadata nesting/count limit')
        if isinstance(item, dict):
            if len(item) > MAX_NODES: raise ValueError(label + ' has too many fields')
            size += len(item) * 4 + 2
            for key, child in item.items():
                if not isinstance(key, str): raise ValueError(label + ' must have text object keys')
                stack.append((key, depth + 1)); stack.append((child, depth + 1))
        elif isinstance(item, list):
            if len(item) > MAX_NODES: raise ValueError(label + ' has too many entries')
            size += len(item) + 2
            stack.extend((child, depth + 1) for child in item)
        elif isinstance(item, str):
            if len(item) > limit: raise ValueError(label + ' exceeds the metadata byte limit')
            size += len(item.encode('utf-8')) + 2
        elif item is None or isinstance(item, bool): size += 5
        elif isinstance(item, (int, float)):
            try: valid = math.isfinite(item)
            except OverflowError: valid = False
            if not valid: raise ValueError(label + ' must be finite JSON data')
            size += 32
        else: raise ValueError(label + ' must be finite JSON data')
        if size > limit: raise ValueError(label + ' exceeds the metadata byte limit')
    return size


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def number(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(label + ' must be finite and at least ' + str(minimum))
    return value


def _source_family(project, requested):
    library = project.get('media')
    if not isinstance(library, dict) or len(library) > MAX_MEDIA: raise ValueError('Relink supports up to 10000 library items')
    if not isinstance(requested, str) or requested not in library: raise ValueError('Choose an existing source to relink')
    selected = library[requested]
    if not isinstance(selected, dict) or selected.get('id') != requested: raise ValueError('The selected media identity is ambiguous')
    # Resolve the requested parent only to provide a precise refusal. The
    # renderer supports one physical parent level, not nested subclip chains.
    seen = set(); parent = selected
    while parent.get('subclip_of'):
        identity = parent.get('id')
        if identity in seen: raise ValueError('A source parent cycle must be repaired before relinking')
        seen.add(identity); key = parent['subclip_of']
        parent = library.get(key)
        if not isinstance(parent, dict) or parent.get('id') != key: raise ValueError('The physical source parent is missing or ambiguous')
        if len(seen) > 1: raise ValueError('Flatten nested subclips before relinking their source')
    pid = parent.get('id')
    if not isinstance(pid, str) or not pid: raise ValueError('The physical source ID cannot be safely edited')
    if parent.get('synthetic') or parent.get('sequence_frames'): raise ValueError('Generated media and numbered sequences need their dedicated replacement workflow')
    if parent.get('input_opts'): raise ValueError('Custom decoder input options cannot be verified by ordinary-file Relink; render or import a standard source first')
    children = []
    for key, item in library.items():
        if not isinstance(item, dict): raise ValueError('Repair invalid source library metadata before relinking')
        if item.get('subclip_of') == pid:
            if key != item.get('id') or not key: raise ValueError('A dependent source identity is ambiguous or cannot be edited')
            if item.get('synthetic') or item.get('sequence_frames'): raise ValueError('A dependent generated source cannot be relinked as an ordinary subclip')
            children.append(item)
    child_ids = {child['id'] for child in children}
    if any(item.get('subclip_of') in child_ids for item in library.values()):
        raise ValueError('Flatten dependent nested subclips before relinking their physical source')
    if parent.get('sub_in', 0): raise ValueError('A physical source cannot carry a subclip offset')
    return selected, parent, sorted(children, key=lambda m: m['id'])


def _measured(old, info, path, *, child=False, alias=False):
    result = copy.deepcopy(old)
    for key in MEASURED | {'native_duration', 'native_fps'}: result.pop(key, None)
    result.update({key: copy.deepcopy(info[key]) for key in MEASURED if key in info})
    result['path'] = path
    factor = interpretation_factor(result)
    native = number(info.get('duration'), 'Replacement native duration', 1e-12)
    if child:
        result['duration'] = number(old.get('duration'), 'Dependent source duration', 1e-12)
        result['sub_in'] = number(old.get('sub_in', 0), 'Dependent source offset')
        result['native_duration'] = result['duration'] / factor
    else:
        result['duration'] = number(old.get('duration'), 'Still selection duration', 1e-12) if info.get('is_image') else native * factor
        result['native_duration'] = native
    rate = info.get('frame_rate') if info.get('frame_rate') is not None else info.get('fps')
    result['native_fps'] = float(frame_rate(rate)) if rate else None
    if alias:
        for key in VIDEO_ONLY: result.pop(key,None)
        result.update(has_video=False, has_audio=True, is_image=False, width=0, height=0,
                      codec=info.get('acodec'))
    number(result['duration'], 'Interpreted source duration', 1e-12)
    return result, factor


def plan(project, body, probed_info, normalized_path, stamp):
    """Return a deterministic proposed edit, or an incompatible inspection.

    ``stamp`` is task_inputs.source_stamp's one-file list of
    ``[absolute_path, size, mtime_ns, inode]``. The caller must verify it before
    and after probing and again before committing this plan.
    """
    bounded(project, 'Project'); bounded(body, 'Relink request', 64*1024); bounded(probed_info, 'Replacement probe', 1024*1024)
    if not isinstance(project, dict) or not isinstance(body, dict) or not isinstance(probed_info, dict): raise ValueError('Relink needs project, request and probe objects')
    context = body.get('_context')
    if not isinstance(context, dict) or any(not isinstance(context.get(k), str) or not context[k] for k in ('workspace','project','revision')):
        raise ValueError('Relink needs the captured saved project context')
    if not isinstance(normalized_path, str) or not normalized_path or '\x00' in normalized_path or not os.path.isabs(normalized_path): raise ValueError('Choose an absolute replacement file path')
    bounded(stamp, 'Replacement file identity', 16*1024)
    if not isinstance(stamp, list) or len(stamp) != 1 or not isinstance(stamp[0], list) or len(stamp[0]) != 4 or stamp[0][0] != normalized_path:
        raise ValueError('Replacement identity must describe exactly the inspected file')
    for index, value in enumerate(stamp[0][1:]):
        if isinstance(value, bool) or not isinstance(value, int) or value < (1 if index == 0 else 0): raise ValueError('Replacement file identity is invalid')
    selected, parent, children = _source_family(project, body.get('media_id'))
    # Probe metadata (notably audio_streams) is copied into every dependent.
    # Refuse amplification before the first complete media clone is allocated.
    projected = sum(bounded(old, 'Source metadata') for old in [parent]+children)
    projected += (len(children)+1) * (bounded({key:probed_info[key] for key in MEASURED if key in probed_info}, 'Probe metadata')+4096)
    if projected > MAX_BYTES: raise ValueError('Relink would exceed the metadata copy budget')
    pid = parent['id']; issues = []
    def issue(code, message, severity='error', **scope):
        issues.append({'code':code,'message':message,'severity':severity,**scope})
    for flag in ('has_video','has_audio','is_image'):
        if not isinstance(probed_info.get(flag), bool): raise ValueError('Replacement '+flag+' metadata must be boolean')
    native_duration = number(probed_info.get('duration'), 'Replacement native duration', 1e-12)
    if not probed_info['has_video'] and not probed_info['has_audio']: issue('invalid_media','Replacement has no readable video or audio stream.')
    for stream in ('video','audio'):
        if parent.get('has_'+stream) and not probed_info['has_'+stream]: issue('missing_'+stream,'Replacement lacks the original '+stream+' stream.',media_id=pid)
    if bool(parent.get('is_image')) != probed_info['is_image']: issue('media_kind_mismatch','Still images and timed sources cannot be relinked interchangeably.',media_id=pid)
    if probed_info['is_image'] and not probed_info['has_video']: raise ValueError('A still image needs a readable picture stream')
    updates = {}; factors = {}; dependents = []
    for old in [parent] + children:
        mid = old['id']; alias = old.get('audio_alias') is not None
        if alias:
            descriptor = old['audio_alias']
            if not isinstance(descriptor, dict) or descriptor.get('version') != 1 or descriptor.get('physical_media_id') != pid or old.get('subclip_of') != pid:
                raise ValueError('Repair the typed audio alias before relinking its parent')
            if not probed_info['has_audio']: issue('alias_missing_audio','The replacement has no audio for this audio alias.',media_id=mid)
        try: updated, factor = _measured(old, probed_info, normalized_path, child=old is not parent, alias=alias)
        except ValueError as error:
            issue('interpretation_unavailable', str(error), media_id=mid); continue
        if alias:
            from audio_source_channels import channel_index
            try: channel_index(updated)
            except ValueError as error: issue('alias_channel_unavailable',str(error),media_id=mid)
        updates[mid] = updated; factors[mid] = factor
        if old is not parent:
            start = updated['sub_in']/factor; end = (updated['sub_in']+updated['duration'])/factor
            if not math.isfinite(end): raise ValueError('Dependent source window is too large to represent')
            dependents.append({'media_id':mid,'name':str(old.get('name') or mid),'kind':'audio_alias' if alias else 'subclip',
                'logical_in':updated['sub_in'],'logical_duration':updated['duration'],'native_in':start,'native_out':end,'factor':factor})
            if not probed_info['is_image'] and end > native_duration + max(1e-12, math.ulp(native_duration)*8):
                issue('dependent_too_short',f'The complete {mid} source window requires {end:.12g} native seconds; replacement has {native_duration:.12g}. No dependent is trimmed.',media_id=mid)
    for child in children:
        if child.get('audio_alias') and 'channel_index' not in child['audio_alias'] and child['id'] in factors and pid in factors and abs(factors[child['id']]-factors[pid]) > 1e-12:
            issue('alias_interpretation_mismatch','Audio alias interpretation must match its physical parent; recreate the alias with the intended interpretation.',media_id=child['id'])
    for mid, media in updates.items():
        markers = media.get('markers')
        if markers is None: continue
        if not isinstance(markers,list) or len(markers)>MAX_POINTS: raise ValueError('Source markers need a bounded list')
        unrecognized = False
        for marker in markers:
            if not isinstance(marker,dict): raise ValueError('Source markers must be objects')
            keys = [key for key in ('t','time','start') if key in marker]
            if not keys or marker.get('clock') not in (None,'source','media','logical'):
                unrecognized = True; continue
            at = number(marker[keys[0]],'Source marker position')
            if any(marker[key] != at for key in keys): raise ValueError('A source marker has conflicting position fields')
            end = number(marker['end'],'Source marker end') if 'end' in marker else at+number(marker.get('duration',0),'Source marker duration')
            if end < at or not math.isfinite(end): raise ValueError('A source marker has an invalid range')
            if 'end' in marker and 'duration' in marker and abs(end-at-number(marker['duration'],'Source marker duration')) > 1e-12:
                raise ValueError('A source marker has conflicting duration fields')
            if not probed_info['is_image'] and end > media['duration']+max(1e-12,math.ulp(media['duration'])*8):
                issue('source_marker_outside','A retained logical source marker lies outside the replacement window; resolve it before relinking.',media_id=mid)
        if unrecognized: issue('source_marker_clock_unknown','Some authored source markers have an unrecognized clock/schema; they are retained unchanged and need review.','warning',media_id=mid)
    uses = []; count = 0; sequences = project.get('sequences', [])
    if not isinstance(sequences, list) or len(sequences) > 1000: raise ValueError('Relink supports up to 1000 sequences')
    family = {pid} | {child['id'] for child in children}
    for sequence in sequences:
        if not isinstance(sequence, dict) or not isinstance(sequence.get('tracks',[]), list): raise ValueError('Repair invalid sequence tracks before relinking')
        for track in sequence.get('tracks', []):
            if not isinstance(track, dict) or not isinstance(track.get('clips',[]), list): raise ValueError('Repair invalid track clips before relinking')
            count += len(track.get('clips', []))
            if count > MAX_CLIPS: raise ValueError('Relink supports up to 100000 timeline clips')
            for clip in track.get('clips', []):
                if not isinstance(clip, dict): raise ValueError('Repair invalid timeline clips before relinking')
                mid = clip.get('media_id')
                if mid not in family or mid not in updates: continue
                points = clip.get('time_remap') or []
                if not isinstance(points,list) or len(points)>MAX_POINTS: raise ValueError('Repair an invalid or excessive speed ramp before relinking')
                for flag in ('hold','reverse'):
                    if flag in clip and not isinstance(clip[flag],bool): raise ValueError('Clip '+flag+' must be boolean')
                duration = _clock(clip); media = updates[mid]; factor = factors[mid]; offset = media.get('sub_in',0)
                start = (clip['in_']+offset)/factor; end = start if clip.get('hold') else (clip['out']+offset)/factor
                if not math.isfinite(start) or not math.isfinite(end): raise ValueError('A clip source window is too large to represent')
                scope = {'media_id':mid,'sequence':sequence.get('id'),'track_id':track.get('id'),'clip_id':clip.get('id')}
                row = {**scope,'locked':bool(track.get('locked')),'hold':bool(clip.get('hold')),'native_in':start,'native_out':end,
                    'logical_in':clip['in_'],'logical_out':clip['out'],'duration':duration}
                if clip.get('hold') and track.get('kind') == 'video' and media.get('has_video') and not probed_info['is_image']:
                    rate = media.get('frame_rate') if media.get('frame_rate') is not None else media.get('fps')
                    try: row['source_frame'] = display_frame(start, frame_rate(rate))
                    except ValueError: issue('hold_frame_rate_missing','A held picture needs a valid replacement native frame rate.',**scope)
                uses.append(row)
                if not probed_info['is_image']:
                    # Held Out describes timeline duration, never source extent.
                    outside = start >= native_duration if clip.get('hold') else end > native_duration + max(1e-12,math.ulp(native_duration)*8)
                    child_outside = (clip['in_'] >= media['duration'] if clip.get('hold') else clip['out'] > media['duration']+max(1e-12,math.ulp(media['duration'])*8)) if mid != pid else False
                    if outside or child_outside:
                        issue('held_frame_outside' if clip.get('hold') else 'replacement_too_short',
                            'The selected held frame is outside the replacement source window.' if clip.get('hold') else 'A retained timeline source range extends beyond the replacement or its dependent window.',**scope)
    for field in ('width','height','channels','sample_rate'):
        if parent.get(field) != probed_info.get(field): issue('changed_'+field,f'{field} changes from {parent.get(field)} to {probed_info.get(field)}.','warning',media_id=pid)
    old_rate = parent.get('frame_rate') if parent.get('frame_rate') is not None else parent.get('fps')
    new_rate = probed_info.get('frame_rate') if probed_info.get('frame_rate') is not None else probed_info.get('fps')
    if old_rate != new_rate: issue('changed_frame_rate',f'Native frame rate changes from {old_rate} to {new_rate}; logical edits and interpretation targets remain in place.','warning',media_id=pid)
    if os.path.basename(normalized_path).lower() != os.path.basename(str(parent.get('path',''))).lower(): issue('changed_filename','The replacement filename differs; verify the intended source.','warning',media_id=pid)
    basis = {'version':1,'path':normalized_path,'stamp':portable_source_stamp(stamp),'probe':digest(probed_info)}
    # An accepted, identical source with already truthful dependent metadata is
    # a real no-op. Unknown same-path bytes always establish a new generation.
    unchanged = all(old.get('source_relink_basis') == basis and updates.get(old['id']) == old for old in [parent]+children)
    ok = not any(row['severity']=='error' for row in issues)
    warnings = [row['message'] for row in issues if row['severity']=='warning']
    warnings += ['This source-wide change affects every use, including locked tracks; complete clips and track edits stay in place.',
        'Range checks use the probed container duration, not a decoded scan of every picture/audio timestamp. Review VFR, delayed audio and missing stream tails.',
        'Authored source notes, labels, markers, color overrides and resource bindings are retained. Review those annotations against the replacement.']
    if children: warnings.append('Every dependent logical source window and interpretation target is retained; native metadata is refreshed. No source window is trimmed.')
    cleared = sorted({key for old in [parent]+children for key in CLEAR if key in old}) if not unchanged else []
    if cleared: warnings.append('Source-bound preview, transcript, stabilization and bake metadata is cleared where present; rebuild or reanalyze the replacement.')
    affected = [pid]+[child['id'] for child in children]
    summary = {'kind':'source_relink','changed':bool(ok and not unchanged),'message':'Source is already relinked to this inspected file.' if unchanged and ok else 'Reviewed replacement is incompatible; no changes can be applied.' if not ok else 'Relink the physical source and refresh all dependent source metadata without changing timeline edits.',
        'media_id':pid,'requested_media_id':selected['id'],'source_name':str(parent.get('name') or pid),'path':normalized_path,
        'affected_media_ids':affected,'warnings':warnings,'dependents':dependents,'uses':uses,'replacement':{key:probed_info.get(key) for key in ('duration','frame_rate','fps','width','height','has_video','has_audio','is_image','channels','sample_rate')},
        'scope':'physical_source','range_scope':'container_duration','cleared_fields':cleared}
    basis_identity = digest({'project':project,'context':context,'info':probed_info,'path':normalized_path,'stamp':stamp,'summary':summary})
    changed = {}; ops = []
    if ok and not unchanged:
        for mid in affected:
            value = updates[mid]
            for key in CLEAR: value.pop(key,None)
            value.update(status='unprepared',ingest_token=digest([basis_identity,mid])[:32],workflow_import=True,source_relink_basis=copy.deepcopy(basis))
            changed[mid] = value; ops.append({'op':'set','path':'/media/'+mid.replace('~','~0').replace('/','~1'),'value':value})
    bounded(changed,'Relink result')
    fingerprint = digest({'basis':basis_identity,'ops':ops,'issues':issues})
    return {'ok':ok,'ops':ops,'media':changed,'media_id':pid,'requested_media_id':selected['id'],'affected_media_ids':affected,
        'issues':issues,'summary':summary,'warnings':warnings,'fingerprint':fingerprint,'expectedSource':digest(parent)}
