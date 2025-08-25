

import sys
import os
import pytest
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from app import create_app

UPDATE_IMAGES = False
EXAMPLE_FORMDATA = {
    'print_type': 'text',
    'label_size': '62',
    'orientation': 'standard',
    'text[0][font_family]': 'DejaVu Sans',
    'text[0][font_style]': 'Regular',
    'text[0][text]': 'Test',
    'text[0][font_size]': '40',
    'text[0][align]': 'center',
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
    data['text[0][text]'] = 'Left'
    data['text[0][font_size]'] = '60'
    data['text[0][align]'] = 'left'
    data['text[1][font_family]'] = 'Droid Sans Mono'
    data['text[1][font_style]'] = 'Regular'
    data['text[1][text]'] = '-- LONG MONO TEXT --'
    data['text[1][font_size]'] = '50'
    data['text[1][align]'] = 'center'

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
    data['text[0][font_family]'] = 'DejaVu Sans'
    data['text[0][font_style]'] = 'Regular'
    data['text[0][text]'] = '!!! LEFT !!!'
    data['text[0][font_size]'] = '50'
    data['text[0][align]'] = 'left'
    data['text[0][font_inverted]'] = 'true'

    data['text[1][font_family]'] = 'DejaVu Sans'
    data['text[1][font_style]'] = 'Regular'
    data['text[1][text]'] = '!!! CENTER !!!'
    data['text[1][font_size]'] = '50'
    data['text[1][align]'] = 'center'
    data['text[1][font_inverted]'] = 'true'

    data['text[2][font_family]'] = 'DejaVu Sans'
    data['text[2][font_style]'] = 'Regular'
    data['text[2][text]'] = '!!! RIGHT !!!'
    data['text[2][font_size]'] = '50'
    data['text[2][align]'] = 'right'
    data['text[2][font_inverted]'] = 'true'

    data['text[3][font_family]'] = 'Droid Sans Mono'
    data['text[3][font_style]'] = 'Regular'
    data['text[3][text]'] = '-- LONG MONO TEXT --'
    data['text[3][font_size]'] = '50'
    data['text[3][align]'] = 'center'

    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png', 'text/plain']

    # Check image
    verify_image(response.data, 'tests/preview_inverted.png')


def test_generate_preview_rotated(client):
    data = EXAMPLE_FORMDATA.copy()
    data['orientation'] = 'rotated'
    data['text[0][text]'] = 'Left'
    data['text[0][font_size]'] = '60'
    data['text[0][align]'] = 'left'
    data['text[1][font_family]'] = 'Droid Sans Mono'
    data['text[1][font_style]'] = 'Regular'
    data['text[1][text]'] = '-- LONG MONO TEXT --'
    data['text[1][font_size]'] = '50'
    data['text[1][align]'] = 'center'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png', 'text/plain']

    # Check image
    verify_image(response.data, 'tests/preview_rotated.png')


def test_generate_ean13(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'qrcode_text'
    data['barcode_type'] = 'ean13'
    data['text[0][text]'] = '123456789012'
    data['text[1][font_family]'] = 'Droid Serif'
    data['text[1][font_style]'] = 'Bold'
    data['text[1][text]'] = 'Some example product'
    data['text[1][font_size]'] = '50'
    data['text[1][align]'] = 'center'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png', 'text/plain']

    # Write image into file
    if UPDATE_IMAGES:
        with open('tests/preview_ean13.png', 'wb') as f:
            f.write(response.data)

    # Compare generated preview with the image in file
    with open('tests/preview_ean13.png', 'rb') as f:
        expected_data = f.read()
    assert response.data == expected_data


def test_invalid_ean13(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'qrcode_text'
    data['barcode_type'] = 'ean13'
    data['text[0][text]'] = '1234567890'
    data['text[1][font_family]'] = 'Droid Serif'
    data['text[1][font_style]'] = 'Bold'
    data['text[1][text]'] = 'Some example product'
    data['text[1][font_size]'] = '50'
    data['text[1][align]'] = 'center'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.is_json
    data = response.get_json()
    assert data['message'] == 'EAN must have 12 digits, not 10.'


def test_generate_qr(client):
    data = EXAMPLE_FORMDATA.copy()
    data['print_type'] = 'qrcode_text'
    data['barcode_type'] = 'QR'
    data['text[0][text]'] = '123456789012'
    data['text[1][font_family]'] = 'Droid Serif'
    data['text[1][font_style]'] = 'Bold'
    data['text[1][text]'] = 'Some example product'
    data['text[1][font_size]'] = '50'
    data['text[1][align]'] = 'center'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.content_type in ['image/png', 'text/plain']

    # Write image into file
    if UPDATE_IMAGES:
        with open('tests/preview_qr.png', 'wb') as f:
            f.write(response.data)

    # Compare generated preview with the image in file
    with open('tests/preview_qr.png', 'rb') as f:
        expected_data = f.read()
    assert response.data == expected_data

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
