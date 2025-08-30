

from datetime import datetime
import json
import sys
import os
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app
from werkzeug.datastructures import FileStorage
import io
import multiprocessing
from random import choice

UPDATE_IMAGES = False
EXAMPLE_FORMDATA = {
    'print_type': 'text',
    'label_size': 62,
    'orientation': 'standard',
    'print_count': '1',
    'cut_once': '0',
}


def verify_image(response_data, expected_image_path):
    # Compare generated preview with the image in file (if it exists)
    if not UPDATE_IMAGES and os.path.isfile(expected_image_path):
        with open(expected_image_path, 'rb') as f:
            expected_data = f.read()
        if response_data != expected_data:
            raise AssertionError("Generated image does not match expected image")

    # Write image into file
    with open(expected_image_path, 'wb') as f:
        f.write(response_data)

@pytest.fixture
def client():
    my_app = create_app()
    my_app.config['TESTING'] = True
    my_app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 MB
    with my_app.test_client() as client:
        yield client


def image_updating_is_disabled():
    assert not UPDATE_IMAGES


def test_render_frontend(client):
    response = client.get('/labeldesigner/')
    assert response.status_code == 200
    assert b'labeldesigner' in response.data
    assert response.content_type == 'text/html; charset=utf-8'


def test_get_font_styles(client):
    response = client.get('/labeldesigner/api/font/styles')
    assert response.status_code == 200
    assert response.is_json
    data = response.get_json()
    assert 'Bold' in data
    assert 'Bold Italic' in data
    assert 'Italic' in data
    assert 'Book' in data


def test_get_barcodes(client):
    response = client.get('/labeldesigner/api/barcodes')
    assert response.status_code == 200
    assert response.is_json
    assert 'barcodes' in response.get_json()
    offered_barcodes = response.get_json()['barcodes']
    assert 'QR' in offered_barcodes
    assert 'EAN13' in offered_barcodes


def test_generate_preview(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': 'Left',
            'font_size': '60',
            'align': 'left'
        },
        {
            'font_family': 'Droid Sans Mono',
            'font_style': 'Regular',
            'text': '-- LONG MONO TEXT --',
            'font_size': '50',
            'align': 'center'
        }
    ])

    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/preview_simple.png')


def test_generate_preview_inverted(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '!!! LEFT !!!',
            'font_size': '50',
            'align': 'left',
            'font_inverted': 'true'
        },
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '!!! CENTER !!!',
            'font_size': '50',
            'align': 'center',
            'font_inverted': 'true'
        },
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '!!! RIGHT !!!',
            'font_size': '50',
            'align': 'right',
            'font_inverted': 'true'
        },
        {
            'font_family': 'Droid Sans Mono',
            'font_style': 'Regular',
            'text': '-- LONG MONO TEXT --',
            'font_size': '50',
            'align': 'center'
        }
    ])

    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/preview_inverted.png')


def test_generate_preview_rotated(client):
    data = EXAMPLE_FORMDATA.copy()
    data['orientation'] = 'rotated'
    data['text'] = json.dumps([
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': 'Left',
            'font_size': '60',
            'align': 'left'
        },
        {
            'font_family': 'Droid Sans Mono',
            'font_style': 'Regular',
            'text': '-- LONG MONO TEXT --',
            'font_size': '50',
            'align': 'center'
        }
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/preview_rotated.png')


def test_generate_ean13(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'qrcode_text'
    data['barcode_type'] = 'ean13'
    data['text'] = json.dumps([
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '123456789012',
            'font_size': '60',
            'align': 'left'
        },
        {
            'font_family': 'Droid Serif',
            'font_style': 'Bold',
            'text': 'Some example product',
            'font_size': '50',
            'align': 'center'
        }
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/preview_ean13.png')


def test_invalid_ean13(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'qrcode_text'
    data['barcode_type'] = 'ean13'
    data['text'] = json.dumps([
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '1234567890',
            'font_size': '60',
            'align': 'left'
        },
        {
            'font_family': 'Droid Serif',
            'font_style': 'Bold',
            'text': 'Some example product',
            'font_size': '50',
            'align': 'center'
        }
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.is_json
    data = response.get_json()
    assert data['message'] == 'EAN must have 12 digits, not 10.'


def test_generate_qr(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'qrcode_text'
    data['barcode_type'] = 'QR'
    data['text'] = json.dumps([
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '123456789012',
            'font_size': '40',
            'align': 'center'
        },
        {
            'font_family': 'Droid Serif',
            'font_style': 'Bold',
            'text': 'Some example product',
            'font_size': '50',
            'align': 'center'
        }
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/preview_qr.png')


def image_test(client, image_path: str = "tests/demo_image.jpg", rotated: bool = False, fit: bool = False, text: bool = False, image_mode: str = "grayscale"):
    data = EXAMPLE_FORMDATA.copy()
    my_file = FileStorage(
        stream=open(image_path, "rb"),
        filename=os.path.basename(image_path),
        content_type="image/jpeg",
    )
    data['print_type'] = 'image'
    data['image'] = my_file
    data['image_mode'] = image_mode

    if image_mode == "black":
        data['image_bw_threshold'] = '128'

    data['image_fit'] = '1' if fit else '0'
    data['orientation'] = 'rotated' if rotated else 'normal'
    if text:
        data['text'] = json.dumps([
            {
                'font_family': 'DejaVu Sans',
                'font_style': 'Regular',
                'text': 'Test',
                'font_size': '40',
                'align': 'center'
            }
        ])

    expected_img_path = "tests/preview_image" + ("_rotated" if rotated else "") + ("_fit" if fit else "") + ("_text" if text else "") + "_" + image_mode + ".png"

    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, expected_img_path)


def test_image(client):
    image_test(client)


def test_image_fit(client):
    image_test(client, fit=True)


def test_image_rotated(client):
    image_test(client, rotated=True)


def test_image_rotated_fit(client):
    image_test(client, rotated=True, fit=True)


def test_image_with_text(client):
    image_test(client, text=True)


def test_image_with_text_fit(client):
    image_test(client, text=True, fit=True)


def test_image_with_text_rotated(client):
    image_test(client, text=True, rotated=True)


def test_image_with_text_fit_rotated(client):
    image_test(client, text=True, rotated=True, fit=True)


def test_image_color_fit(client):
    image_test(client, image_mode="colored", fit=True)


def test_image_red_and_black_fit(client):
    image_test(client, image_mode="red_and_black", fit=True)


def test_image_black_fit(client):
    image_test(client, image_mode="black", fit=True)


def test_generate_template(client):
    data = EXAMPLE_FORMDATA.copy()
    # Mock current datetime.now
    data['timestamp'] = int(datetime(2023, 3, 18, 12, 15, 30).timestamp())

    data['text'] = json.dumps([
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '{{datetime:%d.%m.%Y %H:%M:%S}} COUNTER: {{counter}}',
            'font_size': '30',
            'align': 'left'
        },
        {
            'font_family': 'DejaVu Sans',
            'font_style': 'Regular',
            'text': '>> {{datetime:Label created at %H:%M on %m/%d/%y}} <<',
            'font_size': '20',
            'align': 'right',
            'font_inverted': True
        }
    ])

    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/template.png')


def test_invalid_data_types(client):
    # Non-integer label_size
    data = EXAMPLE_FORMDATA.copy()
    data['label_size'] = 'sixty-two'
    data['text'] = json.dumps([{'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'Test', 'font_size': '12', 'align': 'center'}])
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


def test_large_input_handling(client):
    # Very large text
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'A' * 20000, 'font_size': '12', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    # 413 = Content Too Large
    assert response.status_code == 413


def test_unsupported_barcode_type(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'qrcode_text'
    data['barcode_type'] = 'UNSUPPORTED'
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': '123456789012', 'font_size': '40', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.is_json
    assert 'message' in response.get_json()


def test_font_not_found(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'NonExistentFont', 'font_style': 'Regular', 'text': 'Test', 'font_size': '12', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.is_json
    assert 'message' in response.get_json()


def test_method_enforcement(client):
    # POST required for preview
    response = client.get('/labeldesigner/api/preview')
    # 405 = Method Not Allowed
    assert response.status_code == 405


def test_template_edge_cases(client):
    # Incomplete template
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': '{{datetime:}}', 'font_size': '32', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/template_incomplete.png')

    # Unknown template variable
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': '{{unknownvar}}', 'font_size': '32', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/template_unknown.png')


def test_concurrent_preview_requests(client):
    data = EXAMPLE_FORMDATA.copy()
    results = []
    def make_request(i: int):
        data['text'] = json.dumps([
            {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': str(i), 'font_size': '32', 'align': 'center'}
        ])
        resp = client.post('/labeldesigner/api/preview', data=data)
        results.append(resp.status_code)
    threads = [multiprocessing.Process(target=make_request, args=(i,)) for i in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert all(code == 200 for code in results)


def test_invalid_image_upload(client):
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


def test_corrupted_image_upload(client):
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

def test_empty_label(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/empty_label.png')


def test_minimal_label(client):
    # Minimum label_size
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'Min', 'font_size': '1', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/minimal_label.png')


def test_unicode_and_special_characters(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'Emoji: 😃', 'font_size': '32', 'align': 'center'},
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'RTL: שלום', 'font_size': '32', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/unicode.png')


def test_security_xss(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': '<script>alert(1)</script>', 'font_size': '32', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    # Should return image with plain text
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/security_xss.png')


@pytest.mark.parametrize('method', ['put', 'delete', 'patch'])
def test_invalid_http_methods(client, method):
    func = getattr(client, method)
    response = func('/labeldesigner/api/preview')
    assert response.status_code == 405


def test_large_number_of_text_blocks(client):
    data = EXAMPLE_FORMDATA.copy()
    ALIGNS = ['center', 'left', 'right']
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': f'--- {i} ---', 'font_size': str(i + 1), 'align': ALIGNS[i % 3], 'font_inverted': bool(i % 2)} for i in range(100)
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/large_label.png')


def test_file_size_limits(client):
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


# Even more edge-case and robustness tests
import time
from flask import Response

def test_extra_fields_ignored(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'Extra', 'font_size': '12', 'align': 'center'}
    ])
    data['unexpected_field'] = 'should be ignored'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/extra_fields.png')


def test_multiple_images_failure(client):
    data = EXAMPLE_FORMDATA.copy()
    file1 = FileStorage(stream=io.BytesIO(b'img1'), filename='img1.jpg', content_type='image/jpeg')
    file2 = FileStorage(stream=io.BytesIO(b'img2'), filename='img2.jpg', content_type='image/jpeg')
    data['print_type'] = 'image'
    data['image'] = [file1, file2]
    data['image_mode'] = 'grayscale'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400


def test_image_mode_edge_case(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'image'
    data['image'] = FileStorage(stream=io.BytesIO(b'img'), filename='img.jpg', content_type='image/jpeg')
    data['image_mode'] = 'not_a_mode'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400


def test_non_existing_label_size(client):
    data = EXAMPLE_FORMDATA.copy()
    data['label_size'] = 62.5
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'Float', 'font_size': '12', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400


def test_non_utf8_encoded_text(client):
    data = EXAMPLE_FORMDATA.copy()
    # Latin-1 encoded string
    text = 'Café'.encode('latin-1')
    data['text'] = text
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400


def test_html_in_text(client):
    data = EXAMPLE_FORMDATA.copy()
    data['text'] = json.dumps([
        {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': '<b>Bold</b>', 'font_size': '12', 'align': 'center'}
    ])
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/html_in_text_plain.png')


def test_missing_optional_fields(client):
    data = {
        'print_type': 'text',
        'label_size': 62,
        'orientation': 'standard',
        'print_count': '1',
        'cut_once': '0',
        'text': json.dumps([
            {'font_family': 'DejaVu Sans', 'font_style': 'Regular', 'text': 'No style', 'font_size': '12', 'align': 'center'}
        ])
    }
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png']

    # Check image
    verify_image(response.data, 'tests/missing_optional_fields.png')

# We cannot test the print functionality without a physical printer
# def test_print_label(client):
#    data = {
#        'print_type': 'text',
#        'label_size': '62',
#        'orientation': 'standard',
#        'text[0][font_family]': 'DejaVu Sans',
#        'text[0][font_style]': 'Regular',
#        'text[0][text]': 'Test',
#        'text[0][font_size]': '12',
#        'text[0][align]': 'center',
#        'print_count': '1',
#        'cut_once': '0',
#    }
#    response = client.post('/labeldesigner/api/print', data=data)
#    assert response.status_code == 200
#    assert response.is_json
#    assert 'success' in response.get_json()
