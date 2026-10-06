"""Pure, bounded creation of independent library items; no timeline mutation.

Subclip arguments use the selected item's logical clock. One physical parent is
retained, with the selected interpretation. Discrete audio aliases select a
channel of the first physical audio stream, before any stereo downmix. The IO
owner verifies files/probes and decides which independent previews can queue.
"""
import copy
import hashlib
import math
import re
from pathlib import Path

from audio_source_channels import MAX_CHANNELS, channel_index
from preflight import media_files
from source_commands import physical
from source_relink import bounded, number, VIDEO_ONLY
from task_inputs import DERIVED
from timeline_time import frame_rate, interpretation_factor

MAX_SELECTION = 50
MAX_OUTPUT_BYTES = 8 * 1024 * 1024
MAX_FILES = 10000
RESET = DERIVED | {'workflow', 'transcript', 'transcript_basis', 'rendered_from', 'render_replace_task',
    'source_edit_window', 'stab_trf', 'audio_alias_basis', 'audio_alias_source_generation', 'audio_alias_native_duration'}


def _name(value, fallback):
    if value is None: return fallback[:256]
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 256 or any(ord(c) < 32 for c in value):
        raise ValueError('Source name must contain 1–256 characters without control characters')
    return value.strip()


def _range(selected, parent):
    duration = number(selected.get('duration'), 'Selected source duration', 1e-12)
    offset = number(selected.get('sub_in', 0), 'Selected source offset')
    if selected is parent and offset: raise ValueError('A physical source cannot carry a subclip offset')
    factor = interpretation_factor(selected); parent_factor = interpretation_factor(parent)
    total = number(parent.get('duration'), 'Physical source duration', 1e-12) / parent_factor
    end = (offset + duration) / factor
    if not math.isfinite(end) or end > total + max(1e-12, math.ulp(total)*8):
        raise ValueError('The selected logical window exceeds its physical source; relink or repair it first')
    if selected.get('audio_alias'):
        descriptor = selected['audio_alias']
        if (not isinstance(descriptor, dict) or descriptor.get('version') != 1
            or descriptor.get('physical_media_id') != parent['id'] or selected.get('subclip_of') != parent['id']):
            raise ValueError('Repair the typed audio alias reference first')
        if not parent.get('has_audio') or 'channel_index' not in descriptor and abs(factor-parent_factor) > 1e-12:
            raise ValueError('Audio aliases must retain their physical source interpretation; recreate this alias')
        channel_index(selected, parent)
    return duration, offset, factor, total


def _markers(markers, begin, end, warnings):
    if markers is None: return None
    if not isinstance(markers, list) or len(markers) > 8192: raise ValueError('Source markers need a bounded list')
    result = []; opaque = False; cropped = 0
    for old in markers:
        if not isinstance(old, dict): raise ValueError('Source markers must be objects')
        keys = [key for key in ('t','time','start') if key in old]
        if not keys or old.get('clock') not in (None,'source','media','logical'):
            result.append(copy.deepcopy(old)); opaque = True; continue
        at = number(old[keys[0]], 'Source marker position')
        if any(old[key] != at for key in keys): raise ValueError('A source marker has conflicting position fields')
        stop = number(old['end'], 'Source marker end') if 'end' in old else at + number(old.get('duration', 0), 'Source marker duration')
        if not math.isfinite(stop) or stop < at: raise ValueError('A source marker has an invalid range')
        if 'end' in old and 'duration' in old and abs(stop-at-number(old['duration'],'Source marker duration')) > 1e-12:
            raise ValueError('A source marker has conflicting duration fields')
        if (stop <= begin or at >= end) if stop > at else not begin <= at < end:
            cropped += 1; continue
        marker = copy.deepcopy(old); first, last = max(begin,at)-begin, min(end,stop)-begin
        for key in keys: marker[key] = first
        if 'end' in marker: marker['end'] = last
        if 'duration' in marker: marker['duration'] = last-first
        result.append(marker)
    warnings.append('Recognized source markers are cropped and rebased into the new subclip; original markers are unchanged.')
    if cropped: warnings.append(f'{cropped} source marker(s) outside the new subclip are omitted from its copy.')
    if opaque: warnings.append('Markers with an unknown clock/schema are retained unchanged; review them on the new source.')
    return result


def _fresh(selected, identity, token, added, name):
    result = {key:copy.deepcopy(value) for key,value in selected.items() if key not in RESET}
    result.update(id=identity, name=name, ingest_token=token, added=added, workflow_import=True,
                  status='ready' if selected.get('subclip_of') and not selected.get('audio_alias') or selected.get('synthetic') else 'unprepared')
    return result


def plan(project, body, mode, *, identity, added):
    bounded(body, 'Source creation request', 1024*1024)
    if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity): raise ValueError('A fresh source creation identity is required')
    number(added,'Creation time')
    library = project.get('media')
    if not isinstance(library,dict) or len(library)>10000: raise ValueError('Source creation supports up to 10000 library items')
    if mode == 'duplicate':
        ids = body.get('media_ids')
        if not isinstance(ids,list) or not 1 <= len(ids) <= MAX_SELECTION or any(not isinstance(i,str) for i in ids) or len(set(ids)) != len(ids):
            raise ValueError('Choose 1–50 distinct source items to duplicate')
    elif mode in ('subclip','breakout'):
        ids = [body.get('media_id')]
    else: raise ValueError('Unknown source creation command')
    sources = []; estimated = 0; warnings = []; resources = []; resource_ids = []
    for mid in ids:
        selected,parent = physical(project,mid)
        size = bounded(selected,'Selected source metadata',MAX_OUTPUT_BYTES)
        bounded(parent,'Physical source metadata',MAX_OUTPUT_BYTES)
        duration,offset,factor,native_total = _range(selected,parent)
        if not parent.get('synthetic') and (not isinstance(parent.get('path'),str) or not parent['path']): raise ValueError('The physical source path is unavailable')
        if mode == 'subclip' and parent.get('synthetic'):
            raise ValueError('Generated media has no bounded physical file clock; render it before making a subclip')
        if mode == 'breakout':
            if parent.get('synthetic') or parent.get('sequence_frames') or not parent.get('has_audio'):
                raise ValueError('Breakout needs an original file with measured audio channels')
            if (selected.get('audio_alias') or {}).get('channel_index') is not None:
                raise ValueError('This item is already a selected channel; choose its original source for Breakout')
            count = parent.get('channels')
            if type(count) is not int or not 1 <= count <= MAX_CHANNELS: raise ValueError('Relink this source to measure 1–32 first-stream audio channels')
            channel_index({'audio_alias':{'version':1,'channel_index':0}},parent)
            rate = parent.get('sample_rate')
            if type(rate) is not int or not 1 <= rate <= 768000: raise ValueError('Relink this source to measure its first audio stream sample rate')
            if native_total > 4*3600: raise ValueError('Channel alias preparation supports source files up to four hours')
            if selected.get('channel_mode'): warnings.append('Breakout selects original discrete channels, before the existing stereo-bus channel selection.')
        else: count = 1
        estimated += size*count + 4096*count
        if estimated > MAX_OUTPUT_BYTES: raise ValueError('Copied source metadata would exceed eight MiB; reduce the selection or metadata first')
        sources.append((selected,parent,duration,offset,factor,count))
        if parent['id'] not in resource_ids:
            resource_ids.append(parent['id'])
            if not parent.get('synthetic'):
                frames = parent.get('sequence_frames',0)
                if frames and (type(frames) is not int or not 1<=frames<=MAX_FILES): raise ValueError('This creation command supports up to 10000 numbered source frames')
                paths = media_files(parent)
                if len(paths)+len(resources)>MAX_FILES: raise ValueError('Source creation supports up to 10000 physical files per command')
                resources.extend(paths)
    output=[]; prepare_ids=[]; windows=[]
    for selected,parent,duration,offset,factor,count in sources:
        begin,end = 0,duration
        if mode == 'subclip':
            begin=number(body.get('in'),'Subclip In');end=number(body.get('out'),'Subclip Out')
            if not begin < end <= duration: raise ValueError('Subclip In/Out must select a positive range within the selected source')
            # Keep exact authored boundaries. Reject empty native picture/sample
            # windows rather than silently snapping them to another range.
            if selected.get('has_video') and not selected.get('is_image'):
                minimum=1/float(frame_rate(selected.get('frame_rate') if selected.get('frame_rate') is not None else selected.get('fps')))
            elif selected.get('has_audio'):
                rate=parent.get('sample_rate')
                if type(rate) is not int or not 1<=rate<=768000: raise ValueError('Relink this source to measure its audio sample rate before selecting a subclip')
                minimum=1/rate
            else: minimum=0
            if (end-begin)/factor < minimum-max(1e-12,math.ulp(minimum)*8): raise ValueError('The selected source range is shorter than one native frame/sample')
        for channel in range(count):
            index=len(output); nid='src_'+hashlib.sha256(f'{identity}:{index}'.encode()).hexdigest()[:24]
            token=hashlib.sha256(f'{identity}:ingest:{index}'.encode()).hexdigest()
            if nid in library: raise ValueError('Source creation identity has already been used; reload before retrying')
            name=_name(body.get('name'),str(selected.get('name') or 'Source')+' — Subclip') if mode=='subclip' else _name(None,str(selected.get('name') or 'Source')+(' — Copy' if mode=='duplicate' else f' — Channel {channel+1}'))
            item=_fresh(selected,nid,token,added,name)
            if mode=='subclip':
                item.update(subclip_of=parent['id'],sub_in=offset+begin,duration=end-begin,native_duration=(end-begin)/factor,
                            path=parent['path'],status='unprepared' if selected.get('audio_alias') else 'ready')
                if selected.get('audio_alias'): item['audio_alias']['source_media_id']=selected['id']
                if 'markers' in selected: item['markers']=_markers(selected['markers'],begin,end,warnings)
            elif mode=='breakout':
                for key in VIDEO_ONLY | {'input_transform','input_transform_resource','channel_mode'}: item.pop(key,None)
                item.update(path=parent['path'],subclip_of=parent['id'],sub_in=offset,duration=duration,native_duration=duration/factor,
                            has_video=False,has_audio=True,is_image=False,width=0,height=0,codec=parent.get('acodec'),status='unprepared',
                            breakout_of=selected['id'],audio_alias={'version':1,'source_media_id':selected['id'],'physical_media_id':parent['id'],'channel_index':channel})
                for key in ('channels','channel_layout','audio_streams','sample_rate','acodec'): item[key]=copy.deepcopy(parent.get(key))
            if item.get('audio_alias') or not item.get('subclip_of') and not item.get('synthetic'): prepare_ids.append(nid)
            output.append(item)
            windows.append({'media_id':nid,'source_media_id':selected['id'],'physical_media_id':parent['id'],
                'logical_in':item.get('sub_in',0),'duration':item['duration'],'native_in':item.get('sub_in',0)/factor,
                'native_out':(item.get('sub_in',0)+item['duration'])/factor,'channel_index':(item.get('audio_alias') or {}).get('channel_index')})
    if len(library)+len(output)>10000: raise ValueError('Source creation would exceed 10000 library items')
    warnings.append('Existing source records and all timeline clips remain unchanged. New items have independent job identities; ordinary subclips share their physical parent preview.')
    if any(any(k in selected for k in RESET- DERIVED) for selected,*_ in sources): warnings.append('Copied source analysis, transcripts, stabilization and workflow receipts are cleared; authored notes, labels and resource bindings are retained.')
    if mode=='breakout': warnings.append('Each item selects one channel of the first audio stream before downmix. Audition requires its independently verified AAC dual-mono preview; export reads the original discrete channel. Other audio streams are not selected.')
    summary={'kind':'source_creation','mode':mode,'changed':True,'source_media_ids':ids,'created_media_ids':[m['id'] for m in output],
             'count':len(output),'message':f'Created {len(output)} '+('source subclip.' if mode=='subclip' else 'independent source copy/copies.' if mode=='duplicate' else 'discrete channel alias(es).'),
             'warnings':list(dict.fromkeys(warnings)),'windows':windows}
    ops=[{'op':'set','path':'/media/'+m['id'].replace('~','~0').replace('/','~1'),'value':m} for m in output]
    result={'ops':ops,'media':output,'media_ids':summary['created_media_ids'],'prepare_ids':prepare_ids,
            'resource_media_ids':resource_ids,'resources':list(dict.fromkeys(resources)),'summary':summary,'warnings':summary['warnings'],'changed':True}
    bounded(result,'Source creation plan',3*MAX_OUTPUT_BYTES)
    return result
