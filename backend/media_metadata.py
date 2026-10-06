"""Source metadata from ffprobe, without invented sample rates/channel layouts."""
from fractions import Fraction
import math
from source_color import dynamic_range


def positive_int(value):
    if isinstance(value, bool): return None
    try:
        number = int(value)
        return number if number > 0 and str(number) == str(value) else None
    except (TypeError, ValueError, OverflowError): return None


def rate(value):
    try:
        result = Fraction(str(value))
        return result if 0 < result <= 1000 else None
    except (ValueError, ZeroDivisionError, TypeError, OverflowError): return None


def duration(value):
    try:
        result = float(value)
        return result if math.isfinite(result) and result >= 0 else None
    except (TypeError, ValueError, OverflowError): return None


def summarize(document):
    streams = document.get('streams', [])
    if not isinstance(streams, list): raise ValueError('Media probe returned invalid streams')
    streams = [s for s in streams if isinstance(s, dict)]
    video = next((s for s in streams if s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic')), None)
    audios = [s for s in streams if s.get('codec_type') == 'audio']
    audio = audios[0] if audios else None
    if not video and not audio: raise ValueError('No supported video or audio stream was found')
    fmt = document.get('format') or {}; v, a = video or {}, audio or {}
    average, nominal = rate(v.get('avg_frame_rate')), rate(v.get('r_frame_rate'))
    fps = average or nominal
    frames = positive_int(v.get('nb_frames'))
    format_names = set(str(fmt.get('format_name', '')).split(','))
    still_formats = {'image2', 'image2pipe', 'png_pipe', 'jpeg_pipe', 'bmp_pipe', 'tiff_pipe', 'webp_pipe', 'jpegls_pipe'}
    is_image = bool(video) and not audio and (bool(format_names & still_formats) or (v.get('codec_name') == 'gif' and frames == 1))
    dur = duration(fmt.get('duration'))
    if dur is None: dur = max([duration(s.get('duration')) or 0 for s in [v, a]])
    rotation = 0
    for raw in [s.get('rotation') for s in (v.get('side_data_list') or []) if isinstance(s, dict)] + [(v.get('tags') or {}).get('rotate')]:
        try:
            if raw is not None: rotation = int(float(raw)); break
        except (ValueError, TypeError, OverflowError): continue
    width, height = positive_int(v.get('width')) or 0, positive_int(v.get('height')) or 0
    if rotation % 180: width, height = height, width
    audio_streams = [{'index':s.get('index'), 'codec':s.get('codec_name'), 'sample_rate':positive_int(s.get('sample_rate')),
                      'channels':positive_int(s.get('channels')), 'channel_layout':s.get('channel_layout') or None} for s in audios]
    color = {'color_transfer':v.get('color_transfer'), 'color_primaries':v.get('color_primaries')}
    light = {}
    for side in v.get('side_data_list') or []:
        if not isinstance(side, dict): continue
        fields = {'Content light level metadata': [('max_content', 'hdr_max_cll')], 'Mastering display metadata': [('max_luminance', 'hdr_mastering_peak_nits')]}.get(side.get('side_data_type'), [])
        for source, target in fields:
            try:
                value = float(Fraction(str(side[source])))
                if math.isfinite(value) and value > 0: light[target] = value
            except (KeyError, ValueError, TypeError, ZeroDivisionError, OverflowError): pass
    return {**light, 'hdr':bool(video) and dynamic_range(color) in ('pq','hlg'), 'dynamic_range':dynamic_range(color), 'wide_gamut':v.get('color_primaries') == 'bt2020',
            'rotation':rotation, 'sample_aspect_ratio':v.get('sample_aspect_ratio') or None, 'color_transfer':v.get('color_transfer'), 'color_primaries':v.get('color_primaries'), 'color_space':v.get('color_space'), 'color_range':v.get('color_range'),
            'pix_fmt':v.get('pix_fmt'), 'vcodec':v.get('codec_name'), 'acodec':a.get('codec_name'),
            # Different declared rates suggest VFR; this is not a timestamp scan.
            'vfr':bool(average and nominal and average != nominal),
            'duration':5.0 if is_image else dur, 'width':width, 'height':height, 'is_image':is_image,
            'fps':0.0 if is_image or fps is None else float(fps),
            'frame_rate':None if is_image or fps is None else f'{fps.numerator}/{fps.denominator}',
            'has_video':video is not None, 'has_audio':audio is not None, 'codec':v.get('codec_name') if video else a.get('codec_name'),
            'sample_rate':positive_int(a.get('sample_rate')), 'channels':positive_int(a.get('channels')), 'channel_layout':a.get('channel_layout') or None,
            'audio_streams':audio_streams}
