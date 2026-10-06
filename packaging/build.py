"""Build a separate Windows candidate; never replace an existing executable.

    python packaging/build.py --check
    python packaging/build.py [--ffmpeg-dir PATH] [--output NEW_DIRECTORY]

No dependencies are installed or downloaded by this command. See
packaging/README.md for setup, receipts and native acceptance requirements.
"""
import argparse
import json
from pathlib import Path
import sys
from candidate import preflight, reserve_output, build, CandidateError

ROOT = Path(__file__).resolve().parents[1]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true', help='Report prerequisites as JSON without building or reserving output')
    parser.add_argument('--ffmpeg-dir', help='Directory containing Windows x64 ffmpeg.exe, ffprobe.exe, DLLs and license/notices')
    parser.add_argument('--output', help='New candidate directory; existing paths are never overwritten')
    args = parser.parse_args(argv)
    try:
        report = preflight(ROOT, args.ffmpeg_dir)
        if args.check or not report['ok']:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0 if report['ok'] else 2
        output = reserve_output(ROOT, args.output)
        print(f'Building candidate in {output}', flush=True)
        result = build(ROOT, output, report)
        print(f'Built: {output / result["artifact"]["path"]}\nSHA-256: {result["artifact"]["sha256"]}\nStatus: built, native verification not run\nReceipt: {output / "build.json"}')
        return 0
    except (CandidateError, OSError, ValueError) as error:
        print('BUILD FAILED: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__': raise SystemExit(main())
