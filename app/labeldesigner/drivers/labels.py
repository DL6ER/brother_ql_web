"""Label sizes, as each driver describes those of its printers."""

from dataclasses import dataclass
from enum import IntEnum


class FormFactor(IntEnum):
    DIE_CUT = 1
    ENDLESS = 2
    ROUND_DIE_CUT = 3
    # Endless P-touch tape
    PTOUCH_ENDLESS = 4


class Color(IntEnum):
    BLACK_WHITE = 0
    # Black and red on white, e.g. DK-22251
    BLACK_RED_WHITE = 1


@dataclass(frozen=True)
class Label:
    """A label size. tape_size is (width, length) in mm, the length 0 for
    endless tape. dots_total and dots_printable are (across, along) in dots of
    the printer, the length 0 for endless tape. offset_r moves the print this
    many dots off the right edge, so that it is centered, feed_margin is fed
    before and after it. models lists the printers it is made for, empty for
    all printers of its driver."""
    identifier: str
    tape_size: tuple[int, int]
    form_factor: FormFactor
    dots_total: tuple[int, int]
    dots_printable: tuple[int, int]
    offset_r: int
    feed_margin: int = 0
    models: tuple[str, ...] = ()
    color: Color = Color.BLACK_WHITE

    @property
    def name(self) -> str:
        if self.form_factor == FormFactor.DIE_CUT:
            name = '{0}mm x {1}mm die-cut'.format(*self.tape_size)
        elif self.form_factor == FormFactor.ROUND_DIE_CUT:
            name = f'{self.tape_size[0]}mm round die-cut'
        else:
            name = f'{self.tape_size[0]}mm endless'
        return name + (' (black/red/white)' if self.color == Color.BLACK_RED_WHITE else '')
