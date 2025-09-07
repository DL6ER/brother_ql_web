

import io
import os
import sys
import json
import random
import pytest
import unicodedata
import multiprocessing
from typing import Union
from datetime import datetime
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app

UPDATE_IMAGES = False
EXAMPLE_FORMDATA = {
    'print_type': 'text',
    'label_size': 62,
    'orientation': 'standard',
    'print_count': '1',
    'cut_once': '0',
}


class TestLabelDesignerAPI:
    def verify_image(self, response_data: bytes, expected_image_path: str):
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

        # Write image into file
        with open('tests/images/' + expected_image_path, 'wb') as f:
            f.write(response_data)

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
        self.verify_image(response.data, expected_img_path)

    @pytest.fixture(autouse=True)
    def client(self):
        my_app = create_app()
        my_app.config['TESTING'] = True
        my_app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
        with my_app.test_client() as client:
            yield client

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
        self.verify_image(response.data, 'simple.png')

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
        self.verify_image(response.data, 'simple_high_res.png')

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
        self.verify_image(response.data, 'inverted_text.png')

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
        self.verify_image(response.data, 'rotated.png')

    def test_generate_ean13(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['print_type'] = 'qrcode_text'
        data['barcode_type'] = 'ean13'
        data['text'] = json.dumps([
            {
                'font': 'DejaVu Sans,Book',
                'text': '123456789012',
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
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        self.verify_image(response.data, 'barcode_ean13.png')

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
        self.verify_image(response.data, 'qr.png')

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
        self.verify_image(response.data, 'template.png')

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
        self.verify_image(response.data, 'shifted_random.png')

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
        self.verify_image(response.data, 'template_incomplete.png')

        # Unknown template variable
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': '{{unknownvar}}', 'size': '32', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        self.verify_image(response.data, 'template_unknown.png')

    def test_concurrent_preview_requests(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        results = []
        def make_request(i: int):
            data['text'] = json.dumps([
                {'font': 'DejaVu Sans,Book', 'text': str(i), 'size': '32', 'align': 'center'}
            ])
            resp = client.post('/labeldesigner/api/preview', data=data)
            results.append(resp.status_code)
        threads = [multiprocessing.Process(target=make_request, args=(i,)) for i in range(50)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
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
        self.verify_image(response.data, 'empty_label.png')

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
        self.verify_image(response.data, 'minimal_label.png')

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
        self.verify_image(response.data, 'unicode.png')

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
        self.verify_image(response.data, 'security_xss.png')


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
        self.verify_image(response.data, 'red_text.png')

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
        self.verify_image(response.data, 'large_label.png')

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
        self.verify_image(response.data, 'extra_fields.png')

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
        self.verify_image(response.data, 'html_in_text_plain.png')

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
        self.verify_image(response.data, 'missing_optional_fields.png')

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
        self.verify_image(response.data, 'unicode_normalization.png')

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
            {'font': 'Droid Sans,Regular', 'text': 'Item 1 (very small)', 'size': '15', 'align': 'left', 'todo': True},
            {'font': 'Noto Sans,Regular', 'text': 'Item 2', 'size': '50', 'align': 'left', 'todo': True},
            {'font': 'DejaVu Sans,Book', 'text': 'Not an item XX', 'size': '70', 'align': 'right', 'todo': False, 'color': 'red'},
            {'font': 'DejaVu Serif,Bold', 'text': 'Item 3', 'size': '50', 'align': 'left', 'todo': True}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        self.verify_image(response.data, 'todo_list.png')

    # We cannot test the print functionality without a physical printer
    def test_print_label(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        # Set config for printer
        client.application.config['PRINTER_OFFLINE'] = True
        response = client.post('/labeldesigner/api/print', data=data)
        assert response.status_code == 200
        assert response.is_json
        assert 'success' in response.get_json()
