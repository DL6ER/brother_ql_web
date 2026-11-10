"""Saved labels: saving, listing, loading and removing, with their images."""

import base64
import io
import json
import os

import pytest
from PIL import Image

from app.labeldesigner.routes import _text_fit, _whole_or
from app.utils import fill_first_line_fields

from tests.helpers import make_client, verify_image
from tests.repository.helpers import _png_b64, BLANK_LABEL, SAVE_URL, TEXT


@pytest.mark.parametrize('label', ['EAN-Label', 'QR-Example', 'URGENT-Text'])
def test_repository_save_list_load_delete_and_preview(tmp_path, label: str):
    client = make_client(tmp_path, empty_repo=True)

    # Load some label from file to use as test data
    sample_label_path = os.path.join(
        os.path.dirname(__file__), f'../../labels/{label}.json'
    )
    with open(sample_label_path, 'r', encoding='utf-8') as f:
        file = json.load(f)

    # Save the label
    save_url = '/labeldesigner/api/repository/save'
    file["name"] = f'repo_test_{label}.json'
    resp = client.post(save_url, json=file)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True
    assert data.get('name') == f'repo_test_{label}.json'

    # List repository, expect the saved file among them
    resp = client.get('/labeldesigner/api/repository/list')
    assert resp.status_code == 200
    listing = resp.get_json()
    assert 'files' in listing
    files = listing['files']
    names = [f['name'] for f in files]
    assert f'repo_test_{label}.json' in names

    # Check that label_size metadata is present
    entry = next((f for f in files if f['name'] == f'repo_test_{label}.json'), None)
    assert entry is not None
    assert entry.get('label_size') == ('62 mm endless (DK-22205 / DK-2205)' if label != 'URGENT-Text'
                                       else '62 mm endless, black/red/white (DK-22251 / DK-2251)')
    assert entry.get('label_size_id') == ('62' if label != 'URGENT-Text' else '62red')

    # Preview the stored label (base64)
    preview_url = (
        '/labeldesigner/api/repository/preview?'
        f'name=repo_test_{label}.json&return_format=base64'
    )
    resp = client.get(preview_url)
    assert resp.status_code == 200
    # Should return base64 text
    b64 = resp.get_data()
    # decode and check PNG header
    decoded = base64.b64decode(b64)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")

    # Verify image preview
    verify_image(decoded, f'repo_test_{label}.png')

    # Load the stored JSON
    resp = client.get(f'/labeldesigner/api/repository/load?name=repo_test_{label}.json')
    assert resp.status_code == 200
    loaded = resp.get_json()

    # Compare content to original file
    loaded["text"] = json.loads(loaded.get("text", "[]"))
    # Fill first line fields to match what the client would do, the flags
    # and the text size as it shows them
    file = fill_first_line_fields(file["text"], file)
    file.update({'image_fit': '1' if _whole_or(file.get('image_fit'), 1) > 0 else '0',
                 'image_crop': '1' if _whole_or(file.get('image_crop'), 0) > 0 else '0', 'text_fit': _text_fit(file)})
    assert loaded == file

    # Delete the file
    del_url = f'/labeldesigner/api/repository/delete?name=repo_test_{label}.json'
    resp = client.post(del_url)
    assert resp.status_code == 200
    assert resp.get_json().get('success') is True

    # Ensure it's gone
    resp = client.get('/labeldesigner/api/repository/list')
    files = resp.get_json().get('files', [])
    names = [f['name'] for f in files]
    assert f'repo_test_{label}.json' not in names


def test_save_image_restore_other_and_print(tmp_path):
    client = make_client(tmp_path, empty_repo=True)

    # Save another sample label to restore later
    sample_b_path = os.path.join(os.path.dirname(__file__), '../../labels/EAN-Label.json')
    with open(sample_b_path, 'r', encoding='utf-8') as fh:
        label_b = json.load(fh)
    label_b['name'] = 'repo_other.json'
    resp = client.post('/labeldesigner/api/repository/save', json=label_b)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True

    # Prepare and save a label that includes a demo image as base64 in JSON
    sample_a_path = os.path.join(os.path.dirname(__file__), '../../labels/Kopie-vorab.json')
    with open(sample_a_path, 'r', encoding='utf-8') as fh:
        label_a = json.load(fh)

    # Read demo image
    demo_img_path = os.path.join(os.path.dirname(__file__), '../../labels/Kopie-vorab_image.png')
    with open(demo_img_path, 'rb') as imgfh:
        img_b = imgfh.read()

    label_a['name'] = 'repo_image.json'
    label_a['image_data'] = base64.b64encode(img_b).decode('ascii')
    label_a['image_mime'] = 'image/png'
    label_a['image_name'] = 'repo_image_image.png'

    resp = client.post('/labeldesigner/api/repository/save', json=label_a)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True

    # Restore/load the other label
    resp = client.get('/labeldesigner/api/repository/load?name=repo_other.json')

    # Preview the stored label (base64) - this test should NOT return the other
    # label
    preview_url = (
        '/labeldesigner/api/repository/preview?'
        'name=repo_image.json&return_format=base64'
    )
    resp = client.get(preview_url)
    assert resp.status_code == 200
    # Should return base64 text
    b64 = resp.get_data()
    # decode and check PNG header
    decoded = base64.b64decode(b64)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
    # Verify image preview
    verify_image(decoded, 'repo_image.png')
    assert resp.status_code == 200

    # Preview the other label to ensure it's different
    preview_url = (
        '/labeldesigner/api/repository/preview?'
        'name=repo_other.json&return_format=base64'
    )
    resp = client.get(preview_url)
    assert resp.status_code == 200
    # Should return base64 text
    b64 = resp.get_data()
    # decode and check PNG header
    decoded = base64.b64decode(b64)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
    # Verify image preview
    verify_image(decoded, 'repo_other.png')
    assert resp.status_code == 200

    # Now print the image label using the repository print endpoint
    resp = client.post('/labeldesigner/api/repository/print', json={'name': 'repo_image.json'})
    assert resp.status_code == 200
    assert resp.is_json
    data = resp.get_json()
    assert data.get('success') is True


def test_repository_save_and_load_utf8_symbols(tmp_path):
    client = make_client(tmp_path)
    # Preview the stored label
    preview_url = (
        '/labeldesigner/api/repository/preview?'
        'name=UTF-8_Symbols.json&return_format=base64'
    )
    resp = client.get(preview_url)
    assert resp.status_code == 200
    # Should return base64 text
    b64 = resp.get_data()
    # decode and check PNG header
    decoded = base64.b64decode(b64)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")
    # Verify image preview
    verify_image(decoded, 'repo_test_UTF-8_Symbols.png')
    assert resp.status_code == 200


def test_repository_images_are_stored_per_label(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    for name, color in (('Alpha', 'red'), ('Beta', 'blue')):
        resp = client.post(SAVE_URL, json={'name': name, 'label_size': '62', 'print_type': 'image', 'text': TEXT,
                                           'image_data': _png_b64(color), 'image_mime': 'image/png', 'image_name': 'logo.png'})
        assert resp.status_code == 200

    # Each label keeps its own image although both uploads were named logo.png
    for name, color in (('Alpha', (255, 0, 0)), ('Beta', (0, 0, 255))):
        loaded = client.get(f'/labeldesigner/api/repository/load?name={name}.json').get_json()
        image = Image.open(io.BytesIO(base64.b64decode(loaded['image_data']))).convert('RGB')
        assert image.getpixel((0, 0)) == color

    # Deleting a label removes its image, but no other files
    with open(os.path.join(repo, 'Alpha_image_backup.json'), 'w') as fh:
        fh.write('{}')
    resp = client.post('/labeldesigner/api/repository/delete', json={'name': 'Alpha.json'})
    assert resp.get_json().get('success') is True
    assert sorted(os.listdir(repo)) == ['Alpha_image_backup.json', 'Beta.json', 'Beta_image.png']


def test_repository_list_skips_broken_files(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, 'broken.json'), 'w') as fh:
        fh.write('{not json')
    with open(os.path.join(repo, 'list.json'), 'w') as fh:
        fh.write('[1, 2]')
    assert client.post(SAVE_URL, json={'name': 'good', 'label_size': '62', 'text': TEXT}).status_code == 200

    resp = client.get('/labeldesigner/api/repository/list')
    assert resp.status_code == 200
    assert [f['name'] for f in resp.get_json()['files']] == ['good.json']


def test_repository_pdf_image_is_used(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    pdf = io.BytesIO()
    Image.new('RGB', (200, 100), 'black').save(pdf, 'PDF')
    resp = client.post(SAVE_URL, json={'name': 'pdf', 'label_size': '62', 'print_type': 'image', 'image_mode': 'grayscale',
                                       'image_fit': '1', 'text': [], 'image_mime': 'application/pdf',
                                       'image_data': base64.b64encode(pdf.getvalue()).decode('ascii')})
    assert resp.status_code == 200

    resp = client.get('/labeldesigner/api/repository/preview?name=pdf.json')
    assert resp.status_code == 200
    preview = Image.open(io.BytesIO(resp.data)).convert('L')
    # The black 2:1 PDF page is fitted to the label width
    assert preview.height > 200
    assert preview.getextrema()[0] == 0


def test_repository_save_keeps_image_on_invalid_data(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    label = {'name': 'Alpha', 'label_size': '62', 'text': TEXT, 'image_mime': 'image/png'}
    assert client.post(SAVE_URL, json={**label, 'image_data': _png_b64('red')}).status_code == 200
    with open(os.path.join(repo, 'Alpha_image.png'), 'rb') as fh:
        stored = fh.read()

    resp = client.post(SAVE_URL, json={**label, 'image_data': 'not base64!'})
    assert resp.status_code == 400
    assert resp.get_json()['message'] == 'Invalid image data'
    with open(os.path.join(repo, 'Alpha_image.png'), 'rb') as fh:
        assert fh.read() == stored


def test_repository_label_cannot_use_image_of_other_label(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Beta', 'label_size': '62', 'text': TEXT, 'image_data': _png_b64('blue'), 'image_mime': 'image/png'}).status_code == 200
    assert client.post(SAVE_URL, json={'name': 'Alpha', 'label_size': '62', 'text': TEXT, 'image': 'Beta_image.png'}).status_code == 200
    loaded = client.get('/labeldesigner/api/repository/load?name=Alpha.json').get_json()
    assert 'image' not in loaded and 'image_data' not in loaded


def test_flags_of_a_saved_label_are_loaded_as_they_are_printed(tmp_path):
    # The label designer read them on its own, "true" as off; it takes them
    # as the server gives them. One that is refused stays as it is.
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    labels = {
        'A.json': ({'image_fit': 'true', 'image_crop': 'false', 'shrink_text': 'true'}, ('1', '0', 'shrink', 'true')),
        'B.json': ({'image_fit': False, 'image_crop': 1, 'shrink_text': 0}, ('0', '1', 'none', 0)),
        'C.json': ({}, ('1', '0', 'none', None)),
        'D.json': ({'image_fit': 'maybe', 'shrink_text': 'yes'}, ('maybe', '0', None, 'yes')),
        'E.json': ({'text_fit': 'Shrink', 'shrink_text': 'true'}, ('1', '0', 'Shrink', 'true')),
    }
    for name, (flags, _) in labels.items():
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as f:
            json.dump(dict({'title': name, 'label_size': '62', 'text': TEXT}, **flags), f)
    for name, (_, expected) in labels.items():
        loaded = client.get('/labeldesigner/api/repository/load', query_string={'name': name}).get_json()
        assert tuple(loaded.get(key) for key in ('image_fit', 'image_crop', 'text_fit', 'shrink_text')) == expected, name


def test_cable_flag_is_saved_and_loaded(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    resp = client.post(SAVE_URL, json={'name': 'Patch.json', 'label_size': '12', 'text': TEXT, 'cable_flag': True, 'cable_diameter': '8'})
    assert resp.status_code == 200
    loaded = client.get('/labeldesigner/api/repository/load', query_string={'name': 'Patch.json'}).get_json()
    assert (loaded['cable_flag'], loaded['cable_diameter']) == (True, '8')


def test_a_label_whose_image_cannot_be_read_is_not_loaded_without_it(tmp_path):
    # Loaded without its image, the label saved again in the label designer
    # removed the image for good
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, 'Shelf.json'), 'w', encoding='utf-8') as f:
        json.dump({'title': 'Shelf', 'label_size': '62', 'print_type': 'image', 'image': 'Shelf_image.png', 'text': TEXT}, f)
    os.mkdir(os.path.join(repo, 'Shelf_image.png'))
    response = client.get('/labeldesigner/api/repository/load', query_string={'name': 'Shelf.json'})
    assert response.status_code == 500 and response.get_json()['message'] == 'Failed to load file'
    # Without the file it was never there, the label loads without it
    os.rmdir(os.path.join(repo, 'Shelf_image.png'))
    response = client.get('/labeldesigner/api/repository/load', query_string={'name': 'Shelf.json'})
    assert response.status_code == 200 and 'image_data' not in response.get_json()


def test_an_image_that_is_no_name_is_not_kept(tmp_path):
    # A list failed with an error of the server
    client = make_client(tmp_path, empty_repo=True)
    response = client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT, 'image': ['Shelf_image.png']})
    assert response.status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'), encoding='utf-8') as f:
        assert 'image' not in json.load(f)


def test_an_image_removed_from_a_saved_label_goes_with_it(tmp_path):
    # Saved again without it, the image stayed in the folder for good
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    body = {'name': 'Shelf.json', 'label_size': '62', 'print_type': 'image', 'text': TEXT}
    assert client.post(SAVE_URL, json=dict(body, image_data=_png_b64('red'), image_mime='image/png')).status_code == 200
    assert sorted(os.listdir(repo)) == ['Shelf.json', 'Shelf_image.png']
    # Kept where the label names it, as before
    assert client.post(SAVE_URL, json=dict(body, image='Shelf_image.png')).status_code == 200
    assert sorted(os.listdir(repo)) == ['Shelf.json', 'Shelf_image.png']
    assert client.post(SAVE_URL, json=dict(body, print_type='text')).status_code == 200
    assert sorted(os.listdir(repo)) == ['Shelf.json']


def test_a_label_is_saved_under_the_name_it_is_printed_and_removed_by(tmp_path):
    # The name of the body went before the one of the address only here
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL + '?name=Address.json', json={'name': 'Body.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == ['Address.json']


def test_the_name_kept_with_a_label_is_the_one_it_is_saved_under(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL + '?name=b', json={'name': 'a.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'b.json'), encoding='utf-8') as f:
        assert json.load(f)['name'] == 'b.json'


def test_the_list_gives_titles_only_as_text(tmp_path):
    # A title saved through the API may be a number, the label designer
    # takes the titles of the list as text
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Year.json', 'title': 2026, 'label_size': '62', 'text': TEXT}).status_code == 200
    entry = next(f for f in client.get('/labeldesigner/api/repository/list').get_json()['files'] if f['name'] == 'Year.json')
    assert 'title' not in entry


def test_list_has_the_title_the_label_was_saved_under(tmp_path):
    # The file name lacks umlauts and spaces, the label designer shows the title
    client = make_client(tmp_path, empty_repo=True)
    text = [{'text': 'Soup', 'size': '40', 'font': 'DejaVu Sans,Book'}]
    assert client.post('/labeldesigner/api/repository/save', json={'name': 'Kuche', 'title': 'Küche, Regal 2', 'text': text}).status_code == 200
    assert client.post('/labeldesigner/api/repository/save', json={'name': 'Plain', 'text': text}).status_code == 200
    files = {f['name']: f for f in client.get('/labeldesigner/api/repository/list').get_json()['files']}
    assert files['Kuche.json']['title'] == 'Küche, Regal 2'
    assert 'title' not in files['Plain.json']


@pytest.mark.parametrize('values, message', [
    ({'fields': {'Box': 'number'}}, 'fields must be a JSON object with the settings of each blank: a type of text, number or date, '
                                    'a format as text and a default as text or number, e.g. {"Frozen on": {"type": "date"}}'),
    ({'blanks': {'Room': True}}, 'blanks must be a JSON object with a text or number for each blank, e.g. {"Room": "Garage"}'),
])
def test_label_with_invalid_blanks_or_their_settings_is_not_saved(tmp_path, values, message):
    # Saved, the label could not be printed or previewed afterwards
    client = make_client(tmp_path, empty_repo=True)
    resp = client.post(SAVE_URL, json=dict(BLANK_LABEL, **values))
    assert resp.status_code == 400 and resp.get_json()['message'] == message
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == []


@pytest.mark.parametrize('values, message', [
    # null or a list failed with an error of the server
    ({'shrink_text': None}, None),
    ({'shrink_text': [1]}, 'shrink_text must be 0 or 1'),
    ({'image_text_gap': 12.5}, 'image_text_gap must be a whole number of dots or empty'),
    ({'image_text_gap': -4}, 'image_text_gap must not be negative'),
    ({'image_position': 'top'}, 'image_position must be above, below, left or right'),
    ({'text_fit': 'large'}, 'text_fit must be none, shrink or fill'),
    ({'image_mode': 'sepia'}, 'image_mode must be grayscale, black_and_white, red_and_black or colored'),
    ({'label_size': '63'}, 'Unknown label_size'),
    ({'label_size': None}, 'Unknown label_size'),
    ({'cable_flag': True, 'label_size': '62x29'}, 'Cable flags need continuous tape'),
    ({'cable_flag': '1', 'cable_diameter': '0'}, 'The cable diameter must be a number from 1 to 50 mm'),
    ({'cable_flag': False, 'cable_diameter': '0'}, None),
])
def test_label_with_settings_its_printing_refuses_is_not_saved(tmp_path, values, message):
    # Saved, it could not be printed or previewed afterwards
    client = make_client(tmp_path, empty_repo=True)
    resp = client.post(SAVE_URL, json=dict({'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}, **values))
    if message is None:
        # Like left out
        assert resp.status_code == 200
        return
    assert resp.status_code == 400 and resp.get_json()['message'] == message
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == []


def test_a_label_size_sent_as_a_number_is_saved_as_the_size_it_names(tmp_path):
    # It was saved as a number, which no label size is, and the label could
    # not be previewed or printed
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': 62, 'text': TEXT}).status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'), encoding='utf-8') as fh:
        assert json.load(fh)['label_size'] == '62'
    response = client.get('/labeldesigner/api/repository/preview?name=Shelf.json')
    assert response.status_code == 200 and response.mimetype == 'image/png'


def test_a_label_saved_with_a_number_as_its_size_is_read_as_the_size_it_names(tmp_path, monkeypatch):
    # Saved like that before
    from app.labeldesigner import printer
    sizes = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, *args, **kwargs: sizes.append(self.label_size))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'), 'w', encoding='utf-8') as fh:
        json.dump({'name': 'Shelf.json', 'label_size': 62, 'text': TEXT}, fh)
    entry = client.get('/labeldesigner/api/repository/list').get_json()['files'][0]
    assert (entry['label_size_id'], entry['label_size']) == ('62', '62 mm endless (DK-22205 / DK-2205)')
    assert client.get('/labeldesigner/api/repository/load?name=Shelf.json').get_json()['label_size'] == '62'
    assert client.get('/labeldesigner/api/repository/preview?name=Shelf.json').status_code == 200
    assert client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json'}).get_json() == {'success': True}
    assert sizes == ['62']


def test_a_label_saved_without_a_size_prints_on_the_size_its_preview_shows(tmp_path, monkeypatch):
    # The printer took LABEL_DEFAULT_SIZE, the label was made for 62 mm
    from app.labeldesigner import printer
    sizes = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, *args, **kwargs: sizes.append(self.label_size))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['LABEL_DEFAULT_SIZE'] = '29'
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'text': TEXT}).status_code == 200
    preview = client.get('/labeldesigner/api/repository/preview?name=Shelf.json')
    on_62 = client.post('/labeldesigner/api/preview', data={'label_size': '62', 'text': json.dumps(TEXT)})
    assert preview.data == on_62.data
    assert client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json'}).get_json() == {'success': True}
    assert sizes == ['62']


@pytest.mark.parametrize('barcode_type, code_text, lines, code', [
    ('QR', 'https://example.com/shelf', ['Hidden', 'Not printed'], 'https://example.com/shelf'),
    ('QR', '', ['BEGIN:VCARD', 'FN:Jane Doe\r\nEND:VCARD'], 'BEGIN:VCARD\nFN:Jane Doe\nEND:VCARD'),
    ('code128', '', ['4006381333931\nShelf', 'Not printed'], '4006381333931'),
    ('QR', 'https://example.com/shelf', [], 'https://example.com/shelf'),
])
def test_a_code_alone_loads_as_a_code_with_its_text_left_empty(tmp_path, barcode_type, code_text, lines, code):
    # The label designer has no layout of a code alone. Its lines were not
    # printed, a code without content of its own took them.
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    text = [{'font': 'DejaVu Sans,Bold', 'size': '50', 'text': line} for line in lines]
    with open(os.path.join(repo, 'Shelf.json'), 'w', encoding='utf-8') as f:
        json.dump({'title': 'Shelf', 'label_size': '62', 'print_type': 'qrcode', 'barcode_type': barcode_type, 'code_text': code_text, 'text': text}, f)
    loaded = client.get('/labeldesigner/api/repository/load', query_string={'name': 'Shelf.json'}).get_json()
    assert (loaded['print_type'], loaded['code_text']) == ('qrcode_text', code)
    assert json.loads(loaded['text']) == ([{'font': 'DejaVu Sans,Bold', 'size': '50', 'text': ''}] if lines else [])
    # Printed as it was saved
    stored = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Shelf.json'})
    form = {key: loaded[key] for key in ('label_size', 'print_type', 'barcode_type', 'code_text', 'text')}
    made = client.post('/labeldesigner/api/preview', data=form, content_type='multipart/form-data')
    assert stored.status_code == made.status_code == 200 and stored.data == made.data
