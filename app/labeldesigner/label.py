from .enums import LabelContent, LabelOrientation, LabelType
from . import icons
from .cable_flag import flag_strip
import os
import uuid
from qrcode import QRCode, constants
from PIL import Image, ImageDraw, ImageFont, ImageChops
import logging
import barcode
from barcode.errors import BarcodeError
from barcode.writer import ImageWriter
import collections
import datetime
import functools
import itertools
import json
import math
import re
import random
import string
import threading
import copy
from typing import Any, Dict, List, Optional, Tuple, Union

logger = logging.getLogger(__name__)

# Constants
WARNING_TEXT_LENGTH = 500
MAX_TEXT_LENGTH = 10_000
MAX_TOTAL_TEXT_LENGTH = 100_000
DEFAULT_RANDOM_LENGTH = 64
MAX_RANDOM_LENGTH = 10_000
MAX_IMAGE_SCALING_FACTOR = 500.0
# Upper limit for the label and every image created on the way, enough for a
# 62 mm wide label of about 2 m length at 600 dpi
MAX_LABEL_PIXELS = 64_000_000
# Barcode values are rendered before the label size is known, a long value
# would create a huge image. 1000 characters are far more than a label holds.
MAX_BARCODE_LENGTH = 1000
# Text filled into the label grows to at most this font size, e.g. a short
# word along endless tape
MAX_FILL_SIZE = 2000
# Text filled into endless tape makes the label at most this many times as
# long as the tape is wide, about a meter on 62 mm tape at any resolution,
# e.g. many lines across the tape
MAX_FILLED_RATIO = 16
# Where an image or code can be next to the text
IMAGE_POSITIONS = ('above', 'below', 'left', 'right')
# The contents of labels with an image, by its colors
IMAGE_CONTENTS = (LabelContent.IMAGE_BW, LabelContent.IMAGE_GRAYSCALE, LabelContent.IMAGE_RED_BLACK, LabelContent.IMAGE_COLORED)
# Where the content is on a label that has room to spare above and below it
VERTICAL_ALIGNMENTS = ('top', 'center', 'bottom')
# The date format of a blank, e.g. %d.%m.%Y. A long one would make a long
# date of every blank.
MAX_DATE_FORMAT_LENGTH = 100
DEFAULT_FONT_SIZE = 12

# Pillow refuses to open images with more than twice this number of pixels,
# so uploaded images above MAX_LABEL_PIXELS are rejected right when decoding
Image.MAX_IMAGE_PIXELS = MAX_LABEL_PIXELS // 2
# Environment variables that were not allowed in a template, logged only once.
# Bounded, the names come from the requests.
_REFUSED_ENV_VARS: set = set()
_MAX_REFUSED_ENV_VARS = 100
# A blank, filled in when the label is printed, as the label designer stores it
BLANK_TEMPLATE = re.compile(r"\{\{blank:([^}]+)\}\}")


class TextTooLong(ValueError):
    """Text longer than a label may have, or a date format that would make
    it so: a request too large."""


def on_white(img: Image.Image) -> Image.Image:
    """img as it is printed: its transparent parts white like the label,
    without transparency."""
    if 'A' not in img.getbands() and 'transparency' not in img.info:
        return img
    rgba = img.convert('RGBA')
    return Image.alpha_composite(Image.new('RGBA', rgba.size, 'white'), rgba).convert('RGB')


def check_image_position(value) -> str | None:
    """The side of the text an image or code is on, None where none is
    given (left out or empty), ValueError for another."""
    value = value or None
    if value is not None and value not in IMAGE_POSITIONS:
        raise ValueError("image_position must be above, below, left or right")
    return value


def check_vertical_align(value) -> str:
    """Where the content is placed between the top and bottom margins: top,
    center or bottom. Left out or empty: in the middle."""
    if value is None or value == '':
        return 'center'
    if value not in VERTICAL_ALIGNMENTS:
        raise ValueError("vertical_align must be top, center or bottom")
    return value


def check_image_size(width: int, height: int) -> None:
    """Refuse to create images that would exhaust the memory."""
    if width * height > MAX_LABEL_PIXELS:
        raise ValueError(f"Label is too large ({int(width)} x {int(height)} pixels)")


def _gtin_check_digit(digits: str) -> str:
    """Check digit of EAN, UPC, GTIN and ISBN-13: the digits weighted 3 and
    1 from the right."""
    total = sum(int(digit) * (3 if i % 2 == 0 else 1) for i, digit in enumerate(reversed(digits)))
    return str(-total % 10)


def _isbn10_check_digit(digits: str) -> str:
    check = sum(weight * int(digit) for weight, digit in enumerate(digits, start=1)) % 11
    return 'X' if check == 10 else str(check)


def _issn_check_digit(digits: str) -> str:
    check = -sum(weight * int(digit) for weight, digit in zip(range(8, 1, -1), digits)) % 11
    return 'X' if check == 10 else str(check)


def _pzn_check_digit(digits: str) -> str:
    # 10 is none, python-barcode refuses such a number
    return str(sum(weight * int(digit) for weight, digit in enumerate(digits, start=2)) % 11)


# The codes with a check digit, by type: their name, the number of digits
# without it, how it is calculated and whether hyphens may separate the
# digits (python-barcode leaves them out). python-barcode calculates the
# check digit itself and ignores what follows the digits it needs, so a wrong
# check digit or a typo at the end made another valid code.
CHECK_DIGIT_CODES = {
    'ean': ('EAN-13', 12, _gtin_check_digit, False),
    'ean13': ('EAN-13', 12, _gtin_check_digit, False),
    'ean13-guard': ('EAN-13', 12, _gtin_check_digit, False),
    'ean8': ('EAN-8', 7, _gtin_check_digit, False),
    'ean8-guard': ('EAN-8', 7, _gtin_check_digit, False),
    'ean14': ('EAN-14', 13, _gtin_check_digit, False),
    'gtin': ('GTIN', 13, _gtin_check_digit, False),
    'jan': ('JAN', 12, _gtin_check_digit, False),
    'upc': ('UPC-A', 11, _gtin_check_digit, False),
    'upca': ('UPC-A', 11, _gtin_check_digit, False),
    'gs1': ('GS1', 12, _gtin_check_digit, True),
    'isbn': ('ISBN-13', 12, _gtin_check_digit, True),
    'isbn13': ('ISBN-13', 12, _gtin_check_digit, True),
    'isbn10': ('ISBN-10', 9, _isbn10_check_digit, True),
    'issn': ('ISSN', 7, _issn_check_digit, True),
    'pzn': ('PZN', 6, _pzn_check_digit, False),
}


def check_check_digit(barcode_type: str, value: str) -> None:
    """ValueError if value is no number of the type: the digits without the
    check digit, which is calculated, or with the right one."""
    if barcode_type.lower() not in CHECK_DIGIT_CODES:
        return
    name, length, check_digit, hyphens = CHECK_DIGIT_CODES[barcode_type.lower()]
    number = value.replace('-', '') if hyphens else value
    digits, given = number[:length], number[length:].upper()
    may_be_x = _may_be_x(check_digit)
    if not (len(digits) == length and _is_digits(digits)
            and (given == '' or len(given) == 1 and (_is_digits(given) or may_be_x and given == 'X'))):
        raise ValueError(_length_problem(name, length, may_be_x))
    expected = check_digit(digits)
    if given and given != expected and expected != '10':
        raise ValueError(_check_digit_problem(name, expected))


def check_digit_problems() -> list[str]:
    """Every message check_check_digit can refuse a value with."""
    problems = []
    for name, length, check_digit, _ in CHECK_DIGIT_CODES.values():
        may_be_x = _may_be_x(check_digit)
        problems.append(_length_problem(name, length, may_be_x))
        problems += [_check_digit_problem(name, digit) for digit in '0123456789X' if digit != 'X' or may_be_x]
    return list(dict.fromkeys(problems))


def _may_be_x(check_digit) -> bool:
    return check_digit in (_isbn10_check_digit, _issn_check_digit)


def _length_problem(name: str, length: int, may_be_x: bool) -> str:
    return f"{name} takes {length} digits, or {length + 1} with the check digit{', which may be an X' if may_be_x else ''}"


def _check_digit_problem(name: str, expected: str) -> str:
    # Without the value, which the designer shows next to it anyway
    return f"The check digit of {name} must be {expected}"


def _is_digits(text: str) -> bool:
    return text.isascii() and text.isdigit()


# The codes of {{datetime:<format>}} the label designer explains. Their
# examples come from the strftime() that fills in the labels, for a day that
# tells day and month apart.
DATETIME_CODES = (
    ('%d', 'Day of the month'),
    ('%-d', 'Day of the month, no leading zero'),
    ('%a', 'Weekday, short'),
    ('%A', 'Weekday'),
    ('%u', 'Weekday as a number, Monday is 1'),
    ('%j', 'Day of the year'),
    ('%m', 'Month'),
    ('%-m', 'Month, no leading zero'),
    ('%b', 'Month name, short'),
    ('%B', 'Month name'),
    ('%y', 'Year, two digits'),
    ('%Y', 'Year'),
    ('%V', 'Calendar week, starting on Monday'),
    ('%H', 'Hour, 24-hour clock'),
    ('%I', 'Hour, 12-hour clock'),
    ('%p', 'AM or PM'),
    ('%M', 'Minute'),
    ('%S', 'Second'),
    ('%x', 'Date'),
    ('%X', 'Time'),
    ('%%', 'A % sign'),
)
DATETIME_EXAMPLE = datetime.datetime(2026, 3, 7, 14, 5, 9)


def datetime_codes() -> dict:
    """The codes with what they stand for and their example, and the time of
    the examples."""
    when = DATETIME_EXAMPLE
    return {'example': f'{when:%A}, {when.day} {when:%B %Y, %H:%M:%S}',
            'codes': [[code, meaning, when.strftime(code)] for code, meaning in DATETIME_CODES]}


def _iso_date(value: str) -> datetime.date | None:
    """value as a date if it is one like 2026-11-03."""
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value, re.ASCII):
        try:
            return datetime.date.fromisoformat(value)
        except ValueError:
            pass
    return None


# Colors of text, codes and borders: black and red for QL printers, any
# color as #rrggbb for the VC-500W
COLORS = {'black': (0, 0, 0), 'red': (255, 0, 0)}


def parse_color(value) -> Tuple[int, int, int]:
    if isinstance(value, str) and value in COLORS:
        return COLORS[value]
    if isinstance(value, str) and re.fullmatch(r'#[0-9a-fA-F]{6}', value):
        return tuple(int(value[i:i + 2], 16) for i in (1, 3, 5))
    raise ValueError(f'Unknown color: {value}')


class SimpleLabel:
    """
    Represents a label with text, image, barcode, and QR code support.
    Handles rendering, template processing, and layout.
    """
    QR_CORRECTION_MAPPING = {
        'M': constants.ERROR_CORRECT_M,
        'L': constants.ERROR_CORRECT_L,
        'H': constants.ERROR_CORRECT_H,
        'Q': constants.ERROR_CORRECT_Q
    }

    def __init__(
        self,
        width: int = 0,
        height: int = 0,
        label_content: LabelContent = LabelContent.TEXT_ONLY,
        label_orientation: LabelOrientation = LabelOrientation.STANDARD,
        label_type: LabelType = LabelType.ENDLESS_LABEL,
        barcode_type: str = "QR",
        label_margin: Tuple[int, int, int, int] = (0, 0, 0, 0),
        fore_color: Tuple[int, int, int] = (0, 0, 0),
        text: Optional[List[Dict[str, Any]]] = None,
        qr_size: int = 10,
        qr_correction: str = 'L',
        image_fit: bool = False,
        image_crop: bool = False,
        image_scaling_factor: float = 100.0,
        image_rotation: int = 0,
        image: Optional[Union[Image.Image, None]] = None,
        border_thickness: int = 0,
        border_roundness: int = 0,
        border_distance: Tuple[int, int] = (0, 0),
        border_color: Tuple[int, int, int] = (0, 0, 0),
        timestamp: int = 0,
        counter: int = 0,
        red_support: bool = False,
        code_text: str = '',
        template_env_vars: frozenset = frozenset(),
        warnings: list[str] | None = None,
        shrink_text: bool = False,
        fill_text: bool = False,
        image_text_gap: int | None = None,
        image_position: str | None = None,
        blanks: dict[str, str] | None = None,
        blank_fields: dict | None = None,
        mark_blanks: bool = False,
        layouts: dict | None = None,
        with_code: bool = False,
        flag_wrap: int = 0,
        flag_dpi: int = 300,
        fixed_length: bool = False,
        vertical_align: str = 'center',
    ):
        """Initialize a SimpleLabel object. warnings are problems found
        before, e.g. a font that is not installed. blanks are the values
        for the blanks ({{blank:<name>}}) by name, blank_fields their
        settings as saved with the label, e.g. {"Box": {"type": "number",
        "default": "1"}} or {"Frozen on": {"type": "date", "format":
        "%d.%m.%Y"}}. With mark_blanks the text shows blanks without a value
        as their name and a question mark, as the label designer previews
        them. with_code puts a code next to the image of an image label, on
        the other side of the text. With fixed_length an endless label has
        the size given, it does not grow with its content. vertical_align
        places the content at the top, in the center or at the bottom of a
        label that does not grow with it in height."""
        # Input validation
        if width < 0 or height < 0:
            raise ValueError("Width and height must be non-negative.")
        if border_thickness < 0:
            raise ValueError("Border thickness must be non-negative.")
        if qr_size < 1:
            raise ValueError("QR size must be positive.")
        if image_scaling_factor <= 0:
            raise ValueError("Image scaling factor must be > 0.")
        if image_scaling_factor > MAX_IMAGE_SCALING_FACTOR:
            raise ValueError(f"Image scaling factor must not exceed {MAX_IMAGE_SCALING_FACTOR:g}.")
        if image_rotation < 0 or image_rotation > 360:
            raise ValueError("Image rotation must be between 0 and 360 inclusive.")
        self._width = width
        self._height = height
        self.label_content = label_content
        self.label_orientation = label_orientation
        self.label_type = label_type
        self.barcode_type = barcode_type
        self._label_margin = label_margin
        self._fore_color = fore_color
        self.text = None
        self.input_text = text
        self._qr_size = qr_size
        self.qr_correction = qr_correction
        self._image = image
        self._image_fit = image_fit
        self._image_crop = image_crop
        self._image_scaling_factor = image_scaling_factor
        self._image_rotation = image_rotation
        # Problems found before and by generate(), e.g. text that does not fit
        self._given_warnings = list(warnings or [])
        self.warnings = list(self._given_warnings)
        self._border_thickness = border_thickness
        self._border_roundness = border_roundness
        self._border_distance = border_distance
        self._border_color = border_color
        self._counter = counter
        self._timestamp = timestamp
        self._red_support = red_support
        self._code_text = code_text
        # The code with its templates replaced, set when the label is generated
        self.code_text = code_text
        self._template_env_vars = template_env_vars
        # Text that does not fit is drawn smaller by this factor instead of
        # being cut off
        self._shrink_text = shrink_text
        # Text made as large as fits, the lines keep the ratio of their sizes
        self._fill_text = fill_text
        # A cable flag with a wrap of this many dots at flag_dpi, 0 for none
        self._flag_wrap = flag_wrap
        self._flag_dpi = flag_dpi
        # The text measured at each set of line sizes, see _measure(), shared
        # by the copies of a print job where given
        self._shared_layouts = layouts
        # An endless label of a given length is laid out like a die-cut one
        self._fixed_length = fixed_length
        self._layouts: dict = {}
        self._text_key = ''
        self._text_scale = 1.0
        # The orientation was chosen to fit an image best, not by the
        # request, see turn_to_fit()
        self.auto_orientation = False
        # Distance between an image or code and the text in dots. None
        # chooses it from the size of the text.
        if image_text_gap is not None and image_text_gap < 0:
            raise ValueError("The distance between image and text must be non-negative.")
        self._image_text_gap = image_text_gap
        # Where an image or code is next to the text, see _image_side()
        self._image_position = check_image_position(image_position)
        self._vertical_align = check_vertical_align(vertical_align)
        # How much the image or code was enlarged or shrunk to fit the label
        self._image_scale = 1.0
        # The code of an image label with a code as well: its scale, and its
        # size once it is placed
        self._with_code = with_code
        self._code_scale = 1.0
        self._code_size: tuple[int, int] | None = None
        # The distance used, known once the label is generated
        self.image_text_gap = 0
        # Known once the label is generated as well: where the image or code
        # is next to the text, for labels with both
        self.image_side: str | None = None
        self._blanks = blanks or {}
        self._blank_fields = blank_fields or {}
        self._mark_blanks = mark_blanks
        self._shown_code = code_text
        # The value of each blank, made once for the label
        self._blank_cache: dict[str, str] = {}

    @property
    def label_content(self):
        return self._label_content

    @label_content.setter
    def label_content(self, value):
        self._label_content = value

    def want_text(self, img: Optional[Image.Image]) -> bool:
        """Whether text is drawn: without an image or code always, else the
        lines as drawn, with the marks of a preview for blanks left empty.
        has_lines() looks at them as written, for the layout, has_text() once
        they are filled in, without marks."""
        # If we are not drawing an image, we need to draw text or the label will
        # vanish
        return img is None or self._with_words(self.text)

    @property
    def auto_image_text_gap(self) -> int:
        """The distance between an image or code and the text when none is
        given: two fifths of the largest text as printed, also when it is
        shrunk to fit, for a QR code at least two of its modules as printed,
        so that it stays readable."""
        sizes = [self._line_size(line) for line in self.text if self._has_words(line)]
        gap = round(0.4 * max(sizes)) if sizes else 0
        if self.barcode_type == "QR" and (self._label_content == LabelContent.TEXT_QRCODE or self._with_code):
            scale = self._code_scale if self._with_code else self._image_scale
            gap = max(gap, round(2 * self._qr_size * scale))
        return gap

    @property
    def in_color(self) -> bool:
        """Whether red or color is chosen for a part of the label: its text,
        code or frame, or its image in red and black or in color."""
        black = (0, 0, 0)
        return (self._fore_color != black or (self._border_color != black and self._border_thickness > 0)
                or any(parse_color(line.get('color') or 'black') != black for line in self.text)
                or self._label_content in (LabelContent.IMAGE_RED_BLACK, LabelContent.IMAGE_COLORED))

    @property
    def text(self):
        return self._text

    @text.setter
    def text(self, value):
        self._text = value

    @property
    def qr_correction(self):
        for key, val in self.QR_CORRECTION_MAPPING.items():
            if val == self._qr_correction:
                return key
        return 'L'

    @qr_correction.setter
    def qr_correction(self, value):
        self._qr_correction = self.QR_CORRECTION_MAPPING.get(value, constants.ERROR_CORRECT_L)

    @property
    def label_orientation(self):
        return self._label_orientation

    @label_orientation.setter
    def label_orientation(self, value):
        self._label_orientation = value

    @property
    def label_type(self):
        return self._label_type

    @label_type.setter
    def label_type(self, value):
        self._label_type = value

    def process_templates(self) -> None:
        """Process and replace templates in the text lines and the code. A
        line with line breaks, e.g. from the value of a blank, becomes as many
        lines with its settings."""
        self.text = []
        total_length = 0
        for line in copy.deepcopy(self.input_text):
            # Only the templates set it, not the request
            line.pop('unmarked', None)
            text_val = line.get('text', '')
            if len(text_val) > WARNING_TEXT_LENGTH:
                logger.warning(
                    f"Text line is very long (> {WARNING_TEXT_LENGTH} characters), "
                    "this may lead to long processing times.")
            text_val = self._expand_templates(text_val, line, marks=self._mark_blanks)

            # The limits apply to the text after expanding the templates
            total_length += len(text_val)
            if len(text_val) > MAX_TEXT_LENGTH or total_length > MAX_TOTAL_TEXT_LENGTH:
                raise TextTooLong("Text is too long")
            line['text'] = text_val
            self.text.extend(self._split_line(line))
        # The code with the same counter and date as the text, so that e.g.
        # each copy has its own number in the code as well. Icons are drawn
        # with their font, a code keeps their name. Only labels that draw a
        # code: the label designer keeps the code when the layout changes to
        # text, its templates must neither refuse nor log anything then.
        if self._draws_code():
            code = {}
            self._shown_code = self._expand_templates(self._code_text, code, with_icons=False, marks=self._mark_blanks)
            self.code_text = code.get('unmarked', self._shown_code)
        else:
            self.code_text = self._shown_code = ''

    def _expand_templates(self, text_val: str, line: dict, with_icons: bool = True, marks: bool = False) -> str:
        """Return text_val with its templates replaced. A random text that
        shifts sets 'shift' in line. With marks, blanks without a value show
        their name and a question mark, line['unmarked'] is the text without
        them."""
        random_length = 0

        # Replace {{blank:<name>}} with the value of the blank, before the
        # other templates. The values are inserted as given: the other
        # templates are only replaced in the text around the blanks, so that
        # a value cannot insert e.g. an environment variable. split() puts
        # the names of the blanks at the odd positions.
        parts = BLANK_TEMPLATE.split(text_val)
        names = parts[1::2]
        # Checked while the values are put together, a short line with many
        # blanks could become a huge text
        values, length = [], 0
        for name in names:
            values.append(self._blank_value(name))
            length += len(values[-1])
            if length > MAX_TEXT_LENGTH:
                raise TextTooLong("Text is too long")
        pieces = parts[0::2]

        def substitute(pattern, replacement):
            pieces[:] = [re.sub(pattern, replacement, piece) for piece in pieces]

        # Replace {{counter[:<start>]}} with current label counter (<start> is
        # an optional offset defaulting to 1)
        def counter_replacer(match):
            offset = int(match.group(1)) if match.group(1) else 1
            return str(self._counter + offset)
        substitute(r"\{\{counter(?:\:(\d+))?\}\}", counter_replacer)

        # Replace {{datetime:<format>}} with current datetime formatted as <format>
        def datetime_replacer(match):
            return self._now().strftime(match.group(1))
        substitute(r"\{\{datetime:([^}]+)\}\}", datetime_replacer)

        # Replace {{uuid}} with a new UUID, the same one in the whole text
        if any("{{uuid}}" in piece for piece in pieces):
            ui = uuid.UUID(int=random.getrandbits(128))
            pieces[:] = [piece.replace("{{uuid}}", str(ui)) for piece in pieces]

        # Replace {{short-uuid}} with a shortened UUID
        if any("{{short-uuid}}" in piece for piece in pieces):
            ui = uuid.UUID(int=random.getrandbits(128))
            pieces[:] = [piece.replace("{{short-uuid}}", str(ui)[:8]) for piece in pieces]

        # Replace {{mdi:<name>}} with the Material Design Icon of the name,
        # e.g. {{mdi:lightbulb}}. Unknown names stay as they
        # are, so that a typo shows on the label.
        if with_icons:
            substitute(r"\{\{mdi:([a-z0-9-]+)\}\}", lambda match: icons.icon(match.group(1)) or match.group(0))

        # Replace {{env:<var>}} with the value of the environment variable
        # var. Only explicitly allowed variables are inserted, others could
        # leak secrets into the preview.
        def env_replacer(match):
            var_name = match.group(1)
            if var_name not in self._template_env_vars:
                # Previews are rendered on every change, log only once
                if var_name not in _REFUSED_ENV_VARS and len(_REFUSED_ENV_VARS) < _MAX_REFUSED_ENV_VARS:
                    _REFUSED_ENV_VARS.add(var_name)
                    # repr() so that a name cannot add lines to the log
                    logger.warning(f"Environment variable {var_name!r} is not listed in LABEL_TEMPLATE_ENV_VARS, inserting nothing")
                return ""
            return os.getenv(var_name, "")
        substitute(r"\{\{env:([^}]+)\}\}", env_replacer)

        # Replace {{random[:<len>][:shift]}} with random string of optional
        # length <len> and shifting instruction. ":s" is accepted as
        # shorthand for ":shift"
        def random_replacer(match):
            nonlocal random_length
            length = int(match.group(1)) if match.group(1) else DEFAULT_RANDOM_LENGTH
            if length > MAX_RANDOM_LENGTH:
                raise ValueError(f"Random text length must not exceed {MAX_RANDOM_LENGTH}")
            # Many short templates must not add up to a huge line either
            random_length += length
            if random_length > MAX_TEXT_LENGTH:
                raise TextTooLong("Text is too long")
            if match.group(2):
                line['shift'] = True
            return ''.join(random.choices(string.ascii_letters + string.digits + string.punctuation, k=length))
        substitute(r"\{\{random(?:\:(\d+))?(?:\:(s(hift)?))?\}\}", random_replacer)
        text = ''.join(piece + value for piece, value in zip(pieces, values + ['']))
        if not marks:
            return text
        # The same pieces, e.g. with the same random text: a code that takes
        # the text gets it as printed
        line['unmarked'] = text
        # A name with a line break makes no more lines than the values
        shown = [value if value != '' else re.sub(r'[\r\n]', ' ', name) + '?' for name, value in zip(names, values)]
        return ''.join(piece + value for piece, value in zip(pieces, shown + ['']))

    @staticmethod
    def _split_line(line: dict) -> list:
        """The lines a line shows, one for each of its line breaks, all with
        its settings. A line break failed the label, Pillow cannot place
        several lines by the top of the letters, and a carriage return was
        drawn as a box."""
        def lines(text):
            return text.replace('\r\n', '\n').replace('\r', '\n').split('\n')
        texts = lines(line['text'])
        if 'unmarked' not in line:
            return [dict(line, text=text) for text in texts]
        # The marks of the blanks in a preview have no line breaks
        return [dict(line, text=text, unmarked=unmarked) for text, unmarked in zip(texts, lines(line['unmarked']))]

    def _now(self) -> datetime.datetime:
        """The time the label is made for: its timestamp, or else now."""
        return datetime.datetime.fromtimestamp(self._timestamp) if self._timestamp > 0 else datetime.datetime.now()

    def _shown_marks(self, lines: list) -> str:
        """What a preview shows in a code that would be empty when printed:
        the blanks still to fill in, of the code or else of the text."""
        return self._shown_code or "\n".join(line.get('text', '') for line in lines)

    @staticmethod
    def _printed_text(line: dict) -> str:
        """The text of a line as printed: a preview may show marks for its
        blanks."""
        return line.get('unmarked', line.get('text', ''))

    def has_text(self) -> bool:
        """Whether the label has text. A line of blanks left empty has none."""
        return any(self._has_content(line.get('text', '')) for line in self.input_text or [])

    def _has_content(self, text) -> bool:
        """Whether text shows anything once its blanks are filled in, blanks
        left empty show nothing. Looked at piece by piece, the values of many
        blanks are not put together."""
        parts = BLANK_TEMPLATE.split(str(text))
        return any(piece.strip() for piece in parts[0::2]) or any(self._blank_value(name).strip() for name in parts[1::2])

    def turn_to_fit(self, width: int, height: int, orientation: LabelOrientation) -> None:
        """Take the size and orientation in which its image comes out
        largest. It has no lines of text then."""
        self._width, self._height = width, height
        self.label_orientation = orientation
        self.auto_orientation = True

    def _code_given(self) -> bool:
        """Whether the label has a code of its own, else the code takes the
        text. A given code stays the code, even if its templates fill in
        nothing. Blanks left empty are not given: a code made of them, also
        with spaces between them, takes the text, as one left empty does."""
        return self._has_content(self._code_text)

    def _blank_value(self, name: str) -> str:
        """The value of the blank name: as given, else its default saved with
        the label, else today's date for a date and nothing for others. A
        date like 2026-11-03 is written in the format of the blank, e.g.
        %d.%m.%Y, else as it is."""
        if name not in self._blank_cache:
            self._blank_cache[name] = self._fill_blank(name)
        return self._blank_cache[name]

    def _fill_blank(self, name: str) -> str:
        field = self._blank_fields.get(name)
        field = field if isinstance(field, dict) else {}
        # A value given is used, also one left empty on purpose
        given = name in self._blanks
        if given:
            value = str(self._blanks[name])
        else:
            value = '' if field.get('default') is None else str(field['default'])
        # Without a type a blank is text, the label designer saves the type
        if field.get('type') != 'date':
            return value
        if value == '' and given:
            return ''
        # Without a value the same date as the other templates
        date = self._now().date() if value == '' else _iso_date(value)
        if date is None:
            return value
        date_format = str(field.get('format') or '%Y-%m-%d')
        if len(date_format) > MAX_DATE_FORMAT_LENGTH:
            raise TextTooLong("The date format of a blank is too long")
        try:
            return date.strftime(date_format)
        except ValueError:
            return date.isoformat()

    def generate(self):
        img = self._generate_label()
        if not self._flag_wrap:
            return img
        # Both halves from one render, so random texts and counters match
        strip = flag_strip(img, self._flag_wrap, self._flag_dpi, self._label_orientation == LabelOrientation.ROTATED)
        check_image_size(*strip.size)
        return strip

    def _generate_label(self):
        # Process possible templates in the text and the code
        self.process_templates()
        # A preview shows a code of the blanks still to fill in, printed it
        # would be empty
        self._code_of_marks = False

        # Generate codes or load images if requested. An image label with a
        # code has both.
        if self._with_code and self._image is None:
            raise ValueError("The label has no image")
        code = None
        if self._draws_code():
            code = self._generate_qr() if self.barcode_type == "QR" else self._generate_barcode()
        if self._label_content in (LabelContent.QRCODE_ONLY, LabelContent.TEXT_QRCODE):
            img, code = code, None
        elif self._label_content in IMAGE_CONTENTS:
            img = self._image
        else:
            img = None

        # Initialize dimensions
        width, height = self._width, self._height
        margin_left, margin_right, margin_top, margin_bottom = self._label_margin

        # The parts of a side of the label that the image or code may take
        # when fitted, next to the text and a code on the other side of it
        parts = 1 + self.has_lines() + (code is not None)
        if img is not None:
            img, self._image_scale = self._sized(img, width, height, parts)
            img_width, img_height = img.size
        else:
            img_width, img_height = (0, 0)
        # The code next to an image keeps its size where it is not fitted:
        # the crop, rotation and scale are those of the image
        if code is not None:
            code, self._code_scale = self._sized(code, width, height, parts, own_look=False)
        self._code_size = code.size if code is not None else None

        if self.want_text(img):
            self._text_scale = 1.0
            self._layouts = self._shared_layouts if self._shared_layouts is not None else {}
            # The text is part of the key: copies differ e.g. by a counter
            self._text_key = json.dumps(self.text, sort_keys=True, default=str)
            bboxes, textsize = self._measure()
            if self._shrink_text or self._fill_text:
                bboxes, textsize = self._shrink_text_next_to_image(bboxes, textsize, width, height, img_width, img_height)
        else:
            bboxes = []
            textsize = (0, 0, 0, 0)

        # Distance between image or code and text, only where the label has
        # both, and between an image and a code without text
        gap = self._gap(img_width, img_height) if bboxes or code is not None else 0
        self.image_text_gap = gap
        # Text made as small as it gets that still does not fit has no room,
        # e.g. below a code that takes the whole height of a die-cut label
        no_room = (bool(bboxes) and (self._shrink_text or self._fill_text) and self.has_text()
                   and not self._fits_room(textsize, *self._text_space(width, height, img_width, img_height, gap)))

        # The image or code is at the margin on its side of the text, the
        # code of a label with both at the other one, the text in the middle
        # of the room left. An endless label grows along the tape to hold
        # them, the text starts at the margin or next to an image or code
        # there. Without an image or code there is no side for it.
        paired = img is not None and (self.has_lines() or code is not None)
        side = self._image_side(with_text=paired)
        self.image_side = side if paired else None
        beside = side in ('left', 'right')
        # Left or above the text, and right or below it
        first, last = (img, code) if side in ('left', 'above') else (code, img)
        (first_width, first_height), (last_width, last_height) = (item.size if item is not None else (0, 0) for item in (first, last))
        first_gap = gap if first is not None else 0
        last_gap = gap if last is not None and (bboxes or first is None) else 0
        grows_wide, grows_high = self._grows()
        text_height = textsize[3] - textsize[1]
        # Where a letter reaches left of where the lines start, e.g. a "j",
        # the text takes room for it: a label that grows in width makes it
        text_width = self._text_width(textsize)
        bearing = -min(textsize[0], 0)
        if grows_high:
            height = (max(first_height, text_height, last_height) if beside
                      else first_height + first_gap + text_height + last_gap + last_height) + margin_top + margin_bottom
        if grows_wide:
            width = (first_width + first_gap + text_width + last_gap + last_width if beside
                     else max(first_width, text_width, last_width)) + margin_left + margin_right

        if beside:
            # In the middle of the room between the margins, at its start if
            # the text is wider
            room = width - margin_left - margin_right - first_width - first_gap - last_width - last_gap
            horizontal_offset_text = margin_left + first_width + first_gap + max((room - text_width)//2, 0) + bearing
            # From its top, e.g., of an icon reaching above the first line.
            # All share their middle, at the top or bottom the highest of
            # them is at the margin.
            block = max(first_height, text_height, last_height)
            shift = self._vertical_start(height, block) - ((height - block)//2 + (margin_top - margin_bottom)//2)
            vertical_offset_text = (height - textsize[3] - textsize[1])//2 + (margin_top - margin_bottom)//2 + shift
            first_offset = margin_left, (height - first_height)//2 + (margin_top - margin_bottom)//2 + shift
            last_offset = width - margin_right - last_width, (height - last_height)//2 + (margin_top - margin_bottom)//2 + shift
        else:
            start = margin_top if grows_high else self._vertical_start(height, first_height + first_gap + text_height + last_gap + last_height)
            # Across, in the middle between the margins
            horizontal_offset_text = max((width - text_width)//2 + (margin_left - margin_right)//2, 0) + bearing
            vertical_offset_text = start + first_height + first_gap - textsize[1]
            first_offset = (width - first_width)//2 + (margin_left - margin_right)//2, margin_top
            last_offset = (width - last_width)//2 + (margin_left - margin_right)//2, height - margin_bottom - last_height
            if self._placed() != 'center':
                # At the top or bottom, all of them at their distance
                first_offset = first_offset[0], start
                last_offset = last_offset[0], start + first_height + first_gap + text_height + last_gap

        text_offset = horizontal_offset_text, vertical_offset_text
        image_offset, code_offset = (first_offset, last_offset) if first is img else (last_offset, first_offset)

        # Ensure height and width are at least 1 to avoid generating valishing
        # preview images for empty inputs. PIL cannot store images with 0
        # width/height dimensions
        width = max(int(width), 1)
        height = max(int(height), 1)

        # Content beyond the label is cut off, which is easy to miss in the
        # preview
        self.warnings = list(self._given_warnings)
        if self._code_of_marks:
            self.warnings.append('The code is empty until its blanks are filled in.')
        what = 'code' if self._label_content in (LabelContent.QRCODE_ONLY, LabelContent.TEXT_QRCODE) else 'image'
        # What is placed next to the text, by name
        boxes = [(name, (x, y, x + item.width, y + item.height))
                 for name, item, (x, y) in ((what, img, image_offset), ('code', code, code_offset)) if item is not None]
        if self.want_text(img) and bboxes:
            x, y = text_offset
            text_box = (x + textsize[0], y + textsize[1], x + textsize[2], y + textsize[3])
            overlapped = next((name for name, box in boxes if self._overlaps(text_box, box)), None)
            if no_room:
                self.warnings.append('The text does not fit on the label at any size.')
            elif text_box[0] < 0 or text_box[1] < 0 or text_box[2] > width or text_box[3] > height:
                self.warnings.append('The text does not fit on the label, part of it is cut off.')
            elif overlapped:
                self.warnings.append(f'The text does not fit next to the {overlapped}, it overlaps it.')
        for name, box in boxes:
            if box[0] < 0 or box[1] < 0 or box[2] > width or box[3] > height:
                self.warnings.append(f'The {name} does not fit on the label, part of it is cut off.')
        if len(boxes) == 2 and self._overlaps(boxes[0][1], boxes[1][1]):
            self.warnings.append('The code does not fit next to the image, it overlaps it.')

        logger.debug(f"Image resolution: {int(width)} x {int(height)} px")
        check_image_size(width, height)
        imgResult = Image.new('RGB', (int(width), int(height)), 'white')

        if img is not None:
            imgResult.paste(img, image_offset)
        if code is not None:
            imgResult.paste(code, code_offset)

        if self.want_text(img):
            self._draw_text(imgResult, bboxes, text_offset)

        # Draw border if thickness > 0
        if self._border_thickness > 0:
            draw = ImageDraw.Draw(imgResult)
            # Calculate border rectangle (inside the image, respecting thickness)
            rect = [self._border_distance[0],
                    self._border_distance[1],
                    imgResult.width - self._border_distance[0] - 1,
                    imgResult.height - self._border_distance[1] - 1]
            # Validity checks on rect:
            # - x1 >= x0
            # - y1 >= y0
            if rect[2] < rect[0] or rect[3] < rect[1]:
                raise ValueError("Invalid border rectangle")

            # Draw (rounded) rectangle
            draw.rounded_rectangle(rect, radius=self._border_roundness, outline=self._border_color, width=self._border_thickness)
        return imgResult

    def _sized(self, img: Image.Image, width: int, height: int, parts: int, own_look: bool = True) -> tuple[Image.Image, float]:
        """img as it is placed on the label and how much it was enlarged or
        shrunk: with own_look cropped, rotated and scaled as set, fitted to
        the label where image_fit is set. Next to text, along a side of the
        label that does not grow with the text, it takes one of parts of it."""
        margin_left, margin_right, margin_top, margin_bottom = self._label_margin
        if own_look and self._image_crop:
            img = self._crop_image_to_content(img)

        # First rotate the image if requested
        if own_look and self._image_rotation != 0 and self._image_rotation != 360:
            # expand=True enlarges the canvas to fit the rotated image
            angle = math.radians(self._image_rotation)
            cos, sin = abs(math.cos(angle)), abs(math.sin(angle))
            check_image_size(img.width * cos + img.height * sin, img.width * sin + img.height * cos)
            img = img.rotate(-self._image_rotation, expand=True, fillcolor="white")

        # Rotation with expand=True may add an empty border around content.
        if own_look and self._image_crop:
            img = self._crop_image_to_content(img)
        # Transparent once it is cropped to its content, white from here
        img = on_white(img)

        # Resize image to fit if image_fit is True
        if self._image_fit:
            # Calculate the maximum allowed dimensions
            max_width = max(width - margin_left - margin_right, 1)
            max_height = max(height - margin_top - margin_bottom, 1)
            # Next to text, along a side of the label that does not grow
            # with the text, the image takes its part of it, the text the
            # rest: half of it, a third with a code on the other side
            if parts > 1:
                grows_wide, grows_high = self._grows()
                if self._image_side(True) in ('left', 'right'):
                    if not grows_wide:
                        max_width = max(max_width // parts, 1)
                elif not grows_high:
                    max_height = max(max_height // parts, 1)

            # Get image dimensions
            img_width, img_height = img.size

            # Print the original image size
            logger.debug(f"Maximal allowed dimensions: {max_width}x{max_height} mm")
            logger.debug(f"Original image size: {img_width}x{img_height} px")

            # Resize the image to fit within the maximum dimensions. A
            # label that grows with its content, endless tape, has only
            # the size across the tape to fit into.
            grows_wide, grows_high = self._grows()
            if grows_high:
                scale = max_width / img_width
            elif grows_wide:
                scale = max_height / img_height
            else:
                scale = min(max_width / img_width, max_height / img_height)
            logger.debug(f"Scaling image by factor: {scale}")
        else:
            # Use image_scaling_factor if provided
            scale = self._image_scaling_factor / 100.0 if own_look else 1.0
            logger.debug(f"Manual image scaling factor: {scale}")
        new_size = (max(1, int(img.width * scale)), max(1, int(img.height * scale)))
        logger.debug(f"Resized image size: {new_size} px")
        check_image_size(*new_size)
        return img.resize(new_size, Image.Resampling.LANCZOS), scale

    @staticmethod
    def _crop_image_to_content(img: Image.Image) -> Image.Image:
        """Trim uniform border/background from an image.

        The background color is inferred from the top-left pixel to support
        white as well as non-white backgrounds.
        """
        if img.width <= 1 or img.height <= 1:
            return img

        if 'A' in img.getbands():
            alpha_bbox = img.getchannel('A').getbbox()
            if alpha_bbox is not None:
                return img.crop(alpha_bbox)

        background_color = img.getpixel((0, 0))
        background = Image.new(img.mode, img.size, background_color)
        diff = ImageChops.difference(img, background)
        bbox = diff.getbbox()
        if bbox is None:
            return img
        return img.crop(bbox)

    def _generate_barcode(self):
        # An unknown type is refused before the value is looked at
        barcode_class = barcode.get_barcode_class(self.barcode_type)
        if self._code_given():
            value = self.code_text
        else:
            # Take value from the first line of text, as printed
            value = self._printed_text(self.text[0]) if self.text else ''
            if not value.strip() and self._mark_blanks:
                value = self._shown_marks(self.text[:1])
                self._code_of_marks = True
        if not value:
            raise ValueError("The barcode is empty")
        if len(value) > MAX_BARCODE_LENGTH:
            raise ValueError(f"Barcode value must not exceed {MAX_BARCODE_LENGTH} characters")
        # The marks of blanks are said to be no code below
        if not self._code_of_marks:
            check_check_digit(self.barcode_type, value)
        try:
            return _barcode_image(barcode_class, self.barcode_type, value, self._fore_color)
        except BarcodeError:
            # The marks of blanks are no digits, e.g. of an EAN
            if self._code_of_marks:
                raise ValueError("The code is empty until its blanks are filled in.") from None
            raise

    def _generate_qr(self):
        if self._code_given():
            text = self.code_text
        else:
            # Combine texts from all lines for QR code, as printed
            text = "\n".join(self._printed_text(line) for line in self.text)
            if not text.strip() and self._mark_blanks:
                text = self._shown_marks(self.text)
                self._code_of_marks = True
        if not text.strip():
            raise ValueError("The QR code is empty")
        # Black and red by name, a color as RGB gives another image mode
        names = {color: name for name, color in COLORS.items()}
        return _qr_image(text, self._qr_correction, self._qr_size, names.get(self._fore_color, self._fore_color))

    def _draw_text(self, img = None, bboxes = [], text_offset = (0, 0)):
        """
        Returns a list of bounding boxes for each line, so each line can use a different font.
        """
        do_draw = img is not None
        if not do_draw:
            img = Image.new('L', (20, 20), 'white')
        draw = ImageDraw.Draw(img)
        y = 0

        # Empty lines at the end leave the text as high as without them: the
        # last line that shows something is measured like the final line
        # where only an empty final line follows, which takes no room and no
        # spacing before it. Empty lines before the final one stay blank
        # lines, a line before them is measured like any other.
        last = len(self.text) - 1
        last_shown = max((i for i, line in enumerate(self.text) if self._shows_something(line)), default=last)
        spaced = last if self.text and self._shows_something(self.text[-1]) else last - 1

        # Iterate over lines of text
        for i, line in enumerate(self.text):
            # Calculate spacing
            spacing = int(self._line_size(line)*((int(line['line_spacing']) - 100) / 100)) if 'line_spacing' in line else 0

            # Get font
            font = self._get_font(line['path'], self._line_size(line), measuring=not do_draw)

            # Determine anchors
            anchor = None
            align = line.get('align', 'center')

            # Left aligned text
            if align == "left":
                anchor = "lt"
            # Center aligned text
            elif align == "center":
                anchor = "mt"
            # Right aligned text
            elif align == "right":
                anchor = "rt"
            # else: error
            else:
                raise ValueError(f"Unsupported alignment: {align}")

            color = parse_color(line.get('color') or 'black')

            # Draw checkbox if needed
            checkbox = line.get('checkbox', False)

            INVERT_LINE = 'inverted' in line and line['inverted']
            if do_draw and INVERT_LINE:
                # Draw a filled rectangle
                center_x = 0
                if anchor == "lt":
                    min_bbox_x = text_offset[0] + min(bbox[0][0] for bbox in bboxes)
                    max_bbox_x = text_offset[0] + bboxes[i][0][2] # max(bbox[0][2] for bbox in bboxes)
                elif anchor == "mt":
                    min_bbox_x = min(bbox[0][0] for bbox in bboxes)
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes)
                    center_x = (min_bbox_x + max_bbox_x) // 2
                    min_bbox_x = text_offset[0] + center_x - (bboxes[i][0][2] - bboxes[i][0][0]) // 2
                    max_bbox_x = text_offset[0] + center_x + (bboxes[i][0][2] - bboxes[i][0][0]) // 2
                elif anchor == "rt":
                    max_bbox_x = text_offset[0] + max(bbox[0][2] for bbox in bboxes)
                    min_bbox_x = max_bbox_x - (bboxes[i][0][2] - bboxes[i][0][0])
                # As high as the line was measured, see below
                y_min = bboxes[i][0][1] + text_offset[1]
                y_max = bboxes[i][0][3] + text_offset[1]
                draw.rectangle((min_bbox_x, y_min, max_bbox_x, y_max), fill=color)
                # Overwrite font color with white on colored background
                color = (255, 255, 255)

            # Either calculate bbox or actually draw
            if not do_draw:
                # Get bbox of the text. A checkbox is drawn in front of the
                # text, its room counts to the line, so that the label is
                # large enough for it (labels along the tape are only as long
                # as their text).
                checkbox_space = self._checkbox_space(line) if checkbox else 0
                bbox = self._draw_line(draw, (checkbox_space, y), line['text'], font, "lt", align=align, measuring=not do_draw)
                if checkbox:
                    bbox = (0, bbox[1], bbox[2], bbox[3])

                # Ensure consistent line heights for each line except the last
                # one (where it is not needed)
                IS_LAST_LINE = i == last or (i == last_shown and last_shown >= last - 1)
                # Where the top of the letters is drawn
                text_y = y
                if INVERT_LINE:
                    # White on black: the box reaches from the ascender of
                    # the font to its descender, the same for all lines of a
                    # size, and holds what reaches further, e.g. an icon. It
                    # held only the letters of ASCII and was drawn higher
                    # than them, those reaching below the line like a "g"
                    # were white on white. The letters are drawn from their
                    # top, so they move down by the room above them.
                    ascent, descent = font.getmetrics()
                    baseline = self._baseline(draw, line['text'], font)
                    top = min(bbox[1] - y, baseline - ascent)
                    bottom = max(bbox[3] - y, baseline + descent)
                    text_y = y - top
                    bbox = (bbox[0], y, bbox[2], y + bottom - top)
                elif not IS_LAST_LINE:
                    # Some characters may need special height
                    all_characters = ''.join(string.ascii_letters + string.digits + string.punctuation)
                    Ag = draw.textbbox((0, y), all_characters, font, anchor="lt")
                    # Get bbox with width of text and dummy height
                    bbox = (bbox[0], Ag[1], bbox[2], Ag[3])
                # The box is drawn from the top of the line, its lower edge
                # included, also where the text is lower or there is none
                if checkbox:
                    bbox = (bbox[0], bbox[1], bbox[2], max(bbox[3], text_y + 8 * self._line_size(line) // 10 + 1))
                bboxes.append((bbox, text_y))
                y += bbox[3] - bbox[1] + (spacing if i < spaced else 0)
            else:
                bbox = bboxes[i][0]
                y = bboxes[i][1] + text_offset[1]
                # Left aligned text
                if align == "left":
                    # Where the lines start, letters reaching left of it,
                    # e.g. a "j", are in the room made for them
                    x = text_offset[0]
                # Center aligned text
                elif align == "center":
                    min_bbox_x = min(bbox[0][0] for bbox in bboxes) if len(bboxes) > 0 else 0
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes) if len(bboxes) > 0 else 0
                    # In the middle with a letter reaching left, e.g. a "j",
                    # as its white on black box: the font puts the middle of
                    # its width there
                    x = (max_bbox_x - min_bbox_x) // 2 + min_bbox_x + text_offset[0] - bbox[0] // 2
                # Right aligned text
                elif align == "right":
                    max_bbox_x = max(bbox[0][2] for bbox in bboxes) if len(bboxes) > 0 else 0
                    x = max_bbox_x + text_offset[0]

                # Draw checkbox if needed, in the room left for it in front of
                # the text
                if checkbox:
                    if align == "left":
                        x += self._checkbox_space(line)
                    elif align == "center":
                        x += self._checkbox_space(line) / 2
                    checkbox_box_dimensions = 8 * self._line_size(line) // 10
                    bbox = self._draw_line(draw, (x - 1.2 * checkbox_box_dimensions, y), line['text'], font, anchor, align=align, measuring=not do_draw)
                    box_dimensions = bbox[0], y, bbox[0] + checkbox_box_dimensions, y + checkbox_box_dimensions
                    draw.rounded_rectangle(box_dimensions, radius=5, outline=color, width=max(1, checkbox_box_dimensions//10), fill=(255,255,255))

                self._draw_line(draw, (x, y), line['text'], font, anchor, fill=color, align=align, spacing=spacing)

                # Shift text around if requested
                if "shift" in line:
                    def get_shift_amount():
                        return 0.03 * random.randint(5, 10) * self._line_size(line)
                    for x_shift in [-get_shift_amount(), get_shift_amount()]:
                        for y_shift in [-get_shift_amount(), get_shift_amount()]:
                            new_random_text = ''.join(random.choices(string.ascii_letters + string.digits + string.punctuation, k=len(line['text'])))
                            draw.text((x + x_shift, y + y_shift), new_random_text, color, font=font, anchor=anchor, align=align, spacing=spacing)

        # Return total bbox
        # each in form (x0, y0, x1, y1)
        return bboxes

    def _draw_line(self, draw, xy, text, font, anchor, fill=None, measuring=False, **kwargs):
        """The bounding box of a line of text at xy, also drawn in fill if
        given. Icons ({{mdi:<name>}}) are drawn with the icon font, on the
        same baseline as the text around them."""
        if not any(icons.is_icon(char) for char in text):
            if fill is not None:
                draw.text(xy, text, fill, font=font, anchor=anchor, **kwargs)
            return draw.textbbox(xy, text, font=font, anchor=anchor, align=kwargs.get('align', 'left'))
        icon_font = self._get_font(icons.FONT_PATH, font.size, measuring=measuring)
        runs = [(''.join(chars), icon_font if is_icon else font)
                for is_icon, chars in itertools.groupby(text, icons.is_icon)]
        width = sum(run_font.getlength(run) for run, run_font in runs)
        # Anchors are "lt", "mt" or "rt": left, middle or right, top of the text font
        x = xy[0] - {'l': 0, 'm': width / 2, 'r': width}[anchor[0]]
        # The runs share the baseline below the top of an "H"
        baseline = xy[1] + self._baseline(draw, text, font)
        boxes = []
        for run, run_font in runs:
            if fill is not None:
                draw.text((x, baseline), run, fill, font=run_font, anchor='ls')
            boxes.append(draw.textbbox((x, baseline), run, font=run_font, anchor='ls'))
            x += run_font.getlength(run)
        return (min(b[0] for b in boxes), min(b[1] for b in boxes), max(b[2] for b in boxes), max(b[3] for b in boxes))

    @staticmethod
    def _baseline(draw, text, font) -> int:
        """How far below xy _draw_line puts the baseline of text: Pillow's
        top anchor is the top of the letters, with icons that of an "H"."""
        if any(icons.is_icon(char) for char in text):
            text = 'H'
        return -draw.textbbox((0, 0), text, font=font, anchor='ls')[1]

    def _line_size(self, line) -> int:
        """Font size of a line, made smaller when the text is shrunk to fit."""
        return max(1, int(int(line['size']) * self._text_scale))

    @staticmethod
    def _has_words(line) -> bool:
        """Whether a line has text other than spaces: as written in the
        lines of the request, as printed in the lines filled in."""
        return bool(str(line.get('text', '')).strip())

    @classmethod
    def _shows_something(cls, line) -> bool:
        """Whether a line puts anything on the label: text, a to-do box or
        white on black."""
        return cls._has_words(line) or bool(line.get('checkbox')) or bool(line.get('inverted'))

    def _checkbox_space(self, line) -> int:
        """Room for the checkbox in front of a line: the box and a gap."""
        return math.ceil(1.2 * (8 * self._line_size(line) // 10))

    def _gap(self, img_width, img_height) -> int:
        """The distance between an image or code and the text, where both
        are on the label: as given, or else the automatic one."""
        if not (img_width or img_height):
            return 0
        if self._image_text_gap is None:
            return self.auto_image_text_gap
        return self._image_text_gap

    def _vertical_start(self, height: int, extent: int) -> int:
        """Where content of extent dots begins on a label of height dots: in the
        middle between the top and bottom margins or at one of them, in the
        middle on a label that grows with its content."""
        margin_top, margin_bottom = self._label_margin[2:]
        if self._placed() == 'top':
            return margin_top
        if self._placed() == 'bottom':
            return height - margin_bottom - extent
        return (height - extent)//2 + (margin_top - margin_bottom)//2

    def _placed(self) -> str:
        """Where the content is placed: as given, in the middle on a label
        growing in height."""
        return 'center' if self._grows()[1] else self._vertical_align

    def _image_side(self, with_text: bool = True) -> str:
        """Where the image or code is next to the text: as given, else
        above it on labels across the tape and left of it along the tape.
        Without text it is where it is without a choice."""
        if self._image_position and with_text:
            return self._image_position
        # An image with a code is left of the text, the code right of it
        if self._with_code:
            return 'left'
        return 'above' if self._label_orientation == LabelOrientation.STANDARD else 'left'

    def _draws_code(self) -> bool:
        """Whether the label has a code: alone, with text, or next to an
        image as well."""
        return self._label_content in (LabelContent.QRCODE_ONLY, LabelContent.TEXT_QRCODE) or self._with_code

    def has_lines(self) -> bool:
        """Whether the label is laid out with text: a line that is not empty
        as written, also one of blanks left empty. The image or code is where
        it is with text, whatever the blanks are filled with, so that a preview
        that shows blanks left empty looks like the label printed."""
        return self._with_words(self.input_text)

    def _with_words(self, lines) -> bool:
        """Whether lines have text other than spaces to lay out or draw, never
        on a label of a code only."""
        return self._label_content != LabelContent.QRCODE_ONLY and any(self._has_words(line) for line in lines or [])

    @staticmethod
    def _overlaps(a, b) -> bool:
        """Whether the boxes a and b (x0, y0, x1, y1) overlap."""
        return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]

    def _grows(self) -> tuple[bool, bool]:
        """Whether the label grows with its content in width and in height:
        endless labels do along the tape, unless they have a fixed length."""
        endless = self._label_type == LabelType.ENDLESS_LABEL and not self._fixed_length
        return endless and self._label_orientation == LabelOrientation.ROTATED, endless and self._label_orientation == LabelOrientation.STANDARD

    def _text_space(self, width, height, img_width, img_height, gap, largest=False):
        """Width and height the text may take within the margins, beside,
        above or below an image or code at the distance gap, and a code on
        the other side of an image label with both. None where an endless
        label grows with the text, with largest as far as a label may be
        large."""
        margin_left, margin_right, margin_top, margin_bottom = self._label_margin
        grows_wide, grows_high = self._grows()
        if largest and grows_wide:
            width, grows_wide = min(MAX_LABEL_PIXELS // max(height, 1), MAX_FILLED_RATIO * height), False
        if largest and grows_high:
            height, grows_high = min(MAX_LABEL_PIXELS // max(width, 1), MAX_FILLED_RATIO * width), False
        beside = self._image_side() in ('left', 'right')
        # A code next to an image is on the other side of the text
        code_width, code_height = (self._code_size[0] + gap, self._code_size[1] + gap) if self._code_size else (0, 0)
        max_width = None if grows_wide else width - margin_left - margin_right - (img_width + gap + code_width if beside else 0)
        max_height = None if grows_high else height - margin_top - margin_bottom - (0 if beside else img_height + gap + code_height)
        return max_width, max_height

    def _shrink_text_next_to_image(self, bboxes, textsize, width, height, img_width, img_height):
        """Shrink the text of full size (bboxes, textsize) until it fits
        beside or below an image or code, or on the label without one. With
        fill_text make it as large as fits there instead.

        The automatic distance follows the text as printed, while the room
        left for the text depends on the distance. Text fitted at a distance
        fits on the label if its own distance is not larger, and the smaller
        the distance it is fitted at, the larger it comes out. So the
        smallest distance that the text fitted at it keeps to is searched.
        Each fit narrows the range from both sides: the text fitted at a
        distance it keeps to has a distance not below the one searched, one
        it does not keep to is below it, and the one searched is not above
        the distance of the text fitted at it. The lowest is tried first, it
        is often close, then the range is halved."""
        full_size = (bboxes, textsize)
        fits = {}
        # Filled in, the size of the largest line at each distance tried
        sizes = {}

        def fit(gap):
            if gap not in fits:
                if self._fill_text:
                    # A larger distance leaves less room: the size filled in
                    # at one fits at this one too, the size at a smaller one
                    # is the most it can get
                    fitted = self._fill_room(*self._text_space(width, height, img_width, img_height, gap, largest=True),
                                             at_least=max((size for g, size in sizes.items() if g > gap), default=1),
                                             at_most=min((size for g, size in sizes.items() if g < gap), default=MAX_FILL_SIZE))
                    sizes[gap] = self._filled_size
                else:
                    self._text_scale = 1.0
                    fitted = self._shrink_text_to_fit(*full_size, *self._text_space(width, height, img_width, img_height, gap))
                fits[gap] = (self._text_scale, fitted)
            self._text_scale = fits[gap][0]
            return fits[gap]

        # Fitted at the distance of the largest text, the text keeps to it:
        # the full-size text, or filled in at no distance. Its own distance
        # is the smallest that can be kept to.
        if self._fill_text and self._image_text_gap is None:
            fit(0)
        else:
            self._text_scale = 1.0
        high = self._gap(img_width, img_height)
        best, best_gap = fit(high), high
        low = self._gap(img_width, img_height)
        # Where the room for the text does not depend on the distance, e.g.
        # below a code on endless tape, the text is the same at any distance
        if self._text_space(width, height, img_width, img_height, low, largest=self._fill_text) == self._text_space(width, height, img_width, img_height, high, largest=self._fill_text):
            low = high
        middle = low
        while low < high:
            candidate = fit(middle)
            own = self._gap(img_width, img_height)
            if own <= middle:
                high, best, best_gap = middle, candidate, middle
                low = max(low, own)
            else:
                low, high = middle + 1, min(high, own)
            middle = (low + high) // 2
        if best_gap != high:
            candidate = fit(high)
            # Rounded font sizes may not follow the reasoning exactly: only a
            # distance the text keeps to is used
            if self._gap(img_width, img_height) <= high:
                best = candidate
        self._text_scale, (bboxes, textsize) = best
        return bboxes, textsize

    def _fill_room(self, max_width, max_height, at_least=1, at_most=MAX_FILL_SIZE):
        """Make the text as large as fits into max_width x max_height, all
        lines by the same factor: the size of the largest line is searched,
        between at_least, known to fit, and at_most where known. None where
        an endless label grows with the text."""
        largest = max((int(line['size']) for line in self.text), default=0)
        self._filled_size = largest

        def layout(size):
            # A half more, so that the largest line gets exactly this size
            self._text_scale = (size + 0.5) / largest
            return self._text_scale, self._measure()

        def fits(size):
            return self._fits_room(layout(size)[1][1], max_width, max_height)

        # Lines that all came out empty, e.g. of blanks left empty, have
        # nothing to fill the label with: their height alone would grow
        if largest < 1 or not any(self._has_words(line) for line in self.text):
            self._text_scale = 1.0
            return self._measure()
        low = max(1, at_least)
        if at_most < MAX_FILL_SIZE:
            high = max(low, at_most)
        else:
            # Text grows about as its size: the size as set tells where to look
            textsize = layout(largest)[1][1]
            factors = [room / extent for room, extent in ((max_width, self._text_width(textsize)), (max_height, textsize[3])) if room is not None and extent > 0]
            high = min(MAX_FILL_SIZE, max(low + 1, int(largest * min(factors, default=1.0) * 1.2) + 2))
            while high < MAX_FILL_SIZE and fits(high):
                low, high = high, min(MAX_FILL_SIZE, high * 2)
        if fits(high):
            low = high
        while low < high:
            middle = (low + high + 1) // 2
            if fits(middle):
                low = middle
            else:
                high = middle - 1
        self._filled_size = low
        self._text_scale, fitted = layout(low)
        return fitted

    @staticmethod
    def _text_width(textsize) -> int:
        """The width text of textsize takes: up to its right edge, from where
        a letter reaches left of where the lines start, e.g. a "j"."""
        return textsize[2] - min(textsize[0], 0)

    @classmethod
    def _fits_room(cls, textsize, max_width, max_height) -> bool:
        """Whether text of textsize fits into max_width x max_height, None
        where an endless label grows with the text."""
        return (max_width is None or cls._text_width(textsize) <= max_width) and (max_height is None or textsize[3] <= max_height)

    def _shrink_text_to_fit(self, bboxes, textsize, max_width, max_height):
        """Make all lines smaller by the same factor until the text fits into
        max_width x max_height. Font sizes are whole numbers and text does not
        scale exactly, so it may take a few steps."""
        for _ in range(10):
            factor = 1.0
            if max_width is not None and self._text_width(textsize) > max_width:
                factor = min(factor, max(max_width, 1) / self._text_width(textsize))
            if max_height is not None and textsize[3] > max_height:
                factor = min(factor, max(max_height, 1) / textsize[3])
            if factor >= 1.0 or all(self._line_size(line) == 1 for line in self.text):
                break
            # At least a few percent per step, otherwise rounding can stall it
            self._text_scale *= min(factor, 0.98)
            bboxes, textsize = self._measure()
        return bboxes, textsize

    def _measure(self):
        """The lines laid out at the current scale and the box around them.
        Measured once per label for each set of line sizes: a layout does not
        depend on the room, and next to an image the text is fitted at
        several distances, which often comes to the same sizes."""
        key = (tuple(self._line_size(line) for line in self.text), self._text_key)
        if key not in self._layouts:
            bboxes = self._draw_text(None, [])
            self._layouts[key] = (bboxes, self._compute_bbox(bboxes))
        return self._layouts[key]

    def _compute_bbox(self, bboxes):
        # Edge case: No text
        if not bboxes:
            return (0, 0, 0, 0)
        # Iterate over right margins of multiple text lines and find the maximum
        # width needed to fit all lines of text. The left edge is that of the
        # line reaching furthest left, e.g. with a "j" in front, as the lines
        # are drawn from it.
        max_width = max(bbox[0][2] for bbox in bboxes)
        return (min(bbox[0][0] for bbox in bboxes), bboxes[0][0][1], max_width, bboxes[-1][0][3])

    def _get_font(self, font_path: str, size: int, measuring: bool = False) -> ImageFont.FreeTypeFont:
        """Get a font object, using cache for performance. Sizes measured to
        fit the text have a cache of their own."""
        try:
            return (_load_measured_font if measuring else _load_font)(font_path, int(size))
        except Exception as e:
            logger.error(f"Failed to load font '{font_path}' with size {size}: {e}")
            return ImageFont.load_default()


class _ImageCache:
    """The images used last, up to a number of pixels in all. Callers get
    the images kept and must not change them, they make new ones from them,
    e.g. with resize()."""

    def __init__(self, max_pixels: int):
        self._max_pixels = max_pixels
        self._images: "collections.OrderedDict[tuple, Image.Image]" = collections.OrderedDict()
        self._pixels = 0
        self._lock = threading.Lock()

    def get(self, key: tuple, make) -> Image.Image:
        with self._lock:
            image = self._images.get(key)
            if image is not None:
                self._images.move_to_end(key)
                return image
        image = make()
        # One larger than all that is kept is not kept
        if image.width * image.height > self._max_pixels:
            return image
        with self._lock:
            if key not in self._images:
                self._images[key] = image
                self._pixels += image.width * image.height
            while self._pixels > self._max_pixels:
                _, dropped = self._images.popitem(last=False)
                self._pixels -= dropped.width * dropped.height
        return image


# While a label is edited it is previewed with every change, its code mostly
# stays the same. About ten codes of a usual size.
_CODES = _ImageCache(8_000_000)


def _qr_image(data: str, correction: int, box_size: int, fill_color) -> Image.Image:
    def make():
        qr = QRCode(version=1, error_correction=correction, box_size=box_size, border=0)
        qr.add_data(data.encode("utf-8"))
        qr.make(fit=True)
        check_image_size(qr.modules_count * box_size, qr.modules_count * box_size)
        return qr.make_image(fill_color=fill_color, back_color="white").get_image()
    return _CODES.get(('qr', data, correction, box_size, fill_color), make)


def _barcode_image(barcode_class, barcode_type: str, value: str, color: Tuple[int, int, int] = (0, 0, 0)) -> Image.Image:
    def make():
        return barcode_class(value, writer=ImageWriter()).render(writer_options={'foreground': '#%02x%02x%02x' % color})
    return _CODES.get(('barcode', barcode_type, value, color), make)


# Bounded, requests can ask for any number of sizes
@functools.lru_cache(maxsize=256)
def _load_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    return _open_font(font_path, size)


def _open_font(font_path: str, size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path, size)


# The sizes tried while text is fitted to the label, so that they do not
# push out those of the labels drawn
_load_measured_font = functools.lru_cache(maxsize=128)(_open_font)
