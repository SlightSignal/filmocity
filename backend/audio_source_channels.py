"""Discrete first-stream channel aliases, before the stereo editing bus."""
from audio_contract import stereo_input

MAX_CHANNELS = 32


def channel_index(media, physical=None):
    descriptor = media.get('audio_alias')
    if not isinstance(descriptor, dict) or 'channel_index' not in descriptor:
        return None
    if descriptor.get('version') != 1: raise ValueError('Repair the selected channel alias version')
    index = descriptor['channel_index']; source = physical or media
    count = source.get('channels')
    if type(count) is not int or not 1 <= count <= MAX_CHANNELS:
        raise ValueError('Relink this source to measure its first audio stream channels (1–32)')
    if type(index) is not int or not 0 <= index < count:
        raise ValueError('The selected audio channel is unavailable in the physical source; recreate this channel alias')
    streams = source.get('audio_streams')
    if streams is not None and (not isinstance(streams, list) or not streams or not isinstance(streams[0], dict)
                                or type(streams[0].get('channels')) is not int or streams[0].get('channels') != count):
        raise ValueError('Relink this source to resolve inconsistent first audio stream metadata')
    return index


def input_chain(media):
    """Return a dual-mono selected channel or the existing stereo source bus."""
    index = channel_index(media)
    if index is None:
        return stereo_input(media.get('channels'))
    return [f'pan=stereo|c0=c{index}|c1=c{index}', 'aformat=sample_fmts=fltp:channel_layouts=stereo']
