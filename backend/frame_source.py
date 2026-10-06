"""Build a source-monitor still without timeline edits or footage interpretation."""
import copy
import math

from timeline_time import frame_rate
from picture_geometry import display_size


def source_project(project, media_id):
    media = project.get('media', {})
    item = media.get(media_id); seen = set()
    while item and item.get('subclip_of'):
        if media_id in seen: raise ValueError('Source subclip cycle')
        seen.add(media_id); media_id = item['subclip_of']; item = media.get(media_id)
    if not item or not item.get('has_video'): raise ValueError('Load a source with a video picture first')
    item = copy.deepcopy(item)
    duration = float(item.get('native_duration') or item.get('duration') or 0)
    if not math.isfinite(duration) or duration <= 0: raise ValueError('The source duration is unavailable')
    width, height = display_size(item, [])
    fps = frame_rate(item.get('frame_rate') or item.get('native_fps') or item.get('fps') or 30)
    for field in ('interpret_fps', 'subclip_of', 'sub_in'): item.pop(field, None)
    item['duration'] = duration
    sequence = {'id': 'source', 'name': item.get('name', 'Source frame'), 'width': width, 'height': height, 'fps': float(fps), 'captions': [],
        'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'clips': [{'id': 'source', 'media_id': media_id, 'start': 0, 'in_': 0, 'out': duration}]}]}
    return {'media': {media_id: item}, 'sequences': [sequence]}
