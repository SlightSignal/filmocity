"""Read-only export resource checks. Proxies are playback aids, never original sources."""
import math
import os
import re
from matte_tracks import track_dependencies, render_clips
from input_options import validated_input_options


class ResourceError(RuntimeError):
    def __init__(self, report):
        self.report = report
        messages = [i['message'] for i in report['issues'] if i['severity'] == 'error']
        super().__init__('Export needs attention: ' + '; '.join(messages[:8]))


def active_clips(sequence):
    tracks, mattes, _ = track_dependencies(sequence)
    for track in tracks:
        for clip in render_clips(sequence, track, mattes):
            yield track, clip


def media_files(media):
    """Return actual numbered frames, rather than testing the literal %04d pathname."""
    opts = validated_input_options(media.get('input_opts'))
    path = media.get('path') or ''
    if not media.get('sequence_frames'):
        return [path]
    count = int(media['sequence_frames'])
    if count <= 0 or count > 1000000 or not re.search(r'%0?\d*d', path):
        raise ValueError('Invalid numbered sequence; expected 1–1,000,000 frames and a %d pattern')
    start = int(opts[opts.index('-start_number') + 1]) if '-start_number' in opts else 0
    return [path % index for index in range(start, start + count)]


def media_online(media):
    if media.get('synthetic'):
        return True
    try:
        return all(os.path.isfile(path) and os.path.getsize(path) > 0 for path in media_files(media))
    except (OSError, ValueError, TypeError, IndexError):
        return False


def inspect_resources(project, sequence_id, preset=None):
    from project_resources import style_font, font_binding, input_lut
    preset = preset or {}
    sequences = {s['id']: s for s in project.get('sequences', [])}
    media = project.get('media') or {}
    issues, resources, inspected = [], [], set()
    accepted_sources = {}

    def issue(code, message, context, severity='error', **extra):
        issues.append({'severity': severity, 'code': code, 'message': message, **context, **extra})

    # Fail before any decoder/filter/font inspection. Direct callers need the
    # same admission boundary as saved-project loading, including parent and
    # unused source records that a nested render may subsequently capture.
    for identity, item in media.items():
        try:
            validated_input_options(item.get('input_opts'))
        except ValueError as error:
            issue('invalid_input_options', str(error), {'sequence': sequence_id, 'media_id': identity})
    if issues:
        return {'ok': False, 'sequence': sequence_id, 'errors': len(issues), 'warnings': 0,
                'issues': issues, 'resources': [], 'resource_count': 0, 'delivery_color': None}

    from render_color import ColorPipeline
    from delivery_color import plan as delivery_plan
    delivery = None
    try:
        ColorPipeline.from_preset(preset)
        delivery = delivery_plan(preset)
        if delivery["mode"] == "legacy_unmanaged":
            issue("legacy_delivery_color", delivery["description"], {"sequence": sequence_id}, "warning")
    except ValueError as error:
        issue('color_processing', str(error), {'sequence': sequence_id})

    def file(path, kind, context):
        key = (kind, path)
        if key in inspected:
            return
        inspected.add(key)
        try:
            if not path or not os.path.isfile(path):
                raise FileNotFoundError(path)
            with open(path, 'rb') as stream:
                if not stream.read(1):
                    raise ValueError('File is empty')
            resources.append({'kind': kind, 'path': os.path.abspath(path), **context})
        except (OSError, ValueError) as exc:
            code = 'missing_' + kind if isinstance(exc, FileNotFoundError) else 'unreadable_' + kind
            issue(code, f"{kind.replace('_', ' ').capitalize()} unavailable: {path or '(no path)'}", context, path=path)

    def font(style, context):
        from PIL import ImageFont
        family = style.get('font')
        resolved = style_font(style)
        file(resolved, 'font', context)
        if font_binding(style):
            try: ImageFont.truetype(resolved, 24)
            except (OSError, ValueError, TypeError): issue('invalid_font', f'Pinned font cannot be loaded: {resolved}', context, path=resolved)
            return
        if not family:
            return
        if any(ch in str(family) for ch in ('/', '\\')) or str(family).lower().endswith(('.ttf', '.otf', '.ttc')):
            file(str(family), 'font', context)
            return
        try:
            actual = ImageFont.truetype(resolved, 24).getname()[0]
            normalize = lambda text: re.sub(r'[^a-z0-9]', '', str(text).lower())
            if normalize(actual) != normalize(family):
                issue('font_substitution', f'Font "{family}" will use "{actual}". Install or select the intended font.',
                      context, 'warning', requested=family, resolved=actual)
        except (OSError, ValueError):
            issue('invalid_font', f'Font cannot be loaded: {resolved}', context, path=resolved)

    root_angle = object()

    def walk(sid, stack=(), angle=root_angle):
        context = {'sequence': sid}
        if sid in stack:
            issue('nested_cycle', 'Nested sequence cycle: ' + ' → '.join((*stack, sid)), context)
            return
        if len(stack) > 4:
            issue('nested_depth', 'Nested sequences exceed the supported depth of five levels.', context)
            return
        sequence = sequences.get(sid)
        if not sequence:
            issue('missing_sequence', f'Sequence unavailable: {sid}', context)
            return
        if sequence.get('multicam') and angle is not root_angle:
            from multicam import view as multicam_view
            try: sequence = multicam_view(sequence, angle)
            except ValueError as error:
                issue('invalid_multicam', str(error), context)
                return
        for field in ('width', 'height', 'fps'):
            try:
                value = float(sequence[field])
                if not math.isfinite(value) or value <= 0:
                    raise ValueError()
            except (KeyError, TypeError, ValueError):
                issue('invalid_sequence', f'Sequence {field} must be a positive finite number.', context)
        _, matte_ids, matte_issues = track_dependencies(sequence)
        issues.extend(matte_issues)
        for track, clip in active_clips(sequence):
            picture_needed = preset.get('format') != 'audio' and track['kind'] == 'video' and (not track.get('_mc_picture_hidden') or track['id'] in matte_ids)
            at = {'sequence': sid, 'track': track['id'], 'clip': clip['id']}
            mid, child = clip.get('media_id'), clip.get('sequence_id')
            if mid:
                item = media.get(mid)
                if not item:
                    issue('missing_media_entry', f'Media unavailable: {mid}', at, media_id=mid)
                elif not item.get('synthetic'):
                    original = media.get(item.get('subclip_of'), item)
                    if item.get('subclip_of') and item['subclip_of'] not in media:
                        issue('missing_parent_media', f'Subclip parent unavailable: {item["subclip_of"]}', at, media_id=mid)
                    source_at = {**at, 'media_id': mid, 'name': item.get('name', mid)}
                    # A reviewed replacement remains bound to its measured file.
                    # Local import avoids the task-input/preflight import cycle.
                    source_key = id(original)
                    if source_key not in accepted_sources:
                        from source_relink_io import check_accepted
                        try:
                            check_accepted(original)
                            accepted_sources[source_key] = None
                        except (OSError, ValueError, TypeError) as exc:
                            accepted_sources[source_key] = str(exc)
                    if accepted_sources[source_key] is not None:
                        issue('source_changed_after_relink', accepted_sources[source_key], source_at, path=original.get('path'))
                    try:
                        paths = media_files(original)
                        missing = [p for p in paths if not os.path.isfile(p) or os.path.getsize(p) == 0]
                        if original.get('sequence_frames') and missing:
                            issue('missing_sequence_frames', f'{len(missing)} numbered frame(s) unavailable in "{item.get("name", mid)}".',
                                  source_at, path=original.get('path'), missing_frames=missing[:10], missing_count=len(missing))
                        else:
                            for path in paths:
                                file(path, 'source', source_at)
                    except (OSError, ValueError, TypeError, IndexError) as exc:
                        issue('invalid_image_sequence', str(exc), source_at)
                    if picture_needed and original.get('stab_trf'):
                        file(original['stab_trf'], 'stabilization', source_at)
                    if picture_needed:
                        from source_color import plan as source_color_plan, filters as source_color_filters
                        from render import has_filter
                        try:
                            policy = source_color_plan(original)
                            source_color_filters(original, has_filter)
                            for warning in policy['warnings']: issue('source_color_conversion', warning, source_at, 'warning', color_policy=policy)
                        except ValueError as error: issue('source_color', str(error), source_at)
                    transform = original.get('input_transform')
                    if picture_needed and isinstance(transform, str) and transform in ('slog3', 'vlog', 'clog3', 'logc3'):
                        file(input_lut(original), 'input_lut', source_at)
            elif child:
                walk(child, (*stack, sid), clip.get('multicam_angle', 0))
            if not picture_needed: continue
            color = clip.get('color') or {}
            if color.get('lut'):
                file(color['lut'], 'lut', at)
            if clip.get('title'):
                font(clip['title'], at)
            graphic = clip.get('graphic') or {}
            if graphic.get('text'):
                font(graphic, at)
            for layer in graphic.get('layers', []):
                if layer.get('kind') == 'text':
                    font(layer, at)
                if layer.get('kind') == 'image':
                    file(layer.get('path'), 'graphic_image', at)
        if sequence.get('captions'):
            font(sequence.get('caption_style') or {}, context)

    walk(sequence_id)
    watermark = preset.get('watermark') or {}
    if isinstance(watermark, dict) and watermark.get('path'):
        file(watermark['path'], 'watermark', {'sequence': sequence_id})
    errors = sum(i['severity'] == 'error' for i in issues)
    return {'ok': errors == 0, 'sequence': sequence_id, 'errors': errors,
            'warnings': len(issues) - errors, 'issues': issues,
            'resources': resources, 'resource_count': len(resources), 'delivery_color': delivery}


def require_resources(project, sequence_id, preset=None):
    report = inspect_resources(project, sequence_id, preset)
    if not report['ok']:
        raise ResourceError(report)
    return report
