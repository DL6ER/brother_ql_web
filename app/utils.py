# -*- coding: utf-8 -*-

from PIL import Image
from io import BytesIO
from PIL.ImageOps import colorize
from pdf2image import convert_from_bytes
from werkzeug.datastructures import FileStorage


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


def imgfile_to_image(file: FileStorage) -> Image.Image:
    s = BytesIO()
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
