"""Text on labels through the API: fonts, alignment, templates, icons, to-do
boxes and empty lines."""

import io
import json
import random
import re
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

    @pytest.mark.parametrize('settings, lines, reference', [
        pytest.param({}, [], 'empty_label.png', id='empty_label'),
        # The smallest font size
        pytest.param({}, [{'font': 'DejaVu Sans,Book', 'text': 'Min', 'size': '1', 'align': 'center'}], 'minimal_label.png', id='minimal_label'),
        pytest.param({}, [{'font': 'DejaVu Sans,Book', 'text': 'Emoji: \U0001f603', 'size': '32', 'align': 'center'},
                          {'font': 'DejaVu Sans,Book', 'text': 'RTL: \u05e9\u05dc\u05d5\u05dd', 'size': '32', 'align': 'center'}],
                     'unicode.png', id='unicode_and_special_characters'),
        # Markup is drawn as plain text
        pytest.param({}, [{'font': 'DejaVu Sans,Book', 'text': '<script>alert(1)</script>', 'size': '32', 'align': 'center'}],
                     'security_xss.png', id='security_xss'),
        # Red on black and red tape, other tape prints it black
        pytest.param({'label_size': '62red'}, [{'font': 'DejaVu Sans,Book', 'text': 'Red Text', 'size': '24', 'align': 'center', 'color': 'red'},
                          {'font': 'DejaVu Sans,Book', 'text': 'Red Text INVERTED', 'size': '24', 'align': 'center', 'inverted': True, 'color': 'red'}],
                     'red_text.png', id='print_red_text'),
        pytest.param({'label_size': '62red'}, [{'font': 'DejaVu Sans,Book', 'text': f'--- {i} ---', 'size': str(i + 1), 'align': ['center', 'left', 'right'][i % 3],
                           'inverted': bool(i % 2), 'color': 'red' if i % 5 == 0 else 'black'} for i in range(100)],
                     'large_label.png', id='large_number_of_text_blocks'),
        # White on black around the letters reaching below and above the line
        pytest.param({}, [{'font': 'DejaVu Serif,Book', 'text': 'Agjpqy', 'size': '70', 'align': 'center', 'inverted': True},
                          {'font': 'DejaVu Serif,Book', 'text': '\u00c4gjpqy', 'size': '120', 'align': 'center', 'inverted': True}],
                     'inverted_descenders.png', id='inverted_descenders'),
        # Composed and decomposed
        pytest.param({}, [{'font': 'DejaVu Sans,Book', 'text': 'Caf\u00e9', 'size': '24', 'align': 'center'},
                          {'font': 'DejaVu Sans,Book', 'text': unicodedata.normalize('NFD', 'Caf\u00e9'), 'size': '24', 'align': 'center'}],
                     'unicode_normalization.png', id='unicode_normalization'),
        pytest.param({'label_size': '62red'}, [{'font': 'Droid Sans,Regular', 'text': 'Item 1 (very small)', 'size': '15', 'align': 'left', 'checkbox': True},
                          {'font': 'Noto Sans,Regular', 'text': 'Item 2', 'size': '50', 'align': 'left', 'checkbox': True},
                          {'font': 'DejaVu Sans,Book', 'text': 'Not an item XX', 'size': '70', 'align': 'right', 'checkbox': False, 'color': 'red'},
                          {'font': 'DejaVu Serif,Bold', 'text': 'Item 3', 'size': '50', 'align': 'left', 'checkbox': True}],
                     'todo_list.png', id='todo_list'),
        pytest.param({'label_size': '62red', 'border_thickness': 1, 'border_color': 'red'},
                     [{'font': 'DejaVu Math TeX Gyre,Regular', 'text': 'Mehrwegbecher', 'size': '70', 'align': 'left'}],
                     'large_text.png', id='large_font_not_cropped'),
        pytest.param({}, [{'font': 'DejaVu Sans,Book', 'text': '{{datetime:}}', 'size': '32', 'align': 'center'}],
                     'template_incomplete.png', id='incomplete_template'),
        pytest.param({}, [{'font': 'DejaVu Sans,Book', 'text': '{{unknownvar}}', 'size': '32', 'align': 'center'}],
                     'template_unknown.png', id='unknown_template'),
    ])
    def test_text_as_its_reference_image(self, client: FlaskClient, settings, lines, reference):
        data = dict(EXAMPLE_FORMDATA, **settings)
        data['text'] = json.dumps(lines)
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        assert response.content_type in ['image/png']

        # Check image
        verify_image(response.data, reference)

    @pytest.mark.parametrize('line', [
        pytest.param({'text': 'Bad align', 'align': 'diagonal'}, id='invalid_alignment'),
        pytest.param({'text': 12345, 'align': 'center'}, id='non_string_text_value'),
    ])
    def test_a_line_that_is_refused(self, client: FlaskClient, line):
        data = EXAMPLE_FORMDATA.copy()
        data['text'] = json.dumps([dict({'font': 'DejaVu Sans,Book', 'size': '12'}, **line)])
        response = client.post('/labeldesigner/api/preview', data=data)
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


@pytest.mark.parametrize('font', ['DejaVu Serif,Book', 'DejaVu Sans,Bold'])
@pytest.mark.parametrize('size', [70, 120])
def test_white_on_black_keeps_all_of_the_letters(tmp_path, font, size):
    # The box was drawn higher than the letters: those reaching below the
    # line were cut off, white on white
    client = make_client(tmp_path)

    def ink(inverted):
        line = {'font': font, 'text': 'Agjpqy \u00c4', 'size': str(size), 'align': 'center', 'inverted': inverted}
        data = dict(EXAMPLE_FORMDATA, border_thickness='0', text=json.dumps([line]))
        with Image.open(io.BytesIO(client.post('/labeldesigner/api/preview', data=data).data)) as image:
            gray = image.convert('L')
        if not inverted:
            return sum(gray.histogram()[:128])
        # The letters are the light pixels in the box
        inside = gray.crop(gray.point(lambda v: 255 if v < 128 else 0).getbbox())
        return sum(inside.histogram()[128:])
    assert ink(True) == pytest.approx(ink(False), rel=0.02)


def test_white_on_black_boxes_of_a_size_are_alike(tmp_path):
    # From the ascender to the descender of the font, whatever the letters
    client = make_client(tmp_path)

    def box(text):
        line = {'font': 'DejaVu Sans,Book', 'text': text, 'size': '60', 'inverted': True}
        data = dict(EXAMPLE_FORMDATA, border_thickness='0', text=json.dumps([line]))
        with Image.open(io.BytesIO(client.post('/labeldesigner/api/preview', data=data).data)) as image:
            left, top, right, bottom = image.convert('L').point(lambda v: 255 if v < 128 else 0).getbbox()
        return top, bottom
    assert box('ace') == box('Agjpqy') == box('\u00c4\u00d6\u00dc')


@pytest.mark.parametrize('settings', [{'align': 'left', 'checkbox': True}, {'align': 'center', 'inverted': True}])
@pytest.mark.parametrize('line_break', ['\n', '\r\n', '\r'])
def test_a_line_break_starts_a_line_with_the_same_settings(tmp_path, line_break, settings):
    # A line break failed the label, "anchor not supported for multiline
    # text", a carriage return was drawn as a box. Each line keeps the
    # settings, a to-do box too: a list given as one text is a to-do list.
    client = make_client(tmp_path)
    line = dict({'font': 'DejaVu Sans,Book', 'size': '40'}, **settings)
    assert preview_of_lines(client, EXAMPLE_FORMDATA, [dict(line, text=f'Milk{line_break}Eggs')]) == \
        preview_of_lines(client, EXAMPLE_FORMDATA, [dict(line, text='Milk'), dict(line, text='Eggs')])


def test_a_blank_filled_with_lines_shows_them(tmp_path):
    # e.g. an address given through the API
    client = make_client(tmp_path)
    line = {'font': 'DejaVu Sans,Book', 'size': '40', 'align': 'left'}
    data = dict(EXAMPLE_FORMDATA, blanks=json.dumps({'Address': 'Jane Doe\r\nMain Street 1'}))
    assert preview_of_lines(client, data, [dict(line, text='To: {{blank:Address}}')]) == \
        preview_of_lines(client, EXAMPLE_FORMDATA, [dict(line, text='To: Jane Doe'), dict(line, text='Main Street 1')])


def test_a_code_of_the_text_keeps_its_line_breaks(tmp_path):
    client = make_client(tmp_path)
    line = {'font': 'DejaVu Sans,Book', 'size': '30', 'align': 'left'}
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type='QR')
    assert preview_of_lines(client, data, [dict(line, text='Milk\r\nEggs')]) == \
        preview_of_lines(client, data, [dict(line, text='Milk'), dict(line, text='Eggs')])
    with client.application.test_request_context():
        label = create_label_from_request(dict(data, label_size='62', code_text='Milk\r\nEggs', text=json.dumps([dict(line, text='Shopping')])))
        label.process_templates()
        assert label.code_text == 'Milk\r\nEggs'


def test_the_marks_of_blanks_make_no_lines_of_their_own(tmp_path):
    # A preview shows the name of a blank still to fill in, the text as
    # printed (e.g. for the code) is split into the same lines
    client = make_client(tmp_path)
    text = 'To: {{blank:Name\nof the person}} {{blank:Address}}'
    data = {'print_type': 'qrcode_text', 'blanks': {'Address': 'Main\nStreet'},
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': text}])}
    with client.application.test_request_context():
        label = create_label_from_request(data, mark_blanks=True)
        label.process_templates()
    assert [(line['text'], line['unmarked']) for line in label.text] == [('To: Name of the person? Main', 'To:  Main'), ('Street', 'Street')]


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
    assert response.get_json()['message'] == 'Font must be given as "<family>,<style>"'


def test_date_and_time_codes_for_the_label_designer(tmp_path):
    # Their examples come from the strftime() that fills in the labels
    html = make_client(tmp_path).get('/labeldesigner/').get_data(as_text=True)
    codes = json.loads(re.search(r'const DESIGNER_CONFIG = (.*);</script>', html).group(1))['datetime_codes']
    assert codes['example'] == 'Saturday, 7 March 2026, 14:05:09'
    examples = {code: example for code, _, example in codes['codes']}
    assert [examples[code] for code in ('%d', '%-d', '%m', '%-m', '%b', '%V', '%I', '%p', '%%')] == [
        '07', '7', '03', '3', 'Mar', '10', '02', 'PM', '%']


@pytest.mark.parametrize('when, template, expected', [
    # Days and weeks, also into the next year, and the time stays
    ((2024, 12, 30), '{{datetime+5d:%Y-%m-%d %H:%M}}', '2025-01-04 12:15'),
    ((2024, 1, 31), '{{datetime-2w:%Y-%m-%d}}', '2024-01-17'),
    ((2024, 1, 31), '{{datetime+0d:%Y-%m-%d}}', '2024-01-31'),
    # Months and years keep the day, or take the last one of a shorter month
    ((2024, 1, 31), '{{datetime+1m:%Y-%m-%d}}', '2024-02-29'),
    ((2023, 1, 31), '{{datetime+1m:%Y-%m-%d}}', '2023-02-28'),
    ((2024, 3, 31), '{{datetime-1m:%Y-%m-%d}}', '2024-02-29'),
    ((2024, 11, 15), '{{datetime+6m:%d.%m.%Y}}', '15.05.2025'),
    ((2024, 2, 29), '{{datetime+1y:%Y-%m-%d}}', '2025-02-28'),
    ((2024, 2, 29), '{{datetime-100y:%Y-%m-%d}}', '1924-02-29'),
    # Several in a line, and one that is no offset stays as it is
    ((2024, 1, 31), 'Made {{datetime:%d.%m.}}, use by {{datetime+5d:%d.%m.}}', 'Made 31.01., use by 05.02.'),
    ((2024, 1, 31), '{{datetime+5x:%Y}} {{datetime 5d:%Y}}', '{{datetime+5x:%Y}} {{datetime 5d:%Y}}'),
])
def test_dates_moved_from_today(tmp_path, when, template, expected):
    # A best-before date as a date, not as "Use within 5 days"
    client = make_client(tmp_path)
    data = {'timestamp': str(int(datetime(*when, 12, 15).timestamp())),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': template}])}
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.process_templates()
        assert label.text[0]['text'] == expected


@pytest.mark.parametrize('template, offset', [
    ('{{datetime+101y:%Y}}', '+101y'),
    ('{{datetime-36526d:%Y}}', '-36526d'),
    ('{{datetime+1201m:%Y}}', '+1201m'),
    ('{{datetime+999999w:%Y}}', '+999999w'),
])
def test_dates_moved_too_far_are_refused(tmp_path, caplog, template, offset):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': template}]))
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == 'A date offset is at most a hundred years'
    # The offset is named in the log only
    assert any(f'A date offset is at most a hundred years, not {offset}' in record.getMessage() for record in caplog.records)
    # A hundred years are fine
    data['text'] = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{datetime+36525d:%Y}} {{datetime-100y:%Y}}'}])
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
