"""Editable, additive attenuation from selected audible dialogue clip spans.

This is clip-span automation, not speech detection or a live sidechain. Manual
gain curves remain separate. The caller owns saved-project/history publication.
"""
from bisect import bisect_left, bisect_right
import copy
import hashlib
import json
import math
from audio_contract import route

KEY = 'audio.duck_db'
MAX_POINTS = 8192


def number(value, label, lo, hi):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f'{label} must be between {lo:g} and {hi:g}')
    return float(value)


def curve(points):
    if not isinstance(points, list) or len(points) > MAX_POINTS:
        raise ValueError('Ducking has too many points or an invalid curve')
    last = -1.0
    for point in points:
        if not isinstance(point, dict): raise ValueError('Ducking points must be objects')
        t = number(point.get('t'), 'Ducking time', 0, 1e12)
        number(point.get('v'), 'Ducking level', -60, 0)
        if t <= last or point.get('e', 'linear') not in ('linear', 'hold'):
            raise ValueError('Ducking points must have increasing times and linear or hold interpolation')
        last = t
    return points


def expression(points):
    """Balanced conditionals avoid recursion proportional to the number of cuts."""
    points = curve(points)
    if not points: return '0'
    def branch(lo, hi):
        if hi-lo == 1:
            p = points[lo]
            if lo == len(points)-1 or p.get('e') == 'hold': return f"{p['v']:.12g}"
            q = points[lo+1]
            return f"({p['v']:.12g}+({q['v']-p['v']:.12g})*(t-{p['t']:.12g})/{q['t']-p['t']:.12g})"
        mid=(lo+hi)//2
        return f"if(lt(t,{points[mid]['t']:.12g}),{branch(lo,mid)},{branch(mid,hi)})"
    return f"if(lt(t,{points[0]['t']:.12g}),{points[0]['v']:.12g},{branch(0,len(points))})"


def filters(clip):
    points=(clip.get('keyframes') or {}).get(KEY)
    if points is None: return []
    curve(points)
    if not points: return []
    # The volume filter evaluates at audio-frame boundaries. A bounded 128-
    # sample block limits duck scheduling error to 2.667 ms at the 48 kHz mix.
    return ['asetnsamples=n=128:p=0', f"volume='pow(10,({expression(points)})/20)':eval=frame"]


def envelope(spans, amount, attack, release, hold):
    merged=[]
    for start,end in sorted(spans):
        end += hold
        if merged and start <= merged[-1][1]: merged[-1][1]=max(end,merged[-1][1])
        else: merged.append([start,end])
    points=[]
    for i,(start,end) in enumerate(merged):
        if not i: points.append((start-attack,0.0))
        else:
            previous=merged[i-1][1];gap=start-previous
            if gap >= attack+release:
                points.extend([(previous+release,0.0),(start-attack,0.0)])
            else:
                # The lower of the release and next attack ramps. No rise to
                # unity or stacked reduction where dialogue overlaps.
                points.append((previous+gap*release/(attack+release), amount*(1-gap/(attack+release))))
        points.extend([(start,amount),(end,amount)])
    if merged: points.append((merged[-1][1]+release,0.0))
    unique=[]
    for t,v in points:
        if unique and t == unique[-1][0]: unique[-1]=(t,v)
        else: unique.append((t,v))
    return unique,merged


def value(points, times, t):
    if not points: return 0.0
    i=bisect_right(times,t)-1
    if i<0:return points[0][1]
    if i>=len(points)-1:return points[-1][1]
    a,b=points[i],points[i+1]
    return a[1]+(b[1]-a[1])*(t-a[0])/(b[0]-a[0])


def clip_curve(points, times, start, duration):
    end=start+duration
    sliced=[(start,value(points,times,start)),*points[bisect_right(times,start):bisect_left(times,end)],(end,value(points,times,end))]
    if all(v==0 for _,v in sliced):return []
    result=[]
    for t,v in sliced:
        p={'t':max(0.0,t-start),'v':max(-60.0,min(0.0,v))}
        # Retire redundant collinear points without changing the curve.
        while len(result)>=2:
            a,b=result[-2:]
            if abs((b['v']-a['v'])*(p['t']-b['t'])-(p['v']-b['v'])*(b['t']-a['t']))>1e-10:break
            result.pop()
        result.append(p)
    return curve(result)


def has_audio(project, clip, visiting=()):
    sid=clip.get('sequence_id')
    if sid:
        if sid in visiting:raise ValueError('Nested audio contains a cycle')
        if len(visiting) >= 4:raise ValueError('Nested audio exceeds four levels')
        sub=next((s for s in project['sequences'] if s['id']==sid),None)
        if sub is None:raise ValueError('A nested audio sequence is missing')
        if sub.get('multicam'):
            from multicam import view
            sub=view(sub,clip.get('multicam_angle',0))
        return any(route(sub,t,c) is not None and has_audio(project,c,(*visiting,sid)) for t in sub['tracks'] for c in t['clips'])
    mid=clip.get('media_id')
    if not mid:return False
    if mid not in project['media']:raise ValueError('A source media entry is missing')
    return bool(project['media'][mid].get('has_audio'))


def span(clip):
    from render import clip_dur
    start=number(clip.get('start'), 'Clip start', 0, 1e12)
    number(clip.get('in_'), 'Clip in', 0, 1e12)
    number(clip.get('out'), 'Clip out', 0, 1e12)
    number(clip.get('speed',1), 'Clip speed', .000001, 1e6)
    duration=number(clip_dur(clip), 'Clip duration', .000000001, 1e12)
    return start,start+duration


def build(project, body, check=lambda: None):
    check()
    seq=next((s for s in project['sequences'] if s['id']==body.get('sequence')),None)
    if seq is None:raise ValueError('Choose an existing sequence')
    mode=body.get('mode','apply')
    if mode not in ('apply','remove'):raise ValueError('Choose Apply or Remove ducking')
    tracks={t['id']:t for t in seq['tracks']}
    def selection(key, fallback):
        ids=body.get(key,fallback)
        if not isinstance(ids,list) or not ids or any(not isinstance(i,str) or i not in tracks for i in ids) or len(set(ids))!=len(ids):
            raise ValueError('Choose existing distinct '+key.replace('_',' '))
        return ids
    defaults=[t['id'] for t in seq['tracks'] if
              (t['kind']=='video' and any(c.get('audio_tag')!='music' and (c.get('audio') or {}).get('linked') is not False for c in t['clips']))
              or any(c.get('audio_tag')=='dialogue' or 'dialogue' in (c.get('tags') or []) for c in t['clips'])]
    music=selection('music_tracks',[body['music_track']] if body.get('music_track') else [t['id'] for t in seq['tracks'] if t['kind']=='audio' and t['id'] not in defaults and t['clips']])
    if any(tracks[i]['kind']!='audio' or tracks[i].get('locked') for i in music):raise ValueError('Choose unlocked audio tracks for the music')
    dialogue=selection('dialogue_tracks',[i for i in defaults if i not in music]) if mode=='apply' else []
    if set(music)&set(dialogue):raise ValueError('Music and dialogue tracks must be different')
    amount=number(body.get('amount',-12), 'Reduction in dB', -60, -.1) if mode=='apply' else 0
    attack=number(body.get('attack',body.get('fade',.15)), 'Attack in seconds', .001, 10) if mode=='apply' else .15
    release=number(body.get('release',body.get('fade',.5)), 'Release in seconds', .001, 10) if mode=='apply' else .5
    hold=number(body.get('hold',.1), 'Hold in seconds', 0, 10) if mode=='apply' else 0
    spans=[];source_count=0
    for identity in dialogue:
        track=tracks[identity]
        for clip in track['clips']:
            check()
            if route(seq,track,clip) is None or not has_audio(project,clip):continue
            spans.append(span(clip));source_count+=1
    if mode=='apply' and not spans:raise ValueError('The selected dialogue tracks have no audible clip spans')
    points,merged=envelope(spans,amount,attack,release,hold);times=[t for t,_ in points]
    after=copy.deepcopy(project);target=next(s for s in after['sequences'] if s['id']==seq['id'])
    changed=0;affected=[];skipped=0;total_points=0
    for track in target['tracks']:
        if track['id'] not in music:continue
        for clip in track['clips']:
            check();keys=clip.get('keyframes') or {};old=keys.get(KEY)
            if mode=='remove':new=[]
            else:
                if route(target,track,clip) is None or not has_audio(after,clip):skipped+=1;continue
                start,end=span(clip);new=clip_curve(points,times,start,end-start)
            total_points+=len(new)
            if total_points>65536:raise ValueError('Ducking exceeds the 65,536-point sequence limit')
            if KEY not in keys and not new:continue
            if mode=='apply' and old==new:continue
            clip['keyframes']=copy.deepcopy(keys)
            if new:clip['keyframes'][KEY]=new
            else:clip['keyframes'].pop(KEY,None)
            changed+=1;affected.append({'track':track['id'],'clip':clip['id'],'points':len(new)})
    summary={'sequence':seq['id'],'ducked':changed,'dialogue_spans':len(merged),'dialogue_clips':source_count,'skipped':skipped,
             'music_tracks':music,'dialogue_tracks':dialogue,'points':total_points,'mode':mode,
             'settings':{'amount':amount,'attack':attack,'release':release,'hold':hold},'affected':affected,
             'message':f"{'Remove' if mode=='remove' else 'Apply'} ducking on {changed} clip(s); manual volume automation is preserved."}
    material=[project,summary]
    summary['plan']=hashlib.sha256(json.dumps(material,sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    check()
    return after,summary
