"""Pure, bounded plans for reviewed Explainer cards and explicit Hook Variants.

The workflow owns saved context, resource stamps, policy and one Undo commit.
These functions perform no IO and never mutate the supplied project or assets.
"""
import copy
import hashlib
import math
from fractions import Fraction

from analysis_edits import _json
from editing_workflow import transcript_basis
from media_analysis import number
from overlap_normalization import _clock, MAX_CLIPS
from recipe_plans import _bool, _text, _bounded, _budget, _brand, _fit_text
from render import seq_total
from timeline_time import frame_rate, from_frames, to_frames, interpretation_factor

MAX_VARIANTS = 20
MAX_TARGETS = 100
MAX_CHAPTERS = 256
MAX_DEPENDENCIES = 256
MAX_GRAPH_CLIPS = 10_000
MAX_SECONDS = 864_000
RECEIPTS = ('analysis_task', 'transcript_task', 'recipe_tasks', 'sync_tasks', 'audio_tasks', 'render_replace')


def _identity(value, name):
    if not isinstance(value, str) or not value or len(value) > 512:
        raise ValueError(name + ' must be a nonempty ID of at most 512 characters')
    return value


def _graph(project, sid):
    sequences = project.get('sequences')
    if not isinstance(sequences, list) or len(sequences) > 10_000:
        raise ValueError('Recipe needs a bounded sequence library')
    lookup = {}
    for seq in sequences:
        if not isinstance(seq, dict): raise ValueError('Invalid sequence')
        key = _identity(seq.get('id'), 'Sequence')
        if key in lookup: raise ValueError('Duplicate sequence IDs must be repaired before this recipe')
        lookup[key] = seq
    if sid not in lookup: raise ValueError('Choose one existing sequence')
    library = project.get('media') or {}; media = {}; dependencies = {}; visiting = set(); done = set(); count = 0

    def source(mid):
        if mid in media: return media[mid]
        item = library.get(mid)
        if not isinstance(item, dict) or item.get('id') != mid: raise ValueError('A sequence media source is missing or ambiguous')
        _bounded(item); number(item.get('duration'), 'Media duration', minimum=0, maximum=MAX_SECONDS)
        interpretation_factor(item)
        parent_id = item.get('subclip_of')
        if parent_id:
            parent = library.get(parent_id)
            if not isinstance(parent, dict) or parent.get('subclip_of') or parent_id == mid: raise ValueError('Flatten missing or nested subclip parents first')
            source(parent_id)
            offset = number(item.get('sub_in', 0), 'Subclip offset', minimum=0, maximum=MAX_SECONDS)
            if abs(interpretation_factor(item)-interpretation_factor(parent)) > 1e-9 or offset+item['duration'] > parent['duration']+1e-9:
                raise ValueError('Subclip interpretation or source bounds do not match its parent')
        media[mid] = copy.deepcopy(item)
        return item

    def visit(key):
        nonlocal count
        if key in visiting: raise ValueError('A nested sequence dependency contains a cycle')
        if key in done: return
        if key not in lookup: raise ValueError('A nested sequence is missing')
        if len(done)+len(visiting) >= MAX_DEPENDENCIES or len(visiting) >= 32: raise ValueError('The nested sequence graph is too large or deep')
        visiting.add(key); seq = lookup[key]; _bounded(seq)
        frame_rate(seq.get('fps'))
        tracks = seq.get('tracks')
        if not isinstance(tracks, list) or len(tracks) > 1024: raise ValueError('Invalid or excessive sequence tracks')
        track_ids = set(); clip_ids = set()
        for track in tracks:
            if not isinstance(track, dict): raise ValueError('Invalid track')
            tid = _identity(track.get('id'), 'Track')
            if tid in track_ids: raise ValueError('Duplicate track IDs must be repaired before this recipe')
            track_ids.add(tid)
            if track.get('kind') not in ('audio', 'video'): raise ValueError('Unsupported track kind')
            index = number(track.get('index', 0), 'Track index', minimum=0, maximum=1_000_000)
            if index != int(index): raise ValueError('Track index must be an integer')
            clips = track.get('clips')
            if not isinstance(clips, list): raise ValueError('Invalid track clips')
            count += len(clips)
            if count > MAX_GRAPH_CLIPS: raise ValueError('A recipe source graph supports at most 10000 clips')
            for clip in clips:
                if not isinstance(clip, dict): raise ValueError('Invalid clip')
                cid = _identity(clip.get('id'), 'Clip')
                if cid in clip_ids: raise ValueError('Duplicate clip IDs must be repaired before this recipe')
                clip_ids.add(cid); duration = _clock(clip)
                if clip['start']+duration > MAX_SECONDS: raise ValueError('The recipe timeline exceeds ten days')
                if clip.get('sequence_id'):
                    visit(_identity(clip['sequence_id'], 'Nested sequence'))
                if clip.get('media_id'):
                    source(_identity(clip['media_id'], 'Media'))
        if seq.get('duration') is not None: number(seq['duration'], 'Sequence duration', minimum=0, maximum=MAX_SECONDS)
        for field in ('markers', 'captions'):
            values = seq.get(field) or []
            if not isinstance(values, list) or len(values) > 100_000 or any(not isinstance(v, dict) for v in values): raise ValueError('Invalid or excessive '+field)
            ids = [v['id'] for v in values if v.get('id') is not None]
            if any(not isinstance(i, str) or not i for i in ids) or len(ids) != len(set(ids)): raise ValueError('Duplicate or invalid annotation IDs')
        visiting.remove(key); done.add(key)
        if key != sid: dependencies[key] = copy.deepcopy(seq)
    visit(sid)
    return lookup[sid], dependencies, media


def _targets(seq, values):
    if not isinstance(values, list) or not 1 <= len(values) <= MAX_TARGETS: raise ValueError('Choose 1–100 explicit text targets')
    clips = {c['id']: (t, c) for t in seq['tracks'] for c in t['clips']}; targets = []; seen = set()
    for value in values:
        if not isinstance(value, dict) or set(value) not in ({'clip_id', 'layer'}, {'clip_id', 'title'}): raise ValueError('Each text target needs clip_id and either layer or title:true')
        cid = _identity(value['clip_id'], 'Target clip')
        if cid not in clips: raise ValueError('The selected text target no longer exists')
        track, clip = clips[cid]
        if track.get('locked'): raise ValueError('Unlock the selected text target track before making variants')
        if 'layer' in value:
            index = value['layer']; layers = (clip.get('graphic') or {}).get('layers')
            if type(index) is not int or index < 0 or not isinstance(layers, list) or index >= len(layers) or not isinstance(layers[index], dict) or layers[index].get('kind') != 'text': raise ValueError('Choose an existing graphic text layer')
            target = {'clip_id': cid, 'layer': index}; key = (cid, index)
        else:
            if value['title'] is not True or not isinstance(clip.get('title'), dict): raise ValueError('Choose an existing title text target')
            target = {'clip_id': cid, 'title': True}; key = (cid, 'title')
        if key in seen: raise ValueError('Choose each text target only once')
        seen.add(key); targets.append(target)
    return targets


def capture(project, body, mode):
    """Validate options and snapshot every nested/source dependency without IO."""
    if not isinstance(project, dict) or not isinstance(body, dict) or mode not in ('explainer', 'variants'): raise ValueError('Choose Explainer or Hook Variants')
    _bounded(body, 1024*1024)
    sid = _identity(body.get('sequence'), 'Sequence'); seq, dependencies, media = _graph(project, sid)
    width = number(seq.get('width'), 'Sequence width', minimum=16, maximum=16384); height = number(seq.get('height'), 'Sequence height', minimum=16, maximum=16384)
    if width != int(width) or height != int(height): raise ValueError('Sequence dimensions must be integers')
    if mode == 'explainer':
        lower = body.get('lower_third')
        if lower is not None:
            if not isinstance(lower, dict): raise ValueError('Lower third must be an object or none')
            lower = {'name': _text(lower, 'name', maximum=200, required=True), 'role': _text(lower, 'role', maximum=200),
                     'at': number(lower.get('at', 1), 'Lower-third start', minimum=0, maximum=MAX_SECONDS),
                     'duration': number(lower.get('duration', 4.5), 'Lower-third duration', minimum=1e-6, maximum=60)}
        settings = {'lower_third': lower, 'chapters': _bool(body, 'chapters', True), 'end_card': _text(body, 'end_card', maximum=500),
                    'chapter_duration': number(body.get('chapter_duration', 3), 'Chapter duration', minimum=1e-6, maximum=60),
                    'end_duration': number(body.get('end_duration', 3), 'End-card duration', minimum=1e-6, maximum=60)}
        chapters = [m for m in seq.get('markers') or [] if m.get('type') in ('chapter', 'comment') and m.get('name')]
        if settings['chapters'] and len(chapters) > MAX_CHAPTERS: raise ValueError('Explainer supports at most 256 named chapter/comment markers')
        for marker in chapters if settings['chapters'] else []:
            number(marker.get('time'), 'Chapter time', minimum=0, maximum=MAX_SECONDS)
            _text(marker, 'name', maximum=500, required=True)
    else:
        hooks = body.get('hooks')
        if not isinstance(hooks, list) or not 1 <= len(hooks) <= MAX_VARIANTS: raise ValueError('Enter 1–20 hook headlines')
        hooks = [_text({'hook': h}, 'hook', maximum=500, required=True) for h in hooks]
        settings = {'hooks': hooks, 'targets': _targets(seq, body.get('targets')),
                    'name_prefix': _text(body, 'name_prefix', str(seq.get('name') or 'Sequence')[:120], maximum=120, required=True)}
        local = {c['id'] for t in seq['tracks'] for c in t['clips']}
        for track in seq['tracks']:
            for clip in track['clips']:
                for field in ('audio_detached_id', 'unlinked_from'):
                    if clip.get(field) and clip[field] not in local: raise ValueError('Restore missing or external detached audio associations before making variants')
    return _bounded({'version': 1, 'mode': mode, 'sequence': sid, 'clip_id': None, 'settings': settings, 'fps': seq['fps'], 'width': int(width), 'height': int(height),
                     'sequence_basis': copy.deepcopy(seq), 'sequence_dependencies': dependencies, 'media_ids': list(media), 'media_basis': media, 'brand': copy.deepcopy(project.get('brand') or {})})


def _frame(value, fps, *, up=False):
    nearest = to_frames(value, fps)
    if abs(from_frames(nearest, fps)-value) <= max(1e-12, 8*math.ulp(value)): return nearest
    exact = Fraction(str(value))*frame_rate(fps)
    return math.ceil(exact) if up else math.floor(exact)


def _duration(seq):
    # The renderer defaults an entirely empty sequence to a one-second canvas;
    # this editing command must not invent content in an empty timeline.
    return seq_total(seq) if seq.get('duration') or any(t['clips'] for t in seq['tracks']) else 0.0


def _card(payload, kind, text, sub, start, end, budget):
    template = (payload.get('templates') or {}).get(kind)
    if not isinstance(template, dict) or not isinstance(template.get('layers'), list) or len(template['layers']) > 100 or any(not isinstance(layer, dict) for layer in template['layers']): raise ValueError('The captured '+kind+' template is missing or invalid')
    brand = {'primary': '#E8631C', 'secondary': '#7A2E9E', 'text': '#FFFFFF', 'font': '', **payload['brand']}
    layers = _brand(template['layers'], brand); texts = [layer for layer in layers if layer.get('kind') == 'text']
    if len(texts) < (1 if kind == 'cta' else 2): raise ValueError('The captured '+kind+' template lacks its required text fields')
    for i, layer in enumerate(texts):
        value = text if kind == 'cta' or i == 0 else sub if i == 1 else layer.get('text', '')
        layer['text'], layer['size'] = _fit_text(value, number(layer.get('size', 64), 'Text size', minimum=1, maximum=2048), payload['width'])
    duration = end-start
    # Short cards keep animation phases inside their actual duration.
    for layer in layers:
        for field in ('anim_in', 'anim_out'):
            animation = layer.get(field)
            if isinstance(animation, dict):
                delay = number(animation.get('delay', 0), 'Animation delay', minimum=0, maximum=60)
                length = number(animation.get('duration', .3), 'Animation duration', minimum=0, maximum=60)
                animation['delay'] = min(delay, duration/4)
                animation['duration'] = min(length, max(0, duration/2-animation['delay']))
    return {'id': budget.id(), 'media_id': None, 'start': start, 'in_': 0, 'out': duration, 'speed': 1,
            'graphic': {'name': {'lower_third': 'Lower third', 'chapter': 'Chapter', 'cta': 'End card'}[kind], 'layers': layers},
            'transform': {'opacity': 1}, 'keyframes': {}, 'note': 'explainer '+kind}


def _explainer(project, payload, budget):
    seq = payload['sequence_basis']; settings = payload['settings']; fps = payload['fps']; total = _duration(seq); end_frame = _frame(total, fps)
    warnings = []; placements = []; tracks = []; requests = {'chapter': [], 'lower_third': [], 'cta': []}
    locked = [t['id'] for t in seq['tracks'] if t.get('locked')]
    if locked: warnings.append('Original locked tracks are preserved without editing: '+', '.join(locked))
    if settings['lower_third']:
        lower = settings['lower_third']; start = _frame(lower['at'], fps, up=True); end = min(end_frame, _frame(lower['at']+lower['duration'], fps))
        if end <= start: raise ValueError('The lower third has no complete picture frame inside the existing sequence')
        requests['lower_third'].append((start, end, lower['name'], lower['role']))
        if from_frames(end-start, fps) < lower['duration']-1e-9: warnings.append('The lower third is shortened to fit whole frames inside the existing sequence.')
    if settings['chapters']:
        chapters = sorted([m for m in seq.get('markers') or [] if m.get('type') in ('chapter', 'comment') and m.get('name')], key=lambda m: m['time'])
        starts = [_frame(m['time'], fps, up=True) for m in chapters]
        for index, marker in enumerate(chapters):
            start = starts[index]; end = min(end_frame, _frame(marker['time']+settings['chapter_duration'], fps), starts[index+1] if index+1 < len(starts) else end_frame)
            if end <= start:
                warnings.append('Skipped chapter '+str(index+1)+' because no full frame remains before the next chapter or sequence end.'); continue
            if from_frames(end-start, fps) < settings['chapter_duration']-1e-9: warnings.append('Chapter '+str(index+1)+' is shortened to fit its available whole frames.')
            requests['chapter'].append((start, end, f'{index+1:02d}', marker['name']))
    if settings['end_card']:
        start = max(0, _frame(total-settings['end_duration'], fps, up=True)); end = end_frame
        if end <= start: raise ValueError('The end card has no complete picture frame inside the existing sequence')
        requests['cta'].append((start, end, settings['end_card'], ''))
        if from_frames(end-start, fps) < settings['end_duration']-1e-9: warnings.append('The end card is shortened to fit whole frames inside the existing sequence.')
    used_names = {t.get('name') for t in seq['tracks']}; index = max([t.get('index', 0) for t in seq['tracks'] if t['kind'] == 'video']+[-1])+1
    for kind, values in requests.items():
        if not values: continue
        stem = {'chapter': 'Explainer chapters', 'lower_third': 'Explainer lower thirds', 'cta': 'Explainer end cards'}[kind]; name = stem; suffix = 2
        while name in used_names: name = stem+' '+str(suffix); suffix += 1
        used_names.add(name); track = {'id': budget.id(), 'name': name, 'kind': 'video', 'index': index, 'locked': False, 'muted': False, 'clips': []}; index += 1
        for a, b, text, sub in values:
            start, end = from_frames(a, fps), from_frames(b, fps); clip = _card(payload, kind, text, sub, start, end, budget); track['clips'].append(clip)
            placements.append({'id': clip['id'], 'track': track['id'], 'kind': kind, 'start': start, 'end': end, 'text': text, 'sub': sub})
        tracks.append(track)
    if any(a['kind'] != b['kind'] and min(a['end'], b['end']) > max(a['start'], b['start'])+1e-12 for i, a in enumerate(placements) for b in placements[i+1:]):
        warnings.append('Cards overlap across separate upper tracks; review their visual layering. End cards are above lower thirds and chapter cards.')
    if placements: warnings.append('Text fitting is approximate; review the cards visually. Short card animations are bounded to their duration.')
    ops = []
    if tracks:
        result = budget.clone(seq); result['tracks'].extend(tracks)
        if seq.get('transcript_basis') and seq['transcript_basis'] == transcript_basis(seq, project):
            result['transcript_basis'] = transcript_basis(result, project)
        si = next(i for i, item in enumerate(project['sequences']) if item['id'] == seq['id'])
        ops = [{'op': 'set', 'path': f'/sequences/{si}', 'value': result}]
    return ops, {'sequence': seq['id'], 'sequence_id': seq['id'], 'name': seq.get('name', ''), 'cards': len(placements), 'placements': placements,
                 'variants': [], 'tracks': [t['id'] for t in tracks], 'locked_tracks': locked, 'cuts': [], 'ranges': [[p['start'], p['end']] for p in placements],
                 'requested': total, 'achieved': total, 'warnings': warnings, 'affected_fields': ['new graphics tracks'] if tracks else [],
                 'message': f'Add {len(placements)} Explainer card(s) on {len(tracks)} new upper track(s).' if tracks else 'No eligible Explainer cards; the sequence is unchanged.'}


def _variant_copy(seq, project, hook, name, targets, budget):
    result = budget.clone(seq); clip_map = {}; track_map = {}; marker_map = {}; caption_map = {}; groups = {}; warnings = []
    result['id'] = budget.id(); result['name'] = name; result['variant_of'] = seq['id']; result['variant_hook'] = hook
    for track in result['tracks']:
        old = track['id']; track['id'] = budget.id(); track_map[old] = track['id']
        for clip in track['clips']:
            old = clip['id']; clip['id'] = budget.id(); clip_map[old] = clip['id']
            if clip.get('group'):
                _identity(clip['group'], 'Group')
                if clip['group'] not in groups: groups[clip['group']] = budget.id()
                clip['group'] = groups[clip['group']]
            for marker in clip.get('markers') or []:
                if marker.get('id') is not None: marker['id'] = budget.id()
    for key, mapping in (('markers', marker_map), ('captions', caption_map)):
        for item in result.get(key) or []:
            if item.get('id') is not None: old = item['id']; item['id'] = budget.id(); mapping[old] = item['id']
    # ID values are only meaningful within their editing scope. Nested clips
    # carry sequence_id as a *source*, whereas reference objects can bind their
    # clip_id to another sequence with the same local ID.
    cloned_clip_ids = set(clip_map.values())
    def references(value, inherited_scope=seq['id']):
        if isinstance(value, list):
            for item in value: references(item, inherited_scope)
        elif isinstance(value, dict):
            is_clip = value.get('id') in cloned_clip_ids
            scope = inherited_scope if is_clip else value.get('sequence', value.get('sequence_id', inherited_scope))
            source_scope = value.get('source_sequence_id', scope)
            target_scope = value.get('target_sequence_id', scope)
            for key, item in list(value.items()):
                if key in ('source_edit_window', 'rendered_from', 'provenance', 'source_provenance'):
                    continue  # Historical source coordinates/identity are not edit pointers.
                mapping = (clip_map if key in ('clip_id', 'source_clip_id', 'target_clip_id') else
                           track_map if key in ('track_id', 'source_track_id', 'target_track_id') else
                           marker_map if key == 'marker_id' else caption_map if key == 'caption_id' else None)
                field_scope = source_scope if key.startswith('source_') else target_scope if key.startswith('target_') else scope
                if mapping is not None and isinstance(item, str):
                    if field_scope == seq['id'] and item in mapping: value[key] = mapping[item]
                elif key in ('sequence', 'sequence_id') and not is_clip and item == seq['id']:
                    value[key] = result['id']
                elif key in ('source_sequence_id', 'target_sequence_id') and key.replace('sequence_id', 'clip_id') in value and item == seq['id']:
                    value[key] = result['id']
                elif key in ('clip_ids', 'track_ids', 'marker_ids', 'caption_ids') and isinstance(item, list):
                    if scope == seq['id']:
                        mapping = {'clip_ids': clip_map, 'track_ids': track_map, 'marker_ids': marker_map, 'caption_ids': caption_map}[key]
                        value[key] = [mapping.get(i, i) if isinstance(i, str) else i for i in item]
                elif key.endswith('_id') and key not in ('media_id', 'sequence_id', 'source_sequence_id', 'target_sequence_id', 'audio_detached_id') and scope == seq['id'] and isinstance(item, str) and (item in clip_map or item in track_map or item in marker_map or item in caption_map):
                    raise ValueError('Unsupported local reference '+key+'; resolve it before making variants')
                else: references(item, scope)
    references(result)
    for track in result['tracks']:
        for clip in track['clips']:
            for key in ('audio_detached_id', 'unlinked_from'):
                if clip.get(key): clip[key] = clip_map[clip[key]]
            # A bake's media and full source-edit clock remain valid in a copy;
            # its task receipt belongs to the original edit, not the new clip.
            clip.pop('render_replace_task', None)
            for fx in clip.get('fx_stack') or []:
                if fx.get('type') == 'track_matte':
                    params = fx.get('params') or {}; target = params.get('track', 'V2')
                    if target not in track_map: raise ValueError('Repair missing track-matte references before making variants')
                    fx['params'] = {**params, 'track': track_map[target]}
    if result.get('multicam_audio_track') is not None:
        if result['multicam_audio_track'] not in track_map: raise ValueError('Repair the multicam audio-track reference before making variants')
        result['multicam_audio_track'] = track_map[result['multicam_audio_track']]
    workflow = result.get('workflow')
    if workflow is not None:
        if not isinstance(workflow, dict): raise ValueError('Invalid sequence workflow metadata')
        for key in RECEIPTS: workflow.pop(key, None)
        if 'caption_review_ids' in workflow:
            old_ids = workflow['caption_review_ids']
            if not isinstance(old_ids, list) or any(v not in caption_map for v in old_ids): raise ValueError('Repair missing caption-review references before making variants')
            workflow['caption_review_ids'] = [caption_map[v] for v in old_ids]
    copies = {c['id']: c for t in result['tracks'] for c in t['clips']}
    for target in targets:
        clip = copies[clip_map[target['clip_id']]]
        layer = clip['title'] if target.get('title') else clip['graphic']['layers'][target['layer']]
        layer['text'], layer['size'] = _fit_text(hook, number(layer.get('size', 64), 'Text size', minimum=1, maximum=2048), seq['width'])
    if seq.get('transcript_basis'):
        if seq['transcript_basis'] == transcript_basis(seq, project): result['transcript_basis'] = transcript_basis(result, project)
        else: warnings.append('The source transcript is stale; copied word timings remain stale and must be retranscribed before text editing.')
    return result, warnings


def _variants(project, payload, budget):
    seq = payload['sequence_basis']; settings = payload['settings']; ops = []; variants = []; warnings = []; tracks = []
    if budget.count+len(settings['hooks'])*sum(len(t['clips']) for t in seq['tracks']) > MAX_CLIPS: raise ValueError('Recipe would create too many clips')
    if payload['sequence_dependencies']: warnings.append('Nested sequences remain shared dependencies; editing a nested source later affects the original and its variants.')
    for index, hook in enumerate(settings['hooks']):
        name = f"{settings['name_prefix']} — V{index+1}: {hook[:28]}"
        result, notices = _variant_copy(seq, project, hook, name, settings['targets'], budget); warnings.extend(notices)
        ops.append({'op': 'insert', 'path': '/sequences/'+str(len(project['sequences'])+index), 'value': result})
        variants.append({'id': result['id'], 'name': name, 'hook': hook, 'replaced': len(settings['targets'])}); tracks.extend(t['id'] for t in result['tracks'])
    warnings.extend(['Only the explicitly chosen text targets change; captions and unrelated text are preserved.', 'Text fitting is approximate; review each variant visually.', 'Copy identities and local associations are remapped; applied-task receipts are cleared in copies.'])
    total = _duration(seq)
    return ops, {'sequence': variants[0]['id'], 'sequence_id': variants[0]['id'], 'name': variants[0]['name'], 'variants': variants, 'cards': 0, 'placements': [],
                 'targets': copy.deepcopy(settings['targets']), 'tracks': tracks, 'cuts': [], 'ranges': [], 'requested': total, 'achieved': total,
                 'warnings': list(dict.fromkeys(warnings)), 'affected_fields': ['new sequences', 'selected text', 'copy IDs and associations', 'copy task receipts'],
                 'message': f"Create {len(variants)} variant sequence(s), changing {len(settings['targets'])} explicit text target(s) in each."}


def plan(project, payload, result, identity=''):
    if not isinstance(payload, dict) or not isinstance(result, dict) or result.get('version') != 1 or result.get('kind') != 'recipe' or result.get('mode') != payload.get('mode') or result.get('signature') != payload.get('signature'): raise ValueError('Recipe result does not match its captured input')
    _bounded(payload); _bounded(result)
    current = capture(project, {**payload['settings'], 'sequence': payload['sequence']}, payload['mode'])
    for key in ('sequence_basis', 'sequence_dependencies', 'settings', 'fps', 'width', 'height', 'brand', 'media_ids'):
        if current[key] != payload.get(key): raise ValueError('A recipe dependency changed; start a fresh recipe')
    fields = ('id', 'path', 'duration', 'subclip_of', 'sub_in', 'sub_out', 'fps', 'frame_rate', 'interpret_fps', 'has_audio', 'has_video', 'is_image', 'synthetic', 'channels', 'sample_rate')
    for mid in current['media_ids']:
        old = payload.get('media_basis', {}).get(mid)
        if not isinstance(old, dict) or any(current['media_basis'][mid].get(key) != old.get(key) for key in fields): raise ValueError('A recipe source dependency changed')
    budget = _budget(project, identity, payload)
    ops, summary = (_explainer if payload['mode'] == 'explainer' else _variants)(project, payload, budget)
    _bounded(ops)
    count = budget.count
    for op in ops:
        added = sum(len(t['clips']) for t in op['value']['tracks'])
        previous = 0 if op['op'] == 'insert' else sum(len(t['clips']) for t in project['sequences'][int(op['path'].split('/')[2])]['tracks'])
        count += added-previous
    if count > MAX_CLIPS: raise ValueError('Recipe would create too many clips')
    summary = {'kind': 'recipe', 'mode': payload['mode'], **summary}
    return {'ops': ops, 'summary': summary, 'fingerprint': hashlib.sha256(_json({'identity': identity, 'ops': ops, 'summary': summary}).encode()).hexdigest()}
