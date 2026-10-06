"""Non-destructive, framework-independent builders for the guided editing workspace.

Builders never save files or mutate their input. The server commits the complete
result with a captured project revision and one history entry.
"""
import copy
import math
import uuid
import hashlib
import json
from bisect import bisect_right
import unicodedata

from timeline_time import frame_rate
from render import chunk_sequence, index_sequence, seq_total


def uid():
    return uuid.uuid4().hex[:16]


def number(value, label, lo, hi):
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number")
    try:
        value = float(value)
    except (ValueError, TypeError):
        raise ValueError(f"{label} must be a number") from None
    if not math.isfinite(value) or not lo <= value <= hi:
        raise ValueError(f"{label} must be between {lo} and {hi}")
    return value


def sequence(project, sid):
    result = next((s for s in project.get('sequences', []) if s['id'] == sid), None)
    if result is None:
        raise ValueError('The selected sequence no longer exists')
    return result


def name_of(body, default):
    name = body.get('name', default)
    if not isinstance(name, str) or not name.strip() or len(name) > 160:
        raise ValueError('Enter a sequence name of 1–160 characters')
    return name.strip()


def transcript_basis(seq, project=None, seen=None):
    fields = ('id', 'media_id', 'sequence_id', 'start', 'in_', 'out', 'speed', 'time_remap', 'hold', 'reverse', 'enabled')
    timing = [(t['id'], t.get('muted', False), [{k: c.get(k) for k in fields} for c in t['clips']]) for t in seq['tracks']]
    nested = {}
    if project is not None:
        seen = set(seen or ())
        if seq['id'] in seen: raise ValueError('A nested sequence refers to itself')
        seen.add(seq['id'])
        for track in seq['tracks']:
            for clip in track['clips']:
                sid = clip.get('sequence_id')
                if sid and sid not in nested:
                    nested[sid] = transcript_basis(sequence(project, sid), project, seen)
    return hashlib.sha256(json.dumps({'tracks': timing, 'nested': nested}, sort_keys=True).encode()).hexdigest()


def words_of(seq, project=None):
    words = seq.get('transcript') or []
    if not isinstance(words, list) or not words:
        raise ValueError('Transcribe this sequence first')
    if seq.get('transcript_basis') and seq['transcript_basis'] != transcript_basis(seq, project):
        raise ValueError('The timeline changed after transcription. Transcribe this sequence again before editing by text.')
    previous = -1
    for word in words:
        start = number(word.get('s'), 'Word start', 0, 864000)
        end = number(word.get('e'), 'Word end', start, 864000)
        if start < previous or end <= start or not isinstance(word.get('w'), str):
            raise ValueError('Transcript timings are invalid; transcribe the sequence again')
        previous = start
    return words


def phrases(words, max_words=12, gap=.65):
    """Stable word-index groups for the story picker; punctuation and pauses split."""
    result, current = [], []
    for i, word in enumerate(words):
        if current and (len(current) >= max_words or word['s'] - words[current[-1]]['e'] > gap):
            result.append(current); current = []
        current.append(i)
        if word['w'].rstrip().endswith(('.', '?', '!')):
            result.append(current); current = []
    if current:
        result.append(current)
    return result


def fresh_sequence(seq, name, source=None):
    seq['id'], seq['name'] = uid(), name
    seq['duration'] = None
    seq.pop('in_point', None); seq.pop('out_point', None)
    # These fields describe the old edit and must not survive a derived cut.
    seq.pop('workflow', None)
    seq['workflow'] = {'source_sequence': source} if source else {}
    return seq


def assemble(project, body):
    ids = body.get('media_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 500 or any(not isinstance(x, str) for x in ids) or len(set(ids)) != len(ids):
        raise ValueError('Choose 1–500 different media items in story order')
    dims = {'landscape': (1920, 1080), 'portrait': (1080, 1920), 'square': (1080, 1080)}
    if body.get('format', 'landscape') not in dims:
        raise ValueError('Choose landscape, portrait, or square')
    width, height = dims[body.get('format', 'landscape')]
    fps = float(frame_rate(number(body.get('fps', 30), 'Frame rate', 1, 120)))
    seq = fresh_sequence({'width': width, 'height': height, 'fps': fps, 'markers': [], 'captions': [],
        'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [], 'muted': False, 'locked': False},
                   {'id': 'A1', 'kind': 'audio', 'index': 1, 'clips': [], 'muted': False, 'locked': False}]}, name_of(body, 'Rough cut'))
    cursor = 0
    for mid in ids:
        media = project.get('media', {}).get(mid)
        if not media or not (media.get('has_video') or media.get('has_audio')):
            raise ValueError('A selected media item is missing or has no playable streams')
        duration = number(media.get('duration'), 'Media duration', .001, 864000)
        clip = {'id': uid(), 'media_id': mid, 'start': round(cursor, 6), 'in_': 0, 'out': duration,
                'speed': 1, 'fit': 'contain', 'transform': {'scale': 1, 'x': 0, 'y': 0, 'opacity': 1},
                'audio': {'gain_db': 0, 'linked': True}, 'keyframes': {}}
        seq['tracks'][0 if media.get('has_video') else 1]['clips'].append(clip)
        cursor += duration
    seq['workflow']['assembled'] = True
    project['sequences'].append(seq)
    return seq, f'Created {seq["name"]} from {len(ids)} items'


def story_cut(project, body):
    source = sequence(project, body.get('sequence'))
    words = words_of(source, project)
    selected = body.get('words')
    if not isinstance(selected, list) or not selected or any(type(i) is not int or not 0 <= i < len(words) for i in selected):
        raise ValueError('Select transcript passages to keep')
    selected = sorted(set(selected))
    selected_set = set(selected)
    padding = number(body.get('padding', .08), 'Cut padding', 0, .5)
    # Chunk trimming is exact for constant-speed clips. Reject unsupported edits
    # instead of silently damaging retiming, multicam decisions or frozen frames.
    for track in source['tracks']:
        for clip in track['clips']:
            if any(clip.get(k) for k in ('time_remap', 'reverse', 'hold', 'multicam')):
                raise ValueError('Story cuts need constant-speed clips. Make the story cut before speed ramps, reverse, frame holds, or multicam edits.')
    duration = seq_total(source)
    ranges, range_words = [], []
    groups = []
    for i in selected:
        if groups and i == groups[-1][-1] + 1:
            groups[-1].append(i)
        else:
            groups.append([i])
    for group in groups:
        first, last = words[group[0]]['s'], max(words[i]['e'] for i in group)
        start, end = max(0, first - padding), min(duration, last + padding)
        if end <= start:
            raise ValueError('Transcript extends beyond the sequence; transcribe it again')
        # Do not bring an explicitly excluded adjacent word back through padding.
        if group[0] > 0 and group[0] - 1 not in selected_set:
            start = max(start, words[group[0] - 1]['e'])
        if group[-1] + 1 < len(words) and group[-1] + 1 not in selected_set:
            end = min(end, words[group[-1] + 1]['s'])
        if end <= start:
            raise ValueError('Selected words overlap excluded speech; keep the whole passage or trim it on the timeline')
        if start > first + 1e-6 or end < last - 1e-6:
            raise ValueError('Selected speech overlaps excluded speech or the end of the timeline. Keep the overlapping words together or trim on the timeline.')
        if ranges and start <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], end)
            range_words[-1].extend(group)
        else:
            ranges.append([start, end])
            range_words.append(group)
    result = fresh_sequence(copy.deepcopy(source), name_of(body, source['name'] + ' · Story'), source['id'])
    for track in result['tracks']:
        track['clips'] = []
    result['captions'], result['markers'], result['transcript'] = [], [], []
    # Slicing only needs tracks. Keeping a long transcript/caption history in
    # the index metadata used to copy that entire history for every passage.
    slice_source = {'id': source['id'], 'tracks': source['tracks'], 'captions': []}
    index, cursor = index_sequence(slice_source), 0
    markers = [[] for _ in ranges]
    starts = [start for start, _ in ranges]
    for marker in source.get('markers', []):
        at = marker.get('time', marker.get('t', -1))
        j = bisect_right(starts, at) - 1
        if j >= 0 and at < ranges[j][1]: markers[j].append(marker)
    for j, (start, end) in enumerate(ranges):
        chunk = chunk_sequence(slice_source, start, end, index=index)
        groups_map = {}
        for target, track in zip(result['tracks'], chunk['tracks']):
            for clip in track['clips']:
                clip['id'] = uid(); clip['start'] = round(clip['start'] + cursor, 6)
                if clip.get('group'):
                    clip['group'] = groups_map.setdefault(clip['group'], uid())
                target['clips'].append(clip)
        for i in range_words[j]:
            word = words[i]
            result['transcript'].append(dict(word, s=round(max(start, word['s']) - start + cursor, 6), e=round(min(end, word['e']) - start + cursor, 6)))
        for marker in markers[j]:
            at = marker.get('time', marker.get('t', -1))
            if start <= at < end:
                mapped = dict(marker, id=uid(), time=round(at - start + cursor, 6))
                mapped.pop('t', None)
                if mapped.get('duration'): mapped['duration'] = min(mapped['duration'], end - at)
                result['markers'].append(mapped)
        cursor += end - start
    # Captions copied from the original may contain excluded words. Regenerate
    # them from the retained transcript in the captions step instead.
    result['captions'] = []
    result['transcript_basis'] = transcript_basis(result, project)
    result['workflow'].update(story=True, source_ranges=ranges)
    project['sequences'].append(result)
    return result, f'Created {result["name"]}: {len(selected)} words, {cursor:.1f}s'


def transcript_edit(project, body):
    seq = sequence(project, body.get('sequence'))
    words = words_of(seq, project)
    edits = body.get('edits')
    if not isinstance(edits, list) or not 1 <= len(edits) <= 500:
        raise ValueError('Correct 1–500 words per save')
    changed, seen = {}, set()
    for edit in edits:
        if not isinstance(edit, dict): raise ValueError('Expected word corrections')
        i, text = edit.get('index'), edit.get('text')
        if type(i) is not int or not 0 <= i < len(words) or i in seen:
            raise ValueError('A word index is missing, repeated or outside this transcript')
        seen.add(i)
        if edit.get('expected') != words[i]['w']:
            raise ValueError('The original word changed. Refresh and review your correction.')
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > 120 or any(c.isspace() for c in text.strip()) or any(unicodedata.category(c) in ('Cc', 'Cs') for c in text):
            raise ValueError('Use one word of 1–120 characters per correction; timings stay unchanged')
        text = text.strip()
        if text != words[i]['w']: changed[i] = text
    valid_ids = {c['id'] for c in seq.get('captions', [])}
    review = set(seq.get('workflow', {}).get('caption_review_ids', [])) & valid_ids
    updated = 0
    for caption in seq.get('captions', []):
        group = caption.get('workflow_words')
        tracked = (isinstance(group, list) and group and all(type(i) is int and 0 <= i < len(words) for i in group))
        linked_changes = tracked and any(i in changed for i in group)
        affected = linked_changes or any(words[i]['s'] < caption['end'] and words[i]['e'] > caption['start'] for i in changed)
        if not affected: continue
        text_matches = tracked and caption.get('workflow_text') == caption['text'] == ' '.join(words[i]['w'].strip() for i in group)
        times_match = tracked and caption['start'] == words[group[0]]['s'] and caption['end'] == words[group[-1]]['e']
        if text_matches and times_match:
            if linked_changes:
                caption['text'] = caption['workflow_text'] = ' '.join(changed.get(i, words[i]['w']).strip() for i in group)
                updated += 1
            # An earlier review requirement stays until a human reviews it.
        else:
            review.add(caption['id'])
    for i, text in changed.items(): words[i]['w'] = text
    if changed:
        seq.setdefault('workflow', {})['caption_review_ids'] = sorted(review & valid_ids)
    message = f'Corrected {len(changed)} words; timings preserved. Updated {updated} linked captions.'
    if review: message += f' Review {len(review)} captions with independent edits.'
    return seq, message, {'words_changed': len(changed), 'captions_updated': updated, 'captions_to_review': sorted(review)}


def caption_review(project, body):
    seq = sequence(project, body.get('sequence'))
    ids = body.get('caption_ids')
    current = set(seq.get('workflow', {}).get('caption_review_ids', []))
    if not isinstance(ids, list) or not ids or any(not isinstance(i, str) for i in ids) or not set(ids) <= current:
        raise ValueError('Choose the captions you reviewed')
    remaining = current - set(ids)
    seq.setdefault('workflow', {})['caption_review_ids'] = sorted(remaining)
    return seq, f'Marked {len(set(ids))} captions reviewed'


def cleanup(project, body):
    seq = sequence(project, body.get('sequence'))
    ids = body.get('clip_ids')
    if not isinstance(ids, list) or not ids or any(not isinstance(x, str) for x in ids):
        raise ValueError('Choose dialogue clips first')
    cutoff = number(body.get('highpass', 80), 'Low-cut frequency', 40, 180)
    reduction = number(body.get('denoise', 0), 'Noise reduction', 0, 18)
    ratio = number(body.get('ratio', 2), 'Compression ratio', 1, 6)
    targets = [(t, c) for t in seq['tracks'] for c in t['clips'] if c['id'] in ids]
    if len(targets) != len(set(ids)):
        raise ValueError('A dialogue clip no longer exists')
    for track, clip in targets:
        media = project.get('media', {}).get(clip.get('media_id'), {})
        soloed_out = any(t.get('solo') for t in seq['tracks']) and not track.get('solo')
        unlinked = track.get('kind') == 'video' and clip.get('audio', {}).get('linked') is False
        if track.get('locked') or track.get('muted') or soloed_out or unlinked or clip.get('hold') or clip.get('enabled') is False or not media.get('has_audio') or media.get('synthetic') or clip.get('audio', {}).get('mute'):
            raise ValueError('Choose unlocked, audible media clips with audio')
        effects = [{'type': 'highpass', 'params': {'frequency': cutoff}}]
        if reduction:
            effects.append({'type': 'denoise', 'params': {'reduction_db': reduction}})
        effects.append({'type': 'compressor', 'params': {'threshold_db': -18, 'ratio': ratio, 'attack_ms': 20, 'release_ms': 200, 'makeup_db': 0}})
        for effect in effects:
            effect.update(id=uid(), enabled=True, workflow='dialogue')
        clip['afx_stack'] = [f for f in clip.get('afx_stack', []) if f.get('workflow') != 'dialogue'] + effects
    seq.setdefault('workflow', {})['dialogue'] = True
    return seq, f'Updated editable dialogue effects on {len(targets)} clips'


def captions(project, body):
    seq = sequence(project, body.get('sequence'))
    words = words_of(seq, project)
    limit = int(number(body.get('max_words', 7), 'Words per caption', 2, 16))
    style = body.get('style', 'clean')
    if style not in ('clean', 'boxed', 'highlight'):
        raise ValueError('Choose a caption style')
    caps = []
    for group in phrases(words, limit):
        first, last = words[group[0]], words[group[-1]]
        text = ' '.join(words[i]['w'].strip() for i in group)
        caps.append({'id': uid(), 'start': first['s'], 'end': last['e'], 'text': text,
                     'workflow_words': group, 'workflow_text': text})
    seq['captions'] = caps
    seq['caption_style'] = {'size': round(min(seq['width'], seq['height']) * .045), 'y': .82,
                            'color': 'white', 'borderw': 2, 'box': style == 'boxed',
                            'animate': 'highlight' if style == 'highlight' else '', 'highlight_color': '#FFD84A'}
    seq.setdefault('workflow', {})['captions'] = True
    seq['workflow']['caption_review_ids'] = []
    return seq, f'Created {len(caps)} editable captions'


def versions(project, body):
    source = sequence(project, body.get('sequence'))
    formats = body.get('formats')
    sizes = {'portrait': (1080, 1920), 'square': (1080, 1080), 'landscape': (1920, 1080)}
    if not isinstance(formats, list) or not formats or any(not isinstance(f, str) or f not in sizes for f in formats) or len(set(formats)) != len(formats):
        raise ValueError('Choose different delivery formats')
    fit = body.get('fit', 'contain')
    if fit not in ('contain', 'cover'):
        raise ValueError('Choose fit or fill')
    if not any(t['clips'] for t in source['tracks']):
        raise ValueError('Add footage before creating versions')
    # Each version nests its own editable copy. This preserves titles, masks,
    # keyframes, color and mix in the original coordinate system, while the
    # outer clip provides a reversible fit/fill framing control.
    ids = []
    transcript = []
    if source.get('transcript'):
        try: transcript = words_of(source, project)
        except ValueError: pass  # Existing caption edits remain; stale word timings do not.
    for fmt in formats:
        clones, visiting = {}, set()
        def clone_tree(original):
            if original['id'] in visiting:
                raise ValueError('A nested sequence refers to itself; fix the nesting before creating versions')
            if original['id'] in clones:
                return clones[original['id']]
            visiting.add(original['id'])
            cloned = fresh_sequence(copy.deepcopy(original), original['name'] + ' · ' + fmt + ' content', original['id'])
            cloned['duration'] = original.get('duration')
            group_ids = {}
            for track in cloned['tracks']:
                for clip in track['clips']:
                    clip['id'] = uid()
                    if clip.get('group'): clip['group'] = group_ids.setdefault(clip['group'], uid())
                    if clip.get('sequence_id'):
                        clip['sequence_id'] = clone_tree(sequence(project, clip['sequence_id']))['id']
            child_review = set(original.get('workflow', {}).get('caption_review_ids', []))
            cloned['workflow']['caption_review_ids'] = []
            for caption in cloned.get('captions', []):
                old_id, caption['id'] = caption['id'], uid()
                if old_id in child_review: cloned['workflow']['caption_review_ids'].append(caption['id'])
            for marker in cloned.get('markers', []): marker['id'] = uid()
            if cloned.get('transcript_basis'):
                if original['transcript_basis'] == transcript_basis(original, project):
                    cloned['transcript_basis'] = transcript_basis(cloned, project)
                else: cloned['transcript'] = []; cloned.pop('transcript_basis', None)
            visiting.remove(original['id']); clones[original['id']] = cloned
            project['sequences'].append(cloned)
            return cloned
        snapshot = clone_tree(source)
        snapshot['captions'] = []  # captions stay legible in the output dimensions
        snapshot['workflow']['caption_review_ids'] = []
        snapshot['transcript'] = []
        snapshot.pop('transcript_basis', None)
        width, height = sizes[fmt]
        result = fresh_sequence({'width': width, 'height': height, 'fps': source['fps'], 'timecode_format': source.get('timecode_format', 'ndf'), 'markers': copy.deepcopy(source.get('markers', [])),
            'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'locked': False, 'muted': False,
                'clips': [{'id': uid(), 'sequence_id': snapshot['id'], 'media_id': None, 'start': 0, 'in_': 0, 'out': seq_total(source),
                           'speed': 1, 'fit': fit, 'transform': {'x': 0, 'y': 0, 'scale': 1, 'opacity': 1}, 'audio': {'gain_db': 0, 'linked': True}, 'keyframes': {}}]}],
            'captions': copy.deepcopy(source.get('captions', [])), 'transcript': copy.deepcopy(transcript),
            'caption_style': copy.deepcopy(source.get('caption_style', {}))}, source['name'] + ' · ' + fmt, source['id'])
        review_ids = set(source.get('workflow', {}).get('caption_review_ids', []))
        copied_review_ids = []
        for caption in result['captions']:
            old_id, caption['id'] = caption['id'], uid()
            if old_id in review_ids: copied_review_ids.append(caption['id'])
        if result['caption_style'].get('size'):
            result['caption_style']['size'] *= min(width, height) / min(source['width'], source['height'])
        result['workflow'].update(version=fmt, content_sequence=snapshot['id'])
        result['workflow']['caption_review_ids'] = copied_review_ids
        result['transcript_basis'] = transcript_basis(result, project)
        project['sequences'].append(result); ids.append(result['id'])
    message = f'Created {len(ids)} independent versions; review framing before export'
    if source.get('transcript') and not transcript: message += '. Outdated word timings were omitted; transcribe the version again to regenerate captions'
    return sequence(project, ids[0]), message, ids


def build(project, body):
    if not isinstance(body, dict):
        raise ValueError('Expected an action object')
    actions = {'assemble': assemble, 'story': story_cut, 'transcript_edit': transcript_edit,
               'caption_review': caption_review, 'cleanup': cleanup, 'captions': captions, 'versions': versions}
    action = body.get('action')
    if not isinstance(action, str) or action not in actions:
        raise ValueError('Unknown workflow action')
    result = copy.deepcopy(project)
    values = actions[action](result, body)
    seq, message = values[:2]
    extra = values[2] if len(values) > 2 else None
    summary = {'sequence': seq['id'], 'message': message, 'sequences': extra if isinstance(extra, list) else [seq['id']]}
    if isinstance(extra, dict): summary.update(extra)
    return result, summary


def relink_history_changes(before, after, changes):
    """Keep complete old/new source records for Relink/collection completion."""
    identities = sorted({change['path'][1] for change in changes
                         if len(change['path']) >= 2 and change['path'][0] == 'media'})
    result = [change for change in changes if not (len(change['path']) >= 2 and change['path'][0] == 'media')]
    for identity in identities:
        old, new = before.get('media', {}).get(identity), after.get('media', {}).get(identity)
        if not isinstance(old, dict) or not isinstance(new, dict):
            raise ValueError('Relink cannot add or remove source identities')
        result.append({'path':['media',identity], 'anchors':[{'path':['media',identity],'id':identity}],
                       'before':{'exists':True,'value':copy.deepcopy(old)},
                       'after':{'exists':True,'value':copy.deepcopy(new)}})
    return result


def update_import_history(history, mid, path, token, update):
    """Keep an import undo/redo receipt valid as its derived previews complete.

    Only matching after-values of import/relink/interpretation/collection receipts are updated; user edits,
    replacements and other history values keep their normal conflict checks.
    """
    for stack in ('undo', 'redo'):
        for entry in history.get(stack, []):
            if entry.get('tool') not in ('workflow_import', 'workflow_relink', 'workflow_interpret', 'collect'):
                continue
            for change in entry.get('changes', []):
                if change.get('path') != ['media', mid]:
                    continue
                value = change.get('after', {}).get('value')
                if isinstance(value, dict) and value.get('path') == path and value.get('ingest_token') == token:
                    value.update(copy.deepcopy(update))
    return history
