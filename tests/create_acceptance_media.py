"""Create a disposable, reproducible Windows QA kit. Never uses a project library.

Requires Pillow and FFmpeg/FFprobe. Synthetic transcripts test editing, NOT ASR.
Default FFV1/PCM fixtures are lossless; --h264 selects libx264/AAC MP4 instead.
"""
import argparse
import array
import math
from pathlib import Path
import platform
import sys
import tempfile
import wave

from acceptance_tools import digest, probe, run, tools, write_json

COLORS = [(255, 0, 0), (0, 0, 255), (0, 255, 0), (255, 255, 0), (255, 0, 255), (0, 255, 255)]


def audio(path, hum=False):
    samples = array.array('h')
    for i in range(6 * 48000):
        t = i / 48000
        # 100 ms beeps exactly at each second; left amplitude is twice right.
        value = .2 * math.sin(2 * math.pi * 1000 * t) if hum or i % 48000 < 4800 else 0
        if hum: value += .08 * math.sin(2 * math.pi * 50 * t)
        samples.extend((round(value * 32767), round(value * .5 * 32767)))
    if sys.byteorder != 'little': samples.byteswap()
    with wave.open(str(path), 'wb') as stream:
        stream.setparams((2, 2, 48000, 0, 'NONE', 'not compressed')); stream.writeframes(samples.tobytes())


def project(paths, minutes=None):
    clips = 2 if minutes is None else minutes * 10
    media = {key: {'id': key, 'name': Path(path).name, 'path': str(Path(path).resolve()), 'duration': 6,
             'width': 640, 'height': 360, 'fps': 30, 'has_audio': True, 'has_video': True, 'is_image': False} for key, path in paths.items()}
    seq = {'id': 'qa-reference', 'name': 'QA · red then blue' if minutes is None else f'QA · {minutes} minutes',
           'width': 640, 'height': 360, 'fps': 30, 'markers': [{'id': 'blue', 'name': 'Blue begins', 'time': 6}], 'captions': [],
           'tracks': [{'id': 'V1', 'kind': 'video', 'index': 1, 'muted': False, 'clips': [
               {'id': f'qa-{i}', 'media_id': 'A' if i % 2 == 0 else 'B', 'start': i * 6, 'in_': 0, 'out': 6, 'speed': 1} for i in range(clips)]}],
           'transcript': [{'w': ('Red' if i % 24 < 12 else 'Blue') if i % 2 == 0 else f'word{i}.',
                's': round((i // 2) + (.1 if i % 2 == 0 else .55), 6),
                'e': round((i // 2) + (.4 if i % 2 == 0 else .85), 6), 'p': .4 if i % 19 == 0 else .98} for i in range(clips * 12)]}
    return {'id': 'qa-long' if minutes else 'qa-reference', 'name': 'Filmocity synthetic QA fixture', 'version': 3,
            'media': media, 'sequences': [seq]}


def generate(output, selected, *, h264=False, long_minutes=20):
    if not 1 <= long_minutes <= 120: raise ValueError('Long fixture must be 1–120 minutes')
    output = Path(output).resolve(); output.mkdir(parents=True, exist_ok=False)
    report = {'format': 1, 'status': 'incomplete', 'host': platform.platform(), 'tools': selected,
        'generator_sha256': digest(__file__), 'h264': h264,
        'scope': 'Synthetic tones, pictures and transcript words. Does not test speech recognition accuracy.', 'files': []}
    try:
        from PIL import Image, ImageDraw
        audio(output / 'Stereo beeps.wav'); audio(output / 'Dialogue cleanup tones.wav', hum=True)
        ffmpeg = selected['ffmpeg']['path']; ext = '.mp4' if h264 else '.mkv'
        codec = ['-c:v', 'libx264', '-preset', 'veryfast', '-crf', '18', '-c:a', 'aac', '-b:a', '192k'] if h264 else ['-c:v', 'ffv1', '-level', '3', '-c:a', 'pcm_s16le']
        paths = {}
        for key, folder, color in [('A', "Camera A · O'Neill", '0xff0000'), ('B', 'Camera B · 東京', '0x0000ff')]:
            parent = output / folder; parent.mkdir(); path = parent / ('Same name' + ext); paths[key] = path
            run([ffmpeg, '-v', 'error', '-nostdin', '-n', '-f', 'lavfi', '-i', f'color=c={color}:s=640x360:r=30:d=6',
                '-i', output / 'Stereo beeps.wav', '-map', '0:v:0', '-map', '1:a:0', *codec, '-pix_fmt', 'yuv420p', '-threads', '1', '-t', '6', path])
        with tempfile.TemporaryDirectory(prefix='filmocity-qa-frames-') as directory:
            frames = Path(directory)
            for n in range(180):
                picture = Image.new('RGB', (640, 360), COLORS[n // 30]); draw = ImageDraw.Draw(picture)
                draw.rectangle((0, 0, 235, 32), fill='black'); draw.text((8, 8), f'FRAME {n:03d} / 180   {n / 30:05.2f}s', fill='white')
                x = (n % 30) * 20; draw.rectangle((x, 320, x + 19, 359), fill='white')
                if n % 30 < 3: draw.rectangle((600, 0, 639, 39), fill='white')
                picture.save(frames / f'frame{n:04d}.png')
            run([ffmpeg, '-v', 'error', '-nostdin', '-n', '-framerate', '30', '-i', frames / 'frame%04d.png',
                '-i', output / 'Stereo beeps.wav', '-map', '0:v:0', '-map', '1:a:0', *codec, '-pix_fmt', 'yuv420p', '-threads', '1', '-t', '6', output / ('Seek and sync' + ext)])
        overlay = Image.new('RGBA', (320, 180), (0, 0, 0, 0)); draw = ImageDraw.Draw(overlay)
        draw.rectangle((20, 20, 300, 160), fill=(255, 255, 255, 128)); draw.text((40, 70), 'ALPHA 50%', fill=(0, 0, 0, 255))
        overlay.save(output / 'Half transparent.png')
        (output / 'Unicode captions.srt').write_text("1\n00:00:00,100 --> 00:00:02,000\nÉmile, O’Neill & 東京\n\n2\n00:00:03,000 --> 00:00:05,500\nTwo lines\nwith punctuation!\n", encoding='utf-8')
        write_json(output / 'reference-project.json', project(paths))
        write_json(output / 'long-project.json', project(paths, long_minutes))
        report['expected'] = {'camera_A_RGB': COLORS[0], 'camera_B_RGB': COLORS[1], 'video': {'width': 640, 'height': 360, 'fps': 30, 'seconds': 6, 'frames': 180},
            'audio': {'rate': 48000, 'channels': 2, 'seconds': 6, 'tone_Hz': 1000, 'beep_seconds': .1, 'left_right_amplitude_ratio': 2},
            'seek_colors_per_second': COLORS, 'reference_seconds': 12, 'long_minutes': long_minutes,
            'story': {'selected_word_indices': [0, 1, 22, 23], 'padding': 0, 'source_ranges': [[.1, .85], [11.1, 11.85]], 'seconds': 1.5,
                      'export_samples': [{'time': .25, 'rgb': COLORS[0]}, {'time': 1, 'rgb': COLORS[1]}]}}
        for path in sorted(output.rglob('*')):
            if path.is_file():
                item = {'path': path.relative_to(output).as_posix(), 'bytes': path.stat().st_size, 'sha256': digest(path)}
                if path.suffix.lower() in ('.mp4', '.mkv', '.wav', '.png'): item['probe'] = probe(path, selected)
                report['files'].append(item)
        report['status'] = 'created'
    except Exception as error:
        report['status'] = 'failed'; report['error'] = str(error); raise
    finally:
        write_json(output / 'fixture-manifest.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True, help='NEW directory; existing directories are refused')
    parser.add_argument('--ffmpeg-dir', type=Path); parser.add_argument('--h264', action='store_true')
    parser.add_argument('--long-minutes', type=int, default=20)
    args = parser.parse_args()
    try:
        result = generate(args.output, tools(args.ffmpeg_dir), h264=args.h264, long_minutes=args.long_minutes)
    except Exception as error: print(f'FAILED: {error}', file=sys.stderr); return 1
    print(f'Created {len(result["files"])} files: {args.output.resolve()}\nSynthetic words are for editing tests; use a real recording for speech recognition.')
    return 0


if __name__ == '__main__': raise SystemExit(main())
