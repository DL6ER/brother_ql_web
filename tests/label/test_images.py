"""Images and PDFs on labels through the API: fit, crop, rotation, scaling,
colors and pages."""

import io
import json
import os
import re
from typing import Union

import pytest
from PIL import Image, ImageChops, ImageOps
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage

from app.labeldesigner.enums import LabelContent
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _pdf, _red_square, _upload, EXAMPLE_FORMDATA, make_client, UPDATE_IMAGES, verify_image


class TestLabelDesignerAPI:
    def run_image_test(self,
                       client: FlaskClient,
                       image_path: Union[str, None] = None,
                       rotated: bool = False,
                       fit: bool = False,
                       text: bool = False,
                       image_mode: str = "grayscale",
                       high_res: bool = False):

        data = EXAMPLE_FORMDATA.copy()
        if image_path is None:
            if high_res:
                image_path = "tests/fixtures/_demo_image_highres.jpg"
            else:
                image_path = "tests/fixtures/_demo_image.jpg"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/jpeg",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = image_mode
        data['high_res'] = 1 if high_res else 0

        if image_mode == "black_and_white":
            data['image_bw_threshold'] = '128'

        data['image_fit'] = '1' if fit else '0'
        data['orientation'] = 'rotated' if rotated else 'normal'
        if text:
            data['text'] = json.dumps([
                {
                    'font': 'DejaVu Sans,Book',
                    'text': 'Test',
                    'size': '40',
                    'align': 'center'
                }
            ])

        expected_img_path = "image" + ("_rotated" if rotated else "") + ("_fit" if fit else "") + ("_text" if text else "") + ("_highres" if high_res else "") + "_" + image_mode + ".png"

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, expected_img_path)

    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    def image_updating_is_disabled(self):
        assert not UPDATE_IMAGES

    @pytest.mark.parametrize('settings', [
        pytest.param({}, id='image'),
        pytest.param({'fit': True}, id='fit'),
        pytest.param({'rotated': True}, id='rotated'),
        pytest.param({'rotated': True, 'fit': True}, id='rotated_fit'),
        pytest.param({'text': True}, id='with_text'),
        pytest.param({'text': True, 'fit': True}, id='with_text_fit'),
        pytest.param({'text': True, 'rotated': True}, id='with_text_rotated'),
        pytest.param({'text': True, 'rotated': True, 'fit': True}, id='with_text_fit_rotated'),
        pytest.param({'image_mode': 'colored', 'fit': True}, id='color_fit'),
        pytest.param({'image_mode': 'red_and_black', 'fit': True}, id='red_and_black_fit'),
        pytest.param({'image_mode': 'black_and_white', 'fit': True}, id='black_fit'),
        pytest.param({'high_res': True}, id='highres'),
        pytest.param({'high_res': True, 'fit': True}, id='highres_fit'),
    ])
    def test_image(self, client: FlaskClient, settings):
        # Compared with the reference image of the settings
        self.run_image_test(client, **settings)

    @pytest.mark.parametrize('fit', [True, False])
    @pytest.mark.parametrize('rotation', [0, 90])
    def test_image_crop(self, client: FlaskClient, fit, rotation):
        data = EXAMPLE_FORMDATA.copy()
        image_path = f"tests/fixtures/_X.png"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '1' if fit else '0'
        data['image_crop'] = '1'
        data['image_rotation'] = rotation
        response = client.post('/labeldesigner/api/preview', data=data)

        # Should work
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, f'image_crop_{"fit" if fit else "nofit"}_{rotation}.png')

    def test_invalid_image_upload(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        fake_file = FileStorage(
            stream=io.BytesIO(b'not an image'),
            filename='fake.txt',
            content_type='text/plain',
        )
        data['print_type'] = 'image'
        data['image'] = fake_file
        data['image_mode'] = 'grayscale'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        data = response.get_json()
        assert 'message' in data
        assert data['message'] == 'Unsupported file type'

    def test_corrupted_image_upload(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        corrupted = FileStorage(
            stream=io.BytesIO(b'\x89PNG\r\n\x1a\nBADBADBAD'),
            filename='corrupt.png',
            content_type='image/png',
        )
        data['print_type'] = 'image'
        data['image'] = corrupted
        data['image_mode'] = 'grayscale'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        data = response.get_json()
        assert 'message' in data
        assert data['message'] == 'The image cannot be read'

    def test_image_mode_edge_case(self, client: FlaskClient):
        # An unknown mode was printed in black and white. bw, as the webhook
        # was described, is black and white.
        def preview(mode):
            data = EXAMPLE_FORMDATA.copy()
            data['print_type'] = 'image'
            data['image'] = _upload(_red_square(), 'red.png')
            data['image_mode'] = mode
            return client.post('/labeldesigner/api/preview', data=data)
        response = preview('not_a_mode')
        assert response.status_code == 400
        assert response.get_json()['message'] == 'image_mode must be grayscale, black_and_white, red_and_black or colored'
        assert preview('bw').data == preview('black_and_white').data

    @pytest.mark.parametrize('factor', [0, 1, 10, 100, 150])
    @pytest.mark.parametrize('sign', [1, -1])
    def test_image_manual_scaling(self, client, factor, sign):
        SCALING = factor * sign
        data = EXAMPLE_FORMDATA.copy()
        image_path = "tests/fixtures/_demo_image_simple.png"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '0'
        data['image_scaling_factor'] = SCALING
        response = client.post('/labeldesigner/api/preview', data=data)

        if SCALING > 0:
            # Should work
            assert response.status_code == 200
            assert response.content_type in ['image/png']

            # Check image
            verify_image(response.data, f'image_manual_scaling_{SCALING}.png')
        else:
            # Should be rejected
            assert response.status_code == 400
            assert response.is_json
            data = response.get_json()
            assert data['message'] == 'Image scaling factor must be > 0.'

    @pytest.mark.parametrize('rotation', [-10, -1, 0, 1, 10, 45, 90, 180, 270, 360, 450])
    def test_image_rotation(self, client: FlaskClient, rotation):
        data = EXAMPLE_FORMDATA.copy()
        image_path = "tests/fixtures/_demo_image_simple.png"
        my_file = FileStorage(
            stream=open(image_path, "rb"),
            filename=os.path.basename(image_path),
            content_type="image/png",
        )
        data['print_type'] = 'image'
        data['image'] = my_file
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '1'
        data['image_rotation'] = rotation
        response = client.post('/labeldesigner/api/preview', data=data)

        if rotation >= 0 and rotation <= 360:
            # Should work
            assert response.status_code == 200
            assert response.content_type in ['image/png']
            verify_image(response.data, f'image_rotation_{rotation}.png')
        else:
            # Should be rejected
            assert response.status_code == 400
            assert response.is_json
            data = response.get_json()
            assert data['message'] == 'Image rotation must be between 0 and 360 inclusive.'

    @pytest.mark.parametrize('fit', ['fit', 'no_fit'])
    @pytest.mark.parametrize('orientation', ['standard', 'rotated'])
    @pytest.mark.parametrize('label_size', ["12", "62", "62x29", "62x100", "d12"])
    def test_print_pdf_all_permutations(self, client: FlaskClient, label_size, orientation, fit):
        """PDF printing for endless and die-cut labels.

        Uses tests/images/TestPrint.pdf and checks both orientations
        and image-fit enabled/disabled.
        """
        pdf_path = 'tests/images/TestPrint.pdf'
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'image'
        data['label_size'] = label_size
        data['orientation'] = orientation
        data['image_mode'] = 'grayscale'
        data['image_fit'] = '1' if fit == 'fit' else '0'

        my_file = FileStorage(
            stream=open(pdf_path, 'rb'),
            filename=os.path.basename(pdf_path),
            content_type='application/pdf',
        )
        data['image'] = my_file

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']
        ls = label_size.replace(' ', '-').lower()
        rot = 'rotated' if orientation == 'rotated' else 'standard'
        verify_image(response.data, f'pdf_printing_{ls}_{rot}_{fit}.png')


def test_image_scaling_factor_is_limited(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image_fit': '0', 'image_scaling_factor': '501',
                 'image': _upload(Image.new('RGB', (100, 100), 'black'), 'black.png')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Image scaling factor must not exceed 500.'


def test_image_fit_never_scales_to_zero_pixels(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image_fit': '1',
                 'image': _upload(Image.new('RGB', (5000, 3), 'black'), 'wide.png')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200


@pytest.mark.parametrize('filename, save_format', [('image.png', 'PNG'), ('image.pdf', 'PDF')])
def test_print_uploaded_image_several_times(tmp_path, filename, save_format):
    client = make_client(tmp_path)
    buffer = io.BytesIO()
    _red_square().save(buffer, save_format)
    buffer.seek(0)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'print_count': '3', 'image': FileStorage(stream=buffer, filename=filename)})
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 200
    assert response.get_json() == {'success': True}


def test_red_and_black_image_mode(tmp_path):
    client = make_client(tmp_path)
    with client.application.test_request_context():
        label = create_label_from_request({'label_size': '62red', 'print_type': 'image', 'image_mode': 'red_and_black'})
    assert label.label_content == LabelContent.IMAGE_RED_BLACK


@pytest.mark.parametrize('image_mode', ['red_and_black', 'colored'])
def test_pdf_upload_keeps_red(tmp_path, image_mode):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': '62red', 'print_type': 'image', 'image_mode': image_mode, 'image_fit': '1',
                 'image': _upload(_red_square(), 'red.pdf')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    red, green, _ = Image.open(io.BytesIO(response.data)).convert('RGB').split()
    assert ImageChops.subtract(red, green).getextrema()[1] > 60


def test_pdf_upload_uses_high_res_dpi(tmp_path):
    client = make_client(tmp_path)
    widths = []
    for high_res in ('0', '1'):
        data = EXAMPLE_FORMDATA.copy()
        data.update({'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0', 'border_thickness': '0',
                     'high_res': high_res, 'image': _upload(_red_square(), 'red.pdf')})
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        bbox = ImageOps.invert(Image.open(io.BytesIO(response.data)).convert('L')).getbbox()
        widths.append(bbox[2] - bbox[0])
    # Same physical size, so twice the pixels at 600 dpi
    assert widths[1] >= 1.9 * widths[0]


def test_uploaded_image_is_decoded_once_per_job(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    calls = []
    original = routes.open_images
    monkeypatch.setattr(routes, 'open_images', lambda *args: calls.append(args) or original(*args))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'print_count': '3', 'image': _upload(_red_square(), 'red.png')})
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.get_json() == {'success': True}
    assert len(calls) == 1


def test_pdf_upload_renders_first_page_only(tmp_path, monkeypatch):
    import app.utils
    calls = []
    original = app.utils.convert_from_bytes
    monkeypatch.setattr(app.utils, 'convert_from_bytes', lambda data, **kwargs: calls.append(kwargs) or original(data, **kwargs))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image': _upload(_red_square(), 'red.pdf')})
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert calls[0]['first_page'] == 1 and calls[0]['last_page'] == 1


@pytest.mark.parametrize('size, extra', [
    # Refused when decoding
    ((8200, 8000), {}),
    # Small enough, but not when rotated by 45 degrees
    ((16000, 2000), {'image_rotation': '45', 'image_fit': '0'}),
])
def test_large_images_are_refused(tmp_path, size, extra):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image': _upload(Image.new('L', size, 'white'), 'large.png'), **extra})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    message = response.get_json()['message']
    assert message in ('The image is too large', 'Label is too large')


def test_pdf_with_huge_page_is_refused_before_rendering(tmp_path, monkeypatch):
    import app.utils
    monkeypatch.setattr(app.utils, 'convert_from_bytes', lambda *args, **kwargs: pytest.fail('PDF was rendered'))
    client = make_client(tmp_path)
    buffer = io.BytesIO()
    # 200 pixels at 1 dpi declare a page of 200 x 200 inches
    Image.new('L', (200, 200), 'white').save(buffer, 'PDF', resolution=1)
    buffer.seek(0)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image': FileStorage(stream=buffer, filename='huge.pdf')})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'].startswith('Label is too large')


THREE_PAGES = ((300, 200), (200, 300), (400, 100))


@pytest.mark.parametrize('selection, pages', [
    ('', [1]), ('all', [1, 2, 3, 4]), ('1-3, 4', [1, 2, 3, 4]), ('2-', [2, 3, 4]), ('3,1', [3, 1]), (' 2 - 3 ,', [2, 3]),
])
def test_selected_pdf_pages(selection, pages):
    from app.utils import selected_pdf_pages
    assert selected_pdf_pages(selection, 4) == pages


@pytest.mark.parametrize('selection, message', [
    ('5', 'The PDF has 4 pages, there is no page 5'),
    ('2-7', 'The PDF has 4 pages, there is no page 7'),
    ('0', 'The PDF has 4 pages, there is no page 0'),
    ('3-1', '"3-1" is not a page selection, give pages like 1-3, 5 or all'),
    ('first', '"first" is not a page selection, give pages like 1-3, 5 or all'),
    (',', '"," is not a page selection, give pages like 1-3, 5 or all'),
])
def test_invalid_pdf_page_selection(selection, message):
    from app.utils import selected_pdf_pages
    with pytest.raises(ValueError, match=re.escape(message)):
        selected_pdf_pages(selection, 4)


def _pdf_label(**values):
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'image_fit': '1', 'border_thickness': '0', 'text': '[]', **values}
    data['image'] = (io.BytesIO(_pdf(*THREE_PAGES)), 'pages.pdf', 'application/pdf')
    return data


def test_preview_shows_the_first_selected_pdf_page(tmp_path):
    client = make_client(tmp_path)
    previews = {}
    for selection in ('', '2', '3-', 'all'):
        response = client.post('/labeldesigner/api/preview', data=_pdf_label(pdf_pages=selection))
        assert response.status_code == 200
        assert response.headers['X-PDF-Pages'] == '3'
        previews[selection] = Image.open(io.BytesIO(response.data)).size
    # The pages are fitted to the label width, their heights differ
    assert previews[''] == previews['all'] != previews['2'] != previews['3-']
    assert previews['2'][1] > previews[''][1] > previews['3-'][1]


def test_each_selected_pdf_page_is_printed_as_a_label(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data=_pdf_label(pdf_pages='all', print_count='2', printer='simulation'))
    assert response.status_code == 200, response.get_json()
    files = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    heights = [Image.open(os.path.join(printer.SIMULATED_LABELS_DIR, name)).height for name in files]
    # Collated: all pages, then all pages again
    assert len(heights) == 6
    assert heights[:3] == heights[3:]
    assert heights[1] > heights[0] > heights[2]


def test_pdf_pages_count_against_the_print_limit(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data=_pdf_label(pdf_pages='all', print_count='40', printer='simulation'))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'A print job must not have more than 100 labels'


def test_pdf_page_selection_errors_are_reported(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=_pdf_label(pdf_pages='4'))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The PDF has no such page'


def test_pdf_pages_make_several_labels_of_image_labels_only(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    data = _pdf_label(pdf_pages='all', printer='simulation', print_type='text',
                      text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Text', 'size': '40'}]))
    assert client.post('/labeldesigner/api/print', data=data).status_code == 200
    assert len(os.listdir(printer.SIMULATED_LABELS_DIR)) == 1
