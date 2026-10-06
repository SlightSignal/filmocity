"""Bounded word-layer plans; saving and resource ownership belong to the caller.

The renderer draws each layer on the same full-size transparent canvas. Its
position, rotation, scale and opacity curves can therefore be copied to all
words without changing the animation's coordinate system or clock.
"""
import copy
import bisect
import hashlib
import json
import math
import re
import unicodedata

from PIL import ImageFont
from project_resources import style_font
from overlap_normalization import _clock
from timeline_time import frame_rate

MAX_WORDS = 100
MAX_LAYERS = 256
MAX_TEXT = 10_000
MAX_POINTS = 8192
MAX_EXPANDED_POINTS = 32_768
MAX_COMMANDS = 200_000
MAX_BYTES = 4 * 1024 * 1024
ANIMATIONS = {'none', 'fade', 'rise', 'drop', 'slide_left', 'slide_right',
              'slide_up', 'slide_down', 'pop', 'zoom', 'rotate_in',
              'wipe_left', 'wipe_right', 'wipe_up', 'wipe_down', 'blur_in', 'blur_out'}
EASES = {'linear', 'ease_out', 'ease_in', 'ease_in_out', 'back_out', 'bounce'}
CURVES = {'x', 'y', 'opacity', 'scale', 'rotation'}
LAYER_KEY = re.compile(r'^g(\d+)\.(.+)$')


def _number(value, label, minimum=-1_000_000, maximum=1_000_000):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not minimum <= value <= maximum:
        raise ValueError(label + f' must be finite and between {minimum:g} and {maximum:g}')
    return value


def _bounded(value):
    pending = [(value, 0)]; count = 0
    while pending:
        item, depth = pending.pop(); count += 1
        if depth > 32 or count + len(pending) > 100_000: raise ValueError('Graphic metadata exceeds the depth or item limit')
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item): raise ValueError('Graphic metadata keys must be strings')
            pending.extend((child, depth+1) for child in item.values())
        elif isinstance(item, list): pending.extend((child, depth+1) for child in item)
    try: encoded = json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(',', ':'))
    except (TypeError, ValueError, RecursionError) as error: raise ValueError('Graphic settings must be finite JSON data') from error
    if len(encoded.encode('utf-8')) > MAX_BYTES: raise ValueError('Graphic metadata exceeds four MiB')
    return encoded


def _target(project, body):
    if not isinstance(project, dict) or not isinstance(body, dict): raise ValueError('Word splitting requires a saved graphic and command')
    _bounded(body)
    sequences = project.get('sequences')
    if not isinstance(sequences, list) or len(sequences) > 256: raise ValueError('Choose one existing sequence')
    matches = [(i, value) for i, value in enumerate(sequences) if isinstance(value, dict) and value.get('id') == body.get('sequence')]
    if len(matches) != 1: raise ValueError('The sequence is missing or ambiguous')
    si, sequence = matches[0]; tracks = sequence.get('tracks')
    if not isinstance(tracks, list) or len(tracks) > 1024: raise ValueError('The sequence has too many or invalid tracks')
    matches = []; count = 0
    for ti, track in enumerate(tracks):
        if not isinstance(track, dict) or not isinstance(track.get('clips', []), list): raise ValueError('Repair invalid sequence tracks')
        count += len(track.get('clips', []))
        if count > 100_000: raise ValueError('The sequence exceeds the word-command clip limit')
        matches.extend((ti, ci, track, clip) for ci, clip in enumerate(track.get('clips', [])) if isinstance(clip, dict) and clip.get('id') == body.get('clip_id'))
    if len(matches) != 1: raise ValueError('The graphic clip is missing or ambiguous')
    ti, ci, track, clip = matches[0]
    if track.get('locked'): raise ValueError('Unlock the graphic track before splitting words')
    if track.get('kind') != 'video': raise ValueError('Choose a graphic on a video track')
    _bounded(clip)
    graphic = clip.get('graphic'); layers = graphic.get('layers') if isinstance(graphic, dict) else None
    if not isinstance(layers, list) or not 1 <= len(layers) <= MAX_LAYERS or any(not isinstance(layer, dict) for layer in layers):
        raise ValueError('Choose a graphic with 1–256 valid layers')
    index = body.get('layer', 0)
    if type(index) is not int or not 0 <= index < len(layers): raise ValueError('Choose one existing text layer')
    layer = layers[index]
    if layer.get('kind') != 'text': raise ValueError('The selected layer is not text')
    return si, ti, ci, sequence, track, clip, layers, index, layer


def resources(project, body):
    """Resolve the same selected font used by plan and the renderer."""
    return [style_font(_target(project, body)[-1])]


def _animation(value, label):
    if not isinstance(value, dict): raise ValueError(label+' must be an animation object')
    unknown = set(value) - {'type', 'duration', 'delay', 'ease', 'distance'}
    if unknown: raise ValueError(label+' contains unsupported animation settings')
    kind = value.get('type', 'none')
    if kind not in ANIMATIONS: raise ValueError(label+' has an unsupported type; remove typewriter or unknown animation first')
    result = copy.deepcopy(value)
    if 'ease' in result and result['ease'] not in EASES: raise ValueError(label+' has an unsupported easing curve')
    for key, low, high in [('duration', .05, 60), ('delay', 0, 600), ('distance', .000001, 4)]:
        if key in result: _number(result[key], label+' '+key, low, high)
    return result


def _curves(clip, index, added, layers):
    original = clip.get('keyframes')
    if original is None: return None, 0
    if not isinstance(original, dict) or len(original) > 2048: raise ValueError('Repair invalid or excessive graphic keyframes')
    result = {}; copied = 0; commands = 0
    for key, points in original.items():
        if not isinstance(points, list): raise ValueError('Graphic keyframe curves must be arrays')
        if len(points) > MAX_POINTS: raise ValueError('A graphic curve exceeds 8192 points')
        previous = -math.inf
        for point in points:
            if not isinstance(point, dict): raise ValueError('Graphic keyframes must be objects')
            time = _number(point.get('t'), 'Keyframe time', -864_000, 864_000)
            _number(point.get('v'), 'Keyframe value')
            if time <= previous: raise ValueError('Graphic keyframe times must be strictly increasing')
            previous = time
        match = LAYER_KEY.fullmatch(key)
        targets = [key]
        if match:
            old, prop = int(match[1]), match[2]
            if match[1] != str(old): raise ValueError('Graphic automation layer indices must use canonical integers')
            if old >= layers: raise ValueError('A keyframe refers to a missing graphic layer')
            if prop in CURVES:
                for point in points:
                    if point.get('e','linear') not in ('linear','hold','ease','ease_in','ease_out','bezier'):
                        raise ValueError('A layer curve uses unsupported interpolation')
                    for handle in ('i','o'):
                        if point.get(handle) is not None:
                            values = point[handle]
                            if not isinstance(values,list) or len(values)!=2: raise ValueError('Bezier handles must contain two finite numbers')
                            for value in values: _number(value,'Bezier handle')
                baked = len(points)+11*sum(point.get('e') in ('ease','ease_in','ease_out','bezier') for point in points[:-1])
                if prop in ('x','y','rotation') and baked>256:
                    raise ValueError('A layer motion expression exceeds 256 rendered segments; simplify the curve before splitting')
            if old == index:
                if prop not in CURVES: raise ValueError('Selected-layer automation '+prop+' cannot be preserved by word splitting')
                targets = [f'g{li}.{prop}' for li in range(index, index+added+1)]
            elif old > index: targets = [f'g{old+added}.{prop}']
            if points and prop in ('scale', 'opacity'): commands += len(targets)
        copied += len(points)*len(targets)
        if copied > MAX_EXPANDED_POINTS: raise ValueError('Split-word automation would exceed 32768 copied points')
        for target in targets: result[target] = copy.deepcopy(points)
    return result, commands


def _origin(align, canvas, extent, offset, vertical=False):
    if vertical:
        return {'center': math.floor((canvas-extent)/2), 'top': round(canvas*.08), 'bottom': canvas-extent-round(canvas*.12)}[align] + offset
    return {'center': (canvas-extent)/2, 'left': round(canvas*.06), 'right': canvas-extent-round(canvas*.06)}[align] + offset


def plan(project, body):
    si, ti, ci, sequence, track, clip, layers, index, layer = _target(project, body)
    width, height = sequence.get('width'), sequence.get('height')
    if type(width) is not int or type(height) is not int or not 16 <= width <= 16384 or not 16 <= height <= 16384:
        raise ValueError('Sequence dimensions must be integer pixels from 16 to 16384')
    fps = float(frame_rate(sequence.get('fps', 30)))
    if fps > 240: raise ValueError('Word animation supports rates up to 240 fps')
    if any(key in clip and type(clip[key]) is not bool for key in ('hold','reverse')): raise ValueError('Graphic hold and reverse settings must be true or false')
    duration = _number(_clock(clip), 'Graphic duration', 1/fps, 600)
    text = layer.get('text', '')
    if not isinstance(text, str) or len(text) > MAX_TEXT: raise ValueError('Text must contain at most 10000 characters')
    spans = list(re.finditer(r'\S+', text))
    if len(spans) > MAX_WORDS or len(layers)+len(spans)-1 > MAX_LAYERS: raise ValueError('Split Words supports at most 100 words and 256 resulting layers')
    stagger = _number(body.get('stagger', .12), 'Word stagger', 0, 60)
    old_in = layer.get('anim_in') or {}
    if not isinstance(old_in,dict): raise ValueError('Existing incoming animation must be an object')
    if 'anim' not in body: old_in = _animation(old_in, 'Existing incoming animation')
    incoming = _animation(body.get('anim', old_in or {'type': 'pop', 'duration': .35, 'ease': 'back_out'}), 'Incoming animation')
    if not incoming: incoming = {'type':'none'}
    summary = {'sequence': sequence['id'], 'clip_id': clip['id'], 'track': track.get('id'), 'layer': index,
               'words': len(spans), 'layers': len(spans), 'changed': False, 'duration': duration, 'stagger': stagger,
               'warnings': [], 'affected_fields': [], 'message': 'This layer has fewer than two words; nothing changed.'}
    if len(spans) < 2:
        return {'ops': [], 'summary': summary, 'layers': len(spans), 'fingerprint': hashlib.sha256(_bounded(summary).encode()).hexdigest()}
    # Admission precedes every per-word layer/curve copy. Unknown editorial
    # fields remain intact, but must not amplify a four-MiB clip into hundreds
    # of MiB before the final serialized-output check can run.
    clip_bytes = len(_bounded(clip).encode('utf-8'))
    layer_bytes = len(_bounded(layer).encode('utf-8'))
    curves = clip.get('keyframes') or {}
    selected_curves = {key:value for key,value in curves.items() if key.startswith(f'g{index}.')} if isinstance(curves,dict) else {}
    curve_bytes = len(_bounded(selected_curves).encode('utf-8'))
    if clip_bytes+(len(spans)-1)*(layer_bytes+curve_bytes+512)+8192 > MAX_BYTES:
        raise ValueError('Split-word metadata expansion would exceed four MiB; simplify the selected layer or automation first')
    for key in ('uppercase', 'vertical', 'box', 'glow', 'fix_bounds', 'shadow'):
        if key in layer and type(layer[key]) is not bool: raise ValueError('Text '+key+' must be true or false')
    if any(char in text for char in '\r\t\v\f') or layer.get('vertical'):
        raise ValueError('Split Words requires horizontal text without tabs or control-line separators')
    lines = text.split('\n')
    if len(lines)>100 or not lines[0].strip() or not lines[-1].strip():
        raise ValueError('Use at most 100 lines, with no leading or trailing blank line, before splitting words')
    if '%{' in text or any(unicodedata.bidirectional(char) in ('R','AL','AN','RLE','RLO','LRE','LRO','PDF','RLI','LRI','FSI','PDI') for char in text):
        raise ValueError('Dynamic or bidirectional text cannot retain its shaping when split into independent words')
    if layer.get('box') or layer.get('glow') or layer.get('blur') or layer.get('fix_bounds'):
        raise ValueError('Remove the aggregate box, glow, blur or automatic bounds correction before splitting words')
    if layer.get('letter_spacing'): raise ValueError('Clear letter spacing before splitting; preview and rendered tracking differ')
    align = layer.get('align', 'center'); valign = layer.get('valign', 'center')
    if align not in ('left','center','right') or valign not in ('top','center','bottom'): raise ValueError('Choose a supported text alignment')
    for key in ('x','y','baseline_dy','scale','rotation','opacity','borderw','shadowx','shadowy'):
        if key in layer and layer[key] is not None: _number(layer[key], 'Text '+key)
    size = int(_number(layer.get('size', height*.05), 'Font size', 1, 2048))
    font_path = style_font(layer)
    try: font = ImageFont.truetype(font_path, size)
    except (OSError, ValueError, TypeError) as error: raise ValueError('The selected font cannot be measured') from error
    outgoing = _animation(layer.get('anim_out') or {}, 'Existing outgoing animation')
    if old_in and old_in != incoming: summary['warnings'].append('The requested word entrance replaces the selected layer’s incoming animation; outgoing animation and clip-local keyframes are retained.')
    # Uppercase can change character count, so measure the rendered prefixes,
    # never offsets into an independently transformed concatenation.
    rendered_lines = [line.upper() if layer.get('uppercase') else line for line in lines]
    spacing = int(_number(layer.get('line_spacing',size*.15), 'Line spacing', -2048, 2048))
    from text_metrics import measure
    rendered_text = '\n'.join(rendered_lines)
    prefixes = []
    line_starts = [0]
    for line in lines[:-1]: line_starts.append(line_starts[-1]+len(line)+1)
    for match in spans:
        line_index = bisect.bisect_right(line_starts,match.start())-1
        prefix = text[line_starts[line_index]:match.start()]
        prefixes.append(prefix.upper() if layer.get('uppercase') else prefix)
    measured_words = [match.group().upper() if layer.get('uppercase') else match.group() for match in spans]
    metrics = measure(font_path, size, [rendered_text, *rendered_lines, *prefixes, *measured_words])
    advance = metrics[rendered_text][3]+spacing
    if len(lines)>1 and advance<=0: raise ValueError('Line spacing must preserve positive baseline advance')
    # drawtext's text y anchor belongs to the first line's glyph top, even
    # when a later line has a taller ascender. Its baseline advance is fixed.
    full_ascent = metrics[rendered_lines[0]][2]
    full_height = metrics[rendered_text][1] + (len(lines)-1)*spacing
    full_width = metrics[rendered_text][0]
    origin_x = _origin(align,width,full_width,int(layer.get('x',0)))
    origin_y = _origin(valign,height,full_height,int(layer.get('y',0))+int(layer.get('baseline_dy',0) or 0),True)
    new_layers = []; animation_commands = 0
    for wi, match in enumerate(spans):
        word = match.group(); measured = word.upper() if layer.get('uppercase') else word
        line_index = bisect.bisect_right(line_starts,match.start())-1
        prefix = text[line_starts[line_index]:match.start()]; prefix = prefix.upper() if layer.get('uppercase') else prefix
        box = metrics[measured]; word_height = box[1]
        value = copy.deepcopy(layer)
        value.update(text=word, align='left', x=math.floor(origin_x)+metrics[prefix][0]-round(width*.06),
                     baseline_dy=origin_y + line_index*advance + full_ascent-box[2] - _origin(valign,height,word_height,int(layer.get('y',0)),True),
                     word_of=index)
        entry = copy.deepcopy(incoming)
        entry['delay'] = _number(incoming.get('delay',old_in.get('delay',0)), 'Entrance delay', 0, 600) + wi*stagger
        if incoming.get('type','none') != 'none' and entry['delay']+entry.get('duration',.6) > duration+1e-10:
            raise ValueError('The staggered word entrance exceeds the graphic duration; shorten the animation/stagger or extend the clip')
        if outgoing.get('type','none') != 'none' and outgoing.get('delay',0)+outgoing.get('duration',.6) > duration+1e-10:
            raise ValueError('The outgoing animation exceeds the graphic duration')
        if incoming.get('type','none') != 'none' and outgoing.get('type','none') != 'none' and entry['delay']+entry.get('duration',.6) > duration-outgoing.get('delay',0)-outgoing.get('duration',.6)+1e-10:
            raise ValueError('Word entrance and outgoing animation overlap; shorten the stagger or animations')
        value['anim_in'] = entry
        if 'anim_out' in layer: value['anim_out'] = copy.deepcopy(layer['anim_out'])
        animation_commands += int(incoming.get('type') in ('pop','zoom')) + int(outgoing.get('type') in ('pop','zoom'))
        new_layers.append(value)
    curves, curve_commands = _curves(clip,index,len(spans)-1,len(layers))
    for li,other in enumerate(layers):
        if li!=index:
            for side in ('anim_in','anim_out'):
                animation=other.get(side)
                if isinstance(animation,dict) and animation.get('type') in ('pop','zoom'): animation_commands+=1
    if (math.ceil(duration*fps)+1)*(animation_commands+curve_commands) > MAX_COMMANDS:
        raise ValueError('Split-word animation exceeds 200000 generated control commands; shorten the clip or use simpler motion')
    result = copy.deepcopy(clip)
    result['graphic']['layers'] = copy.deepcopy(layers[:index])+new_layers+copy.deepcopy(layers[index+1:])
    if curves is not None: result['keyframes'] = curves
    summary.update(changed=True, font_path=font_path, affected_fields=['graphic.layers']+(['keyframes'] if curves is not None and curves!=clip.get('keyframes') else []),
                   message=f'Split into {len(spans)} words; following-layer animation kept with its original layer.')
    summary['warnings'].append('Word layers retain the original full-canvas motion origin and clip-local clock. Independent glyph rasterization and overlapping decorations may differ slightly; review the rendered typography.')
    ops = [{'op':'set','path':f'/sequences/{si}/tracks/{ti}/clips/{ci}','value':result}]
    encoded = _bounded({'ops':ops,'summary':summary})
    return {'ops':ops,'summary':summary,'layers':len(spans),'fingerprint':hashlib.sha256(encoded.encode()).hexdigest()}
