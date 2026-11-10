"""Printing and previews of saved labels, with their blanks and options."""

import base64
import io
import json
import os

import pytest
from PIL import Image

from tests.helpers import make_client
from tests.repository.helpers import BLANK_LABEL, SAVE_URL, TEXT


@pytest.mark.parametrize('label', ['EAN-Label', 'QR-Example'])
def test_repository_print_success(tmp_path, label: str):
    client = make_client(tmp_path)

    # Print the stored label (simulator)
    print_url = '/labeldesigner/api/repository/print'
    data = {
        'name': f'{label}.json',
    }
    resp = client.post(print_url, json=data)
    assert resp.status_code == 200
    assert resp.is_json
    data = resp.get_json()
    assert data.get('success') is True


def test_repository_print_wrong_label(tmp_path):
    client = make_client(tmp_path, model='QL-500')
    label = 'URGENT-Text'

    # Attempt to print to an invalid printer path
    print_url = '/labeldesigner/api/repository/print'
    data = {
        'name': f'{label}.json',
    }
    resp = client.post(print_url, json=data)
    assert resp.status_code == 400
    assert resp.is_json
    data = resp.get_json()
    assert 'message' in data
    # The QL-500 cannot print in red
    assert data['message'] == 'The label size 62 mm endless, black/red/white (DK-22251 / DK-2251) cannot be printed on a QL-500'
    assert data.get('success') is False


def test_blank_values_saved_with_a_label_are_used(tmp_path):
    # Thrown away for the values of the request, also when it had none
    from tests.helpers import pixels
    client = make_client(tmp_path, empty_repo=True)
    text = [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': 'Shelf {{blank:Room}}'}]
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': text, 'blanks': {'Room': 'Garage'}}).status_code == 200

    def preview(**query):
        response = client.get('/labeldesigner/api/repository/preview', query_string=dict({'name': 'Shelf.json'}, **query))
        assert response.status_code == 200
        return pixels(response.data)
    garage = preview(blanks=json.dumps({'Room': 'Garage'}))
    assert preview() == garage
    # Those of the request come first
    assert preview(blanks=json.dumps({'Room': 'Attic'})) != garage


def test_printing_a_saved_label_takes_false_and_whole_numbers_of_a_json_body(tmp_path, monkeypatch):
    # False could not turn off what the label was saved with, 2.0 copies
    # were refused
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue',
                        lambda self: queued.extend(zip(self.label_cuts(), (e['high_res'] for e in self._print_queue))) or '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT, 'high_res': 1, 'cut_once': 1}).status_code == 200
    body = {'name': 'Shelf.json', 'print_count': 2.0, 'high_res': False, 'cut_once': False}
    assert client.post('/labeldesigner/api/repository/print', json=body).get_json() == {'success': True}
    assert queued == [(True, False), (True, False)]
    assert client.post('/labeldesigner/api/repository/print', json=dict(body, print_count=2.5)).status_code == 400


def test_marks_of_blanks_are_not_printed_or_saved(tmp_path, monkeypatch):
    # Sent with a print or saved with a label, the label printed showed
    # "Name?" where the blank was left empty
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    text = [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf {{blank:Name}}'}]
    assert client.post('/labeldesigner/api/print', data={'label_size': '62', 'text': json.dumps(text), 'mark_blanks': '1'}).get_json() == {'success': True}
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': text, 'mark_blanks': 1}).status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'), encoding='utf-8') as f:
        assert 'mark_blanks' not in json.load(f)
    assert client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json'}).get_json() == {'success': True}
    for label in queued:
        label.process_templates()
        assert label.text[0]['text'] == 'Shelf '


def test_text_too_long_of_a_saved_label_answers_too_large(tmp_path):
    # Like /api/preview, the preview of a saved label answered 400
    client = make_client(tmp_path, empty_repo=True)
    text = [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Note}}'}]
    assert client.post(SAVE_URL, json={'name': 'Note.json', 'label_size': '62', 'text': text}).status_code == 200
    response = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Note.json', 'blanks': json.dumps({'Note': 'x' * 11000})})
    assert response.status_code == 413 and response.get_json()['message'] == 'Text is too long'


def test_preview_of_a_saved_label_tells_what_a_preview_tells(tmp_path):
    # Only /api/preview told where the code went and its distance
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'https://example.com',
                                       'image_position': 'left', 'text': TEXT}).status_code == 200
    response = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Shelf.json'})
    assert response.headers.get('X-Label-Image-Position') == 'left' and int(response.headers['X-Label-Image-Text-Gap']) > 0


def test_marks_of_blanks_saved_with_a_label_are_not_shown(tmp_path):
    # Only the previews of the label designer ask for them, a label saved by
    # hand with them showed them in its preview
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    label = {'label_size': '62', 'text': [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf {{blank:Room}}'}]}
    for name, extra in (('Marked.json', {'mark_blanks': 1}), ('Plain.json', {})):
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as f:
            json.dump(dict(label, **extra), f)
    from tests.helpers import pixels
    previews = [pixels(client.get('/labeldesigner/api/repository/preview', query_string={'name': name}).data) for name in ('Marked.json', 'Plain.json')]
    assert previews[0] == previews[1]


def test_the_copies_of_a_print_share_the_text_measured(tmp_path, monkeypatch):
    # Each copy searched the size of filled text anew
    from app.labeldesigner import printer, label as label_module
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: label.generate())
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    measured = []
    draw = label_module.SimpleLabel._draw_text
    monkeypatch.setattr(label_module.SimpleLabel, '_draw_text', lambda self, img=None, *args: (measured.append(1) if img is None else None) or draw(self, img, *args))
    client = make_client(tmp_path, empty_repo=True)
    text = [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Pantry'}, {'font': 'DejaVu Sans,Book', 'size': '30', 'text': 'Shelf 2'}]
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'orientation': 'rotated', 'text_fit': 'fill', 'text': text}).status_code == 200
    counts = []
    for copies in (1, 3):
        measured.clear()
        assert client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'print_count': copies}).get_json() == {'success': True}
        counts.append(len(measured))
    assert counts[0] == counts[1]


def test_preview_of_a_saved_label_takes_its_parameters_from_a_json_body(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    response = client.post('/labeldesigner/api/repository/preview', json={'name': 'Shelf.json', 'return_format': 'base64'})
    assert response.status_code == 200 and response.content_type.startswith('text/plain')
    assert base64.b64decode(response.data).startswith(b'\x89PNG')


def test_repository_print_uses_requested_resolution(tmp_path, monkeypatch):
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append((label._width, high_res)))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path)
    resp = client.post('/labeldesigner/api/repository/print', data={'name': 'QR-Example.json', 'high_res': '1'})
    assert resp.get_json() == {'success': True}
    # The labels are generated with the 600 dpi width they are printed with
    assert queued and all(entry == (1392, True) for entry in queued)


def test_repository_label_with_a_font_that_is_not_installed(tmp_path):
    # e.g. a label saved on an installation with other fonts
    client = make_client(tmp_path, empty_repo=True)
    with open(os.path.join(os.path.dirname(__file__), '../../labels/QR-Example.json'), encoding='utf-8') as f:
        label = json.load(f)
    for line in label['text']:
        line['font'] = 'Not Installed,Regular'
    label['name'] = 'other_fonts.json'
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200
    response = client.get('/labeldesigner/api/repository/preview?name=other_fonts.json')
    assert response.status_code == 200
    assert response.headers['X-Label-Warning'].startswith('The font Not Installed (Regular) is not installed')
    response = client.post('/labeldesigner/api/repository/print', json={'name': 'other_fonts.json'})
    assert response.status_code == 200
    assert response.get_json()['success'] is True


def test_repository_pdf_pages_are_printed(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path, empty_repo=True)
    pages = [Image.new('L', size, 'black') for size in ((300, 200), (200, 300))]
    pdf = io.BytesIO()
    pages[0].save(pdf, format='PDF', save_all=True, append_images=pages[1:])
    resp = client.post(SAVE_URL, json={'name': 'pages', 'label_size': '62', 'print_type': 'image', 'image_mode': 'grayscale',
                                       'image_fit': '1', 'text': [], 'pdf_pages': 'all', 'image_mime': 'application/pdf',
                                       'image_data': base64.b64encode(pdf.getvalue()).decode('ascii')})
    assert resp.status_code == 200
    resp = client.post('/labeldesigner/api/repository/print', json={'name': 'pages.json', 'printer': 'simulation'})
    assert resp.status_code == 200, resp.get_json()
    assert len(os.listdir(printer.SIMULATED_LABELS_DIR)) == 2


# Saved with the label and requested, the request overrides what is saved
@pytest.mark.parametrize('saved, collate, order', [
    (None, None, [0, 1, 0, 1]), (None, '1', [0, 1, 0, 1]), (None, '0', [0, 0, 1, 1]),
    ('0', None, [0, 0, 1, 1]), ('0', '1', [0, 1, 0, 1]),
])
def test_repository_pdf_pages_are_collated_or_grouped(tmp_path, saved, collate, order):
    from app.labeldesigner import printer
    client = make_client(tmp_path, empty_repo=True)
    pages = [Image.new('L', size, 'black') for size in ((300, 200), (200, 300))]
    pdf = io.BytesIO()
    pages[0].save(pdf, format='PDF', save_all=True, append_images=pages[1:])
    label = {'name': 'pages', 'label_size': '62', 'print_type': 'image', 'image_mode': 'grayscale',
             'image_fit': '1', 'text': [], 'pdf_pages': 'all', 'image_mime': 'application/pdf',
             'image_data': base64.b64encode(pdf.getvalue()).decode('ascii')}
    if saved is not None:
        label['collate'] = saved
    assert client.post(SAVE_URL, json=label).status_code == 200
    body = {'name': 'pages.json', 'printer': 'simulation', 'print_count': 2}
    if collate is not None:
        body['collate'] = collate
    resp = client.post('/labeldesigner/api/repository/print', json=body)
    assert resp.status_code == 200, resp.get_json()
    names = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    heights = [Image.open(os.path.join(printer.SIMULATED_LABELS_DIR, name)).height for name in names]
    first = heights[0]
    assert [0 if h == first else 1 for h in heights] == order


@pytest.mark.parametrize('request_kwargs', [
    {'json': {'name': 'Freezer.json', 'blanks': {'What': 'Chili', 'Box': 2}}},
    {'data': {'name': 'Freezer.json', 'blanks': json.dumps({'What': 'Chili', 'Box': 2})}},
    {'query_string': {'name': 'Freezer.json', 'blanks': json.dumps({'What': 'Chili', 'Box': 2})}},
])
def test_saved_label_is_printed_with_its_blanks_filled_in(tmp_path, monkeypatch, request_kwargs):
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BLANK_LABEL).status_code == 200
    resp = client.post('/labeldesigner/api/repository/print', **request_kwargs)
    assert resp.get_json() == {'success': True}
    label = queued[0]
    label.process_templates()
    assert [line['text'] for line in label.text] == ['Chili', 'Box 2, 2026-11-01']
    assert label.code_text == 'freezer/Chili'


def test_saved_label_preview_has_its_blanks_filled_in(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BLANK_LABEL).status_code == 200
    # The same label with the values written into it. Box gets its default.
    filled = dict(BLANK_LABEL, name='Filled', code_text='freezer/Chili', fields={},
                  text=[dict(BLANK_LABEL['text'][0], text='Chili'), dict(BLANK_LABEL['text'][1], text='Box 1, 2026-11-01')])
    assert client.post(SAVE_URL, json=filled).status_code == 200
    expected = client.get('/labeldesigner/api/repository/preview?name=Filled.json')
    assert expected.status_code == 200
    resp = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Freezer.json', 'blanks': json.dumps({'What': 'Chili'})})
    assert resp.status_code == 200
    assert resp.data == expected.data


def test_numbers_of_blanks_in_a_json_body_are_printed_as_written(tmp_path, monkeypatch):
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BLANK_LABEL).status_code == 200
    body = '{"name": "Freezer.json", "blanks": {"What": 2.50, "Box": 3.0}}'
    assert client.post('/labeldesigner/api/repository/print', data=body, content_type='application/json').status_code == 200
    queued[0].process_templates()
    assert [line['text'] for line in queued[0].text][:2] == ['2.50', 'Box 3.0, 2026-11-01']


def test_saved_label_preview_takes_a_json_body(tmp_path):
    # Like printing it: name and blanks in the body
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BLANK_LABEL).status_code == 200
    blanks = {'What': 'Chili', 'Box': '2'}
    by_body = client.post('/labeldesigner/api/repository/preview', json={'name': 'Freezer.json', 'blanks': blanks})
    by_query = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Freezer.json', 'blanks': json.dumps(blanks)})
    assert by_body.status_code == by_query.status_code == 200 and by_body.data == by_query.data


def test_saved_label_with_invalid_blanks_is_refused(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BLANK_LABEL).status_code == 200
    message = 'blanks must be a JSON object with a text or number for each blank, e.g. {"Room": "Garage"}'
    resp = client.post('/labeldesigner/api/repository/print', json={'name': 'Freezer.json', 'blanks': ['Chili']})
    assert resp.status_code == 400 and resp.get_json()['message'] == message
    resp = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Freezer.json', 'blanks': 'Chili'})
    assert resp.status_code == 400 and resp.get_json()['message'] == message


def test_a_saved_label_is_read_once(tmp_path, monkeypatch):
    # The body, e.g. with an image of some megabytes, was read twice
    from flask import Request
    monkeypatch.setattr(Request, 'get_json', lambda self, *args, **kwargs: pytest.fail('read a second time'))
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'margin_top': 10, 'text': TEXT}).status_code == 200


def test_printing_a_saved_label_takes_its_settings_from_a_json_body(tmp_path, monkeypatch):
    # The blanks were taken from it, the number of copies was not
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: queued.extend(self.label_cuts()) or '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'print_count': 3, 'cut_once': 1}).get_json() == {'success': True}
    assert queued == [False, False, True]


def test_saved_label_with_image_text_gap_null(tmp_path):
    # Saved through the API, null chooses the distance like left out
    client = make_client(tmp_path, empty_repo=True)
    label = {'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'text': TEXT}
    assert client.post(SAVE_URL, json=dict(label, name='Null', image_text_gap=None)).status_code == 200
    assert client.post(SAVE_URL, json=dict(label, name='Automatic')).status_code == 200
    null = client.get('/labeldesigner/api/repository/preview?name=Null.json')
    automatic = client.get('/labeldesigner/api/repository/preview?name=Automatic.json')
    assert null.status_code == automatic.status_code == 200
    assert null.data == automatic.data
