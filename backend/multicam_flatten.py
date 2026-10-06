"""Reviewed direct-source multicamera flattening with explicit sampling limits.

This removes the multicamera source reference; it never substitutes a renamed
ordinary nest. Unsupported composition stages produce a read-only refusal.
"""
import copy
import math

from audio_contract import destination, route, fade_spec
from multicam import view
from overlap_normalization import _clock, slice_clip
from render import audio_fx_chain, color_chain, fx_chain, seq_total
from sequence_nesting import _library, _overlap, _audible_source, identity
from source_relink import bounded, digest, number
from timeline_time import frame_rate, interpretation_factor

MAX_SELECTED = 100
MAX_OUTPUT = 10000
GUIDANCE = ' Use Render & Replace to preserve this processing, or simplify the indicated source edit and review again.'


def _close(a, b): return abs(a-b) <= max(1e-9, 8*math.ulp(max(abs(a),abs(b))))
def _integer(value): return _close(value, round(value))
def _fx(value): return len(audio_fx_chain(value)) > 1

def _speed(clip):
    points=clip.get('time_remap') or []
    if points:
        values=[p['v'] for p in points]
        if any(not _close(v,values[0]) for v in values): return None
        return values[0]
    return clip.get('speed',1)


def _envelope(clip):
    return any(f['duration'] for f in fade_spec(clip,_clock(clip))) or any(
        points for key,points in (clip.get('keyframes') or {}).items() if key.startswith('audio.'))


def _audio_processing(clip):
    audio=clip.get('audio') or {}
    return (_fx(clip.get('audio_fx')) or any(f.get('enabled',True) for f in clip.get('afx_stack') or []) or
            audio.get('channels') not in (None,'stereo') or bool(audio.get('pan')) or _envelope(clip))


def _neutral_picture(clip):
    tf=clip.get('transform') or {}
    if any(not _close(number(v,'Transform value',-1e6), {'scale':1,'opacity':1}.get(k,0)) for k,v in tf.items() if k in ('x','y','scale','rotation','opacity')):return False
    if any(points for key,points in (clip.get('keyframes') or {}).items() if not key.startswith('audio.')):return False
    return not (color_chain(clip.get('color')) or fx_chain(clip,64,64) or clip.get('fx') or clip.get('fx_stack') or clip.get('mask') or clip.get('adjustment') or
                clip.get('blend') not in (None,'normal') or clip.get('title') or clip.get('graphic') or clip.get('time_interpolation') in ('frame_blending','optical_flow') or
                any((clip.get('transition_'+side) or {}).get('duration') for side in ('in','out')))


def _shift_gain(clip, delta):
    if not delta:return
    audio=clip.setdefault('audio',{});audio['gain_db']=number(audio.get('gain_db',0),'Clip gain',-1000)+delta
    for point in (clip.get('keyframes') or {}).get('audio.gain_db') or []:point['v']+=delta
    # Current source-edit history stores only ramp and duck; remove obsolete
    # ramp restoration when the media clock is replaced, retain duck history.


def _source(outer,inner,q0,q1,speed):
    v=_speed(inner);local0=q0-inner['start'];local1=q1-inner['start']
    if inner.get('hold'):lo=inner['in_'];hi=lo+(q1-q0)/speed
    elif inner.get('reverse'):lo=inner['out']-local1*v;hi=inner['out']-local0*v
    else:lo=inner['in_']+local0*v;hi=inner['in_']+local1*v
    return lo,hi,1 if inner.get('hold') else speed*v,bool(outer.get('reverse')) != bool(inner.get('reverse'))


def plan(project,body,*,proc_holder=None):
    bounded(body,'Flatten request',128*1024)
    if not isinstance(body,dict):raise ValueError('Flatten request must be an object')
    context=body.get('_context')
    if not isinstance(context,dict) or any(not isinstance(context.get(k),str) or not context[k] for k in ('workspace','project','revision')):raise ValueError('Flatten requires the saved project context')
    sid=identity(body.get('sequence'),'Sequence');ids=body.get('clip_ids')
    if not isinstance(ids,list) or not 1<=len(ids)<=MAX_SELECTED or any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):raise ValueError('Choose 1–100 unique multicamera clip IDs in order')
    sequences,clipsets,spans,reserved,_=_library(project)
    if sid not in sequences or any(cid not in clipsets[sid] for cid in ids):raise ValueError('The captured multicamera selection no longer exists')
    seq=sequences[sid];selected=set(ids);parent=copy.deepcopy(seq);issues=[];warnings=[];resolved=[];replacements=[];added=[];count=0
    seed=digest({'project':project,'context':context,'sequence':sid,'clip_ids':ids});counter=0;copied_bytes=0;work=0;copy_sizes={}
    def admit_copy(clip):
        nonlocal copied_bytes
        key=id(clip)
        if key not in copy_sizes:copy_sizes[key]=bounded(clip,'Flatten source clip')
        copied_bytes+=copy_sizes[key]
        if copied_bytes>32*1024*1024:raise ValueError('Flatten would copy more than 32 MiB of clip metadata; select fewer edits')
    def fresh(prefix):
        nonlocal counter
        while True:
            counter+=1;value=prefix+'_'+digest([seed,counter])[:20]
            if value not in reserved:reserved.add(value);return value
    def issue(code,message,**extra):
        if len(issues)<100:issues.append({'code':code,'severity':'error','message':message+GUIDANCE,**extra})
    def record(c):return {'start':c['start'],'end':c['start']+_clock(c),'media_id':c['media_id'],'source_in':c['in_'],'source_out':c['out'],'speed':c.get('speed',1),'reverse':bool(c.get('reverse')),'hold':bool(c.get('hold'))}
    # Keep every authored unrelated track/clip, including existing overlaps.
    for track in parent['tracks']:track['clips']=[c for c in track['clips'] if c['id'] not in selected]
    groups={clipsets[sid][cid][1].get('group') for cid in ids}-{None,''}
    for other_sid,values in clipsets.items():
        for cid,(_,c) in values.items():
            if c.get('group') in groups and not (other_sid==sid and cid in selected):issue('group_boundary','Include the entire linked group before flattening',clip_id=cid)
            if other_sid==sid and (c.get('audio_detached_id') or c.get('unlinked_from')) and (cid in selected or c.get('audio_detached_id') in selected or c.get('unlinked_from') in selected):issue('detached_boundary','Resolve the existing detached-audio association before flattening',clip_id=cid)
    audio_memo={}
    audible={cid:route(seq,t,c) for cid,(t,c) in clipsets[sid].items() if _audible_source(project,c,sequences,memo=audio_memo)}
    processed_buses={}
    for cid in ids:
        tr,outer=clipsets[sid][cid];child=sequences.get(outer.get('sequence_id'));duration=_clock(outer)
        if any(other['id']!=cid and _overlap(spans[(sid,cid)],spans[(sid,other['id'])]) for other in tr['clips']):issue('outer_overlap','The selected multicamera clip overlaps another clip on its picture track',clip_id=cid)
        if tr.get('locked'):issue('locked_track','Unlock the selected track before flattening',clip_id=cid,track=tr['id'])
        if not child or not child.get('multicam') or outer.get('media_id'):
            issue('not_multicam','Select only multicamera source clips',clip_id=cid);continue
        if tr['kind']!='video':issue('source_track','Flatten the picture multicamera clip and its reviewed sound together',clip_id=cid);continue
        if outer.get('hold'):
            if outer['in_']>=seq_total(child):issue('source_range','The held multicamera point is outside its source',clip_id=cid)
        elif outer['out']>seq_total(child)+1e-9:issue('source_range','The multicamera range exceeds its source duration',clip_id=cid)
        u=_speed(outer)
        if u is None:issue('source_sampling','A varying outer speed ramp requires its intermediate sampling stage',clip_id=cid);continue
        if child.get('captions') or child.get('caption_style',{}).get('burn'):issue('child_captions','The multicamera source has composition-wide captions',clip_id=cid)
        if (child['width'],child['height'])!=(seq['width'],seq['height']):issue('canvas_stage','The child and parent canvases differ; direct sources cannot retain both fit stages',clip_id=cid)
        if frame_rate(child['fps'])!=frame_rate(seq['fps']):issue('source_sampling','The child and parent frame rates differ; removing the intermediate CFR stage changes frame selection',clip_id=cid)
        if outer.get('adjustment'):issue('outer_picture_processing','An outer adjustment depends on the parent composition rather than only this camera source',clip_id=cid)
        temporal={'echo','posterize_time','replicate','timecode','stabilize','add_noise'}
        if any(f.get('enabled') is not False and f.get('type') in temporal for f in outer.get('fx_stack') or []):issue('outer_picture_processing','The outer effect has temporal, generated-clock or source-analysis state that direct fragments cannot retain',clip_id=cid)
        if outer.get('time_interpolation') in ('optical_flow','frame_blending'):issue('source_sampling','Outer optical flow/frame blending needs its intermediate camera sampling stage',clip_id=cid)
        if outer.get('fit') not in (None,'contain'):issue('outer_picture_fit','The outer fit must preserve the child canvas fit stage',clip_id=cid)
        if any((outer.get('transition_'+side) or {}).get('duration') for side in ('in','out')):issue('outer_transition','The outer picture transition spans a composition boundary',clip_id=cid)
        angle=outer.get('multicam_angle',0)
        try:selected_view=view(child,angle)
        except ValueError as error:issue('angle',str(error),clip_id=cid);continue
        camera=next(t for t in selected_view['tracks'] if t['kind']=='video' and not t.get('_mc_picture_hidden'))
        result={'clip_id':cid,'source_sequence':child['id'],'angle':angle,'audio_mode':'follow' if child.get('multicam_audio')=='follow' else 'fixed','picture_ranges':[],'audio_ranges':[]};resolved.append(result)
        group=outer.get('group') or fresh('group');pic=[];sound=[]
        qin,qout=outer['in_'],outer['out'];frame=float(frame_rate(child['fps']))
        if outer.get('hold'):qin=math.floor(outer['in_']*frame+1e-9)/frame;qout=qin+1/frame
        def intersections(track):
            nonlocal work
            work+=len(track['clips'])
            if work>1000000:raise ValueError('Flatten source comparison exceeds one million clip visits')
            values=[]
            for inner in track['clips']:
                if inner.get('enabled') is False:continue
                a=max(qin,inner['start']);b=min(qout,inner['start']+_clock(inner))
                if b>a+1e-10:values.append((inner,a,b))
            return values
        pictures=intersections(camera)
        work+=len(pictures)*len(pictures)
        if work>1000000:raise ValueError('Flatten picture comparison exceeds one million pairs')
        picture_coverage=sum(b-a for _,a,b in pictures)
        has_gap=picture_coverage<qout-qin-1e-9
        if has_gap and (color_chain(outer.get('color')) or fx_chain(outer,seq['width'],seq['height']) or outer.get('mask') or any(f.get('enabled') is not False for f in outer.get('fx_stack') or [])):
            issue('gap_picture_processing','The outer filters or mask may render content inside empty camera intervals; direct fragments would omit those canvas pixels',clip_id=cid)
        for i,(inner,a,b) in enumerate(pictures):
            if any(_overlap((a,b),(x,y)) for _,x,y in pictures[:i]):issue('camera_overlap','Overlapping camera edits need their original compositing stage',clip_id=cid)
            media=project['media'].get(inner.get('media_id'))
            if not media or inner.get('sequence_id') or media.get('synthetic'):issue('camera_source','This camera edit must resolve directly to original media',clip_id=inner['id']);continue
            v=_speed(inner)
            if v is None:issue('source_sampling','A varying camera speed ramp requires its intermediate sampling stage',clip_id=inner['id']);continue
            physical=project['media'].get(media.get('subclip_of'),media)
            if media.get('vfr') or physical.get('vfr'):issue('source_sampling','Known variable-frame-rate camera media requires the intermediate sampling stage',clip_id=inner['id'])
            if not _neutral_picture(inner):issue('inner_picture_processing','The camera edit has an additional picture processing or transition stage',clip_id=inner['id'])
            if inner.get('fit') not in (None,'contain'):issue('inner_picture_fit','The camera uses a non-default fit stage',clip_id=inner['id'])
            if not media.get('is_image') and (inner['in_']>=media.get('duration',0) if inner.get('hold') else inner['out']>media.get('duration',0)+1e-9):issue('source_range','The camera edit exceeds its original media range',clip_id=inner['id'])
            lo,hi,spd,reverse=_source(outer,inner,a,b,u)
            begin=(outer['out']-b)/u if outer.get('reverse') else (a-outer['in_'])/u
            end=(outer['out']-a)/u if outer.get('reverse') else (b-outer['in_'])/u
            if outer.get('hold'):
                begin,end=0,duration;local=qin-inner['start'];lo=inner['in_'] if inner.get('hold') else (inner['out']-local*v if inner.get('reverse') else inner['in_']+local*v)
                if inner.get('reverse') and not inner.get('hold'):lo-=interpretation_factor(media)/float(frame_rate(media.get('frame_rate',media.get('fps'))))
                hi=lo+duration;spd=1;reverse=False
            elif not inner.get('hold') and not media.get('is_image'):
                native=float(frame_rate(media.get('frame_rate',media.get('fps'))));factor=interpretation_factor(media);off=media.get('sub_in',0) or 0;step=v*native/(factor*frame)
                grid=all(_integer(x*frame) for x in (outer['in_'],outer['out'],inner['start'],a,b)) and all(_integer((x+off)/factor*native) for x in (lo,hi))
                cadence=_close(step,1) or (not outer.get('reverse') and _integer(u) and _integer(step) and step>=1)
                if not grid or not cadence:issue('source_sampling','The camera/native frame phase or composed speed requires the intermediate CFR sampling stage',clip_id=inner['id'])
            admit_copy(outer)
            if lo< -1e-9:issue('source_sampling','The composed held frame falls before its original source',clip_id=inner['id'])
            try:replacement=slice_clip(outer,max(0,begin),min(duration,end),duration)
            except ValueError as error:issue('outer_slice',str(error),clip_id=cid);continue
            replacement.update(id=cid if not pic else fresh('clip'),media_id=inner['media_id'],sequence_id=None,multicam_angle=None,in_=max(0,lo),out=hi,speed=spd,reverse=reverse,hold=bool(inner.get('hold') or outer.get('hold')),time_remap=None,group=group)
            replacement['audio']={**(replacement.get('audio') or {}),'linked':False}
            history=replacement.get('source_edit_window')
            if isinstance(history,dict):history.pop('ramp',None)
            replacement.setdefault('provenance',{})['multicam_flatten']={'sequence':child['id'],'outer_clip':cid,'camera_clip':inner['id'],'angle':angle}
            pic.append(replacement);result['picture_ranges'].append(record(replacement))
        parent_bus=destination(seq,tr);child_buses={};audio_candidates=[]
        for track in selected_view['tracks']:
            for inner,a,b in intersections(track):
                if not _audible_source(project,inner,sequences,memo=audio_memo):continue
                bus=route(selected_view,track,inner)
                if bus is not None:child_buses[bus['id']]=bus;audio_candidates.append((inner,a,b,bus))
        if outer.get('hold'):audio_candidates=[]
        if audio_candidates and parent_bus.get('locked'):issue('locked_audio_bus','Unlock the routed parent sound bus before flattening',track=parent_bus['id'],clip_id=cid)
        parent_fx=_fx(parent_bus.get('audio_fx'));child_fx=[b for b in child_buses.values() if _fx(b.get('audio_fx'))];master_fx=_fx((child.get('master') or {}).get('audio_fx'))
        if parent_fx:
            peers=[i for i,b in audible.items() if b is not None and b['id']==parent_bus['id'] and i not in selected]
            if peers:issue('audio_bus_boundary','Include every contributor to the processed parent audio bus, including earlier/later clips',track=parent_bus['id'],clip_ids=peers[:50])
        carried_fx=None;carried_master=False
        if child_fx or master_fx:
            if parent_fx or len(child_buses)!=1 or (child_fx and master_fx) or not _close(u,1) or outer.get('reverse') or not _close(outer['in_'],0) or not _close(outer['out'],seq_total(child)) or _audio_processing(outer):
                issue('child_audio_processing','The child sound bus/master processing cannot be moved across this crop, clock, outer processing or parent bus',clip_id=cid)
            else:
                carried_master=not bool(child_fx)
                carried_fx=copy.deepcopy(child_fx[0]['audio_fx'] if child_fx else child['master']['audio_fx'])
        for inner,a,b,bus in audio_candidates:
            media=project['media'].get(inner.get('media_id'));v=_speed(inner)
            if any((inner.get('transition_'+side) or {}).get('duration') for side in ('in','out')):issue('audio_transition_stage','A source-camera picture transition can change the pre-rendered sound window',clip_id=inner['id'])
            if not media or inner.get('sequence_id') or media.get('synthetic') or v is None:issue('audio_source','The sound edit must have a constant clock and direct original media source',clip_id=inner['id']);continue
            if inner['out']>media.get('duration',0)+1e-9:issue('source_range','The sound edit exceeds its original media range',clip_id=inner['id'])
            outer_audio=outer.get('audio') or {}
            if outer_audio.get('channels') not in (None,'stereo') and media.get('channel_mode') not in (None,'stereo'):
                issue('audio_channel_stages','The source channel selection and outer channel selection require two ordered stages',clip_id=inner['id'])
            if not _close(v/interpretation_factor(media),1) and (outer_audio.get('pan') or outer_audio.get('channels') not in (None,'stereo')):
                issue('audio_channel_stages','Outer pan/channel selection follows the original inner stretch; moving it before the stretch can change its analysis',clip_id=inner['id'])
            if not _close(v/interpretation_factor(media),1) and (not _close(a,inner['start']) or not _close(b,inner['start']+_clock(inner))):issue('audio_tempo_crop','Cropping an already retimed camera sound stage would reset its stretch/resampling phase',clip_id=inner['id'])
            if not _close(v/interpretation_factor(media),1) and len(inner.get('time_remap') or [])>1:issue('audio_tempo_stages','Multiple inner tempo segments retain separate stretch states even when their displayed speeds match',clip_id=inner['id'])
            if not _close(u,1) and not _close(v/interpretation_factor(media),1):issue('audio_tempo_stages','Two sound time-stretch stages cannot be replaced by one without changing samples',clip_id=inner['id'])
            lo,hi,spd,reverse=_source(outer,inner,a,b,u)
            begin=(outer['out']-b)/u if outer.get('reverse') else (a-outer['in_'])/u;end=(outer['out']-a)/u if outer.get('reverse') else (b-outer['in_'])/u
            inner_processing=_audio_processing(inner);outer_processing=_audio_processing(outer)
            if inner_processing and (outer_processing or not _close(u,1) or outer.get('reverse')):issue('audio_processing_stages','Both inner and outer sound processing, or retimed inner envelopes, need their separate stages',clip_id=inner['id']);continue
            if (_fx(inner.get('audio_fx')) or inner.get('afx_stack')) and (a>inner['start']+1e-9 or b<inner['start']+_clock(inner)-1e-9):issue('audio_effect_crop','Cropping a stateful inner audio effect would reset its processing state',clip_id=inner['id'])
            if _fx(outer.get('audio_fx')) or outer.get('afx_stack'):issue('outer_audio_effects','Outer audio effects require the original mixed-sound processing boundary',clip_id=cid)
            use_inner=inner_processing or carried_fx is not None
            admit_copy(inner if use_inner else outer)
            try:replacement=slice_clip(inner,a-inner['start'],b-inner['start']) if use_inner else slice_clip(outer,max(0,begin),min(duration,end),duration)
            except ValueError as error:issue('audio_slice',str(error),clip_id=inner['id']);continue
            replacement.update(id=fresh('clip'),start=outer['start']+begin,media_id=inner['media_id'],sequence_id=None,multicam_angle=None,in_=lo,out=hi,speed=spd,reverse=reverse,hold=False,time_remap=None,group=group,enabled=outer.get('enabled',True))
            if not use_inner:
                for field in ('transform','color','fx','fx_stack','mask','transition_in','transition_out','graphic','title'):replacement.pop(field,None)
                replacement['keyframes']={k:v for k,v in (replacement.get('keyframes') or {}).items() if k.startswith('audio.')}
            gain=(outer.get('audio') or {}).get('gain_db',0) if use_inner else (inner.get('audio') or {}).get('gain_db',0)
            if carried_fx is None:gain+=number(bus.get('gain_db',0) or 0,'Child bus gain',-1000)+number((child.get('master') or {}).get('gain_db',0) or 0,'Child master gain',-1000)
            elif use_inner:gain=number(bus.get('gain_db',0) or 0,'Child bus gain',-1000) if carried_master else 0
            _shift_gain(replacement,gain);replacement.setdefault('audio',{})['linked']=True
            replacement['audio']['maintain_pitch']=(inner.get('audio') or {}).get('maintain_pitch',True) if not _close(v/interpretation_factor(media),1) else (outer.get('audio') or {}).get('maintain_pitch',True)
            history=replacement.get('source_edit_window')
            if isinstance(history,dict):history.pop('ramp',None)
            replacement.setdefault('provenance',{})['multicam_flatten']={'sequence':child['id'],'outer_clip':cid,'audio_clip':inner['id'],'policy':result['audio_mode']}
            sound.append(replacement);result['audio_ranges'].append(record(replacement))
        if sound:
            existing=processed_buses.get(parent_bus['id']) if parent_fx else None
            if existing is None:
                track={'id':fresh('track'),'name':f"{child.get('name') or 'Multicam'} — Flattened sound",'kind':'audio','index':max([t['index'] for t in parent['tracks']]+[-1])+1,
                    'muted':bool(tr.get('muted') or parent_bus.get('muted') or (outer.get('audio') or {}).get('linked') is False),
                    'solo':bool(any(t.get('solo') for t in seq['tracks']) and (tr.get('solo') or parent_bus.get('solo'))),'locked':False,
                    'gain_db':number(parent_bus.get('gain_db',0) or 0,'Parent bus gain',-1000),'audio_fx':copy.deepcopy(carried_fx if carried_fx is not None else parent_bus.get('audio_fx') or {}),'clips':[]}
                if carried_fx is not None:track['gain_db']+=(0 if carried_master else number(next(iter(child_buses.values())).get('gain_db',0) or 0,'Child gain',-1000))+number((child.get('master') or {}).get('gain_db',0) or 0,'Child master gain',-1000)+number((outer.get('audio') or {}).get('gain_db',0) or 0,'Outer gain',-1000)
                parent['tracks'].append(track);added.append({k:track[k] for k in ('id','name','kind','index')})
                if parent_fx:processed_buses[parent_bus['id']]=track
            else:
                track=existing
                muted=bool(tr.get('muted') or parent_bus.get('muted') or (outer.get('audio') or {}).get('linked') is False)
                solo=bool(any(t.get('solo') for t in seq['tracks']) and (tr.get('solo') or parent_bus.get('solo')))
                if track['muted']!=muted or track['solo']!=solo:issue('audio_bus_admission','Processed parent bus contributors have different mute or solo admission',clip_id=cid)
            # Same-source-bus overlap was mixed originally. Separate lanes keep
            # it durable under later generic track overlap normalization.
            for replacement in sound:
                work+=len(track['clips'])
                if work>1000000:raise ValueError('Flatten audio comparison exceeds one million pairs')
                if any(_overlap((replacement['start'],replacement['start']+_clock(replacement)),(c['start'],c['start']+_clock(c))) for c in track['clips']):
                    issue('audio_overlap','Overlapping sound contributors need a preserved combined bus; direct flat lanes would change its normalization behavior',clip_id=cid)
                track['clips'].append(replacement)
        destination_track=next(t for t in parent['tracks'] if t['id']==tr['id']);destination_track['clips'].extend(pic)
        replacements.extend(c['id'] for c in pic+sound);count+=len(pic)+len(sound)
        if count+sum(len(v) for v in clipsets.values())-len(selected)>MAX_OUTPUT:raise ValueError('Flatten would exceed the 10000-clip project budget')
    if len(parent['tracks'])>256:issue('track_limit','Flatten would exceed 256 tracks')
    for cid,(tr,c) in clipsets[sid].items():
        if cid in selected or not _audible_source(project,c,sequences,memo=audio_memo):continue
        old=route(seq,tr,c);new=route(parent,tr,c)
        if (old is None)!=(new is None) or (old and new and (audio_fx_chain(old.get('audio_fx'))!=audio_fx_chain(new.get('audio_fx')) or (old.get('gain_db') or 0)!=(new.get('gain_db') or 0))):issue('routing_change','The added sound bus would change an unselected clip’s routing or processing',clip_id=cid)
    if not replacements and not issues:issue('empty_range','The selected ranges contain no direct picture or audible source clips')
    fixed_duration=False
    if not seq.get('duration') and seq_total(parent)<seq_total(seq)-1e-9:
        parent['duration']=seq_total(seq);fixed_duration=True
        warnings.append('The source ends with an empty camera/sound interval. The parent duration is retained explicitly to keep that trailing gap; extend the sequence duration when adding later material.')
    warnings.extend(['Direct picture flattening requires Full color output; Legacy nested color/alpha behavior is a separate rendering contract.',
                     'Original multicamera source sequences remain unchanged. New sound tracks retain current routing; future source-sequence edits no longer update these clips.',
                     'Audio envelopes use the renderer’s existing 128-sample automation clock; splitting may change its control-grid phase.'])
    summary={'kind':'multicam_flatten','sequence':sid,'selected_count':len(ids),'replacement_clip_ids':replacements,
        'source_tracks':[{'id':t['id'],'name':t.get('name',t['id']),'kind':t['kind'],'index':t['index'],'selected_clip_ids':[c['id'] for c in t['clips'] if c['id'] in selected]} for t in seq['tracks'] if any(c['id'] in selected for c in t['clips'])],
        'added_tracks':added,'resolved':resolved,'processing':['Original-media picture fragments with sliced outer processing','Separate fixed/follow direct sound fragments','Parent master retained once','Original multicamera source sequences retained'],
        'warnings':warnings,'affected_fields':['selected multicamera clips','dedicated sound tracks']+(['explicit sequence duration for trailing gap'] if fixed_duration else []),'message':f'Flatten {len(ids)} multicamera clip(s) into {len(replacements)} original-source fragment(s).'}
    si=next(i for i,s in enumerate(project['sequences']) if s['id']==sid)
    ops=[] if issues else [{'op':'set','path':f'/sequences/{si}','value':parent}]
    bounded(ops,'Flatten planned operations');result={'ok':not issues,'kind':'multicam_flatten','sequence':sid,'clip_ids':copy.deepcopy(ids),'settings':{},'issues':issues,'summary':summary,'ops':ops}
    result['fingerprint']=digest({'seed':seed,'result':result})
    if proc_holder and proc_holder.get('cancelled'):raise ValueError('Flatten review cancelled')
    return result
