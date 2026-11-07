"""A label as the raw RGB image the VC-500W prints."""

from PIL import Image, ImageEnhance

from ...label import LabelOrientation
from . import protocol
from .tapes import LABELS

# ZINK paper prints colors duller than a screen shows them. Of 1, 1.5 and 2
# times the saturation, 2 printed red best, darker colors gain the most.
SATURATION = 2.0

# The printer places an image this many dots off the middle of the tape,
# toward its first pixel, and its cuts take off this many millimeters of
# both ends (docs/vc500w.md)
OFFSET = 3
CUT_MM = 0.8


def _repeat_rows(img: Image.Image, before: int, after: int) -> Image.Image:
    """img with its first row repeated before times above it and its last
    row after times below it."""
    out = Image.new(img.mode, (img.width, img.height + before + after))
    if before:
        out.paste(img.crop((0, 0, img.width, 1)).resize((img.width, before), Image.Resampling.NEAREST), (0, 0))
    if after:
        last = img.crop((0, img.height - 1, img.width, img.height))
        out.paste(last.resize((img.width, after), Image.Resampling.NEAREST), (0, before + img.height))
    out.paste(img, (0, before))
    return out


def label_image(label, label_size: str, mode: str) -> Image.Image:
    """A label as the image the VC-500W prints: each row is a line along the
    tape at the lines per inch of the mode, the first row comes out first,
    so standard labels come out top first, rotated ones with the end of
    their text, as on QL printers. The label is as wide as the tape, its
    edges are repeated beyond those of the tape and beyond both cuts, so a
    color at an edge reaches it. Colors are saturated for the paper."""
    img = label.generate().convert('RGB')
    if label.label_orientation == LabelOrientation.STANDARD:
        img = img.rotate(-90, expand=True)
    # Now along the tape: its right end comes out first, its top becomes
    # the first pixels of the rows
    tape = next(tape for tape in LABELS if tape.identifier == label_size)
    lpi = protocol.MODES[mode][2]
    img = img.resize((round(img.width * lpi / protocol.DPI), img.height), Image.Resampling.LANCZOS)
    extra = max(tape.dots_total[0] - img.height, 0)
    top = min(extra // 2 + OFFSET, extra)
    img = _repeat_rows(img, top, extra - top).transpose(Image.Transpose.TRANSPOSE)
    ends = round(CUT_MM / 25.4 * lpi)
    img = _repeat_rows(img, ends, ends).transpose(Image.Transpose.TRANSPOSE)
    img = ImageEnhance.Color(img).enhance(SATURATION)
    return img.transpose(Image.Transpose.ROTATE_90)
