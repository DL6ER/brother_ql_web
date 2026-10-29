import io
import os
import sys
import json
import base64
import random
import pytest
import unicodedata
from typing import Union
from datetime import datetime
from flask.testing import FlaskClient
from PIL import Image, ImageChops, ImageOps
from werkzeug.datastructures import FileStorage
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app
from app.labeldesigner.enums import LabelContent
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


def verify_image(response_data: bytes, expected_image_path: str):
    # Compare generated preview with the image in file (if it exists)
    if not UPDATE_IMAGES and os.path.isfile('tests/images/' + expected_image_path):
        with open('tests/images/' + expected_image_path, 'rb') as f:
            expected_data = f.read()
        if response_data != expected_data:
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
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'NonExistentFont,Book', 'text': 'Test', 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        assert 'message' in response.get_json()

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
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([
            {'font': 'Droid Sand,Book', 'text': 'Item 1 (very small)', 'size': '15', 'align': 'left'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        data = response.get_json()
        assert data['message'] == 'Unknown font family: Droid Sand'

        data['text'] = json.dumps([
            {'font': 'Droid Sans,Non-exist', 'text': 'Item 1 (very small)', 'size': '15', 'align': 'left'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.is_json
        data = response.get_json()
        assert data['message'] == 'Unknown font style: Non-exist for font Droid Sans'

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
        assert "Exception during sending to printer" in json_response['message']
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

    def test_get_status_times_out_without_response(self):
        from app.labeldesigner.printer import get_status
        printer = FakeSlowPrinter(empty_reads=10**6, response=b'')
        with pytest.raises(TimeoutError):
            get_status(printer, timeout=0.1)
