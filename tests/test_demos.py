"""The demo labels that come with the label designer."""
import base64
import io
import json
import os
import re

import pytest
from PIL import Image
from brother_ql.labels import ALL_LABELS

from app.labeldesigner import demos
from test_labeldesigner_api import make_client

LABEL_SIZES = {label.identifier for label in ALL_LABELS}
DEMO_IDS = [label_id for category in demos.categories() for label_id, _ in demos.labels(category['id'])]


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    return make_client(tmp_path_factory.mktemp('demos'), empty_repo=True)


def test_list(client):
    categories = client.get('/labeldesigner/api/demos/list').get_json()['categories']
    assert [c['id'] for c in categories] == [c['id'] for c in demos.categories()]
    assert categories[0] == dict(categories[0], id='address', name='Address and shipping')
    labels = [label for category in categories for label in category['labels']]
    assert len(labels) == len(DEMO_IDS)
    fire = next(label for label in labels if label['id'] == 'signs/001-fire-extinguisher-62.json')
    assert fire == {'id': 'signs/001-fire-extinguisher-62.json', 'title': 'Fire extinguisher', 'language': 'en',
                    'label_size_id': '62', 'label_size': '62 mm endless (DK-22205 / DK-2205)'}
    german = next(label for label in labels if label['id'] == 'signs/001-fire-extinguisher-62-de.json')
    assert german['title'] == 'Feuerlöscher' and german['language'] == 'de'


def test_every_demo_label_in_english_and_german():
    english = {label_id for label_id in DEMO_IDS if not label_id.endswith('-de.json')}
    assert {label_id for label_id in DEMO_IDS if label_id not in english} == {label_id[:-len('.json')] + '-de.json' for label_id in english}
    for label_id in english:
        en, de = demos.load(label_id), demos.load(label_id[:-len('.json')] + '-de.json')
        assert (en['language'], de['language']) == ('en', 'de')
        # The same label in the other language
        same = ('label_size', 'orientation', 'print_type', 'image', 'image_mode', 'barcode_type')
        assert [en.get(key) for key in same] == [de.get(key) for key in same]
        # Translated, unless the label reads the same in both, e.g. "Defibrillator"
        assert en['title'] != de['title'] or en['text'] == de['text']


@pytest.mark.parametrize('label_id', DEMO_IDS)
def test_every_demo_label_fits(client, label_id):
    data = demos.load(label_id)
    assert data['title'] and data['label_size'] in LABEL_SIZES
    # Only ASCII, like everything else in the repository, German umlauts and the euro sign
    assert set(json.dumps(data, ensure_ascii=False)) - set(map(chr, range(128))) <= set('äöüÄÖÜß€')
    response = client.get('/labeldesigner/api/demos/preview', query_string={'id': label_id})
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    preview = Image.open(io.BytesIO(response.data))
    # Not empty
    assert preview.convert('L').getextrema()[0] == 0


@pytest.mark.parametrize('old, new', sorted(demos.REPLACED.items()))
def test_replaced_demo_labels_lead_to_those_in_their_place(client, old, new):
    # Links to them, e.g. for printing, answered not found
    assert new in DEMO_IDS and old not in DEMO_IDS
    response = client.get('/labeldesigner/api/demos/load', query_string={'id': old})
    assert response.status_code == 200
    assert response.get_json()['title'] == demos.load(new)['title']
    previews = [client.get('/labeldesigner/api/demos/preview', query_string={'id': label_id}) for label_id in (old, new)]
    assert previews[0].status_code == 200 and previews[0].data == previews[1].data


@pytest.mark.parametrize('label_id', [['signs/001-fire-extinguisher-62.json'], {'a': 1}])
def test_a_demo_id_that_is_no_text_is_refused(client, label_id):
    # It failed with an error of the server
    response = client.post('/labeldesigner/api/demos/print', json={'id': label_id})
    assert response.status_code == 400 and response.get_json()['message'] == 'No demo label specified'
    with pytest.raises(FileNotFoundError):
        demos.load(label_id)


def test_images_and_their_sources():
    used = {demos.load(label_id).get('image') for label_id in DEMO_IDS} - {None}
    files = set(os.listdir(demos.IMAGE_DIR)) - {'SOURCES.md'}
    assert used == files
    with open(os.path.join(demos.IMAGE_DIR, 'SOURCES.md'), encoding='utf-8') as fh:
        sources = fh.read()
    assert set(re.findall(r'^\| `([^`]+)` \| \[.+\]\(https://commons\.wikimedia\.org/wiki/File:.+\) \| .+ \| (?:Public domain|CC0) \|$',
                          sources, re.M)) == files


def test_load(client):
    data = client.get('/labeldesigner/api/demos/load', query_string={'id': 'signs/001-fire-extinguisher-62.json'}).get_json()
    assert data['title'] == 'Fire extinguisher'
    assert json.loads(data['text'])[0]['text'] == 'Fire'
    # The settings of the first line for the font controls
    assert data['font_size'] == json.loads(data['text'])[0]['size']
    # The image for the image upload of the page
    assert data['image_name'] == 'fire-extinguisher.png' and data['image_mime'] == 'image/png'
    with open(os.path.join(demos.IMAGE_DIR, 'fire-extinguisher.png'), 'rb') as fh:
        assert base64.b64decode(data['image_data']) == fh.read()


@pytest.mark.parametrize('label_id', ['signs/999-unknown-62.json', 'unknown/001-fire-extinguisher-62.json', 'signs/../categories.json',
                                      '../demos/signs/001-fire-extinguisher-62.json', 'signs/x/../001-fire-extinguisher-62.json',
                                      'images/fire-extinguisher.png', 'signs'])
def test_unknown_demo_labels(client, label_id):
    for url in ('/labeldesigner/api/demos/load', '/labeldesigner/api/demos/preview'):
        response = client.get(url, query_string={'id': label_id})
        assert response.status_code == 404, url
        assert response.get_json() == {'success': False, 'message': 'Not found'}
    assert client.post('/labeldesigner/api/demos/print', data={'id': label_id}).status_code == 404


def test_no_demo_label(client):
    for response in (client.get('/labeldesigner/api/demos/load'), client.get('/labeldesigner/api/demos/preview'),
                     client.post('/labeldesigner/api/demos/print')):
        assert response.status_code == 400
        assert response.get_json() == {'success': False, 'message': 'No demo label specified'}


@pytest.mark.parametrize('label_id', ['signs/001-fire-extinguisher-62.json', 'signs/002-fire-extinguisher-62red.json'])
def test_print(client, monkeypatch, label_id):
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue',
                        lambda self, label, cut=True, high_res=False, fast=False: queued.append((self.label_size, label)))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    response = client.post('/labeldesigner/api/demos/print', json={'id': label_id})
    assert response.get_json() == {'success': True}
    (label_size, label), = queued
    assert label_size == demos.load(label_id)['label_size']
    # With the pictogram from the demo images, red on black/red labels
    colors = {color for _, color in label.generate().convert('RGB').getcolors(1 << 20)}
    assert (0, 0, 0) in colors
    assert any(r > 120 and g < 60 and b < 60 for r, g, b in colors) == label_id.endswith('62red.json')
