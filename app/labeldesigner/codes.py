"""All codes of the labels: QR and the other 2D codes (DataMatrix, Micro QR,
rMQR, Aztec, PDF417), each from a library of its own, and python-barcode's
barcodes, with their error correction, check digits and refusals."""
import math

import aztec_code_generator
import barcode
import segno
from barcode.writer import ImageWriter
from pdf417gen import encode as pdf417_encode
from pdf417gen.compaction import compact as pdf417_compact
from pdf417gen.rendering import barcode_size as pdf417_size, modules as pdf417_modules
from PIL import Image, ImageOps
from ppf.datamatrix import DataMatrix
from qrcode import QRCode, constants
from qrcode.exceptions import DataOverflowError
from rmqrcode import DataTooLongError, ErrorCorrectionLevel, FitStrategy, rMQR

# The 2D codes by their barcode_type, with their names, QR codes first
NAMES = {'QR': 'QR', 'DATAMATRIX': 'DataMatrix', 'MICROQR': 'Micro QR', 'RMQR': 'rMQR', 'AZTEC': 'Aztec', 'PDF417': 'PDF417'}
# The levels of error correction of the codes that have a choice, the first
# one is used where none is given. The other codes choose it themselves.
CORRECTIONS = {'QR': ('L', 'M', 'Q', 'H'), 'MICROQR': ('L', 'M', 'Q'), 'RMQR': ('M', 'H')}
QR_LEVELS = {'L': constants.ERROR_CORRECT_L, 'M': constants.ERROR_CORRECT_M, 'Q': constants.ERROR_CORRECT_Q, 'H': constants.ERROR_CORRECT_H}
# All types of codes, the 2D codes first, as /api/barcodes lists them
TYPES = [*NAMES, *(code.upper() for code in barcode.PROVIDED_BARCODES)]
# Barcode values are rendered before the label size is known, a long value
# would create a huge image. 1000 characters are far more than a label holds.
MAX_BARCODE_LENGTH = 1000
BARCODE_TOO_LONG = f"Barcode value must not exceed {MAX_BARCODE_LENGTH} characters"
# PDF417 codes have 3 to 90 rows of up to 30 columns, 6 where they can
PDF417_ROWS = (3, 90)
PDF417_COLUMNS = 6


def correction(code_type: str, value) -> str | None:
    """The level of error correction of a code: as given, else its first level;
    None for codes without a choice. ValueError for a level it does not have."""
    levels = CORRECTIONS.get(code_type)
    if levels is None:
        return None
    if value is None or value == '':
        return levels[0]
    if value not in levels:
        raise ValueError(_correction_problem(code_type))
    return value


def _correction_problem(code_type: str) -> str:
    levels = CORRECTIONS[code_type]
    return f"qrcode_correction must be {', '.join(levels[:-1])} or {levels[-1]} for {NAMES[code_type]} codes"


def _too_long_problem(code_type: str) -> str:
    return f"The {NAMES[code_type]} code holds too much text"


def empty_problem(code_type: str) -> str:
    return f"The {NAMES[code_type]} code is empty"


DATAMATRIX_ASCII_ONLY = "DataMatrix codes can hold only ASCII characters, no umlauts or accents"


def code_problems() -> list[str]:
    """Every message a request for a 2D code may be refused with."""
    return [*(_correction_problem(code_type) for code_type in CORRECTIONS),
            *(_too_long_problem(code_type) for code_type in NAMES),
            *(empty_problem(code_type) for code_type in NAMES),
            DATAMATRIX_ASCII_ONLY]


def matrix(code_type: str, data: str, level: str | None) -> tuple[list, int]:
    """The dots of a 2D code, rows of 1 (dark) and 0, and how many dots of
    its width a row is high. ValueError where the code cannot hold the
    data."""
    too_long = ValueError(_too_long_problem(code_type))
    if code_type == 'QR':
        qr = QRCode(version=1, error_correction=QR_LEVELS[level], border=0)
        qr.add_data(data.encode('utf-8'))
        try:
            qr.make(fit=True)
        except (DataOverflowError, ValueError):
            # Beyond the largest version it is a ValueError
            raise too_long from None
        return [[1 if dot else 0 for dot in row] for row in qr.modules], 1
    if code_type == 'DATAMATRIX':
        # The library encodes ASCII only, not the extended ASCII of the
        # standard or other characters
        if not data.isascii():
            raise ValueError(DATAMATRIX_ASCII_ONLY)
        try:
            return DataMatrix(data).matrix, 1
        except ValueError:
            raise too_long from None
    if code_type == 'MICROQR':
        try:
            code = segno.make_micro(data, error=level, boost_error=False)
        except segno.DataOverflowError:
            raise too_long from None
        return [list(row) for row in code.matrix], 1
    if code_type == 'RMQR':
        try:
            code = rMQR.fit(data, ecc=getattr(ErrorCorrectionLevel, level), fit_strategy=FitStrategy.MINIMIZE_HEIGHT)
        except DataTooLongError:
            raise too_long from None
        return code.to_list(with_quiet_zone=False), 1
    if code_type == 'AZTEC':
        # Text is read as ISO 8859-1 where the code does not say otherwise,
        # it says so for other text
        try:
            data.encode('iso8859-1')
            encoding = None
        except UnicodeEncodeError:
            encoding = 'utf-8'
        try:
            code = aztec_code_generator.AztecCode(data, encoding=encoding)
        except Exception as e:
            # Raised as a plain Exception
            if 'too big' in str(e):
                raise too_long from None
            raise
        return [list(row) for row in code.matrix], 1
    if code_type == 'PDF417':
        return _pdf417(data, too_long), 3
    raise ValueError(f"Unknown code type {code_type}")


def _pdf417(data: str, too_long: ValueError) -> list:
    # The level of error correction ISO 15438 recommends for the number of
    # code words of the data
    words = len(list(pdf417_compact(data.encode('utf-8'))))
    level = 2 if words <= 40 else 3 if words <= 160 else 4 if words <= 320 else 5
    # With the length and the error correction, in as many columns as
    # possible near the usual ones
    total = words + 2 ** (level + 1) + 1
    fitting = [columns for columns in range(1, 31) if PDF417_ROWS[0] <= math.ceil(total / columns) <= PDF417_ROWS[1]]
    if not fitting:
        raise too_long
    try:
        codes = pdf417_encode(data, columns=min(fitting, key=lambda columns: abs(columns - PDF417_COLUMNS)), security_level=level)
    except ValueError:
        # More code words than a code holds
        raise too_long from None
    width, height = pdf417_size(codes)
    rows = [[0] * width for _ in range(height)]
    for x, y in pdf417_modules(codes):
        rows[y][x] = 1
    return rows


def image(rows: list, ratio: int, box_size: int, fill_color: str) -> Image.Image:
    """The dots of a 2D code as an image, each box_size wide and ratio times as
    high, in fill_color on white. Black codes have one bit per pixel, so their
    dots stay sharp when scaled to the label."""
    width, height = len(rows[0]), len(rows)
    dots = Image.new('L', (width, height), 255)
    dots.putdata([0 if dot else 255 for row in rows for dot in row])
    dots = dots.resize((width * box_size, height * box_size * ratio), Image.Resampling.NEAREST)
    if fill_color == 'black':
        return dots.convert('1', dither=Image.Dither.NONE)
    return ImageOps.colorize(dots, black=fill_color, white='white')


def barcode_class(barcode_type: str):
    """The python-barcode class of a barcode type, BarcodeNotFoundError for an
    unknown one."""
    return barcode.get_barcode_class(barcode_type)


def barcode_image(kind, value: str) -> Image.Image:
    """The image of a barcode of the python-barcode class kind."""
    return kind(value, writer=ImageWriter()).render()


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
# without it, how it is calculated and whether hyphens may separate them.
# python-barcode ignores what follows the digits it needs, so it is checked here.
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
