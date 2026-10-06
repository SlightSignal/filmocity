"""Owned isolated-audio capture and atomic gain/transient-marker edit plans."""
import copy
import hashlib
import json
import math

from media_analysis import digest, number, source_inputs
from overlap_normalization import _clock
from preflight import inspect_resources
from render_replace import _snapshot, stamp
from timeline_time import frame_rate, interpretation_factor

MAX_ITEMS = 50
MAX_SECONDS = 600
MAX_TOTAL_SECONDS = 3600
MAX_POINTS = 8192
MAX_BYTES = 16 * 1024 * 1024


def _selection(project, body, *, maximum=MAX_ITEMS, audible=False):
    ids = body.get('clip_ids')
    if not isinstance(ids, list) or not 1 <= len(ids) <= maximum or any(not isinstance(x,str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError(f'Choose 1–{maximum} distinct clips')
    sequences = [s for s in project.get('sequences',[]) if s.get('id') == body.get('sequence')]
    if len(sequences) != 1: raise ValueError('Choose one existing sequence')
    sequence = sequences[0]; frame_rate(sequence.get('fps'))
    targets = []
    for identity in ids:
        matches = [(ti,ci,tr,c) for ti,tr in enumerate(sequence.get('tracks',[])) for ci,c in enumerate(tr.get('clips',[])) if c.get('id') == identity]
        if len(matches) != 1: raise ValueError('A selected audio clip is missing or ambiguous')
        ti,ci,track,clip = matches[0]
        if track.get('locked'): raise ValueError('Unlock every selected audio clip track first')
        if track.get('kind') not in ('audio','video'): raise ValueError('Choose audio or video clips')
        _clock(clip)
        media = project.get('media',{}).get(clip.get('media_id'))
        if not clip.get('sequence_id') and (not isinstance(media,dict) or not media.get('has_audio')):
            raise ValueError('Every selected clip needs an audio source')
        if media and media.get('synthetic'): raise ValueError('Render generated audio to media before using this gain workflow')
        if audible and (clip.get('enabled') is False or clip.get('hold') or track['kind']=='video' and (clip.get('audio') or {}).get('linked') is False):
            raise ValueError('Enable clip audio before analysis; held or unlinked picture audio cannot be measured')
        targets.append((ti,ci,track,clip))
    return sequence, targets


def _gain(clip, delta):
    delta = number(delta,'Gain adjustment'); value = copy.deepcopy(clip)
    audio = value.get('audio')
    if audio is None: audio = value['audio'] = {}
    if not isinstance(audio,dict): raise ValueError('Clip audio settings must be an object')
    base = number(audio.get('gain_db',0),'Clip gain'); changed = base+delta
    if not -40 <= changed <= 24: raise ValueError('The edited clip gain must stay between −40 and +24 dB; no clipping or rounding was applied')
    frames = value.get('keyframes')
    if frames is None:frames={}
    if not isinstance(frames,dict): raise ValueError('Clip keyframes must be an object')
    points = frames.get('audio.gain_db')
    if points is None:points=[]
    if not isinstance(points,list) or len(points)>MAX_POINTS: raise ValueError('Too many or invalid gain keyframes')
    previous = -math.inf
    for point in points:
        if not isinstance(point,dict): raise ValueError('Gain keyframes must be objects')
        at=number(point.get('t'),'Gain keyframe time'); gain=number(point.get('v'),'Gain keyframe value')+delta
        if at<=previous or not -40<=gain<=24: raise ValueError('Edited gain keyframes need increasing times and values between −40 and +24 dB')
        previous=at; point['v']=gain
    # Current trims retain all generic gain anchors directly, including signed
    # offscreen times. Version-1 restoration records only define ramp/duck.
    # Never carry an unknown gain-restoration record across a gain edit: future
    # extension could otherwise revive unadjusted values.
    window=value.get('source_edit_window')
    def gain_history(item):
        if isinstance(item,dict):
            return any('gain' in str(key).lower() or gain_history(child) for key,child in item.items())
        return isinstance(item,list) and any(gain_history(child) for child in item)
    if window is not None and gain_history(window):
        raise ValueError('This clip has unsupported saved gain restoration data; resolve that history before editing gain')
    if delta: audio['gain_db']=changed
    else: return copy.deepcopy(clip)
    return value


def _whole_clip_op(project, sequence, target, clip):
    si=project['sequences'].index(sequence); ti,ci,_,_=target
    return {'op':'set','path':f'/sequences/{si}/tracks/{ti}/clips/{ci}','value':clip}


def manual(project, body):
    if not isinstance(body,dict): raise ValueError('Gain command must be an object')
    mode=body.get('mode'); requested=number(body.get('value'),'Gain value')
    if mode not in ('set','adjust'): raise ValueError('Choose set or adjust gain')
    if mode=='set' and not -40<=requested<=24: raise ValueError('Set gain between −40 and +24 dB')
    sequence,targets=_selection(project,body);ops=[];moves=[]
    for target in targets:
        clip=target[3];audio=clip.get('audio') or {}
        if not isinstance(audio,dict): raise ValueError('Clip audio settings must be an object')
        base=number(audio.get('gain_db',0),'Clip gain');delta=requested-base if mode=='set' else requested
        updated=_gain(clip,delta)
        if updated!=clip:ops.append(_whole_clip_op(project,sequence,target,updated));moves.append({'clip_id':clip['id'],'from':base,'to':base+delta,'delta':delta})
    if len(json.dumps(ops,allow_nan=False).encode())>MAX_BYTES: raise ValueError('The gain edit contains too much clip data')
    return {'ops':ops,'summary':{'kind':'audio_gain','changed':bool(ops),'gains':moves,'message':f'Updated gain on {len(ops)} clip(s); automation shape and other clip settings preserved.'}}


def capture(project, body, mode, context):
    if not isinstance(body,dict) or mode not in ('peak','loudness','beats'): raise ValueError('Choose peak, loudness, or beat analysis')
    timeline=body.get('clip_ids') is not None
    if timeline and body.get('media_id') is not None: raise ValueError('Choose timeline clips or a raw source, not both')
    if mode=='beats':
        every=body.get('every',1)
        if isinstance(every,bool) or not isinstance(every,int) or not 1<=every<=128: raise ValueError('Marker spacing must be an integer from 1 to 128 measured transients')
        settings={'every':every}
    else:
        target=body.get('target',-3 if mode=='peak' else -18)
        settings={'target':number(target,'Normalization target',minimum=-40,maximum=0)}
    if timeline:
        sequence,targets=_selection(project,body,maximum=1 if mode=='beats' else MAX_ITEMS,audible=True)
    else:
        mid=body.get('media_id');source,offset,factor,identity=source_inputs(project,mid);media=project['media'][mid]
        if not media.get('has_audio'): raise ValueError('Choose a source with audio')
        begin=number(body.get('in',0),'Source In',minimum=0);end=number(media.get('duration') if body.get('out') is None else body['out'],'Source Out',minimum=0)
        if not begin<end<=number(media.get('duration'),'Media duration',minimum=0)+1e-9: raise ValueError('Choose a nonempty range inside the source')
        clip={'id':mid,'media_id':mid,'start':0,'in_':begin,'out':end,'speed':1,'audio':{'gain_db':0}}
        track={'id':'audio-analysis','kind':'audio','index':0,'clips':[clip]}
        sequence={'id':'audio-analysis','width':64,'height':48,'fps':30,'tracks':[track]};targets=[(0,0,track,clip)]
    items=[];seconds=0;render_seconds=0;render_native_seconds=0;render_visits=0
    for _,_,track,clip in targets:
        duration=_clock(clip);seconds+=duration
        if duration>MAX_SECONDS or seconds>MAX_TOTAL_SECONDS: raise ValueError('Analyze at most 10 minutes per clip and one hour total per task')
        if clip.get('media_id'):
            source,offset,factor,source_identity=source_inputs(project,clip['media_id'])
            media=project['media'][clip['media_id']]
            if (clip['out']>number(media.get('duration'),'Media duration',minimum=0)+1e-9
                    or (clip['out']+offset)/factor>number(source.get('duration'),'Parent duration',minimum=0)/interpretation_factor(source)+1e-9):
                raise ValueError('The selected clip range extends beyond its source')
        _gain(clip,0)  # Validate all retained gain anchors before expensive work.
        snapshot=_snapshot(project,sequence,track,clip)
        isolated=copy.deepcopy(clip);isolated['start']=0
        snapshot['sequences'][0]['tracks'][0]['clips']=[isolated]
        # Picture dependencies (such as a matte on another track) do not affect
        # isolated clip audio. Inaudible video link states were refused above.
        snapshot['sequences'][0]['tracks'][0]['kind']='audio'
        from audio_measurement import estimated_work
        estimate=estimated_work(snapshot,sequence['id']);render_seconds+=estimate['seconds'];render_native_seconds+=estimate['native_seconds'];render_visits+=estimate['sequences']
        if max(render_seconds,render_native_seconds)>MAX_TOTAL_SECONDS or render_visits>256:
            raise ValueError('The captured audio, including native source windows and nested renders, exceeds one hour or 256 sequence visits')
        report=inspect_resources(snapshot,sequence['id'],{'format':'audio','acodec':'wav_float'})
        errors=[r['message'] for r in report['issues'] if r['severity']=='error']
        if errors:raise ValueError('; '.join(errors[:8]))
        resources=[stamp(path) for path in sorted({r['path'] for r in report['resources']})]
        # Names/proxy status are presentation, not audio identity.
        for m in snapshot['media'].values():m.pop('name',None)
        item={'id':clip['id'],'media_id':clip.get('media_id'),'track_id':track['id'],'track_kind':track['kind'],
              'clip':copy.deepcopy(clip),'duration':duration,'sequence':sequence['id'],'project':snapshot,'resources':resources}
        items.append(item)
        if len(json.dumps(items,allow_nan=False).encode())>MAX_BYTES:raise ValueError('The captured audio dependencies exceed the task budget')
    value={'version':1,'kind':'audio_analysis','mode':mode,'scope':'timeline' if timeline else 'media','sequence':sequence['id'] if timeline else None,
           'clip_ids':[t[3]['id'] for t in targets] if timeline else [],'media_id':None if timeline else body['media_id'],
           'range':None if timeline else {'start':targets[0][3]['in_'],'end':targets[0][3]['out']},'settings':settings,'items':items}
    return {**value,'signature':digest(value),'context':copy.deepcopy(context)}


def validate_current(project,payload,context):
    if any(context.get(k)!=payload['context'].get(k) for k in ('workspace','project')):raise ValueError('Open the original audio-analysis project first')
    body={**payload['settings']}
    if payload['scope']=='timeline':body.update(sequence=payload['sequence'],clip_ids=payload['clip_ids'])
    else:body.update(media_id=payload['media_id'],**{'in':payload['range']['start'],'out':payload['range']['end']})
    if capture(project,body,payload['mode'],context)['signature']!=payload['signature']:
        raise ValueError('A selected clip, source, or audio processing setting changed; analyze it again')
    return True


def check_sources(payload):
    for item in payload['items']:
        if [stamp(resource[0]) for resource in item['resources']]!=item['resources']:
            raise ValueError('An audio source or processing resource changed during analysis')


def plan(project,payload,result,context,identity):
    validate_current(project,payload,context)
    if result.get('kind')!='audio_analysis' or result.get('version')!=1 or result.get('signature')!=payload['signature'] or result.get('mode')!=payload['mode'] or result.get('scope')!=payload['scope'] or result.get('clock')!='clip-local' or result.get('sequence')!=payload['sequence'] or result.get('clip_ids')!=payload['clip_ids']:
        raise ValueError('The audio result does not match its captured input')
    for key in ('media_id','range'):
        if key in result and result[key]!=payload.get(key):raise ValueError('The audio result source range changed')
    measured=result.get('measurements')
    if not isinstance(measured,list) or len(measured)!=len(payload['items']) or any(not isinstance(r,dict) or r.get('id')!=c['id'] or r.get('media_id')!=c['media_id'] for r,c in zip(measured,payload['items'])):
        raise ValueError('The audio result contains missing or mismatched clip measurements')
    for row,item in zip(measured,payload['items']):
        expected=math.floor(item['duration']*48000+.5)/48000
        if row.get('sample_rate')!=48000 or row.get('channels')!=2 or abs(number(row.get('duration'),'Measured duration')-expected)>1e-10:
            raise ValueError('Audio measurement sample clock or duration does not match the captured clip')
    ops=[];gains=[];markers=[];warnings=list(result.get('warnings') or [])
    for row in measured:
        for warning in row.get('warnings') or []:
            if warning not in warnings:warnings.append(warning)
    if payload['scope']=='timeline':
        sequence,targets=_selection(project,{'sequence':payload['sequence'],'clip_ids':payload['clip_ids']},maximum=1 if payload['mode']=='beats' else MAX_ITEMS,audible=True)
        if payload['mode'] in ('peak','loudness'):
            field='peak_db' if payload['mode']=='peak' else 'integrated_lufs'
            for target,item in zip(targets,measured):
                if item.get('silent') or item.get(field) is None:raise ValueError('Silent or gated audio cannot be normalized reliably; no gain changes were applied')
                value=number(item[field],'Measured audio level');delta=payload['settings']['target']-value;clip=target[3]
                # A fresh float-PCM scan can differ by sub-micro-dB after an
                # exact gain change; LUFS is reported at .001 LU precision.
                # Do not create Undo entries below these measurement limits.
                if abs(delta)<(1e-6 if payload['mode']=='peak' else .0005):delta=0.
                updated=_gain(clip,delta)
                if updated!=clip:ops.append(_whole_clip_op(project,sequence,target,updated))
                sample_peak=item.get('peak_db');true_peak=item.get('true_peak_dbtp')
                predicted_peak=None if sample_peak is None else number(sample_peak,'Sample peak')+delta
                predicted_true=None if true_peak is None else number(true_peak,'True peak')+delta
                gains.append({'clip_id':clip['id'],'measured':value,'target':payload['settings']['target'],'delta_db':delta,
                              'from_db':(clip.get('audio') or {}).get('gain_db',0),'to_db':(updated.get('audio') or {}).get('gain_db',0),
                              'automation_points':len((clip.get('keyframes') or {}).get('audio.gain_db') or []),
                              'predicted_peak_db':predicted_peak,'predicted_true_peak_dbtp':predicted_true})
                if any(p is not None and p>0 for p in (predicted_peak,predicted_true)):
                    warnings.append(f"Clip {clip['id']} is predicted to exceed 0 dBFS/dBTP after this gain adjustment; no limiter is added.")
        else:
            beats=measured[0].get('beats');clip=targets[0][3];duration=payload['items'][0]['duration']
            if not isinstance(beats,list) or not 1<=len(beats)<=10000:raise ValueError('No reliable measured transients are available for markers')
            previous=-1
            for at in beats:
                at=number(at,'Measured transient time',minimum=0)
                if at<=previous or at>=duration:raise ValueError('Transient measurements must increase inside the analyzed clip')
                previous=at
            reserved={x.get('id') for s in project.get('sequences',[]) for key in ('markers','captions') for x in (s.get(key) or [])}
            reserved.update(c.get('id') for s in project.get('sequences',[]) for t in s.get('tracks',[]) for c in t.get('clips',[]))
            for i,at in enumerate(beats[::payload['settings']['every']]):
                serial=0
                while True:
                    marker='audio_'+hashlib.sha256(f'{identity}:{i}:{serial}'.encode()).hexdigest()[:24]
                    if marker not in reserved:break
                    serial+=1
                reserved.add(marker);markers.append({'id':marker,'time':clip['start']+at,'name':f'Transient {i*payload["settings"]["every"]+1}','type':'comment','color':'purple'})
            existing=sequence.get('markers') or []
            if not isinstance(existing,list) or len(existing)+len(markers)>100000:raise ValueError('Too many or invalid sequence markers')
            ops.append({'op':'set','path':f'/sequences/{project["sequences"].index(sequence)}/markers','value':copy.deepcopy(existing)+markers})
    summary={'kind':'audio_analysis','mode':payload['mode'],'gains':gains,'markers':markers,'warnings':warnings,
             'message':('Raw source measurements are read-only; no timeline changes are planned.' if payload['scope']=='media' else
                        f'Review {len(markers)} measured transient markers; these are not verified musical beats or downbeats.' if payload['mode']=='beats' else
                        f'Review normalization of {len(gains)} clip(s); track and master processing are excluded.')}
    if len(json.dumps(ops,allow_nan=False).encode())>MAX_BYTES:raise ValueError('The planned audio edit exceeds the data budget')
    return {'ops':ops,'summary':summary,'fingerprint':digest({'task':identity,'context':context,'signature':payload['signature'],'result':result,'ops':ops})}
