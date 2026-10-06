"""Exact boundary conversions for legacy seconds-based project interchange.

This does not migrate the timeline storage model. Common decimal NTSC aliases
represent their broadcast rational rates; unsupported XML rates are rejected.
"""
from fractions import Fraction
import math
import re

NTSC = tuple(Fraction(n * 1000, 1001) for n in (24, 30, 48, 60, 120))


def frame_rate(value):
    if isinstance(value, bool): raise ValueError('Invalid frame rate')
    try: result = Fraction(str(value))
    except (ValueError, ZeroDivisionError, TypeError, OverflowError) as error: raise ValueError('Invalid frame rate') from error
    if not 0 < result <= 1000: raise ValueError('Frame rate must be between 0 and 1000')
    for standard in NTSC:
        if abs(result - standard) < Fraction(1, 2000): return standard
    return result


def interpretation_factor(media):
    """Logical source seconds per native second, using canonical frame rates.

    A present authoritative frame_rate must be valid; malformed metadata never
    silently falls back to rounded fps. None means interpretation is disabled.
    """
    interpreted = media.get('interpret_fps')
    if interpreted is None: return 1.0
    native = media.get('frame_rate')
    if native is None: native = media.get('fps')
    return float(frame_rate(native) / frame_rate(interpreted))


def xml_rate(value):
    value = frame_rate(value)
    if value.denominator == 1: return value.numerator, False
    if value in NTSC: return value.numerator // 1000, True
    raise ValueError('This frame rate cannot be represented by FCP7 XML')


def read_xml_rate(element, default=30):
    if element is None: return frame_rate(default)
    try: timebase = Fraction(element.findtext('timebase') or str(default))
    except (ValueError, ZeroDivisionError) as error: raise ValueError('Invalid XML timebase') from error
    ntsc = (element.findtext('ntsc') or 'FALSE').upper()
    if timebase.denominator != 1 or not 0 < timebase <= 1000 or ntsc not in ('TRUE','FALSE'): raise ValueError('Invalid XML frame rate')
    return timebase * Fraction(1000,1001) if ntsc == 'TRUE' else timebase


def to_frames(seconds, fps):
    value = Fraction(str(seconds)) * frame_rate(fps)
    # Nearest frame, halves away from zero. Round-trip frame positions exactly.
    sign = -1 if value < 0 else 1; value = abs(value)
    return sign * ((value.numerator * 2 + value.denominator) // (2 * value.denominator))


def from_frames(frames, fps):
    return float(Fraction(frames) / frame_rate(fps))


def frame_range(start, end, fps):
    """Quantize an exclusive In/Out interval to picture frames."""
    if any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in (start, end)):
        raise ValueError('Set finite export In and Out points')
    if start < 0 or end <= start: raise ValueError('Export Out must be after In')
    first, last = to_frames(start, fps), to_frames(end, fps)
    if last <= first: raise ValueError('Export In/Out must contain at least one frame')
    return first, last


def milliseconds(seconds):
    try: value = float(seconds)
    except (ValueError, TypeError, OverflowError) as error: raise ValueError('Invalid subtitle time') from error
    if not math.isfinite(value) or value < 0: raise ValueError('Subtitle time must be finite and nonnegative')
    return to_frames(seconds, 1000)


def nominal_rate(fps):
    return max(1, math.floor(frame_rate(fps) + Fraction(1, 2)))


def timecode_mode(fps, mode='ndf'):
    if mode not in ('ndf', 'df'): raise ValueError('Choose NDF or DF timecode')
    if mode == 'df' and frame_rate(fps) not in (Fraction(30000, 1001), Fraction(60000, 1001)):
        raise ValueError('Drop-frame timecode requires 29.97 or 59.94 fps')
    return mode


def display_frame(seconds, fps):
    """Frame containing this time; tolerate floating point error at boundaries.

    This differs from to_frames, which rounds edit/interchange boundaries to the
    nearest frame. Neither function changes the project's stored seconds.
    """
    try: value = float(seconds) * float(frame_rate(fps))
    except (ValueError, TypeError, OverflowError) as error: raise ValueError('Invalid time') from error
    if not math.isfinite(value) or value < 0 or value > 2**53 - 1: raise ValueError('Time is outside the supported range')
    return math.floor(value + min(1e-4, max(1e-7, abs(value) * 2**-50)))


def format_frames(frames, fps, mode='ndf'):
    """Zero-origin timecode. DF skips labels, never picture frames. No 24h wrap."""
    if isinstance(frames, bool) or not isinstance(frames, int) or not 0 <= frames <= 2**53 - 1:
        raise ValueError('Frame count must be a nonnegative safe integer')
    nominal = nominal_rate(fps)
    if timecode_mode(fps, mode) == 'df':
        drop = nominal // 15
        per_minute = nominal * 60 - drop
        per_ten = nominal * 600 - drop * 9
        tens, remainder = divmod(frames, per_ten)
        frames += drop * 9 * tens + drop * max(0, (remainder - drop) // per_minute)
    if frames > 2**53 - 1: raise ValueError('Time is outside the supported range')
    seconds, frame = divmod(frames, nominal)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}{';' if mode == 'df' else ':'}{frame:02d}"


def format_timecode(seconds, fps, mode='ndf'):
    return format_frames(display_frame(seconds, fps), fps, mode)


def parse_timecode(value, fps):
    """Strict HH:MM:SS:FF (NDF), HH:MM:SS;FF (DF), or decimal seconds.

    The separator selects the notation, independent of a sequence's display
    preference. Signed decimal seconds are allowed for navigation offsets.
    """
    text = str(value).strip()
    rate = frame_rate(fps)
    if re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', text):
        seconds = float(text)
        if not math.isfinite(seconds) or abs(seconds * float(rate)) > 2**53 - 1: raise ValueError('Time is outside the supported range')
        return seconds
    match = re.fullmatch(r'([0-9]{2,}):([0-9]{2}):([0-9]{2})([:;])([0-9]{2,3})', text)
    if not match: raise ValueError('Enter HH:MM:SS:FF (NDF), HH:MM:SS;FF (DF), or decimal seconds')
    hours, minutes, seconds, separator, frame = match.groups()
    hours, minutes, seconds, frame = map(int, (hours, minutes, seconds, frame))
    nominal = nominal_rate(rate)
    if minutes >= 60 or seconds >= 60 or frame >= nominal: raise ValueError('Timecode contains an out-of-range field')
    total_minutes = hours * 60 + minutes
    count = (total_minutes * 60 + seconds) * nominal + frame
    if separator == ';':
        timecode_mode(rate, 'df')
        drop = nominal // 15
        if minutes % 10 and seconds == 0 and frame < drop: raise ValueError('That frame label is skipped in drop-frame timecode')
        count -= drop * (total_minutes - total_minutes // 10)
    if count > 2**53 - 1: raise ValueError('Time is outside the supported range')
    return from_frames(count, rate)
