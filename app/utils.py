# -*- coding: utf-8 -*-

from PIL import Image
from io import BufferedReader, BytesIO
from PIL.ImageOps import colorize
from flask import current_app
from pdf2image import convert_from_bytes
from werkzeug.datastructures import FileStorage
from app import FONTS, init_fonts


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


def imgfile_to_image(file: FileStorage | BufferedReader) -> Image.Image:
    s = BytesIO()
    if isinstance(file, BufferedReader):
        s.write(file.read())
    else:
        file.save(s)
    im = Image.open(s)
    return im


def pdffile_to_image(file: FileStorage, dpi: int) -> Image.Image:
    s = BytesIO()
    file.save(s)
    s.seek(0)
    im = convert_from_bytes(s.read(), dpi=dpi)[0]
    return im


def image_to_png_bytes(im: Image.Image) -> bytes:
    image_buffer = BytesIO()
    im.save(image_buffer, format="PNG")
    image_buffer.seek(0)
    return image_buffer.read()


def fill_first_line_fields(text, data: dict):
    global FONTS
    if FONTS is None:
        FONTS = init_fonts(current_app)
    # Restore zeroth line font settings or use defaults
    if len(text) > 0:
        data['font_size'] = str(text[0].get('size', current_app.config['LABEL_DEFAULT_FONT_SIZE']))
        data['font_inverted'] = True if text[0].get('inverted', 0) else False
        data['font'] = text[0].get('font', FONTS.get_default_font()[0])
        data['font_align'] = text[0].get('align', 'left')
        data['font_checkbox'] = True if text[0].get('checkbox', 0) else False
        data['font_color'] = text[0].get('color', 'black')
        data['font_inverted'] = True if text[0].get('inverted', 0) else False
        data['line_spacing'] = str(text[0].get('line_spacing', current_app.config['LABEL_DEFAULT_LINE_SPACING']))
    else:
        data['font_size'] = str(current_app.config['LABEL_DEFAULT_FONT_SIZE'])
        data['font_inverted'] = False
        data['font'] = FONTS.get_default_font()[0]
        data['font_align'] = 'left'
        data['font_checkbox'] = False
        data['font_color'] = 'black'
        data['line_spacing'] = str(current_app.config['LABEL_DEFAULT_LINE_SPACING'])
    return data
