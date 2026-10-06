"""Small lossless sources with independent picture and audio clocks."""
import array
import json
from pathlib import Path
import subprocess
import sys
import wave


def run(args):
    result = subprocess.run(args, capture_output=True, timeout=45)
    if result.returncode:
        raise AssertionError(result.stderr.decode(errors='replace')[-5000:])
    return result.stdout


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def make_source(root, *, audio_offset=0, video_offset=0, origin=0, sample_rate=48000, duration=1, audio_codec="pcm_s16le", audio_gap=0):
    root = Path(root); root.mkdir(parents=True, exist_ok=True)
    wave_path = root / 'pulses.wav'
    # Two 50 ms plateaus; positions are independent sample counts, not values
    # obtained from the renderer or its generated graph.
    samples = array.array('h', [0] * sample_rate)
    for start in (sample_rate // 10, sample_rate * 6 // 10):
        samples[start:start + sample_rate // 20] = array.array('h', [12000] * (sample_rate // 20))
    if sys.byteorder != 'little': samples.byteswap()
    with wave.open(str(wave_path), 'wb') as stream:
        stream.setparams((1, 2, sample_rate, 0, 'NONE', '')); stream.writeframes(samples.tobytes())
    source = root / ('source.nut' if audio_gap else 'source.mov')
    command = ['ffmpeg', '-v', 'error', '-y', '-itsoffset', str(video_offset), '-f', 'lavfi', '-i',
               f"color=black:s=64x48:r=20:d={duration},drawbox=c=white:t=fill:enable='eq(n,2)+eq(n,12)'",
               '-itsoffset', str(audio_offset), '-i', str(wave_path), '-map', '0:v:0', '-map', '1:a:0',
               *(['-af', rf'asetpts=PTS+gte(T\,0.5)*{audio_gap}/TB'] if audio_gap else []),
               '-fps_mode:v', 'passthrough', '-c:v', 'png', '-threads', '1', '-c:a', audio_codec,
               '-output_ts_offset', str(origin), str(source)]
    run(command)
    return source, probe(source), command


def pcm(path):
    data = array.array('f', run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:a:0', '-af', 'pan=mono|c0=c0', '-ar', '48000', '-f', 'f32le', '-']))
    if sys.byteorder != 'little': data.byteswap()
    return data


def runs(samples, threshold=.1):
    found = []; begin = None
    for index, value in enumerate(samples):
        hot = abs(value) > threshold
        if hot and begin is None: begin = index
        elif not hot and begin is not None: found.append((begin, index)); begin = None
    if begin is not None: found.append((begin, len(samples)))
    return found


def pictures(path):
    data = run(['ffmpeg', '-v', 'error', '-i', str(path), '-map', '0:v:0', '-fps_mode', 'passthrough', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-'])
    size = 64 * 48 * 3
    assert len(data) % size == 0
    return [sum(data[i:i+size]) / size for i in range(0, len(data), size)]
