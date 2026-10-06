"""Reviewed source-rate edits that preserve physical selection windows.

Planning is pure; inspect/check own bounded file-stat validation. Timeline clips
are never rewritten or normalized. Full-mix aliases require explicit parent
group consent; ordinary subclips and selected channels keep independent rates.
"""
import copy
import math
import os
import re

from overlap_normalization import _clock
from source_commands import physical
from source_relink import bounded, digest, number, MAX_MEDIA, MAX_CLIPS, MAX_POINTS, CLEAR
from timeline_time import frame_rate, interpretation_factor, display_frame

MAX_FILES = 10000
RESET = CLEAR | {'transcript_basis'}


def rate(value):
    if isinstance(value, str):
        if len(value) > 64 or not re.fullmatch(r'(?:[0-9]+(?:\.[0-9]+)?|\.[0-9]+|[0-9]+/[0-9]+)', value.strip()):
            raise ValueError('Enter a complete positive decimal or fractional frame rate, or explicit null for native')
        value = value.strip()
    elif type(value) not in (int, float) or not math.isfinite(value):
        raise ValueError('Frame rate must be a finite number or complete decimal/fraction')
    result = frame_rate(value)
    return f'{result.numerator}/{result.denominator}'


def _family(project, identity):
    library = project.get('media')
    if not isinstance(library, dict) or len(library) > MAX_MEDIA: raise ValueError('Interpretation supports up to 10000 source items')
    selected, parent = physical(project, identity)
    children = []
    for mid, item in library.items():
        if not isinstance(item, dict) or item.get('id') != mid: raise ValueError('Repair ambiguous source identities before interpretation')
        if item.get('proxy_info') is not None and not isinstance(item['proxy_info'],dict):raise ValueError('Repair invalid preview metadata before interpretation')
        if item.get('audio_alias') is not None and not isinstance(item['audio_alias'],dict):raise ValueError('Repair invalid audio alias metadata before interpretation')
        if item.get('subclip_of') == parent['id']: children.append(item)
    ids = {m['id'] for m in children}
    if any(m.get('subclip_of') in ids for m in library.values()): raise ValueError('Flatten nested subclips before interpretation')
    if parent.get('synthetic') or parent.get('is_image') or not parent.get('has_video'):
        raise ValueError('Interpret Footage needs a timed picture source; stills, generated media and audio-only originals are unsupported')
    if parent.get('vfr') is True: raise ValueError('Variable-frame-rate sources need a constant-frame-rate transcode before interpretation')
    frames = parent.get('sequence_frames')
    if frames is not None and (type(frames) is not int or not 1 <= frames <= MAX_FILES):
        raise ValueError('Numbered source sequences support 1–10000 frames per interpretation')
    if parent.get('input_opts') and not frames: raise ValueError('Custom decoder input options are unsupported for ordinary-file interpretation')
    if frames:
        options=parent.get('input_opts');native=frame_rate(parent.get('frame_rate') if parent.get('frame_rate') is not None else parent.get('fps'))
        if (not isinstance(options,list) or len(options)!=4 or options[0]!='-framerate' or options[2]!='-start_number'
                or not isinstance(options[3],str) or not re.fullmatch('[0-9]{1,12}',options[3]) or frame_rate(options[1])!=native):
            raise ValueError('Numbered sequences require their original native frame rate and start-number decoder options')
        total=number(parent.get('duration'),'Numbered source duration',1e-12)/interpretation_factor(parent)
        if abs(total-frames/float(native))>max(1e-9,math.ulp(total)*8):raise ValueError('Numbered sequence duration disagrees with its native frame count')
    if parent.get('sub_in', 0): raise ValueError('A physical source cannot carry a subclip offset')
    return selected, parent, sorted(children, key=lambda item:item['id'])


def _markers(media, ratio, warnings):
    points = media.get('markers')
    if points is None: return
    if not isinstance(points, list) or len(points) > MAX_POINTS: raise ValueError('Source markers need a bounded list')
    for marker in points:
        if not isinstance(marker, dict): raise ValueError('Source markers must be objects')
        keys = [key for key in ('t','time','start') if key in marker]
        if not keys or marker.get('clock') not in (None,'source','media','logical'):
            warnings.append('Source markers with an unknown clock/schema are retained unchanged; review them manually.')
            continue
        at = number(marker[keys[0]], 'Source marker position')
        if any(marker[key] != at for key in keys): raise ValueError('Source marker position fields disagree')
        stop = number(marker['end'],'Source marker end') if 'end' in marker else at+number(marker.get('duration',0),'Source marker duration')
        if not math.isfinite(stop) or stop < at: raise ValueError('Source marker range is invalid')
        if 'end' in marker and 'duration' in marker and abs(stop-at-number(marker['duration'],'Source marker duration'))>1e-9:
            raise ValueError('Source marker duration fields disagree')
        if stop*ratio > media['duration']+max(1e-9,math.ulp(media['duration'])*8): raise ValueError('Source marker extends beyond its source window')
        for key in keys + [key for key in ('end','duration') if key in marker]: marker[key] *= ratio


def plan(project, body, *, resources=None, offline=False):
    bounded(project,'Interpretation project'); bounded(body,'Interpretation request',64*1024)
    if not isinstance(body,dict) or 'fps' not in body: raise ValueError('Interpretation requires an explicit fps value or null to restore native')
    context = body.get('_context')
    if not isinstance(context,dict) or any(not isinstance(context.get(k),str) or not context[k] for k in ('workspace','project','revision')):
        raise ValueError('Interpretation requires the saved project context')
    consent = body.get('include_fullmix',False)
    if type(consent) is not bool: raise ValueError('Full-mix group consent must be boolean')
    settings = {'fps':None if body['fps'] is None else rate(body['fps']), 'include_fullmix':consent}
    selected,parent,children = _family(project,body.get('media_id'))
    native = frame_rate(parent.get('frame_rate') if parent.get('frame_rate') is not None else parent.get('fps'))
    target = native if settings['fps'] is None else frame_rate(settings['fps'])
    current = frame_rate(selected['interpret_fps']) if selected.get('interpret_fps') is not None else native
    setting_changed = (selected.get('interpret_fps') is None) != (settings['fps'] is None) or current != target
    if selected.get('audio_alias') and 'channel_index' not in selected['audio_alias'] and current == target:
        # This alias cannot establish an independent future rate override.
        # Its already-matching physical-parent rate is a true no-op.
        setting_changed=False
    issues=[];warnings=[]
    def issue(code,message,**scope):issues.append({'code':code,'message':message,'severity':'error',**scope})
    fullmix = [m for m in children if m.get('audio_alias') and 'channel_index' not in m['audio_alias']]
    group = fullmix if selected is parent and setting_changed else []
    if group and not consent: issue('fullmix_consent_required','Changing this physical source requires explicit consent to update its full-mix audio aliases.',media_id=parent['id'])
    if selected.get('audio_alias') and 'channel_index' not in selected['audio_alias'] and current != target:
        issue('interpret_parent_group','This full-mix alias shares its physical parent rate. Select the physical parent and review its full-mix group.',media_id=parent['id'])
    timing = [selected]+group
    # Parent generation changes retire every dependent task, even where an
    # independent child's authored clock remains unchanged.
    affected = [parent]+children if selected is parent and setting_changed else [selected]
    projected=sum(bounded(m,'Affected source metadata')+4096 for m in affected)
    if projected > 32*1024*1024: raise ValueError('Interpretation exceeds the source metadata copy budget')
    updates={m['id']:copy.deepcopy(m) for m in affected};windows=[]
    timing_ids={m['id'] for m in timing}
    native_total=number(parent.get('duration'),'Physical source duration',1e-12)/interpretation_factor(parent)
    if not math.isfinite(native_total): raise ValueError('Physical source duration is not representable')
    for old in [parent]+children:
        media=updates.get(old['id'],old);before_factor=interpretation_factor(old)
        source_rate=frame_rate(old.get('frame_rate') if old.get('frame_rate') is not None else old.get('fps'))
        if source_rate != native: raise ValueError('Dependent native frame-rate metadata differs from its physical source; Relink it first')
        offset=number(old.get('sub_in',0),'Source offset');duration=number(old.get('duration'),'Source duration',1e-12)
        end=(offset+duration)/before_factor
        if end > native_total+max(1e-9,math.ulp(native_total)*8): issue('source_window_outside','Repair a source window outside its physical source before interpretation.',media_id=old['id'])
        if old['id'] in timing_ids and setting_changed:
            new_factor=float(source_rate/target);ratio=new_factor/before_factor
            media.update(duration=duration*ratio,native_duration=duration/before_factor,native_fps=float(source_rate))
            if settings['fps'] is None: media.pop('interpret_fps',None)
            else: media['interpret_fps']=settings['fps']
            if old.get('subclip_of'):media['sub_in']=offset*ratio
            if 'sub_out' in old:media['sub_out']=number(old['sub_out'],'Source Out')*ratio
            if any(not math.isfinite(media[k]) or media[k]<0 for k in ('duration','native_duration','native_fps','sub_in','sub_out') if k in media): raise ValueError('Interpreted source range is too large')
            _markers(media,ratio,warnings)
        after_factor=interpretation_factor(media)
        if old['id'] in timing_ids:
            windows.append({'media_id':old['id'],'name':str(old.get('name') or old['id']),'old_factor':before_factor,'new_factor':after_factor,
                'old_duration':duration,'new_duration':media['duration'],'old_sub_in':offset,'new_sub_in':media.get('sub_in',0),
                'native_in':offset/before_factor,'native_out':end})
        if old.get('audio_alias'):
            descriptor=old['audio_alias']
            if not isinstance(descriptor,dict) or descriptor.get('version')!=1 or descriptor.get('physical_media_id')!=parent['id']:
                raise ValueError('Repair the typed audio alias before interpretation')
            from audio_source_channels import channel_index
            channel_index(media,parent)
            parent_after=updates.get(parent['id'],parent)
            if 'channel_index' not in descriptor and abs(after_factor-interpretation_factor(parent_after))>1e-9:
                issue('fullmix_rate_mismatch','Full-mix audio aliases must retain their physical parent rate; select the parent group.',media_id=old['id'])
    uses=[];count=0
    sequences=project.get('sequences',[])
    if not isinstance(sequences,list) or len(sequences)>1000: raise ValueError('Interpretation supports up to 1000 sequences')
    family={m['id'] for m in [parent]+children}
    for seq in sequences:
        if not isinstance(seq,dict) or not isinstance(seq.get('tracks',[]),list):raise ValueError('Repair invalid sequence tracks')
        for track in seq.get('tracks',[]):
            if not isinstance(track,dict) or not isinstance(track.get('clips',[]),list):raise ValueError('Repair invalid track clips')
            count+=len(track.get('clips',[]))
            if count>MAX_CLIPS:raise ValueError('Interpretation supports up to 100000 timeline clips')
            for clip in track.get('clips',[]):
                if not isinstance(clip,dict):raise ValueError('Repair invalid timeline clips')
                mid=clip.get('media_id')
                if mid not in family:continue
                if not isinstance(clip.get('time_remap') or [],list) or len(clip.get('time_remap') or [])>MAX_POINTS:raise ValueError('Repair an invalid or excessive speed curve')
                for flag in ('hold','reverse'):
                    if flag in clip and type(clip[flag]) is not bool:raise ValueError('Clip '+flag+' must be boolean')
                duration=_clock(clip);media=updates.get(mid,project['media'][mid]);factor=interpretation_factor(media)
                first=(clip['in_']+media.get('sub_in',0))/factor;last=first if clip.get('hold') else (clip['out']+media.get('sub_in',0))/factor
                row={'media_id':mid,'sequence':seq.get('id'),'track_id':track.get('id'),'clip_id':clip.get('id'),'locked':bool(track.get('locked')),
                    'hold':bool(clip.get('hold')),'logical_in':clip['in_'],'logical_out':clip['out'],'duration':duration,'native_in':first,'native_out':last}
                if not math.isfinite(first) or not math.isfinite(last):raise ValueError('A timeline source range is not representable')
                if clip.get('hold') and track.get('kind')=='video':row['source_frame']=display_frame(first,native)
                uses.append(row)
                outside=clip['in_']>=media['duration'] or first>=native_total if clip.get('hold') else clip['out']>media['duration']+1e-9 or last>native_total+1e-9
                if outside:issue('held_frame_outside' if clip.get('hold') else 'clip_range_outside','An unchanged timeline source range would be outside the interpreted source; adjust that edit before applying.',**{k:row[k] for k in ('media_id','sequence','track_id','clip_id')})
    warnings += ['Timeline placements, durations, source In/Out, effects and automation stay unchanged, including locked tracks. Their playback/content changes with the interpreted source clock.',
        'Selected native source windows and recognized logical source markers are preserved. Ordinary subclips and discrete channel aliases retain their independent interpretation settings unless selected.',
        'Timing uses declared source metadata and a scalar timestamp-rate change; no decoded VFR/frame-cadence certification is performed.']
    if offline:warnings.append('Original files are offline. Captured metadata is used; preparation waits for the source or an owned Relink.')
    changed=setting_changed and not issues
    cleared=sorted({k for m in affected for k in RESET if k in m and not (k=='proxy_info' and (m.get(k) or {}).get('source_signature'))}) if changed else []
    if cleared:warnings.append('Source-bound analysis and preview metadata is cleared; refreshed derivatives attach only to the new source generation.')
    warnings.append('Existing validated source-file signatures are retained independently of preview validity. Previously unbound files are checked during this command; a later explicit Prepare follows their existing source-identity policy.')
    warnings=list(dict.fromkeys(warnings))
    summary={'kind':'source_interpretation','changed':changed,'message':'Interpretation is unchanged.' if not setting_changed and not issues else 'Interpretation needs the listed issues resolved.' if issues else 'Apply the reviewed interpretation without changing timeline edits.',
        'scope':'selected_source','source_name':str(selected.get('name') or selected['id']),'native_rate':str(native),'current_rate':str(current),'target_rate':str(target),
        'required_fullmix_ids':[m['id'] for m in group],'timing_media_ids':[m['id'] for m in timing],'affected_media_ids':[m['id'] for m in affected],
        'windows':windows,'uses':uses,'warnings':warnings,'cleared_fields':cleared}
    seed=digest({'project':project,'context':context,'settings':settings,'selected':selected['id'],'resources':resources or []})
    ops=[];prepare_ids=[]
    if changed:
        for mid,value in updates.items():
            known_source=(value.get('proxy_info') or {}).get('source_signature')
            for key in RESET:value.pop(key,None)
            if known_source:
                if not isinstance(known_source,str) or not re.fullmatch('[0-9a-f]{64}',known_source):raise ValueError('Repair invalid prepared source identity before interpretation')
                value['proxy_info']={'source_signature':known_source}
            value.update(ingest_token=digest([seed,mid]),workflow_import=True,status='ready' if value.get('subclip_of') and not value.get('audio_alias') else 'unprepared')
            if not offline and (value.get('audio_alias') or not value.get('subclip_of')):prepare_ids.append(mid)
            ops.append({'op':'set','path':'/media/'+mid.replace('~','~0').replace('/','~1'),'value':value})
    fingerprint=digest({'basis':seed,'ops':ops,'summary':summary,'issues':issues})
    result={'ok':not issues,'kind':'source_interpretation','media_id':selected['id'],'requested_media_id':selected['id'],
        'physical_media_id':parent['id'],'settings':settings,'affected_media_ids':summary['affected_media_ids'],'issues':issues,'summary':summary,
        'fingerprint':fingerprint,'ops':ops,'media':updates,'prepare_ids':prepare_ids}
    bounded(result,'Interpretation plan',96*1024*1024)
    return result


def check(resources):
    from source_creation_io import check as check_files
    check_files(resources)


def inspect(project, body, *, proc_holder=None, scratch_parent=None):
    from preflight import media_files
    from render_context import RenderContext
    from render_replace import stamp
    from source_relink_io import check_accepted
    from media_preview import digest as file_digest
    from task_inputs import source_stamp
    with RenderContext(proc_holder=proc_holder, scratch_parent=scratch_parent) as context:
        context.check_cancelled();plan(project,body)
        _,parent,children=_family(project,body['media_id']);paths=media_files(parent)
        if len(paths)>MAX_FILES:raise ValueError('Interpretation supports up to 10000 physical source files')
        resources=[]
        for path in paths:
            context.check_cancelled()
            try:current=stamp(path)
            except FileNotFoundError:current=None
            resources.append({'path':os.path.abspath(path),'stamp':current})
        offline=any(r['stamp'] is None for r in resources)
        if not offline:
            check_accepted(parent)
            current_signature=file_digest(source_stamp(parent))
            for media in [parent]+children:
                previous=(media.get('proxy_info') or {}).get('source_signature')
                if previous and previous!=current_signature:raise ValueError('Source changed on disk; inspect and Relink it before interpreting')
        planned=plan(project,body,resources=resources,offline=offline)
        check(resources);context.check_cancelled()
        return {'plan':planned,'resources':resources}
