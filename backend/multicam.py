"""Read-only multicamera selection shared by preflight and export.

The multicamera selector owns track selection in this temporary view. Saved
track mute flags are unchanged (legacy creation used them for angle selection).
Clip enable/link controls and solo admission still apply within selected tracks.
"""
from audio_contract import destination


def view(sequence, angle=0):
    videos = sorted((t for t in sequence['tracks'] if t['kind'] == 'video'), key=lambda t:t['index'])
    audios = sorted((t for t in sequence['tracks'] if t['kind'] == 'audio'), key=lambda t:t['index'])
    if isinstance(angle, bool) or not isinstance(angle, int) or not 0 <= angle < len(videos):
        raise ValueError('Choose an existing multicamera angle')
    camera = videos[angle]
    follow = sequence.get('multicam_audio') == 'follow'
    wanted = sequence.get('multicam_audio_track')
    if follow:
        sound = destination(sequence, camera)
        sound_camera = camera
    elif audios:
        sound = next((t for t in audios if t['id'] == wanted), None) if wanted else audios[0]
        if sound is None: raise ValueError('The fixed multicamera audio track is unavailable')
        sound_camera = next((t for t in videos if t['index'] == sound['index']), None)
    else:
        sound = sound_camera = videos[0]
    tracks = []
    for track in sequence['tracks']:
        value = dict(track)
        if track['kind'] == 'video':
            value['_mc_picture_hidden'] = track['id'] != camera['id']
            value['_mc_audio_disabled'] = sound_camera is None or track['id'] != sound_camera['id']
            value['muted'] = value['_mc_picture_hidden'] and value['_mc_audio_disabled']
        elif track['kind'] == 'audio': value['muted'] = track['id'] != sound['id']
        # A solo on an excluded angle must not silence the selected mix.
        if value.get('muted'): value['solo'] = False
        tracks.append(value)
    return {**sequence, 'multicam':False, '_multicam_angle':angle, 'tracks':tracks}
