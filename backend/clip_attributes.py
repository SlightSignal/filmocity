"""Selective attribute transfer from a frozen clipboard donor, without timeline edits.

plan is pure; inspect adds owned filesystem identities to the reviewed digest.
Curve clocks refer to the donor's visible clip time, including offscreen anchors.
"""
import copy
import math
import os
import re
import stat

from audio_contract import fade_window
from audio_ducking import curve as duck_curve
from effects import VIDEO_FX, AUDIO_FX
from matte_tracks import track_dependencies
from overlap_normalization import _clock
from source_relink import bounded, digest
from timeline_time import frame_rate

GROUPS = ('motion', 'color', 'video_effects', 'audio_effects', 'mask', 'audio_gain', 'audio_controls', 'audio_fades', 'picture_transitions')
DEFAULT_GROUPS = ('motion', 'color', 'video_effects', 'audio_effects', 'audio_gain', 'audio_fades')
MEDIA_FIELDS = ('id','path','subclip_of','sub_in','duration','frame_rate','fps','interpret_fps','ingest_token','source_relink_basis','stab_trf','sequence_frames','input_opts')
FIELDS = {'motion': ('transform','fit','blur_fill_sigma'), 'color': ('color',),
          'video_effects': ('effects','fx_stack','blend'), 'audio_effects': ('audio_fx','afx_stack'),
          'mask': ('mask',), 'audio_gain': (), 'audio_controls': (),
          'audio_fades': ('audio_transition_in','audio_transition_out'),
          'picture_transitions': ('transition_in','transition_out')}
AUDIO_FIELDS = {'audio_gain': ('gain_db',), 'audio_controls': ('pan','channels','maintain_pitch'),
                'audio_fades': ('fade_in','fade_out','constant_power','fade_window')}
MAX_REQUEST = 2 * 1024 * 1024
MAX_OUTPUT = 32 * 1024 * 1024
MAX_POINTS = 8192
MAX_TOTAL_POINTS = 100000
TRANSITIONS = {'dissolve','fade','dip_black','dip_white','wipe_left','wipe_right','wipe_up','wipe_down','iris','iris_close','diagonal_tl','diagonal_tr','diagonal_bl','diagonal_br','barn_h','barn_v','clock','checker','cross_zoom','glitch','push_left','push_right','slide_left','slide_right','slide_up','slide_down'}


def number(v, label, lo=-1e12, hi=1e12):
    if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not lo <= v <= hi:
        raise ValueError(f'{label} must be a finite number between {lo:g} and {hi:g}')
    return v


def _object(v, label):
    if v is None: return {}
    if not isinstance(v,dict): raise ValueError(label+' must be an object')
    return v


def _selected(key, groups):
    prefixes = {'motion': ('transform.',), 'color': ('color.',), 'video_effects': ('effects.','fx_stack.'),
                'audio_effects': ('audio_fx.','afx_stack.'), 'mask': ('mask.',),
                'audio_gain': ('audio.gain_db','audio.duck_db'), 'audio_controls': ('audio.pan','audio.channels','audio.maintain_pitch'),
                'audio_fades': ('audio.fade_in','audio.fade_out','audio.constant_power','audio_transition_'),
                'picture_transitions': ('transition_in.','transition_out.')}
    return any(key.startswith(p) if p.endswith(('.', '_')) else key == p for g in groups for p in prefixes[g])


def _curve(key, points):
    if not isinstance(points,list) or len(points)>MAX_POINTS: raise ValueError('Attribute curves require at most 8192 points each')
    previous = -math.inf
    for p in points:
        if not isinstance(p,dict): raise ValueError('Attribute curve points must be objects')
        t=number(p.get('t'),'Curve time'); number(p.get('v'),'Curve value')
        if t<=previous: raise ValueError('Attribute curve times must be strictly increasing')
        previous=t
        if p.get('e','linear') not in ('linear','hold','ease','ease_in','ease_out','bezier'): raise ValueError('Unsupported attribute curve interpolation')
        for handle in ('i','o','cp1','cp2'):
            if handle in p:
                h=p[handle]
                if not isinstance(h,list) or len(h)!=2: raise ValueError('Bezier handles must be two finite numbers')
                number(h[0],'Bezier handle x',0,1);number(h[1],'Bezier handle y')
        if key=='audio.gain_db':number(p['v'],'Manual gain automation',-40,24)
    if key=='audio.duck_db':duck_curve(points)


def _stacks(clip,key,registry):
    value=clip.get(key) or []
    if not isinstance(value,list) or len(value)>128:raise ValueError(key+' requires at most 128 effect entries')
    for fx in value:
        if not isinstance(fx,dict) or fx.get('type') not in registry:raise ValueError('Unregistered '+key+' effect cannot be transferred; register or render it first')
        if 'enabled' in fx and type(fx['enabled']) is not bool:raise ValueError('Effect enabled must be boolean')
        params=_object(fx.get('params'),'Effect parameters')
        spec=registry[fx['type']]['params']
        for key,v in params.items():
            if key not in spec:raise ValueError('Unknown effect parameter: '+key)
            item=spec[key];kind=item[4] if len(item)>4 else 'number'
            if kind=='bool':
                if v not in (0,1,False,True):raise ValueError('Effect toggle must be boolean or zero/one')
            elif item[1] is not None:number(v,'Effect '+key,item[1],item[2])
            elif kind.startswith('select:'):
                if v not in kind[7:].split(','):raise ValueError('Unsupported effect '+key)
            elif kind=='color':
                if not isinstance(v,str) or not re.fullmatch(r'(?:#|0x)?[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?',v):raise ValueError('Effect color must be hexadecimal')
            elif not isinstance(v,str) or len(v)>512:raise ValueError('Effect text is invalid or too long')


def _numeric_object(value, label, fields):
    value=_object(value,label)
    for key,v in value.items():
        if key not in fields:raise ValueError('Unsupported '+label+' field '+key)
        lo,hi=fields[key];number(v,label+' '+key,lo,hi)


def _legacy(clip,groups):
    if 'color' in groups:
        value=_object(clip.get('color'),'Color')
        scalars={'exposure','contrast','saturation','temperature','tint','highlights','shadows','whites','blacks','vibrance'}
        for k,v in value.items():
            if k in scalars:number(v,'Color '+k,-100,100)
            elif k in ('curves','curves_r','curves_g','curves_b'):
                if not isinstance(v,list) or len(v)>256:raise ValueError('Color curves require at most 256 points')
                last=-1
                for point in v:
                    if not isinstance(point,list) or len(point)!=2:raise ValueError('Color points require two numbers')
                    x=number(point[0],'Color input',0,1);number(point[1],'Color output',0,1)
                    if x<=last:raise ValueError('Color curve inputs must increase')
                    last=x
            elif k=='wheels':
                for name,w in _object(v,'Color wheels').items():
                    if name not in ('shadows','midtones','highlights'):raise ValueError('Unsupported color wheel')
                    _numeric_object(w,'Color wheel',{'r':(-1,1),'g':(-1,1),'b':(-1,1)})
            elif k=='lut':
                if v is not None and (not isinstance(v,str) or len(v)>4096):raise ValueError('Invalid LUT path')
            else:raise ValueError('Unsupported color attribute '+k)
    if 'mask' in groups:
        value=_object(clip.get('mask'),'Mask')
        for k,v in value.items():
            if k=='type':
                if v not in ('rect','ellipse',None,''):raise ValueError('Unsupported mask shape')
            elif k=='invert':
                if type(v) is not bool:raise ValueError('Mask invert must be boolean')
            elif k in ('x','y','w','h'):number(v,'Mask '+k,-100 if k in ('x','y') else 0,100)
            elif k=='feather':number(v,'Mask feather',0,1000)
            else:raise ValueError('Unsupported mask attribute '+k)
    if 'video_effects' in groups:
        value=_object(clip.get('effects'),'Legacy effects')
        for k,v in value.items():
            if k=='crop':_numeric_object(v,'Crop',{x:(0,.95) for x in ('l','t','r','b')})
            elif k=='chromakey':
                for field,n in _object(v,'Chroma key').items():
                    if field=='enabled':
                        if type(n) is not bool:raise ValueError('Chroma key enabled must be boolean')
                    elif field=='color':
                        if not isinstance(n,str) or not re.fullmatch(r'(?:#|0x)?[0-9a-fA-F]{6}',n):raise ValueError('Chroma key color must be hexadecimal')
                    elif field in ('similarity','blend'):number(n,'Chroma key '+field,0,1)
                    else:raise ValueError('Unsupported chroma key parameter')
            elif k in ('blur','sharpen','vignette'):number(v,'Legacy '+k,0,200 if k=='blur' else 3 if k=='sharpen' else 1)
            else:raise ValueError('Unsupported legacy picture effect '+k)
    if 'audio_effects' in groups:
        value=_object(clip.get('audio_fx'),'Legacy audio effects')
        for k,v in value.items():
            if k=='eq':_numeric_object(v,'EQ',{x:(-40,40) for x in ('low_db','mid_db','high_db')})
            elif k in ('denoise','comp'):
                obj=_object(v,k);num={key:n for key,n in obj.items() if key!='enabled'}
                if 'enabled' in obj and type(obj['enabled']) is not bool:raise ValueError('Audio effect enabled must be boolean')
                spec={'db':(0,97)} if k=='denoise' else {'threshold_db':(-60,0),'ratio':(1,20),'attack_ms':(.01,2000),'release_ms':(.01,9000),'makeup_db':(0,36)}
                _numeric_object(num,k,spec)
            elif k=='limiter':
                if type(v) is not bool:raise ValueError('Limiter must be boolean')
            else:raise ValueError('Unsupported legacy sound effect '+k)


def _validate(clip,groups,animation):
    audio=_object(clip.get('audio'),'Clip audio')
    _legacy(clip,groups)
    for g in groups:
        for key in FIELDS[g]:
            if key in ('fit','blend','blur_fill_sigma','fx_stack','afx_stack'):continue
            _object(clip.get(key),key)
    if 'motion' in groups:
        tf=_object(clip.get('transform'),'Transform')
        for k,v in tf.items():
            if k not in ('x','y','scale','rotation','opacity','anchor_x','anchor_y'):raise ValueError('Unknown transform attribute '+k)
            number(v,'Transform '+k,0.0001 if k=='scale' else 0 if k=='opacity' else -1e7,100 if k=='scale' else 1 if k=='opacity' else 1e7)
        if clip.get('fit') not in (None,'contain','cover','blur_fill'):raise ValueError('Unsupported fit mode')
        if 'blur_fill_sigma' in clip:number(clip['blur_fill_sigma'],'Blur fill',0,200)
    if 'audio_gain' in groups and 'gain_db' in audio:number(audio['gain_db'],'Manual gain',-40,24)
    if 'audio_controls' in groups:
        if 'pan' in audio:number(audio['pan'],'Pan',-1,1)
        if audio.get('channels') not in (None,'stereo','left','right','mono','swap'):raise ValueError('Unsupported audio channel mapping')
        if 'maintain_pitch' in audio and type(audio['maintain_pitch']) is not bool:raise ValueError('Maintain pitch must be boolean')
    if 'audio_fades' in groups:
        for k in ('fade_in','fade_out'):
            if k in audio:number(audio[k],k,0,1e12)
        if 'constant_power' in audio and type(audio['constant_power']) is not bool:raise ValueError('Fade curve setting must be boolean')
        for k in ('audio_transition_in','audio_transition_out'):
            tr=_object(clip.get(k),k)
            if tr:
                if tr.get('type') not in ('constant_gain','constant_power','exponential'):raise ValueError('Unsupported audio transition')
                number(tr.get('duration',0),k+' duration',0,1e12)
        window=audio.get('fade_window')
        if window is not None:
            if not isinstance(window,dict):raise ValueError('Inherited fade clock must be an object')
            number(window.get('duration'),'Inherited fade duration',1e-12,1e12);number(window.get('offset'),'Inherited fade offset')
    if 'picture_transitions' in groups:
        for k in ('transition_in','transition_out'):
            tr=_object(clip.get(k),k)
            if tr:
                if tr.get('type','dissolve') not in TRANSITIONS:raise ValueError('Unsupported picture transition')
                if k.endswith('out') and tr.get('type','dissolve') not in ('dissolve','fade','dip_black','dip_white'):raise ValueError('That outgoing picture transition is not implemented by the renderer')
                number(tr.get('duration',0),k+' duration',0,1e12)
    if 'video_effects' in groups:
        _stacks(clip,'fx_stack',VIDEO_FX)
        if clip.get('blend') not in (None,'normal','multiply','screen','overlay','darken','lighten','difference','add','softlight','hardlight','exclusion','subtract'):raise ValueError('Unsupported blend mode')
    if 'audio_effects' in groups:_stacks(clip,'afx_stack',AUDIO_FX)
    kfs=_object(clip.get('keyframes'),'Keyframes')
    total=0
    if animation:
        for key,points in kfs.items():
            if _selected(key,groups):_curve(key,points);total+=len(points)
    return total


def _copy_field(target,source,key):
    if key in source:target[key]=copy.deepcopy(source[key])
    else:target.pop(key,None)


def _context_owner(value):
    return (value or {}).get('workspace'),(value or {}).get('project')


def _resources(project,body,donor,groups,targets,issues):
    paths=[];clip=donor['clip'];same_owner=_context_owner(donor['context'])==_context_owner(body.get('_context'))
    if 'color' in groups and (clip.get('color') or {}).get('lut'):
        lut=clip['color']['lut']
        if not isinstance(lut,str) or not lut or len(lut)>4096:raise ValueError('LUT path is invalid')
        if not same_owner:issues.append({'code':'resource_owner','message':'Import the LUT into this project before transferring a grade from another workspace/project.'})
        else:paths.append(lut)
    if 'video_effects' in groups:
        for fx in clip.get('fx_stack') or []:
            if fx.get('enabled') is False:continue
            if fx['type']=='track_matte' and (not same_owner or donor['sequence']['id']!=body.get('sequence')):issues.append({'code':'matte_dependency','message':'A live Track Matte can transfer only within its captured sequence and project; choose an explicit matte on the target sequence.'})
            if fx['type']=='timecode':
                base=os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
                font=os.path.join(base,'assets','fonts','DejaVuSansMono-Bold.ttf')
                paths.append(font)
            if fx['type']=='stabilize':
                captured=donor.get('media');current=(project.get('media') or {}).get(clip.get('media_id'))
                if not same_owner or not isinstance(captured,dict) or not isinstance(current,dict) or captured.get('subclip_of') or current.get('subclip_of') or current.get('synthetic') or current.get('sequence_frames') or current.get('input_opts') or any(captured.get(k)!=current.get(k) for k in MEDIA_FIELDS) or any(c.get('media_id')!=clip.get('media_id') for _,_,_,c in targets):
                    issues.append({'code':'source_analysis','message':'Stabilization can transfer only to the same unchanged physical source; analyze each target source instead.'})
                elif not current.get('stab_trf') or not current.get('path'):issues.append({'code':'source_analysis','message':'Stabilization analysis is missing; analyze the source first.'})
                else:paths.extend([current['path'],current['stab_trf']])
    return sorted(set(paths))


def plan(project,body,*,proc_holder=None):
    bounded(project,'Attribute project');bounded(body,'Attribute request',MAX_REQUEST)
    if not isinstance(body,dict):raise ValueError('Attribute request must be an object')
    donor=body.get('donor')
    if not isinstance(donor,dict) or type(donor.get('version')) is not int or donor.get('version')!=1:raise ValueError('Choose a version 1 frozen clipboard donor')
    source=donor.get('clip');fmt=donor.get('sequence');ctx=donor.get('context')
    if not isinstance(source,dict) or not isinstance(source.get('id'),str) or not source['id']:raise ValueError('Donor clip identity is required')
    if 'name' in source and source['name'] is not None and not isinstance(source['name'],str):raise ValueError('Donor clip name must be text')
    if not isinstance(fmt,dict) or not isinstance(fmt.get('id'),str) or not fmt['id']:raise ValueError('Donor sequence format is required')
    frame_rate(fmt.get('fps'))
    if not isinstance(body.get('_context'),dict):raise ValueError('Saved target context is required')
    for k in ('width','height'):number(fmt.get(k),'Donor '+k,1,16384)
    if not isinstance(ctx,dict) or any(not isinstance(ctx.get(k),str) or not ctx[k] for k in ('workspace','project','revision')):raise ValueError('Donor saved ownership is required')
    if donor.get('media') is not None and not isinstance(donor.get('media'),dict):raise ValueError('Donor source must be a captured descriptor or null')
    duration=_clock(source);number(duration,'Donor duration',1e-9,1e9)
    groups=body.get('groups',list(DEFAULT_GROUPS));animation=body.get('include_animation',True);timing=body.get('timing','seconds')
    if not isinstance(groups,list) or not groups or any(g not in GROUPS for g in groups) or len(groups)!=len(set(groups)):raise ValueError('Select unique supported attribute groups')
    if type(animation) is not bool or timing not in ('seconds','scale'):raise ValueError('Choose animation true/false and timing seconds/scale')
    ids=body.get('clip_ids');sid=body.get('sequence')
    if not isinstance(ids,list) or not 1<=len(ids)<=100 or any(not isinstance(i,str) or not i for i in ids) or len(set(ids))!=len(ids):raise ValueError('Choose 1–100 unique target clip IDs')
    seqs=project.get('sequences') or [];matches=[(i,s) for i,s in enumerate(seqs) if s.get('id')==sid]
    if len(matches)!=1:raise ValueError('Choose one existing target sequence')
    si,seq=matches[0];found={};track_ids=set();targets=[]
    for ti,tr in enumerate(seq.get('tracks') or []):
        if not isinstance(tr.get('id'),str) or not tr['id'] or tr.get('id') in track_ids:raise ValueError('Target track IDs must be nonempty and unique')
        track_ids.add(tr.get('id'))
        for ci,c in enumerate(tr.get('clips') or []):
            if not isinstance(c.get('id'),str) or not c['id'] or c.get('id') in found:raise ValueError('Target clip IDs must be nonempty and unique across the sequence')
            found[c.get('id')]=(ti,ci,tr,c)
    if any(i not in found for i in ids):raise ValueError('A selected target clip no longer exists')
    targets=[found[i] for i in ids];points=_validate(source,groups,animation)
    if points*len(ids)>MAX_TOTAL_POINTS:raise ValueError('Attribute transfer exceeds 100000 copied automation points')
    projected=sum(bounded(c,'Target clip') for _,_,_,c in targets)+bounded(source,'Donor clip')*len(targets)
    if projected>MAX_OUTPUT:raise ValueError('Attribute transfer exceeds 32 MiB of copied metadata')
    issues=[];warnings=[];ops=[];rows=[]
    matte= 'video_effects' in groups and any(fx.get('type')=='track_matte' and fx.get('enabled') is not False for fx in source.get('fx_stack') or [])
    if matte:
        names=[(fx.get('params') or {}).get('track','V2') for fx in source['fx_stack'] if fx.get('type')=='track_matte' and fx.get('enabled') is not False]
        named={tr['id']:tr.get('name') for tr in seq['tracks']}
        names=[f'{tid} ({named[tid]})' if named.get(tid) else tid for tid in names]
        warnings.append('Track Matte uses current live track '+', '.join(names)+' in this sequence; its content is not copied or frozen.')
        if any(tr.get('kind')!='video' for _,_,tr,_ in targets):issues.append({'code':'matte_target','message':'Track Matte requires a video-track target.'})
    paths=_resources(project,body,donor,groups,targets,issues)
    if 'video_effects' in groups and any(fx.get('type')=='stabilize' and fx.get('enabled') is not False for fx in source.get('fx_stack') or []):
        warnings.append('Legacy stabilization has no analysis/source receipt: pairing with the original analysis cannot be verified. Inspect the current original and analysis before use.')
    if fmt.get('width')!=seq.get('width') or fmt.get('height')!=seq.get('height'):
        warnings.append('Pixel-valued transforms and effect parameters retain their values across different sequence canvases; inspect the result at the target size.')
    for ti,ci,tr,c in targets:
        if proc_holder and proc_holder.get('cancelled'):raise ValueError('Attribute review cancelled')
        td=_clock(c);number(td,'Target duration',1e-9,1e9);ratio=td/duration if timing=='scale' else 1
        _object(c.get('audio'),'Target audio');_object(c.get('keyframes'),'Target keyframes')
        local=[];new=copy.deepcopy(c);fields=[];curves=[]
        if tr.get('locked'):issues.append({'code':'locked_track','clip_id':c['id'],'message':'Unlock every selected target track before pasting attributes.'})
        for g in groups:
            for k in FIELDS[g]:_copy_field(new,source,k)
            if g in AUDIO_FIELDS:
                au=copy.deepcopy(new.get('audio') or {});src=source.get('audio') or {}
                for k in AUDIO_FIELDS[g]:_copy_field(au,src,k)
                if au or 'audio' in c or any(k in src for k in AUDIO_FIELDS[g]):new['audio']=au
            if g=='audio_fades':
                au=new.setdefault('audio',{})
                for key in ('fade_in','fade_out'):
                    if key in au:au[key]*=ratio
                for key in ('audio_transition_in','audio_transition_out'):
                    if new.get(key):new[key]['duration']=new[key].get('duration',0)*ratio
                window=fade_window(source,duration)
                window={'duration':window['duration']*ratio,'offset':window['offset']*ratio,'settings':fade_window(new,td)['settings']}
                number(window['duration'],'Mapped fade duration',1e-12,1e12);number(window['offset'],'Mapped fade offset')
                for key in ('fade_in','fade_out'):
                    if key in au:number(au[key],'Mapped '+key,0,1e12)
                for key in ('audio_transition_in','audio_transition_out'):
                    if new.get(key):number(new[key]['duration'],'Mapped audio transition',0,1e12)
                # Preserve only a meaningful non-default envelope clock; an empty fade paste stays a true no-op.
                has_fades=any(window['settings'][i] for i in (0,1,4,6))
                if has_fades and ((source.get('audio') or {}).get('fade_window') is not None or abs(window['duration']-td)>1e-9 or window['offset']!=0):au['fade_window']=window
                else:au.pop('fade_window',None)
                if has_fades and (window['offset']<0 or window['offset']+td>window['duration']+1e-9):local.append('The target extends outside the inherited donor fade clock; its envelope can include silence.')
            if g=='picture_transitions':
                for key in ('transition_in','transition_out'):
                    if new.get(key):
                        value=new[key];value['duration']=value.get('duration',0)*ratio
                        if value['duration']>td+1e-9:issues.append({'code':'transition_duration','clip_id':c['id'],'message':'A copied picture transition exceeds the target duration; choose Scale timing or a shorter transition.'})
                        if key=='transition_in' and value['duration']>0:
                            align=value.get('align') or ('end' if value.get('type','dissolve') in ('dissolve','fade') else 'start')
                            if align not in ('start','center','end'):raise ValueError('Unsupported picture transition alignment')
                            if align!='start' and not c.get('hold') and (c.get('time_remap') or c.get('reverse')):
                                issues.append({'code':'transition_handles','clip_id':c['id'],'message':'An aligned incoming transition on reversed/ramped footage needs separate source-handle editing; use start alignment or the transition editor.'})
                            elif align!='start':local.append('The incoming transition uses available target source handles and can begin before the clip cut; inspect adjacent picture compositing.')
        old_kf=c.get('keyframes') or {};new_kf=copy.deepcopy(old_kf)
        if animation:
            for k in list(new_kf):
                if _selected(k,groups):new_kf.pop(k)
            for k,v in (source.get('keyframes') or {}).items():
                if _selected(k,groups):
                    new_kf[k]=[dict(copy.deepcopy(p),t=p['t']*ratio) for p in v]
                    _curve(k,new_kf[k])
            if new_kf!=old_kf:new['keyframes']=new_kf
            curves=sorted(k for k in set(old_kf)|set(new_kf) if old_kf.get(k)!=new_kf.get(k))
            if 'audio_gain' in groups:
                history=new.get('source_edit_window')
                if isinstance(history,dict) and 'duck' in history:history.pop('duck')
            if any(p['t']<0 or p['t']>td for k,v in new_kf.items() if _selected(k,groups) for p in v):local.append('Selected automation retains offscreen anchors outside this target duration.')
        elif any(_selected(k,groups) and v for k,v in old_kf.items()):local.append('Existing target automation is retained and may override pasted static values.')
        # An absent/default empty audio object is not itself a user-visible edit.
        if new.get('audio')=={} and 'audio' not in c:new.pop('audio',None)
        for k in set(new)|set(c):
            if new.get(k)!=c.get(k) or (k in new)!=(k in c):fields.append(k)
        changed=new!=c
        rows.append({'clip_id':c['id'],'track':tr['id'],'duration':td,'ratio':ratio,'changed':changed,'fields':sorted(fields),'curves':curves,'warnings':local})
        if changed:ops.append({'op':'set','path':f'/sequences/{si}/tracks/{ti}/clips/{ci}','value':new})
    if matte and not issues:
        candidate=copy.deepcopy(seq)
        for op in ops:
            address=op['path'].split('/');candidate['tracks'][int(address[4])]['clips'][int(address[6])]=copy.deepcopy(op['value'])
        # Validate dependencies even when the target is currently muted or disabled.
        for tr in candidate['tracks']:
            tr['muted']=False
            if any(c.get('id') in ids for c in tr.get('clips',[])):tr['_mc_picture_hidden']=False
            for c in tr.get('clips',[]):
                if c.get('id') in ids:c['enabled']=True
        _,_,matte_issues=track_dependencies(candidate)
        issues.extend(matte_issues)
    settings={'groups':copy.deepcopy(groups),'include_animation':animation,'timing':timing}
    changed=[r['clip_id'] for r in rows if r['changed']]
    warnings+=list(dict.fromkeys(w for r in rows for w in r['warnings']))
    summary={'kind':'clip_attributes','sequence':sid,'donor':{'clip_id':source['id'],'name':source.get('name') or (donor.get('media') or {}).get('name') or source['id'],'duration':duration},
             'selected_count':len(ids),'changed_count':len(changed),'changed_clip_ids':changed,'targets':rows,**settings,'affected_fields':sorted({f for r in rows for f in r['fields']}),'warnings':warnings,
             'message':f'Paste selected attributes to {len(changed)} of {len(ids)} clips.' if changed else 'Selected attributes already match; no project edit is needed.'}
    bounded(ops,'Attribute output',MAX_OUTPUT)
    issues=[{'severity':'error',**i} for i in issues]
    result={'ok':not issues,'kind':'clip_attributes','sequence':sid,'clip_ids':copy.deepcopy(ids),'donor':copy.deepcopy(donor),'settings':settings,'issues':issues,'summary':summary,'ops':ops if not issues else [],'resources':paths}
    result['fingerprint']=digest({'project':project,'context':body.get('_context'),'result':result})
    return result


def resource_stamps(paths):
    result=[]
    for name in paths:
        path=os.path.abspath(name);info=os.stat(path)
        if not stat.S_ISREG(info.st_mode):raise ValueError('Attribute resource must be a regular file: '+path)
        result.append([path,info.st_size,info.st_mtime_ns,info.st_ino])
    return result


def inspect(project,body,*,proc_holder=None):
    result=plan(project,body,proc_holder=proc_holder)
    if result['ok'] and 'video_effects' in result['settings']['groups'] and any(fx.get('type')=='stabilize' and fx.get('enabled') is not False for fx in body['donor']['clip'].get('fx_stack') or []):
        from source_relink_io import check_accepted
        from task_inputs import source_stamp
        media=project['media'][body['donor']['clip']['media_id']]
        check_accepted(media)
        previous=(media.get('proxy_info') or {}).get('source_signature')
        if previous and previous!=digest(source_stamp(media)):raise ValueError('The stabilization source changed on disk; Relink and analyze it again.')
    try:stamps=resource_stamps(result['resources'])
    except OSError as error:raise ValueError('An attribute resource is unavailable: '+str(error)) from error
    if resource_stamps(result['resources'])!=stamps:raise ValueError('An attribute resource changed during review')
    result['resource_stamps']=stamps
    result['fingerprint']=digest({'plan':result['fingerprint'],'resources':stamps})
    return result
