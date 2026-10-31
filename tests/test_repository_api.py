import io
import os
import json
import base64

from PIL import Image
from app.utils import fill_first_line_fields
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
    # Fill first line fields to match what the client would do
    file = fill_first_line_fields(file["text"], file)
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


def test_repository_print_uses_requested_resolution(tmp_path, monkeypatch):
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False: queued.append((label._width, high_res)))
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
