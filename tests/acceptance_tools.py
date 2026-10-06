"""Small standard-library helpers shared by the Windows acceptance commands."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def run(args, *, timeout=120):
    result = subprocess.run([str(a) for a in args], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout)
    if result.returncode:
        raise RuntimeError(f'{Path(args[0]).name} exited {result.returncode}: ' + result.stderr.decode('utf-8', 'replace')[-4000:])
    return result.stdout


def tools(folder=None):
    result = {}
    for name in ('ffmpeg', 'ffprobe'):
        path = Path(folder) / (name + ('.exe' if os.name == 'nt' else '')) if folder else shutil.which(name)
        if not path or not Path(path).is_file(): raise ValueError(f'{name} not found; supply --ffmpeg-dir')
        path = Path(path).resolve()
        result[name] = {'path': str(path), 'sha256': digest(path), 'version': run([path, '-version']).decode('utf-8', 'replace').splitlines()[0]}
    return result


def probe(path, selected):
    return json.loads(run([selected['ffprobe']['path'], '-v', 'error', '-show_format', '-show_streams', '-of', 'json', path]))


def write_json(path, value):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False, allow_nan=False)
        stream.write('\n')
