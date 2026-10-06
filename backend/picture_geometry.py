"""Measured picture geometry for square-pixel sequence and Source-frame output.

Media width/height already include quarter-turn rotation. Pixel aspect is from
its unrotated stream, so that scale follows the displayed height after a turn.
"""
import math
import re
from fractions import Fraction

def dimension(value, label):
    if type(value) is not int or not 1 <= value <= 16384:
        raise ValueError(label+' must be an integer from 1 to 16384')
    return value


def display_size(media, warnings):
    width = dimension(media.get('width'), 'Source width')
    height = dimension(media.get('height'), 'Source height')
    # Measured dimensions are already rotation-aware. SAR still refers to the
    # decoder's unrotated pixels; a quarter turn moves its scale to height.
    rotation = media.get('rotation', 0)
    if type(rotation) not in (int, float) or not math.isfinite(rotation) or rotation % 90:
        raise ValueError('Match source format needs measured quarter-turn rotation metadata')
    value = media.get('sample_aspect_ratio')
    if value in (None, '', 'N/A', 'unknown'):
        ratio = Fraction(1)
        warnings.append('The source pixel aspect ratio is unavailable; sequence dimensions assume square pixels. Inspect the original before judging display-format fidelity.')
    else:
        if not isinstance(value, str) or len(value) > 64 or not re.fullmatch(r'[0-9]+[:/][0-9]+', value):
            raise ValueError('Source pixel aspect ratio is invalid; inspect and Relink it first')
        try: ratio = Fraction(value.replace(':', '/'))
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError('Source pixel aspect ratio is invalid; inspect and Relink it first') from error
        if ratio <= 0: raise ValueError('Source pixel aspect ratio must be positive')
    if ratio != 1:
        field = 'height' if rotation % 180 else 'width'
        exact = (height if field == 'height' else width) * ratio
        rounded = max(1, (2*exact.numerator + exact.denominator)//(2*exact.denominator))
        if field == 'height': height = dimension(rounded, 'Display height')
        else: width = dimension(rounded, 'Display width')
        warnings.append('The source pixel aspect ratio is converted to square-pixel sequence dimensions; a fractional dimension is rounded to the nearest pixel.')
    return width, height
