"""Files, links and texts shared with the label designer."""

import base64
import io

import pytest
from PIL import Image

from tests.helpers import make_client


def test_shared_file_opens_the_label_designer_once(tmp_path):
    # Android posts it from the share menu, possibly with "Origin: null"
    client = make_client(tmp_path)
    png = io.BytesIO()
    Image.new('L', (40, 20), 'black').save(png, format='PNG')
    response = client.post('/labeldesigner/share', headers={'Origin': 'null'},
                           data={'file': (io.BytesIO(png.getvalue()), 'photo.png', 'image/png')})
    assert response.status_code == 303
    location = response.headers['Location']
    assert location.startswith('/labeldesigner/?shared=')
    token = location.split('=', 1)[1]
    shared = client.get('/labeldesigner/share', query_string={'token': token}).get_json()
    assert shared == {'name': 'photo.png', 'type': 'image/png', 'data': base64.b64encode(png.getvalue()).decode('ascii')}
    # Once only
    response = client.get('/labeldesigner/share', query_string={'token': token})
    assert response.status_code == 404
    assert response.get_json()['message'] == 'The shared content is no longer available, share it again.'


@pytest.mark.parametrize('form, expected', [
    ({'url': 'https://example.com/a'}, {'url': 'https://example.com/a'}),
    # Android shares a link as text
    ({'title': 'Example', 'text': 'https://example.com/b'}, {'url': 'https://example.com/b'}),
    ({'text': 'Shelf A-12'}, {'text': 'Shelf A-12'}),
])
def test_shared_link_or_text(tmp_path, form, expected):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/share', data=form)
    token = response.headers['Location'].split('=', 1)[1]
    assert client.get('/labeldesigner/share', query_string={'token': token}).get_json() == expected


def test_sharing_nothing_opens_the_label_designer(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/share', data={})
    assert response.status_code == 303
    assert response.headers['Location'] == '/labeldesigner/'


def test_shared_content_is_kept_for_a_few_items_only(tmp_path):
    client = make_client(tmp_path)
    from app.labeldesigner import routes
    tokens = []
    for n in range(routes.SHARE_MAX_ITEMS + 1):
        response = client.post('/labeldesigner/share', data={'text': f'Item {n}'})
        tokens.append(response.headers['Location'].split('=', 1)[1])
    assert client.get('/labeldesigner/share', query_string={'token': tokens[0]}).status_code == 404
    assert client.get('/labeldesigner/share', query_string={'token': tokens[-1]}).get_json() == {'text': f'Item {routes.SHARE_MAX_ITEMS}'}
