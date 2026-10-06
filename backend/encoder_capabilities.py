"""Bounded local encoder checks; a listed codec is not proof of usable hardware.

The smoke test uses the same H.264/HEVC video arguments as export. It checks a
small SDR encode and software decode, not every resolution/profile or GPU load.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import threading
import time

PROFILES = {
    'libx264': ('h264', 'Software H.264', False),
    'h264_nvenc': ('h264', 'NVIDIA H.264', True),
    'h264_qsv': ('h264', 'Intel H.264', True),
    'h264_amf': ('h264', 'AMD H.264', True),
    'libx265': ('hevc', 'Software HEVC', False),
    'hevc_nvenc': ('hevc', 'NVIDIA HEVC', True),
    'hevc_qsv': ('hevc', 'Intel HEVC', True),
    'hevc_amf': ('hevc', 'AMD HEVC', True),
    # Retain existing portable source behavior without adding release targets.
    'h264_videotoolbox': ('h264', 'Apple H.264', True),
    'hevc_videotoolbox': ('hevc', 'Apple HEVC', True),
}


def selected_encoder(preset):
    family = preset.get('format', 'h264')
    if family not in ('h264', 'hevc'):
        return None
    codec = preset.get('vcodec') or ('libx265' if family == 'hevc' else 'libx264')
    if codec in PROFILES and PROFILES[codec][0] != family:
        raise ValueError(f'{codec} does not encode {family.upper()}. Choose a matching encoder.')
    if codec == 'h264_vaapi':
        raise ValueError('VAAPI requires a hardware frame-upload pipeline that this exporter does not provide.')
    return codec


def video_options(codec, preset):
    """Shared production/probe options for the supported delivery encoders."""
    if codec not in PROFILES:
        raise ValueError(f'No delivery encoder profile for {codec}.')
    args = ['-c:v', codec]
    if codec in ('libx264', 'libx265'):
        args += ['-preset', str(preset.get('x264_preset', 'medium'))]
    elif codec.endswith('_nvenc'):
        args += ['-preset', 'p5', '-rc', 'vbr']
    if PROFILES[codec][0] == 'hevc':
        args += ['-tag:v', 'hvc1']
    args += ['-pix_fmt', 'yuv420p']
    bitrate = preset.get('bitrate')
    if codec in ('libx264', 'libx265') and not bitrate:
        args += ['-crf', str(preset.get('crf', 18 if codec == 'libx264' else 22))]
    else:
        bitrate = str(bitrate or preset.get('hw_bitrate') or '10M')
        args += ['-b:v', bitrate, '-maxrate', bitrate, '-bufsize', '4M']
    return args


def _run(command, **options):
    if os.name == 'nt':
        options['creationflags'] = subprocess.CREATE_NO_WINDOW
    return subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, timeout=15, **options)


class EncoderCapabilities:
    def __init__(self, ffmpeg='ffmpeg', ffprobe='ffprobe', *, runner=None, clock=None):
        self.ffmpeg, self.ffprobe = ffmpeg, ffprobe
        self.run, self.clock = runner or _run, clock or time.monotonic
        self.lock = threading.Lock()
        self.catalog_cache = None
        self.results = {}

    def identity(self):
        tools = []
        for name in (self.ffmpeg, self.ffprobe):
            path = shutil.which(name)
            if not path:
                raise ValueError(f'{name} was not found. Repair the Filmocity media tools installation.')
            path = str(Path(path).resolve()); stat = os.stat(path)
            tools.append((path, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns))
        return tuple(tools)

    def _catalog(self, identity):
        if self.catalog_cache and self.catalog_cache[0] == identity and self.clock() - self.catalog_cache[1] < 60:
            return self.catalog_cache[2]
        result = self.run([identity[0][0], '-hide_banner', '-encoders'])
        if result.returncode:
            raise ValueError('FFmpeg could not list its encoders: ' + result.stderr.decode('utf-8', 'replace')[-400:])
        listed = set(re.findall(r'^\s*[VAS][A-Z.]{5}\s+(\S+)\s', result.stdout.decode('utf-8', 'replace'), re.M))
        catalog = {'encoders': [name for name in PROFILES if name in listed],
                   'details': [{'id': name, 'format': family, 'label': label, 'hardware': hardware,
                                'listed': name in listed} for name, (family, label, hardware) in PROFILES.items()],
                   'scope': 'Listed in this FFmpeg build; export preflight checks the selected encoder.'}
        self.catalog_cache = identity, self.clock(), catalog
        return catalog

    def catalog(self):
        with self.lock:
            return copy.deepcopy(self._catalog(self.identity()))

    def check(self, codec, preset=None, *, refresh=False):
        preset = preset or {}
        if codec not in PROFILES:
            return {'ok': False, 'encoder': codec, 'message': f'{codec} has no supported H.264/HEVC delivery profile.'}
        # Bound lock waiting as well as child processes. Concurrent UI requests
        # share completed probes; they cannot start an unbounded number of GPUs.
        if not self.lock.acquire(timeout=50):
            return {'ok': False, 'encoder': codec, 'message': 'Another encoder check is still running. Try Check again.'}
        try:
            identity = self.identity()
            options = video_options(codec, preset)
            key = (identity, tuple(options))
            cached = self.results.get(key)
            if not refresh and cached and self.clock() - cached[0] < (300 if cached[1]['ok'] else 15):
                return copy.deepcopy(cached[1])
            catalog = self._catalog(identity)
            if codec not in catalog['encoders']:
                result = {'ok': False, 'encoder': codec, 'message': f'{codec} is not included in this FFmpeg build.'}
            else:
                result = self._probe(identity, codec, options)
            if self.identity() != identity:
                raise ValueError('Media tools changed during the encoder check. Run Check again.')
            result.update(checked_at=time.time(), tool_paths=[x[0] for x in identity],
                          scope='320×180 SDR video encode, container metadata and software decode; no audio, 4K/HDR or speed guarantee.')
            if len(self.results) >= 32: self.results.clear()
            self.results[key] = self.clock(), copy.deepcopy(result)
            return result
        except (OSError, ValueError, subprocess.SubprocessError) as error:
            return {'ok': False, 'encoder': codec, 'message': f'Encoder check failed: {error}'}
        finally:
            self.lock.release()

    def _probe(self, identity, codec, options):
        from delivery_color import plan as delivery_plan, inspect as inspect_delivery, inspect_headers
        policy = delivery_plan({'format': PROFILES[codec][0]})
        ffmpeg, ffprobe = (x[0] for x in identity)
        with tempfile.TemporaryDirectory(prefix="Filmocity encoder é's ") as folder:
            output = str(Path(folder) / 'probe.mp4')
            command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-nostdin', '-y',
                       '-f', 'lavfi', '-i', 'color=c=0x30a050:s=320x180:r=30,format=rgb24', '-frames:v', '4',
                       '-vf', ','.join(policy['filters']), '-an', *options, *policy['encoder_options'],
                       '-r', '30', '-g', '60', '-movflags', '+faststart', output]
            encoded = self.run(command)
            if encoded.returncode:
                detail = encoded.stderr.decode('utf-8', 'replace')[-600:].strip()
                return {'ok': False, 'encoder': codec, 'message': f'{PROFILES[codec][1]} could not encode on this device. {detail}'}
            probe = self.run([ffprobe, '-v', 'error', '-select_streams', 'v:0', '-count_frames',
                              '-show_entries', 'stream=codec_name,width,height,nb_read_frames,color_range,color_space,color_transfer,color_primaries', '-of', 'json', output])
            if probe.returncode: raise ValueError('Encoded test file could not be inspected.')
            streams = json.loads(probe.stdout).get('streams', [])
            if len(streams) != 1 or streams[0].get('codec_name') != PROFILES[codec][0] or (
                    streams[0].get('width'), streams[0].get('height'), str(streams[0].get('nb_read_frames'))) != (320, 180, '4'):
                raise ValueError('Encoded test file has unexpected codec, dimensions or frame count.')
            color = inspect_delivery(streams[0], {'format': PROFILES[codec][0]})
            if color['status'] != 'matched':
                raise ValueError('Encoded test file did not retain Rec.709 limited-range color tags: ' + json.dumps(color['mismatches']))
            headers = self.run([ffmpeg, '-hide_banner', '-v', 'info', '-nostdin', '-i', output,
                                '-map', '0:v:0', '-c:v', 'copy', '-bsf:v', 'trace_headers', '-f', 'null', '-'])
            if headers.returncode: raise ValueError('Encoded video headers could not be inspected.')
            header_color = inspect_headers(headers.stderr.decode('utf-8', 'replace'))
            if header_color['status'] != 'matched':
                raise ValueError('Encoded video headers did not retain Rec.709 limited-range color tags: ' + json.dumps(header_color['mismatches']))
            decoded = self.run([ffmpeg, '-v', 'error', '-nostdin', '-i', output, '-map', '0:v:0',
                                '-frames:v', '4', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
            if decoded.returncode or len(decoded.stdout) != 320 * 180 * 3 * 4:
                raise ValueError('Encoded test frames could not be decoded completely.')
            # The solid swatch permits codec rounding but rejects black/corrupt output.
            for offset in (0, 320 * 180 * 3, 3 * 320 * 180 * 3):
                if max(abs(a-b) for a, b in zip(decoded.stdout[offset:offset+3], (48, 160, 80))) > 12:
                    raise ValueError('Encoded test pixels differ from the source swatch.')
            return {'ok': True, 'encoder': codec, 'message': f'{PROFILES[codec][1]} passed the device encode check.',
                    'delivery_color': color,
                    'elementary_delivery_color': header_color,
                    'sample_sha256': hashlib.sha256(Path(output).read_bytes()).hexdigest()}


CAPABILITIES = EncoderCapabilities()


def inspect_export(project, sequence_id, preset, *, capabilities=None):
    from preflight import inspect_resources
    from export_storage import output_extension
    report = inspect_resources(project, sequence_id, preset)
    capabilities = capabilities or CAPABILITIES
    try:
        output_extension(preset)
        codec = selected_encoder(preset)
        # Existing internal/lossless renderer clients retain their established
        # presets. This runtime gate covers the public H.264/HEVC delivery profiles.
        if report['ok'] and codec and codec not in ('ffv1', 'mpeg4'):
            result = capabilities.check(codec, preset)
            report['encoder_check'] = result
            if not result['ok']: raise ValueError(result['message'])
    except ValueError as error:
        report['issues'].append({'severity': 'error', 'code': 'encoder_unavailable',
                                 'sequence': sequence_id, 'message': str(error)})
        report['errors'] += 1; report['ok'] = False
    return report
