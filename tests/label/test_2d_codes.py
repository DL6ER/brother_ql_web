"""2D codes next to QR codes: DataMatrix, Micro QR, rMQR, Aztec and PDF417.
The reference images were checked once with a decoder (zxing-cpp)."""

import io
import json

import pytest
from PIL import Image, ImageChops

from app.labeldesigner import codes
from app.labeldesigner.label import _code_image

from tests.helpers import EXAMPLE_FORMDATA, make_client, TEXT, verify_image

PREVIEW = '/labeldesigner/api/preview'
LINES = [{'font': 'DejaVu Sans,Book', 'text': 'Shelf 4', 'size': '50', 'align': 'left'}]


def preview(client, **settings):
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', text=json.dumps(LINES))
    data.update(settings)
    return client.post(PREVIEW, data=data)


def test_the_code_types_say_which_are_2d(tmp_path):
    client = make_client(tmp_path)
    answer = client.get('/labeldesigner/api/barcodes').get_json()
    # QR codes first, then the other 2D codes, then the barcodes
    assert answer['barcodes'][:6] == ['QR', 'DATAMATRIX', 'MICROQR', 'RMQR', 'AZTEC', 'PDF417']
    assert 'EAN13' in answer['barcodes']
    kinds = {code['type']: code for code in answer['codes']}
    assert [code['type'] for code in answer['codes']] == answer['barcodes']
    assert kinds['QR'] == {'type': 'QR', 'name': 'QR', 'two_d': True, 'corrections': ['L', 'M', 'Q', 'H']}
    assert kinds['MICROQR'] == {'type': 'MICROQR', 'name': 'Micro QR', 'two_d': True, 'corrections': ['L', 'M', 'Q']}
    assert kinds['RMQR'] == {'type': 'RMQR', 'name': 'rMQR', 'two_d': True, 'corrections': ['M', 'H']}
    assert kinds['DATAMATRIX'] == {'type': 'DATAMATRIX', 'name': 'DataMatrix', 'two_d': True, 'corrections': []}
    assert kinds['EAN13'] == {'type': 'EAN13', 'name': 'EAN13', 'two_d': False, 'corrections': []}


@pytest.mark.parametrize('code_type, content', [
    ('DATAMATRIX', 'grcy:p:12:x6a3c7f0b'),
    ('MICROQR', '4006381333931'),
    ('RMQR', 'https://example.com'),
    ('AZTEC', 'Grüße aus Köln'),
    ('PDF417', 'Grüße aus Köln \u20ac'),
])
def test_2d_codes_are_printed(tmp_path, code_type, content):
    client = make_client(tmp_path)
    response = preview(client, barcode_type=code_type, code_text=content, qrcode_size='3', image_fit='0')
    assert response.status_code == 200
    verify_image(response.data, f'code_{code_type.lower()}.png')


@pytest.mark.parametrize('code_type', ['RMQR', 'AZTEC', 'PDF417'])
def test_2d_codes_hold_text_beyond_latin_1(code_type):
    # Aztec codes mark text beyond Latin-1 with its encoding
    rows, _ = codes.matrix(code_type, 'Preis 4,99 \u20ac', codes.correction(code_type, None))
    assert rows and all(len(row) == len(rows[0]) for row in rows)


@pytest.mark.parametrize('code_type, rows, high', [('DATAMATRIX', 14, 1), ('AZTEC', 15, 1), ('PDF417', 3, 3)])
def test_a_dot_has_the_size_set(tmp_path, code_type, rows, high):
    # Without fitting, each dot is qrcode_size dots of the label wide, the
    # rows of a PDF417 code three times as high
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type=code_type, code_text='Hello 123', qrcode_size='5', image_fit='0',
                border_thickness='0')
    response = client.post(PREVIEW, data=data)
    assert response.status_code == 200
    image = Image.open(io.BytesIO(response.data)).convert('L')
    left, top, right, bottom = image.point(lambda v: 255 if v < 128 else 0).getbbox()
    assert bottom - top == rows * 5 * high
    if code_type != 'PDF417':
        assert right - left == rows * 5


def test_a_2d_code_without_content_of_its_own_holds_all_lines(tmp_path, monkeypatch):
    held = []
    original = codes.matrix
    monkeypatch.setattr(codes, 'matrix', lambda code_type, data, level: held.append(data) or original(code_type, data, level))
    client = make_client(tmp_path)
    lines = [dict(LINES[0], text='Shelf 4'), dict(LINES[0], text='Box 2')]
    assert preview(client, barcode_type='DATAMATRIX', text=json.dumps(lines)).status_code == 200
    assert held == ['Shelf 4\nBox 2']
    # Empty, it says which code is empty
    response = preview(client, barcode_type='AZTEC', print_type='qrcode', text=json.dumps([]))
    assert response.status_code == 400 and response.get_json()['message'] == 'The Aztec code is empty'


@pytest.mark.parametrize('code_type, content, message', [
    # Said as of QR codes before
    ('QR', 'x' * 3000, 'The QR code holds too much text'),
    ('MICROQR', 'https://example.com', 'The Micro QR code holds too much text'),
    ('RMQR', 'x' * 400, 'The rMQR code holds too much text'),
    ('DATAMATRIX', 'x' * 3000, 'The DataMatrix code holds too much text'),
    ('AZTEC', 'x' * 4000, 'The Aztec code holds too much text'),
    ('PDF417', 'x' * 3000, 'The PDF417 code holds too much text'),
    # Only ASCII, the library has no other encoding
    ('DATAMATRIX', 'Grüße', 'DataMatrix codes can hold only ASCII characters, no umlauts or accents'),
])
def test_a_code_too_small_for_its_content_is_refused(tmp_path, code_type, content, message):
    client = make_client(tmp_path)
    response = preview(client, barcode_type=code_type, code_text=content)
    assert response.status_code == 400
    assert response.get_json()['message'] == message


@pytest.mark.parametrize('code_type, given, used', [
    ('QR', None, 'L'),
    ('QR', 'h', 'H'),
    ('MICROQR', None, 'L'),
    ('MICROQR', 'Q', 'Q'),
    ('RMQR', None, 'M'),
    ('RMQR', 'H', 'H'),
    # The others choose it themselves, a level sent along is of no matter
    ('DATAMATRIX', 'H', None),
    ('PDF417', 'X', None),
])
def test_error_correction_of_the_codes_that_have_a_choice(tmp_path, monkeypatch, code_type, given, used):
    levels = []
    monkeypatch.setattr(codes, 'matrix', lambda code_type, data, level, original=codes.matrix: levels.append(level) or original(code_type, data, level))
    client = make_client(tmp_path)
    settings = {'barcode_type': code_type, 'code_text': '12345'}
    if given is not None:
        settings['qrcode_correction'] = given
    assert preview(client, **settings).status_code == 200
    assert levels == [used]


@pytest.mark.parametrize('code_type, level, message', [
    ('MICROQR', 'H', 'qrcode_correction must be L, M or Q for Micro QR codes'),
    ('RMQR', 'L', 'qrcode_correction must be M or H for rMQR codes'),
    ('QR', 'X', 'qrcode_correction must be L, M, Q or H for QR codes'),
])
def test_a_level_of_error_correction_the_code_does_not_have_is_refused(tmp_path, code_type, level, message):
    client = make_client(tmp_path)
    response = preview(client, barcode_type=code_type, code_text='12345', qrcode_correction=level)
    assert response.status_code == 400
    assert response.get_json()['message'] == message


def test_2d_codes_are_also_taken_in_lowercase(tmp_path):
    client = make_client(tmp_path)
    lower = preview(client, barcode_type='datamatrix', code_text='Shelf 4')
    upper = preview(client, barcode_type='DATAMATRIX', code_text='Shelf 4')
    assert lower.status_code == 200 and lower.data == upper.data


def test_a_2d_code_keeps_two_of_its_dots_to_the_text(tmp_path):
    client = make_client(tmp_path)
    response = preview(client, barcode_type='DATAMATRIX', code_text='Shelf 4', orientation='rotated', qrcode_size='20', image_fit='0',
                       image_text_gap='')
    assert response.headers['X-Label-Image-Text-Gap'] == '40'


def test_a_red_2d_code(tmp_path):
    client = make_client(tmp_path)
    response = preview(client, label_size='62red', barcode_type='AZTEC', code_text='Shelf 4', print_color='red', image_fit='0')
    assert response.status_code == 200
    colors = {color for _, color in Image.open(io.BytesIO(response.data)).convert('RGB').getcolors(1_000_000)}
    assert (255, 0, 0) in colors


def test_a_2d_code_next_to_an_image_in_any_color(tmp_path):
    # In the layout of image, code and text, as large as set, in the color
    # set for the VC-500W
    client = make_client(tmp_path)
    image = io.BytesIO()
    Image.new('RGB', (120, 80), (255, 0, 0)).save(image, 'PNG')
    image.seek(0)
    data = dict(EXAMPLE_FORMDATA, label_size='vc50', orientation='rotated', print_type='image_qrcode_text', image_fit='0', image_position='left',
                barcode_type='DATAMATRIX', code_text='Shelf 4', qrcode_size='4', print_color='#0000ff', text=json.dumps(LINES),
                image=(image, 'red.png', 'image/png'))
    response = client.post(PREVIEW, data=data, content_type='multipart/form-data')
    assert response.status_code == 200
    preview = Image.open(io.BytesIO(response.data)).convert('RGB')
    red, green, blue = preview.split()
    blue = ImageChops.multiply(blue.point(lambda v: 255 if v > 200 else 0),
                               ImageChops.multiply(red.point(lambda v: 255 if v < 80 else 0), green.point(lambda v: 255 if v < 80 else 0)))
    code = preview.crop(blue.getbbox())
    assert code.tobytes() == _code_image('DATAMATRIX', 'Shelf 4', None, 4, (0, 0, 255)).convert('RGB').tobytes()


def test_2d_codes_fit_narrow_tape(tmp_path):
    # rMQR codes are made as low as their content allows, along 12 mm tape
    # the whole code fits across it
    client = make_client(tmp_path, model='PT-P750W')
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', label_size='pt12', orientation='rotated', barcode_type='RMQR',
                code_text='https://example.com', image_fit='1', margin_top='0', margin_bottom='0', border_thickness='0')
    response = client.post(PREVIEW, data=data)
    assert response.status_code == 200
    image = Image.open(io.BytesIO(response.data))
    # 7 rows of 10 dots across the 70 dots of the tape
    assert image.height == 70 and image.width > 7 * image.height


@pytest.mark.parametrize('values, message', [
    ({'barcode_type': 'RMQR', 'qrcode_correction': 'L'}, 'qrcode_correction must be M or H for rMQR codes'),
    ({'barcode_type': 'datamatrix', 'qrcode_correction': 'X'}, None),
])
def test_a_label_with_a_level_its_code_does_not_have_is_not_saved(tmp_path, values, message):
    client = make_client(tmp_path, empty_repo=True)
    response = client.post('/labeldesigner/api/repository/save', json=dict({'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}, **values))
    if message is None:
        assert response.status_code == 200
        return
    assert response.status_code == 400 and response.get_json()['message'] == message


@pytest.mark.parametrize('saved, loaded', [('datamatrix', 'DATAMATRIX'), ('ean13', 'EAN13'), ('QR', 'QR')])
def test_a_label_loads_with_the_code_type_the_designer_offers(tmp_path, saved, loaded):
    # The designer offers the codes in uppercase
    client = make_client(tmp_path, empty_repo=True)
    response = client.post('/labeldesigner/api/repository/save', json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT,
                                                                       'print_type': 'qrcode', 'barcode_type': saved})
    assert response.status_code == 200
    assert client.get('/labeldesigner/api/repository/load?name=Shelf.json').get_json()['barcode_type'] == loaded
