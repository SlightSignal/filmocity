"""Owned recipe capture, bounded analysis and staged generated media.

Pure editorial construction belongs to recipe_plans. Workers never save projects.
"""
import copy
import json
import os
from pathlib import Path

from media_analysis import digest
from render_replace import stamp, file_hash, _folder, _identity
from project_package import DERIVED

MAX_BYTES = 16 * 1024 * 1024
MAX_RESOURCES = 2048


def _json(path):
    with open(path, 'rb') as stream: raw = stream.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES: raise ValueError('Recipe configuration exceeds its data budget')
    try: value = json.loads(raw)
    except RecursionError as error: raise ValueError('Recipe configuration is too deeply nested') from error
    if not isinstance(value, dict): raise ValueError('Recipe configuration must be an object')
    pending = [(value, 0)]; count = 0
    while pending:
        item, depth = pending.pop(); count += 1
        if count > 100000 or depth > 32: raise ValueError('Recipe configuration has too many or too deeply nested values')
        if isinstance(item, dict): pending.extend((child, depth+1) for child in item.values())
        elif isinstance(item, list): pending.extend((child, depth+1) for child in item)
    return value


def _templates(root, assets):
    output = {}; folder = Path(assets) / 'templates'
    paths = sorted(folder.glob('*.json'))
    if len(paths) > 512: raise ValueError('Too many recipe templates')
    total = 0
    for path in paths:
        total += path.stat().st_size
        if total > MAX_BYTES: raise ValueError('Recipe templates exceed their aggregate data budget')
        value = _json(path); output[value.get('name', path.name)] = value
    settings = Path(root) / 'settings.json'
    if settings.exists():
        custom = _json(settings).get('graphics_templates') or {}
        if not isinstance(custom, dict): raise ValueError('Custom recipe templates must be an object')
        output.update(custom)
    return output


def _resources(payload):
    from project_resources import references, font_styles, style_font, input_lut
    value = {'media': copy.deepcopy(payload['media_basis']), 'sequences': [copy.deepcopy(payload['sequence_basis']), *copy.deepcopy(list(payload.get('sequence_dependencies', {}).values()))],
             'templates': copy.deepcopy(payload['templates']), 'voice_preset': copy.deepcopy(payload['voice_preset'])}
    settings = payload['settings']
    if settings.get('captions'):
        if payload['mode'] == 'reel' and (settings.get('hook') or settings.get('caption_text')):
            value['generated_captions'] = {'caption_style': copy.deepcopy(settings.get('caption_style') or {})}
        elif payload['mode'] == 'talking_head' and payload['sequence_basis'].get('transcript'):
            value['generated_captions'] = {'caption_style': copy.deepcopy(payload['sequence_basis'].get('caption_style') or {})}
    # Only newly generated cards/styles interpret brand tokens. Literal source
    # paths and already-authored sequence resources must remain unchanged.
    def brand(item):
        if isinstance(item, str):
            defaults = {'primary': '#E8631C', 'secondary': '#7A2E9E', 'text': '#FFFFFF', 'font': ''}
            for key in ('primary', 'secondary', 'text', 'font'):
                replacement = payload.get('brand', {}).get(key, defaults[key])
                item = item.replace('{{'+key+'}}', str(replacement))
            return item
        if isinstance(item, dict): return {key: brand(child) for key, child in item.items()}
        if isinstance(item, list): return [brand(child) for child in item]
        return item
    value['templates'] = brand(value['templates'])
    if payload['mode'] == 'reel' and 'generated_captions' in value: value['generated_captions'] = brand(value['generated_captions'])
    paths = {obj[key] for obj, key, _, _ in references(value)}
    if payload['settings'].get('look'): paths.add(payload['settings']['look'])
    for style in font_styles(value): paths.add(style_font(style))
    for media in value['media'].values():
        path = input_lut(media)
        if path: paths.add(path)
    if len(paths) > MAX_RESOURCES: raise ValueError('The recipe contains too many resource files')
    return [stamp(path) for path in sorted(paths)]


def capture(project, body, mode, context, root, assets):
    import recipe_plans
    if mode in ('explainer', 'variants'):
        import sequence_recipes as recipe_plans
    if not isinstance(body, dict): raise ValueError('Recipe input must be an object')
    payload = recipe_plans.capture(project, body, mode)
    for media in payload['media_basis'].values():
        for key in DERIVED: media.pop(key, None)
    settings = payload['settings']; payload['templates'] = {}; payload['voice_preset'] = {}
    if mode == 'reel' and (settings.get('hook') or settings.get('cta')):
        templates = _templates(root, assets)
        for key, name in (('hook', 'Hook — Big Statement'), ('cta', 'CTA — Follow')):
            if settings.get(key):
                if not isinstance(templates.get(name), dict): raise ValueError('Required recipe template is unavailable: '+name)
                payload['templates'][key] = copy.deepcopy(templates[name])
    if mode == 'explainer':
        required = [('lower_third', 'Lower Third — Card', settings.get('lower_third')),
                    ('chapter', 'Chapter Title', settings.get('chapters')), ('cta', 'CTA — Follow', settings.get('end_card'))]
        templates = _templates(root, assets) if any(item[2] for item in required) else {}
        for key, name, enabled in required:
            if enabled:
                if not isinstance(templates.get(name), dict): raise ValueError('Required recipe template is unavailable: '+name)
                payload['templates'][key] = copy.deepcopy(templates[name])
    if mode == 'talking_head' and settings.get('voice_preset'):
        payload['voice_preset'] = _json(Path(assets) / 'presets' / 'effect_presets.json').get('Voice — Clean-up')
        if not isinstance(payload['voice_preset'], dict): raise ValueError('Voice Clean-up preset is unavailable')
    payload['resources'] = _resources(payload)
    payload['analysis_payload'] = None
    source_project = {'version': project.get('version', 3), 'media': payload['media_basis'], 'sequences': [payload['sequence_basis']]}
    if mode == 'talking_head' and settings.get('silences'):
        import media_analysis
        clip = next(c for t in payload['sequence_basis']['tracks'] for c in t['clips'] if c['id'] == payload['clip_id'])
        payload['analysis_payload'] = media_analysis.capture(source_project, {
            'media_id': clip['media_id'], 'sequence': payload['sequence'], 'clip_id': payload['clip_id'],
            'in': clip['in_'], 'out': clip['out'], 'threshold_db': settings.get('threshold_db', -38),
            'min_gap': settings.get('min_gap', .45), 'pad': settings.get('pad', .08)}, 'silences', context)
    elif mode == 'reel' and settings.get('rhythm') == 'onsets':
        import audio_workflow
        payload['analysis_payload'] = audio_workflow.capture(source_project, {'media_id': settings['music']}, 'beats', context)
    # Context revisions do not affect source analysis identity. Review always
    # fingerprints its own current context separately.
    identity = copy.deepcopy(payload)
    if identity.get('analysis_payload'): identity['analysis_payload'].pop('context', None)
    payload['signature'] = digest(identity)
    payload.update(context=copy.deepcopy(context), root=os.path.abspath(root), assets=os.path.abspath(assets))
    if len(json.dumps(payload, allow_nan=False).encode()) > MAX_BYTES: raise ValueError('The captured recipe exceeds its data budget')
    return payload


def validate_current(project, payload, context, root, assets):
    if any(payload['context'].get(key) != context.get(key) for key in ('workspace', 'project')) or os.path.abspath(root) != payload['root']:
        raise ValueError('Open the recipe’s original workspace and project')
    body = dict(payload['settings'], sequence=payload['sequence'])
    if payload.get('clip_id'): body['clip_id'] = payload['clip_id']
    current = capture(project, body, payload['mode'], context, root, assets)
    if current['signature'] != payload['signature']: raise ValueError('Recipe sources, target, settings or resources changed; start a new recipe')
    return current


def check_sources(payload):
    for expected in payload['resources']:
        if stamp(expected[0]) != expected: raise ValueError('A captured recipe source or resource changed')


def artifact_folder(root, identity):
    return _folder(root, 'tasks', 'recipes', _identity(identity))


def _needs_sfx(payload):
    return payload['mode'] == 'reel' and payload['settings'].get('sfx') and len(payload['settings'].get('shots', [])) > 1


def analyze(payload, task):
    owned = []
    try:
        result = _analyze(payload, task, owned)
        # Once the owned artifact is complete, publish its result and ready
        # state together. A last-moment cancellation cannot strand it unreadable.
        return task.commit_result(lambda: result, ready=True)
    except BaseException:
        for path in owned:
            if path.exists() or path.is_symlink(): path.unlink()
        raise


def _analyze(payload, task, owned):
    task.check(); check_sources(payload)
    result = {'version': 1, 'kind': 'recipe', 'mode': payload['mode'], 'signature': payload['signature'],
              'context': payload['context'], 'sequence': payload['sequence'], 'clip_id': payload.get('clip_id'),
              'analysis': {'silences': None, 'onsets': None}, 'sfx_media': None, 'sfx': None}
    if payload['analysis_payload']:
        if payload['mode'] == 'talking_head':
            import media_analysis
            result['analysis']['silences'] = media_analysis.analyze(payload['analysis_payload'], task)
        else:
            import audio_measurement
            result['analysis']['onsets'] = audio_measurement.analyze(payload['analysis_payload'], task)
    if _needs_sfx(payload):
        import audio_measurement
        from render_context import RenderContext
        folder = artifact_folder(payload['root'], task.id); output = folder / 'whoosh.wav'
        with output.open('xb'): pass
        owned.append(output)
        try:
            task.progress('Generating recipe whoosh', .9)
            with RenderContext(proc_holder=task.holder, scratch_parent=str(folder)) as context:
                command = ['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                    'anoisesrc=c=pink:r=48000:d=0.8,lowpass=f=6000,afade=t=in:d=0.25,afade=t=out:st=0.35:d=0.45,volume=0.9,aformat=channel_layouts=stereo',
                    '-t', '0.8', '-ac', '2', '-ar', '48000', '-c:a', 'pcm_s16le', str(output)]
                audio_measurement._run(command, context, watched=((str(output), 200000),), timeout=60)
            import wave
            with wave.open(str(output), 'rb') as stream:
                if (stream.getnchannels(), stream.getsampwidth(), stream.getframerate(), stream.getnframes()) != (2, 2, 48000, 38400):
                    raise ValueError('Generated recipe sound has an invalid format or duration')
            task.check(); result['sfx'] = {'path': str(output), 'sha256': file_hash(output, task.check), 'size': output.stat().st_size}
            result['sfx_media'] = {'id': 'recipe_sfx_'+task.id, 'name': 'Recipe whoosh', 'path': str(Path(payload['root'])/'media'/'recipes'/(task.id+'.wav')),
                'duration': .8, 'has_audio': True, 'has_video': False, 'channels': 2, 'sample_rate': 48000, 'sfx': 'whoosh', 'status': 'ready', 'ingest_token': task.id}
        except BaseException:
            if output.exists() and not output.is_symlink(): output.unlink()
            raise
    task.check(); check_sources(payload)
    return result


def verify_result(payload, result, identity, check=lambda: None):
    if not isinstance(result, dict) or any(result.get(key) != value for key, value in {'version': 1, 'kind': 'recipe', 'mode': payload['mode'], 'signature': payload['signature'], 'sequence': payload['sequence'], 'clip_id': payload.get('clip_id')}.items()):
        raise ValueError('The recipe result does not match its captured inputs')
    needed = _needs_sfx(payload)
    if not needed:
        if result.get('sfx') is not None or result.get('sfx_media') is not None: raise ValueError('Unexpected recipe sound artifact')
        return None
    folder = artifact_folder(payload['root'], identity); path = folder / 'whoosh.wav'; value = result.get('sfx') or {}; media = result.get('sfx_media') or {}
    if value.get('path') != str(path) or path.is_symlink() or not path.is_file() or path.stat().st_size != value.get('size') or file_hash(path, check) != value.get('sha256'):
        raise ValueError('The recipe sound artifact is missing or changed')
    expected = {'id': 'recipe_sfx_'+identity, 'path': str(Path(payload['root'])/'media'/'recipes'/(identity+'.wav')),
                'duration': .8, 'has_audio': True, 'has_video': False, 'channels': 2, 'sample_rate': 48000, 'sfx': 'whoosh', 'ingest_token': identity}
    if any(media.get(key) != value for key, value in expected.items()):
        raise ValueError('The recipe sound ownership changed')
    return path


def review(project, payload, result, context, identity, root, assets):
    import recipe_plans
    if payload['mode'] in ('explainer', 'variants'):
        import sequence_recipes as recipe_plans
    validate_current(project, payload, context, root, assets); verify_result(payload, result, identity)
    plan = recipe_plans.plan(project, payload, result, identity)
    plan['fingerprint'] = digest({'task': identity, 'context': context, 'signature': payload['signature'], 'result': result, 'plan': plan})
    return plan


def publish(payload, result, identity, *, proc_holder=None):
    holder = proc_holder if proc_holder is not None else {}
    def check():
        if holder.get('cancelled'): raise RuntimeError('cancelled')
    check(); source = verify_result(payload, result, identity, check)
    if source is None: return
    folder = _folder(payload['root'], 'media', 'recipes'); output = folder / (identity+'.wav')
    if output.is_symlink(): raise ValueError('Recipe media must not be a link')
    if output.exists():
        if file_hash(output, check) != result['sfx']['sha256']: raise ValueError('Different recipe media already exists')
        return
    stage = folder / (identity+'.pending.wav'); owned = False
    try:
        with stage.open('xb') as target:
            owned = True
            with source.open('rb') as original:
                while True:
                    check(); block = original.read(1024*1024)
                    if not block: break
                    target.write(block)
            target.flush(); os.fsync(target.fileno())
        if file_hash(stage, check) != result['sfx']['sha256']: raise ValueError('Recipe media copy verification failed')
        check()
        if output.exists(): raise ValueError('Recipe media appeared during publication')
        os.replace(stage, output)
    finally:
        if owned and stage.exists(): stage.unlink()
