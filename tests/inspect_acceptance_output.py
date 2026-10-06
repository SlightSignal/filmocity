"""Verify a real output's metadata and selected decoded colors; optionally decode all.

Does not certify audio fidelity, caption readability, speech accuracy or native UI.
The report is bound to the input SHA-256 and exact FFmpeg/FFprobe binaries.
"""
import argparse
from fractions import Fraction
import math
from pathlib import Path
import platform
import sys
import time

from acceptance_tools import digest, probe, run, tools


def color_argument(value):
    try:
        time_text, rgb = value.split(':'); seconds = float(time_text); values = [int(v) for v in rgb.split(',')]
        if not math.isfinite(seconds) or seconds < 0 or len(values) != 3 or any(v < 0 or v > 255 for v in values): raise ValueError()
        return {'time': seconds, 'rgb': values}
    except ValueError: raise argparse.ArgumentTypeError('Use seconds:R,G,B, for example 0.25:255,0,0') from None


def inspect(path, selected, *, width, height, fps, duration, expect_audio=False, colors=(), full_decode=False):
    if width <= 0 or height <= 0 or not math.isfinite(fps) or fps <= 0 or not math.isfinite(duration) or duration <= 0:
        raise ValueError('Dimensions, FPS and duration must be positive and finite')
    path = Path(path).resolve()
    report = {'format': 1, 'created': time.time(), 'status': 'failed', 'host': platform.platform(), 'input': str(path),
              'inspector_sha256': digest(__file__), 'tools': selected, 'checks': [],
              'scope': 'Metadata, selected center pixels and optional full decode; audio perception and caption/visual quality require separate review.'}
    def check(name, actual, expected, passed): report['checks'].append({'name': name, 'actual': actual, 'expected': expected, 'pass': bool(passed)})
    try:
        report['sha256'] = digest(path); report['probe'] = metadata = probe(path, selected)
        video = next((s for s in metadata['streams'] if s['codec_type'] == 'video'), None)
        if not video: raise ValueError('No video stream')
        check('width', video['width'], width, video['width'] == width); check('height', video['height'], height, video['height'] == height)
        actual_fps = float(Fraction(video.get('avg_frame_rate', '0/1')))
        check('fps', actual_fps, fps, abs(actual_fps - fps) <= .001)
        actual_duration = float(video.get('duration', metadata['format'].get('duration', 'nan')))
        tolerance = max(2 / fps, .1)
        check('duration', actual_duration if math.isfinite(actual_duration) else None, {'seconds': duration, 'tolerance': tolerance}, abs(actual_duration - duration) <= tolerance)
        audios = [s for s in metadata['streams'] if s['codec_type'] == 'audio']
        if expect_audio:
            check('audio_present', len(audios), 'at least one stream', bool(audios))
            if audios:
                audio_duration = float(audios[0].get('duration', metadata['format'].get('duration', 'nan')))
                check('audio_duration', audio_duration if math.isfinite(audio_duration) else None, {'seconds': duration, 'tolerance': tolerance}, abs(audio_duration - duration) <= tolerance)
        for expected in colors:
            if expected['time'] >= duration: raise ValueError('Color sample must be inside expected duration')
            data = run([selected['ffmpeg']['path'], '-v', 'error', '-nostdin', '-i', path, '-ss', str(expected['time']), '-map', '0:v:0',
                        '-vf', 'format=rgb24,crop=1:1:iw/2:ih/2', '-frames:v', '1', '-threads', '1', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
            check(f'center_RGB_at_{expected["time"]}', list(data), {'rgb': expected['rgb'], 'channel_tolerance': 12},
                  len(data) == 3 and all(abs(a-b) <= 12 for a, b in zip(data, expected['rgb'])))
        if full_decode:
            run([selected['ffmpeg']['path'], '-v', 'error', '-nostdin', '-xerror', '-err_detect', 'explode', '-i', path,
                 '-map', '0:v:0', '-map', '0:a:0?', '-threads', '1', '-f', 'null', '-'], timeout=max(120, duration * 10))
            check('full_decode', 'completed', 'no decoder error', True)
        report['full_decode'] = bool(full_decode)
        after = digest(path)
        check('input_unchanged', after, report['sha256'], after == report['sha256'])
        report['status'] = 'passed' if all(c['pass'] for c in report['checks']) else 'failed'
    except Exception as error: report['error'] = str(error)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True); parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--ffmpeg-dir', type=Path)
    parser.add_argument('--width', type=int, required=True); parser.add_argument('--height', type=int, required=True)
    parser.add_argument('--fps', type=float, required=True); parser.add_argument('--duration', type=float, required=True)
    parser.add_argument('--expect-audio', action='store_true'); parser.add_argument('--color', action='append', type=color_argument, default=[])
    parser.add_argument('--full-decode', action='store_true'); args = parser.parse_args()
    # Reserve the evidence path before invoking any media tool.
    try:
        with args.output.open('x', encoding='utf-8') as stream:
            import json
            try:
                report = inspect(args.input, tools(args.ffmpeg_dir), width=args.width, height=args.height, fps=args.fps, duration=args.duration,
                    expect_audio=args.expect_audio, colors=args.color, full_decode=args.full_decode)
            except Exception as error: report = {'status': 'failed', 'input': str(args.input), 'error': str(error)}
            json.dump(report, stream, indent=2, ensure_ascii=False, allow_nan=False); stream.write('\n')
    except OSError as error: print(f'FAILED: {error}', file=sys.stderr); return 1
    print(f'{report["status"].upper()}: {args.output}')
    return 0 if report['status'] == 'passed' else 1


if __name__ == '__main__': raise SystemExit(main())
