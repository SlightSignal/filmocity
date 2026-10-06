"""Project-owned font and camera-LUT bindings used by render and packaging.

Bindings pin the resolved file while their saved font/transform choice matches.
Changing that choice stops using the old binding; missing active files fail.
"""
import os


def font_binding(style):
    binding = style.get('font_resource')
    if isinstance(binding, dict) and binding.get('family') == (style.get('font') or '') and binding.get('weight') == style.get('weight', 'bold'):
        return binding


def style_font(style):
    from render import font_file
    binding = font_binding(style)
    return binding.get('path') if binding else font_file(style.get('font'), style.get('weight', 'bold'))


def input_lut(media):
    from render import INPUT_LUTS
    chosen = media.get('input_transform')
    if chosen in (None, '', 'none'): return None
    if chosen not in ('slog3', 'vlog', 'clog3', 'logc3'): raise ValueError('Unsupported camera-log LUT')
    binding = media.get('input_transform_resource')
    if isinstance(binding, dict) and binding.get('name') == chosen: return binding.get('path')
    return os.path.join(INPUT_LUTS, chosen + '.cube')


def objects(value):
    if isinstance(value, dict):
        yield value
        for child in list(value.values()): yield from objects(child)
    elif isinstance(value, list):
        for child in value: yield from objects(child)


def font_styles(project):
    """Include inactive tracks/versions/proposals as well as current pictures."""
    seen = set()
    for sequence in project.get('sequences', []):
        if sequence.get('captions'): sequence.setdefault('caption_style', {})
    for obj in objects(project):
        candidates = [obj] if ('font' in obj or obj.get('kind') == 'text') else []
        for key in ('title', 'caption_style'):
            if isinstance(obj.get(key), dict): candidates.append(obj[key])
        if isinstance(obj.get('graphic'), dict) and obj['graphic'].get('text'): candidates.append(obj['graphic'])
        for style in candidates:
            if id(style) not in seen:
                seen.add(id(style));yield style


def references(project):
    """Typed file-valued fields; JSON operation paths are field addresses."""
    def walk(value, pointer=()):
        if isinstance(value, dict):
            for key, child in value.items():
                at = pointer + (key,)
                operation_address = key == 'path' and 'op' in value and len(pointer) >= 2 and pointer[-2] == 'ops' and isinstance(pointer[-1], int)
                if key in ('path', 'lut', 'stab_trf') and isinstance(child, str) and child and not operation_address:
                    kind = 'source' if pointer[:1] == ('media',) and len(pointer) == 2 and key == 'path' else 'resource'
                    yield value, key, at, kind
                else: yield from walk(child, at)
        elif isinstance(value, list):
            for index, child in enumerate(value): yield from walk(child, pointer + (index,))
    yield from walk(project)
