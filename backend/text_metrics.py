"""Measure shaped drawtext geometry with the renderer's selected FFmpeg/font.

Pillow BASIC rounds individual advances and does not provide HarfBuzz shaping.
It cannot be used to place independently rendered words on those platforms.
"""
from collections import OrderedDict
import hashlib
import os
import re
import subprocess
import threading
import time

from ffmpeg_graph import externalize, _identity
from render_context import RenderContext
from work_budget import work

_CACHE = OrderedDict()
_LOCK = threading.Lock()


def _font_snapshot(font_path, context):
    """Key and measure the same owned bytes, including cache-hit lookups."""
    captured = context.new_file('.ttf')
    digest = hashlib.sha256(); total = 0
    with open(font_path, 'rb') as reader:
        before = os.fstat(reader.fileno())
        if before.st_size > 64*1024*1024: raise ValueError('Selected font exceeds the 64 MiB measurement limit')
        with open(captured, 'wb') as writer:
            while True:
                context.check_cancelled()
                chunk = reader.read(1024*1024)
                if not chunk: break
                total += len(chunk)
                if total > 64*1024*1024: raise ValueError('Selected font exceeds the 64 MiB measurement limit')
                writer.write(chunk); digest.update(chunk)
        after = os.fstat(reader.fileno())
    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns):
        raise ValueError('Selected font changed during measurement capture; try again')
    with open(captured, 'rb') as reader:
        actual = hashlib.file_digest(reader, 'sha256').hexdigest()
    if actual != digest.hexdigest(): raise ValueError('Selected font snapshot could not be verified')
    context.check_cancelled()
    return captured, actual


def measure(font_path, size, texts, *, ffmpeg='ffmpeg'):
    texts = list(dict.fromkeys(texts))
    if (len(texts) > 301 or any(len(text) > 10_000 for text in texts)
            or sum(len(text.encode('utf-8')) for text in texts) > 4*1024*1024):
        raise ValueError('Shaped text measurement exceeds its bounded input limit')
    # Imports remain lazy: graphics planning and rendering share this helper.
    from render import ffpath, textfile
    with RenderContext() as context:
        captured, digest = _font_snapshot(font_path, context)
        identity = (_identity(ffmpeg), digest, size)
        result = {}; pending = []
        with _LOCK:
            for text in texts:
                key = identity + (text,)
                if not text: result[text] = (0, 0, 0, 0)
                elif key in _CACHE: result[text] = _CACHE[key]; _CACHE.move_to_end(key)
                else: pending.append(text)
        if not pending: return result
        font_argument = context.font_file(captured)
        for start in range(0, len(pending), 16):
            batch = pending[start:start+16]; filters = []
            for text in batch:
                filters.append("drawtext=" + textfile(text, context=context) +
                    f":fontfile='{ffpath(font_argument)}':fontsize={size}:expansion=none:" +
                    "x='print(1000000000+text_w);print(2000000000+text_h);" +
                    "print(3000000000+max_glyph_a);print(4000000000+line_h);0':y=0")
            command = [identity[0][0], '-hide_banner', '-v', 'info', '-nostdin', '-f', 'lavfi', '-i',
                       'color=s=16x16:r=1:d=1', '-filter_complex', ','.join(filters),
                       '-frames:v', '1', '-f', 'null', '-']
            command = externalize(command, context)
            errors = context.new_file('.text-metrics-log')
            flags = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
            with work(context.holder, 'probe', check=context.check_cancelled):
                with open(errors, 'wb') as stderr:
                    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=stderr, cwd=context.command_cwd, **flags)
                    context.holder['proc'] = child
                    try:
                        deadline = time.monotonic() + 10
                        while child.poll() is None:
                            context.check_cancelled()
                            if time.monotonic() >= deadline: raise ValueError('Shaped text measurement timed out')
                            if os.path.getsize(errors) > 2*1024*1024: raise ValueError('Shaped text measurement exceeded its diagnostic limit')
                            time.sleep(.02)
                        context.check_cancelled()
                        if child.returncode: raise ValueError('Selected font could not be measured by FFmpeg')
                    finally:
                        if child.poll() is None: child.kill()
                        child.wait()
                        if context.holder.get('proc') is child: context.holder.pop('proc', None)
            if os.path.getsize(errors) > 2*1024*1024: raise ValueError('Shaped text measurement exceeded its diagnostic limit')
            with open(errors, 'r', encoding='utf-8', errors='replace') as stream: log = stream.read(2*1024*1024+1)
            if len(log) > 2*1024*1024: raise ValueError('Shaped text measurement exceeded its diagnostic limit')
            values = [float(value) for value in re.findall(r'\[Eval @ [^\]]+\]\s+([0-9]+\.[0-9]+)', log)]
            if len(values) != 8*len(batch): raise ValueError('FFmpeg returned incomplete shaped text metrics')
            for index, text in enumerate(batch):
                raw = values[index*8:index*8+8]
                if raw[:4] != raw[4:]: raise ValueError('FFmpeg returned unstable shaped text metrics')
                metrics = tuple(round(value-(field+1)*1_000_000_000) for field,value in enumerate(raw[:4]))
                if not text.strip(): metrics = (metrics[0], 0, 0, metrics[3])
                if (any(value < 0 or value > 40_000_000 for value in (metrics[0],metrics[1],metrics[3]))
                        or not -2048 <= metrics[2] <= 2048): raise ValueError('FFmpeg returned invalid shaped text metrics')
                result[text] = metrics
                with _LOCK:
                    _CACHE[identity+(text,)] = metrics
                    while len(_CACHE) > 256: _CACHE.popitem(last=False)
    return result
