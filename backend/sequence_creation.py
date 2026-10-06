"""Atomic empty/source sequence planning; no project, file or task mutation.

The caller binds this bounded metadata operation to one saved owner and commits
all operations once. Media inspection/preparation remains a separate workflow.
"""
import re

from source_commands import physical
from source_creation import _range as source_window
from source_relink import bounded, number
from timeline_time import frame_rate, timecode_mode
from picture_geometry import dimension as _dimension, display_size as _picture_size


def _name(value, fallback):
    if value is None: value = fallback
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 256 or any(ord(c) < 32 for c in value):
        raise ValueError('Sequence name must contain 1–256 characters without control characters')
    return value.strip()


def plan(project, body, *, identity):
    bounded(project, 'Sequence creation project')
    bounded(body, 'Sequence creation request', 64*1024)
    if not isinstance(body, dict): raise ValueError('Sequence creation needs an object request')
    if not isinstance(identity, str) or not re.fullmatch('[0-9a-f]{32}', identity):
        raise ValueError('Sequence creation needs a fresh operation identity')
    context = body.get('_context')
    if not isinstance(context, dict) or any(not isinstance(context.get(k), str) or not context[k] for k in ('workspace','project','revision')):
        raise ValueError('Sequence creation needs the captured saved context')
    mode = body.get('mode')
    if mode not in ('empty', 'source'): raise ValueError('Choose empty or source sequence creation')
    sequences = project.get('sequences')
    if not isinstance(sequences, list) or not 1 <= len(sequences) < 1000:
        raise ValueError('Sequence creation supports up to 1000 sequences')
    if any(not isinstance(s, dict) or not isinstance(s.get('id'), str) or not s['id'] for s in sequences):
        raise ValueError('Repair ambiguous sequence identities before creating a sequence')
    sequence_ids = [s['id'] for s in sequences]
    if len(sequence_ids) != len(set(sequence_ids)): raise ValueError('Repair duplicated sequence identities first')
    source_id = body.get('sequence')
    if not isinstance(source_id, str) or source_id not in sequence_ids: raise ValueError('Choose the captured sequence whose format should be used')
    template = sequences[sequence_ids.index(source_id)]
    width = _dimension(template.get('width'), 'Sequence width')
    height = _dimension(template.get('height'), 'Sequence height')
    rate = frame_rate(template.get('fps')); display = timecode_mode(rate, template.get('timecode_format', 'ndf'))
    warnings = []; media_id = None; clip_id = None; duration = None; picture = False; has_audio = False
    fallback = 'Sequence '+str(len(sequences)+1).zfill(2)
    if mode == 'empty':
        if body.get('media_id') is not None or 'still_duration' in body:
            raise ValueError('An empty sequence does not take a source or still duration')
    else:
        library = project.get('media')
        if not isinstance(library, dict) or len(library) > 10000: raise ValueError('Source sequence creation supports up to 10000 media items')
        selected, parent = physical(project, body.get('media_id')); media_id = selected['id']
        for flag in ('has_video','has_audio'):
            if type(selected.get(flag)) is not bool: raise ValueError('Source stream metadata is incomplete; finish inspection or Relink before creating its sequence')
        picture = selected['has_video'] or bool(selected.get('is_image'))
        has_audio = selected['has_audio']
        if not picture and not has_audio: raise ValueError('The source has no known picture or audio stream')
        if selected.get('is_image'):
            duration = number(body.get('still_duration', 5), 'Still duration', 1e-9)
            if duration > 86400: raise ValueError('Still duration must not exceed 24 hours')
            if selected.get('subclip_of'): raise ValueError('Flatten an unsupported still subclip before sequence creation')
        else:
            if 'still_duration' in body: raise ValueError('Still duration applies only to a still image')
            duration, _, _, _ = source_window(selected, parent)
            if picture:
                value = selected.get('interpret_fps')
                if value is None: value = selected.get('frame_rate') if selected.get('frame_rate') is not None else selected.get('fps')
                rate = frame_rate(value)
        if picture: width, height = _picture_size(selected, warnings)
        # New source sequences use NDF; a format change must not silently carry
        # an incompatible DF label from the template sequence.
        display = 'ndf'
        fallback = re.sub(r'\.[^.]+$', '', str(selected.get('name') or media_id))[:256] or 'Source sequence'
        warnings.append('The complete selected source window is referenced once; existing source media and timelines remain unchanged.')
        if selected.get('vfr'): warnings.append('The sequence uses the declared source rate; this does not conform variable-frame-rate source pictures.')
        if selected.get('status') not in (None,'ready'):
            warnings.append('Source metadata is usable, but preview preparation may still be pending or unavailable.')
    name = _name(body.get('name'), fallback)
    new_id = 'seq_'+identity
    if new_id in sequence_ids: raise ValueError('Sequence identity already exists; submit a new command from saved state')
    tracks = [{'id':tid,'kind':kind,'index':index,'muted':False,'locked':False,'clips':[]}
        for tid,kind,index in [('V2','video',2),('V1','video',1),('A1','audio',1),('A2','audio',2)]]
    if mode == 'source':
        clip_id = 'clip_'+identity
        clip = {'id':clip_id,'media_id':media_id,'start':0,'in_':0,'out':duration,'speed':1,
            'audio':{'gain_db':0,'linked':has_audio},'transform':{'x':0,'y':0,'scale':1,'rotation':0,'opacity':1},
            'keyframes':{},'color':{}}
        tracks[1 if picture else 2]['clips'] = [clip]
    sequence = {'id':new_id,'name':name,'width':width,'height':height,'fps':float(rate),'timecode_format':display,
        'duration':None,'markers':[],'captions':[],'tracks':tracks}
    if frame_rate(sequence['fps']) != rate:
        warnings.append('The source rational rate is represented numerically by the current sequence schema; standard NTSC rates retain canonical rational timing.')
    summary = {'kind':'sequence_creation','mode':mode,'sequence':new_id,'source_sequence':source_id,'media_id':media_id,
        'clip_id':clip_id,'name':name,'format':{'width':width,'height':height,'fps':float(rate),'frame_rate':str(rate),'timecode_format':display},
        'duration':duration,'warnings':warnings,'message':'Created '+name+(' from the selected source.' if mode=='source' else '.')}
    return {'ops':[{'op':'insert','path':'/sequences/'+str(len(sequences)),'value':sequence}],
        'sequence':sequence,'summary':summary,'mode':mode,'source_sequence':source_id,'media_id':media_id,'clip_id':clip_id,'warnings':warnings}
