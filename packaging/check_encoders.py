"""Record real delivery-encoder smoke checks against explicitly selected tools.

Run from the candidate's captured inputs for Windows acceptance. A successful
small SDR probe is not a complete export, audio or native-window test.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'backend'))
from encoder_capabilities import EncoderCapabilities, PROFILES


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ffmpeg-dir', required=True, help='Exact directory containing ffmpeg and ffprobe to test')
    parser.add_argument('--output', required=True, help='New JSON evidence file; existing files are never overwritten')
    parser.add_argument('--encoder', action='append', choices=list(PROFILES), help='Limit checks; repeat for multiple encoders')
    args = parser.parse_args(argv)
    # Reserve before probing so an accidental rerun never overwrites evidence.
    with Path(args.output).open('x', encoding='utf-8') as stream:
        report = {'format': 1, 'created': time.time(), 'host': platform.platform(),
                  'python': platform.python_version(), 'native_windows': os.name == 'nt',
                  'source_root': str(ROOT), 'source': {name: digest(ROOT / name) for name in (
                      'backend/encoder_capabilities.py', 'backend/render.py', 'packaging/check_encoders.py')},
                  'scope': 'Four 320x180 SDR frames, metadata and decoded pixels. Full exports/native UI remain separate.',
                  'checks': []}
        try:
            folder = Path(args.ffmpeg_dir).resolve(); suffix = '.exe' if os.name == 'nt' else ''
            cap = EncoderCapabilities(str(folder / ('ffmpeg' + suffix)), str(folder / ('ffprobe' + suffix)))
            identity = cap.identity()
            report['tools'] = [{'path': item[0], 'sha256': digest(item[0])} for item in identity]
            report['catalog'] = cap.catalog()
            selected = args.encoder or [name for name in PROFILES if not name.endswith('_videotoolbox')]
            for codec in selected:
                report['checks'].append(cap.check(codec, {'format': PROFILES[codec][0]}, refresh=True))
            if cap.identity() != identity:
                raise ValueError('Tools changed during the matrix; discard the results and repeat.')
            # Absent hardware is a recorded device limitation, not a failed
            # software baseline. Explicit selections must all pass.
            required = args.encoder or ['libx264', 'libx265']
            report['ok'] = all(result['ok'] for result in report['checks'] if result['encoder'] in required)
            report['required'] = required
        except (OSError, ValueError) as error:
            report.update(ok=False, error=str(error))
        json.dump(report, stream, indent=2, ensure_ascii=False); stream.write('\n')
    print(f"Encoder report: {Path(args.output).resolve()} — {'passed' if report['ok'] else 'needs attention'}")
    return 0 if report['ok'] else 2


if __name__ == '__main__': raise SystemExit(main())
