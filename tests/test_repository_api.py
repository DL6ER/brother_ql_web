import os
import json
import base64

from flask.testing import FlaskClient
from app.utils import fill_first_line_fields
from test_labeldesigner_api import verify_image, make_client
import pytest


def _test_repository_save_sample_labels(label: str, client: FlaskClient):
    # Copy sample label to repository
    sample_label_path = os.path.join(
        os.path.dirname(__file__), f'../labels/{label}.json'
    )
    with open(sample_label_path, 'r', encoding='utf-8') as f:
        file = json.load(f)
        file["name"] = f'{label}.json'
    save_url = '/labeldesigner/api/repository/save'
    resp = client.post(save_url, json=file)
    assert resp.status_code == 200


@pytest.mark.parametrize('label', ['EAN-Label', 'QR-Example', 'URGENT-Text'])
def test_repository_save_list_load_delete_and_preview(tmp_path, label: str):
    client = make_client(tmp_path)

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
    assert entry.get('label_size') == '62mm endless' if label != 'URGENT-Text' else '62mm endless (black/red/white)'

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
    _test_repository_save_sample_labels(label, client)

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
    client = make_client(tmp_path)
    label = 'URGENT-Text'
    _test_repository_save_sample_labels(label, client)

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
    assert "Printing in red is not supported with the selected model." in data['message']
    assert data.get('success') is False


def test_save_image_restore_other_and_print(tmp_path):
    client = make_client(tmp_path)

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
