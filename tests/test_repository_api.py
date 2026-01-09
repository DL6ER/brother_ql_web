import os
import json
import base64
from test_labeldesigner_api import verify_image, make_client


def test_repository_save_list_load_delete_and_preview(tmp_path):
    client = make_client(tmp_path)

    # Load some label from file to use as test data
    sample_label_path = os.path.join(
        os.path.dirname(__file__), '../labels/EAN-Label.json'
    )
    with open(sample_label_path, 'r', encoding='utf-8') as f:
        payload = json.load(f)

    # Save the label
    save_url = '/labeldesigner/api/repository/save?name=repo_test'
    resp = client.post(save_url, json=payload)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data.get('success') is True
    assert data.get('name') == 'repo_test.json'

    # List repository, expect the saved file among them
    resp = client.get('/labeldesigner/api/repository/list')
    assert resp.status_code == 200
    listing = resp.get_json()
    assert 'files' in listing
    files = listing['files']
    names = [f['name'] for f in files]
    assert 'repo_test.json' in names

    # Check that label_size metadata is present
    entry = next((f for f in files if f['name'] == 'repo_test.json'), None)
    assert entry is not None
    assert entry.get('label_size') == '62mm endless'

    # Preview the stored label (base64)
    preview_url = (
        '/labeldesigner/api/repository/preview?'
        'name=repo_test.json&return_format=base64'
    )
    resp = client.get(preview_url)
    assert resp.status_code == 200
    # Should return base64 text
    b64 = resp.get_data()
    # decode and check PNG header
    decoded = base64.b64decode(b64)
    assert decoded.startswith(b"\x89PNG\r\n\x1a\n")

    # Verify image preview
    verify_image(decoded, 'repo_test_preview.png')

    # Load the stored JSON
    resp = client.get('/labeldesigner/api/repository/load?name=repo_test.json')
    assert resp.status_code == 200
    loaded = resp.get_json()
    assert loaded.get('label_size') == '62'
    # Compare content to original payload
    loaded["text"] = json.loads(loaded.get("text", "[]"))
    # All keys in payload should be in loaded with same value
    for key, value in payload.items():
        assert key in loaded
        assert loaded[key] == value

    # Delete the file
    del_url = '/labeldesigner/api/repository/delete?name=repo_test.json'
    resp = client.post(del_url)
    assert resp.status_code == 200
    assert resp.get_json().get('success') is True

    # Ensure it's gone
    resp = client.get('/labeldesigner/api/repository/list')
    files = resp.get_json().get('files', [])
    names = [f['name'] for f in files]
    assert 'repo_test.json' not in names


def test_repository_save_requires_json_and_name(tmp_path):
    client = make_client(tmp_path)

    # Missing JSON payload
    resp = client.post(
        '/labeldesigner/api/repository/save?name=noname',
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
