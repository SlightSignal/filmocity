"""Core stereo routing/balance/fade contract shared by export and live fixtures."""
import math


def number(value, default=0):
    try: result = float(value)
    except (ValueError, TypeError, OverflowError): return default
    return result if math.isfinite(result) else default


def destination(sequence, track):
    if track.get('kind') != 'video': return track
    audios = sorted([t for t in sequence['tracks'] if t.get('kind') == 'audio'], key=lambda t:t.get('index', 0))
    return next((t for t in audios if t.get('index') == track.get('index')), audios[0] if audios else track)


def route(sequence, track, clip):
    bus = destination(sequence, track)
    if clip.get('enabled') is False or clip.get('hold') or track.get('muted') or track.get('_mc_audio_disabled') or bus.get('muted'): return None
    if track.get('kind') == 'video' and (clip.get('audio') or {}).get('linked') is False: return None
    if any(t.get('solo') for t in sequence['tracks']) and not (track.get('solo') or bus.get('solo')): return None
    return bus


def balance(value):
    pan = max(-1, min(1, number(value)))
    return min(1, 1-pan), min(1, 1+pan)


def fade_window(clip, duration):
    audio = clip.get('audio') or {}
    settings = [number(audio.get('fade_in')), number(audio.get('fade_out')), audio.get('constant_power') is not False]
    for side in ('in', 'out'):
        transition = clip.get('audio_transition_'+side) or {}
        settings += [transition.get('type') or '', number(transition.get('duration'))]
    window = audio.get('fade_window')
    if isinstance(window, dict) and window.get('settings') == settings:
        old_duration, offset = window.get('duration'), window.get('offset')
        if all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in (old_duration, offset)) and old_duration > 0:
            return dict(duration=old_duration, offset=offset, settings=settings)
    return dict(duration=duration, offset=0, settings=settings)


def fade_spec(clip, duration):
    window = fade_window(clip, duration); duration = window['duration']
    audio = clip.get('audio') or {}; result = []
    default = 'constant_power' if audio.get('constant_power', True) else 'constant_gain'
    for side in ('in', 'out'):
        transition = clip.get('audio_transition_'+side) or {}
        curve = transition.get('type') or default
        if curve not in ('constant_power', 'constant_gain', 'exponential'): curve = 'constant_power'
        length = min(max(0, duration), max(0, number(audio.get('fade_'+side)), number(transition.get('duration'))))
        result.append({'side':side, 'duration':length, 'start':(0 if side == 'in' else max(0,duration-length))-window['offset'], 'curve':curve})
    return result


def fade_chain(clip, duration):
    names = {'constant_power':'qsin', 'constant_gain':'tri', 'exponential':'exp'}
    specs = [f for f in fade_spec(clip, duration) if f['duration'] > 0]
    if not specs: return []
    # afade indexes samples from PTS. Move only its clock, then restore it;
    # no silence is allocated and the clip's placement remains unchanged.
    offset = fade_window(clip, duration)['offset']
    chain = [f"asetpts=PTS+{offset:.12g}/TB"] if offset else []
    chain += [f"afade=t={f['side']}:st={max(0, f['start']+offset):.12g}:d={f['duration']:.12g}:curve={names[f['curve']]}" for f in specs]
    return chain + ([f"asetpts=PTS-{offset:.12g}/TB"] if offset else [])


def stereo_input(channels):
    # Web Audio speaker up-mixing duplicates a mono input at unity. Explicitly
    # match it; libswresample's default mono->stereo otherwise attenuates by 3 dB.
    return (['pan=stereo|c0=c0|c1=c0'] if channels == 1 else []) + ['aformat=sample_fmts=fltp:channel_layouts=stereo']
