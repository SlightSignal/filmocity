"""Explicit tagged BT.2020 input conversion into the existing SDR compositor.

This is not full display/ICC management or a linear-light working pipeline.
Unknown source tags cannot establish HDR or justify guessing a PQ transform.
"""
import math

HDR_TRANSFERS = ('smpte2084', 'arib-std-b67')
SDR_TRANSFERS = ('bt709', 'bt2020-10', 'bt2020-12', 'smpte170m', 'gamma22', 'gamma28', 'iec61966-2-1')


def tag(value): return value if isinstance(value, str) and value not in ('', 'unknown', 'unspecified', 'reserved') else None


def dynamic_range(media):
    transfer = tag(media.get('color_transfer'))
    if transfer in HDR_TRANSFERS: return 'pq' if transfer == 'smpte2084' else 'hlg'
    if transfer in SDR_TRANSFERS: return 'sdr'
    return 'unknown'


def peak(value):
    if isinstance(value, bool): return None
    try:
        value = float(value)
        return value if math.isfinite(value) and 100 <= value <= 10000 else None
    except (TypeError, ValueError, OverflowError): return None


def plan(media):
    kind = dynamic_range(media)
    primaries, matrix, transfer, levels = [tag(media.get(key)) for key in ('color_primaries', 'color_space', 'color_transfer', 'color_range')]
    wide = primaries == 'bt2020'
    result = {'dynamic_range': kind, 'wide_gamut': wide, 'conversion': 'none', 'filters': [], 'required_filters': [], 'warnings': []}
    chosen = media.get('input_transform')
    if chosen not in (None, '', 'none'):
        if chosen not in ('slog3', 'vlog', 'clog3', 'logc3'): raise ValueError('Unknown camera-log input LUT. Choose a supported transform or clear it.')
        result['conversion'] = 'camera_log_lut'
        result['warnings'].append('The selected camera-log LUT overrides tagged HDR/gamut conversion. Camera-specific LUT accuracy and live/rendered agreement require footage qualification.')
        return result
    # An old hdr flag can be stale: known SDR transfer takes precedence. If the
    # tags are missing, that old flag instead demands a new source inspection.
    if kind == 'unknown' and (wide or media.get('hdr')):
        raise ValueError('Source transfer is unknown. Relink/reinspect this HDR or wide-gamut source before rendering; PQ is not assumed.')
    if kind not in ('pq', 'hlg') and not wide: return result
    if primaries != 'bt2020' or matrix != 'bt2020nc' or levels not in ('tv', 'pc'):
        raise ValueError('Tagged HDR/wide-gamut conversion requires BT.2020 primaries, BT.2020 nonconstant matrix and a known limited/full range. Reinspect or supply correctly tagged media.')
    if kind == 'sdr' and transfer not in ('bt709', 'bt2020-10', 'bt2020-12'):
        raise ValueError('This BT.2020 SDR transfer is not yet supported. Use a qualified Rec.709 conversion.')
    prefix = f'zscale=pin=bt2020:min=bt2020nc:tin={transfer}:rin={levels}'
    result['required_filters'] = ['zscale']
    if kind == 'sdr':
        result.update(conversion='bt2020_sdr_to_bt709', filters=[prefix + ':p=bt709:t=bt709:m=gbr:r=full', 'format=gbrp16le'])
        result['warnings'].append('BT.2020 SDR is converted to Rec.709 without HDR tone mapping. Out-of-gamut colors can clip; inspect a rendered preview.')
        return result
    if 'hdr_peak_nits' in media:
        selected = peak(media['hdr_peak_nits'])
        if selected is None: raise ValueError('HDR source peak must be a finite number from 100 to 10000 nits.')
        origin = 'override'
    else:
        selected, origin = next(((peak(media.get(key)), name) for key, name in (
            ('hdr_max_cll', 'max_cll'), ('hdr_mastering_peak_nits', 'mastering_display')) if peak(media.get(key)) is not None), (1000.0, 'assumed'))
    result.update(conversion='hdr_to_bt709', peak_nits=selected, peak_origin=origin, reference_nits=100, display_transfer='bt1886',
        filters=[prefix + ':t=linear:npl=100', 'format=gbrpf32le', 'zscale=p=bt709',
                 f'tonemap=hable:desat=0:peak={selected/100:.12g}', 'zscale=t=bt709:m=gbr:r=full', 'format=gbrp16le'])
    if kind == 'hlg': result['hlg_reference_peak_nits'] = 1000
    result['required_filters'].append('tonemap')
    result['warnings'].append(f'{kind.upper()} converts to SDR Rec.709 with Hable, a 100-nit linear reference and {selected:g}-nit peak ({origin}). Live HDR display is not qualified; inspect a rendered preview.')
    return result


def filters(media, available):
    policy = plan(media)
    missing = [name for name in policy['required_filters'] if not available(name)]
    if missing: raise ValueError('Source color conversion needs FFmpeg filters: ' + ', '.join(missing) + '. No brightness-curve substitute is applied.')
    return policy['filters']
