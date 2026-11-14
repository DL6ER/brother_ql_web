"""Blanks of labels: values, settings, dates, marks in previews and blanks
in codes."""

import json
import os
from datetime import datetime

import pytest
from PIL import Image

from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _image_and_text_boxes, _upload, EXAMPLE_FORMDATA, make_client, TEXT


def test_blanks_are_filled_in(tmp_path, monkeypatch):
    # The values come with the request, as given: templates in them are not
    # filled in. Without a value a blank gets its default, a date today's
    # date of the label, any other blank stays empty.
    client = make_client(tmp_path)
    monkeypatch.setenv('LABEL_ROOM', 'Lab 1')
    client.application.config['LABEL_TEMPLATE_ENV_VARS'] = 'LABEL_ROOM'
    timestamp = 1785000000
    today = datetime.fromtimestamp(timestamp).strftime('%Y-%m-%d')
    lines = ['{{blank:What}} {{env:LABEL_ROOM}}', 'Frozen on {{blank:Frozen on}}', 'Best before {{blank:Best before}}',
             'Box {{blank:Box}}/{{blank:Shelf}}', '{{blank:Note}}', '{{blank:Use by}}']
    data = {'print_type': 'qrcode_text', 'timestamp': str(timestamp), 'code_text': '{{blank:What}} {{counter}}',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '40'} for text in lines]),
            'fields': {'Box': {'type': 'number', 'default': '3'}, 'Best before': {'type': 'date'}, 'Note': {'default': 'n/a'},
                       'Use by': {'type': 'date', 'default': '2026-12-24'}, 'Frozen on': {'type': 'date'}}}
    expected = ['{{env:LABEL_ROOM}} {{counter}} Lab 1', f'Frozen on {today}', f'Best before {today}', 'Box 3/', 'n/a', '2026-12-24']
    with client.application.test_request_context():
        # As JSON text in a form or query, and as object in a JSON body
        for blanks in (json.dumps({'What': '{{env:LABEL_ROOM}} {{counter}}'}),
                       {'What': '{{env:LABEL_ROOM}} {{counter}}'}):
            label = create_label_from_request(dict(data, blanks=blanks))
            label.process_templates()
            assert [line['text'] for line in label.text] == expected
            assert label.code_text == '{{env:LABEL_ROOM}} {{counter}} 1'
        # Left empty on purpose, also a date: no default, no date of today
        label = create_label_from_request(dict(data, blanks={'Note': '', 'Use by': '', 'Frozen on': ''}))
        label.process_templates()
        assert [label.text[i]['text'] for i in (1, 4, 5)] == ['Frozen on ', '', '']
        label = create_label_from_request(dict(data, blanks={'Box': 4, 'Shelf': 2.5}))
        label.process_templates()
        assert label.text[3]['text'] == 'Box 4/2.5'
        assert label.code_text == ' 1'


@pytest.mark.parametrize('barcode_type', ['QR', 'ean13'])
def test_code_of_empty_blanks_takes_the_text(tmp_path, barcode_type):
    # Like a code left empty
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type=barcode_type,
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '123456789012', 'size': '40'}]))

    def preview(code_text, **values):
        response = client.post('/labeldesigner/api/preview', data=dict(data, code_text=code_text, **values))
        assert response.status_code == 200
        return response.data
    assert preview('{{blank:Code}}') == preview('')
    assert preview('{{blank:Code}}', blanks=json.dumps({'Code': '400638133393'})) == preview('400638133393')
    assert preview('400638133393') != preview('')


def test_a_blank_without_a_type_is_text(tmp_path):
    # Named with the word "on" it was taken for a date, e.g. "Add-on" or "On
    # hand", and got today's date when left empty
    client = make_client(tmp_path)
    data = {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Add-on}}|{{blank:On hand}}|{{blank:Frozen on}}'}])}
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.process_templates()
        assert label.text[0]['text'] == '||'


def test_dates_of_blanks_are_written_in_their_format(tmp_path):
    # As the label designer saves them, the settings come with the label or
    # as JSON with the request. Text that looks like a date stays as it is.
    # Without a type a blank is text, also one named like a date.
    client = make_client(tmp_path)
    timestamp = 1785000000
    today = datetime.fromtimestamp(timestamp).strftime('%d.%m.%Y')
    fields = {'Due': {'type': 'date', 'format': '%d.%m.%Y'}, 'Code': {'type': 'text'}, 'Made on': {'format': '%d/%m/%Y'},
              'Odd': {'type': 'date', 'format': 'week %V'}}
    data = {'timestamp': str(timestamp), 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40',
            'text': '{{blank:Code}}|{{blank:Due}}|{{blank:Made on}}|{{blank:Sent on}}|{{blank:Odd}}'}])}
    expected = {
        json.dumps({'Code': '2026-11-01', 'Due': '2026-11-01', 'Made on': '2026-11-02', 'Sent on': '2026-11-03', 'Odd': '2026-11-04'}):
            '2026-11-01|01.11.2026|2026-11-02|2026-11-03|week 45',
        # Without a value today, a value that is no date stays as given
        json.dumps({'Due': 'tomorrow', 'Made on': '2026-13-01'}): f'|tomorrow|2026-13-01||{datetime.fromtimestamp(timestamp):week %V}',
    }
    with client.application.test_request_context():
        for blanks, text in expected.items():
            for given in (fields, json.dumps(fields)):
                label = create_label_from_request(dict(data, blanks=blanks, fields=given))
                label.process_templates()
                assert label.text[0]['text'] == text
        label = create_label_from_request(dict(data, fields=json.dumps(fields)))
        label.process_templates()
        assert label.text[0]['text'].split('|')[1] == today


def test_dates_of_blanks_start_at_a_day_moved_from_today(tmp_path):
    # A best-before date as a default, e.g., 5 days or 6 months from today.
    # Given values may be moved as well, also through the API.
    client = make_client(tmp_path)
    timestamp = int(datetime(2024, 1, 31, 12).timestamp())
    fields = {'Use by': {'type': 'date', 'format': '%d.%m.%Y', 'default': '+5d'}, 'Made on': {'type': 'date', 'default': '-1w'},
              'Note': {'type': 'text', 'default': '+5d'}}
    data = {'timestamp': str(timestamp), 'fields': json.dumps(fields),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Use by}}|{{blank:Made on}}|{{blank:Note}}'}])}
    expected = {
        json.dumps({}): '05.02.2024|2024-01-24|+5d',
        json.dumps({'Use by': '+1m', 'Made on': ' +1y '}): '29.02.2024|2025-01-31|+5d',
        json.dumps({'Use by': '2024-03-01', 'Made on': '', 'Note': '+1d'}): '01.03.2024||+1d',
        # Not an offset, stays as given like any value that is no date
        json.dumps({'Use by': '+5', 'Made on': 'in 5d'}): '+5|in 5d|+5d',
    }
    with client.application.test_request_context():
        for blanks, text in expected.items():
            label = create_label_from_request(dict(data, blanks=blanks))
            label.process_templates()
            assert label.text[0]['text'] == text
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, **data, blanks=json.dumps({'Use by': '+101y'})))
    assert response.status_code == 400 and response.get_json()['message'] == 'A date offset is at most a hundred years'


@pytest.mark.parametrize('default, message', [
    ('+5x', 'A date offset is a sign, a number and d, w, m or y, e.g., +5d'),
    ('-5', 'A date offset is a sign, a number and d, w, m or y, e.g., +5d'),
    (' +101y', 'A date offset is at most a hundred years'),
])
def test_dates_of_blanks_moved_by_another_offset_are_refused(tmp_path, default, message):
    # Refused when saved, not only when printed
    client = make_client(tmp_path, empty_repo=True)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Use by}}', 'size': '40'}])
    fields = {'Use by': {'type': 'date', 'default': default}}
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=text, fields=json.dumps(fields)))
    assert response.status_code == 400 and response.get_json()['message'] == message
    response = client.post('/labeldesigner/api/repository/save', json={'name': 'Use by.json', 'label_size': '62', 'text': text, 'fields': fields})
    assert response.status_code == 400 and response.get_json()['message'] == message
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == []
    # Text may look like it
    fields['Use by']['type'] = 'text'
    response = client.post('/labeldesigner/api/repository/save', json={'name': 'Use by.json', 'label_size': '62', 'text': text, 'fields': fields})
    assert response.status_code == 200


@pytest.mark.parametrize('fields', ['Due', '["Due"]', '{"Due": "date"}',
                                    # What is in them as well: a format that is
                                    # no text was written as its Python text
                                    '{"Due": {"type": "date", "format": {"x": 1}}}', '{"Due": {"type": "color"}}',
                                    '{"Due": {"type": ["date"]}}', '{"Due": {"default": true}}', '{"Due": {"default": [1]}}',
                                    # Nested deeper than Python reads
                                    '[' * 100000])
def test_settings_of_blanks_must_be_an_object_of_objects(tmp_path, fields):
    client = make_client(tmp_path, empty_repo=True)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Due}}', 'size': '40'}]), fields=fields)
    message = ('fields must be a JSON object with the settings of each blank: a type of text, number or date, '
               'a format as text and a default as text or number, e.g. {"Frozen on": {"type": "date"}}')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == message
    # Also where they are saved with a label, and as an object
    for given in (fields, *([json.loads(fields)] if fields.startswith('{') else [])):
        response = client.post('/labeldesigner/api/repository/save', json={'name': 'Due.json', 'label_size': '62', 'text': data['text'], 'fields': given})
        assert response.status_code == 400 and response.get_json()['message'] == message
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == []


def test_settings_of_blanks_as_the_label_designer_saves_them(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Due}} {{blank:Box}} {{blank:Note}}', 'size': '40'}])
    fields = {'Due': {'type': 'date', 'format': '%d.%m.%Y', 'default': '2026-11-04'}, 'Box': {'type': 'number', 'default': 3},
              'Note': {'type': 'text', 'default': None, 'format': None}}
    for given in (fields, json.dumps(fields)):
        response = client.post('/labeldesigner/api/repository/save', json={'name': 'Due.json', 'label_size': '62', 'text': text, 'fields': given})
        assert response.status_code == 200
    assert client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, text=text, fields=json.dumps(fields))).status_code == 200


def test_blank_values_nested_too_deep_are_refused_where_a_label_is_saved(tmp_path):
    # Read from JSON text, they failed with an error of the server
    client = make_client(tmp_path, empty_repo=True)
    response = client.post('/labeldesigner/api/repository/save', json={'name': 'Box.json', 'label_size': '62', 'text': TEXT, 'blanks': '[' * 100000})
    assert response.status_code == 400 and response.get_json()['message'].startswith('blanks must be a JSON object')


def test_preview_marks_the_blanks_to_fill_in(tmp_path, monkeypatch):
    # The label designer shows them by name. A code that takes the text gets
    # it as printed, with the same random parts as the text.
    from app.labeldesigner import label as label_module
    coded = []
    original = label_module._qr_image
    monkeypatch.setattr(label_module, '_qr_image', lambda data, *args: coded.append(data) or original(data, *args))
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'barcode_type': 'QR', 'code_text': '', 'blanks': json.dumps({'Box': '4'}),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf {{blank:Room}} {{blank:Box}} {{short-uuid}}'}])}
    with client.application.test_request_context():
        for marks, shown in (('1', 'Shelf Room? 4 '), ('0', 'Shelf  4 ')):
            label = create_label_from_request(data, mark_blanks=marks == '1')
            label.generate()
            text = label.text[0]['text']
            assert text.startswith(shown) and len(text) == len(shown) + 8
            assert coded[-1] == 'Shelf  4 ' + text[-8:]


def test_each_blank_is_filled_in_once(tmp_path, monkeypatch):
    # Also for the code, which takes the value of the text
    from app.labeldesigner import label as label_module
    filled = []
    original = label_module.SimpleLabel._fill_blank
    monkeypatch.setattr(label_module.SimpleLabel, '_fill_blank', lambda self, name: filled.append(name) or original(self, name))
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'barcode_type': 'QR', 'code_text': 'box/{{blank:Box}}', 'blanks': json.dumps({'Box': '4'}),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Box {{blank:Box}}, {{blank:Box}}'}])}
    with client.application.test_request_context():
        create_label_from_request(data).generate()
        assert filled == ['Box']
        # Also for an image, which turns to fit the label without text
        filled.clear()
        image = Image.new('L', (300, 600), 0)
        create_label_from_request(dict(data, label_size='62x100', print_type='image'), image=image).generate()
        assert filled == ['Box']


def test_long_dates_of_blanks_are_refused_early(tmp_path, monkeypatch):
    # Many blanks with a long date are refused before they are all made, and
    # a format that would make each date long is refused
    from app.labeldesigner import label as label_module
    made = []
    original = label_module._iso_date
    monkeypatch.setattr(label_module, '_iso_date', lambda value: made.append(value) or original(value))
    client = make_client(tmp_path)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '10', 'text': '{{blank:d}}' * 900}])
    data = dict(EXAMPLE_FORMDATA, text=text, blanks=json.dumps({'d': '2026-11-01'}))
    response = client.post('/labeldesigner/api/preview', data=dict(data, fields=json.dumps({'d': {'type': 'date', 'format': '%A %d %B %Y'}})))
    assert response.status_code == 413 and response.get_json()['message'] == 'Text is too long'
    assert len(made) == 1
    response = client.post('/labeldesigner/api/preview', data=dict(data, fields=json.dumps({'d': {'type': 'date', 'format': '%Y' * 51}})))
    assert response.status_code == 413 and response.get_json()['message'] == 'The date format of a blank is too long'


@pytest.mark.parametrize('barcode_type', ['QR', 'code128'])
def test_preview_shows_the_blanks_of_a_code_that_has_nothing_else(tmp_path, monkeypatch, barcode_type):
    # Printed, a code of blanks left empty takes the text, here there is none:
    # the preview shows what is still to fill in instead of nothing
    from app.labeldesigner import label as label_module
    coded = []
    for name in ('_qr_image', '_barcode_image'):
        original = getattr(label_module, name)
        monkeypatch.setattr(label_module, name, lambda *args, original=original, name=name: coded.append(args[0] if name == '_qr_image' else args[2]) or original(*args))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type=barcode_type, code_text='{{blank:Link}}', text='[]')
    assert client.post('/labeldesigner/api/preview', data=dict(data, mark_blanks='1')).status_code == 200
    assert coded == ['Link?']
    # Printed, an empty code says so
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == ('The QR code is empty' if barcode_type == 'QR' else 'The barcode is empty')


@pytest.mark.parametrize('barcode_type', ['QR', 'code128'])
@pytest.mark.parametrize('code, lines, qr, barcode', [
    # The code takes the text, which is only blanks left empty
    ('', ['{{blank:Name}}'], 'Name?', 'Name?'),
    ('', ['{{blank:A}}', '{{blank:B}}'], 'A?\nB?', 'A?'),
    # The blanks of the code itself
    ('{{blank:Link}}', ['{{blank:Name}}'], 'Link?', 'Link?'),
])
def test_preview_shows_the_blanks_of_a_code_that_takes_empty_text(tmp_path, monkeypatch, barcode_type, code, lines, qr, barcode):
    from app.labeldesigner import label as label_module
    coded = []
    for name in ('_qr_image', '_barcode_image'):
        original = getattr(label_module, name)
        monkeypatch.setattr(label_module, name, lambda *args, original=original, name=name: coded.append(args[0] if name == '_qr_image' else args[2]) or original(*args))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type=barcode_type, code_text=code, mark_blanks='1',
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': line} for line in lines]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert coded == [qr if barcode_type == 'QR' else barcode]


@pytest.mark.parametrize('label_size, side', [('62x100', ''), ('62', 'left'), ('62', 'above')])
def test_preview_beside_blanks_left_empty_is_the_print_with_their_marks(tmp_path, label_size, side):
    # The marks counted as text: an image fitted next to them was laid out
    # in another place and size than on the label printed without them
    client = make_client(tmp_path)

    def preview(**values):
        data = dict(label_size=label_size, print_type='image', image_mode='grayscale', image_position=side,
                    image=_upload(Image.new('L', (300, 600), 128), 'tall.png'),
                    text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Note}}'}]), **values)
        response = client.post('/labeldesigner/api/preview', data=data)
        assert response.status_code == 200
        return response
    marked, printed = preview(mark_blanks='1'), preview()
    (image, text), (image_printed, _) = _image_and_text_boxes(marked.data), _image_and_text_boxes(printed.data)
    assert text and image == image_printed
    assert printed.headers.get('X-Label-Image-Position') == marked.headers.get('X-Label-Image-Position')


def test_numbers_of_blanks_are_printed_as_written(tmp_path):
    client = make_client(tmp_path)
    data = {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Price}} {{blank:Qty}} {{blank:Big}} {{blank:N}}'}])}
    with client.application.test_request_context():
        label = create_label_from_request(dict(data, blanks='{"Price": 2.50, "Qty": 3.0, "Big": 1e21, "N": 7}'))
        label.process_templates()
    assert label.text[0]['text'] == '2.50 3.0 1e21 7'


def test_image_label_with_many_large_blanks_is_refused_without_putting_them_together(tmp_path):
    # Whether the label has text was told by its text with all values put
    # together: 800 blanks of 100 kB were 80 MB before any limit
    import tracemalloc
    client = make_client(tmp_path)
    data = {'label_size': '62x100', 'print_type': 'image', 'blanks': json.dumps({'a': 'x' * 100000}),
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '10', 'text': '{{blank:a}}' * 800}])}
    with client.application.test_request_context():
        tracemalloc.start()
        try:
            with pytest.raises(ValueError, match='Text is too long'):
                create_label_from_request(data, image=Image.new('L', (10, 10), 0)).generate()
            assert tracemalloc.get_traced_memory()[1] < 20_000_000
        finally:
            tracemalloc.stop()


def test_code_of_empty_blanks_and_spaces_takes_the_text(tmp_path, monkeypatch):
    from app.labeldesigner import label as label_module
    coded = []
    original = label_module._qr_image
    monkeypatch.setattr(label_module, '_qr_image', lambda data, *args: coded.append(data) or original(data, *args))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', barcode_type='QR', code_text='{{blank:A}} {{blank:B}}',
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf'}]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert coded == ['Shelf']


def test_blanks_count_toward_the_text_limit(tmp_path):
    # The text is short, the values make it too long
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:A}}' * 900, 'size': '10'}]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    response = client.post('/labeldesigner/api/preview', data=dict(data, blanks=json.dumps({'A': 'x' * 20})))
    assert response.status_code == 413
    assert response.get_json()['message'] == 'Text is too long'


@pytest.mark.parametrize('blanks', ['Garage', '["Garage"]', '{"Room": ["Garage"]}', '{"Room": null}', '{"Room": true}'])
def test_blanks_must_be_an_object_of_texts_or_numbers(tmp_path, blanks):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{blank:Room}}', 'size': '40'}]), blanks=blanks)
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'blanks must be a JSON object with a text or number for each blank, e.g. {"Room": "Garage"}'


@pytest.mark.parametrize('barcode_type, printed', [('QR', 'The QR code is empty'), ('code128', 'The barcode is empty')])
def test_a_code_of_blanks_still_to_fill_in_is_warned_about(tmp_path, barcode_type, printed):
    # The preview showed a code of their marks, printing it was refused
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode', barcode_type=barcode_type, code_text='{{blank:Link}}', text='[]')
    response = client.post('/labeldesigner/api/preview', data=dict(data, mark_blanks='1'))
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') == 'The code is empty until its blanks are filled in.'
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 400 and response.get_json()['message'] == printed


def test_text_as_written_filled_in_and_drawn(tmp_path):
    # A line of a blank left empty: laid out as written, without text once
    # filled in, drawn with its mark in a preview only. Never text on a label
    # of a code only.
    client = make_client(tmp_path)
    data = {'print_type': 'qrcode_text', 'barcode_type': 'QR', 'code_text': 'https://example.com',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Room}}'}])}
    code = Image.new('1', (10, 10))
    with client.application.test_request_context():
        for marks, drawn in ((True, True), (False, False)):
            label = create_label_from_request(data, mark_blanks=marks)
            label.process_templates()
            assert (label.has_lines(), label.has_text(), label.want_text(code), label.want_text(None)) == (True, False, drawn, True)
        label = create_label_from_request(dict(data, print_type='qrcode', blanks=json.dumps({'Room': 'Garage'})))
        label.process_templates()
        assert (label.has_lines(), label.has_text(), label.want_text(code)) == (False, True, False)
