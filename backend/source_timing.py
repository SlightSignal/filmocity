"""Source-clock windows and sample placement for the 48 kHz render mix.

FFmpeg receives copyts/start_at_zero, so all streams in each input use the
container's common zero origin. Never independently subtract stream STARTPTS.
"""
from fractions import Fraction

SAMPLE_RATE = 48000


def sample_index(seconds):
    value = Fraction(str(seconds)) * SAMPLE_RATE
    sign = -1 if value < 0 else 1
    value = abs(value)
    return sign * ((2 * value.numerator + value.denominator) // (2 * value.denominator))


def audio_window(index, start, end, *, origin=0):
    """Materialize missing source audio as silence before trimming a window.

    Resample first so the clip boundary is on the output mix's sample grid.
    Initial padding honors first_pts even for a wholly silent early window;
    end padding handles windows after audio EOF. Internal timestamp correction
    uses a 1 ms threshold to avoid chasing coarse mux timestamp rounding. No
    soft time stretching is requested. This is not clock-drift calibration.
    """
    origin_sample = sample_index(origin)
    first, last = max(0, sample_index(start)-origin_sample), max(0, sample_index(end)-origin_sample)
    if last <= first:
        raise ValueError('Audio source window must contain at least one sample')
    # Input seeking with copyts/start_at_zero keeps the common source clock.
    # Shift that common clock explicitly before resampling; never STARTPTS.
    shift = f'asetpts=PTS-({origin_sample}/{SAMPLE_RATE})/TB,' if origin_sample else ''
    return [f'[{index}:a]{shift}aresample={SAMPLE_RATE}:async=1:first_pts=0:min_hard_comp=0.001',
            f'apad=whole_len={last}', f'atrim=start_sample={first}:end_sample={last}',
            'asetpts=N/SR/TB']


def audio_duration(duration):
    count = max(1, sample_index(duration))
    return [f'apad=whole_len={count}', f'atrim=end_sample={count}', 'asetpts=N/SR/TB']


def audio_placement(start):
    return f'adelay={max(0, sample_index(start))}S:all=1'


def video_origin(start):
    return ['settb=AVTB', f'setpts=PTS-({start:.12f})/TB']
