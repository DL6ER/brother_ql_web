import io
import os
import json
import base64

from PIL import Image
from app.utils import fill_first_line_fields
from app.labeldesigner.routes import _text_fit, _whole_or
from test_labeldesigner_api import verify_image, make_client
import pytest


@pytest.mark.parametrize('label', ['EAN-Label', 'QR-Example', 'URGENT-Text'])
def test_repository_save_list_load_delete_and_preview(tmp_path, label: str):
    client = make_client(tmp_path, empty_repo=True)

    # Load some label from file to use as test data
    sample_label_path = os.path.join(
        os.path.dirname(__file__), f'../labels/{label}.json'
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


def test_save_image_restore_other_and_print(tmp_path):
    client = make_client(tmp_path, empty_repo=True)

    # Save another sample label to restore later
    sample_b_path = os.path.join(os.path.dirname(__file__), '../labels/EAN-Label.json')
    with open(sample_b_path, 'r', encoding='utf-8') as fh:
        label_b = json.load(fh)
    label_b['name'] = 'repo_other.json'
    resp = client.post('/labeldesigner/api/repository/save', json=label_b)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True

    # Prepare and save a label that includes a demo image as base64 in JSON
    sample_a_path = os.path.join(os.path.dirname(__file__), '../labels/Kopie-vorab.json')
    with open(sample_a_path, 'r', encoding='utf-8') as fh:
        label_a = json.load(fh)

    # Read demo image
    demo_img_path = os.path.join(os.path.dirname(__file__), '../labels/Kopie-vorab_image.png')
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


TEXT = [{'font': 'DejaVu Sans,Book', 'text': 'x', 'size': '40'}]
SAVE_URL = '/labeldesigner/api/repository/save'


def _png_b64(color: str) -> str:
    buffer = io.BytesIO()
    Image.new('RGB', (40, 40), color).save(buffer, 'PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


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


def test_repository_save_accepts_text_as_json_string(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    resp = client.post(SAVE_URL, json={'name': 'string', 'label_size': '62', 'text': json.dumps(TEXT)})
    assert resp.status_code == 200

    resp = client.get('/labeldesigner/api/repository/load?name=string.json')
    assert resp.status_code == 200
    assert json.loads(resp.get_json()['text']) == TEXT


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


def test_repository_save_failure_keeps_label_consistent(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    label = {'name': 'Alpha', 'label_size': '62', 'text': TEXT}
    assert client.post(SAVE_URL, json={**label, 'image_data': _png_b64('red'), 'image_mime': 'image/png'}).status_code == 200

    # Writing the label fails (e.g. disk full) after the new image was stored
    original = routes._write_atomically

    def failing_write(path, content):
        if path.endswith('.json'):
            raise OSError('No space left on device')
        original(path, content)
    monkeypatch.setattr(routes, '_write_atomically', failing_write)
    resp = client.post(SAVE_URL, json={**label, 'image_data': _png_b64('blue'), 'image_mime': 'image/jpeg'})
    assert resp.status_code == 500

    # The stored label still refers to its old image, which still exists
    with open(os.path.join(repo, 'Alpha.json')) as fh:
        assert json.load(fh)['image'] == 'Alpha_image.png'
    assert os.path.exists(os.path.join(repo, 'Alpha_image.png'))


def test_repository_save_failure_keeps_image_of_same_type(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    label = {'name': 'Alpha', 'label_size': '62', 'text': TEXT, 'image_mime': 'image/png'}
    assert client.post(SAVE_URL, json={**label, 'image_data': _png_b64('red')}).status_code == 200
    with open(os.path.join(repo, 'Alpha_image.png'), 'rb') as fh:
        stored = fh.read()

    def failing_write(path, content):
        raise OSError('No space left on device')
    monkeypatch.setattr(routes, '_write_atomically', failing_write)
    assert client.post(SAVE_URL, json={**label, 'image_data': _png_b64('blue')}).status_code == 500
    with open(os.path.join(repo, 'Alpha_image.png'), 'rb') as fh:
        assert fh.read() == stored
    # No temporary files are left behind
    assert sorted(os.listdir(repo)) == ['Alpha.json', 'Alpha_image.png']


def test_repository_label_cannot_use_image_of_other_label(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Beta', 'label_size': '62', 'text': TEXT, 'image_data': _png_b64('blue'), 'image_mime': 'image/png'}).status_code == 200
    assert client.post(SAVE_URL, json={'name': 'Alpha', 'label_size': '62', 'text': TEXT, 'image': 'Beta_image.png'}).status_code == 200
    loaded = client.get('/labeldesigner/api/repository/load?name=Alpha.json').get_json()
    assert 'image' not in loaded and 'image_data' not in loaded


def test_concurrent_saves_keep_label_readable(tmp_path):
    import threading
    client = make_client(tmp_path, empty_repo=True)
    errors = []

    def save(color):
        for _ in range(10):
            resp = client.application.test_client().post(SAVE_URL, json={'name': 'Alpha', 'label_size': '62', 'text': TEXT, 'image_data': _png_b64(color), 'image_mime': 'image/png'})
            if resp.status_code != 200:
                errors.append(resp.status_code)
    threads = [threading.Thread(target=save, args=(color,)) for color in ('red', 'blue')]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert client.get('/labeldesigner/api/repository/load?name=Alpha.json').status_code == 200


def test_a_saved_label_is_replaced_only_when_asked(tmp_path, monkeypatch):
    # The label designer saves a new label with replace false: a label saved
    # under the name meanwhile, e.g. in another browser, is kept
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']

    def save(**body):
        return client.post(SAVE_URL, json=dict({'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}, **body))

    def title():
        return client.get('/labeldesigner/api/repository/load?name=Shelf.json').get_json()['title']
    assert save(title='Shelf', replace=False).status_code == 200
    response = save(title='Other shelf', replace=False, image_data=_png_b64('red'), image_mime='image/png')
    assert response.status_code == 409
    assert response.get_json() == {'success': False, 'message': 'A label is saved under this name already'}
    assert title() == 'Shelf' and sorted(os.listdir(repo)) == ['Shelf.json']
    with open(os.path.join(repo, 'Shelf.json'), encoding='utf-8') as f:
        assert 'replace' not in json.load(f)
    # Also when it is saved while this one is written
    stage = routes._stage_file

    def saved_meanwhile(directory, content):
        if not os.path.exists(os.path.join(repo, 'Bin.json')):
            with open(os.path.join(repo, 'Bin.json'), 'w', encoding='utf-8') as f:
                json.dump({'title': 'Bin', 'text': TEXT}, f)
        return stage(directory, content)
    monkeypatch.setattr(routes, '_stage_file', saved_meanwhile)
    assert save(name='Bin.json', title='Other bin', replace=False).status_code == 409
    monkeypatch.undo()
    assert client.get('/labeldesigner/api/repository/load?name=Bin.json').get_json()['title'] == 'Bin'
    assert sorted(os.listdir(repo)) == ['Bin.json', 'Shelf.json']
    # Without replace or with it true it is replaced, as before
    assert save(title='Other shelf').status_code == 200 and title() == 'Other shelf'
    assert save(title='Third shelf', replace=True).status_code == 200 and title() == 'Third shelf'
    # Read like the other flags, also as text or 0 and 1, of the body or the
    # address
    for kept in ('false', 0, '0'):
        assert save(title='Fourth shelf', replace=kept).status_code == 409 and title() == 'Third shelf'
    for replaced in ('true', 1, '1'):
        assert save(title=f'Shelf {replaced!r}', replace=replaced).status_code == 200 and title() == f'Shelf {replaced!r}'
    response = client.post(SAVE_URL + '?replace=false', json={'name': 'Shelf.json', 'title': 'Fifth shelf', 'label_size': '62', 'text': TEXT})
    assert response.status_code == 409
    for wrong in ('maybe', 2, [True]):
        response = save(replace=wrong)
        assert response.status_code == 400 and response.get_json()['message'] == 'replace must be true or false'


def test_saving_waits_for_another_one_only_so_long(tmp_path, monkeypatch):
    # Another worker holding the folder of the labels, e.g. hung on a slow
    # network share, held up all saves and removals for good
    import fcntl
    import threading
    from app.labeldesigner import routes
    monkeypatch.setattr(routes, 'SAVE_LOCK_TIMEOUT', 0.3)
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    held = os.open(repo, os.O_RDONLY)
    fcntl.flock(held, fcntl.LOCK_EX)
    answers = []

    def save():
        answers.append(client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}))
    try:
        saving = threading.Thread(target=save)
        saving.start()
        saving.join(5)
        assert not saving.is_alive()
    finally:
        os.close(held)
    saving.join()
    assert answers[0].status_code == 503
    assert answers[0].get_json() == {'success': False, 'message': 'Another label is being saved, try again'}
    assert os.listdir(repo) == []
    # Free again, it is saved and removed
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert client.post('/labeldesigner/api/repository/delete', json={'name': 'Shelf.json'}).status_code == 200


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


def test_a_label_is_loaded_with_the_image_saved_with_it(tmp_path):
    # Loaded while it was put in place, the label could come without the
    # image that was not there yet
    import threading
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    answers = []
    loading = threading.Thread(target=lambda: answers.append(client.get('/labeldesigner/api/repository/load', query_string={'name': 'Shelf.json'})))
    with routes._saving(client.application.config['LABEL_REPOSITORY_DIR']):
        loading.start()
        loading.join(0.3)
        assert loading.is_alive()
    loading.join(5)
    assert answers[0].status_code == 200


@pytest.mark.parametrize('read', ['print', 'preview'])
def test_a_label_is_printed_with_the_image_saved_with_it(tmp_path, monkeypatch, read):
    # Read while it was put in place, the label could come without the
    # image that was not there yet, or with the one of before removed
    import threading
    from app.labeldesigner import printer, routes
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, *args, **kwargs: None)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    client = make_client(tmp_path, empty_repo=True)
    body = {'name': 'Shelf.json', 'label_size': '62', 'print_type': 'image', 'text': TEXT, 'image_data': _png_b64('red'), 'image_mime': 'image/png'}
    assert client.post(SAVE_URL, json=body).status_code == 200
    answers = []
    request = (lambda: client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json'})) if read == 'print' else (
        lambda: client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Shelf.json'}))
    reading = threading.Thread(target=lambda: answers.append(request()))
    with routes._saving(client.application.config['LABEL_REPOSITORY_DIR']):
        reading.start()
        reading.join(0.3)
        assert reading.is_alive()
    reading.join(5)
    assert answers[0].status_code == 200, answers[0].get_json()


def test_labels_are_loaded_alongside_each_other(tmp_path):
    # Each load waited for the one before, as for a save
    import threading
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    answers = []
    loading = threading.Thread(target=lambda: answers.append(client.get('/labeldesigner/api/repository/load', query_string={'name': 'Shelf.json'})))
    with routes._reading(client.application.config['LABEL_REPOSITORY_DIR']):
        loading.start()
        loading.join(5)
        assert not loading.is_alive()
    assert answers[0].status_code == 200


def test_an_image_that_is_no_name_is_not_kept(tmp_path):
    # A list failed with an error of the server
    client = make_client(tmp_path, empty_repo=True)
    response = client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT, 'image': ['Shelf_image.png']})
    assert response.status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'), encoding='utf-8') as f:
        assert 'image' not in json.load(f)


def test_saving_waits_so_long_in_all(tmp_path, monkeypatch):
    # It waited as long again for the folder after waiting for a save of its
    # own process: 20 seconds where 10 are told
    import threading
    from app.labeldesigner import routes
    monkeypatch.setattr(routes, 'SAVE_LOCK_TIMEOUT', 1.0)
    waits = []
    flock = routes.flock_within
    monkeypatch.setattr(routes, 'flock_within', lambda fd, timeout, *args: waits.append(timeout) or flock(fd, timeout, *args))
    client = make_client(tmp_path, empty_repo=True)
    routes._SAVING.acquire()
    threading.Timer(0.4, routes._SAVING.release).start()
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert waits and waits[0] <= 0.7


def test_a_folder_that_cannot_be_locked_is_told(tmp_path, monkeypatch, caplog):
    # Saves of other processes are not waited for there, nothing told so
    import errno
    import fcntl

    def unlockable(fd, operation):
        raise OSError(errno.ENOLCK, 'No locks available')
    client = make_client(tmp_path, empty_repo=True)
    monkeypatch.setattr(fcntl, 'flock', unlockable)
    from app.labeldesigner import routes
    monkeypatch.setattr(routes, '_told_unlockable', False, raising=False)
    with caplog.at_level('WARNING'):
        assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
        # Told once, not with each save and load
        assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
        assert client.get('/labeldesigner/api/repository/load', query_string={'name': 'Shelf.json'}).status_code == 200
    assert caplog.text.count('Cannot lock the folder of the labels, saving waits for saves of this process only') == 1


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


def test_a_new_label_is_never_there_without_its_content(tmp_path, monkeypatch):
    # A label that is loaded, listed or saved again while it is written, or
    # after the server stopped part way, is either there in full or not at
    # all
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    path = os.path.join(repo, 'Shelf.json')
    seen = []

    def check():
        if os.path.exists(path):
            with open(path, encoding='utf-8') as f:
                seen.append(json.load(f)['title'])

    def checked(step):
        def run(*args, **kwargs):
            check()
            return step(*args, **kwargs)
        return run
    for step in ('link', 'replace', 'remove'):
        monkeypatch.setattr(os, step, checked(getattr(os, step)))
    body = {'name': 'Shelf.json', 'title': 'Shelf', 'label_size': '62', 'text': TEXT, 'replace': False}
    assert client.post(SAVE_URL, json=body).status_code == 200
    assert client.post(SAVE_URL, json=dict(body, title='Other shelf')).status_code == 409
    check()
    monkeypatch.undo()
    assert set(seen) == {'Shelf'}
    # No staged copies are left
    assert os.listdir(repo) == ['Shelf.json']


def test_a_new_label_is_saved_where_the_folder_has_no_hard_links(tmp_path, monkeypatch):
    # e.g. on some network shares
    import errno
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']

    def no_links(src, dst, **kwargs):
        raise OSError(errno.EPERM, 'Operation not permitted')
    monkeypatch.setattr(os, 'link', no_links)
    body = {'name': 'Shelf.json', 'title': 'Shelf', 'label_size': '62', 'text': TEXT, 'replace': False}
    assert client.post(SAVE_URL, json=body).status_code == 200
    assert client.post(SAVE_URL, json=dict(body, title='Other shelf')).status_code == 409
    assert client.get('/labeldesigner/api/repository/load?name=Shelf.json').get_json()['title'] == 'Shelf'
    assert os.listdir(repo) == ['Shelf.json']
    # Where the content cannot be put in place, nothing is left behind
    replace = os.replace

    def full(src, dst):
        if dst.endswith('Bin.json'):
            raise OSError(errno.ENOSPC, 'No space left on device')
        return replace(src, dst)
    monkeypatch.setattr(os, 'replace', full)
    assert client.post(SAVE_URL, json=dict(body, name='Bin.json', title='Bin')).status_code == 500
    assert os.listdir(repo) == ['Shelf.json']


def test_blank_values_saved_with_a_label_are_used(tmp_path):
    # Thrown away for the values of the request, also when it had none
    from test_labeldesigner_api import pixels
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


def test_a_failed_save_keeps_a_label_saved_meanwhile_without_hard_links(tmp_path, monkeypatch):
    # Where the folder has no hard links the name was taken with an empty
    # file first. Saving into it failed, that removed the file, also when
    # another request had saved its label there in the meantime.
    import errno
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    path = os.path.join(repo, 'Bin.json')

    def no_links(src, dst, **kwargs):
        raise OSError(errno.EPERM, 'Operation not permitted')
    replace = os.replace

    def saved_meanwhile(src, dst):
        if os.path.abspath(dst) == os.path.abspath(path) and not os.path.exists(path):
            other = os.path.join(repo, '.other.tmp')
            with open(other, 'w', encoding='utf-8') as f:
                json.dump({'title': 'Other bin', 'text': TEXT}, f)
            replace(other, dst)
            raise OSError(errno.EIO, 'Input/output error')
        return replace(src, dst)
    monkeypatch.setattr(os, 'link', no_links)
    monkeypatch.setattr(os, 'replace', saved_meanwhile)
    response = client.post(SAVE_URL, json={'name': 'Bin.json', 'title': 'Bin', 'label_size': '62', 'text': TEXT, 'replace': False})
    monkeypatch.undo()
    assert response.status_code == 500
    with open(path, encoding='utf-8') as f:
        assert json.load(f)['title'] == 'Other bin'
    assert os.listdir(repo) == ['Bin.json']


def test_a_new_label_without_hard_links_is_put_in_place_while_no_other_save_is(tmp_path, monkeypatch):
    # The name was taken with an empty file: readers saw it empty, and a
    # label saved under the name meanwhile was overwritten
    import errno
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)

    def no_links(src, dst, **kwargs):
        raise OSError(errno.EPERM, 'Operation not permitted')
    replace, open_file, locked, opened = os.replace, os.open, [], []

    def held(src, dst):
        locked.append(routes._SAVING.locked())
        return replace(src, dst)

    def recorded(path, *args, **kwargs):
        opened.append(os.path.basename(path))
        return open_file(path, *args, **kwargs)
    monkeypatch.setattr(os, 'link', no_links)
    monkeypatch.setattr(os, 'replace', held)
    monkeypatch.setattr(os, 'open', recorded)
    for replacing in (False, True):
        assert client.post(SAVE_URL, json={'name': 'Bin.json', 'title': 'Bin', 'label_size': '62', 'text': TEXT, 'replace': replacing}).status_code == 200
    assert locked == [True, True] and 'Bin.json' not in opened
    assert client.post(SAVE_URL, json={'name': 'Bin.json', 'title': 'Bin', 'label_size': '62', 'text': TEXT, 'replace': False}).status_code == 409


@pytest.mark.parametrize('body', [{'name': 5}, {'name': 5.0}])
def test_a_name_that_is_a_number_is_refused(tmp_path, body):
    # Read as written, a number passed as text
    client = make_client(tmp_path)
    for url in ('/labeldesigner/api/repository/print', '/labeldesigner/api/repository/delete', '/labeldesigner/api/repository/preview'):
        assert client.post(url, json=body).status_code in (400, 405)
    assert client.post('/labeldesigner/api/demos/print', json={'id': 5}).status_code == 400


def test_printing_a_saved_label_takes_false_and_whole_numbers_of_a_json_body(tmp_path, monkeypatch):
    # False could not turn off what the label was saved with, 2.0 copies
    # were refused
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append((cut, high_res)))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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


def test_saves_are_one_after_the_other_also_in_other_processes(tmp_path, monkeypatch):
    # A lock of threads, workers of gunicorn could save under the same name
    # at the same time where the folder has no hard links. The folder of
    # the labels is locked, a file in the shared folder of temporary files
    # could belong to another user.
    import errno
    import tempfile
    from app.labeldesigner import routes
    if routes.fcntl is None:
        pytest.skip('no locks of files')
    locked = []
    flock = routes.fcntl.flock
    monkeypatch.setattr(routes.fcntl, 'flock', lambda fd, operation: locked.append(os.path.realpath(f'/proc/self/fd/{fd}')) or flock(fd, operation))
    monkeypatch.setattr(tempfile, 'gettempdir', lambda: pytest.fail('a file in the folder of temporary files'))
    client = make_client(tmp_path, empty_repo=True)
    repo = os.path.realpath(client.application.config['LABEL_REPOSITORY_DIR'])
    for replace in (False, True):
        assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT, 'replace': replace}).status_code == 200
    assert locked == [repo, repo]
    assert os.listdir(repo) == ['Shelf.json']

    # A folder that cannot be locked, e.g. on some network shares, saves
    def refused(fd, operation):
        raise OSError(errno.ENOLCK, 'No locks available')
    monkeypatch.setattr(routes.fcntl, 'flock', refused)
    assert client.post(SAVE_URL, json={'name': 'Bin.json', 'label_size': '62', 'text': TEXT, 'replace': False}).status_code == 200


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
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    response = client.post('/labeldesigner/api/repository/print', data='{"name": "Shelf.json"}', content_type='application/x-www-form-urlencoded')
    assert response.get_json() == {'success': True}


def test_a_label_and_its_image_are_put_in_place_and_removed_under_one_lock(tmp_path, monkeypatch):
    # Only the label was: two saves of a name, one with a PNG and one with a
    # JPEG, could leave the label with an image the other one removed
    from app.labeldesigner import routes
    client = make_client(tmp_path, empty_repo=True)
    replace, remove, steps = os.replace, os.remove, []
    monkeypatch.setattr(os, 'replace', lambda src, dst: steps.append(('replace', os.path.basename(dst), routes._SAVING.locked())) or replace(src, dst))
    monkeypatch.setattr(os, 'remove', lambda path: steps.append(('remove', os.path.basename(path), routes._SAVING.locked())) or remove(path))
    assert client.post(SAVE_URL, json={'name': 'Bin.json', 'label_size': '62', 'text': TEXT, 'image_data': _png_b64('red'), 'image_mime': 'image/png'}).status_code == 200
    assert client.post(SAVE_URL, json={'name': 'Bin.json', 'label_size': '62', 'text': TEXT, 'image_data': _png_b64('red'), 'image_mime': 'image/jpeg'}).status_code == 200
    assert client.post('/labeldesigner/api/repository/delete', json={'name': 'Bin.json'}).get_json() == {'success': True}
    monkeypatch.undo()
    kept = [step for step in steps if not step[1].startswith('.')]
    assert ('replace', 'Bin_image.png', True) in kept and ('remove', 'Bin_image.png', True) in kept and ('remove', 'Bin.json', True) in kept
    assert all(locked for _, _, locked in kept)


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


def test_a_label_removed_meanwhile_is_not_found(tmp_path, monkeypatch):
    # Removed by another request after it was looked for, its removal failed
    # with an error of the server
    client = make_client(tmp_path, empty_repo=True)
    exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: True if str(path).endswith('Gone.json') else exists(path))
    response = client.post('/labeldesigner/api/repository/delete', json={'name': 'Gone.json'})
    assert response.status_code == 404 and response.get_json()['message'] == 'Not found'


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


def test_a_saved_label_is_saved_when_its_staged_copy_stays(tmp_path, monkeypatch):
    # Saved under its name, the hidden copy could not be removed: the save
    # failed, and once more it was refused as saved already
    client = make_client(tmp_path, empty_repo=True)
    remove = os.remove

    def stays(path):
        if os.path.basename(path).startswith('.') and os.path.exists(os.path.join(os.path.dirname(path), 'Shelf.json')):
            raise PermissionError(1, 'Operation not permitted')
        return remove(path)
    monkeypatch.setattr(os, 'remove', stays)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT, 'replace': False}).status_code == 200


def test_marks_of_blanks_saved_with_a_label_are_not_shown(tmp_path):
    # Only the previews of the label designer ask for them, a label saved by
    # hand with them showed them in its preview
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    label = {'label_size': '62', 'text': [{'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Shelf {{blank:Room}}'}]}
    for name, extra in (('Marked.json', {'mark_blanks': 1}), ('Plain.json', {})):
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as f:
            json.dump(dict(label, **extra), f)
    from test_labeldesigner_api import pixels
    previews = [pixels(client.get('/labeldesigner/api/repository/preview', query_string={'name': name}).data) for name in ('Marked.json', 'Plain.json')]
    assert previews[0] == previews[1]


def test_settings_of_blanks_other_than_defaults_stay_numbers(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    body = {'name': 'Box.json', 'label_size': '62', 'text': TEXT, 'fields': {'Box': {'type': 'number', 'default': 2.50, 'step': 1}}}
    assert client.post(SAVE_URL, data=json.dumps(body), content_type='application/json').status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Box.json'), encoding='utf-8') as f:
        assert json.load(f)['fields']['Box'] == {'type': 'number', 'default': '2.5', 'step': 1}


def test_a_label_is_saved_under_the_name_it_is_printed_and_removed_by(tmp_path):
    # The name of the body went before the one of the address only here
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL + '?name=Address.json', json={'name': 'Body.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert os.listdir(client.application.config['LABEL_REPOSITORY_DIR']) == ['Address.json']


def test_replace_and_options_of_the_address_are_taken_as_text(tmp_path, monkeypatch):
    # replace=false in the address was not looked at, true and false as
    # options of printing were refused
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append((cut, high_res)))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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


def test_the_name_kept_with_a_label_is_the_one_it_is_saved_under(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL + '?name=b', json={'name': 'a.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'b.json'), encoding='utf-8') as f:
        assert json.load(f)['name'] == 'b.json'


def test_the_copies_of_a_print_share_the_text_measured(tmp_path, monkeypatch):
    # Each copy searched the size of filled text anew
    from app.labeldesigner import printer, label as label_module
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: label.generate())
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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


def test_the_list_gives_titles_only_as_text(tmp_path):
    # A title saved through the API may be a number, the label designer
    # takes the titles of the list as text
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Year.json', 'title': 2026, 'label_size': '62', 'text': TEXT}).status_code == 200
    entry = next(f for f in client.get('/labeldesigner/api/repository/list').get_json()['files'] if f['name'] == 'Year.json')
    assert 'title' not in entry


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
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    client = make_client(tmp_path)
    resp = client.post('/labeldesigner/api/repository/print', data={'name': 'QR-Example.json', 'high_res': '1'})
    assert resp.get_json() == {'success': True}
    # The labels are generated with the 600 dpi width they are printed with
    assert queued and all(entry == (1392, True) for entry in queued)


def test_repository_label_with_a_font_that_is_not_installed(tmp_path):
    # e.g. a label saved on an installation with other fonts
    client = make_client(tmp_path, empty_repo=True)
    with open(os.path.join(os.path.dirname(__file__), '../labels/QR-Example.json'), encoding='utf-8') as f:
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


def test_list_has_the_title_the_label_was_saved_under(tmp_path):
    # The file name lacks umlauts and spaces, the label designer shows the title
    client = make_client(tmp_path, empty_repo=True)
    text = [{'text': 'Soup', 'size': '40', 'font': 'DejaVu Sans,Book'}]
    assert client.post('/labeldesigner/api/repository/save', json={'name': 'Kuche', 'title': 'Küche, Regal 2', 'text': text}).status_code == 200
    assert client.post('/labeldesigner/api/repository/save', json={'name': 'Plain', 'text': text}).status_code == 200
    files = {f['name']: f for f in client.get('/labeldesigner/api/repository/list').get_json()['files']}
    assert files['Kuche.json']['title'] == 'Küche, Regal 2'
    assert 'title' not in files['Plain.json']


# A label with blanks as the label designer saves it, with the settings of
# the blanks in "fields"
BLANK_LABEL = {
    'name': 'Freezer', 'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'freezer/{{blank:What}}',
    'text': [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': '{{blank:What}}'},
             {'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Box {{blank:Box}}, {{blank:Frozen on}}'}],
    'fields': {'Box': {'type': 'number', 'default': '1'}, 'Frozen on': {'type': 'date', 'default': '2026-11-01'}},
}


@pytest.mark.parametrize('request_kwargs', [
    {'json': {'name': 'Freezer.json', 'blanks': {'What': 'Chili', 'Box': 2}}},
    {'data': {'name': 'Freezer.json', 'blanks': json.dumps({'What': 'Chili', 'Box': 2})}},
    {'query_string': {'name': 'Freezer.json', 'blanks': json.dumps({'What': 'Chili', 'Box': 2})}},
])
def test_saved_label_is_printed_with_its_blanks_filled_in(tmp_path, monkeypatch, request_kwargs):
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=BLANK_LABEL).status_code == 200
    body = '{"name": "Freezer.json", "blanks": {"What": 2.50, "Box": 3.0}}'
    assert client.post('/labeldesigner/api/repository/print', data=body, content_type='application/json').status_code == 200
    queued[0].process_templates()
    assert [line['text'] for line in queued[0].text][:2] == ['2.50', 'Box 3.0, 2026-11-01']


def test_json_body_is_read_once(tmp_path, monkeypatch):
    from app.labeldesigner import printer
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: None)
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(cut))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json={'name': 'Shelf.json', 'label_size': '62', 'text': TEXT}).status_code == 200
    assert client.post('/labeldesigner/api/repository/print', json={'name': 'Shelf.json', 'print_count': 3, 'cut_once': 1}).get_json() == {'success': True}
    assert queued == [False, False, True]


def test_numbers_of_a_saved_label_are_numbers(tmp_path, monkeypatch):
    # Saved as they were written, 10.0 became "10.0", which the label could
    # not be made with, and a print count of 0 no longer took 1
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
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
