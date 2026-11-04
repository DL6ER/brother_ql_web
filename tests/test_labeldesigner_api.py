import io
import re
import os
import sys
import json
import base64
import random
import pytest
import unicodedata
from typing import Union
from urllib.parse import urlencode
from datetime import datetime
from flask.testing import FlaskClient
from PIL import Image, ImageChops, ImageOps
from werkzeug.datastructures import FileStorage
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app
from app.labeldesigner.enums import LabelContent, LabelOrientation
from app.labeldesigner.routes import create_label_from_request

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

        if image_mode == "black":
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

    def test_render_frontend(self, client: FlaskClient):
        response = client.get('/labeldesigner/')
        assert response.status_code == 200
        assert b'labeldesigner' in response.data
        assert response.content_type == 'text/html; charset=utf-8'

    def test_get_barcodes(self, client: FlaskClient):
        response = client.get('/labeldesigner/api/barcodes')
        assert response.status_code == 200
        assert response.is_json
        assert 'barcodes' in response.get_json()
        offered_barcodes = response.get_json()['barcodes']
        assert 'QR' in offered_barcodes
        assert 'EAN13' in offered_barcodes

    def test_generate_preview(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'Left',
                'size': '60',
                'align': 'left'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'simple.png')

    def test_generate_preview_high_res(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['high_res'] = 1
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'Left',
                'size': '60',
                'align': 'left'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'simple_high_res.png')

    def test_generate_preview_inverted(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! LEFT !!!',
                'size': '50',
                'align': 'left',
                'inverted': 'true'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! CENTER !!!',
                'size': '50',
                'align': 'center',
                'inverted': 'true'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '!!! RIGHT !!!',
                'size': '50',
                'align': 'right',
                'inverted': 'true'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'inverted_text.png')

    def test_generate_preview_rotated(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['orientation'] = 'rotated'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'Left',
                'size': '60',
                'align': 'left'
            },
            {
                'font': 'Droid Sans,Mono',
                'text': '-- LONG MONO TEXT --',
                'size': '50',
                'align': 'center'
            }
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'rotated.png')

    def test_generate_preview_no_margins(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['margin_top'] = 0
        data['margin_bottom'] = 0
        data['text'] = json.dumps([
            {
                'font': 'Droid Serif,Regular',
                'text': '!!! URGENT !!!',
                'size': '60',
                'align': 'center',
                'inverted': True
            },
            {
                'font': 'Droid Serif,Regular',
                'text': 'Ship as soon as possible',
                'size': '60',
                'align': 'center'
            }
        ])

    def test_generate_preview_no_margins_rotated(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['orientation'] = 'rotated'
        data['margin_left'] = 0
        data['margin_right'] = 0
        data['text'] = json.dumps([
            {
                'font': 'Droid Serif,Regular',
                'text': '!!! URGENT !!!',
                'size': '60',
                'align': 'center',
                'inverted': True
            },
            {
                'font': 'Droid Serif,Regular',
                'text': 'Ship as soon as possible',
                'size': '60',
                'align': 'center'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'simple_no_margins_rotated.png')

    def test_generate_ean13(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'ean13'
        data['text'] = json.dumps([
            {
                'font': 'Droid Serif,Bold',
                'text': '123456789012',
                'size': '50',
                'align': 'center'
            }
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'barcode_ean13.png')

    def test_invalid_ean13(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'ean13'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '1234567890',
                'size': '60',
                'align': 'left'
            },
            {
                'font': 'Droid Serif,Bold',
                'text': 'Some example product',
                'size': '50',
                'align': 'center'
            }
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        data = response.get_json()
        assert data['message'] == 'EAN must have 12 digits, received 10.'

    def test_generate_ean13_custom_label(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'ean13'
        data['code_text'] = '123456789012'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'Some example product',
                'size': '50',
                'align': 'center'
            }
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'barcode_ean13_custom_label.png')

    def test_generate_ean13_empty(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'ean13'
        data['code_text'] = '123456789012'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'barcode_ean13_empty_label.png')

    def test_generate_qr(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'QR'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '123456789012',
                'size': '40',
                'align': 'center'
            },
            {
                'font': 'Droid Serif,Bold',
                'text': 'Some example product',
                'size': '50',
                'align': 'center'
            }
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'qr.png')

    def test_generate_qr_custom_content(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'QR'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': 'This is some text not',
                'size': '40',
                'align': 'center'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': 'contained in the QR code',
                'size': '40',
                'align': 'center'
            }
        ])
        data['code_text'] = 'https://github.com/DL6ER/brother_ql_web'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'qr_custom_label.png')

    def test_image(self, client: FlaskClient):
        self.run_image_test(client)

    def test_image_fit(self, client: FlaskClient):
        self.run_image_test(client, fit=True)

    def test_image_rotated(self, client: FlaskClient):
        self.run_image_test(client, rotated=True)

    def test_image_rotated_fit(self, client: FlaskClient):
        self.run_image_test(client, rotated=True, fit=True)

    def test_image_with_text(self, client: FlaskClient):
        self.run_image_test(client, text=True)

    def test_image_with_text_fit(self, client: FlaskClient):
        self.run_image_test(client, text=True, fit=True)

    def test_image_with_text_rotated(self, client: FlaskClient):
        self.run_image_test(client, text=True, rotated=True)

    def test_image_with_text_fit_rotated(self, client: FlaskClient):
        self.run_image_test(client, text=True, rotated=True, fit=True)

    def test_image_color_fit(self, client: FlaskClient):
        self.run_image_test(client, image_mode="colored", fit=True)

    def test_image_red_and_black_fit(self, client: FlaskClient):
        self.run_image_test(client, image_mode="red_and_black", fit=True)

    def test_image_black_fit(self, client: FlaskClient):
        self.run_image_test(client, image_mode="black", fit=True)

    def test_image_highres(self, client: FlaskClient):
        self.run_image_test(client, high_res=True)

    def test_image_highres_fit(self, client: FlaskClient):
        self.run_image_test(client, high_res=True, fit=True)

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

    def test_generate_template(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        # Mock current datetime.now
        data['timestamp'] = int(datetime(2023, 3, 18, 12, 15, 30).timestamp())

        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '{{datetime:%d.%m.%Y %H:%M:%S}} COUNTER: {{counter}} {{counter:5}}',
                'size': '30',
                'align': 'left'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '>> {{datetime:Label created at %H:%M on %m/%d/%y}} <<',
                'size': '20',
                'align': 'right',
                'inverted': True
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '>> {{uuid}} {{short-uuid}} <<',
                'size': '20',
                'align': 'center'
            },
            {
                'font': 'DejaVu Sans,Book',
                'text': '>> {{random:77}} <<',
                'size': '10',
                'align': 'center'
            }
        ])

        # Set random seed to a fixed value for deterministic results of {{random}},
        # {{uuid}}, and {{short-uuid}}
        random.seed(12)
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'template.png')

    def test_generate_shifted_randomness(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '>> {{random:shift}} <<',
                'size': '15',
                'align': 'center',
                'line_spacing': '150'
            },
            {
                'font': 'DejaVu Sans,Bold',
                'text': '>> {{random:77:shift}} <<',
                'size': '10',
                'align': 'center'
            }
        ])

        # Set random seed to a fixed value for deterministic results of {{random}}
        random.seed(12)
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'shifted_random.png')

    def test_invalid_data_types(self, client: FlaskClient):
        # Non-integer label_size
        data = EXAMPLE_FORMDATA.copy()
        data['label_size'] = 'sixty-two'
        data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Test', 'size': '12', 'align': 'center'}])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        assert 'message' in response.get_json()

        # Non-JSON text
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = 'not-a-json-string'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        assert 'message' in response.get_json()

    def test_large_input_handling(self, client: FlaskClient):
        # Very large text
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'A' * 20000, 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        # 413 = Content Too Large
        assert response.status_code == 413

    def test_unsupported_barcode_type(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'UNSUPPORTED'
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': '123456789012', 'size': '40', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        assert 'message' in response.get_json()

    def test_font_not_found(self, client: FlaskClient):
        # A font that is not installed, e.g. in a label saved on another
        # installation, is replaced by the default font
        family, style = client.application.extensions['fonts'].get_default_font()
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'NonExistentFont,Book', 'text': 'Test', 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.headers['X-Label-Warning'] == \
            f'The font NonExistentFont (Book) is not installed, {family} ({style}) is used instead.'
        data['text'] = json.dumps([
            {'font': f'{family},{style}', 'text': 'Test', 'size': '12', 'align': 'center'}
        ])
        expected = client.post('/labeldesigner/api/preview', data=data)
        assert 'X-Label-Warning' not in expected.headers
        assert response.data == expected.data

    def test_method_enforcement(self, client: FlaskClient):
        # POST required for preview
        response = client.get('/labeldesigner/api/preview')
        # 405 = Method Not Allowed
        assert response.status_code == 405

    def test_template_edge_cases(self, client: FlaskClient):
        # Incomplete template
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': '{{datetime:}}', 'size': '32', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'template_incomplete.png')

        # Unknown template variable
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': '{{unknownvar}}', 'size': '32', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'template_unknown.png')

    def test_concurrent_preview_requests(self, client: FlaskClient):
        """
        Spawn multiple threads that each create their own test client instance
        and post to the preview endpoint. Using threads avoids pickling issues
        with Flask test clients when using multiprocessing.Process.
        """
        from threading import Thread

        results = []
        # Protect shared list
        import threading
        results_lock = threading.Lock()

        def make_request(i: int):
            # Each thread creates its own test client to avoid sharing state
            app = client.application
            data = EXAMPLE_FORMDATA.copy()
            data['text'] = json.dumps([
                {'font': 'DejaVu Sans,Book', 'text': str(i), 'size': '32', 'align': 'center'}
            ])
            with app.test_client() as local_client:
                resp = local_client.post('/labeldesigner/api/preview', data=data)
                with results_lock:
                    results.append(resp.status_code)

        threads = [Thread(target=make_request, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(results) == 50
        assert all(code == 200 for code in results)

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
        assert data['message'] == 'Truncated File Read'

    def test_empty_label(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'empty_label.png')

    def test_minimal_label(self, client: FlaskClient):
        # Minimum label_size
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Min', 'size': '1', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'minimal_label.png')

    def test_unicode_and_special_characters(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Emoji: 😃', 'size': '32', 'align': 'center'},
            {'font': 'DejaVu Sans,Book', 'text': 'RTL: שלום', 'size': '32', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'unicode.png')

    def test_security_xss(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': '<script>alert(1)</script>', 'size': '32', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        # Should return image with plain text
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'security_xss.png')

    @pytest.mark.parametrize('method', ['put', 'delete', 'patch'])
    def test_invalid_http_methods(self, client: FlaskClient, method):
        func = getattr(client, method)
        response = func('/labeldesigner/api/preview')
        assert response.status_code == 405

    def test_print_red_text(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Red Text', 'size': '24', 'align': 'center', 'color': 'red'},
            {'font': 'DejaVu Sans,Book', 'text': 'Red Text INVERTED', 'size': '24', 'align': 'center', 'inverted': True, 'color': 'red'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'red_text.png')

    def test_large_number_of_text_blocks(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        ALIGNS = ['center', 'left', 'right']
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': f'--- {i} ---',
                'size': str(i + 1),
                'align': ALIGNS[i % 3],
                'inverted': bool(i % 2),
                'color': 'red' if i % 5 == 0 else 'black'
                } for i in range(100)
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'large_label.png')

    def test_file_size_limits(self, client: FlaskClient):
        # Just below the limit
        data = EXAMPLE_FORMDATA.copy()
        big_content = b'\xff' * (16 * 1024 * 1024 - 100)
        big_file = FileStorage(
            stream=io.BytesIO(big_content),
            filename='big.jpg',
            content_type='image/jpeg',
        )
        data['print_type'] = 'image'
        data['image'] = big_file
        data['image_mode'] = 'grayscale'
        response = client.post('/labeldesigner/api/preview', data=data)
        # File size limit exceeded
        assert response.status_code == 413

    def test_extra_fields_ignored(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Extra', 'size': '12', 'align': 'center'}
        ])
        data['unexpected_field'] = 'should be ignored'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'extra_fields.png')

    def test_multiple_images_failure(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        file1 = FileStorage(stream=io.BytesIO(b'img1'), filename='img1.jpg', content_type='image/jpeg')
        file2 = FileStorage(stream=io.BytesIO(b'img2'), filename='img2.jpg', content_type='image/jpeg')
        data['print_type'] = 'image'
        data['image'] = [file1, file2]
        data['image_mode'] = 'grayscale'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_image_mode_edge_case(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'image'
        data['image'] = FileStorage(stream=io.BytesIO(b'img'), filename='img.jpg', content_type='image/jpeg')
        data['image_mode'] = 'not_a_mode'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_non_existing_label_size(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['label_size'] = 62.5
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Float', 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_non_utf8_encoded_text(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        # Latin-1 encoded string
        text = 'Café'.encode('latin-1')
        data['text'] = text
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_html_in_text(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': '<b>Bold</b>', 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'html_in_text_plain.png')

    def test_missing_optional_fields(self, client: FlaskClient):
        data = {
            'print_type': 'text',
            'label_size': 62,
            'orientation': 'standard',
            'print_count': '1',
            'cut_once': '0',
            'text': json.dumps([
                {'font': 'DejaVu Sans,Book', 'text': 'No style', 'size': '12', 'align': 'center'}
            ])
        }
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'missing_optional_fields.png')

    def test_invalid_json_structure(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        # JSON string, but not a list
        data['text'] = json.dumps({'foo': 'bar'})
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    @pytest.mark.parametrize('size', [0, -1, -100])
    def test_negative_zero_size(self, client: FlaskClient, size):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Bad size', 'size': str(size), 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    @pytest.mark.parametrize('size', [0, -1, -100])
    def test_negative_zero_label_size(self, client: FlaskClient, size):
        data = EXAMPLE_FORMDATA.copy()
        data['label_size'] = size
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Bad label', 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_invalid_alignment(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Bad align', 'size': '12', 'align': 'diagonal'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_non_string_text_value(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 12345, 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_conflicting_fields(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'QR'
        data['image'] = FileStorage(stream=io.BytesIO(b'img'), filename='img.jpg', content_type='image/jpeg')
        data['image_mode'] = 'grayscale'
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Conflict', 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_unicode_normalization(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        s1 = 'Café'
        s2 = unicodedata.normalize('NFD', s1)
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': s1, 'size': '24', 'align': 'center'},
            {'font': 'DejaVu Sans,Book', 'text': s2, 'size': '24', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'unicode_normalization.png')

    def test_head_request(self, client: FlaskClient):
        response = client.head('/labeldesigner/api/preview')
        assert response.status_code == 405

    def test_malformed_multipart(self, client: FlaskClient):
        # Simulate a malformed multipart by sending a bad content-type
        data = '--bad-boundary\r\nContent-Disposition: form-data; name="text"\r\n\r\nfoo\r\n--bad-boundary--\r\n'
        response = client.post('/labeldesigner/api/preview', data=data, headers={'Content-Type': 'multipart/form-data; boundary=bad-boundary'})
        assert response.status_code == 400

    def test_unknown_font(self, client: FlaskClient):
        # Unknown families and styles are both replaced, each named once
        family, style = client.application.extensions['fonts'].get_default_font()
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'Droid Sand,Book', 'text': 'Item 1', 'size': '15', 'align': 'left'},
            {'font': 'Droid Sans,Non-exist', 'text': 'Item 2', 'size': '15', 'align': 'left'},
            {'font': 'Droid Sand,Book', 'text': 'Item 3', 'size': '15', 'align': 'left'},
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.headers['X-Label-Warning'] == (
            f'The font Droid Sand (Book) is not installed, {family} ({style}) is used instead. '
            f'The font Droid Sans (Non-exist) is not installed, {family} ({style}) is used instead.')

    def test_todo_list(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'Droid Sans,Regular', 'text': 'Item 1 (very small)', 'size': '15', 'align': 'left', 'checkbox': True},
            {'font': 'Noto Sans,Regular', 'text': 'Item 2', 'size': '50', 'align': 'left', 'checkbox': True},
            {'font': 'DejaVu Sans,Book', 'text': 'Not an item XX', 'size': '70', 'align': 'right', 'checkbox': False, 'color': 'red'},
            {'font': 'DejaVu Serif,Bold', 'text': 'Item 3', 'size': '50', 'align': 'left', 'checkbox': True}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'todo_list.png')

    def test_image_text_gap(self, client: FlaskClient):
        # Code and text along the tape: the label grows by the distance. The
        # code keeps its size, a fitted one is tested below.
        data = EXAMPLE_FORMDATA.copy()
        data.update({'print_type': 'qrcode_text', 'orientation': 'rotated', 'code_text': 'https://example.com', 'qrcode_size': '5', 'image_fit': '0',
                     'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Item', 'size': '50', 'align': 'left'}])})
        widths, gaps = {}, {}
        for gap in ('', '0', '60'):
            data['image_text_gap'] = gap
            response = client.post('/labeldesigner/api/preview', data=data)
            assert response.status_code == 200
            widths[gap] = Image.open(io.BytesIO(response.data)).width
            gaps[gap] = response.headers.get('X-Label-Image-Text-Gap')
        assert widths['60'] - widths['0'] == 60
        # Without a distance: two fifths of the largest text
        assert widths[''] - widths['0'] == 20
        assert gaps == {'': '20', '0': None, '60': '60'}
        # A QR code keeps at least two of its modules
        data.update({'image_text_gap': '', 'qrcode_size': '20'})
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.headers['X-Label-Image-Text-Gap'] == '40'
        # Without text there is nothing to keep a distance from
        data['text'] = json.dumps([])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert 'X-Label-Image-Text-Gap' not in response.headers

        data['image_text_gap'] = '-1'
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400

    def test_image_text_gap_of_a_fitted_qr_code(self, client: FlaskClient):
        # Fitted to the tape, the modules of 5 dots become 5.4 times larger,
        # the distance keeps two of them
        data = EXAMPLE_FORMDATA.copy()
        data.update({'print_type': 'qrcode_text', 'orientation': 'rotated', 'code_text': 'https://example.com', 'qrcode_size': '5',
                     'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Item', 'size': '50', 'align': 'left'}])})
        gaps = {}
        for fit in ('0', '1'):
            data['image_fit'] = fit
            gaps[fit] = client.post('/labeldesigner/api/preview', data=data).headers['X-Label-Image-Text-Gap']
        assert gaps == {'0': '20', '1': '54'}

    def test_large_font_not_cropped(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['border_thickness'] = 1
        data['border_color'] = 'red'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Math TeX Gyre,Regular',
                'text': 'Mehrwegbecher',
                'size': '70',
                'align': 'left'
            }
        ])

        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, 'large_text.png')

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

    # We cannot test the print functionality without a physical printer, this
    # will go through the simulator and be a no-op.
    def test_print_label_simulator(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        # Set config for printer
        response = client.post('/labeldesigner/api/print', data=data)
        assert response.status_code == 200
        assert response.is_json
        json_response = response.get_json()
        assert 'success' in json_response
        assert json_response['success'] is True

    # Test printing to an invalid printer path
    def test_print_label_invalid_printer(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['printer'] = '/dev/usb/lp99'
        response = client.post('/labeldesigner/api/print', data=data)
        assert response.status_code == 400
        assert response.is_json
        json_response = response.get_json()
        assert 'message' in json_response
        assert "Could not send the label to the printer" in json_response['message']
        assert "/dev/usb/lp99" in json_response['message']
        assert 'success' in json_response
        assert json_response['success'] is False

    def test_printer_status_returns_simulator(self, client: FlaskClient):
        """Check /api/printer_status includes simulator."""
        response = client.get('/labeldesigner/api/printer_status')
        assert response.status_code == 200
        assert response.is_json
        data = response.get_json()
        assert 'printers' in data
        printers = data['printers']
        assert isinstance(printers, list)
        # There must be at least the simulator printer
        assert any(p.get('path') == 'simulation' for p in printers)
        sim = next((p for p in printers if p.get('path') == 'simulation'), None)
        assert sim is not None
        # Simulator should use the configured PRINTER_MODEL
        assert sim.get('model') == client.application.config['PRINTER_MODEL']

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


def test_env_template_only_inserts_allowed_variables(tmp_path, monkeypatch):
    client = make_client(tmp_path)
    monkeypatch.setenv('LABEL_ROOM', 'Lab 1')
    monkeypatch.setenv('LABEL_SECRET', 'secret')
    client.application.config['LABEL_TEMPLATE_ENV_VARS'] = 'LABEL_ROOM'
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{env:LABEL_ROOM}}|{{env:LABEL_SECRET}}', 'size': '40'}])
    with client.application.test_request_context():
        label = create_label_from_request({'text': text})
    label.process_templates()
    assert label.text[0]['text'] == 'Lab 1|'


def test_mdi_template_inserts_the_icon(tmp_path):
    # Unknown names stay, so that a typo shows
    client = make_client(tmp_path)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{mdi:lightbulb}} Light {{mdi:no-such-icon}}', 'size': '40'}])
    with client.application.test_request_context():
        label = create_label_from_request({'text': text})
    label.process_templates()
    assert label.text[0]['text'] == '\U000F0335 Light {{mdi:no-such-icon}}'


@pytest.mark.parametrize('align', ['left', 'center', 'right'])
def test_icon_and_text_in_one_line(tmp_path, align):
    # The icon comes from the icon font, the text from the font of the line,
    # side by side and as high as the text alone
    client = make_client(tmp_path)

    def ink(text):
        data = dict(EXAMPLE_FORMDATA, border_thickness='0', text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '60', 'align': align}]))
        response = client.post('/labeldesigner/api/preview?return_format=png', data=data)
        assert response.status_code == 200 and 'X-Label-Warning' not in response.headers
        image = Image.open(io.BytesIO(response.data)).convert('L')
        return ImageOps.invert(image).point(lambda p: 255 if p > 127 else 0).getbbox()

    text_only, with_icon = ink('230 V'), ink('{{mdi:flash}} 230 V')
    assert with_icon[2] - with_icon[0] > text_only[2] - text_only[0] + 30
    assert abs(with_icon[3] - with_icon[1] - (text_only[3] - text_only[1])) < 10


def test_random_template_length_is_limited(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{random:10001}}', 'size': '40'}])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Random text length must not exceed 10000'


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


def test_print_count_is_limited(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['print_count'] = str(client.application.config['PRINT_MAX_COUNT'] + 1)
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == f"print_count must not exceed {client.application.config['PRINT_MAX_COUNT']}"


def test_print_requires_post(tmp_path):
    client = make_client(tmp_path)
    response = client.get('/labeldesigner/api/print', query_string=EXAMPLE_FORMDATA)
    assert response.status_code == 405


def test_cross_origin_requests_are_rejected(tmp_path):
    client = make_client(tmp_path)
    url = '/labeldesigner/api/print'
    # Form posted to the printer by another website
    response = client.post(url, data=EXAMPLE_FORMDATA, headers={'Origin': 'http://evil.example'})
    assert response.status_code == 403
    # The label designer itself
    response = client.post(url, data=EXAMPLE_FORMDATA, headers={'Origin': 'http://localhost'})
    assert response.status_code == 200
    # The label designer behind a reverse proxy that changes the Host header
    response = client.post(url, data=EXAMPLE_FORMDATA, headers={'Origin': 'https://labels.example', 'X-Requested-With': 'XMLHttpRequest'})
    assert response.status_code == 200
    # Scripts and other clients without Origin header
    response = client.post(url, data=EXAMPLE_FORMDATA)
    assert response.status_code == 200


def test_requests_cannot_change_log_level(tmp_path):
    client = make_client(tmp_path)
    level = client.application.logger.level
    data = EXAMPLE_FORMDATA.copy()
    data['log_level'] = 'CRITICAL'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert client.application.logger.level == level


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


def test_text_limit_applies_after_templates(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    # Each template is allowed, together they exceed the line limit
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{random:1000}}' * 11, 'size': '40'}])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 413
    assert response.get_json()['message'] == 'Text is too long'
    # Many lines below the line limit exceed the total limit
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{random:10000}}', 'size': '40'}] * 11)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 413
    assert response.get_json()['message'] == 'Text is too long'


@pytest.mark.parametrize('extra', [
    {'print_type': 'qrcode', 'qrcode_size': '5000', 'text': TEXT},
    {'print_type': 'image', 'image_fit': '0', 'image_scaling_factor': '500'},
])
def test_label_size_is_limited(tmp_path, extra):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update(extra)
    if extra['print_type'] == 'image':
        data['image'] = _upload(Image.new('L', (2000, 2000), 'black'), 'big.png')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'].startswith('Label is too large')


def test_trusted_hosts(tmp_path):
    client = make_client(tmp_path)
    client.application.config['TRUSTED_HOSTS'] = ['labels.local']
    # A page using DNS rebinding reaches the server under its own host name
    assert client.get('/labeldesigner/', base_url='http://evil.example').status_code == 400
    assert client.get('/labeldesigner/', base_url='http://labels.local:8013').status_code == 200


def test_webhook_accepts_pdf_in_json(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    buffer = io.BytesIO()
    _red_square().save(buffer, 'PDF')
    payload = {'password': 'secret', 'images': [{'data': base64.b64encode(buffer.getvalue()).decode('ascii'), 'mime': 'application/pdf'}]}
    response = client.post('/labeldesigner/api/webhook/print', json=payload)
    assert response.status_code == 200
    assert response.get_json() == {'success': True, 'count': 1}


def test_webhook_authentication(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    url = '/labeldesigner/api/webhook/print'
    # Wrong passwords are refused, including ones with non-ASCII characters
    assert client.post(url, headers={'Authorization': 'Bearer pässwort'}).status_code == 401
    # The password is not accepted in the URL, it would end up in access logs
    assert client.post(url + '?password=secret').status_code == 401
    # Header, form field and JSON body are accepted (no images sent, though)
    for kwargs in ({'headers': {'Authorization': 'Bearer secret'}}, {'data': {'password': 'secret'}}, {'json': {'password': 'secret'}}):
        response = client.post(url, **kwargs)
        assert response.status_code == 400
        assert response.get_json()['message'] == 'No images provided'


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


def test_api_defaults_match_label_designer(tmp_path):
    client = make_client(tmp_path)
    config = client.application.config
    implicit = client.post('/labeldesigner/api/preview', data={'label_size': '62', 'text': TEXT})
    explicit = client.post('/labeldesigner/api/preview', data={
        'label_size': '62',
        'text': TEXT,
        'border_thickness': '0',
        'margin_top': config['LABEL_DEFAULT_MARGIN_TOP'],
        'margin_bottom': config['LABEL_DEFAULT_MARGIN_BOTTOM'],
        'margin_left': config['LABEL_DEFAULT_MARGIN_LEFT'],
        'margin_right': config['LABEL_DEFAULT_MARGIN_RIGHT'],
    })
    assert implicit.status_code == explicit.status_code == 200
    assert implicit.data == explicit.data


@pytest.mark.parametrize('device', ['file://{target}', '{target}', 'tcp://127.0.0.1:6379'])
def test_print_rejects_unsupported_printer_devices(tmp_path, device):
    client = make_client(tmp_path)
    target = tmp_path / 'important.json'
    target.write_text('{"keep": true}')
    data = EXAMPLE_FORMDATA.copy()
    data['printer'] = device.format(target=target)
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == f"Unsupported printer: {data['printer']}"
    assert target.read_text() == '{"keep": true}'


class FakeSlowPrinter:
    """Printer that returns empty reads before answering, like the QL-1100."""

    def __init__(self, empty_reads: int, response: bytes):
        self.empty_reads = empty_reads
        self.response = response
        self.written = b''

    def write(self, data: bytes):
        self.written += data

    def read(self, length: int) -> bytes:
        if self.empty_reads > 0:
            self.empty_reads -= 1
            return b''
        chunk, self.response = self.response[:length], self.response[length:]
        return chunk


class FakeResetPrinter(FakeSlowPrinter):
    """Printer that answers only after "ESC @", like the QL-560 after its
    first status request."""

    def __init__(self, response: bytes):
        super().__init__(empty_reads=0, response=response)

    def read(self, length: int) -> bytes:
        if b'\x1b\x40' not in self.written:
            return b''
        return super().read(length)


def make_status_packet() -> bytes:
    packet = bytearray(32)
    packet[0:3] = b'\x80\x20\x42'  # header
    packet[3] = 0x34  # series code
    packet[4] = 0x38  # model code
    packet[10] = 62  # media width
    packet[11] = 0x0A  # continuous length tape
    return bytes(packet)


class TestPrinterStatus:
    def test_get_status_waits_for_delayed_response(self):
        from app.labeldesigner.printer import get_status
        printer = FakeSlowPrinter(empty_reads=5, response=make_status_packet())
        status = get_status(printer)
        assert printer.written == b'\x1b\x69\x53'
        assert status['media_width'] == 62
        assert status['errors'] == []

    def test_get_status_resets_a_printer_that_does_not_answer(self):
        from app.labeldesigner.printer import get_status
        printer = FakeResetPrinter(make_status_packet())
        status = get_status(printer, timeout=0.1)
        assert printer.written == b'\x1b\x69\x53' + b'\x00' * 200 + b'\x1b\x40\x1b\x69\x53'
        assert status['media_width'] == 62

    def test_get_status_times_out_without_response(self):
        from app.labeldesigner.printer import get_status
        printer = FakeSlowPrinter(empty_reads=10**6, response=b'')
        with pytest.raises(TimeoutError):
            get_status(printer, timeout=0.1)


def test_barcode_length_is_limited(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'qrcode', 'barcode_type': 'code128', 'code_text': 'A' * 1001})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Barcode value must not exceed 1000 characters'


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
    assert 'exceeds limit' in message or message.startswith('Label is too large')


def test_webhook_image_count_is_limited(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    client.application.config['PRINT_MAX_COUNT'] = 2
    image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image] * 3})
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Too many images, at most 2 per request'


def test_webhook_refuses_images_too_large_for_the_label(tmp_path):
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    # Scaled to the label width, this thin image would get 69 million pixels high
    image = base64.b64encode(_upload(Image.new('L', (1, 100000), 'black'), 'thin.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image]})
    assert response.status_code == 400


def test_printer_device_with_trailing_newline_is_refused(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['printer'] = '/dev/usb/lp0\n'
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'].startswith('Unsupported printer')


def test_simulator_keeps_only_the_newest_labels(tmp_path, monkeypatch):
    from app.labeldesigner import printer
    monkeypatch.setattr(printer, 'MAX_SIMULATED_LABELS', 2)
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['print_count'] = '3'
    assert client.post('/labeldesigner/api/print', data=data).get_json() == {'success': True}
    assert len(os.listdir(printer.SIMULATED_LABELS_DIR)) == 2


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


def test_text_label_is_not_loaded_again_for_every_copy(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    calls = []
    original = routes.load_label_images
    monkeypatch.setattr(routes, 'load_label_images', lambda *args, **kwargs: calls.append(args) or original(*args, **kwargs))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['print_count'] = '3'
    assert client.post('/labeldesigner/api/print', data=data).get_json() == {'success': True}
    assert len(calls) == 1


def test_font_cache_is_bounded():
    from app.labeldesigner import label
    path = create_app().extensions['fonts'].get_path('DejaVu Sans,Book')
    label._load_font.cache_clear()
    for size in ['40', '040', '0040']:
        label.SimpleLabel()._get_font(path, size)
    assert label._load_font.cache_info().currsize == 1
    assert label._load_font.cache_info().maxsize == 256


def test_refused_env_variable_is_logged_once(tmp_path, caplog):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{env:ROOM_FOR_LOG_TEST}}', 'size': '40'}])
    for _ in range(3):
        assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert sum('ROOM_FOR_LOG_TEST' in record.getMessage() for record in caplog.records) == 1


def test_refused_env_variables_are_bounded(tmp_path, monkeypatch):
    from app.labeldesigner import label
    monkeypatch.setattr(label, '_REFUSED_ENV_VARS', set())
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '10', 'text': ''.join(f'{{{{env:X{i}}}}}' for i in range(500))}])
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert len(label._REFUSED_ENV_VARS) == label._MAX_REFUSED_ENV_VARS


def test_qr_code_has_no_byte_order_mark(tmp_path, monkeypatch):
    # A BOM in front of the content keeps phones from recognizing URLs (#101)
    from app.labeldesigner import label
    added = []
    original = label.QRCode.add_data
    monkeypatch.setattr(label.QRCode, 'add_data', lambda self, data, *args: added.append(data) or original(self, data, *args))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'qrcode', 'barcode_type': 'QR', 'code_text': 'https://example.com/Größe'})
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert added == ['https://example.com/Größe'.encode('utf-8')]


def test_templates_in_the_code_are_filled_in(tmp_path):
    # Like the text, each copy has its own number in the code. Icons are
    # drawn, a code keeps their name.
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'code_text': 'Box {{counter:100}}, {{datetime:%Y}} {{mdi:lightbulb}}', 'timestamp': '1785000000',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Box {{counter:100}}', 'size': '40'}])}
    with client.application.test_request_context():
        labels = [create_label_from_request(data, {}, counter) for counter in range(2)]
    for label in labels:
        label.process_templates()
    assert [label.code_text for label in labels] == ['Box 100, 2026 {{mdi:lightbulb}}', 'Box 101, 2026 {{mdi:lightbulb}}']
    assert [label.text[0]['text'] for label in labels] == ['Box 100', 'Box 101']


def test_code_that_fills_in_to_nothing_keeps_the_text_out(tmp_path, monkeypatch):
    # A variable that is not listed inserts nothing, the code must not hold
    # the text of the label instead. Empty, a code says so.
    from app.labeldesigner import label
    added = []
    original = label.QRCode.add_data
    monkeypatch.setattr(label.QRCode, 'add_data', lambda self, data, *args: added.append(data) or original(self, data, *args))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'qrcode_text', 'barcode_type': 'QR', 'code_text': '{{env:NOT_LISTED_FOR_CODE_TEST}}',
                 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Server room', 'size': '40'}])})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == 'The QR code is empty'
    assert added == []
    data['barcode_type'] = 'code128'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == 'The barcode is empty'


@pytest.mark.parametrize('print_type', ['text', 'image'])
def test_code_of_a_label_without_code_is_not_filled_in(tmp_path, monkeypatch, caplog, print_type):
    # The label designer keeps the code when the layout is changed back to
    # text, its templates must neither refuse the label nor log anything
    from app.labeldesigner import label
    monkeypatch.setattr(label, '_REFUSED_ENV_VARS', set())
    client = make_client(tmp_path)

    def preview(code_text):
        data = dict(EXAMPLE_FORMDATA, print_type=print_type, text=TEXT, code_text=code_text)
        if print_type == 'image':
            data['image'] = _upload(_red_square(), 'red.png')
        return client.post('/labeldesigner/api/preview', data=data)
    assert preview('{{random:20000}}').status_code == 200
    assert preview('{{env:UNUSED_CODE_VARIABLE}}').status_code == 200
    assert not any('UNUSED_CODE_VARIABLE' in record.getMessage() for record in caplog.records)
    # A label with a code still fills it in
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', text=TEXT, code_text='Room {{env:UNUSED_CODE_VARIABLE}}')
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert any('UNUSED_CODE_VARIABLE' in record.getMessage() for record in caplog.records)


def test_blanks_are_filled_in(tmp_path, monkeypatch):
    # The values come with the request, as given: templates in them are not
    # filled in. Without a value a blank gets its default, a date today's
    # date of the label, any other blank stays empty.
    client = make_client(tmp_path)
    monkeypatch.setenv('LABEL_ROOM', 'Lab 1')
    client.application.config['LABEL_TEMPLATE_ENV_VARS'] = 'LABEL_ROOM'
    timestamp = 1785000000
    today = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
    lines = ['{{blank:What}} {{env:LABEL_ROOM}}', 'Frozen on {{blank:Frozen on}}', 'Best before {{blank:Best before}}',
             'Box {{blank:Box}}/{{blank:Shelf}}', '{{blank:Note}}', '{{blank:Use by}}']
    data = {'print_type': 'qrcode_text', 'timestamp': str(timestamp), 'code_text': '{{blank:What}} {{counter}}',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '40'} for text in lines]),
            'fields': {'Box': {'type': 'number', 'default': '3'}, 'Best before': {'type': 'date'}, 'Note': {'default': 'n/a'},
                       'Use by': {'type': 'date', 'default': '2026-12-24'}, 'Frozen on': {'type': 'date'}}}
    expected = ['{{env:LABEL_ROOM}} {{counter}} Lab 1', f'Frozen on {today}', f'Best before {today}', 'Box 3/', 'n/a', '2026-12-24']
    with client.application.test_request_context():
        # As JSON text in a form or query, and as object in a JSON body
        for blanks in (json.dumps({'What': '{{env:LABEL_ROOM}} {{counter}}'}),
                       {'What': '{{env:LABEL_ROOM}} {{counter}}'}):
            label = create_label_from_request(dict(data, blanks=blanks))
            label.process_templates()
            assert [line['text'] for line in label.text] == expected
            assert label.code_text == '{{env:LABEL_ROOM}} {{counter}} 1'
        # Left empty on purpose, also a date: no default, no date of today
        label = create_label_from_request(dict(data, blanks={'Note': '', 'Use by': '', 'Frozen on': ''}))
        label.process_templates()
        assert [label.text[i]['text'] for i in (1, 4, 5)] == ['Frozen on ', '', '']
        label = create_label_from_request(dict(data, blanks={'Box': 4, 'Shelf': 2.5}))
        label.process_templates()
        assert label.text[3]['text'] == 'Box 4/2.5'
        assert label.code_text == ' 1'


@pytest.mark.parametrize('barcode_type', ['QR', 'ean13'])
def test_code_of_empty_blanks_takes_the_text(tmp_path, barcode_type):
    # Like a code left empty
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type=barcode_type,
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '123456789012', 'size': '40'}]))

    def preview(code_text, **values):
        response = client.post('/labeldesigner/api/preview', data=dict(data, code_text=code_text, **values))
        assert response.status_code == 200
        return response.data
    assert preview('{{blank:Code}}') == preview('')
    assert preview('{{blank:Code}}', blanks=json.dumps({'Code': '400638133393'})) == preview('400638133393')
    assert preview('400638133393') != preview('')


def test_a_blank_without_a_type_is_text(tmp_path):
    # Named with the word "on" it was taken for a date, e.g. "Add-on" or "On
    # hand", and got today's date when left empty
    client = make_client(tmp_path)
    data = {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Add-on}}|{{blank:On hand}}|{{blank:Frozen on}}'}])}
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.process_templates()
        assert label.text[0]['text'] == '||'


def test_dates_of_blanks_are_written_in_their_format(tmp_path):
    # As the label designer saves them, the settings come with the label or
    # as JSON with the request. Text that looks like a date stays as it is.
    # Without a type a blank is text, also one named like a date.
    client = make_client(tmp_path)
    timestamp = 1785000000
    today = datetime.fromtimestamp(timestamp).strftime('%d.%m.%Y')
    fields = {'Due': {'type': 'date', 'format': '%d.%m.%Y'}, 'Code': {'type': 'text'}, 'Made on': {'format': '%d/%m/%Y'},
              'Odd': {'type': 'date', 'format': 'week %V'}}
    data = {'timestamp': str(timestamp), 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40',
            'text': '{{blank:Code}}|{{blank:Due}}|{{blank:Made on}}|{{blank:Sent on}}|{{blank:Odd}}'}])}
    expected = {
        json.dumps({'Code': '2026-11-01', 'Due': '2026-11-01', 'Made on': '2026-11-02', 'Sent on': '2026-11-03', 'Odd': '2026-11-04'}):
            '2026-11-01|01.11.2026|2026-11-02|2026-11-03|week 45',
        # Without a value today, a value that is no date stays as given
        json.dumps({'Due': 'tomorrow', 'Made on': '2026-13-01'}): f'|tomorrow|2026-13-01||{datetime.fromtimestamp(timestamp):week %V}',
    }
    with client.application.test_request_context():
        for blanks, text in expected.items():
            for given in (fields, json.dumps(fields)):
                label = create_label_from_request(dict(data, blanks=blanks, fields=given))
                label.process_templates()
                assert label.text[0]['text'] == text
        label = create_label_from_request(dict(data, fields=json.dumps(fields)))
        label.process_templates()
        assert label.text[0]['text'].split('|')[1] == today


@pytest.mark.parametrize('fields', ['Due', '["Due"]', '{"Due": "date"}',
                                    # What is in them as well: a format that is
                                    # no text was written as its Python text
                                    '{"Due": {"type": "date", "format": {"x": 1}}}', '{"Due": {"type": "color"}}',
                                    '{"Due": {"type": ["date"]}}', '{"Due": {"default": true}}', '{"Due": {"default": [1]}}',
                                    # Nested deeper than Python reads
                                    '[' * 100000])
def test_settings_of_blanks_must_be_an_object_of_objects(tmp_path, fields):
    client = make_client(tmp_path, empty_repo=True)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Due}}', 'size': '40'}]), fields=fields)
    message = ('fields must be a JSON object with the settings of each blank: a type of text, number or date, '
               'a format as text and a default as text or number, e.g. {"Frozen on": {"type": "date"}}')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == message
    # Also where they are saved with a label, and as an object
    for given in (fields, *([json.loads(fields)] if fields.startswith('{') else [])):
        response = client.post('/labeldesigner/api/repository/save', json={'name': 'Due.json', 'label_size': '62', 'text': data['text'], 'fields': given})
        assert response.status_code == 400 and response.get_json()['message'] == message
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == []


def test_settings_of_blanks_as_the_label_designer_saves_them(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Due}} {{blank:Box}} {{blank:Note}}', 'size': '40'}])
    fields = {'Due': {'type': 'date', 'format': '%d.%m.%Y', 'default': '2026-11-04'}, 'Box': {'type': 'number', 'default': 3},
              'Note': {'type': 'text', 'default': None, 'format': None}}
    for given in (fields, json.dumps(fields)):
        response = client.post('/labeldesigner/api/repository/save', json={'name': 'Due.json', 'label_size': '62', 'text': text, 'fields': given})
        assert response.status_code == 200
    assert client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=text, fields=json.dumps(fields))).status_code == 200


def test_blank_values_nested_too_deep_are_refused_where_a_label_is_saved(tmp_path):
    # Read from JSON text, they failed with an error of the server
    client = make_client(tmp_path, empty_repo=True)
    response = client.post('/labeldesigner/api/repository/save', json={'name': 'Box.json', 'label_size': '62', 'text': TEXT, 'blanks': '[' * 100000})
    assert response.status_code == 400 and response.get_json()['message'].startswith('blanks must be a JSON object')


def test_preview_marks_the_blanks_to_fill_in(tmp_path, monkeypatch):
    # The label designer shows them by name. A code that takes the text gets
    # it as printed, with the same random parts as the text.
    from app.labeldesigner import label as label_module
    coded = []
    original = label_module._qr_image
    monkeypatch.setattr(label_module, '_qr_image', lambda data, *args: coded.append(data) or original(data, *args))
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'barcode_type': 'QR', 'code_text': '', 'blanks': json.dumps({'Box': '4'}),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf {{blank:Room}} {{blank:Box}} {{short-uuid}}'}])}
    with client.application.test_request_context():
        for marks, shown in (('1', 'Shelf Room? 4 '), ('0', 'Shelf  4 ')):
            label = create_label_from_request(data, mark_blanks=marks == '1')
            label.generate()
            text = label.text[0]['text']
            assert text.startswith(shown) and len(text) == len(shown) + 8
            assert coded[-1] == 'Shelf  4 ' + text[-8:]


def test_each_blank_is_filled_in_once(tmp_path, monkeypatch):
    # Also for the code, which takes the value of the text
    from app.labeldesigner import label as label_module
    filled = []
    original = label_module.SimpleLabel._fill_blank
    monkeypatch.setattr(label_module.SimpleLabel, '_fill_blank', lambda self, name: filled.append(name) or original(self, name))
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'barcode_type': 'QR', 'code_text': 'box/{{blank:Box}}', 'blanks': json.dumps({'Box': '4'}),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Box {{blank:Box}}, {{blank:Box}}'}])}
    with client.application.test_request_context():
        create_label_from_request(data).generate()
        assert filled == ['Box']
        # Also for an image, which turns to fit the label without text
        filled.clear()
        image = Image.new('L', (300, 600), 0)
        create_label_from_request(dict(data, label_size='62x100', print_type='image'), image=image).generate()
        assert filled == ['Box']


def test_long_dates_of_blanks_are_refused_early(tmp_path, monkeypatch):
    # Many blanks with a long date are refused before they are all made, and
    # a format that would make each date long is refused
    from app.labeldesigner import label as label_module
    made = []
    original = label_module._iso_date
    monkeypatch.setattr(label_module, '_iso_date', lambda value: made.append(value) or original(value))
    client = make_client(tmp_path)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '10', 'text': '{{blank:d}}' * 900}])
    data = dict(EXAMPLE_FORMDATA, text=text, blanks=json.dumps({'d': '2026-11-01'}))
    response = client.post('/labeldesigner/api/preview', data=dict(data, fields=json.dumps({'d': {'type': 'date', 'format': '%A %d %B %Y'}})))
    assert response.status_code == 413 and response.get_json()['message'] == 'Text is too long'
    assert len(made) == 1
    response = client.post('/labeldesigner/api/preview', data=dict(data, fields=json.dumps({'d': {'type': 'date', 'format': '%Y' * 51}})))
    assert response.status_code == 413 and response.get_json()['message'] == 'The date format of a blank is too long'


def test_lines_of_a_request_cannot_set_the_code(tmp_path, monkeypatch):
    # The text of a line without its marks is set by the templates only
    from app.labeldesigner import label as label_module
    coded = []
    original = label_module._qr_image
    monkeypatch.setattr(label_module, '_qr_image', lambda data, *args: coded.append(data) or original(data, *args))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type='QR', code_text='',
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'ABC', 'unmarked': 'XYZ'}]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert coded == ['ABC']


def test_distance_kept_to_is_used_when_fitting_is_uneven(monkeypatch):
    # Rounded font sizes can make text fitted at a distance larger than at a
    # smaller one. The distance the search ends at is used only if the text
    # fitted at it keeps to it, else the last one it kept to.
    from app.labeldesigner import label as label_module
    own_gap = {48: 30, 30: 40, 35: 36, 36: 38}
    label = label_module.SimpleLabel()
    monkeypatch.setattr(label, '_text_space', lambda width, height, img_width, img_height, gap, largest=False: (gap, None))
    monkeypatch.setattr(label, '_gap', lambda img_width, img_height: 48 if label._text_scale == 1.0 else own_gap[label._text_scale])

    def fit(bboxes, textsize, gap, max_height):
        label._text_scale = gap
        return bboxes, textsize
    monkeypatch.setattr(label, '_shrink_text_to_fit', fit)
    label._shrink_text_next_to_image([], (0, 0, 0, 0), 100, 100, 10, 10)
    assert own_gap[label._text_scale] <= label._text_scale


def test_image_turns_to_fit_only_without_lines_of_text(tmp_path):
    # Beside a line of blanks it is laid out as with text, also while they
    # are left empty: the preview with their marks looks like the label
    # printed, and it does not turn once they are filled in
    client = make_client(tmp_path)
    tall = io.BytesIO()
    Image.new('L', (300, 600), 0).save(tall, 'PNG')

    def preview(**values):
        data = dict(label_size='62x100', print_type='image', image=(io.BytesIO(tall.getvalue()), 'tall.png', 'image/png'),
                    **dict({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Note}}'}])}, **values))
        response = client.post('/labeldesigner/api/preview', data=data, content_type='multipart/form-data')
        assert response.status_code == 200
        return response
    assert 'X-Label-Orientation' not in preview().headers
    assert 'X-Label-Orientation' not in preview(blanks=json.dumps({'Note': 'Garage'})).headers
    assert preview(text='[]').headers.get('X-Label-Orientation') == 'auto'


@pytest.mark.parametrize('barcode_type', ['QR', 'code128'])
def test_preview_shows_the_blanks_of_a_code_that_has_nothing_else(tmp_path, monkeypatch, barcode_type):
    # Printed, a code of blanks left empty takes the text, here there is none:
    # the preview shows what is still to fill in instead of nothing
    from app.labeldesigner import label as label_module
    coded = []
    for name in ('_qr_image', '_barcode_image'):
        original = getattr(label_module, name)
        monkeypatch.setattr(label_module, name, lambda *args, original=original, name=name: coded.append(args[0] if name == '_qr_image' else args[-1]) or original(*args))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type=barcode_type, code_text='{{blank:Link}}', text='[]')
    assert client.post('/labeldesigner/api/preview', data=dict(data, mark_blanks='1')).status_code == 200
    assert coded == ['Link?']
    # Printed, an empty code says so
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == ('The QR code is empty' if barcode_type == 'QR' else 'The barcode is empty')


@pytest.mark.parametrize('barcode_type', ['QR', 'code128'])
@pytest.mark.parametrize('code, lines, qr, barcode', [
    # The code takes the text, which is only blanks left empty
    ('', ['{{blank:Name}}'], 'Name?', 'Name?'),
    ('', ['{{blank:A}}', '{{blank:B}}'], 'A?\nB?', 'A?'),
    # The blanks of the code itself
    ('{{blank:Link}}', ['{{blank:Name}}'], 'Link?', 'Link?'),
])
def test_preview_shows_the_blanks_of_a_code_that_takes_empty_text(tmp_path, monkeypatch, barcode_type, code, lines, qr, barcode):
    from app.labeldesigner import label as label_module
    coded = []
    for name in ('_qr_image', '_barcode_image'):
        original = getattr(label_module, name)
        monkeypatch.setattr(label_module, name, lambda *args, original=original, name=name: coded.append(args[0] if name == '_qr_image' else args[-1]) or original(*args))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type=barcode_type, code_text=code, mark_blanks='1',
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': line} for line in lines]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert coded == [qr if barcode_type == 'QR' else barcode]


@pytest.mark.parametrize('label_size, side', [('62x100', ''), ('62', 'left'), ('62', 'above')])
def test_preview_beside_blanks_left_empty_is_the_print_with_their_marks(tmp_path, label_size, side):
    # The marks counted as text: an image fitted next to them was laid out
    # in another place and size than on the label printed without them
    client = make_client(tmp_path)

    def preview(**values):
        data = dict(label_size=label_size, print_type='image', image_mode='grayscale', image_position=side,
                    image=_upload(Image.new('L', (300, 600), 128), 'tall.png'),
                    text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Note}}'}]), **values)
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        return response
    marked, printed = preview(mark_blanks='1'), preview()
    (image, text), (image_printed, _) = _image_and_text_boxes(marked.data), _image_and_text_boxes(printed.data)
    assert text and image == image_printed
    assert printed.headers.get('X-Label-Image-Position') == marked.headers.get('X-Label-Image-Position')


def test_code_cache_counts_its_pixels():
    # Kept as they come and go, one larger than all is not kept at all
    from app.labeldesigner import label
    cache = label._ImageCache(100)
    large = cache.get(('large',), lambda: Image.new('L', (11, 11), 7))
    assert large.getpixel((0, 0)) == 7 and cache._pixels == 0 and not cache._images
    for key, size in (('a', 6), ('b', 5), ('c', 7)):
        cache.get((key,), lambda size=size: Image.new('L', (size, size), 0))
    assert list(cache._images) == [('b',), ('c',)] and cache._pixels == 25 + 49


def test_code_cache_gives_the_codes_kept_and_labels_leave_them_as_they_are(tmp_path):
    # Copied each time a preview was made with it, nobody changes them
    from app.labeldesigner import label as label_module
    cache = label_module._ImageCache(100)
    made = cache.get(('a',), lambda: Image.new('L', (5, 5), 0))
    assert cache.get(('a',), lambda: Image.new('L', (5, 5), 255)) is made
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', print_type='qrcode_text', code_text='https://example.com/kept', image_rotation='90', image_crop='1', text=TEXT)
    with client.application.test_request_context():
        create_label_from_request(data).generate()
        kept = label_module._CODES._images
        code = next(image for key, image in kept.items() if key[1] == 'https://example.com/kept')
        before = code.tobytes()
        create_label_from_request(data).generate()
        assert code.tobytes() == before


def test_numbers_of_blanks_are_printed_as_written(tmp_path):
    client = make_client(tmp_path)
    data = {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Price}} {{blank:Qty}} {{blank:Big}} {{blank:N}}'}])}
    with client.application.test_request_context():
        label = create_label_from_request(dict(data, blanks='{"Price": 2.50, "Qty": 3.0, "Big": 1e21, "N": 7}'))
        label.process_templates()
    assert label.text[0]['text'] == '2.50 3.0 1e21 7'


def test_gray_preview_is_made_without_looking_at_each_pixel(tmp_path, monkeypatch):
    # Whether red or color is chosen says it: a large label needed several
    # copies of its pixels to find out again
    def split(self):
        raise AssertionError('split')
    monkeypatch.setattr(Image.Image, 'split', split)
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=TEXT))
    assert response.status_code == 200 and Image.open(io.BytesIO(response.data)).mode == 'L'


def test_text_below_a_code_on_endless_tape_is_fitted_once(tmp_path, monkeypatch):
    # Its room does not depend on the distance
    from app.labeldesigner import label as label_module
    fits = []
    original = label_module.SimpleLabel._shrink_text_to_fit
    monkeypatch.setattr(label_module.SimpleLabel, '_shrink_text_to_fit', lambda self, *args: fits.append(1) or original(self, *args))
    client = make_client(tmp_path)
    data = {'label_size': '62', 'print_type': 'qrcode_text', 'orientation': 'standard', 'code_text': 'https://example.com',
            'shrink_text': '1', 'image_fit': '0',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '120', 'text': 'A very long line of text that needs shrinking to fit'}])}
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert len(fits) == 1


def test_large_label_as_plain_form(tmp_path):
    # Werkzeug limits only the fields of multipart forms to
    # MAX_FORM_MEMORY_SIZE, a plain form only to MAX_CONTENT_LENGTH. The
    # label designer sends previews without files as plain forms.
    client = make_client(tmp_path)
    lines = [{'font': 'DejaVu Sans,Book', 'size': '10', 'text': 'Ж' * 9000} for _ in range(10)]
    body = urlencode({'label_size': '62', 'print_type': 'text', 'text': json.dumps(lines, ensure_ascii=False)})
    assert len(body) > client.application.config['MAX_FORM_MEMORY_SIZE']
    response = client.post('/labeldesigner/api/preview', data=body, content_type='application/x-www-form-urlencoded')
    assert response.status_code == 200


def test_image_label_with_many_large_blanks_is_refused_without_putting_them_together(tmp_path):
    # Whether the label has text was told by its text with all values put
    # together: 800 blanks of 100 kB were 80 MB before any limit
    import tracemalloc
    client = make_client(tmp_path)
    data = {'label_size': '62x100', 'print_type': 'image', 'blanks': json.dumps({'a': 'x' * 100000}),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '10', 'text': '{{blank:a}}' * 800}])}
    with client.application.test_request_context():
        tracemalloc.start()
        try:
            with pytest.raises(ValueError, match='Text is too long'):
                create_label_from_request(data, image=Image.new('L', (10, 10), 0)).generate()
            assert tracemalloc.get_traced_memory()[1] < 20_000_000
        finally:
            tracemalloc.stop()


FILL_LINES = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '60', 'text': 'Kitchen'},
                         {'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'spices, top row'}])


@pytest.mark.parametrize('settings', [
    # Its widest line as wide as the tape
    {'label_size': '62'},
    # Along the tape, as high as the tape
    {'label_size': '62', 'orientation': 'rotated'},
    # As large as fits both ways
    {'label_size': '29x90'},
    # Below a code, at the distance that suits the text as printed
    {'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'https://example.com'},
])
def test_text_fills_the_label(tmp_path, settings):
    # All lines by the same factor, they keep the ratio of their sizes
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=FILL_LINES, text_fit='fill', **settings)
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.generate()
        assert label.warnings == []
        sizes = [label._line_size(line) for line in label.text]
        assert sizes[0] > 60 and sizes[1] == int((sizes[0] + 0.5) * 40 / 60)
        if settings.get('print_type') == 'qrcode_text':
            assert label.image_text_gap == max(round(0.4 * sizes[0]), 20)
        else:
            # Within the margins, one size more does not fit
            def fits(size):
                lines = [dict(line, size=str(int((size + 0.5) * int(line['size']) / 60))) for line in json.loads(FILL_LINES)]
                sized = create_label_from_request(dict(data, text_fit='none', text=json.dumps(lines)))
                sized.process_templates()
                textsize = sized._compute_bbox(sized._draw_text(None, []))
                max_width, max_height = sized._text_space(sized._width, sized._height, 0, 0, 0)
                return (max_width is None or textsize[2] <= max_width) and (max_height is None or textsize[3] <= max_height)
            assert fits(sizes[0]) and not fits(sizes[0] + 1)


def test_text_fit_and_the_shrink_text_of_before(tmp_path):
    # text_fit wins, without it shrink_text tells shrink or as set
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='29x90', text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '80', 'text': 'A line that is far too long for this label, by far'}]))
    with client.application.test_request_context():
        def printed(**values):
            label = create_label_from_request(dict(data, **values))
            label.generate()
            return label._line_size(label.text[0]), bool(label.warnings)
        assert printed() == printed(text_fit='none') == (80, True)
        shrunk = printed(shrink_text='1')
        assert shrunk == printed(text_fit='shrink') and shrunk[0] < 80 and not shrunk[1]
        assert printed(shrink_text='1', text_fit='none') == (80, True)
    response = client.post('/labeldesigner/api/preview', data=dict(data, text_fit='grow'))
    assert response.status_code == 400 and response.get_json()['message'] == 'text_fit must be none, shrink or fill'


def test_lines_that_came_out_empty_fill_nothing(tmp_path):
    # Their height alone grew to the largest size, a long piece of empty tape
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', text_fit='fill', blanks=json.dumps({'Name': '', 'Note': ''}),
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Name}}'},
                                 {'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Note}}'}]))
    with client.application.test_request_context():
        label = create_label_from_request(data)
        image = label.generate()
    assert [label._line_size(line) for line in label.text] == [40, 40] and image.height < 300


def test_fill_measures_each_size_once(tmp_path, monkeypatch):
    # The distance to a code is searched, each size is measured once for all
    # distances tried. With a distance set, the text is filled in once.
    from app.labeldesigner import label as label_module
    measured, filled = [], []
    draw, fill = label_module.SimpleLabel._draw_text, label_module.SimpleLabel._fill_room
    monkeypatch.setattr(label_module.SimpleLabel, '_draw_text', lambda self, img=None, *args: (measured.append(self._text_scale) if img is None else None) or draw(self, img, *args))
    monkeypatch.setattr(label_module.SimpleLabel, '_fill_room', lambda self, *args, **bounds: filled.append(args) or fill(self, *args, **bounds))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', orientation='rotated', print_type='qrcode_text', code_text='https://example.com',
                text_fit='fill', text=FILL_LINES)
    with client.application.test_request_context():
        create_label_from_request(data).generate()
        fills = [scale for scale in measured if scale != 1.0]
        assert len(fills) == len(set(fills)) and len(filled) > 1
        filled.clear()
        create_label_from_request(dict(data, image_text_gap='20')).generate()
        assert len(filled) == 1


def test_fill_at_other_distances_looks_only_between_the_sizes_known(tmp_path, monkeypatch):
    # Each distance tried searched all sizes again, dozens of layouts for a
    # preview
    from app.labeldesigner import label as label_module
    bounds = []
    fill = label_module.SimpleLabel._fill_room

    def recorded(self, *args, at_least=1, at_most=label_module.MAX_FILL_SIZE):
        bounds.append((at_least, at_most))
        return fill(self, *args, at_least=at_least, at_most=at_most)
    monkeypatch.setattr(label_module.SimpleLabel, '_fill_room', recorded)
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '0',
            'image_position': 'above', 'text_fit': 'fill', 'text': FILL_LINES}
    with client.application.test_request_context():
        create_label_from_request(data).generate()
    assert len(bounds) > 2 and all(bound != (1, label_module.MAX_FILL_SIZE) for bound in bounds[1:])


def test_code_of_empty_blanks_and_spaces_takes_the_text(tmp_path, monkeypatch):
    from app.labeldesigner import label as label_module
    coded = []
    original = label_module._qr_image
    monkeypatch.setattr(label_module, '_qr_image', lambda data, *args: coded.append(data) or original(data, *args))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type='QR', code_text='{{blank:A}} {{blank:B}}',
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert coded == ['Shelf']


def test_empty_text_fills_nothing(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text_fit='fill', text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': ' '}]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200


def preview_of_lines(client, data, lines):
    response = client.post('/labeldesigner/api/preview', data=dict(data, text=json.dumps(lines)))
    assert response.status_code == 200
    return pixels(response.data)


@pytest.mark.parametrize('fit', ['none', 'shrink', 'fill'])
def test_an_empty_last_line_leaves_the_text_as_it_is(tmp_path, fit):
    # It made the line above it as high as a line with room below it. Text
    # that just fitted along the tape next to a code was shrunk, also when
    # it was to fill the label.
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', orientation='rotated', print_type='qrcode_text', code_text='https://example.com',
                margin_top='24', margin_bottom='24', margin_left='35', margin_right='35', border_thickness='0', text_fit=fit)
    line = {'font': 'DejaVu Serif,Book', 'size': '20', 'align': 'left', 'text': '12345678901234567890'}
    lines = [line] * 31
    assert preview_of_lines(client, data, lines + [dict(line, text='')]) == preview_of_lines(client, data, lines)
    with client.application.test_request_context():
        label = create_label_from_request(dict(data, text=json.dumps(lines + [dict(line, text='')])))
        label.generate()
        assert label._line_size(label.text[0]) == 20


def test_empty_lines_at_the_end_take_no_spacing_and_blank_lines_stay(tmp_path):
    # Across the tape the label is as long as the text: an empty last line
    # added the room below the line above it and its spacing. Empty lines
    # before the last one are blank lines.
    client = make_client(tmp_path)
    line = {'font': 'DejaVu Serif,Book', 'size': '40', 'align': 'left', 'line_spacing': '150', 'text': '12345'}
    lines = [line] * 3
    empty = dict(line, text=' ')
    size, image = preview_of_lines(client, EXAMPLE_FORMDATA, lines)
    assert preview_of_lines(client, EXAMPLE_FORMDATA, lines + [empty]) == (size, image)
    taller = preview_of_lines(client, EXAMPLE_FORMDATA, lines + [empty, empty])[0]
    assert taller[0] == size[0] and taller[1] > size[1] + 40
    # A to-do box or white on black shows on the label
    for shown in (dict(empty, checkbox=True), dict(empty, inverted=True)):
        assert preview_of_lines(client, EXAMPLE_FORMDATA, lines + [shown])[0][1] > size[1]


@pytest.mark.parametrize('text', ['', 'ace'])
def test_the_box_of_the_last_to_do_line_is_on_the_label(tmp_path, text):
    # Only the text of the last line was measured: the box in front of no
    # text or of lower letters was cut off at the end of the label
    client = make_client(tmp_path)
    line = {'font': 'DejaVu Serif,Book', 'size': '40', 'align': 'left', 'checkbox': True, 'text': 'Milk'}
    data = dict(EXAMPLE_FORMDATA, border_thickness='0', text=json.dumps([line, dict(line, text=text)]))
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    with Image.open(io.BytesIO(response.data)) as image:
        ink = image.convert('L').point(lambda v: 255 if v < 128 else 0).getbbox()
        assert ink[3] == image.height - int(data['margin_bottom'])


def test_blanks_count_toward_the_text_limit(tmp_path):
    # The text is short, the values make it too long
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:A}}' * 900, 'size': '10'}]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    response = client.post('/labeldesigner/api/preview', data=dict(data, blanks=json.dumps({'A': 'x' * 20})))
    assert response.status_code == 413
    assert response.get_json()['message'] == 'Text is too long'


@pytest.mark.parametrize('blanks', ['Garage', '["Garage"]', '{"Room": ["Garage"]}', '{"Room": null}', '{"Room": true}'])
def test_blanks_must_be_an_object_of_texts_or_numbers(tmp_path, blanks):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Room}}', 'size': '40'}]), blanks=blanks)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'blanks must be a JSON object with a text or number for each blank, e.g. {"Room": "Garage"}'


def _auto_orientation_label(tmp_path, size, **values):
    client = make_client(tmp_path)
    data = {'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '1', 'orientation': 'standard', 'text': '[]',
            'margin_top': '0', 'margin_bottom': '0', 'margin_left': '0', 'margin_right': '0', **values}
    files = {'image': _upload(Image.new('L', size, 'black'), 'image.png')}
    with client.application.test_request_context():
        return create_label_from_request(data, files)


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_portrait_image_on_die_cut_label_is_turned(tmp_path, orientation):
    # A 4 x 6 in shipping label on a 103 x 164 mm label uses the full label
    label = _auto_orientation_label(tmp_path, (1200, 1800), label_size='103x164', orientation=orientation)
    assert (label._width, label._height) == (1200, 1822)
    assert label.label_orientation == LabelOrientation.ROTATED


def test_landscape_image_on_die_cut_label_is_turned(tmp_path):
    label = _auto_orientation_label(tmp_path, (1800, 1200), label_size='103x164', orientation='rotated')
    assert (label._width, label._height) == (1822, 1200)
    assert label.label_orientation == LabelOrientation.STANDARD


@pytest.mark.parametrize('values', [
    # Without auto-fit the chosen orientation is kept
    {'image_fit': '0'},
    # Labels with text keep their layout
    {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Text', 'size': '40'}])},
    # Endless labels have no fixed length to fit
    {'label_size': '62'},
])
def test_orientation_is_kept(tmp_path, values):
    label = _auto_orientation_label(tmp_path, (1200, 1800), **{'label_size': '103x164', **values})
    assert label.label_orientation == LabelOrientation.STANDARD


def test_webhook_turns_images_on_die_cut_labels(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    labels = []
    monkeypatch.setattr(routes.PrinterQueue, 'add_label_to_queue', lambda self, label, **kwargs: labels.append(label))
    monkeypatch.setattr(routes.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    images = [base64.b64encode(_upload(Image.new('L', size, 'black'), 'image.png').stream.read()).decode('ascii')
              for size in ((1200, 1800), (1800, 1200))]
    payload = {'password': 'secret', 'label_size': '103x164', 'model': 'QL-1100', 'orientation': 'standard', 'images': images}
    assert client.post('/labeldesigner/api/webhook/print', json=payload).get_json() == {'success': True, 'count': 2}
    assert [(label._width, label._height, label.label_orientation) for label in labels] == [
        (1200, 1822, LabelOrientation.ROTATED), (1822, 1200, LabelOrientation.STANDARD)]


@pytest.mark.parametrize('size, orientation, expected', [
    # Die-cut labels in standard orientation were shown turned by 90 degrees
    ('62x29', 'standard', (696, 271)),
    ('62x29', 'rotated', (271, 696)),
    ('103x164', 'standard', (1822, 1200)),
])
def test_preview_shows_the_label_upright(tmp_path, size, orientation, expected):
    # The preview shows the label the way it is read, as it is printed
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': size, 'orientation': orientation})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).size == expected


def test_invalid_input_is_logged_without_traceback(tmp_path, caplog):
    # A missing font size is an input error, not a crash
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Text', 'size': ''}])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Font size is required'
    refused = [r for r in caplog.records if 'refused' in r.getMessage()]
    assert [(r.levelname, r.getMessage(), r.exc_info) for r in refused if r.levelname != 'DEBUG'] == [
        ('WARNING', 'Preview refused: Font size is required', None)]
    assert not any(r.levelname == 'ERROR' for r in caplog.records)


def test_label_size_the_printer_cannot_print_is_refused(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': '103x164', 'model': 'QL-800'})
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The label size 103 x 164 mm die-cut (DK-11247 / DK-1247) cannot be printed on a QL-800'

    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image], 'label_size': '103x164', 'model': 'QL-800'})
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The label size 103 x 164 mm die-cut (DK-11247 / DK-1247) cannot be printed on a QL-800'


@pytest.mark.parametrize('model', ['QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W'])
def test_600_dpi_is_refused_for_the_ql1100_series_and_ptouch_printers(tmp_path, model):
    # Brother's command reference of the QL-1100 series lists 300 dpi only
    # (#103), the P-touch printers print at 180 dpi
    client = make_client(tmp_path)
    label_size = 'pt12' if model.startswith('PT-') else '62'
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': label_size, 'model': model, 'high_res': '1'})
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'

    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image], 'label_size': label_size, 'model': model, 'high_res': 1})
    assert response.status_code == 400
    assert response.get_json()['message'] == f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'

    # At normal resolution they print
    data['high_res'] = '0'
    assert client.post('/labeldesigner/api/print', data=data).get_json() == {'success': True}


@pytest.mark.parametrize('values, warning', [
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A' * 40, 'size': '60'}])},
     'The text does not fit on the label, part of it is cut off.'),
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Fits', 'size': '60'}])}, None),
    ({'print_type': 'image', 'image_fit': '0', 'image_scaling_factor': '400', 'text': '[]'},
     'The image does not fit on the label, part of it is cut off.'),
])
def test_preview_warns_about_content_that_does_not_fit(tmp_path, values, warning):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update(values)
    if data['print_type'] == 'image':
        data['image'] = _upload(Image.new('L', (400, 100), 'black'), 'wide.png')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') == warning


@pytest.mark.parametrize('text_fit', ['shrink', 'fill'])
@pytest.mark.parametrize('values, warning', [
    # Margins that leave the text no room: made as small as it gets it was a
    # dot in the margin without a warning
    ({'print_type': 'text', 'label_size': '62x29', 'margin_top': '140', 'margin_bottom': '140'}, 'The text does not fit on the label at any size.'),
    ({'label_size': '62x29', 'image_fit': '0'}, None),
    ({'label_size': '62', 'image_fit': '1'}, None),
])
def test_text_without_room_at_any_size_is_warned_about(tmp_path, text_fit, values, warning):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', code_text='https://example.com', qrcode_size='6', text_fit=text_fit,
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili', 'size': '160'}]))
    response = client.post('/labeldesigner/api/preview', data=dict(data, **values))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') == warning


@pytest.mark.parametrize('label_size, orientation', [('29x90', 'standard'), ('17x54', 'standard'), ('d24', 'standard'), ('62x29', 'standard'),
                                                     ('62x29', 'rotated'), ('62x100', 'standard'), ('62x100', 'rotated'), ('60x86', 'rotated')])
def test_a_code_fitted_next_to_text_leaves_the_text_room(tmp_path, label_size, orientation):
    # Fitted to the die-cut label it took the whole label, the text was not
    # there: a code with text, as the label designer starts it
    client = make_client(tmp_path)
    data = {'label_size': label_size, 'orientation': orientation, 'print_type': 'qrcode_text', 'code_text': 'https://example.com',
            'image_fit': '1', 'text_fit': 'shrink', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili', 'size': '60'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    other = client.post('/labeldesigner/api/preview', data=dict(data, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Beans', 'size': '60'}])))
    assert pixels(other.data) != pixels(response.data)


@pytest.mark.parametrize('text_fit', ['shrink', 'fill'])
@pytest.mark.parametrize('label_size, image_fit', [('62', '1'), ('62x100', '0'), ('29x90', '0')])
def test_the_text_is_laid_out_once_per_set_of_sizes(tmp_path, monkeypatch, text_fit, label_size, image_fit):
    # Fitted next to a code at several distances, the same sizes came out
    # again and were laid out again
    from app.labeldesigner import label as label_module
    sizes = []
    draw = label_module.SimpleLabel._draw_text

    def counted(self, img=None, *args, **kwargs):
        if img is None:
            sizes.append(tuple(self._line_size(line) for line in self.text))
        return draw(self, img, *args, **kwargs)
    monkeypatch.setattr(label_module.SimpleLabel, '_draw_text', counted)
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size=label_size, print_type='qrcode_text', code_text='https://example.com', image_fit=image_fit, qrcode_size='6',
                text_fit=text_fit, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili con carne', 'size': '160'}]))
    with client.application.test_request_context():
        create_label_from_request(data).generate()
    assert sizes and len(sizes) == len(set(sizes))


def _ink_box(png: bytes):
    """Bounding box of everything that is not white."""
    image = Image.open(io.BytesIO(png)).convert('L')
    return image.size, ImageOps.invert(image).getbbox()


@pytest.mark.parametrize('values', [
    # Too wide for 62 mm endless
    {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A' * 40, 'size': '60'}])},
    # Too high for a 62 x 29 mm die-cut label
    {'label_size': '62x29', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': f'Line {n}', 'size': '60'} for n in range(6)])},
    # Too high across 62 mm endless when rotated
    {'orientation': 'rotated', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': f'Line {n}', 'size': '80'} for n in range(10)])},
])
def test_text_that_does_not_fit_is_shrunk(tmp_path, values):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, border_thickness='0', **values)
    assert client.post('/labeldesigner/api/preview', data=data).headers.get('X-Label-Warning')
    response = client.post('/labeldesigner/api/preview', data=dict(data, shrink_text='1'))
    assert response.status_code == 200
    assert 'X-Label-Warning' not in response.headers
    (width, height), (left, top, right, bottom) = _ink_box(response.data)
    # Within the margins across the tape, and along it on die-cut labels
    if data['orientation'] == 'standard':
        assert left >= 20 and right <= width - 20
    else:
        assert top >= 12 and bottom <= height - 12
    if data['label_size'] == '62x29':
        assert top >= 12 and bottom <= height - 12


def test_text_that_fits_is_not_shrunk(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Fits', 'size': '60'}]))
    expected = client.post('/labeldesigner/api/preview', data=data).data
    assert client.post('/labeldesigner/api/preview', data=dict(data, shrink_text='1')).data == expected


def test_shrunk_text_is_the_same_on_every_copy(tmp_path):
    # The printer generates the same label once per copy
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', shrink_text='1', text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A' * 40, 'size': '60'}]))
    with client.application.test_request_context('/labeldesigner/api/preview', method='POST', data=data):
        label = create_label_from_request(data)
        first = label.generate()
        assert label.generate().tobytes() == first.tobytes()


@pytest.mark.parametrize('orientation, text, qrcode_size', [
    # Below the code, a line of 120 dots is too high
    ('standard', 'Chili con carne', '6'),
    # Beside the code, too wide
    ('rotated', 'Chili', '5'),
])
def test_automatic_gap_follows_the_shrunk_text(tmp_path, orientation, text, qrcode_size):
    # The distance is two fifths of the text as printed, not of 120 dots.
    # The larger distance took room from the text, which came out smaller
    # than needed.
    client = make_client(tmp_path)
    data = {'label_size': '29x90', 'print_type': 'qrcode_text', 'orientation': orientation, 'code_text': 'https://example.com',
            'qrcode_size': qrcode_size, 'image_fit': '0', 'shrink_text': '1',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '120'}])}
    with client.application.test_request_context():
        label = create_label_from_request(data)
        image = label.generate()
        assert label.generate().tobytes() == image.tobytes()
        printed = label._line_size(label.text[0])
        assert 1 < printed < 120 and label.warnings == []
        assert label.image_text_gap == max(round(0.4 * printed), 2 * int(qrcode_size))
        # The same as the text of that size from the start
        unshrunk = create_label_from_request(dict(data, shrink_text='0', text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': str(printed)}])))
        assert unshrunk.generate().tobytes() == image.tobytes()
        assert unshrunk.warnings == []
        # Larger than the text shrunk to fit at the distance of 120 dots
        before = create_label_from_request(dict(data, image_text_gap=str(max(48, 2 * int(qrcode_size)))))
        before.generate()
        assert printed > before._line_size(before.text[0])


@pytest.mark.parametrize('orientation, text, qrcode_size', [('standard', 'Chili con carne', '6'), ('rotated', 'Chili', '5')])
def test_distance_next_to_shrunk_text_is_found_quickly(tmp_path, monkeypatch, orientation, text, qrcode_size):
    # Each fit narrows the range from both sides, it took 5 and 7 fits to
    # halve it. It is still the smallest distance the fitted text keeps to.
    from app.labeldesigner import label as label_module
    fits = []
    original = label_module.SimpleLabel._shrink_text_to_fit
    monkeypatch.setattr(label_module.SimpleLabel, '_shrink_text_to_fit', lambda self, *args: fits.append(1) or original(self, *args))
    client = make_client(tmp_path)
    data = {'label_size': '29x90', 'print_type': 'qrcode_text', 'orientation': orientation, 'code_text': 'https://example.com',
            'qrcode_size': qrcode_size, 'image_fit': '0', 'shrink_text': '1',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '120'}])}
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.generate()
        assert len(fits) <= 4
        closer = create_label_from_request(dict(data, image_text_gap=str(label.image_text_gap - 1)))
        closer.generate()
        assert max(round(0.4 * closer._line_size(closer.text[0])), 2 * int(qrcode_size)) > label.image_text_gap - 1


@pytest.mark.parametrize('gap, dots', [(12, 12), (12.0, 12), ('12', 12), (' 7 ', 7), ('', None), (None, None)])
def test_image_text_gap_as_text_or_number(gap, dots):
    # Saved labels and JSON may have it as a number, e.g. 12.0
    from app.labeldesigner.routes import _image_text_gap
    assert _image_text_gap(gap) == dots


@pytest.mark.parametrize('gap', [12.5, True, '12.0'])
def test_image_text_gap_of_a_number_must_be_whole(gap):
    from app.labeldesigner.routes import _image_text_gap
    with pytest.raises(ValueError, match='image_text_gap must be a whole number of dots or empty'):
        _image_text_gap(gap)


@pytest.mark.parametrize('gap', ['2.5', 'abc'])
def test_image_text_gap_must_be_a_whole_number(tmp_path, gap):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', code_text='https://example.com', text=TEXT, image_text_gap=gap)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'image_text_gap must be a whole number of dots or empty'


def _image_and_text_boxes(png: bytes):
    """The boxes of a mid-gray image and of the black text on a label. The
    edges of the text have a few pixels of the same gray, they are left
    out."""
    from PIL import ImageFilter
    image = Image.open(io.BytesIO(png)).convert('L')
    gray = image.point(lambda v: 255 if v == 128 else 0).filter(ImageFilter.MinFilter(5)).getbbox()
    return (gray[0] - 2, gray[1] - 2, gray[2] + 2, gray[3] + 2), image.point(lambda v: 255 if v < 100 else 0).getbbox()


@pytest.mark.parametrize('side', ['above', 'below', 'left', 'right'])
@pytest.mark.parametrize('label_size, orientation', [('60x86', 'standard'), ('60x86', 'rotated'), ('62', 'standard'), ('62', 'rotated')])
def test_the_image_is_on_the_side_of_the_text_chosen(tmp_path, label_size, orientation, side):
    # It was above the text across the tape and left of it along the tape,
    # e.g. a photo left of the name on a landscape name badge was not possible
    client = make_client(tmp_path)
    data = {'label_size': label_size, 'orientation': orientation, 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0',
            'image_position': side, 'text_fit': 'shrink', 'margin_top': '20', 'margin_bottom': '20', 'margin_left': '20', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Erika Mustermann', 'size': '60'}]),
            'image': _upload(Image.new('L', (200, 120), 128), 'photo.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    gap = int(response.headers['X-Label-Image-Text-Gap'])
    width, height = Image.open(io.BytesIO(response.data)).size
    image, text = _image_and_text_boxes(response.data)
    assert (image[2] - image[0], image[3] - image[1]) == (200, 120)
    distance = {'above': text[1] - image[3], 'below': image[1] - text[3], 'left': text[0] - image[2], 'right': image[0] - text[2]}[side]
    assert distance >= gap
    # In the middle across, at the margin on its side
    if side in ('above', 'below'):
        assert abs(image[0] + image[2] - width) <= 1
        assert (image[1] == 20) if side == 'above' else (image[3] == height - 20)
    else:
        assert abs(image[1] + image[3] - height) <= 1
        assert (image[0] == 20) if side == 'left' else (image[2] == width - 20)
    # Endless tape grows along to hold both, side by side not one after the other
    if label_size == '62':
        along = width if orientation == 'rotated' else height
        side_by_side = (side in ('left', 'right')) == (orientation == 'rotated')
        extent = (lambda box: box[2] - box[0]) if orientation == 'rotated' else (lambda box: box[3] - box[1])
        # The margins and the room at the edges of the letters
        if side_by_side:
            assert extent(image) + gap + extent(text) + 40 <= along <= extent(image) + gap + extent(text) + 40 + 20
        else:
            assert max(extent(image), extent(text)) + 40 <= along <= max(extent(image), extent(text)) + 40 + 20


@pytest.mark.parametrize('orientation, side', [('standard', 'left'), ('standard', 'right'), ('rotated', 'above'), ('rotated', 'below')])
def test_an_image_fitted_beside_the_text_across_endless_tape_takes_half_of_it(tmp_path, orientation, side):
    # Fitted to the label it filled the tape across, the text had no room
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': orientation, 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '1', 'image_position': side,
            'text_fit': 'shrink', 'margin_top': '20', 'margin_bottom': '20', 'margin_left': '20', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '80'}]), 'image': _upload(Image.new('L', (100, 100), 128), 'square.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    image, text = _image_and_text_boxes(response.data)
    assert abs((image[2] - image[0]) - (696 - 40) // 2) <= 6 and text


@pytest.mark.parametrize('text_fit, warning', [('none', 'The text does not fit next to the code, it overlaps it.'), ('shrink', None)])
def test_text_over_the_code_is_warned_about(tmp_path, text_fit, warning):
    # Drawn as set over the code, it reported that everything fits
    client = make_client(tmp_path)
    data = {'label_size': '60x86', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '1', 'image_position': 'right',
            'text_fit': text_fit, 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili con carne', 'size': '70'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') == warning


@pytest.mark.parametrize('side', ['left', 'right'])
def test_text_beside_the_image_is_in_the_middle_between_the_margins(tmp_path, side):
    # The margins left and right were taken as equal, wider text started at
    # the edge of the label
    client = make_client(tmp_path)
    data = {'label_size': '60x86', 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0', 'image_position': side, 'text_fit': 'none',
            'margin_top': '20', 'margin_bottom': '20', 'margin_left': '120', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}]), 'image': _upload(Image.new('L', (200, 120), 128), 'photo.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    width = Image.open(io.BytesIO(response.data)).width
    image, text = _image_and_text_boxes(response.data)
    room = (image[2] + int(response.headers['X-Label-Image-Text-Gap']), width - 20) if side == 'left' else (120, image[0] - int(response.headers['X-Label-Image-Text-Gap']))
    assert abs((text[0] + text[2]) - (room[0] + room[1])) <= 12
    # Wider than the room it starts at the margin
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A very long line of text', 'size': '60'}])
    data['image'] = _upload(Image.new('L', (200, 120), 128), 'photo.png')
    response = client.post('/labeldesigner/api/preview', data=data)
    image, text = _image_and_text_boxes(response.data)
    assert text[0] >= (image[2] if side == 'left' else 120)


def test_text_filling_endless_tape_keeps_the_label_within_its_size(tmp_path, monkeypatch):
    # A long line as high as the tape it runs along made a label too large to
    # make, shrunk it worked
    from app.labeldesigner import label as label_module
    monkeypatch.setattr(label_module, 'MAX_LABEL_PIXELS', 4_000_000)
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'text_fit': 'fill', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry ' * 10, 'size': '40'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    width, height = Image.open(io.BytesIO(response.data)).size
    assert width * height <= 4_000_000 and height == 696
    # As large as a label may be, near enough
    assert width * height > 3_000_000


@pytest.mark.parametrize('values, side', [
    ({}, 'above'),
    ({'image_position': 'left'}, 'left'),
    ({'image_position': 'below'}, 'below'),
    ({'orientation': 'rotated'}, 'left'),
    ({'text': '[]'}, None),
])
def test_the_preview_tells_where_the_code_went(tmp_path, values, side):
    # The label designer showed it from its own copy of the rules
    client = make_client(tmp_path)
    data = dict({'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '1', 'qrcode_size': '8',
                 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}])}, **values)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Image-Position') == side


def test_a_qr_code_of_nothing_is_not_printed(tmp_path):
    # Of blanks left empty and with text of blanks left empty, a QR code of
    # nothing was printed without a word, a barcode said so
    client = make_client(tmp_path)
    data = {'label_size': '62', 'print_type': 'qrcode_text', 'code_text': '{{blank:Link}}',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Name}}', 'size': '40'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == 'The QR code is empty'
    # The preview of the label designer shows what is to fill in
    assert client.post('/labeldesigner/api/preview', data=dict(data, mark_blanks='1')).status_code == 200


def test_a_text_label_keeps_its_margins_whatever_side_a_code_had(tmp_path):
    # Kept from a layout with a code, the side moved the text, also over the
    # margins
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'margin_left': '10', 'margin_right': '60', 'margin_top': '20', 'margin_bottom': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}])}
    previews = [pixels(client.post('/labeldesigner/api/preview', data=dict(data, image_position=side)).data) for side in ('', 'above', 'below', 'right')]
    assert all(preview == previews[0] for preview in previews)


@pytest.mark.parametrize('side', ['above', 'below'])
def test_image_and_text_across_are_in_the_middle_between_the_margins(tmp_path, side):
    # Centered on the whole label, they ran over the larger margin
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'print_type': 'image', 'image_mode': 'grayscale', 'image_fit': '0', 'image_position': side,
            'margin_left': '10', 'margin_right': '90', 'margin_top': '20', 'margin_bottom': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}]), 'image': _upload(Image.new('L', (100, 100), 128), 'square.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    width = Image.open(io.BytesIO(response.data)).width
    image, text = _image_and_text_boxes(response.data)
    middle = 10 + (width - 100) / 2
    assert abs((image[0] + image[2]) / 2 - middle) <= 2 and abs((text[0] + text[2]) / 2 - middle) <= 8


def test_a_line_before_blank_lines_at_the_end_is_measured_like_any_other(tmp_path):
    # Measured like the final line, its own letters set its height: the
    # blank lines after it came out lower below "ace" than below "Ace"
    client = make_client(tmp_path)

    def height(lines):
        data = {'label_size': '62', 'text_fit': 'none', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '60'} for text in lines])}
        return Image.open(io.BytesIO(client.post('/labeldesigner/api/preview', data=data).data)).height
    assert height(['ace', '', '']) == height(['Ace', '', ''])
    # Followed only by the empty final line it is measured like the final one
    assert height(['ace', '']) == height(['ace'])


def test_sizes_tried_to_fit_the_text_do_not_push_out_those_drawn(tmp_path):
    # Filling the label tried many sizes in the cache of the fonts drawn
    from app.labeldesigner import label as label_module
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'text_fit': 'fill', 'text': FILL_LINES}
    with client.application.test_request_context():
        label_module._load_font.cache_clear()
        label = create_label_from_request(data)
        label.generate()
    drawn = {label._line_size(line) for line in label.text}
    assert label_module._load_font.cache_info().currsize == len(drawn)
    assert label_module._load_measured_font.cache_info().currsize > len(drawn)
    # Also the font of icons, the size drawn and that of the text
    icons = dict(data, text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '60', 'text': 'Kitchen {{mdi:star}}'}]))
    with client.application.test_request_context():
        label_module._load_font.cache_clear()
        create_label_from_request(icons).generate()
    assert label_module._load_font.cache_info().currsize == 2


def test_only_text_too_long_answers_too_large(tmp_path, monkeypatch):
    # Any message with "too long" in it was taken for a request too large
    from app.labeldesigner import routes
    monkeypatch.setattr(routes, 'create_label_from_request', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('The name is too long for this')))
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=TEXT))
    assert response.status_code == 400


def test_text_filled_into_endless_tape_makes_a_label_of_about_a_meter_at_most(tmp_path):
    # Many short lines across the tape grew the label to 7 m, each line as
    # wide as the tape
    from app.labeldesigner import label as label_module
    client = make_client(tmp_path)
    data = {'label_size': '62', 'text_fit': 'fill', 'margin_top': '20', 'margin_bottom': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'i', 'size': '40'}] * 40)}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).height <= label_module.MAX_FILLED_RATIO * 696 + 40
    # At 600 dpi as long, not half as long
    response = client.post('/labeldesigner/api/preview', data=dict(data, high_res='1'))
    assert label_module.MAX_FILLED_RATIO * 1392 * 0.9 < Image.open(io.BytesIO(response.data)).height <= label_module.MAX_FILLED_RATIO * 1392 + 80


@pytest.mark.parametrize('barcode_type, printed', [('QR', 'The QR code is empty'), ('code128', 'The barcode is empty')])
def test_a_code_of_blanks_still_to_fill_in_is_warned_about(tmp_path, barcode_type, printed):
    # The preview showed a code of their marks, printing it was refused
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type=barcode_type, code_text='{{blank:Link}}', text='[]')
    response = client.post('/labeldesigner/api/preview', data=dict(data, mark_blanks='1'))
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') == 'The code is empty until its blanks are filled in.'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == printed


def test_a_barcode_of_digits_still_to_fill_in_says_so(tmp_path):
    # The marks of its blanks are no digits: the preview failed with the
    # message of the barcode instead
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type='EAN13', code_text='{{blank:EAN}}', text='[]', mark_blanks='1')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == 'The code is empty until its blanks are filled in.'


def test_text_filled_next_to_a_code_searches_the_distance_where_only_the_length_changes(monkeypatch):
    # Along endless tape the rooms differ only in the length the label may
    # grow to, which only filled text has: compared without it, the search
    # ended at once at the largest distance
    from app.labeldesigner import label as label_module
    label = label_module.SimpleLabel(fill_text=True)
    monkeypatch.setattr(label, '_text_space', lambda width, height, img_width, img_height, gap, largest=False: (1000 - gap if largest else None, 100))
    tried = []

    def fill(max_width, max_height, at_least=1, at_most=label_module.MAX_FILL_SIZE):
        tried.append(max_width)
        label._text_scale = max_width / 1000
        label._filled_size = int(max_width)
        return [], (0, 0, 0, 0)
    monkeypatch.setattr(label, '_fill_room', fill)
    monkeypatch.setattr(label, '_gap', lambda img_width, img_height: round(40 * label._text_scale))
    label._shrink_text_next_to_image([], (0, 0, 0, 0), 100, 100, 10, 10)
    assert len(set(tried)) > 2


@pytest.mark.parametrize('values', [{}, {'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '0', 'qrcode_size': '4', 'image_position': 'above'},
                                    {'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '0', 'qrcode_size': '4', 'image_position': 'right'}])
def test_a_first_letter_reaching_left_is_not_cut_off_along_endless_tape(tmp_path, values):
    # The "j" reaches left of where the text starts: on a label growing in
    # width it was cut off at a margin of 0
    client = make_client(tmp_path)
    data = dict({'label_size': '62', 'orientation': 'rotated', 'margin_left': '0', 'margin_right': '0', 'text_fit': 'none',
                 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'jab', 'size': '120'}] * 3)}, **values)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None


def test_flags_are_read_alike_by_all_that_take_them(tmp_path, monkeypatch):
    # true was refused by /api/print and for the marks of a preview, which
    # read them as numbers only
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append((cut, high_res)))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf {{blank:Room}}'}]))
    assert client.post('/labeldesigner/api/print', data=dict(data, print_count='2', cut_once='true')).get_json() == {'success': True}
    assert queued == [(False, False), (True, False)]
    response = client.post('/labeldesigner/api/preview', data=dict(data, mark_blanks='true'))
    assert response.status_code == 200
    assert pixels(response.data) == pixels(client.post('/labeldesigner/api/preview', data=dict(data, mark_blanks='1')).data)


def test_flags_of_the_label_are_read_like_those_of_printing(tmp_path):
    # print read high_res as true, the label made for it refused that, as
    # well as image_fit, image_crop and shrink_text as true or false
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'image', 'image_mode': 'grayscale', 'border_thickness': '0'})

    def preview(**flags):
        response = client.post('/labeldesigner/api/preview', data=dict(data, image=_upload(_red_square(), 'red.pdf'), **flags))
        assert response.status_code == 200, response.get_json()
        return pixels(response.data)
    assert preview(high_res='true', image_fit='false', image_crop='false') == preview(high_res='1', image_fit='0', image_crop='0')
    assert preview(high_res='false', image_fit='true', image_crop='true') == preview(high_res='0', image_fit='1', image_crop='1')
    text = dict(EXAMPLE_FORMDATA, label_size='29x90', text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '80', 'text': 'A line that is far too long for this label, by far'}]))
    shrunk = client.post('/labeldesigner/api/preview', data=dict(text, shrink_text='true'))
    assert shrunk.status_code == 200 and pixels(shrunk.data) == pixels(client.post('/labeldesigner/api/preview', data=dict(text, shrink_text='1')).data)
    response = client.post('/labeldesigner/api/preview', data=dict(text, shrink_text='maybe'))
    assert response.status_code == 400 and response.get_json()['message'] == 'shrink_text must be 0 or 1'


@pytest.mark.parametrize('align', ['left', 'center'])
def test_a_line_reaching_left_has_room_on_a_label_growing_with_it(tmp_path, align):
    # Only the first line was looked at: the tail of a "j" in front of the
    # second line was cut off. Left aligned, the lines were drawn as far
    # left again: at a margin of 0 the "j" lost its tail, else it reached
    # into the margin.
    client = make_client(tmp_path)

    def made(lines, margin):
        data = dict(EXAMPLE_FORMDATA, label_size='62', orientation='rotated', margin_left=str(margin), margin_right='0', border_thickness='0',
                    text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '120', 'text': t, 'align': align} for t in lines]))
        with client.application.test_request_context():
            label = create_label_from_request(data)
            im = label.generate().convert('L')
        ink = im.point(lambda v: 255 if v < 128 else 0).getbbox()
        assert ink[0] >= margin and not label.warnings
        return im.width - margin, ink[2] - ink[0]
    made_alike = {made(lines, margin) for lines in (['jump', 'Ab'], ['Ab', 'jump']) for margin in (0, 50)}
    assert len(made_alike) == 1


@pytest.mark.parametrize('values', [{'text_fit': 'fill', 'text': 'jab', 'size': '300'}, {'text_fit': 'shrink', 'text': 'j' * 29, 'size': '120'}, {'text_fit': 'shrink', 'text': 'jab jab jab', 'size': '200'}])
def test_text_made_to_fit_has_room_for_a_letter_reaching_left(tmp_path, values):
    # Made to fit up to its right edge, a "j" in front reached past the
    # margin: the label told that the text it made to fit was cut off
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'standard', 'margin_left': '0', 'margin_right': '0', 'border_thickness': '0', 'text_fit': values['text_fit'],
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': values['text'], 'size': values['size'], 'align': 'center'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None


@pytest.mark.parametrize('values', [{'image_position': 'above', 'image_text_gap': '0'},
                                    {'image_position': 'left', 'margin_top': '0', 'margin_bottom': '0', 'image_fit': '0', 'qrcode_size': '3'},
                                    {'image_position': 'right', 'margin_top': '0', 'margin_bottom': '0', 'image_fit': '0', 'qrcode_size': '3'}])
def test_text_reaching_above_its_first_line_is_placed_from_its_top(tmp_path, values):
    # An icon reaches above the first line: below a code the text was drawn
    # into it, beside it the text was cut off at the top of a label growing
    # with it
    client = make_client(tmp_path)
    data = dict({'label_size': '62', 'orientation': 'standard', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'border_thickness': '0',
                 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{mdi:lightbulb}} Hello', 'size': '120'}])}, **values)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None


def test_text_as_written_filled_in_and_drawn(tmp_path):
    # A line of a blank left empty: laid out as written, without text once
    # filled in, drawn with its mark in a preview only. Never text on a label
    # of a code only.
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'barcode_type': 'QR', 'code_text': 'https://example.com',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Room}}'}])}
    code = Image.new('1', (10, 10))
    with client.application.test_request_context():
        for marks, drawn in ((True, True), (False, False)):
            label = create_label_from_request(data, mark_blanks=marks)
            label.process_templates()
            assert (label.has_lines(), label.has_text(), label.want_text(code), label.want_text(None)) == (True, False, drawn, True)
        label = create_label_from_request(dict(data, print_type='qrcode', blanks=json.dumps({'Room': 'Garage'})))
        label.process_templates()
        assert (label.has_lines(), label.has_text(), label.want_text(code)) == (False, True, False)


def test_webhook_takes_a_threshold_of_0(tmp_path, monkeypatch):
    # 0 was taken for left out, the threshold was the default
    from app.labeldesigner import routes
    thresholds = []
    convert = routes._convert_image
    monkeypatch.setattr(routes, '_convert_image', lambda img, mode, threshold=70: thresholds.append(threshold) or convert(img, mode, threshold))
    monkeypatch.setattr(routes.PrinterQueue, 'add_label_to_queue', lambda self, label, **kwargs: None)
    monkeypatch.setattr(routes.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = base64.b64encode(_upload(_red_square(), 'image.png').stream.read()).decode('ascii')
    for given, used in ((0, 0), ('0', 0), (None, client.application.config['IMAGE_DEFAULT_BW_THRESHOLD'])):
        payload = {'password': 'secret', 'label_size': '62', 'image_mode': 'black_and_white', 'bw_threshold': given, 'images': [image]}
        assert client.post('/labeldesigner/api/webhook/print', json=payload).get_json() == {'success': True, 'count': 1}
        assert int(thresholds.pop()) == used


def test_image_position_must_be_a_side(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', code_text='https://example.com', text=TEXT, image_position='top')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'image_position must be above, below, left or right'


def test_image_text_gap_null_is_chosen_automatically(tmp_path):
    # Like left out, e.g. in a label saved through the API
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'text': TEXT}
    with client.application.test_request_context():
        labels = [create_label_from_request(dict(data, **gap)) for gap in ({'image_text_gap': None}, {'image_text_gap': ''}, {})]
        images = [label.generate().tobytes() for label in labels]
    assert images[0] == images[1] == images[2]
    assert [label.image_text_gap for label in labels] == [labels[2].image_text_gap] * 3 and labels[2].image_text_gap > 0


def test_default_font_is_selected(tmp_path):
    client = make_client(tmp_path)
    client.application.config['LABEL_DEFAULT_FONT_FAMILY'] = 'DejaVu Serif'
    family, style = client.application.extensions['fonts'].get_default_font()
    html = client.get('/labeldesigner/').get_data(as_text=True)
    assert f'"default_font": "{family},{style}"' in html


def test_label_designer_gets_the_configured_defaults(tmp_path):
    client = make_client(tmp_path)
    client.application.config.update({'LABEL_DEFAULT_MARGIN_LEFT': 0, 'LABEL_DEFAULT_FONT_SIZE': 55, 'LABEL_DEFAULT_ORIENTATION': 'rotated',
                                      'IMAGE_DEFAULT_MODE': 'black_and_white'})
    html = client.get('/labeldesigner/').get_data(as_text=True)
    defaults = json.loads(re.search(r'const DESIGNER_CONFIG = (.*);</script>', html).group(1))['defaults']
    assert (defaults['margin_left'], defaults['font_size'], defaults['orientation']) == (0, 55, 'rotated')
    assert defaults['image_mode'] == 'black_and_white'


def test_text_line_without_font_uses_the_default_font(tmp_path):
    # The label designer always sends a font, requests to the API need not
    client = make_client(tmp_path)
    data = {'text': json.dumps([{'text': 'Hello', 'size': '70'}])}
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    data = {'text': json.dumps([{'text': 'Hello', 'size': '70', 'font': 'DejaVu Sans'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Font must be given as "<family>,<style>": DejaVu Sans'


def test_shared_file_opens_the_label_designer_once(tmp_path):
    # Android posts it from the share menu, possibly with "Origin: null"
    client = make_client(tmp_path)
    png = io.BytesIO()
    Image.new('L', (40, 20), 'black').save(png, format='PNG')
    response = client.post('/labeldesigner/share', headers={'Origin': 'null'},
                           data={'file': (io.BytesIO(png.getvalue()), 'photo.png', 'image/png')})
    assert response.status_code == 303
    location = response.headers['Location']
    assert location.startswith('/labeldesigner/?shared=')
    token = location.split('=', 1)[1]
    shared = client.get('/labeldesigner/share', query_string={'token': token}).get_json()
    assert shared == {'name': 'photo.png', 'type': 'image/png', 'data': base64.b64encode(png.getvalue()).decode('ascii')}
    # Once only
    response = client.get('/labeldesigner/share', query_string={'token': token})
    assert response.status_code == 404
    assert response.get_json()['message'] == 'The shared content is no longer available, share it again.'


@pytest.mark.parametrize('form, expected', [
    ({'url': 'https://example.com/a'}, {'url': 'https://example.com/a'}),
    # Android shares a link as text
    ({'title': 'Example', 'text': 'https://example.com/b'}, {'url': 'https://example.com/b'}),
    ({'text': 'Shelf A-12'}, {'text': 'Shelf A-12'}),
])
def test_shared_link_or_text(tmp_path, form, expected):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/share', data=form)
    token = response.headers['Location'].split('=', 1)[1]
    assert client.get('/labeldesigner/share', query_string={'token': token}).get_json() == expected


def test_sharing_nothing_opens_the_label_designer(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/share', data={})
    assert response.status_code == 303
    assert response.headers['Location'] == '/labeldesigner/'


def test_shared_content_is_kept_for_a_few_items_only(tmp_path):
    client = make_client(tmp_path)
    from app.labeldesigner import routes
    tokens = []
    for n in range(routes.SHARE_MAX_ITEMS + 1):
        response = client.post('/labeldesigner/share', data={'text': f'Item {n}'})
        tokens.append(response.headers['Location'].split('=', 1)[1])
    assert client.get('/labeldesigner/share', query_string={'token': tokens[0]}).status_code == 404
    assert client.get('/labeldesigner/share', query_string={'token': tokens[-1]}).get_json() == {'text': f'Item {routes.SHARE_MAX_ITEMS}'}


def _pdf(*sizes) -> bytes:
    """A PDF with a black page of each size, so the pages can be told apart
    by their aspect ratio."""
    pages = [Image.new('L', size, 'black') for size in sizes]
    pdf = io.BytesIO()
    pages[0].save(pdf, format='PDF', save_all=True, append_images=pages[1:], resolution=100)
    return pdf.getvalue()


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
    assert response.get_json()['message'] == 'A print job must not have more than 100 labels, 3 pages and 40 copies would be 120'


def test_pdf_page_selection_errors_are_reported(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=_pdf_label(pdf_pages='4'))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The PDF has 3 pages, there is no page 4'


def test_pdf_pages_make_several_labels_of_image_labels_only(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    data = _pdf_label(pdf_pages='all', printer='simulation', print_type='text',
                      text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Text', 'size': '40'}]))
    assert client.post('/labeldesigner/api/print', data=data).status_code == 200
    assert len(os.listdir(printer.SIMULATED_LABELS_DIR)) == 1


@pytest.mark.parametrize('values, auto', [
    # An image without text on a die-cut label is turned to fit
    ({}, True),
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'With text', 'size': '40'}])}, False),
    ({'image_fit': '0'}, False),
    ({'label_size': '62'}, False),
])
def test_preview_says_when_the_orientation_is_chosen_automatically(tmp_path, values, auto):
    client = make_client(tmp_path)
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'image_fit': '1', 'label_size': '62x29', 'text': '[]', **values}
    data['image'] = _upload(Image.new('L', (100, 300), 'black'), 'portrait.png')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Orientation') == ('auto' if auto else None)


def test_several_files_are_printed_one_after_the_other(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'image_fit': '1', 'border_thickness': '0', 'text': '[]',
            'printer': 'simulation', 'print_count': '2', 'pdf_pages': 'all'}
    files = [(io.BytesIO(_pdf((300, 200), (200, 300))), 'two.pdf', 'application/pdf'),
             (io.BytesIO(_upload(Image.new('L', (400, 100), 'black'), 'wide.png').stream.read()), 'wide.png', 'image/png')]
    response = client.post('/labeldesigner/api/print', data={**data, 'image': files})
    assert response.status_code == 200, response.get_json()
    names = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    heights = [Image.open(os.path.join(printer.SIMULATED_LABELS_DIR, name)).height for name in names]
    # Both pages of the PDF and the image, twice, collated
    assert len(heights) == 6
    assert heights[:3] == heights[3:]
    assert heights[1] > heights[0] > heights[2]


def test_several_files_count_against_the_print_limit(tmp_path):
    client = make_client(tmp_path)
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'text': '[]', 'printer': 'simulation', 'print_count': '40', 'pdf_pages': 'all',
            'image': [(io.BytesIO(_pdf((300, 200), (200, 300))), 'two.pdf', 'application/pdf'),
                      (io.BytesIO(_pdf((300, 200))), 'one.pdf', 'application/pdf')]}
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'A print job must not have more than 100 labels, 2 files with 3 labels and 40 copies would be 120'


@pytest.mark.parametrize('route', ['print', 'webhook', 'repository'])
@pytest.mark.parametrize('fast', [None, '0', '1'])
def test_fast_printing(tmp_path, monkeypatch, route, fast):
    # Unless printing fast, the printer gives priority to print quality, bit
    # 6 of the media and quality command (ESC i z), which makes it slower
    from app.labeldesigner import printer
    sent = []
    monkeypatch.setattr(printer.PrinterQueue, '_send_raster', lambda self, qlr, *args, **kwargs: sent.append(qlr.data) or '')
    client = make_client(tmp_path)
    options = {} if fast is None else {'fast_printing': fast}
    if route == 'print':
        response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, **options))
    elif route == 'webhook':
        client.application.config['WEBHOOK_PASSWORD'] = 'secret'
        image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
        response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image], 'label_size': '62', **options})
    else:
        response = client.post('/labeldesigner/api/repository/print', data={'name': 'QR-Example.json', **options})
    assert response.get_json()['success'] is True
    data = sent[0]
    flags = data[data.index(b'\x1biz') + 3]
    assert bool(flags & 0x40) == (fast != '1')


@pytest.mark.parametrize('model, label_size, ptouch', [('PT-E550W', 'pt12', True), ('QL-700', '62', False)])
def test_ptouch_printers_get_compressed_lines_and_no_media_type(tmp_path, monkeypatch, model, label_size, ptouch):
    # A PT-E550W hung after the half cut with uncompressed raster lines over
    # Wi-Fi, and with media type 0x00 ("no tape") also over USB
    from app.labeldesigner import printer
    sent = []
    monkeypatch.setattr(printer.PrinterQueue, '_send_raster', lambda self, qlr, *args, **kwargs: sent.append(qlr.data) or '')
    client = make_client(tmp_path, model=model)
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, label_size=label_size, orientation='rotated'))
    assert response.get_json()['success'] is True
    data = sent[0]
    flags = data[data.index(b'\x1biz') + 3]
    # Valid flag of the media type
    assert bool(flags & 0x02) != ptouch
    assert (b'M\x02' in data) == ptouch


@pytest.mark.parametrize('cut_once', ['0', '1'])
def test_copies_on_ptouch_printers_are_one_job_per_batch(tmp_path, monkeypatch, cut_once):
    # As jobs of their own, a PT-E550W fed, half cut and cut the tape for
    # every copy, also with cut_once
    from app.labeldesigner import printer
    sent = []
    monkeypatch.setattr(printer.PrinterQueue, '_send_raster', lambda self, qlr, *args, **kwargs: sent.append(qlr.data) or '')
    client = make_client(tmp_path, model='PT-E550W')
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, label_size='pt12', orientation='rotated', print_count='7', cut_once=cut_once))
    assert response.get_json()['success'] is True
    # Batches of 5 and 2 labels, one job each
    assert len(sent) == 2
    for data, labels in zip(sent, [5, 2]):
        assert data.count(b'\x1b@') == 1
        # The following labels of a job are not its first page
        assert [data[i + 11] for i in range(len(data)) if data.startswith(b'\x1biz', i)] == [0] + [1] * (labels - 1)
        # Half cut, no chain printing
        assert [data[i + 3] for i in range(len(data)) if data.startswith(b'\x1biK', i)] == [0x0c] * labels
        assert data.endswith(b'\x1a')
    cut_every = [[data[i + 3] for i in range(len(data)) if data.startswith(b'\x1biA', i)] for data in sent]
    if cut_once == '1':
        # Half cuts between the labels, the tape is cut after each batch
        assert cut_every == [[5] * 5, [2] * 2]
    else:
        # Cut after every label
        assert cut_every == [[1] * 5, [1] * 2]


def test_ptouch_tape_is_endless(tmp_path):
    # P-touch tapes were taken for round die-cut labels: the label was 1 dot
    # long and the printer stopped after a few millimeters
    client = make_client(tmp_path, model='PT-E550W')
    text = json.dumps([{'text': 'PT-E550W', 'font': 'DejaVu Sans,Book', 'size': 40}])
    response = client.post('/labeldesigner/api/preview?return_format=png', data={
        'printer': 'simulation', 'label_size': 'pt12', 'orientation': 'rotated', 'text': text})
    assert response.status_code == 200
    assert 'X-Label-Warning' not in response.headers
    image = Image.open(io.BytesIO(response.data))
    # Across the tape the printable 70 dots, along it as long as the text
    assert image.height == 70 and image.width > 100


@pytest.mark.parametrize('settings, mode', [
    ({}, 'L'),
    # With red or color chosen it stays in color, even with nothing red on it
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Red', 'size': '40', 'color': 'red'}])}, 'RGB'),
    ({'print_color': 'red'}, 'RGB'),
    # A frame is red only where it is drawn
    ({'border_color': 'red', 'border_thickness': '0'}, 'L'),
    ({'border_color': 'red', 'border_thickness': '2'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'colored'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'red_and_black'}, 'RGB'),
    ({'print_type': 'image', 'image_mode': 'grayscale'}, 'L'),
])
def test_preview_without_red_or_color_is_stored_in_gray(tmp_path, settings, mode):
    # The same pixels in half the bytes, the reference images check them
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Gray', 'size': '40'}]))
    data.update(settings)
    if data.get('print_type') == 'image':
        image = Image.new('L', (200, 100), 255)
        image.paste(0, (50, 25, 150, 75))
        out = io.BytesIO()
        image.save(out, 'PNG')
        data['image'] = (io.BytesIO(out.getvalue()), 'gray.png', 'image/png')
    response = client.post('/labeldesigner/api/preview', data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).mode == mode


def test_codes_are_made_once_while_a_label_is_edited(tmp_path, monkeypatch):
    from app.labeldesigner import label
    made = []
    original = label.QRCode.make
    monkeypatch.setattr(label.QRCode, 'make', lambda self, *args, **kwargs: made.append(1) or original(self, *args, **kwargs))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type='QR', code_text=f'https://example.com/{random.random()}')

    def preview(text, **settings):
        response = client.post('/labeldesigner/api/preview', data=dict(data, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '40'}]), **settings))
        assert response.status_code == 200
        return response.data
    first = preview('Shelf')
    assert preview('Shelf 2') != first and preview('Shelf') == first
    assert len(made) == 1
    # Another color or size is another code
    preview('Shelf', print_color='red')
    preview('Shelf', qrcode_size='5')
    assert len(made) == 3


def test_code_cache_keeps_to_its_pixels():
    from app.labeldesigner import label
    cache = label._ImageCache(100)
    made = []

    def make(color):
        made.append(color)
        return Image.new('L', (6, 6), color)
    for color in (0, 1, 0, 2, 0, 1):
        assert cache.get((color,), lambda: make(color)).getpixel((0, 0)) == color
    # Two of 36 pixels fit, the one used longest ago goes first
    assert made == [0, 1, 2, 1]
