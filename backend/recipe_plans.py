"""Pure, bounded, deterministic plans for reviewed editing recipes.

Owned analysis, resource capture/publication and transactional Apply live in
recipe_workflow. No function here saves, decodes media or reads mutable assets.
"""
import bisect
import copy
import hashlib
import math
import re

from analysis_edits import _Budget, _RangeMap, _json, _slice, plan as analysis_plan
from audio_remix import design as music_design
from editing_workflow import transcript_basis, words_of
from media_analysis import number
from overlap_normalization import _clock, MAX_COPIED_BYTES, MAX_CLIPS
from timeline_time import frame_rate, from_frames, to_frames, interpretation_factor

MAX_SOURCES = 100
MAX_ANCHORS = 4096
MAX_WORDS = 100_000
MAX_SECONDS = 600


def _bool(body, key, default):
    value = body.get(key, default)
    if type(value) is not bool: raise ValueError(key+' must be true or false')
    return value


def _text(body, key, default='', maximum=500, required=False):
    value = body.get(key, default)
    if not isinstance(value, str) or len(value)>maximum or required and not value.strip():
        raise ValueError(f'{key} must contain '+('1–' if required else 'at most ')+f'{maximum} characters')
    return value.strip()


def _ids(value, name, minimum=0):
    if not isinstance(value,list) or not minimum<=len(value)<=MAX_SOURCES or any(not isinstance(v,str) or not v for v in value):
        raise ValueError(f'{name} needs {minimum}–{MAX_SOURCES} ordered source IDs')
    return list(value)  # Repeated sources are deliberate editorial choices.


def _enum(body,key,default,values):
    value=body.get(key,default)
    if value not in values: raise ValueError('Choose '+key+' from '+', '.join(values))
    return value


def _bounded(value, maximum=MAX_COPIED_BYTES):
    pending=[(value,0)];visited=0
    while pending:
        item,depth=pending.pop();visited+=1
        if depth>64 or visited+len(pending)>1_000_000:raise ValueError('Recipe data has excessive depth or nodes')
        if isinstance(item,dict):pending.extend((child,depth+1) for child in item.values())
        elif isinstance(item,list):pending.extend((child,depth+1) for child in item)
    if len(_json(value).encode('utf-8'))>maximum: raise ValueError('Recipe data exceeds its bounded size')
    return value


def _media(project, mid, stream):
    library=project.get('media') or {};media=library.get(mid)
    if not isinstance(media,dict) or media.get('id')!=mid or not media.get('has_'+stream): raise ValueError(f'Recipe source {mid!r} is missing or has no {stream}')
    parent=library.get(media.get('subclip_of')) if media.get('subclip_of') else media
    if not isinstance(parent,dict) or parent.get('subclip_of'): raise ValueError('Flatten missing or nested subclip parents before this recipe')
    duration=number(media.get('duration'),'Source duration',minimum=1/48000,maximum=864000)
    offset=number(media.get('sub_in',0) or 0,'Subclip offset',minimum=0,maximum=864000)
    interpretation_factor(media)
    if media.get('subclip_of'):
        if abs(interpretation_factor(media)-interpretation_factor(parent))>1e-9: raise ValueError('Subclip interpretation must match its parent')
        if offset+duration>number(parent.get('duration'),'Parent duration',minimum=0)+1e-9: raise ValueError('The subclip extends beyond its parent source')
    return media,parent


def capture(project, body, mode):
    """Validate user options and capture immutable planner dependencies."""
    if not isinstance(project,dict) or not isinstance(body,dict) or mode not in ('talking_head','reel'): raise ValueError('Choose a supported recipe')
    _bounded(body,1024*1024)
    found=[s for s in project.get('sequences',[]) if s.get('id')==body.get('sequence')]
    if len(found)!=1: raise ValueError('Choose one existing sequence')
    seq=found[0];fps=seq.get('fps');frame_rate(fps)
    width=number(seq.get('width'),'Sequence width',minimum=16,maximum=16384);height=number(seq.get('height'),'Sequence height',minimum=16,maximum=16384)
    if width!=int(width) or height!=int(height): raise ValueError('Sequence dimensions must be integers')
    settings={};media_ids=[];clip_id=None
    def source(mid,stream):
        media,parent=_media(project,mid,stream)
        for key in (mid,media.get('subclip_of')):
            if key and key not in media_ids:media_ids.append(key)
        return media
    if mode=='talking_head':
        clip_id=body.get('clip_id');matches=[(t,c) for t in seq.get('tracks',[]) for c in t.get('clips',[]) if c.get('id')==clip_id]
        if not isinstance(clip_id,str) or len(matches)!=1: raise ValueError('Choose exactly one talking-head clip')
        track,clip=matches[0]
        if track.get('locked') or track.get('kind') not in ('video','audio'): raise ValueError('Unlock the talking-head audio/video track')
        if clip.get('sequence_id') or clip.get('hold') or clip.get('enabled') is False: raise ValueError('Talking Head needs an enabled ordinary source clip without frame hold or nesting')
        media=source(clip.get('media_id'),'audio');duration=_clock(clip)
        if duration>MAX_SECONDS or clip['out']>media['duration']+1e-9: raise ValueError('Talking Head needs a source-bounded clip of at most ten minutes')
        settings={key:_bool(body,key,True) for key in ('silences','voice_preset','captions')}
        settings.update(punch_every=number(body.get('punch_every',3),'Punch interval',minimum=0,maximum=600),
            threshold_db=number(body.get('threshold_db',-38),'Silence threshold',minimum=-120,maximum=0),
            min_gap=number(body.get('min_gap',.45),'Minimum silence',minimum=0,maximum=60),
            pad=number(body.get('pad',.08),'Speech padding',minimum=0,maximum=10),broll=_ids(body.get('broll',[]),'B-roll'))
        if settings['punch_every']:
            if track['kind']!='video' or not media.get('has_video'): raise ValueError('Punch-ins need a picture clip; set the punch interval to zero for audio only')
            if settings['punch_every']<from_frames(1,fps)-1e-12 or math.ceil(duration/settings['punch_every'])>MAX_ANCHORS: raise ValueError('Punch-ins need at least one frame per step and at most 4096 anchors')
        if track['kind']=='video' and (clip.get('audio') or {}).get('linked') is False and (settings['silences'] or settings['voice_preset']): raise ValueError('The selected picture has unlinked audio; select the audible source or relink it first')
        if settings['silences'] and (clip.get('audio_detached_id') or clip.get('unlinked_from') or any(c.get('audio_detached_id')==clip_id or c.get('unlinked_from')==clip_id for t in seq['tracks'] for c in t['clips'])):
            raise ValueError('Relink detached audio before ripple-editing the talking head')
        for mid in settings['broll']:source(mid,'video')
        # Ripple planning owns every peer clip and sequence annotation.
        for t in seq.get('tracks',[]):
            for c in t.get('clips',[]):
                mid=c.get('media_id')
                if mid and mid not in media_ids:
                    if not isinstance(project.get('media',{}).get(mid),dict):raise ValueError('A sequence source is missing')
                    media_ids.append(mid)
                    parent=project['media'][mid].get('subclip_of')
                    if parent and parent not in media_ids:
                        if not isinstance(project['media'].get(parent),dict):raise ValueError('A sequence subclip parent is missing')
                        media_ids.append(parent)
        if settings['captions'] and seq.get('transcript'):words_of(seq,project)
    else:
        settings={'shots':_ids(body.get('shots'),'Shots',1),'target':number(body.get('target',15),'Reel duration',minimum=1,maximum=600),
            'name':_text(body,'name','New Reel',120,True),'canvas':_enum(body,'canvas','portrait',('portrait','landscape','current')),
            'rhythm':_enum(body,'rhythm','even',('even','onsets')),'framing':_enum(body,'framing','auto',('auto','cover','contain','blur_fill')),
            'music':body.get('music'),'look':body.get('look'),**{key:_bool(body,key,True) for key in ('captions','sfx')},
            **{key:_text(body,key,maximum=1000 if key in ('hook_sub','caption_text') else 500) for key in ('hook','hook_sub','cta','caption_text')},
            'caption_style':copy.deepcopy(body.get('caption_style'))}
        if settings['music'] is not None:
            if not isinstance(settings['music'],str) or not settings['music']:raise ValueError('Choose music explicitly or none')
            music=source(settings['music'],'audio')
            if music['duration']>MAX_SECONDS:raise ValueError('Make a music subclip of at most ten minutes before building this reel')
        elif settings['rhythm']=='onsets':raise ValueError('Measured-onset rhythm needs an explicit music source')
        if settings['look'] is not None and (not isinstance(settings['look'],str) or not settings['look'] or len(settings['look'])>4096):raise ValueError('Choose a valid LUT resource or none')
        if settings['caption_style'] is not None and not isinstance(settings['caption_style'],dict):raise ValueError('Caption style must be an object or none')
        for mid in settings['shots']:source(mid,'video')
        frames=to_frames(settings['target'],fps);reserved=(to_frames(1.6,fps) if settings['hook'] else 0)+(to_frames(2.2,fps) if settings['cta'] else 0)
        if frames-reserved<len(settings['shots']):raise ValueError('The requested duration needs room for the hook, CTA and at least one frame per shot')
    value={'version':1,'mode':mode,'sequence':seq['id'],'clip_id':clip_id,'settings':settings,'fps':fps,'width':int(width),'height':int(height),
        'media_ids':media_ids,'sequence_basis':copy.deepcopy(seq),'media_basis':{mid:copy.deepcopy(project['media'][mid]) for mid in media_ids},'brand':copy.deepcopy(project.get('brand') or {})}
    return _bounded(value)


def _apply_sequence_ops(sequence,ops,si):
    for op in ops:
        prefix=f'/sequences/{si}/'
        if op.get('op')!='set' or not op.get('path','').startswith(prefix):raise ValueError('Analysis returned an unexpected recipe operation')
        parts=op['path'][len(prefix):].split('/');target=sequence
        for key in parts[:-1]:target=target[int(key)] if isinstance(target,list) else target[key]
        target[int(parts[-1]) if isinstance(target,list) else parts[-1]]=copy.deepcopy(op['value'])


def _budget(project,identity,payload):
    budget=_Budget(project,identity or _json(payload))
    budget.reserved.update(project.get('media',{}))
    for seq in project.get('sequences',[]):
        budget.reserved.add(seq['id']);budget.reserved.update(t['id'] for t in seq.get('tracks',[]))
        budget.reserved.update(c['group'] for t in seq.get('tracks',[]) for c in t.get('clips',[]) if isinstance(c.get('group'),str))
    return budget


def _remap_words(words,mapping,budget):
    if not isinstance(words,list) or len(words)>MAX_WORDS:raise ValueError('Too many or invalid transcript words')
    output=[];previous=-1
    for word in words:
        budget.work()
        if not isinstance(word,dict) or not isinstance(word.get('w'),str) or len(word['w'])>1000:raise ValueError('Invalid transcript word')
        start=number(word.get('s'),'Word start',minimum=0);end=number(word.get('e'),'Word end',minimum=0)
        if start<previous or end<=start:raise ValueError('Transcript words must have ordered positive intervals')
        previous=start;a,b=mapping.time(start),mapping.time(end)
        if b>a+1e-10:output.append({**budget.clone(word),'s':a,'e':b})
    return output


def _caption_words(words,start,end,budget):
    epsilon=max(1e-10,math.ulp(max(start,end))*8)
    selected=[word for word in words if min(word['e'],end)-max(word['s'],start)>epsilon];output=[];block=[]
    for index,word in enumerate(selected):
        budget.work();block.append(word)
        done=len(' '.join(w['w'] for w in block))>=34 or word['w'].rstrip().endswith(('.','?','!')) or index+1==len(selected) or selected[index+1]['s']-word['e']>.65
        if done:
            output.append({'id':budget.id(),'start':max(start,block[0]['s']),'end':min(end,block[-1]['e']),'text':' '.join(w['w'] for w in block)});block=[]
    return output


def _fit(media,width,height,choice='auto'):
    return choice if choice!='auto' else ('blur_fill' if media.get('width',width)>media.get('height',height) and width<height else 'cover')


def _shot(media,identity,start,duration,width,height,*,framing='auto',muted=False):
    available=number(media.get('duration'),'Picture source duration',minimum=1/48000)
    if not media.get('is_image') and duration>available+1e-9:raise ValueError('A selected shot has insufficient source handles for its assigned duration; choose longer footage or a shorter recipe')
    begin=0 if media.get('is_image') else max(0,(available-duration)/2)
    return {'id':identity,'media_id':media['id'],'start':start,'in_':begin,'out':begin+duration,'speed':1,
        'fit':_fit(media,width,height,framing),'transform':{'x':0,'y':0,'scale':1,'rotation':0,'opacity':1},
        'audio':{'gain_db':0 if muted else -3,'linked':not muted},'keyframes':{'transform.scale':[{'t':0,'v':1},{'t':duration,'v':1.08,'e':'ease'}]}}


def _association_safety(original,changed):
    before={c['id']:c for t in original['tracks'] for c in t['clips']};after={c['id']:c for t in changed['tracks'] for c in t['clips']};groups={}
    for clip in before.values():
        if clip.get('group'):groups.setdefault(clip['group'],[]).append(clip['id'])
        for key in ('audio_detached_id','unlinked_from'):
            peer=clip.get(key)
            if peer in before:groups.setdefault(('detached',*sorted((clip['id'],peer))),[clip['id'],peer])
    for members in groups.values():
        if len(members)<2:continue
        deltas=[]
        for cid in members:
            old,new=before[cid],after.get(cid)
            if new is None or any(new.get(key)!=old.get(key) for key in ('in_','out','speed','time_remap','reverse','hold')):
                raise ValueError('Silence removal would cut one member of a grouped or detached association; ungroup or relink it first')
            deltas.append(new['start']-old['start'])
        if max(deltas)-min(deltas)>1e-9:raise ValueError('Silence ripple would move grouped or detached peers differently; include all peer tracks or unlink the association first')


def _talking(project,payload,result,identity,budget):
    sid=payload['sequence'];si=next(i for i,s in enumerate(project['sequences']) if s['id']==sid);original=project['sequences'][si];seq=budget.clone(original);settings=payload['settings']
    ti,track,clip=next((i,t,c) for i,t in enumerate(original['tracks']) for c in t['clips'] if c['id']==payload['clip_id']);old_duration=_clock(clip)
    ranges=[];affected=[track['id']];warnings=[]
    if settings['silences']:
        measured=(result.get('analysis') or {}).get('silences')
        source={'mode':'silences','media_id':clip['media_id'],'sequence':sid,'clip_id':clip['id'],'range':{'start':clip['in_'],'end':clip['out']}}
        planned=analysis_plan(project,source,measured,identity+'-silences');ranges=planned['summary']['ranges'];affected=planned['summary']['tracks'];_apply_sequence_ops(seq,planned['ops'],si)
    mapping=_RangeMap(ranges);old_ids={c['id'] for c in track['clips']};pieces=[c for c in seq['tracks'][ti]['clips'] if c['id']==clip['id'] or c['id'] not in old_ids]
    if not pieces:raise ValueError('The silence result removes all speech; adjust the threshold or disable silence removal')
    budget.reserved.update(c['id'] for c in pieces);duration=sum(_clock(c) for c in pieces);start=mapping.time(clip['start']);end=start+duration;anchor_count=0;fields=['tracks/clips']
    if settings['voice_preset']:
        preset=payload.get('voice_preset')
        if not isinstance(preset,dict) or not isinstance(preset.get('afx_stack'),list):raise ValueError('The captured Voice Clean-up preset is unavailable')
        _bounded(preset,1024*1024)
        warnings.append('Voice Clean-up replaces the selected speech clips’ afx_stack. Manual gain, fades, ducking and other effect settings are preserved; audition the new processing.')
        fields.append('speech/afx_stack')
    if settings['punch_every']:
        warnings.append('Punch-ins replace the speech transform.scale curve with alternating 1.00×/1.12× holds relative to the existing base scale; other transform settings and automation are preserved.')
        fields.append('speech/keyframes/transform.scale')
    for index,piece in enumerate(pieces):
        if settings['voice_preset']:piece['afx_stack']=budget.clone(payload['voice_preset']['afx_stack'])
        if settings['punch_every']:
            d=_clock(piece);count=math.ceil(d/settings['punch_every']);anchor_count+=count
            base=number((piece.get('transform') or {}).get('scale',1),'Base picture scale',minimum=.001,maximum=100)
            if anchor_count>MAX_ANCHORS:raise ValueError('The recipe would generate more than 4096 punch-in anchors')
            anchors=[];seen=set()
            for step in range(count):
                absolute=from_frames(to_frames(piece['start']+step*settings['punch_every'],payload['fps']),payload['fps']);at=max(0,absolute-piece['start'])
                if at>=d or at in seen:continue
                anchors.append({'t':at,'v':base*(1.12 if (step+index)%2 else 1.),'e':'hold'});seen.add(at)
            if anchors and anchors[0]['t']!=0:anchors.insert(0,{'t':0,'v':anchors[0]['v'],'e':'hold'})
            if piece.get('keyframes') is None:piece['keyframes']={}
            if not isinstance(piece['keyframes'],dict):raise ValueError('Clip keyframes must be an object')
            piece['keyframes']['transform.scale']=anchors
    if ranges:
        _association_safety(original,seq)
        excluded=[t['id'] for t in original['tracks'] if t['id'] not in affected]
        warnings.append('Ripple participants: '+', '.join(affected)+'. Locked or sync-excluded tracks remain unchanged: '+(', '.join(excluded) or 'none')+'.')
    if original.get('transcript') and not original.get('transcript_basis'):
        warnings.append('The legacy transcript has no freshness basis; remapped words and generated captions need review against the audio.')
    if original.get('transcript') is not None:
        seq['transcript']=_remap_words(original['transcript'],mapping,budget);fields.append('transcript')
        if ranges:warnings.append('Transcript word intervals are compressed through removed silence; words crossing a removed interval need editorial review.')
    captions=0
    if settings['captions']:
        words=seq.get('transcript') or []
        if words:
            generated=_caption_words(words,start,end,budget);caps=[]
            for old in seq.get('captions') or []:
                a,b=number(old.get('start'),'Caption start',minimum=0),number(old.get('end'),'Caption end',minimum=0)
                if b<=a:raise ValueError('Caption intervals must be positive')
                epsilon=max(1e-10,math.ulp(max(a,b,start,end))*8)
                if b<=start+epsilon or a>=end-epsilon:caps.append(old)
                else:
                    if a<start-epsilon:caps.append({**budget.clone(old),'end':start})
                    if b>end+epsilon:caps.append({**budget.clone(old),'id':budget.id(),'start':end})
            seq['captions']=sorted(caps+generated,key=lambda c:c['start']);captions=len(generated)
            seq['caption_style']={**(seq.get('caption_style') or {}),'animate':'pop','size':int(seq['height']*.037),'borderw':5,'y':.62}
            warnings.append('Captions within the edited speech range are regenerated from the remapped transcript; captions outside it are retained. The sequence-wide pop caption style also changes the appearance of retained captions.')
            fields.extend(['captions','caption_style'])
        else:warnings.append('No captured transcript is available; no speech captions were invented. Transcribe the sequence to generate them.')
    broll=settings['broll']
    if broll:
        candidates=sorted((t for t in seq['tracks'] if t.get('kind')=='video' and t.get('index',0)>track.get('index',0)),key=lambda t:t.get('index',0))
        if candidates:destination=candidates[0]
        else:
            destination={'id':budget.id(),'name':'B-roll','kind':'video','index':max([t.get('index',0) for t in seq['tracks'] if t.get('kind')=='video']+[-1])+1,'clips':[]};seq['tracks'].append(destination)
        if destination.get('locked'):raise ValueError('Unlock the B-roll destination track before applying')
        first=math.ceil(start*float(frame_rate(payload['fps']))-1e-8);last=math.floor(end*float(frame_rate(payload['fps']))+1e-8);frames=last-first
        if frames<len(broll):raise ValueError('Not enough picture frames for the requested B-roll shots')
        occupied=sorted((c['start'],c['start']+_clock(c)) for c in destination['clips']);starts=[a for a,_ in occupied];max_ends=[]
        for _,value in occupied:max_ends.append(max(value,max_ends[-1] if max_ends else 0))
        for index,mid in enumerate(broll):
            a=first+(frames*index)//len(broll);b=first+(frames*(index+1))//len(broll);media=project['media'][mid]
            available=(b-a) if media.get('is_image') else math.floor(media['duration']*float(frame_rate(payload['fps']))+1e-8)
            length=min(max(1,(b-a)//2),max(1,to_frames(2.2,payload['fps'])),available)
            if length<1:raise ValueError('B-roll needs at least one source frame')
            at=from_frames(a+(b-a-length)//2,payload['fps']);d=from_frames(length,payload['fps']);edge=bisect.bisect_left(starts,at+d-1e-10)
            if edge and max_ends[edge-1]>at+1e-10:raise ValueError('B-roll would overlap existing material on its destination track')
            value=_shot(media,budget.id(),at,d,payload['width'],payload['height'],muted=True);fade=min(.25,d/4)
            value.update(transition_in={'type':'dissolve','align':'start','duration':fade},transition_out={'type':'dissolve','duration':fade},note=f'B-roll {index+1}/{len(broll)} over speech');destination['clips'].append(value)
        affected.append(destination['id']);warnings.append('B-roll follows the requested source order, with centered bounded source windows and picture audio unlinked.')
    if seq.get('transcript') is not None and (not original.get('transcript_basis') or original['transcript_basis']==transcript_basis(original,project)):
        candidate={**project,'sequences':[seq if s['id']==sid else s for s in project['sequences']]};seq['transcript_basis']=transcript_basis(seq,candidate);fields.append('transcript_basis')
    removed=sum(b-a for a,b in ranges)
    return ([] if seq==original else [{'op':'set','path':f'/sequences/{si}','value':seq}]),{'sequence':sid,'sequence_id':sid,'sequence_name':seq.get('name',sid),
        'canvas':{'mode':'current','width':seq['width'],'height':seq['height'],'fps':seq['fps']},'requested':old_duration,'achieved':duration,
        'pieces':len(pieces),'removed_duration':removed,'broll':len(broll),'captions':captions,'tracks':list(dict.fromkeys(affected)),
        'cuts':[p['start'] for p in pieces[1:]],'ranges':ranges,'affected_fields':fields,'warnings':warnings,
        'message':f'Talking Head: {len(pieces)} speech pieces, {removed:.6f} seconds removed, {len(broll)} B-roll shots and {captions} captions.'}


def _brand(value,brand,depth=0,counter=None):
    counter=[0] if counter is None else counter;counter[0]+=1
    if depth>16 or counter[0]>10000:raise ValueError('Recipe template has excessive depth or nodes')
    if isinstance(value,str):
        if len(value)>8192:raise ValueError('Recipe template text is too large')
        return re.sub(r'\{\{(primary|secondary|text|font)\}\}',lambda match:str(brand.get(match.group(1),'')),value)
    if isinstance(value,list):return [_brand(child,brand,depth+1,counter) for child in value]
    if isinstance(value,dict):
        result={key:_brand(child,brand,depth+1,counter) for key,child in value.items()}
        if result.get('font')=='':result.pop('font',None)
        return result
    return value


def _fit_text(text,size,width):
    """The existing balanced-two-line headline heuristic, with bounded work."""
    words=text.strip().split();lines=[text.strip()]
    if len(words)>1 and len(text)>9:
        lengths=[len(word) for word in words];total=sum(lengths)+len(words)-1;prefix=0;best=None
        for index,length in enumerate(lengths[:-1],1):
            prefix+=length+(1 if index>1 else 0);score=abs(prefix-(total-prefix-1))
            if best is None or score<best[0]:best=(score,index)
        lines=[' '.join(words[:best[1]]),' '.join(words[best[1]:])]
    longest=max((len(line) for line in lines),default=0)
    if longest*size*.62>width*.9:size=max(1,math.floor(width*.9/(longest*.62)))
    return '\n'.join(lines),size


def _card(payload,which,text,sub,start,duration,budget,width):
    template=(payload.get('templates') or {}).get(which)
    if not isinstance(template,dict) or not isinstance(template.get('layers'),list) or len(template['layers'])>100 or any(not isinstance(layer,dict) for layer in template['layers']):raise ValueError('The captured '+which+' template is unavailable or too large')
    brand={'primary':'#E8631C','secondary':'#7A2E9E','text':'#FFFFFF','font':'',**payload.get('brand',{})}
    layers=_brand(template['layers'],brand);text_index=0
    for layer in layers:
        if layer.get('kind')=='text':
            layer['text']=text if which=='cta' or text_index==0 else sub;text_index+=1
            layer['text'],layer['size']=_fit_text(layer['text'],number(layer.get('size',64),'Template text size',minimum=1,maximum=2048),width)
    if not text_index:raise ValueError('The captured '+which+' template has no editable text')
    return {'id':budget.id(),'media_id':None,'start':start,'in_':0,'out':duration,'speed':1,
            'graphic':{'name':'Hook' if which=='hook' else 'CTA','layers':layers},'transform':{'opacity':1},'keyframes':{},'note':which+' card'}


def _music(payload,project,duration,budget):
    mid=payload['settings']['music']
    if not mid:return [],[],[],0
    media=project['media'][mid];intervals,warnings=music_design(media['duration'],duration);clips=[];cursor=0
    for index,(a,b) in enumerate(intervals):
        value={'id':budget.id(),'media_id':mid,'start':cursor,'in_':a,'out':b,'speed':1,
            'audio':{'gain_db':-10,'linked':True,'fade_out':min(.8,b-a) if index==len(intervals)-1 else 0},'note':'Reel music bed'}
        clips.append(value);cursor+=b-a
    warnings.append('The music bed uses −10 dB gain and an ending fade; speech ducking and limiting are not inferred.')
    return clips,intervals,warnings,cursor


def _onset_frames(payload,result,intervals,first,last,budget):
    measured=(result.get('analysis') or {}).get('onsets');mid=payload['settings']['music'];duration=payload['media_basis'][mid]['duration']
    if not isinstance(measured,dict) or measured.get('kind')!='audio_analysis' or measured.get('mode')!='beats' or measured.get('clock')!='clip-local' or measured.get('scope')!='media' or measured.get('media_id')!=mid or measured.get('range')!={'start':0,'end':duration}:
        raise ValueError('Measured onset results do not match the captured music source window')
    rows=measured.get('measurements')
    if not isinstance(rows,list) or len(rows)!=1 or not isinstance(rows[0],dict) or rows[0].get('media_id')!=mid or rows[0].get('silent'):raise ValueError('The selected music has no reliable measured onset result')
    row=rows[0];beats=row.get('beats')
    if not isinstance(beats,list) or not 5<=len(beats)<=8192:raise ValueError('Too few or too many measured onsets; choose even rhythm explicitly to avoid analysis')
    previous=-1
    for value in beats:
        value=number(value,'Measured onset',minimum=0,maximum=duration)
        if value<=previous or value>=duration:raise ValueError('Measured onsets must increase within the music source')
        previous=value
    confidence=number(row.get('tempo_confidence'),'Onset regularity score',minimum=0,maximum=1)
    if confidence<.8:raise ValueError('Measured onsets are too irregular for this recipe; choose even rhythm explicitly')
    candidates=[];cursor=0
    for a,b in intervals:
        for at in beats[bisect.bisect_left(beats,a):bisect.bisect_left(beats,b)]:
            budget.work();frame=to_frames(cursor+at-a,payload['fps'])
            if first<frame<last:candidates.append(frame)
        cursor+=b-a
    return sorted(set(candidates)),confidence


def _reel(project,payload,result,identity,budget):
    settings=payload['settings'];fps=payload['fps'];width,height=(1080,1920) if settings['canvas']=='portrait' else (1920,1080) if settings['canvas']=='landscape' else (payload['width'],payload['height'])
    count=len(settings['shots']);frames=to_frames(settings['target'],fps);duration=from_frames(frames,fps)
    hook=to_frames(1.6,fps) if settings['hook'] else 0;cta=to_frames(2.2,fps) if settings['cta'] else 0;body_end=frames-cta;body_frames=body_end-hook
    if body_frames<count:raise ValueError('Not enough frames for all ordered shots after the hook and CTA')
    music,intervals,warnings,music_coverage=_music(payload,project,duration,budget)
    if settings['rhythm']=='onsets':
        candidates,confidence=_onset_frames(payload,result,intervals,hook,body_end,budget)
        if len(candidates)<count-1:raise ValueError('Not enough measured music onsets fit the requested shot count; choose even rhythm explicitly or change the duration')
        bounds=[hook];previous=-1
        for index in range(1,count):
            desired=hook+body_frames*index/count;lo=previous+1;hi=len(candidates)-(count-index)
            right=min(hi,max(lo,bisect.bisect_left(candidates,desired)));choices=[right]+([right-1] if right>lo else [])
            selected=min(choices,key=lambda item:(abs(candidates[item]-desired),item));bounds.append(candidates[selected]);previous=selected
        warnings.append('Shot cuts use measured onset candidates mapped through the music edits and rounded to picture frames. These are not musical downbeats; audition the result.')
    else:
        confidence=None;bounds=[hook+(body_frames*index)//count for index in range(count)]
        warnings.append('Shot rhythm uses the explicitly selected even spacing; no beat or downbeat detection is claimed.')
    bounds.append(body_end)
    sid=budget.id();video={'id':budget.id(),'name':'Shots','kind':'video','index':0,'clips':[]};cards={'id':budget.id(),'name':'Titles','kind':'video','index':1,'clips':[]}
    source_audio={'id':budget.id(),'name':'Source audio','kind':'audio','index':0,'clips':[]}
    music_track={'id':budget.id(),'name':'Music','kind':'audio','index':2,'clips':music};sfx_track={'id':budget.id(),'name':'SFX','kind':'audio','index':1,'clips':[]}
    for index,(mid,a,b) in enumerate(zip(settings['shots'],bounds,bounds[1:])):
        budget.work();value=_shot(project['media'][mid],budget.id(),from_frames(a,fps),from_frames(b-a,fps),width,height,framing=settings['framing'])
        value.update(color={'lut':settings['look']} if settings['look'] else {},note=f'Reel shot {index+1}/{count}')
        if index:
            length_frames=min(max(1,to_frames(.25,fps)),b-a,bounds[index]-bounds[index-1]);length=from_frames(length_frames,fps)
            kind='dip_black' if index%3==2 else 'dissolve'
            if kind=='dissolve' and (project['media'][mid].get('is_image') or value['in_']+1e-10<length):
                kind='dip_black';warnings.append(f'Shot {index+1} has no incoming dissolve handle; its transition uses a bounded dip to black.')
            value['transition_in']={'type':kind,'align':'end' if kind=='dissolve' else 'start','duration':length}
        video['clips'].append(value)
    if hook:cards['clips'].append(_card(payload,'hook',settings['hook'],settings['hook_sub'],0,from_frames(hook,fps),budget,width))
    if cta:cards['clips'].append(_card(payload,'cta',settings['cta'],'',from_frames(body_end,fps),from_frames(cta,fps),budget,width))
    if hook or cta:warnings.append('Hook/CTA text uses balanced two-line wrapping and approximate width-based sizing; review the actual chosen font and safe areas before delivery.')
    sections=([('Hook',0,hook,'green')] if hook else [])+[('Body',hook,body_end,'blue')]+([('CTA',body_end,frames,'orange')] if cta else [])
    markers=[{'id':budget.id(),'type':'section','name':name,'time':from_frames(a,fps),'duration':from_frames(b-a,fps),'color':color} for name,a,b,color in sections]
    captions=[]
    if settings['captions'] and (settings['caption_text'] or settings['hook']):
        captions=[{'id':budget.id(),'start':from_frames(hook,fps),'end':from_frames(min(body_end,hook+to_frames(2.6,fps)),fps),'text':settings['caption_text'] or settings['hook']}]
        warnings.append('The Reel caption contains the requested hook/caption text; speech transcription is not inferred.')
    seq={'id':sid,'name':settings['name'],'width':width,'height':height,'fps':fps,'duration':duration,'tracks':[video,cards,source_audio,sfx_track,music_track],
        'markers':markers,'captions':captions,'master':{},'workflow':{'recipe':'reel'}}
    if settings['caption_style'] is not None:
        brand={'primary':'#E8631C','secondary':'#7A2E9E','text':'#FFFFFF','font':'',**payload.get('brand',{})};seq['caption_style']=_brand(settings['caption_style'],brand)
    ops=[];cuts=[from_frames(frame,fps) for frame in bounds[1:-1]]
    if settings['sfx'] and cuts:
        media=result.get('sfx_media')
        if not isinstance(media,dict) or not isinstance(media.get('id'),str) or media['id'] in project.get('media',{}) or not media.get('has_audio') or not isinstance(media.get('path'),str):raise ValueError('The task-owned whoosh source is missing or collides with existing media')
        source_duration=number(media.get('duration'),'SFX duration',minimum=1/48000,maximum=10);previous_end=0
        for index,cut in enumerate(cuts):
            # Keep one dedicated non-overlapping SFX track. Dense cuts shorten
            # each source window explicitly rather than normalize away sounds.
            start=max(previous_end,max(0,round((cut-.2)*48000)/48000));next_cut=cuts[index+1] if index+1<len(cuts) else duration
            end=min(duration,start+source_duration,max(start,next_cut-.2))
            end=math.floor(end*48000+1e-8)/48000
            if end-start<1/48000:raise ValueError('The requested cuts are too dense for non-overlapping whooshes; disable SFX or use fewer shots')
            sfx_track['clips'].append({'id':budget.id(),'media_id':media['id'],'start':start,'in_':0,'out':end-start,'speed':1,'audio':{'gain_db':-8,'linked':True,'fade_out':min(.02,(end-start)/4)},'note':'Whoosh under cut'});previous_end=end
        ops.append({'op':'set','path':'/media/'+media['id'].replace('~','~0').replace('/','~1'),'value':budget.clone(media)})
        warnings.append('Whooshes are task-owned media added atomically. Dense cuts shorten their source windows; each ends with a short fade.')
    warnings.append('Source order is preserved. Framing uses the chosen fit with a gentle push-in; subject-aware reframing and automatic speech ducking are not performed.')
    ops.append({'op':'insert','path':f'/sequences/{len(project["sequences"])}','value':seq})
    return ops,{'sequence':sid,'sequence_id':sid,'sequence_name':settings['name'],'canvas':{'mode':settings['canvas'],'width':width,'height':height,'fps':fps},
        'requested':settings['target'],'achieved':duration,'tracks':[t['id'] for t in seq['tracks']],'shots':count,'music_segments':len(music),'music_coverage':music_coverage,
        'transitions':[{'at':c['start'],**c['transition_in']} for c in video['clips'] if c.get('transition_in')],
        'onset_count':len(cuts) if settings['rhythm']=='onsets' else 0,'tempo_confidence':confidence,'captions':len(captions),'cuts':cuts,'ranges':[],
        'affected_fields':['new sequence','ordered shots/framing/look','hook/CTA','captions/style','section markers','music bed','generated SFX'] if settings['sfx'] else ['new sequence','ordered shots/framing/look','hook/CTA','captions/style','section markers','music bed'],
        'warnings':warnings,'message':f'Create “{settings["name"]}”: {duration:.6f} seconds, {count} ordered shots, {width}×{height}, {settings["rhythm"]} rhythm.'}


def plan(project,payload,result,identity=''):
    """Canonical private plan; workflow independently owns context/source checks."""
    if not isinstance(payload,dict) or not isinstance(result,dict) or result.get('version')!=1 or result.get('kind')!='recipe' or result.get('mode')!=payload.get('mode') or result.get('signature')!=payload.get('signature'):raise ValueError('Recipe result does not match its captured input')
    _bounded(payload);_bounded(result)
    current=capture(project,{**payload['settings'],'sequence':payload['sequence'],'clip_id':payload.get('clip_id')},payload['mode'])
    # Workflow separately recaptures resource stamps, presets and ownership.
    # This check binds editable sequence/source metadata even for direct callers.
    for key in ('sequence_basis','settings','fps','width','height','brand'):
        if current[key]!=payload.get(key):raise ValueError('A recipe dependency changed; start a fresh recipe')
    for mid in current['media_ids']:
        original=payload.get('media_basis',{}).get(mid)
        if original is None:raise ValueError('Recipe source dependencies changed')
        fields=('id','path','duration','subclip_of','sub_in','sub_out','fps','frame_rate','interpret_fps','has_audio','has_video','is_image','synthetic','channels','sample_rate')
        if any(current['media_basis'][mid].get(key)!=original.get(key) for key in fields):raise ValueError('A recipe source changed')
    budget=_budget(project,identity,payload)
    ops,summary=(_talking if payload['mode']=='talking_head' else _reel)(project,payload,result,identity,budget)
    _bounded(ops)
    count=budget.count
    for op in ops:
        if op['path'].startswith('/sequences/'):
            added=sum(len(t['clips']) for t in op['value']['tracks'])
            old=0 if op['op']=='insert' else sum(len(t['clips']) for t in project['sequences'][int(op['path'].split('/')[2])]['tracks'])
            count+=added-old
    if count>MAX_CLIPS:raise ValueError('Recipe would create too many clips')
    summary={'kind':'recipe','mode':payload['mode'],**summary}
    fingerprint=hashlib.sha256(_json({'identity':identity,'ops':ops,'summary':summary}).encode()).hexdigest()
    return {'ops':ops,'summary':summary,'fingerprint':fingerprint}
