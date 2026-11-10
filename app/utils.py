# -*- coding: utf-8 -*-

import re
from PIL import Image, ImageChops
from io import BytesIO
from typing import BinaryIO
from PIL.ImageOps import colorize
from flask import current_app
from pdf2image import convert_from_bytes, pdfinfo_from_bytes
from werkzeug.datastructures import FileStorage
from app import get_fonts
from app.labeldesigner.label import check_image_size, on_white


def convert_image_to_bw(image: Image.Image, threshold: int) -> Image.Image:
    def apply_threshold(pixel: int) -> int:
        return 255 if pixel > threshold else 0
    # convert to black and white
    return on_white(image).convert('L').point(apply_threshold, mode='1')


def convert_image_to_grayscale(image: Image.Image) -> Image.Image:
    # convert to grayscale (ITU-R 601-2 Luma transform)
    return on_white(image).convert('L')


def convert_image_to_red_and_black(image: Image.Image,
                                   blackpoint: int = 0,
                                   whitepoint: int = 255,
                                   redpoint: int = 127) -> Image.Image:
    return colorize(on_white(image).convert('L'), black='black', white='white', mid='red',
                    blackpoint=blackpoint, whitepoint=whitepoint, midpoint=redpoint)


# Stronger contrast for drawings with light washes, which print faint on ZINK
# paper: luma to the power of 2.2, the darkest 0.5 % (at most luma 50) black,
# color differences 1.5 times. Picked on the VC-500W from four printed steps.
# From luma 225 up it eases back to the image, paper keeps its grain unchanged.
CONTRAST_GAMMA = 2.2
CONTRAST_KNEE = 225
CONTRAST_BLACK_SHARE = 0.005
CONTRAST_BLACK_MAX = 50
CONTRAST_CHROMA = 1.5
# Faint: the picture, without its white background, has its middle luma at
# least this light and less than this share darker than luma 64
FAINT_MEDIAN = 200
FAINT_DARK_SHARE = 0.02


def stronger_contrast(image: Image.Image) -> Image.Image:
    """image with darker middle tones, the darkest bits black and stronger
    colors. White, black and transparency stay."""
    alpha = image.convert('RGBA').getchannel('A') if image.has_transparency_data else None
    y, cb, cr = image.convert('RGB').convert('YCbCr').split()
    # How much of the image stays, 0 below the knee, 1 at white
    keep = [(lambda t: t * t * (3 - 2 * t))(max(0, (v - CONTRAST_KNEE) / (255 - CONTRAST_KNEE))) for v in range(256)]
    kept = y.point([round(255 * k) for k in keep])
    curve = [255 * (v / 255) ** CONTRAST_GAMMA for v in range(256)]
    black = min(_percentile(y.point([round(c) for c in curve]).histogram(alpha), CONTRAST_BLACK_SHARE), CONTRAST_BLACK_MAX)
    y = y.point([round(max(0, c - black) * 255 / (255 - black) * (1 - k) + v * k) for v, c, k in zip(range(256), curve, keep)])
    chroma = [min(255, max(0, round(128 + (v - 128) * CONTRAST_CHROMA))) for v in range(256)]
    cb, cr = (Image.composite(c, c.point(chroma), kept) for c in (cb, cr))
    out = Image.merge('YCbCr', (y, cb, cr)).convert('RGB')
    if alpha is not None:
        out.putalpha(alpha)
    return out


def looks_faint(image: Image.Image) -> bool:
    """Whether the image is light all over, like a drawing in light washes,
    and would print better with stronger_contrast(). Photos are not."""
    small = on_white(image).convert('RGB')
    small.thumbnail((256, 256))
    y, cb, cr = small.convert('YCbCr').split()
    # The content: what is not white paper, darker or with some color
    tinted = [channel.point(lambda v: 255 if abs(v - 128) > 4 else 0) for channel in (cb, cr)]
    content = ImageChops.lighter(y.point(lambda v: 255 if v <= 245 else 0), ImageChops.lighter(*tinted))
    histogram = y.histogram(content)
    pixels = sum(histogram)
    if pixels < 0.05 * small.width * small.height:
        return False
    return _percentile(histogram, 0.5) >= FAINT_MEDIAN and sum(histogram[:64]) < FAINT_DARK_SHARE * pixels


def _percentile(histogram: list[int], share: float) -> int:
    """The value below which share of the counted pixels lie."""
    wanted, seen = share * sum(histogram), 0
    for value, count in enumerate(histogram):
        seen += count
        if seen >= wanted:
            return value
    return len(histogram) - 1


def read_file(file: FileStorage | BinaryIO) -> bytes:
    """Read the whole file. Starts at the beginning so that the same upload
    can be read again, e.g. once for every label of a print job."""
    stream = file.stream if isinstance(file, FileStorage) else file
    stream.seek(0)
    return stream.read()


def imgfile_to_image(file: FileStorage | BinaryIO) -> Image.Image:
    return Image.open(BytesIO(read_file(file)))


class PageSelectionError(ValueError):
    """A selection of PDF pages that cannot be printed."""


def pdf_page_count(file: FileStorage | BinaryIO) -> int:
    return int(pdfinfo_from_bytes(read_file(file))['Pages'])


def selected_pdf_pages(selection: str, page_count: int) -> list[int]:
    """The pages a selection like "1-3, 5", "4-" or "all" names, in its
    order. Without a selection the first page, as before pages could be
    chosen."""
    selection = (selection or '').strip().lower()
    if not selection:
        return [1]
    if selection == 'all':
        return list(range(1, page_count + 1))
    pages = []
    for part in (part.strip() for part in selection.split(',')):
        if not part:
            continue
        match = re.fullmatch(r'(\d+)\s*(-\s*(\d*))?', part)
        if not match or (match.group(3) and int(match.group(3)) < int(match.group(1))):
            raise PageSelectionError(f'"{part}" is not a page selection, give pages like 1-3, 5 or all')
        first = int(match.group(1))
        last = first if match.group(2) is None else int(match.group(3) or page_count)
        if first < 1 or last > page_count:
            pages_of_pdf = f'{page_count} page' + ('s' if page_count != 1 else '')
            raise PageSelectionError(f'The PDF has {pages_of_pdf}, there is no page {last if first >= 1 else first}')
        pages.extend(range(first, last + 1))
    if not pages:
        raise PageSelectionError(f'"{selection}" is not a page selection, give pages like 1-3, 5 or all')
    return pages


def pdffile_to_image(file: FileStorage | BinaryIO, dpi: int, page: int = 1) -> Image.Image:
    data = read_file(file)
    # Check the page size before rendering. Pillow's pixel limit only applies
    # after pdftoppm rendered the whole page into memory, and a tiny PDF can
    # declare a huge page.
    info = pdfinfo_from_bytes(data, first_page=page, last_page=page)
    size = next((value for key, value in info.items() if re.fullmatch(r'Page\s+\d+ size', key)), info.get('Page size', ''))
    match = re.match(r'([\d.]+) x ([\d.]+)', str(size))
    if not match:
        raise ValueError("Cannot read the page size of the PDF")
    width_pt, height_pt = (float(value) for value in match.groups())
    check_image_size(width_pt / 72 * dpi, height_pt / 72 * dpi)
    # One page at a time, rendering all pages could exhaust the memory
    return convert_from_bytes(data, dpi=dpi, first_page=page, last_page=page)[0]


def image_to_png_bytes(im: Image.Image, gray: bool = False) -> bytes:
    """The image as PNG. With gray, for a label without red or color, it is
    stored in gray: the same pixels in half the bytes, made in half the time."""
    if gray and im.mode == 'RGB':
        im = im.convert('L')
    image_buffer = BytesIO()
    im.save(image_buffer, format="PNG")
    return image_buffer.getvalue()


def fill_first_line_fields(text, data: dict):
    fonts = get_fonts()
    # Restore zeroth line font settings or use defaults
    if len(text) > 0:
        data['font_size'] = str(text[0].get('size', current_app.config['LABEL_DEFAULT_FONT_SIZE']))
        data['font_inverted'] = True if text[0].get('inverted', 0) else False
        data['font'] = text[0].get('font', fonts.get_default_font()[0])
        data['font_align'] = text[0].get('align', 'left')
        data['font_checkbox'] = True if text[0].get('checkbox', 0) else False
        data['font_color'] = text[0].get('color', 'black')
        data['line_spacing'] = str(text[0].get('line_spacing', current_app.config['LABEL_DEFAULT_LINE_SPACING']))
    else:
        data['font_size'] = str(current_app.config['LABEL_DEFAULT_FONT_SIZE'])
        data['font_inverted'] = False
        data['font'] = fonts.get_default_font()[0]
        data['font_align'] = 'left'
        data['font_checkbox'] = False
        data['font_color'] = 'black'
        data['line_spacing'] = str(current_app.config['LABEL_DEFAULT_LINE_SPACING'])
    return data
