"""Pure reviewed Nest: retain source clocks, compositing order and mix boundaries.

The server owns saved-context validation, policy and one atomic history commit.
No media is decoded and no caller data is mutated. The child master is neutral;
its processed sound returns through a dedicated neutral parent bus.
"""
import copy
import math
from fractions import Fraction

from audio_contract import destination, route
from effects import track_matte_params
from overlap_normalization import _clock
from render import audio_fx_chain, seq_total
from source_relink import bounded, digest, number
from timeline_time import frame_rate, from_frames

MAX_SELECTED = 1000
MAX_CLIPS = 10000
MAX_SEQUENCES = 1000
MAX_TRACKS = 256
MAX_PAIRS = 1000000
MAX_SECONDS = 864000
HISTORICAL = {'source_edit_window', 'rendered_from', 'provenance', 'source_provenance', 'workflow'}


def identity(value, label):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(label + ' must be a nonempty ID of at most 512 characters')
    return value


def _overlap(a, b):
    return min(a[1], b[1]) > max(a[0], b[0]) + 1e-10


def _audible_source(project, clip, sequences, visiting=None, memo=None):
    if clip.get('hold'): return False
    if clip.get('media_id'):
        media = project['media'][clip['media_id']]
        parent = project['media'].get(media.get('subclip_of'), media)
        synthetic=parent.get('synthetic') or {}
        return bool(parent.get('has_audio') or (isinstance(synthetic,dict) and synthetic.get('kind') == 'tone'))
    if clip.get('sequence_id'):
        key = clip['sequence_id']; visiting = set(visiting or ()); memo = {} if memo is None else memo
        if key in memo: return memo[key]
        if key in visiting: raise ValueError('Nested sequence cycle')
        visiting.add(key)
        memo[key] = any(_audible_source(project, c, sequences, visiting, memo) for t in sequences[key]['tracks'] for c in t['clips'])
        return memo[key]
    return False


def _library(project):
    bounded(project, 'Nest project')
    sequences = project.get('sequences'); media = project.get('media')
    if not isinstance(sequences, list) or not 1 <= len(sequences) < MAX_SEQUENCES or not isinstance(media, dict) or len(media) > 10000:
        raise ValueError('Nest supports fewer than 1000 sequences and at most 10000 media sources')
    lookup = {}; clips = {}; spans = {}; reserved = set(); count = 0
    for seq in sequences:
        if not isinstance(seq,dict):raise ValueError('Sequences must be objects')
        sid = identity(seq.get('id'), 'Sequence')
        if sid in lookup: raise ValueError('Repair duplicate sequence IDs before nesting')
        lookup[sid] = seq; reserved.add(sid); frame_rate(seq.get('fps'))
        tracks = seq.get('tracks')
        if not isinstance(tracks, list) or len(tracks) > MAX_TRACKS: raise ValueError('Nest supports at most 256 tracks per sequence')
        local = {}; tids = set(); indices = set()
        for track in tracks:
            if not isinstance(track,dict):raise ValueError('Tracks must be objects')
            if track.get('audio_fx') is not None and not isinstance(track['audio_fx'],dict):raise ValueError('Track audio effects must be an object')
            tid = identity(track.get('id'), 'Track'); kind = track.get('kind'); index = number(track.get('index'), 'Track index')
            if tid in tids or (kind,index) in indices or kind not in ('video','audio') or index != int(index) or index>1000000:
                raise ValueError('Repair duplicate/invalid track IDs, kinds or indices before nesting')
            tids.add(tid); indices.add((kind,index)); reserved.add(tid)
            values = track.get('clips')
            if not isinstance(values, list): raise ValueError('Track clips must be an array')
            count += len(values)
            if count > MAX_CLIPS: raise ValueError('Nest supports at most 10000 clips across the project')
            for clip in values:
                if not isinstance(clip,dict):raise ValueError('Clips must be objects')
                for field in ('audio','keyframes','transform','color'):
                    if clip.get(field) is not None and not isinstance(clip[field],dict):raise ValueError('Clip '+field+' must be an object')
                for field in ('fx_stack','afx_stack'):
                    if clip.get(field) is not None and (not isinstance(clip[field],list) or any(not isinstance(item,dict) for item in clip[field])):raise ValueError('Clip '+field+' must contain effect objects')
                cid = identity(clip.get('id'), 'Clip')
                if cid in local: raise ValueError('Repair duplicate clip IDs in the sequence before nesting')
                duration = _clock(clip)
                if clip['start'] + duration > MAX_SECONDS: raise ValueError('Nest supports timelines up to ten days')
                local[cid] = (track,clip); spans[(sid,cid)] = (clip['start'],clip['start']+duration); reserved.add(cid)
                if clip.get('group'): reserved.add(identity(clip['group'],'Clip group'))
            number(track.get('gain_db',0) or 0, 'Track gain', -1000)
        clips[sid] = local
    # Validate dependency structure before any recursive source search/copy.
    completed = {}; active = set()
    def depth(sid):
        if sid in active: raise ValueError('Repair nested sequence cycles before nesting')
        if sid in completed: return completed[sid]
        if sid not in lookup: raise ValueError('A nested sequence source is missing')
        if len(active) > 4: raise ValueError('Nested sequence graph exceeds the renderer depth limit')
        active.add(sid); result = 0
        for _,c in clips[sid].values():
            mid = c.get('media_id'); nested = c.get('sequence_id')
            if mid and nested: raise ValueError('Repair clips with conflicting media and sequence sources')
            if nested: result = max(result, 1+depth(identity(nested,'Nested sequence')))
            if mid:
                m = media.get(mid)
                if not isinstance(m,dict) or m.get('id') != mid: raise ValueError('A clip media source is missing or ambiguous')
                parent = media.get(m.get('subclip_of'),m)
                if not isinstance(parent,dict) or (m.get('subclip_of') and parent.get('subclip_of')): raise ValueError('Repair missing or nested subclip sources')
        active.remove(sid); completed[sid] = result; return result
    for sid in lookup: depth(sid)
    return lookup,clips,spans,reserved,completed


def _effective_span(project, seq, clip, span):
    transition = clip.get('transition_in') or {}
    if not isinstance(transition,dict): raise ValueError('Picture transition must be an object')
    length = number(transition.get('duration',0) or 0,'Picture transition duration')
    alignment = transition.get('align') or ('end' if transition.get('type','dissolve') in ('dissolve','fade') else 'start')
    media = project['media'].get(clip.get('media_id'))
    if not length or alignment not in ('center','end') or (media and media.get('is_image')) or not (media or clip.get('sequence_id')):
        return span
    handle = math.inf if clip.get('hold') else clip['in_']/max(clip.get('speed',1),1e-6)
    shift = min(length/(2 if alignment == 'center' else 1),handle,clip['start'])
    fps = float(frame_rate(seq['fps'])); shift = math.floor(shift*fps+1e-6)/fps
    return (span[0]-shift,span[1])


def _references(value, selected, parent_sid, child_sid, tracks, *, inside=False, scope=None):
    """Move explicit local edit references; leave provenance and sources intact."""
    if isinstance(value,list):
        for item in value: _references(item,selected,parent_sid,child_sid,tracks,inside=inside,scope=scope)
    elif isinstance(value,dict):
        is_clip = value.get('id') in selected
        local_scope = scope if is_clip else value.get('sequence',value.get('sequence_id',scope))
        if local_scope is None: local_scope=parent_sid
        for key,item in list(value.items()):
            if key in HISTORICAL: continue
            if key in ('clip_id','source_clip_id','target_clip_id','audio_detached_id','unlinked_from'):
                field_scope=value.get('source_sequence_id' if key.startswith('source_') else 'target_sequence_id' if key.startswith('target_') else 'sequence',local_scope)
                if not isinstance(item,str) and item is not None: raise ValueError('Clip edit references must be IDs')
                if item in selected and field_scope == parent_sid:
                    if not inside: raise ValueError('A reference outside the selection points to a selected clip; include or resolve it before nesting')
                elif inside and item and (field_scope == parent_sid or key in ('audio_detached_id','unlinked_from')):
                    raise ValueError('Include or resolve every referenced clip before moving it into the nest')
            elif key in ('clip_ids',) and isinstance(item,list):
                overlap = any(i in selected for i in item if isinstance(i,str))
                if overlap and local_scope == parent_sid and not inside:
                    raise ValueError('A reference outside the selection points to selected clips')
            elif key in ('track_id','source_track_id','target_track_id') and inside and isinstance(item,str) and local_scope == parent_sid:
                if item not in tracks: raise ValueError('A moved clip references a missing track')
                value[key]=tracks[item]
            elif key in ('sequence','source_sequence_id','target_sequence_id') and inside and item == parent_sid:
                value[key]=child_sid
            else: _references(item,selected,parent_sid,child_sid,tracks,inside=inside,scope=local_scope)


def plan(project, body, *, proc_holder=None):
    bounded(body,'Nest request',128*1024)
    if not isinstance(body,dict): raise ValueError('Nest request must be an object')
    context = body.get('_context')
    if not isinstance(context,dict) or any(not isinstance(context.get(k),str) or not context[k] for k in ('workspace','project','revision')):
        raise ValueError('Nest requires the saved project context')
    sid=identity(body.get('sequence'),'Sequence'); ids=body.get('clip_ids')
    if not isinstance(ids,list) or not 1 <= len(ids) <= MAX_SELECTED or any(not isinstance(x,str) or not x for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('Choose 1–1000 unique clip IDs in order')
    lookup,all_clips,spans,reserved,depths=_library(project)
    if sid not in lookup: raise ValueError('The selected sequence no longer exists')
    seq=lookup[sid]; clips=all_clips[sid]; chosen=set(ids)
    if any(cid not in clips for cid in ids): raise ValueError('A selected clip no longer belongs to the captured sequence')
    name=body.get('name',f"Nested {seq.get('name') or 'sequence'}")
    if not isinstance(name,str) or not name.strip() or len(name)>120 or any(ord(x)<32 for x in name):
        raise ValueError('Nested sequence name must be 1–120 characters without control characters')
    name=name.strip(); settings={'name':name}
    seed=digest({'project':project,'context':context,'sequence':sid,'clip_ids':ids,'settings':settings}); counter=0
    def fresh(prefix):
        nonlocal counter
        while True:
            counter+=1; value=prefix+'_'+digest([seed,counter])[:20]
            if value not in reserved: reserved.add(value); return value
    child_id=fresh('seq'); group=fresh('group'); track_map={t['id']:fresh('track') for t in seq['tracks']}
    minimum=min(spans[(sid,cid)][0] for cid in ids)
    clock=frame_rate(seq['fps']) if any(clips[cid][0]['kind']=='video' for cid in ids) else Fraction(48000)
    # Keep picture phase on the parent's exact frame clock. A short transparent
    # lead-in preserves off-grid authored placements instead of snapping them.
    nearest=round(minimum*float(clock)); boundary=float(Fraction(nearest,1)/clock)
    index=nearest if abs(minimum-boundary)<=max(1e-12,8*math.ulp(minimum)) else math.floor(Fraction(str(minimum))*clock)
    start=float(Fraction(index,1)/clock); end=max(spans[(sid,cid)][1] for cid in ids); duration=end-start
    issues=[]; warnings=[]
    def issue(code,message,**extra):
        if len(issues)<100: issues.append({'code':code,'severity':'error','message':message,**extra})
    if seq.get('multicam'): issue('multicam_source','Open or flatten the intended camera edit before nesting a multicam source sequence')
    for cid in ids:
        track,c=clips[cid]
        if track.get('locked'): issue('locked_track','Unlock every selected track before nesting',track=track['id'],clip_id=cid)
        media=project['media'].get(c.get('media_id'))
        if media and not media.get('is_image') and not media.get('synthetic'):
            limit=number(media.get('duration'),'Source duration')
            if c['in_']>=limit if c.get('hold') else c['out']>limit+1e-9:
                issue('source_range','Selected clip exceeds its source range; repair it before nesting',clip_id=cid)
        if c.get('sequence_id') and ((c.get('hold') and c['in_']>=seq_total(lookup[c['sequence_id']])) or (not c.get('hold') and c['out']>seq_total(lookup[c['sequence_id']])+1e-9)):
            issue('nested_range','Selected clip exceeds its nested source duration',clip_id=cid)
    # A group/detached pair cannot be split across the new editing boundary.
    groups={c.get('group') for _,c in clips.values() if c['id'] in chosen and c.get('group')}
    for other_sid,local in all_clips.items():
        for cid,(track,c) in local.items():
            selected=other_sid==sid and cid in chosen
            if c.get('group') in groups and not selected:
                issue('group_boundary','Select every member of the linked group before nesting',clip_id=cid,sequence=other_sid)
            if other_sid==sid and bool(cid in chosen)!=bool(c.get('audio_detached_id') in chosen) and c.get('audio_detached_id'):
                issue('detached_boundary','Select the video and its detached audio together',clip_id=cid)
            if other_sid==sid and bool(cid in chosen)!=bool(c.get('unlinked_from') in chosen) and c.get('unlinked_from'):
                issue('detached_boundary','Select the video and its detached audio together',clip_id=cid)
    effective={cid:_effective_span(project,seq,c,spans[(sid,cid)]) for cid,(_,c) in clips.items()}
    # Incoming overlap must remain inside the child, with its original neighbor.
    for cid in ids:
        if effective[cid][0]<start-1e-9:
            issue('transition_boundary','Include the preceding transition material so its complete incoming handle remains inside the nest',clip_id=cid)
    visible=[(t,c) for t,c in clips.values() if t['kind']=='video' and not t.get('muted') and not t.get('_mc_picture_hidden') and c.get('enabled') is not False]
    selected_picture=[(t,c) for t,c in visible if c['id'] in chosen]
    others=[(t,c) for t,c in visible if c['id'] not in chosen]
    if len(selected_picture)*max(1,len(others))>MAX_PAIRS: raise ValueError('Nest picture dependency comparison exceeds one million pairs; select a smaller sequence')
    video_selected=[(t,c) for t,c in clips.values() if c['id'] in chosen and t['kind']=='video']
    candidates=list({t['id']:t for t,c in selected_picture or video_selected}.values())
    candidates.sort(key=lambda t:(t['index'],t['id']))
    lower,upper=-math.inf,math.inf
    same_track_overlap=False
    for track,c in selected_picture:
        for other,d in others:
            if not _overlap(effective[c['id']],effective[d['id']]):continue
            if track['id']==other['id']:same_track_overlap=True
            elif track['index']<other['index']:upper=min(upper,other['index'])
            else:lower=max(lower,other['index'])
    def placement_valid(target):
        return not same_track_overlap and lower<target['index']<upper and not (selected_picture and (target.get('muted') or target.get('_mc_picture_hidden')))
    target=next((t for t in candidates if placement_valid(t)),candidates[0] if candidates else None)
    if target and not placement_valid(target): issue('picture_order','Unselected picture layers cross the proposed nest. Include those layers or choose a selection with the same compositing order')
    dependency_work=len(selected_picture)*max(1,len(others))
    for track,c in visible:
        selected=c['id'] in chosen; matte=track_matte_params(c)
        if matte:
            source=next((t for t in seq['tracks'] if t['id']==matte['track'] and t['kind']=='video'),None)
            if not source: issue('missing_matte','Repair the missing track matte before nesting',clip_id=c['id']); continue
            dependency_work+=len(source['clips'])
            if dependency_work>MAX_PAIRS:raise ValueError('Nest dependency comparison exceeds one million pairs')
            contributors=[d for d in source['clips'] if d.get('enabled') is not False and _overlap(effective[c['id']],effective[d['id']])]
            if any((d['id'] in chosen)!=selected for d in contributors):
                issue('matte_boundary','Include the complete track matte dependency before nesting',clip_id=c['id'],track=source['id'])
        if selected and (c.get('adjustment') or c.get('blend') not in (None,'normal')):
            if any(t['index']<=track['index'] and _overlap(effective[c['id']],effective[d['id']]) for t,d in others):
                issue('compositing_boundary','The selected adjustment/blend depends on unselected lower picture layers; include them before nesting',clip_id=c['id'])
    # Preserve mixer processing once. Nonlinear/stateful buses require all input
    # contributors, even earlier/later ones, because their state crosses gaps.
    audio_memo={}
    possible={cid:_audible_source(project,c,lookup,memo=audio_memo) for cid,(_,c) in clips.items()}
    audible={cid:route(seq,t,c) for cid,(t,c) in clips.items() if possible[cid]}
    buses={bus['id']:bus for cid,bus in audible.items() if cid in chosen and bus is not None}
    for tid,bus in buses.items():
        if len(audio_fx_chain(bus.get('audio_fx')))>1:
            peers=[cid for cid,b in audible.items() if b is not None and b['id']==tid and cid not in chosen]
            if peers: issue('audio_bus_boundary','Select every audible contributor to this processed audio bus, including earlier/later clips, before nesting',track=tid,clip_ids=peers[:50])
        if bus.get('locked'): issue('locked_audio_bus','Unlock the routed audio bus before moving its sound into a nest',track=tid)
    has_sound=any(possible[cid] for cid in chosen)
    if has_sound and len(seq['tracks'])>=MAX_TRACKS:issue('track_limit','Leave room for the dedicated nested sound track (at most 256 tracks)')
    # All tracks (including empty routing/solo tracks) are retained in the child.
    child_duration=duration
    if video_selected:
        exact=Fraction(str(duration))*frame_rate(seq['fps']);nearest=round(float(exact))
        count=nearest if abs(float(exact)-nearest)<1e-9 else math.ceil(exact)
        child_duration=float(Fraction(count,1)/frame_rate(seq['fps']))
    child={'id':child_id,'name':name,'width':seq['width'],'height':seq['height'],'fps':copy.deepcopy(seq['fps']),
           'timecode_format':seq.get('timecode_format','ndf'),'duration':child_duration,'markers':[],'captions':[],'master':{'gain_db':0},'tracks':[]}
    for track in seq['tracks']:
        value=copy.deepcopy({k:v for k,v in track.items() if k!='clips'}); value['id']=track_map[track['id']]; value['clips']=[]
        for c in track['clips']:
            if c['id'] not in chosen: continue
            copied=copy.deepcopy(c); copied['start']=max(0,c['start']-start)
            try:_references(copied,chosen,sid,child_id,track_map,inside=True,scope=sid)
            except ValueError as error:issue('reference_boundary',str(error),clip_id=c['id'])
            for fx in copied.get('fx_stack') or []:
                if fx.get('type')=='track_matte':
                    params=fx.get('params') or {}; old=params.get('track','V2')
                    if old in track_map: fx['params']={**params,'track':track_map[old]}
            value['clips'].append(copied)
        child['tracks'].append(value)
    parent=copy.deepcopy(seq)
    for track in parent['tracks']:track['clips']=[c for c in track['clips'] if c['id'] not in chosen]
    try:_references(parent,chosen,sid,child_id,track_map,scope=sid)
    except ValueError as error:issue('reference_boundary',str(error))
    for other_sid,other_seq in lookup.items():
        if other_sid==sid:continue
        try:_references(other_seq,chosen,sid,child_id,track_map,scope=other_sid)
        except ValueError as error:issue('reference_boundary',str(error),sequence=other_sid)
    wrappers=[]; added=[]
    common={'media_id':None,'sequence_id':child_id,'start':start,'in_':0,'out':duration,'speed':1,'group':group,'keyframes':{}}
    picture_ranges=[]
    if target:
        # Separate picture wrappers leave unselected gap clips untouched even
        # when a later ordinary save runs generic overlap normalization.
        rate=frame_rate(seq['fps'])
        for _,c in video_selected:
            a,b=effective[c['id']]
            frame=math.floor(Fraction(str(a))*rate)
            nearest=round(a*float(rate))
            if abs(a-float(Fraction(nearest,1)/rate))<=max(1e-12,math.ulp(a)*8):frame=nearest
            a=max(start,float(Fraction(frame,1)/rate))
            picture_ranges.append([a,b])
        merged=[]
        for a,b in sorted(picture_ranges):
            if merged and a<=merged[-1][1]+1e-10:merged[-1][1]=max(merged[-1][1],b)
            else:merged.append([a,b])
        picture_ranges=merged
        destination_track=next(t for t in parent['tracks'] if t['id']==target['id'])
        existing_peers=list(destination_track['clips'])
        dependency_work+=len(existing_peers)*len(picture_ranges)
        if dependency_work>MAX_PAIRS:raise ValueError('Nest dependency comparison exceeds one million pairs')
        for a,b in picture_ranges:
            for peer in existing_peers:
                if _overlap((a,b),spans[(sid,peer['id'])]):
                    issue('wrapper_overlap','A picture wrapper would overlap unselected material on its destination track; include it or use a different selection',clip_id=peer['id'])
            picture={**copy.deepcopy(common),'id':fresh('clip'),'name':name,'start':a,'in_':a-start,'out':b-start,
                'transform':{'x':0,'y':0,'scale':1,'rotation':0,'opacity':1},'audio':{'gain_db':0,'linked':False},'color':{}}
            destination_track['clips'].append(picture);wrappers.append(picture['id'])
    if has_sound:
        track={'id':fresh('track'),'name':name+' — Sound','kind':'audio','index':max([t['index'] for t in seq['tracks']]+[-1])+1,
               'muted':False,'locked':False,'solo':any(t.get('solo') for t in seq['tracks']),'gain_db':0,'audio_fx':{},'clips':[]}
        sound={**copy.deepcopy(common),'id':fresh('clip'),'name':name+' — Sound','audio':{'gain_db':0,'linked':True}}
        track['clips'].append(sound);parent['tracks'].append(track);wrappers.append(sound['id']);added.append({k:track[k] for k in ('id','name','kind','index','solo')})
        for cid,(tr,c) in clips.items():
            if cid in chosen or not possible[cid]:continue
            before=route(seq,tr,c);after=route(parent,tr,c)
            if (before is None)!=(after is None) or (before and after and (audio_fx_chain(before.get('audio_fx'))!=audio_fx_chain(after.get('audio_fx')) or (before.get('gain_db') or 0)!=(after.get('gain_db') or 0))):
                issue('routing_change','Adding the nested sound bus would change an unselected clip’s audible processing; add an explicit audio bus or include that clip first',clip_id=cid)
            elif before and after and before['id']!=after['id']:
                warnings.append('Previously self-routed unselected video audio will use the new neutral fallback bus; its current gain/effects and audible state are unchanged.')
    if not wrappers: raise ValueError('Nest needs at least one picture or audio source')
    if sum(len(v) for v in all_clips.values())+len(wrappers)>MAX_CLIPS:issue('clip_limit','Nested wrappers would exceed the 10000-clip project limit')
    for cid in chosen:
        old=audible.get(cid); child_track=next(t for t in child['tracks'] if t['id']==track_map[clips[cid][0]['id']]); child_clip=next(c for c in child_track['clips'] if c['id']==cid)
        new=route(child,child_track,child_clip) if possible[cid] else None
        if (old is None)!=(new is None): issue('child_routing','The captured solo/mute routing cannot be preserved in this child',clip_id=cid)
    if minimum>start+1e-12:warnings.append(f'The child includes {minimum-start:.12g} seconds of leading transparency/silence to preserve the parent clock.')
    if child_duration>duration+1e-12:warnings.append(f'The child render canvas includes {child_duration-duration:.12g} seconds of tail padding to retain the last partial picture frame; parent wrapper endpoints are unchanged.')
    warnings.extend(['Nested picture transparency and gaps require Full color output; Legacy output can cover underlying material with black.',
        'The child keeps clip processing and original track buses; its master is neutral. The parent master processes the result once.',
        'Existing sequence captions and markers remain on the parent; clip markers and complete source/automation payloads move with their clips.'])
    if abs(start*48000-round(start*48000))>1e-7: warnings.append('Fractional-sample nesting origins may round audio placement by one 48 kHz sample; review synchronization for this origin.')
    if any((c.get('keyframes') or {}).get('audio.gain_db') for _,c in clips.values() if c['id'] in chosen):warnings.append('Audio gain automation uses the renderer’s existing 128-sample control grid; nesting can shift that evaluation grid.')
    processing=['Original selected track gain and audio effects retained in child','Neutral child master','Dedicated neutral parent sound bus' if has_sound else 'No audio bus added','Original parent master retained once']
    summary={'kind':'sequence_nesting','sequence':sid,'child_sequence':child_id,'name':name,'range':{'start':start,'end':end,'duration':duration,'selected_start':minimum},
        'child_duration':child_duration,'lead_in':minimum-start,'tail_padding':child_duration-duration,'selected_count':len(ids),'source_tracks':[{'id':t['id'],'kind':t['kind'],'index':t['index'],'name':t.get('name',t['id']),'selected_clip_ids':[c['id'] for c in t['clips'] if c['id'] in chosen]} for t in seq['tracks'] if any(c['id'] in chosen for c in t['clips'])],
        'wrapper_clip_ids':wrappers,'picture_ranges':picture_ranges,'added_tracks':added,'processing':processing,'warnings':list(dict.fromkeys(warnings)),
        'affected_fields':['selected clips moved to nested sequence','picture wrapper','dedicated sound wrapper and routing bus'] if has_sound else ['selected clips moved to nested sequence','picture wrapper'],
        'message':f'Nest {len(ids)} clip(s) into {name}; keep their track processing and {len(wrappers)} linked wrapper(s).'}
    si=next(i for i,s in enumerate(project['sequences']) if s['id']==sid)
    ops=[] if issues else [{'op':'set','path':f'/sequences/{si}','value':parent},{'op':'insert','path':'/sequences/'+str(len(project['sequences'])),'value':child}]
    if ops:
        edges={key:{c['sequence_id'] for _,c in local.values() if c.get('sequence_id') and not (key==sid and c['id'] in chosen)} for key,local in all_clips.items()}
        edges[sid].add(child_id);edges[child_id]={clips[cid][1]['sequence_id'] for cid in chosen if clips[cid][1].get('sequence_id')}
        memo={}
        def longest(key):
            if key not in memo:memo[key]=max([1+longest(other) for other in edges[key]]+[0])
            return memo[key]
        if max(longest(key) for key in edges)>4:
            issue('nested_depth','Nesting would exceed four nested sequence levels');ops=[]
    bounded(ops,'Nest planned operations'); result={'ok':not issues,'kind':'sequence_nesting','sequence':sid,'clip_ids':copy.deepcopy(ids),'settings':settings,'issues':issues,'summary':summary,'ops':ops}
    result['fingerprint']=digest({'seed':seed,'result':result})
    if proc_holder and proc_holder.get('cancelled'): raise ValueError('Nest review cancelled')
    return result
