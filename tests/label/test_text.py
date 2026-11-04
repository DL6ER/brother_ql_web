"""Text on labels through the API: fonts, alignment, templates, icons, to-do
boxes and empty lines."""

import io
import json
import random
import unicodedata
from datetime import datetime

import pytest
from PIL import Image, ImageOps
from flask.testing import FlaskClient

from app import create_app
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import EXAMPLE_FORMDATA, make_client, pixels, TEXT, verify_image


class TestLabelDesignerAPI:
    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

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


def test_only_text_too_long_answers_too_large(tmp_path, monkeypatch):
    # Any message with "too long" in it was taken for a request too large
    from app.labeldesigner import routes
    monkeypatch.setattr(routes, 'create_label_from_request', lambda *args, **kwargs: (_ for _ in ()).throw(ValueError('The name is too long for this')))
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=TEXT))
    assert response.status_code == 400


def test_default_font_is_selected(tmp_path):
    client = make_client(tmp_path)
    client.application.config['LABEL_DEFAULT_FONT_FAMILY'] = 'DejaVu Serif'
    family, style = client.application.extensions['fonts'].get_default_font()
    html = client.get('/labeldesigner/').get_data(as_text=True)
    assert f'"default_font": "{family},{style}"' in html


def test_text_line_without_font_uses_the_default_font(tmp_path):
    # The label designer always sends a font, requests to the API need not
    client = make_client(tmp_path)
    data = {'text': json.dumps([{'text': 'Hello', 'size': '70'}])}
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    data = {'text': json.dumps([{'text': 'Hello', 'size': '70', 'font': 'DejaVu Sans'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Font must be given as "<family>,<style>": DejaVu Sans'
