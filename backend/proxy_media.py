"""Source-timed SDR proxies with owned inspection and checked publication inputs.

The UI/API select size/quality, never arbitrary encoder arguments. An explicit
encoder parameter supports local codec qualification; production uses libx264.
"""
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import subprocess
import time

from media_metadata import summarize
from input_options import validated_input_options

SIZES = (640, 1280, 1920)
QUALITIES = {'draft': 28, 'balanced': 23, 'high': 18}


def settings(value=None):
    value = {} if value is None else value
    if not isinstance(value, dict) or set(value) - {'max_edge', 'quality'}:
        raise ValueError('Proxy settings require a size and quality')
    edge, quality = value.get('max_edge', 1280), value.get('quality', 'balanced')
    if type(edge) is not int or edge not in SIZES or not isinstance(quality, str) or quality not in QUALITIES:
        raise ValueError('Choose a proxy size of 640, 1280 or 1920 and draft, balanced or high quality')
    return {'max_edge': edge, 'quality': quality}


def dimensions(info, policy):
    width, height = float(info.get('width') or 0), float(info.get('height') or 0)
    try: sar = Fraction(str(info.get('sample_aspect_ratio') or '1:1').replace(':', '/'))
    except (ValueError, ZeroDivisionError): sar = Fraction(1)
    if sar <= 0: sar = Fraction(1)
    # Imported dimensions already include display rotation; SAR follows its axis.
    if int(info.get('rotation') or 0) % 180: height *= float(sar)
    else: width *= float(sar)
    if not all(math.isfinite(v) and v >= 2 for v in (width, height)):
        raise ValueError('Source display dimensions are unavailable')
    scale = min(1, policy['max_edge'] / max(width, height))
    return tuple(max(2, int(v * scale / 2) * 2) for v in (width, height))


def options(info, policy=None, *, encoder='libx264'):
    policy = settings(policy)
    from source_color import dynamic_range
    if dynamic_range(info) in ('pq', 'hlg') or dynamic_range(info) == 'unknown' and info.get('hdr'):
        raise ValueError('HDR proxies need a qualified color transform. Use the original or a rendered preview for now.')
    if info.get('color_primaries') == 'bt2020':
        raise ValueError('Wide-gamut proxies need a qualified color transform. Use the original or a rendered preview for now.')
    width, height = dimensions(info, policy)
    if encoder == 'libx264': codec = ['-c:v', encoder, '-preset', 'veryfast', '-crf', str(QUALITIES[policy['quality']])]
    elif encoder == 'libopenh264':
        # Explicit qualification path for hosts without x264; never an automatic fallback.
        bitrate = {'draft': '1M', 'balanced': '3M', 'high': '6M'}[policy['quality']]
        codec = ['-c:v', encoder, '-b:v', bitrate]
    else: raise ValueError('Unsupported proxy encoder')
    return ['-map', '0:V:0', '-map', '0:a:0?', '-sn', '-dn',
            '-vf', f'scale={width}:{height},setsar=1', '-fps_mode:v', 'passthrough', '-enc_time_base:v', 'demux',
            *codec, '-pix_fmt', 'yuv420p', '-force_key_frames', 'expr:gte(t,n_forced*1)',
            '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-map_metadata', '-1', '-metadata:s:v:0', 'rotate=0']


def inspect_file(path, context, *, ffprobe='ffprobe', input_opts=(), frames=False):
    from work_budget import work
    with context.use(), work(context.holder, 'probe', check=context.check_cancelled):
        return _inspect_file(path, context, ffprobe=ffprobe, input_opts=input_opts, frames=frames)


def _inspect_file(path, context, *, ffprobe='ffprobe', input_opts=(), frames=False):
    """Probe into owned files, with bounded memory, cancellation and child reaping."""
    source_options = validated_input_options(input_opts)
    output, errors = context.new_file('.probe'), context.new_file('.log')
    args = [ffprobe, '-v', 'error', *source_options, '-i', os.fspath(path)]
    if frames: args += ['-select_streams', 'V:0', '-show_entries', 'frame=best_effort_timestamp_time', '-of', 'csv=p=0']
    else: args += ['-show_streams', '-show_format', '-of', 'json']
    context.check_cancelled()
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    with open(output, 'wb') as stdout, open(errors, 'wb') as stderr:
        child = subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=stdout, stderr=stderr, **kwargs)
        context.holder['proc'] = child
        deadline = time.monotonic() + 1800
        try:
            while child.poll() is None:
                context.check_cancelled()
                if time.monotonic() > deadline: raise ValueError('Proxy inspection timed out')
                time.sleep(.05)
            context.check_cancelled()
            if child.returncode:
                with open(errors, 'rb') as diagnostic:
                    diagnostic.seek(max(0, os.path.getsize(errors)-500))
                    raise ValueError('Proxy inspection failed: ' + diagnostic.read().decode('utf-8', 'replace'))
        finally:
            if child.poll() is None: child.kill()
            child.wait()
            if context.holder.get('proc') is child: context.holder.pop('proc', None)
    if frames: return output
    if os.path.getsize(output) > 4_000_000: raise ValueError('Proxy metadata exceeds the inspection limit')
    return json.loads(Path(output).read_text(encoding='utf-8'))


def timestamps(path):
    with open(path, encoding='utf-8') as stream:
        previous = None
        for line in stream:
            value = line.strip().split(',')[0]
            if not value: continue
            try: current = float(value)
            except ValueError as error: raise ValueError('Source has missing picture timestamps') from error
            if not math.isfinite(current) or (previous is not None and current <= previous):
                raise ValueError('Source has invalid or repeated picture timestamps')
            previous = current
            yield current


def check_proxy(source, output, source_frames, proxy_frames, info, policy, *, check=lambda: None):
    """Compare all decoded picture PTS plus primary audio start/rate/channels.

    AAC may retain one leading priming packet and one trailing padding packet.
    Validate their separate clock endpoints rather than adding durations whose
    origins differ. This is a timing/metadata
    check, not a perceptual picture/color/listening or browser acceptance test.
    """
    from itertools import zip_longest
    actual = summarize(output)
    if (actual['width'], actual['height']) != dimensions(info, settings(policy)) or actual['rotation'] % 360:
        raise ValueError('Proxy display dimensions or rotation differ from the requested output')
    if actual['vcodec'] != 'h264': raise ValueError('Proxy did not encode H.264')
    streams = source.get('streams', []); produced = output.get('streams', [])
    origin = float((source.get('format') or {}).get('start_time') or 0)
    source_video = next(s for s in streams if s.get('codec_type') == 'video' and not (s.get('disposition') or {}).get('attached_pic'))
    output_video = next(s for s in produced if s.get('codec_type') == 'video')
    if source_video.get('duration') and output_video.get('duration'):
        if abs(float(source_video['duration']) - float(output_video['duration'])) > .0011:
            raise ValueError('Proxy picture duration differs from the original')
    count, max_error = 0, 0.0
    for expected, observed in zip_longest(timestamps(source_frames), timestamps(proxy_frames)):
        if expected is None or observed is None: raise ValueError('Proxy picture count differs from the original')
        error = abs(expected - origin - observed)
        # ffprobe prints microseconds. MP4 also rounds edit-list origins to ms.
        if error > .0011: raise ValueError('Proxy picture timestamps differ from the original')
        count += 1; max_error = max(max_error, error)
        if count % 1000 == 0: check()
    check()
    if not count: raise ValueError('Proxy contains no decoded pictures')
    a = next((s for s in streams if s.get('codec_type') == 'audio'), None)
    b = next((s for s in produced if s.get('codec_type') == 'audio'), None)
    if bool(a) != bool(b): raise ValueError('Proxy primary audio is missing or unexpected')
    audio_error = audio_end_error = None
    if a:
        if (a.get('sample_rate'), a.get('channels')) != (b.get('sample_rate'), b.get('channels')):
            raise ValueError('Proxy audio sample rate or channel count changed')
        if a.get('start_time') is None or b.get('start_time') is None:
            raise ValueError('Cannot verify the primary audio start time')
        audio_error = abs(float(a['start_time']) - origin - float(b['start_time']))
        # Positive-offset AAC may retain one leading priming packet. Its silent
        # decoded samples precede the intended audio, rather than moving it.
        priming = 1024 / int(a['sample_rate']) if b.get('codec_name') == 'aac' else 0
        delta = float(b['start_time']) - (float(a['start_time']) - origin)
        if delta > .002 or delta < -priming - .002: raise ValueError('Proxy primary audio starts out of sync')
        if a.get('duration') and b.get('duration'):
            expected_end = float(a['start_time']) - origin + float(a['duration'])
            actual_end = float(b['start_time']) + float(b['duration'])
            audio_end_error = actual_end - expected_end
            if audio_end_error < -.002 or audio_end_error > priming + .002:
                raise ValueError('Proxy primary audio duration changed')
    return {'policy': settings(policy), 'width': actual['width'], 'height': actual['height'],
            'frame_rate': actual['frame_rate'], 'duration': actual['duration'], 'frames': count,
            'max_timestamp_error_seconds': max_error, 'audio_start_error_seconds': audio_error,
            'audio_end_error_seconds': audio_end_error,
            'audio_priming_tolerance_seconds': 1024 / int(a['sample_rate']) if a else 0,
            'sample_rate': actual['sample_rate'], 'channels': actual['channels'], 'codec': actual['vcodec'],
            'validation': 'all_picture_timestamps_and_primary_audio_metadata',
            'audio_streams_omitted': max(0, len(info.get('audio_streams') or []) - 1)}
