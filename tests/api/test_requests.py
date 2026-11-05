"""Requests to the API: what is accepted and refused, limits, defaults and
requests of other sites."""

import io
import json
import re
from urllib.parse import urlencode

import pytest
from PIL import Image
from flask.testing import FlaskClient
from werkzeug.datastructures import FileStorage

from app.labeldesigner import refusals
from tests.helpers import _red_square, _upload, EXAMPLE_FORMDATA, make_client, pixels, TEXT, verify_image


class TestLabelDesignerAPI:
    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    def test_render_frontend(self, client: FlaskClient):
        response = client.get('/labeldesigner/')
        assert response.status_code == 200
        assert b'labeldesigner' in response.data
        assert response.content_type == 'text/html; charset=utf-8'

    def test_text_that_is_no_json(self, client: FlaskClient):
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

    @pytest.mark.parametrize('method', ['get', 'head', 'put', 'delete', 'patch'])
    def test_invalid_http_methods(self, client: FlaskClient, method):
        # POST only, 405 = Method Not Allowed
        func = getattr(client, method)
        response = func('/labeldesigner/api/preview')
        assert response.status_code == 405

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

    @pytest.mark.parametrize('size', [62.5, 'sixty-two', 0, -1, -100])
    def test_unknown_label_size(self, client: FlaskClient, size):
        data = EXAMPLE_FORMDATA.copy()
        data['label_size'] = size
        data['text'] = json.dumps([
            {'font': 'DejaVu Sans,Book', 'text': 'Bad label', 'size': '12', 'align': 'center'}
        ])
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 400
        assert response.get_json()['message'] == 'Unknown label_size'


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


@pytest.mark.parametrize('url, key, fallback', [
    ('/labeldesigner/api/preview', 'message', refusals.PREVIEW_FAILED),
    ('/labeldesigner/api/print', 'message', refusals.PRINT_FAILED),
])
def test_refused_requests_get_fixed_messages_and_the_log_the_details(tmp_path, monkeypatch, caplog, url, key, fallback):
    # The text of an error may name paths or values, only the log has it
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Test', 'size': '40'}]))
    for error, message in ((RuntimeError('/app/instance/secret.json is gone'), fallback),
                           (ValueError('/app/instance/secret.json is invalid'), refusals.INVALID_INPUT),
                           (ValueError('Font size is required'), 'Font size is required')):
        def refuse(*args, **kwargs):
            raise error
        monkeypatch.setattr('app.labeldesigner.routes.create_label_from_request', refuse)
        caplog.clear()
        response = client.post(url, data=data)
        assert response.status_code == 400
        assert response.get_json()[key] == message
        assert any(str(error) in record.getMessage() for record in caplog.records)
    # An unexpected error is logged with its traceback
    caplog.clear()
    monkeypatch.setattr('app.labeldesigner.routes.create_label_from_request', lambda *args, **kwargs: {}['missing'])
    client.post(url, data=data)
    assert any(record.exc_info and record.exc_info[0] is KeyError for record in caplog.records)


def test_fixed_messages_of_refusals():
    # Details are left out, unknown errors get the fallback
    assert refusals.message(ValueError('Label is too large (9000 x 9000 pixels)'), '-') == 'Label is too large'
    assert refusals.message(ValueError('"x" is not a page selection, give pages like 1-3, 5 or all'), '-') == \
        'Not a page selection, give pages like 1-3, 5 or all'
    assert refusals.message(ValueError('Unknown printer model: /app/instance'), '-') == 'Unknown printer model'
    assert refusals.message(ValueError('Unsupported printer: /app/instance\n'), '-') == 'Unsupported printer'
    assert refusals.message(FileNotFoundError('/app/instance/labels/x.png'), '-') == 'The image cannot be read'
    assert refusals.message(LookupError('Unknown font family: Secret'), '-') == 'Unknown font'
    assert refusals.message(KeyError('secret'), 'Fallback') == 'Fallback'
    # Errors with one of the fixed messages keep it, also those made of the
    # types of codes and the printer models
    assert all(refusals.message(ValueError(text), '-') == text for text in refusals.MESSAGES)
    assert 'The check digit of ISBN-10 must be X' in refusals.MESSAGES
    assert 'The PT-P750W cannot print at 600 dpi, turn off 600 dpi printing' in refusals.MESSAGES
    assert 'The label size 62 mm endless, black/red/white (DK-22251 / DK-2251) cannot be printed on a QL-500' in refusals.MESSAGES


def test_fixed_messages_of_the_configured_limits(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINT_MAX_COUNT'] = 7
    with client.application.app_context():
        assert refusals.message(ValueError('print_count must not exceed 7'), '-') == 'print_count must not exceed 7'
        assert refusals.message(ValueError('A print job must not have more than 7 labels, 3 pages and 4 copies would be 12'), '-') == \
            'A print job must not have more than 7 labels'


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


def test_label_designer_gets_the_configured_defaults(tmp_path):
    client = make_client(tmp_path)
    client.application.config.update({'LABEL_DEFAULT_MARGIN_LEFT': 0, 'LABEL_DEFAULT_FONT_SIZE': 55, 'LABEL_DEFAULT_ORIENTATION': 'rotated',
                                      'IMAGE_DEFAULT_MODE': 'black_and_white'})
    html = client.get('/labeldesigner/').get_data(as_text=True)
    defaults = json.loads(re.search(r'const DESIGNER_CONFIG = (.*);</script>', html).group(1))['defaults']
    assert (defaults['margin_left'], defaults['font_size'], defaults['orientation']) == (0, 55, 'rotated')
    assert defaults['image_mode'] == 'black_and_white'
