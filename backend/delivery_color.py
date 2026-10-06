"""Explicit SDR RGB-to-YCbCr delivery boundary, separate from input/display policy.

The encoded RGB compositor is interpreted as Rec.709 here. This does not turn
unmanaged inputs into calibrated color or provide linear/high-bit-depth mixing.
Legacy, still/palette and lossless intermediate paths remain separately scoped.
"""
from render_color import ColorPipeline
import re


def plan(preset=None):
    preset = preset or {}
    mode = ColorPipeline.from_preset(preset).mode
    fmt = preset.get('format', 'h264')
    result = {'mode': 'not_applicable', 'filters': [], 'encoder_options': [], 'expected': {}}
    if fmt in ('audio', 'gif', 'png_sequence') or preset.get('vcodec') == 'ffv1':
        return result
    if mode == 'legacy':
        return dict(result, mode='legacy_unmanaged', description='Legacy color keeps the older conversion. Output color tags and display agreement are unqualified.')
    if preset.get('vcodec') is not None and not isinstance(preset['vcodec'], str):
        raise ValueError('Video codec must be a text identifier.')
    pixel_format = 'yuv422p10le' if fmt == 'prores' or preset.get('vcodec') == 'prores_ks' else 'yuv420p'
    codec = preset.get('vcodec') or ('libx265' if fmt == 'hevc' else 'libx264' if fmt == 'h264' else '')
    # Some encoders retain the container tags but omit elementary-stream VUI.
    # Stamp headers only after the corresponding real matrix/range conversion.
    bitstream = 'h264_metadata' if (codec in ('libx264', 'libopenh264') or codec.startswith('h264_')) else 'hevc_metadata' if (codec == 'libx265' or codec.startswith('hevc_')) else None
    if fmt in ('prores', 'webm', 'av1'): bitstream = None
    headers = ['-bsf:v', bitstream + '=video_full_range_flag=0:colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1'] if bitstream else []
    return {
        'mode': 'rec709_limited', 'pixel_format': pixel_format,
        'working_assumption': 'encoded Rec.709 RGB; input/display qualification remains separate',
        'description': 'Video delivery: SDR Rec.709, limited range. RGB is converted using the Rec.709 matrix and tagged to match. Source interpretation and display calibration still need verification.',
        'filters': ['format=rgb24',
                    'scale=in_range=full:out_range=limited:out_color_matrix=bt709:flags=accurate_rnd+full_chroma_int',
                    'format=' + pixel_format,
                    'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709'],
        'encoder_options': ['-color_range', 'tv', '-color_primaries', 'bt709', '-color_trc', 'bt709', '-colorspace', 'bt709'] + headers,
        'expected': {'color_range': 'tv', 'color_primaries': 'bt709', 'color_transfer': 'bt709', 'color_space': 'bt709'},
    }


def apply(graph, preset=None):
    policy = plan(preset)
    if not policy['filters']: return graph, []
    # The graph has one terminal video output; range/review/resize precede this.
    graph = graph.replace('[vout]', '[vdeliveryrgb]')
    return graph + ';\n[vdeliveryrgb]' + ','.join(policy['filters']) + '[vout]', policy['encoder_options']


def inspect(stream, preset=None):
    """Report observed versus required tags. Matching tags do not prove pixels."""
    policy = plan(preset)
    expected = policy['expected']
    observed = {key: stream.get(key) for key in expected}
    mismatches = {key: {'expected': value, 'observed': observed[key]}
                  for key, value in expected.items() if observed[key] != value}
    return {'policy': policy, 'observed': observed, 'status': ('mismatch' if mismatches else 'matched') if expected else policy['mode'], 'mismatches': mismatches}


def inspect_headers(trace):
    """Read FFmpeg trace_headers values for every H.264/HEVC SPS in a sample.

    Some decoder builds omit VUI fields from ffprobe's elementary-stream report.
    The bitstream syntax trace checks the actual encoded headers independently.
    Missing, repeated-inconsistent or unrecognized output fails qualification.
    """
    expected = {'vui_parameters_present_flag': 1, 'video_signal_type_present_flag': 1,
                'colour_description_present_flag': 1, 'video_full_range_flag': 0,
                'colour_primaries': 1, 'transfer_characteristics': 1, 'matrix_coefficients': 1}
    observed = {key: [] for key in expected}
    for key, value in re.findall(r'\[trace_headers[^\]]*\]\s+\d+\s+(\w+)\s+[01]+\s*=\s*(\d+)\s*$', trace, re.M):
        if key in observed: observed[key].append(int(value))
    count = len(observed['vui_parameters_present_flag'])
    mismatches = {key: values for key, values in observed.items()
                  if not count or len(values) != count or any(value != expected[key] for value in values)}
    return {'status': 'mismatch' if mismatches else 'matched', 'method': 'FFmpeg trace_headers',
            'expected': expected, 'observed': observed, 'mismatches': mismatches}
