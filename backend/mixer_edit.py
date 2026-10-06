"""Validated, ID-addressed edits to existing audio buses and sequence masters."""
import copy
import math
from audio_contract import destination

GAIN_MIN, GAIN_MAX = -96, 24
FX_RANGES = {
    'eq': {'low_db': (-12, 12), 'mid_db': (-12, 12), 'high_db': (-12, 12)},
    'comp': {'threshold_db': (-60, 0), 'ratio': (1, 20), 'attack_ms': (.01, 2000),
             'release_ms': (.01, 9000), 'makeup_db': (0, 36)},
    'denoise': {'db': (1, 40)},
}


def finite(value, label, low, high):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f'{label} must be a finite number from {low:g} to {high:g}')


def effects(value):
    if not isinstance(value, dict): raise ValueError('Bus processing must be an object')
    for group, fields in FX_RANGES.items():
        if group not in value: continue
        setting = value[group]
        if not isinstance(setting, dict): raise ValueError(f'{group} processing must be an object')
        for field, (lo, hi) in fields.items():
            if field in setting: finite(setting[field], f'{group}.{field}', lo, hi)
        if 'enabled' in setting and type(setting['enabled']) is not bool:
            raise ValueError(f'{group}.enabled must be true or false')
    if 'limiter' in value and type(value['limiter']) is not bool:
        raise ValueError('Limiter must be true or false')
    # Unrecognized extensions are retained by the client, not interpreted here.


def target(project, op):
    if not isinstance(op.get('sequence'), str) or 'track' not in op or (op['track'] is not None and not isinstance(op['track'], str)):
        raise ValueError('Choose a sequence ID and track ID, or null for its master')
    sequences = [s for s in project['sequences'] if s['id'] == op['sequence']]
    if len(sequences) != 1: raise ValueError('Mixer sequence is missing or ambiguous')
    seq = sequences[0]
    base = f"/sequences/{project['sequences'].index(seq)}"
    if op['track'] is None:
        if seq.get('master') is not None and not isinstance(seq['master'], dict): raise ValueError('Sequence master is invalid')
        return seq, seq.get('master') or {}, base + '/master'
    tracks = [t for t in seq['tracks'] if t['id'] == op['track']]
    if len(tracks) != 1: raise ValueError('Mixer track is missing or ambiguous')
    track = tracks[0]
    if track.get('kind') not in ('audio', 'video') or destination(seq, track) is not track:
        raise ValueError('Use the linked audio destination for this video track')
    return seq, track, base + f"/tracks/{seq['tracks'].index(track)}"


def validate(project, op):
    target(project, op)
    changes = op.get('changes')
    allowed = {'gain_db', 'audio_fx'} if op['track'] is None else {'gain_db', 'muted', 'solo', 'audio_fx'}
    if not isinstance(changes, dict) or not changes or not set(changes) <= allowed:
        raise ValueError('Mixer changes must contain supported gain, mute, solo or processing fields')
    if 'gain_db' in changes: finite(changes['gain_db'], 'Bus gain (dB)', GAIN_MIN, GAIN_MAX)
    for key in ('muted', 'solo'):
        if key in changes and type(changes[key]) is not bool: raise ValueError(f'{key} must be true or false')
    if 'audio_fx' in changes: effects(changes['audio_fx'])


def apply(project, op):
    validate(project, op)
    seq, bus, _ = target(project, op)
    before = {'parent_present': 'master' in seq, 'parent_null': seq.get('master') is None,
              'values': {k: copy.deepcopy(bus[k]) for k in op['changes'] if k in bus},
              'missing': [k for k in op['changes'] if k not in bus]}
    if op['track'] is None:
        if not isinstance(seq.get('master'), dict): seq['master'] = {}
        bus = seq['master']
    bus.update(copy.deepcopy(op['changes']))
    return before


def inverse(project, op, before):
    """Compatibility inverse for older history readers; current history is a diff."""
    _, _, path = target(project, op)
    if op['track'] is None and not before['parent_present']: return [{'op': 'remove', 'path': path}]
    if op['track'] is None and before['parent_null']: return [{'op': 'set', 'path': path, 'value': None}]
    return ([{'op': 'set', 'path': path+'/'+k, 'value': copy.deepcopy(v)} for k, v in before['values'].items()] +
            [{'op': 'remove', 'path': path+'/'+k} for k in before['missing']])
