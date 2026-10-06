"""Validated source ingest options, never a project-supplied FFmpeg command.

Numbered images retain their numeric frame rate and first frame. Legacy image
inputs may also set loop to 0 or 1. No input, output, codec, filter, protocol,
file or global option can be supplied through this field. Return fresh argv
without changing the captured decimal/rational source clock.
"""
from fractions import Fraction
from decimal import Decimal
import re

_RATE = re.compile(r'[0-9]+(?:\.[0-9]+|/[0-9]+)?', re.ASCII)
_INTEGER = re.compile(r'[0-9]+', re.ASCII)
_ALLOWED = frozenset(('-framerate', '-start_number', '-loop'))


def validated_input_options(value=None):
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or len(value) > 6 or len(value) % 2:
        raise ValueError('Source input options must contain supported option/value pairs')
    result = []
    seen = set()
    for key, number in zip(value[::2], value[1::2]):
        if (not isinstance(key, str) or key not in _ALLOWED or key in seen
                or not isinstance(number, str) or not 1 <= len(number) <= 64):
            raise ValueError('Source input options support only unique numeric -framerate, -start_number and -loop pairs')
        if key == '-framerate':
            if not _RATE.fullmatch(number):
                raise ValueError('Source frame rate must be a positive decimal or rational number')
            try:
                rate = Fraction(number)
            except (ValueError, ZeroDivisionError) as error:
                raise ValueError('Source frame rate must be between 0 and 1000') from error
            if not 0 < rate <= 1000:
                raise ValueError('Source frame rate must be between 0 and 1000')
        elif key == '-start_number':
            if not _INTEGER.fullmatch(number) or int(number) > 2147483647:
                raise ValueError('Source start number must be an integer from 0 to 2147483647')
        elif number not in ('0', '1'):
            raise ValueError('Source image loop must be 0 or 1')
        seen.add(key)
        result.extend((key, number))
    return result


def numbered_input_options(fps, start_number=0):
    """Build the same admitted numeric argv before importing numbered images.

    Python's ``g`` format can emit an exponent or round away the captured source
    clock. Use the shortest decimal spelling of the actual numeric rate, expanded
    without an exponent, then apply the shared option/value contract.
    """
    if isinstance(fps, bool) or not isinstance(fps, (int, float)):
        raise ValueError('Numbered source frame rate must be numeric')
    if type(start_number) is not int:
        raise ValueError('Numbered source start number must be an integer')
    rate = format(Decimal(str(fps)), 'f')
    if '.' in rate:
        rate = rate.rstrip('0').rstrip('.')
    return validated_input_options(['-framerate', rate, '-start_number', str(start_number)])
