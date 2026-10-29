# -*- coding: utf-8 -*-

from PIL import Image
from io import BytesIO
from typing import BinaryIO
from PIL.ImageOps import colorize
from flask import current_app
from pdf2image import convert_from_bytes
from werkzeug.datastructures import FileStorage
from app import get_fonts


def convert_image_to_bw(image: Image.Image, threshold: int) -> Image.Image:
    def apply_threshold(pixel: int) -> int:
        return 255 if pixel > threshold else 0
    # convert to black and white
    return image.convert('L').point(apply_threshold, mode='1')


def convert_image_to_grayscale(image: Image.Image) -> Image.Image:
    # convert to grayscale (ITU-R 601-2 Luma transform)
    return image.convert('L')


def convert_image_to_red_and_black(image: Image.Image,
                                   blackpoint: int = 0,
                                   whitepoint: int = 255,
                                   redpoint: int = 127) -> Image.Image:
    return colorize(image.convert('L'), black='black', white='white', mid='red',
                    blackpoint=blackpoint, whitepoint=whitepoint, midpoint=redpoint)


def read_file(file: FileStorage | BinaryIO) -> bytes:
    """Read the whole file. Starts at the beginning so that the same upload
    can be read again, e.g. once for every label of a print job."""
    stream = file.stream if isinstance(file, FileStorage) else file
    stream.seek(0)
    return stream.read()


def imgfile_to_image(file: FileStorage | BinaryIO) -> Image.Image:
    return Image.open(BytesIO(read_file(file)))


def pdffile_to_image(file: FileStorage | BinaryIO, dpi: int) -> Image.Image:
    # Only the first page is used, rendering all pages could exhaust the memory
    return convert_from_bytes(read_file(file), dpi=dpi, first_page=1, last_page=1)[0]


def image_to_png_bytes(im: Image.Image) -> bytes:
    image_buffer = BytesIO()
    im.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    return image_buffer.read()


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
