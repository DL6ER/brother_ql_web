"""The messages of refused requests, as they are sent to the browser.

A refused request is answered with one of the fixed messages below, never
with the text of the error: that could show paths, values of the request or
other details of the server. The error itself, with all details, goes to
the log of the server.
"""
import json
import re

from flask import current_app, has_app_context

from barcode.errors import BarcodeError, BarcodeNotFoundError, IllegalCharacterError, NumberOfDigitsError, WrongCountryCodeError
from pdf2image.exceptions import PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError, PDFSyntaxError
from PIL import Image
from qrcode.exceptions import DataOverflowError

from .label import MAX_BARCODE_LENGTH, MAX_IMAGE_SCALING_FACTOR, MAX_RANDOM_LENGTH, TextTooLong, check_digit_problems
from .label_names import label_size_problems
from .printer import MODELS_WITHOUT_600_DPI, resolution_problem

IMAGE_MODE_REFUSED = 'image_mode must be grayscale, black_and_white, red_and_black or colored'
# A detected printer whose status names no model brother_ql knows
UNKNOWN_MODEL = 'The printer does not report a model this server knows. Name its model in PRINTERS, e.g. QL-700=file:///dev/usb/lp0'
BLANKS_MESSAGE = 'blanks must be a JSON object with a text or number for each blank, e.g. {"Room": "Garage"}'
FIELDS_MESSAGE = ('fields must be a JSON object with the settings of each blank: a type of text, number or date, '
                  'a format as text and a default as text or number, e.g. {"Frozen on": {"type": "date"}}')

# Errors whose text is sent as it is
MESSAGES = (
    'Unsupported file type',
    'Cannot read the page size of the PDF',
    'Unknown label_size',
    'Font size is required',
    'Font size must be at least 1',
    'Text is too long',
    'The date format of a blank is too long',
    'shrink_text must be 0 or 1',
    'text_fit must be none, shrink or fill',
    'image_text_gap must be a whole number of dots or empty',
    'image_text_gap must not be negative',
    BLANKS_MESSAGE,
    FIELDS_MESSAGE,
    'image_position must be above, below, left or right',
    'Width and height must be non-negative.',
    'Border thickness must be non-negative.',
    'QR size must be positive.',
    'Image scaling factor must be > 0.',
    f'Image scaling factor must not exceed {MAX_IMAGE_SCALING_FACTOR:g}.',
    'Image rotation must be between 0 and 360 inclusive.',
    'The distance between image and text must be non-negative.',
    f'Random text length must not exceed {MAX_RANDOM_LENGTH}',
    'Invalid border rectangle',
    'The barcode is empty',
    f'Barcode value must not exceed {MAX_BARCODE_LENGTH} characters',
    'The code is empty until its blanks are filled in.',
    'The QR code is empty',
    IMAGE_MODE_REFUSED,
    UNKNOWN_MODEL,
    'print_count must be greater than 0',
    *check_digit_problems(),
    *(resolution_problem(model, True) for model in MODELS_WITHOUT_600_DPI),
    *label_size_problems(),
)

# Errors whose text names details, by their text. They may span lines, a
# request may have a line break e.g. at the end of a value.
PATTERNS = (
    (re.compile(r'(?s)Label is too large \(.*\)'), 'Label is too large'),
    (re.compile(r'(?s)Unsupported alignment: .*'), 'Unsupported alignment'),
    (re.compile(r'(?s).* is not a whole number'), 'A setting is not a whole number'),
    (re.compile(r'(?s)Unsupported printer: .*'), 'Unsupported printer'),
    (re.compile(r'(?s)Unknown printer model: .*'), 'Unknown printer model'),
    (re.compile(r'(?s)Invalid file name: .*'), 'Invalid file name'),
    (re.compile(r'(?s)Font must be given as "<family>,<style>": .*'), 'Font must be given as "<family>,<style>"'),
    (re.compile(r'(?s)Unknown font (family|style): .*'), 'Unknown font'),
    (re.compile(r'(?s)Unknown label size: .*'), 'Unknown label size'),
    (re.compile(r'(?s)The label size .* cannot be printed on a .*'), 'The label size cannot be printed on this printer'),
    (re.compile(r'(?s)".*" is not a page selection, give pages like 1-3, 5 or all'),
     'Not a page selection, give pages like 1-3, 5 or all'),
    (re.compile(r'(?s)The PDF has .*, there is no page .*'), 'The PDF has no such page'),
)

# Errors by their kind, the first that applies
KINDS = (
    (TextTooLong, 'Text is too long'),
    (NumberOfDigitsError, 'The barcode has the wrong number of digits for its type'),
    (IllegalCharacterError, 'The barcode has characters its type does not allow'),
    (WrongCountryCodeError, 'The barcode has a country code its type does not allow'),
    (BarcodeNotFoundError, 'Unknown barcode type'),
    (BarcodeError, 'The barcode cannot be made of this value'),
    (DataOverflowError, 'The QR code holds too much text'),
    (json.JSONDecodeError, 'The text lines are no valid JSON'),
    (Image.DecompressionBombError, 'The image is too large'),
    ((PDFInfoNotInstalledError, PDFPageCountError, PDFPopplerTimeoutError, PDFSyntaxError), 'The PDF cannot be read'),
    (OSError, 'The image cannot be read'),
)

INVALID_INPUT = 'The settings of the label are invalid, the log of the server names the reason'
# For any other error
PREVIEW_FAILED = 'The preview could not be created'
PRINT_FAILED = 'The label could not be printed, the log of the server names the reason'


def is_invalid_input(error: Exception) -> bool:
    """Whether error is raised for invalid input like a missing font size or
    an unknown font. KeyError and IndexError, though LookupErrors, come from
    errors in the code."""
    return isinstance(error, (ValueError, LookupError)) and not isinstance(error, (KeyError, IndexError))


def _limits() -> tuple:
    """The fixed messages of the limits the server is configured with, and
    the patterns of the errors that tell them."""
    if not has_app_context():
        return (), ()
    max_count = current_app.config['PRINT_MAX_COUNT']
    return ((f'print_count must not exceed {max_count}',),
            ((re.compile(r'(?s)A print job must not have more than \d+ labels, .*'),
              f'A print job must not have more than {max_count} labels'),))


def message(error: Exception, fallback: str) -> str:
    """The message for a request refused with error: one of the fixed ones,
    invalid input that has none INVALID_INPUT, any other error fallback."""
    text = str(error)
    limits, limit_patterns = _limits()
    for known in MESSAGES + limits:
        if text == known:
            return known
    for pattern, known in PATTERNS + limit_patterns:
        if pattern.fullmatch(text):
            return known
    for kind, known in KINDS:
        if isinstance(error, kind):
            return known
    if is_invalid_input(error):
        return INVALID_INPUT
    return fallback
