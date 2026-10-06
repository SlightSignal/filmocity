"""Owned editorial commands and deliberately independent audio-alias previews."""
import copy
import json
import math
import os
from pathlib import Path

from media_analysis import digest
from task_inputs import DERIVED, source_stamp
from timeline_time import frame_rate, interpretation_factor, display_frame

MAX_BYTES = 8 * 1024 * 1024
SOURCE_FIELDS = ('path','duration','frame_rate','fps','interpret_fps','has_audio','channels','sample_rate','audio_streams','input_opts','sequence_frames','synthetic','ingest_token')


def bounded(value):
    try: raw = json.dumps(value, allow_nan=False).encode('utf-8')
    except (ValueError, TypeError, RecursionError) as error: raise ValueError('Editorial command data must be finite JSON') from error
    if len(raw) > MAX_BYTES: raise ValueError('Editorial command metadata exceeds eight MiB')


def number(value, label, minimum=0):
    if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < minimum:
        raise ValueError(label+' must be finite and at least '+str(minimum))
    return value


def physical(project, identity):
    media = project.get('media', {}).get(identity) if isinstance(identity, str) else None
    if not isinstance(media, dict) or media.get('id') != identity: raise ValueError('Choose an existing source in this project')
    parent = media
    if media.get('subclip_of'):
        parent = project['media'].get(media['subclip_of'])
        if not isinstance(parent, dict) or parent.get('id') != media['subclip_of']: raise ValueError('The physical source parent is missing')
        if parent.get('subclip_of'): raise ValueError('Flatten nested subclips before this source command')
    return media, parent


def check_resources(stamps):
    from render_replace import stamp
    if any(stamp(entry[0]) != entry for entry in stamps): raise ValueError('A source or font resource changed while preparing the command')


def input_transform(project, body):
    from project_resources import input_lut
    from render_replace import stamp
    from source_color import plan as color_plan
    selected, source = physical(project, body.get('media_id'))
    transform = body.get('transform', 'none')
    if transform not in ('none','slog3','vlog','clog3','logc3'): raise ValueError('Choose a supported camera-log transform')
    if not source.get('has_video'): raise ValueError('Choose a source with picture for camera-log conversion')
    result = copy.deepcopy(source)
    if transform == 'none': result.pop('input_transform', None)
    else: result['input_transform'] = transform
    binding = result.get('input_transform_resource')
    if transform == 'none' or isinstance(binding, dict) and binding.get('name') != transform: result.pop('input_transform_resource', None)
    path = input_lut(result); resources = [stamp(path)] if path else []
    policy = color_plan(result)
    affected = [mid for mid,m in project['media'].items() if mid == source['id'] or m.get('subclip_of') == source['id']]
    changed = result != source
    summary = {'kind':'input_transform','changed':changed,'requested_media_id':selected['id'],'media_id':source['id'],
        'affected_media_ids':affected,'scope':'shared_source','transform':transform,
        'message':'Source color updated for the physical source and its subclips.' if changed else 'Source color is unchanged.',
        'warnings':['This setting affects every use of the physical source, including clips on locked tracks.']+policy['warnings']}
    return {'media':result,'resources':resources,'summary':summary,'changed':changed}


def alias_source(project, media):
    """Full-file preview owner; logical offset/interpretation stay on the alias."""
    descriptor = media.get('audio_alias')
    if not isinstance(descriptor, dict) or descriptor.get('version') != 1 or descriptor.get('physical_media_id') != media.get('subclip_of'):
        raise ValueError('Repair the audio alias source reference before preparing it')
    selected, parent = physical(project, media['id'])
    if parent.get('synthetic') or parent.get('sequence_frames') or not parent.get('has_audio'):
        raise ValueError('The audio alias needs its original file audio source')
    duration = number(parent.get('duration'), 'Physical source duration', 1e-9)
    native_duration = duration/interpretation_factor(parent)
    if native_duration > 4*3600: raise ValueError('Audio alias preparation supports source files up to four hours')
    independent = 'channel_index' in descriptor
    if not independent and abs(interpretation_factor(media)-interpretation_factor(parent)) > 1e-9:
        raise ValueError('Source interpretation changed; recreate this audio alias')
    from audio_source_channels import channel_index
    channel_index(media, parent)
    offset = number(media.get('sub_in',0),'Alias source offset'); length=number(media.get('duration'),'Alias duration',1e-9)
    end = (offset+length)/interpretation_factor(media)
    if end > native_duration+max(1e-12,math.ulp(native_duration)*8): raise ValueError('The audio alias extends beyond the source')
    value = copy.deepcopy(media)
    value.update(path=parent['path'], has_audio=True, has_video=False, is_image=False)
    for key in ('sample_rate','channels','channel_layout','audio_streams','acodec'): value[key] = copy.deepcopy(parent.get(key))
    if parent.get('input_opts') is not None: value['input_opts'] = copy.deepcopy(parent['input_opts'])
    else: value.pop('input_opts',None)
    value['audio_alias_native_duration'] = native_duration
    if parent.get('source_relink_basis') is not None: value['source_relink_basis'] = copy.deepcopy(parent['source_relink_basis'])
    else: value.pop('source_relink_basis', None)
    value['audio_alias_source_generation'] = digest([parent['id'], parent['path'], parent.get('ingest_token')])
    value['audio_alias_basis'] = digest({'parent':{key:parent.get(key) for key in SOURCE_FIELDS},
        'alias':{key:media.get(key) for key in ('id','subclip_of','sub_in','duration','frame_rate','fps','interpret_fps','audio_alias')}})
    return value


def extract_audio(project, body, identity, token, added):
    selected, parent = physical(project, body.get('media_id')); bounded(selected); bounded(parent)
    if not parent.get('has_audio'): raise ValueError('The selected source has no audio')
    if not selected.get('has_video'):
        return {'media':copy.deepcopy(selected),'changed':False,'resources':[], 'summary':{
            'kind':'extract_audio','changed':False,'source_media_id':selected['id'],'media_id':selected['id'],
            'warnings':[],'message':'The selected item is already audio-only.'}}
    if parent.get('synthetic') or parent.get('sequence_frames'): raise ValueError('Render generated media to a file before extracting an audio alias')
    if identity in project['media'] or not isinstance(identity,str) or not token: raise ValueError('Audio alias identity is unavailable')
    factor = interpretation_factor(parent)
    if abs(interpretation_factor(selected)-factor)>1e-9: raise ValueError('Match subclip and parent interpretation before extracting audio')
    duration = number(selected.get('duration'),'Source duration',1e-9); offset=number(selected.get('sub_in',0),'Source offset')
    if offset+duration > number(parent.get('duration'),'Physical duration',1e-9)+1e-9: raise ValueError('The selected source range exceeds its parent')
    # Copy editable source metadata deliberately; never copy another job's
    # identity, preview, readiness, source-bound transcript or workflow receipt.
    excluded = DERIVED | {'workflow','transcript','rendered_from','render_replace_task','audio_alias','extracted_from',
        'vcodec','pix_fmt','stab_trf','input_transform','input_transform_resource','color_transfer','color_primaries','color_space','color_range',
        'hdr','hdr_peak_nits','hdr_max_cll','hdr_mastering_peak_nits','dynamic_range','wide_gamut'}
    result={key:copy.deepcopy(value) for key,value in selected.items() if key not in excluded}
    result.update(id=identity,name=Path(str(selected.get('name') or 'Source')).stem+' — Audio',path=parent['path'],
        subclip_of=parent['id'],sub_in=offset,duration=duration,has_video=False,has_audio=True,is_image=False,width=0,height=0,
        codec=parent.get('acodec'),status='unprepared',ingest_token=token,added=added,workflow_import=True,extracted_from=selected['id'],
        audio_alias={'version':1,'source_media_id':selected['id'],'physical_media_id':parent['id']})
    candidate = dict(project,media={**project['media'],identity:result});prepared=alias_source(candidate,result)
    resources=source_stamp(prepared)
    summary={'kind':'extract_audio','changed':True,'source_media_id':selected['id'],'media_id':identity,
        'message':'Audio alias added; independent preview preparation is queued after saving.',
        'warnings':['This is an audio-only alias of the original file, not a new exported audio file.',
            'Source interpretation and subclip offsets are retained; the lossy AAC stereo preview uses the first audio stream and complete native source clock.']}
    return {'media':result,'prepared':prepared,'resources':resources,'changed':True,'summary':summary}


def describe(project, identity):
    from render import clip_dur, seq_total
    sequences=[s for s in project.get('sequences',[]) if s.get('id')==identity]
    if not isinstance(identity,str) or len(sequences)!=1: raise ValueError('Choose one existing sequence')
    sequence=sequences[0];bounded(sequence);rate=frame_rate(sequence['fps']);total=number(seq_total(sequence),'Sequence duration')
    lines=[f"# {sequence['name']} — {total:.6f}s, {sequence['width']}x{sequence['height']} @ {rate.numerator}/{rate.denominator} fps"]
    sections=copy.deepcopy([m for m in (sequence.get('markers') or []) if m.get('duration')])
    if sections: lines.append('Sections: '+'; '.join(f"{m['name']} {m['time']:.6f}–{m['time']+m['duration']:.6f}s" for m in sections))
    timings=[]
    for track in sorted(sequence['tracks'],key=lambda t:(t['kind']!='video',-t['index'] if t['kind']=='video' else t['index'])):
        if not track['clips']: continue
        lines.append(f"## {track['id']} ({track['kind']})")
        for clip in sorted(track['clips'],key=lambda c:c['start']):
            start=number(clip['start'],'Clip start');duration=number(clip_dur(clip),'Clip duration',1e-9);end=start+duration
            media=project['media'].get(clip.get('media_id'));extras=[]
            what=media.get('name','Source') if media else 'text: '+str(clip['title'].get('text',''))[:40] if clip.get('title') else 'graphic: '+str(clip['graphic'].get('name','')) if clip.get('graphic') else 'nested' if clip.get('sequence_id') else 'adjustment' if clip.get('adjustment') else '?'
            if clip.get('hold'):extras.append('held frame')
            elif clip.get('time_remap'):extras.append('speed ramp')
            elif clip.get('speed',1)!=1:extras.append(f"speed {clip['speed']:g}x")
            if clip.get('reverse'):extras.append('reverse')
            if clip.get('keyframes'):extras.append('keyframed '+','.join(clip['keyframes']))
            if clip.get('fx_stack'):extras.append('fx '+','.join(fx['type'] for fx in clip['fx_stack'] if fx.get('enabled') is not False))
            if clip.get('transition_in'):extras.append('in: '+str(clip['transition_in'].get('type'))+' '+str(clip['transition_in'].get('duration',0))+'s')
            if (clip.get('color') or {}).get('lut'):extras.append('look '+os.path.basename(clip['color']['lut']))
            if clip.get('graphic'):extras.append('anim '+','.join(sorted({(l.get('anim_in') or {}).get('type','') for l in clip['graphic'].get('layers',[]) if (l.get('anim_in') or {}).get('type')})))
            lines.append(f'- {start:.6f}–{end:.6f}s ({duration:.6f}s) {what}'+(' ['+'; '.join(extras)+']' if extras else '')+(' — '+str(clip['note']) if clip.get('note') else ''))
            timings.append({'clip_id':clip['id'],'track_id':track['id'],'kind':track['kind'],'start':start,'end':end,'duration':duration,
                'source_in':clip.get('in_'),'source_out':clip.get('out'),'hold':bool(clip.get('hold')),'reverse':bool(clip.get('reverse')),
                'start_frame':display_frame(start,rate),'end_frame':display_frame(end,rate)})
    if sequence.get('captions'):
        lines.append('## Captions');lines += [f"- {c['start']:.6f}–{c['end']:.6f}s “{c['text']}”" for c in sequence['captions']]
    return {'sequence':identity,'text':'\n'.join(lines),'duration':total,'clips':len(timings),'sections':sections,'timings':timings,
        'clock':'timeline-seconds','frame_rate':str(rate),'frame_policy':'containing frame at each boundary; seconds retain source precision',
        'warnings':[]}


def plan(project, body, mode, *, identity=None, token=None, added=None):
    """Read-only bounded planning; routes commit only after owner revalidation."""
    bounded(body)
    if mode == 'split_words':
        import graphics_words
        from render_replace import stamp
        resources=[stamp(path) for path in graphics_words.resources(project,body)]
        planned=graphics_words.plan(project,body);planned['resources']=resources
        planned['changed']=bool(planned['ops'])
    elif mode == 'input_transform':
        planned=input_transform(project,body)
        key=planned['media']['id'].replace('~','~0').replace('/','~1')
        planned['ops']=[{'op':'set','path':'/media/'+key,'value':planned['media']}] if planned['changed'] else []
    elif mode == 'extract_audio':
        planned=extract_audio(project,body,identity,token,added)
        key=planned['media']['id'].replace('~','~0').replace('/','~1')
        planned['ops']=[{'op':'set','path':'/media/'+key,'value':planned['media']}] if planned['changed'] else []
    else: raise ValueError('Unknown editorial source command')
    bounded(planned);check_resources(planned['resources'])
    return planned
