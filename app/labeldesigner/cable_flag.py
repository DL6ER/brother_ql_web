"""Cable flags: a label twice along the tape, with a blank part between the
two that wraps around the cable while both ends stick together back to
back."""

import math

from PIL import Image, ImageDraw

CABLE_FLAG_REFUSED = 'Cable flags need continuous tape'
CABLE_DIAMETER_REFUSED = 'The cable diameter must be a number from 1 to 50 mm'
# A Cat.7 patch cable
DEFAULT_DIAMETER = 6.5
MIN_DIAMETER, MAX_DIAMETER = 1, 50


def cable_diameter(value) -> float:
    """The diameter of the cable in mm, empty for the default. A decimal
    comma is taken as well."""
    text = '' if value is None else str(value).strip()
    if not text:
        return DEFAULT_DIAMETER
    try:
        diameter = float(text.replace(',', '.'))
    except ValueError:
        raise ValueError(CABLE_DIAMETER_REFUSED) from None
    # Also false for nan
    if not MIN_DIAMETER <= diameter <= MAX_DIAMETER:
        raise ValueError(CABLE_DIAMETER_REFUSED)
    return diameter


def wrap_dots(diameter: float, dpi: int) -> int:
    """The length around a cable in dots."""
    return round(math.pi * diameter * dpi / 25.4)


def flag_strip(half: Image.Image, wrap: int, dpi: int, along: bool) -> Image.Image:
    """The flag of a label: the label, the wrap with the marker where the
    cable goes, and the label again. along: the tape runs along x, else
    along y. Folding turns what points along the tape, so the second label
    is upside down when the text runs across the tape."""
    if along:
        return _strip(half, half, wrap, dpi)
    turned = half.transpose(Image.Transpose.ROTATE_90)
    strip = _strip(turned, turned.transpose(Image.Transpose.ROTATE_180), wrap, dpi)
    return strip.transpose(Image.Transpose.ROTATE_270)


def _strip(first: Image.Image, second: Image.Image, wrap: int, dpi: int) -> Image.Image:
    width, height = first.size
    strip = Image.new(first.mode, (2 * width + wrap, height), 'white')
    strip.paste(first, (0, 0))
    strip.paste(second, (width + wrap, 0))
    _marker(ImageDraw.Draw(strip), width, wrap, height, dpi)
    return strip


def _marker(draw: ImageDraw.ImageDraw, left: int, wrap: int, height: int, dpi: int) -> None:
    """Ticks where the wrap begins and ends, triangles at the edges of the
    tape where the cable goes, and a dotted line between them."""
    mm = dpi / 25.4
    mark = min(round(0.15 * height), round(2.5 * mm))
    tick = max(1, round(0.3 * mm))
    line = max(1, round(0.15 * mm))
    gap = round(0.8 * mm)
    right = left + wrap - 1
    for x in (left, right - tick + 1):
        draw.rectangle((x, 0, x + tick - 1, mark - 1), fill='black')
        draw.rectangle((x, height - mark, x + tick - 1, height - 1), fill='black')
    center = left + wrap // 2
    # Within the wrap, also around thin cables on wide tape
    base = min(round(1.6 * mark), wrap - 1) // 2
    draw.polygon(((center - base, 0), (center + base, 0), (center, mark)), fill='black')
    draw.polygon(((center - base, height - 1), (center + base, height - 1), (center, height - 1 - mark)), fill='black')
    x = center - line // 2
    for y in range(mark + gap, height - mark - line, line + gap):
        draw.rectangle((x, y, x + line - 1, y + line - 1), fill='black')
