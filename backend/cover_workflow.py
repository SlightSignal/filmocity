"""Captured cover rendering. PNGs remain private to their read-only task.

The original composition is rendered at its original canvas once. Target-sized
layouts consume that still, so resizing never changes authored source geometry.
"""
import copy
import math
import os
from pathlib import Path
import threading
import time
from urllib.parse import urlencode

from media_analysis import digest, number
from project_package import DERIVED
from recipe_plans import _bounded, _brand, _fit_text, _text
from recipe_workflow import _templates, MAX_BYTES
from render_replace import _folder, _identity, stamp, file_hash
from timeline_time import frame_rate, display_frame, interpretation_factor

MAX_ITEMS = 2048
MAX_VISITS = 128
MAX_SECONDS = 4 * 3600
MAX_PIXELS = 32 * 1024 * 1024
MAX_OUTPUT_BYTES = 80 * 1024 * 1024
MAX_SCRATCH_BYTES = 2 * 1024 * 1024 * 1024
MAX_RUNTIME = 300


def _snapshot(project, sid):
    """Capture every referenced sequence/media, including subclip parents."""
    import render
    _bounded(project, MAX_BYTES)
    sequences = project.get('sequences')
    if not isinstance(sequences, list): raise ValueError('The project has no sequences')
    by_id = {value.get('id'): value for value in sequences}
    if len(by_id) != len(sequences) or sid not in by_id: raise ValueError('Choose one existing sequence')
    result = {'version': project.get('version', 3), 'media': {}, 'sequences': []}
    visiting, included = set(), set()
    work = {'visits': 0, 'seconds': 0, 'clips': 0, 'native_prefix_seconds': 0}
    prefixes = {}
    def media(mid, chain=()):
        if mid in chain or len(chain) > 4: raise ValueError('Invalid subclip source chain')
        if mid in result['media']: return result['media'][mid]
        original = project.get('media', {}).get(mid)
        if not isinstance(original, dict): raise ValueError('A cover source is missing')
        value = {key: copy.deepcopy(child) for key, child in original.items() if key not in DERIVED}
        result['media'][mid] = value
        if value.get('subclip_of'): media(value['subclip_of'], (*chain, mid))
        if len(result['media']) > MAX_ITEMS: raise ValueError('Too many cover media dependencies')
        return value
    def visit(identity, depth=0):
        if identity in visiting or depth > 4: raise ValueError('Cover nested sequence cycle or depth exceeds five levels')
        original = by_id.get(identity)
        if not isinstance(original, dict): raise ValueError('A nested cover sequence is missing')
        work['visits'] += 1
        if work['visits'] > MAX_VISITS: raise ValueError('Cover exceeds 128 nested render visits')
        duration = number(render.seq_total(original), 'Sequence duration', minimum=1e-9, maximum=MAX_SECONDS)
        work['seconds'] += duration
        if work['seconds'] > 8 * 3600: raise ValueError('Cover nested renders exceed eight hours of combined sequence duration')
        w, h = original.get('width'), original.get('height')
        if type(w) is not int or type(h) is not int or not 16 <= w <= 4096 or not 16 <= h <= 4096:
            raise ValueError('Cover source canvases must use integer dimensions from 16 to 4096')
        if not 0 < float(frame_rate(original.get('fps'))) <= 120: raise ValueError('Cover frame rate must not exceed 120 fps')
        if identity not in included:
            included.add(identity); result['sequences'].append(copy.deepcopy(original))
        visiting.add(identity)
        for track in original.get('tracks', []):
            for clip in track.get('clips', []):
                work['clips'] += 1
                if work['clips'] > MAX_ITEMS: raise ValueError('Cover exceeds 2048 clip dependencies')
                if clip.get('media_id'):
                    source = media(clip['media_id']); parent = result['media'].get(source.get('subclip_of'), source)
                    if not parent.get('is_image') and not parent.get('synthetic'):
                        # Ordinary render_frame decodes from the common input
                        # origin. Account for the prefix, not only the trim span.
                        end = (number(clip.get('out'), 'Source Out', minimum=0) + number(source.get('sub_in', 0), 'Subclip origin', minimum=0)) / interpretation_factor(source)
                        if end > 8 * 3600: raise ValueError('Cover input decode prefixes must not exceed eight hours; use a shorter prepared source')
                        key = parent.get('path') or clip['media_id']; prefixes[key] = max(prefixes.get(key, 0), end)
                if clip.get('sequence_id') and not clip.get('media_id'): visit(clip['sequence_id'], depth+1)
        visiting.remove(identity)
    visit(sid)
    work['native_prefix_seconds'] = sum(prefixes.values())
    if work['native_prefix_seconds'] > 24 * 3600: raise ValueError('Cover source decode prefixes exceed 24 hours in total')
    return result, work


def _fit_layer_bounds(layer, original_size, width, height):
    """Bound generated glyph blocks using the same resolved font as drawtext.

    This is conservative font-metric fitting, not a shaping/layout certification.
    At most eleven candidate sizes are measured for the supported canvases.
    """
    from PIL import ImageFont
    from project_resources import style_font
    text = str(layer.get('text', ''))
    if layer.get('uppercase'): text = text.upper()
    if layer.get('vertical'): text = '\n'.join(text.replace('\n', ' '))
    font_path = style_font(layer)
    requested = layer['size']; original_spacing = layer.get('line_spacing')
    if original_spacing is not None:
        original_spacing = number(original_spacing, 'Template line spacing', minimum=-2048, maximum=2048)
    def spacing(size):
        if original_spacing is not None: return original_spacing * size / original_size
        return -int(.15 * size) if layer.get('vertical') else size * .15
    def fits(size):
        try: font = ImageFont.truetype(font_path, int(size))
        except (OSError, ValueError) as error: raise ValueError('The cover font could not be measured') from error
        lines = text.split('\n'); ascent, descent = font.getmetrics()
        block_width = max((max(font.getlength(line), font.getbbox(line)[2]-font.getbbox(line)[0]) for line in lines), default=0)
        advance = ascent+descent+int(spacing(size))
        if len(lines) > 1 and advance <= 0: return False
        block_height = ascent+descent + advance*(len(lines)-1)
        return block_width <= width and block_height <= height
    if text and not fits(requested):
        low, high, best = 1, int(requested)-1, 0
        while low <= high:
            candidate = (low+high)//2
            if fits(candidate): best = candidate; low = candidate+1
            else: high = candidate-1
        if not best: raise ValueError('Cover text cannot fit this canvas; shorten the text or choose a larger canvas')
        layer['size'] = best
    if original_spacing is not None: layer['line_spacing'] = spacing(layer['size'])
    layer['fix_bounds'] = True


def _layers(settings, template, brand, width, height):
    if not settings['headline'] and not settings['sub']: return []
    layers = template.get('layers') if isinstance(template, dict) else None
    if not isinstance(layers, list) or not 1 <= len(layers) <= 100 or any(not isinstance(layer, dict) for layer in layers):
        raise ValueError('Choose a cover template with 1–100 layers')
    layers = _brand(layers, {'primary': '#E8631C', 'secondary': '#7A2E9E', 'text': '#FFFFFF', 'font': '', **brand})
    text_index = 0
    for layer in layers:
        layer.pop('anim_in', None); layer.pop('anim_out', None)
        if layer.get('kind') == 'text':
            text = settings['headline'] if text_index == 0 else settings['sub']; text_index += 1
            size = number(layer.get('size', 64), 'Template text size', minimum=1, maximum=2048)
            # Font/offset units are pixels. Limit them by the target canvas,
            # never by the original sequence canvas used for the source still.
            layer['text'], layer['size'] = _fit_text(text, min(size, height*.16), width)
            _fit_layer_bounds(layer, size, width, height)
            if isinstance(layer.get('y'), (int, float)) and abs(layer['y']) > 1:
                layer['y'] = max(-height*.35, min(height*.35, layer['y']))
    if not text_index: raise ValueError('The cover template has no text layer')
    return layers


def target_project(payload, background, width, height):
    """Build the target canvas using captured font resources and source frame."""
    source = next(s for s in payload['project']['sequences'] if s['id'] == payload['sequence'])
    media = {'id': 'background', 'name': 'Captured cover frame', 'path': str(background), 'has_video': True,
             'has_audio': False, 'is_image': True, 'width': source['width'], 'height': source['height'], 'duration': 1}
    background_clip = {'id': 'background', 'media_id': 'background', 'start': 0, 'in_': 0, 'out': 1,
                       'speed': 1, 'fit': payload['settings']['framing'], 'audio': {'linked': False}}
    tracks = [{'id': 'background', 'kind': 'video', 'index': 0, 'clips': [background_clip]}]
    layers = _layers(payload['settings'], payload['template'], payload['brand'], width, height)
    if layers:
        tracks.append({'id': 'headline', 'kind': 'video', 'index': 1, 'clips': [
            {'id': 'headline', 'start': 0, 'in_': 0, 'out': 1, 'speed': 1, 'media_id': None,
             'graphic': {'name': 'Cover', 'layers': layers}, 'transform': {'opacity': 1}, 'keyframes': {}}]})
    return {'version': 3, 'media': {'background': media}, 'sequences': [
        {'id': 'cover', 'name': 'Cover', 'width': width, 'height': height, 'fps': source['fps'], 'tracks': tracks, 'captions': []}]}


def capture(project, body, context, root, assets):
    from preflight import inspect_resources
    from project_resources import references, font_styles, style_font
    if not isinstance(body, dict) or not isinstance(body.get('sequence'), str): raise ValueError('Choose the cover sequence')
    snapshot, work = _snapshot(project, body['sequence']); source = snapshot['sequences'][0]
    import render
    at = number(body.get('time', 0), 'Cover time', minimum=0)
    if at >= render.seq_total(source): raise ValueError('Choose a frame before the end of the sequence')
    sizes = body.get('sizes', [[source['width'], source['height']]])
    if not isinstance(sizes, list) or not 1 <= len(sizes) <= 4: raise ValueError('Choose 1–4 cover sizes')
    if any(not isinstance(size, list) or len(size) != 2 or any(type(n) is not int or not 16 <= n <= 4096 for n in size) for size in sizes):
        raise ValueError('Cover dimensions must be integers from 16 to 4096')
    if len(set(map(tuple, sizes))) != len(sizes) or sum(w*h for w,h in sizes) > MAX_PIXELS: raise ValueError('Choose unique cover sizes totalling at most 32 megapixels')
    framing = body.get('framing', 'blur_fill')
    if framing not in ('cover', 'contain', 'blur_fill'): raise ValueError('Choose cover, contain or blur_fill framing')
    settings = {'time': at, 'headline': _text(body, 'headline'), 'sub': _text(body, 'sub', maximum=1000),
                'template': _text(body, 'template', 'Hook — Big Statement', maximum=120, required=True),
                'sizes': copy.deepcopy(sizes), 'framing': framing}
    template = {}
    if settings['headline'] or settings['sub']:
        template = _templates(root, assets).get(settings['template'])
        if not isinstance(template, dict): raise ValueError('The selected cover template is unavailable')
    brand = copy.deepcopy(project.get('brand') or {})
    report = inspect_resources(snapshot, body['sequence'])
    errors = [item['message'] for item in report['issues'] if item['severity'] == 'error']
    if errors: raise ValueError('; '.join(errors[:8]))
    paths = {item['path'] for item in report['resources']}
    for width,height in sizes:
        layout = {'graphic': {'layers': _layers(settings, template, brand, width, height)}}
        paths.update(obj[key] for obj,key,_,_ in references(layout))
        paths.update(style_font(style) for style in font_styles(layout))
    if len(paths) > MAX_ITEMS: raise ValueError('Cover has too many source/resource files')
    payload = {'version': 1, 'sequence': body['sequence'], 'project': snapshot, 'settings': settings, 'template': copy.deepcopy(template),
               'brand': brand, 'frame': display_frame(at, source['fps']), 'work': work,
               'resources': [stamp(path) for path in sorted(paths)], 'warnings': [item['message'] for item in report['issues'] if item['severity'] != 'error']}
    _bounded(payload, MAX_BYTES); payload['signature'] = digest(payload)
    payload.update(context=copy.deepcopy(context), root=os.path.abspath(root), assets=os.path.abspath(assets))
    return payload


def validate_current(project, payload, context, root, assets):
    if os.path.abspath(root) != payload['root'] or any(context.get(key) != payload['context'].get(key) for key in ('workspace','project')):
        raise ValueError('Open the cover’s original workspace and project')
    current = capture(project, dict(payload['settings'], sequence=payload['sequence']), context, root, assets)
    if current['signature'] != payload['signature']: raise ValueError('Cover composition, sources, template or resources changed; generate a new cover')


def check_sources(payload):
    if any(stamp(value[0]) != value for value in payload['resources']): raise ValueError('A cover source or resource changed during rendering')


def image_url(payload, identity, index):
    owner = payload['context']
    return f'/api/tasks/{identity}/cover/{index}?' + urlencode({key: owner[key] for key in ('workspace', 'project')})


def artifact_folder(root, identity):
    return _folder(root, 'tasks', 'covers', _identity(identity))


def _png(path, width, height):
    from PIL import Image
    if path.is_symlink() or not path.is_file() or not 0 < path.stat().st_size <= MAX_OUTPUT_BYTES: raise ValueError('Cover PNG is missing, linked or too large')
    with Image.open(path) as image:
        if image.format != 'PNG' or image.size != (width, height): raise ValueError('Cover PNG format or canvas does not match its request')
        image.verify()
    with Image.open(path) as image: image.load()  # Validate decoded pixels, not just a header.


def analyze(payload, task):
    from render import render_frame
    from render_context import RenderContext
    task.check(); check_sources(payload)
    folder = artifact_folder(payload['root'], task.id); owned = []; stop = threading.Event(); failures = []
    def watch():
        deadline = time.monotonic() + MAX_RUNTIME
        while not stop.wait(.1):
            try:
                files = list(folder.rglob('*'))
                if len(files) > 8192: raise ValueError('Cover scratch file budget exceeded')
                size = sum(path.stat().st_size for path in files if path.is_file())
                if size > MAX_SCRATCH_BYTES: raise ValueError('Cover scratch size budget exceeded')
                if time.monotonic() > deadline: raise ValueError('Cover exceeded its five-minute render budget')
            except FileNotFoundError: continue  # Normal owned scratch retirement.
            except Exception as error:
                failures.append(error); task.holder['cancelled'] = True; return
    watcher = threading.Thread(target=watch, name='Filmocity cover budget', daemon=True)
    watcher.start()
    try:
        source = next(s for s in payload['project']['sequences'] if s['id'] == payload['sequence'])
        background = folder/'source-frame.png'
        with background.open('xb'): pass
        owned.append(background)
        task.progress('Rendering captured composition', .05)
        with RenderContext(proc_holder=task.holder, scratch_parent=str(folder), stall_timeout=60) as context:
            render_frame(payload['project'], payload['sequence'], payload['settings']['time'], str(background), context=context)
            _png(background, source['width'], source['height'])
            covers = []
            for index, (width,height) in enumerate(payload['settings']['sizes']):
                task.check(); task.progress('Rendering cover '+str(index+1), .25 + .6*index/len(payload['settings']['sizes']))
                filename = f'cover-{task.id}-{index}-{width}x{height}.png'; path = folder/filename
                with path.open('xb'): pass
                owned.append(path)
                target = target_project(payload, background, width, height)
                render_frame(target, 'cover', 0, str(path), context=context)
                _png(path, width, height)
                covers.append({'index': index, 'width': width, 'height': height, 'filename': filename,
                               'url': image_url(payload, task.id, index), 'sha256': file_hash(path, task.check), 'size': path.stat().st_size})
        task.check(); check_sources(payload)
        result = {'version': 1, 'kind': 'cover', 'sequence': payload['sequence'], 'time': payload['settings']['time'],
                  'frame': payload['frame'], 'signature': payload['signature'], 'context': payload['context'], 'covers': covers}
        stop.set(); watcher.join(); task.check()
        return task.commit_result(lambda: result, ready=True)
    except BaseException:
        for path in owned:
            if path.exists() or path.is_symlink(): path.unlink()
        if failures: raise ValueError(str(failures[0])) from failures[0]
        raise
    finally:
        stop.set(); watcher.join()


def verify_result(payload, result, identity):
    expected = {'version': 1, 'kind': 'cover', 'sequence': payload['sequence'], 'signature': payload['signature'],
                'time': payload['settings']['time'], 'frame': payload['frame'], 'context': payload['context']}
    if not isinstance(result, dict) or any(result.get(key) != value for key,value in expected.items()): raise ValueError('Cover result does not match its captured inputs')
    covers = result.get('covers')
    if not isinstance(covers, list) or len(covers) != len(payload['settings']['sizes']): raise ValueError('Cover result outputs are incomplete')
    folder = artifact_folder(payload['root'], identity); paths = []
    for index, (cover, (width,height)) in enumerate(zip(covers, payload['settings']['sizes'])):
        filename = f'cover-{identity}-{index}-{width}x{height}.png'
        required = {'index': index, 'width': width, 'height': height, 'filename': filename, 'url': image_url(payload, identity, index)}
        if not isinstance(cover, dict) or any(cover.get(key) != value for key,value in required.items()): raise ValueError('Cover artifact ownership changed')
        path = folder/filename; _png(path,width,height)
        if cover.get('size') != path.stat().st_size or cover.get('sha256') != file_hash(path): raise ValueError('Cover artifact changed after rendering')
        paths.append(path)
    return paths


def review(project, payload, result, context, identity, root, assets):
    validate_current(project,payload,context,root,assets); verify_result(payload,result,identity)
    summary = {'kind': 'cover', 'sequence': payload['sequence'], 'time': payload['settings']['time'], 'frame': payload['frame'],
               'outputs': copy.deepcopy(result['covers']), 'warnings': payload['warnings'] + [
                   'The complete authored frame is retained, including titles and captions. Added cover text uses approximate width-based fitting; inspect each PNG.',
                   'PNG output uses the existing 8-bit RGB renderer. Downloads do not alter the project or create an Undo entry.'],
               'message': f"Rendered {len(result['covers'])} cover PNGs from frame {payload['frame']}."}
    plan = {'ops': [], 'summary': summary}
    plan['fingerprint'] = digest({'task': identity, 'context': context, 'result': result, 'plan': plan})
    return plan
