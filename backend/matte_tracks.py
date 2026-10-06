"""Read-only track dependencies shared by preflight and export graph construction."""
from effects import track_matte_params
from audio_contract import route as audio_route


def track_dependencies(sequence):
    tracks = sequence.get('tracks', [])
    by_id = {t['id']: t for t in tracks}
    required, mattes, done, issues = set(), set(), set(), []

    def issue(code, message, track, clip):
        issues.append({'severity': 'error', 'code': code, 'message': message,
                       'sequence': sequence['id'], 'track': track['id'], 'clip': clip['id']})

    def visit(track, stack, as_matte=False):
        tid = track['id']
        required.add(tid)
        picture_needed = as_matte or not track.get('_mc_picture_hidden')
        key = (tid, picture_needed)
        if key in done:
            return
        for clip in track.get('clips', []):
            if clip.get('enabled') is False or track.get('kind') != 'video' or not picture_needed:
                continue
            params = track_matte_params(clip)
            if not params:
                continue
            target = params['track']
            if not isinstance(target, str) or target not in by_id:
                issue('missing_matte_track', f'Track matte source unavailable: {target!r}. Choose an existing video track.', track, clip)
                continue
            source = by_id[target]
            if source.get('kind') != 'video':
                issue('invalid_matte_track', f'Track matte source {target} must be a video track.', track, clip)
                continue
            if params['type'] not in ('alpha', 'luma'):
                issue('invalid_matte_type', 'Track matte type must be alpha or luma.', track, clip)
                continue
            if clip.get('adjustment'):
                issue('adjustment_matte', 'Apply Track Matte Key to a media, title, graphics or nested clip instead of an adjustment layer.', track, clip)
                continue
            mattes.add(target)
            if target in (*stack, tid):
                issue('matte_cycle', 'Track matte cycle: ' + ' → '.join((*stack, tid, target)), track, clip)
                continue
            if len(stack) >= 31:
                issue('matte_depth', 'Track matte dependencies exceed 32 tracks.', track, clip)
                continue
            visit(source, (*stack, tid), True)
        done.add(key)

    for track in tracks:
        if not track.get('muted'):
            visit(track, ())
    return [t for t in tracks if t['id'] in required], mattes, issues


def render_clips(sequence, track, matte_ids):
    """Hidden camera tracks need only admitted sound unless used as a matte."""
    for clip in track.get('clips', []):
        if clip.get('enabled') is False: continue
        if track.get('_mc_picture_hidden') and track['id'] not in matte_ids and audio_route(sequence, track, clip) is None: continue
        yield clip
