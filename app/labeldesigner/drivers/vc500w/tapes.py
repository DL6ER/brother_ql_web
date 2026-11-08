"""The tapes of the VC-500W. Per tape: its width in inches as the cassette
reports it (width_inches_media). Labels are as wide as the tape, the printer
gets them BLEED dots wider, their edges repeated beyond those of the tape."""

from ..labels import FormFactor, Label

from .protocol import DPI

MODEL = 'VC-500W'
TAPES = {9: 0.367, 12: 0.489, 19: 0.734, 25: 0.978, 50: 1.927}
BLEED = 20


def _dots(inches: float) -> int:
    return round(inches * DPI)


LABELS = tuple(
    Label(f'vc{width}', (width, 0), FormFactor.ENDLESS, (_dots(tape) + BLEED, 0), (_dots(tape), 0), 0,
          models=(MODEL,))
    for width, tape in TAPES.items())

# Continuous ZINK tape
NAMES = {f'vc{width}': f'{width} mm full color tape (VC-500W)' for width in TAPES}
