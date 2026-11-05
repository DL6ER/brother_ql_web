"""QR codes and barcodes through the API, also with templates and blanks,
and the cache of codes."""

import json
import os
import random

import barcode
import pytest
from PIL import Image
from flask.testing import FlaskClient

from app.labeldesigner.label import CHECK_DIGIT_CODES
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _red_square, _upload, EXAMPLE_FORMDATA, make_client, pixels, TEXT, verify_image


class TestLabelDesignerAPI:
    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    def test_get_barcodes(self, client: FlaskClient):
        response = client.get('/labeldesigner/api/barcodes')
        assert response.status_code == 200
        assert response.is_json
        assert 'barcodes' in response.get_json()
        offered_barcodes = response.get_json()['barcodes']
        assert 'QR' in offered_barcodes
        assert 'EAN13' in offered_barcodes

    @pytest.mark.parametrize('settings, lines, reference', [
        # The code of the text
        pytest.param({'barcode_type': 'ean13'}, [{'font': 'Droid Serif,Bold', 'text': '123456789012', 'size': '50', 'align': 'center'}],
                     'barcode_ean13.png', id='ean13'),
        # A code of its own
        pytest.param({'barcode_type': 'ean13', 'code_text': '123456789012'},
                     [{'font': 'DejaVu Sans,Book', 'text': 'Some example product', 'size': '50', 'align': 'center'}],
                     'barcode_ean13_custom_label.png', id='ean13_custom_label'),
        pytest.param({'barcode_type': 'QR'}, [{'font': 'DejaVu Sans,Book', 'text': '123456789012', 'size': '40', 'align': 'center'},
                                              {'font': 'Droid Serif,Bold', 'text': 'Some example product', 'size': '50', 'align': 'center'}],
                     'qr.png', id='qr'),
        pytest.param({'barcode_type': 'QR', 'code_text': 'https://github.com/DL6ER/brother_ql_web'},
                     [{'font': 'DejaVu Sans,Book', 'text': 'This is some text not', 'size': '40', 'align': 'center'},
                      {'font': 'DejaVu Sans,Book', 'text': 'contained in the QR code', 'size': '40', 'align': 'center'}],
                     'qr_custom_label.png', id='qr_custom_content'),
    ])
    def test_generate_code(self, client: FlaskClient, settings, lines, reference):
        data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', **settings)
        data['text'] = json.dumps(lines)
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, reference)

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
        assert data['message'] == 'EAN-13 takes 12 digits, or 13 with the check digit'

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


def test_barcode_length_is_limited(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'print_type': 'qrcode', 'barcode_type': 'code128', 'code_text': 'A' * 1001})
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Barcode value must not exceed 1000 characters'


# Numbers with their right check digit, as printed on products and books
CHECK_DIGIT_NUMBERS = [
    ('EAN13', '4006381333931'), ('EAN13-GUARD', '4006381333931'), ('EAN8', '96385074'), ('EAN14', '10012345678902'),
    ('GTIN', '00012345600012'), ('JAN', '4901234567894'), ('UPCA', '036000291452'), ('ISBN13', '978-3-16-148410-0'),
    ('ISBN10', '0-8044-2957-X'), ('ISSN', '0317-8471'), ('PZN', '4908802'),
]


@pytest.mark.parametrize('barcode_type, number', CHECK_DIGIT_NUMBERS)
def test_a_code_takes_its_number_with_or_without_the_check_digit(tmp_path, barcode_type, number):
    client = make_client(tmp_path)

    def preview(code):
        data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type=barcode_type, code_text=code, text='[]')
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200, response.get_json()
        return pixels(response.data)
    assert preview(number) == preview(number[:-1])


@pytest.mark.parametrize('barcode_type, code, message', [
    # The right check digit was printed instead of a wrong one, and what
    # followed the digits was left out: a typo made another valid code
    ('EAN13', '4006381333932', 'The check digit of EAN-13 must be 1'),
    ('EAN13', '400638133393x', 'EAN-13 takes 12 digits, or 13 with the check digit'),
    ('EAN13', '40063813339310', 'EAN-13 takes 12 digits, or 13 with the check digit'),
    ('EAN8', '96385075', 'The check digit of EAN-8 must be 4'),
    ('EAN14', '10012345678903', 'The check digit of EAN-14 must be 2'),
    ('UPCA', '036000291453', 'The check digit of UPC-A must be 2'),
    ('ISBN13', '978-3-16-148410-9', 'The check digit of ISBN-13 must be 0'),
    ('ISBN10', '0-8044-2957-1', 'The check digit of ISBN-10 must be X'),
    ('ISBN10', '08044295Y', 'ISBN-10 takes 9 digits, or 10 with the check digit, which may be an X'),
    ('ISSN', '0317-8479', 'The check digit of ISSN must be 1'),
    ('PZN', '4908809', 'The check digit of PZN must be 2'),
    # Digits of other scripts are none of the code
    ('EAN13', '٤٠٠638133393', 'EAN-13 takes 12 digits, or 13 with the check digit'),
])
def test_a_code_with_a_wrong_check_digit_is_refused(tmp_path, barcode_type, code, message):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type=barcode_type, code_text=code, text='[]')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == message


def test_a_code_with_a_wrong_check_digit_is_not_printed(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type='EAN13', code_text='4006381333932')
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == 'The check digit of EAN-13 must be 1'
    assert not os.path.isdir(printer.SIMULATED_LABELS_DIR) or not os.listdir(printer.SIMULATED_LABELS_DIR)


def test_every_code_with_a_check_digit_is_checked():
    # A type python-barcode calculates a check digit for
    for name in barcode.PROVIDED_BARCODES:
        if getattr(barcode.get_barcode_class(name), 'digits', 0):
            assert name in CHECK_DIGIT_CODES


@pytest.mark.parametrize('barcode_type, number', CHECK_DIGIT_NUMBERS)
def test_check_digits_are_those_of_python_barcode(barcode_type, number):
    name, length, check_digit, hyphens = CHECK_DIGIT_CODES[barcode_type.lower()]
    digits = number.replace('-', '')[:length]
    assert check_digit(digits) == number[-1]
    assert str(barcode.get(barcode_type.lower(), digits)).endswith(number[-1])


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


def test_a_barcode_of_digits_still_to_fill_in_says_so(tmp_path):
    # The marks of its blanks are no digits: the preview failed with the
    # message of the barcode instead
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type='EAN13', code_text='{{blank:EAN}}', text='[]', mark_blanks='1')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == 'The code is empty until its blanks are filled in.'


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
