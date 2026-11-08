"""Helpers shared by the tests of the API: a client of the app, an example
label and the comparison with reference images."""

import io
import json
import os
from typing import Union

from PIL import Image
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage

from app import create_app


UPDATE_IMAGES = False


EXAMPLE_FORMDATA = {
    'print_type': 'text',
    'label_size': 62,
    'orientation': 'standard',
    'print_count': '1',
    'cut_once': '0',
    # Margins and border the reference images were created with
    'margin_top': '12',
    'margin_bottom': '12',
    'margin_left': '20',
    'margin_right': '20',
    'border_thickness': '1',
}


def pixels(data: bytes) -> tuple:
    with Image.open(io.BytesIO(data)) as image:
        return image.size, image.convert('RGB').tobytes()


def verify_image(response_data: bytes, expected_image_path: str):
    # Compare generated preview with the image in file (if it exists). The
    # pixels count, not how the PNG stores them.
    if not UPDATE_IMAGES and os.path.isfile('tests/images/' + expected_image_path):
        with open('tests/images/' + expected_image_path, 'rb') as f:
            expected_data = f.read()
        if pixels(response_data) != pixels(expected_data):
            # Save image for debugging purposes
            failed_image_path = 'tests/' + 'FAILED_' + expected_image_path
            with open(failed_image_path, 'wb') as f:
                f.write(response_data)
            raise AssertionError("Generated image does not match expected image")
        return

    # Write image into file (missing reference image or UPDATE_IMAGES)
    with open('tests/images/' + expected_image_path, 'wb') as f:
        f.write(response_data)


def make_client(tmp_path, empty_repo: bool = False, model: Union[str, None] = None) -> FlaskClient:
    app = create_app()
    # Bind app context
    app.app_context().push()
    app.config['TESTING'] = True
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
    if model:
        app.config['PRINTER_MODEL'] = model
    if empty_repo:
        # point repository to temporary folder
        repo_dir = tmp_path / 'label_repo'
        app.config['LABEL_REPOSITORY_DIR'] = str(repo_dir)
        os.makedirs(app.config['LABEL_REPOSITORY_DIR'], exist_ok=True)
    return app.test_client()


TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Test', 'size': '40', 'align': 'center'}])


def _upload(image: Image.Image, filename: str) -> FileStorage:
    buffer = io.BytesIO()
    if filename.endswith('.pdf'):
        image.save(buffer, 'PDF', resolution=150)
    else:
        image.save(buffer, 'PNG')
    buffer.seek(0)
    return FileStorage(stream=buffer, filename=filename)


def _red_square() -> Image.Image:
    image = Image.new('RGB', (300, 150), 'white')
    image.paste(Image.new('RGB', (200, 100), (255, 0, 0)), (50, 25))
    return image


def _image_and_text_boxes(png: bytes):
    """The boxes of a mid-gray image and of the black text on a label. The
    edges of the text have a few pixels of the same gray, they are left
    out."""
    from PIL import ImageFilter
    image = Image.open(io.BytesIO(png)).convert('L')
    gray = image.point(lambda v: 255 if v == 128 else 0).filter(ImageFilter.MinFilter(5)).getbbox()
    return (gray[0] - 2, gray[1] - 2, gray[2] + 2, gray[3] + 2), image.point(lambda v: 255 if v < 100 else 0).getbbox()


def _pdf(*sizes) -> bytes:
    """A PDF with a black page of each size, so the pages can be told apart
    by their aspect ratio."""
    pages = [Image.new('L', size, 'black') for size in sizes]
    pdf = io.BytesIO()
    pages[0].save(pdf, format='PDF', save_all=True, append_images=pages[1:], resolution=100)
    return pdf.getvalue()


def sent_raster(monkeypatch) -> list:
    """The raster data of every job printed on a Brother printer, also on the
    simulator."""
    from app.labeldesigner.drivers.brother import BrotherRaster
    sent = []
    render = BrotherRaster.render

    def capture(self, queue, entries, keep=None):
        job = render(self, queue, entries, keep)
        sent.append(job.data)
        return job
    monkeypatch.setattr(BrotherRaster, 'render', capture)
    return sent
