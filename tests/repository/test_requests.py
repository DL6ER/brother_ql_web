"""Requests to the repository: JSON bodies, forms, the address and names."""

import io
import json
import os

import pytest

from tests.helpers import make_client
from tests.repository.helpers import BLANK_LABEL, SAVE_URL, TEXT


def test_repository_save_requires_json_and_name(tmp_path):
    client = make_client(tmp_path)

    # Missing JSON payload
    resp = client.post(
        '/labeldesigner/api/repository/save',
        data='not-json',
        headers={'Content-Type': 'text/plain'}
    )
    assert resp.status_code == 400

    # Missing name
    resp = client.post(
        '/labeldesigner/api/repository/save',
        json={'foo': 'bar'}
    )
    assert resp.status_code == 400


def test_repository_save_accepts_text_as_json_string(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    resp = client.post(SAVE_URL, json={'name': 'string', 'label_size': '62', 'text': json.dumps(TEXT)})
    assert resp.status_code == 200

    resp = client.get('/labeldesigner/api/repository/load?name=string.json')
    assert resp.status_code == 200
    assert json.loads(resp.get_json()['text']) == TEXT


@pytest.mark.parametrize('body', [{'name': 5}, {'name': 5.0}])
def test_a_name_that_is_a_number_is_refused(tmp_path, body):
    # Read as written, a number passed as text
    client = make_client(tmp_path)
    for url in ('/labeldesigner/api/repository/print', '/labeldesigner/api/repository/delete', '/labeldesigner/api/repository/preview'):
        assert client.post(url, json=body).status_code in (400, 405)
    assert client.post('/labeldesigner/api/demos/print', json={'id': 5}).status_code == 400


def test_a_number_python_does_not_take_makes_no_json_body(tmp_path):
    # Its message of Python came back instead of the one of the request
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/repository/delete', data='{"name": 1' + '0' * 5000 + '}', content_type='application/json')
    assert response.status_code == 400 and response.get_json()['message'] == 'No name specified'


def test_an_upload_as_a_form_is_not_read_as_json(tmp_path, monkeypatch):
    # The whole form was read into memory once more to look for JSON in it
    from flask import Request
    monkeypatch.setattr(Request, 'get_data', lambda self, *args, **kwargs: pytest.fail('read as JSON'))
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/repository/print', data={'name': 'Missing.json', 'file': (io.BytesIO(b'x' * 1000), 'x.bin')},
                           content_type='multipart/form-data')
    assert response.status_code == 404


def test_an_empty_json_object_is_saved_under_the_name_of_the_address(tmp_path):
    # Taken for no JSON body, the name in the address was not looked at
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL + '?name=Blank', json={}).status_code == 200
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == ['Blank.json']
    assert client.post(SAVE_URL + '?name=Other', data='not json', content_type='application/json').status_code == 400


def test_parameters_of_the_address_go_before_those_of_a_json_body(tmp_path, monkeypatch):
    # Like the webhook always took them: a body that stays the same, the
    # address changes it. The webhook reads the body like the others.
    from flask import Request
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert client.post('/labeldesigner/api/repository/print?print_count=2', json={'name': 'Shelf.json', 'print_count': 3}).get_json() == {'success': True}
    assert len(queued) == 2
    monkeypatch.setattr(Request, 'get_json', lambda self, *args, **kwargs: pytest.fail('a parser of its own'))
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    assert client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': []}).status_code == 400


def test_json_sent_as_a_form_is_read(tmp_path, monkeypatch):
    # curl -d sends JSON as a form: read as a form first, the body was gone
    from app.labeldesigner import printer
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: None)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    response = client.post('/labeldesigner/api/repository/print', data='{"name": "Shelf.json"}', content_type='application/x-www-form-urlencoded')
    assert response.get_json() == {'success': True}


def test_blanks_and_their_settings_sent_as_json_text_are_saved_as_objects(tmp_path):
    # Saved as text, the label designer did not take them and dropped them
    client = make_client(tmp_path, empty_repo=True)
    body = {'name': 'Shelf.json', 'label_size': '62', 'text': TEXT, 'blanks': '{"N": 3}', 'fields': '{"N": {"type": "number"}}'}
    assert client.post(SAVE_URL, json=body).status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'), encoding='utf-8') as f:
        saved = json.load(f)
    assert saved['blanks'] == {'N': '3'} and saved['fields'] == {'N': {'type': 'number'}}


def test_a_body_with_a_number_python_does_not_take_is_no_json_payload(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    response = client.post(SAVE_URL, data='{"name": "Shelf", "x": 1' + '0' * 5000 + '}', content_type='application/json')
    assert response.status_code == 400 and response.get_json()['message'] == 'No JSON payload provided'


def test_a_body_nested_too_deep_is_no_json_body(tmp_path):
    # Read with Python it failed with an error of the server, also before
    # the password of the webhook was looked at
    client = make_client(tmp_path)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    body = '{"x": ' + '[' * 3000 + ']' * 3000 + '}'
    assert client.post('/labeldesigner/api/webhook/print', data=body, content_type='application/json').status_code == 401
    assert client.post('/labeldesigner/api/repository/delete', data=body, content_type='application/json').status_code == 400


def test_settings_of_blanks_other_than_defaults_stay_numbers(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    body = {'name': 'Box.json', 'label_size': '62', 'text': TEXT, 'fields': {'Box': {'type': 'number', 'default': 2.50, 'step': 1}}}
    assert client.post(SAVE_URL, data=json.dumps(body), content_type='application/json').status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Box.json'), encoding='utf-8') as f:
        assert json.load(f)['fields']['Box'] == {'type': 'number', 'default': '2.5', 'step': 1}


def test_replace_and_options_of_the_address_are_taken_as_text(tmp_path, monkeypatch):
    # replace=false in the address was not looked at, true and false as
    # options of printing were refused
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append((cut, high_res)))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert client.post(SAVE_URL + '?replace=false', json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 409
    assert client.post(SAVE_URL + '?replace=maybe', json={'name': 'Other.json', 'label_size': '62', 'text': TEXT}).status_code == 400
    response = client.post('/labeldesigner/api/repository/print?name=Shelf.json&print_count=2&cut_once=true&high_res=false')
    assert response.get_json() == {'success': True} and queued == [(False, False), (True, False)]


def test_texts_of_a_json_body_sent_as_a_form_are_no_parameters(tmp_path):
    # Read as a form, "&name=Box" in the address of a code was taken for the
    # name: the label was saved as Box.json, "&replace=no" refused the save
    client = make_client(tmp_path, empty_repo=True)
    body = json.dumps({'name': 'Shelf.json', 'label_size': '62', 'print_type': 'qrcode', 'code_text': 'https://example.com/item?id=3&name=Box&replace=no', 'text': []})
    response = client.post(SAVE_URL, data=body, content_type='application/x-www-form-urlencoded')
    assert response.status_code == 200 and response.get_json()['name'] == 'Shelf.json'
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == ['Shelf.json']
    # The address still goes before the body
    assert client.post(SAVE_URL + '?name=Address.json', data=body, content_type='application/x-www-form-urlencoded').get_json()['name'] == 'Address.json'


def test_json_body_is_read_once(tmp_path, monkeypatch):
    from app.labeldesigner import printer
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: None)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BLANK_LABEL).status_code == 200
    provider = client.application.json
    read = []
    original = provider.loads
    body = '{"name": "Freezer.json", "blanks": {"What": "Chili"}}'
    # Only the body, the saved label is read as well
    monkeypatch.setattr(provider, 'loads', lambda data, **kwargs: read.append(data) or original(data, **kwargs))
    assert client.post('/labeldesigner/api/repository/print', data=body, content_type='application/json').status_code == 200
    assert [data for data in read if (data.decode() if isinstance(data, bytes) else data) == body] == [body.encode()]


def test_numbers_of_a_saved_label_are_numbers(tmp_path, monkeypatch):
    # Saved as they were written, 10.0 became "10.0", which the label could
    # not be made with, and a print count of 0 no longer took 1
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    body = {'name': 'Shelf.json', 'label_size': '62', 'margin_top': 10.0, 'image_text_gap': 12.0, 'qrcode_size': 6.0, 'print_count': 0,
            'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'text': TEXT}
    assert client.post(SAVE_URL, json=body).status_code == 200
    assert client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Shelf.json'}).status_code == 200
    assert client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json'}).get_json() == {'success': True}
    assert len(queued) == 1
    loaded = client.get('/labeldesigner/api/repository/load', query_string={'name': 'Shelf.json'}).get_json()
    assert loaded['margin_top'] == 10.0


def test_numbers_of_a_saved_label_stay_as_they_are_written(tmp_path):
    # Read as JSON numbers, 2.50 was saved as 2.5
    client = make_client(tmp_path, empty_repo=True)
    body = '{"name": "Price.json", "label_size": "62", "text": [{"text": "{{blank:Price}}", "size": "40"}], "blanks": {"Price": 2.50}, "fields": {"Price": {"type": "number", "default": 1.10}}}'
    assert client.post(SAVE_URL, data=body, content_type='application/json').status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Price.json'), encoding='utf-8') as f:
        saved = json.load(f)
    assert (saved['blanks']['Price'], saved['fields']['Price']['default']) == ('2.50', '1.10')


@pytest.mark.parametrize('body', [{'name': ['Shelf.json']}, {'name': {'a': 1}}])
def test_a_name_that_is_no_text_is_refused(tmp_path, body):
    client = make_client(tmp_path)
    for url in ('/labeldesigner/api/repository/print', '/labeldesigner/api/repository/delete', SAVE_URL):
        assert client.post(url, json=dict(body, label_size='62', text=TEXT)).status_code == 400
