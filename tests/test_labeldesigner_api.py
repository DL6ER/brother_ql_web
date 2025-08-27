

import json
import sys
import os
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app
from werkzeug.datastructures import FileStorage

UPDATE_IMAGES = False
EXAMPLE_FORMDATA = {
    'print_type': 'text',
    'label_size': '62',
    'orientation': 'standard',
    'print_count': '1',
    'cut_once': '0',
}


def verify_image(response_data, expected_image_path):
    # Compare generated preview with the image in file (if it exists)
    if not UPDATE_IMAGES and os.path.isfile(expected_image_path):
        with open(expected_image_path, 'rb') as f:
            expected_data = f.read()
        assert response_data == expected_data
        return

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


def test_index(client):
    response = client.get('/labeldesigner/')
    assert response.status_code == 200
    assert b'labeldesigner' in response.data


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
    assert response.content_type in ['image/png', 'text/plain']

    # Write image into file
    if UPDATE_IMAGES:
        with open('tests/preview_simple.png', 'wb') as f:
            f.write(response.data)

    # Compare generated preview with the image in file
    with open('tests/preview_simple.png', 'rb') as f:
        expected_data = f.read()
    assert response.data == expected_data


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
    assert response.content_type in ['image/png', 'text/plain']

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
    assert response.content_type in ['image/png', 'text/plain']

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
    assert response.content_type in ['image/png', 'text/plain']

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
    assert response.content_type in ['image/png', 'text/plain']

    # Check image
    verify_image(response.data, 'tests/preview_qr.png')


def image_test(client, image_path: str = "tests/demo_image.jpg", rotated: bool = False, fit: bool = False, text: bool = False):
    data = EXAMPLE_FORMDATA.copy()
    my_file = FileStorage(
        stream=open(image_path, "rb"),
        filename=os.path.basename(image_path),
        content_type="image/jpeg",
    )
    data['print_type'] = 'image'
    data['image'] = my_file

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

    expected_img_path = "tests/preview_image" + ("_rotated" if rotated else "") + ("_fit" if fit else "") + ("_text" if text else "") + ".png"

    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png', 'text/plain']

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

# We cannot test the print functionality without a physical printer
# def test_print_text(client):
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
