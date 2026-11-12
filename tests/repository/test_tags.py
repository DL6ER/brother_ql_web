"""Tags of saved labels, which the label designer groups them by."""

import json
import os

import pytest

from tests.helpers import make_client
from tests.repository.helpers import SAVE_URL, TEXT

LIST_URL = '/labeldesigner/api/repository/list'


def _tags_listed(client, name):
    return next(f['tags'] for f in client.get(LIST_URL).get_json()['files'] if f['name'] == name)


@pytest.mark.parametrize('given, kept', [
    (['Kitchen', ' Freezer  box ', 'kitchen', ''], ['Kitchen', 'Freezer box']),
    ('Kitchen, Freezer,,kitchen', ['Kitchen', 'Freezer']),
    ([], None),
    ('', None),
])
def test_tags_are_saved_trimmed_and_once(tmp_path, given, kept):
    client = make_client(tmp_path, empty_repo=True)
    response = client.post(SAVE_URL, json={'name': 'Shelf', 'label_size': '62', 'text': TEXT, 'tags': given})
    assert response.get_json() == {'success': True, 'name': 'Shelf.json'}
    with open(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'), encoding='utf-8') as fh:
        assert json.load(fh).get('tags') == kept
    assert _tags_listed(client, 'Shelf.json') == (kept or [])


@pytest.mark.parametrize('given', [7, ['Kitchen', 3], {'Kitchen': True}, ['x' * 41], [f'Tag {n}' for n in range(21)]])
def test_tags_that_are_no_list_of_short_texts_are_refused(tmp_path, given):
    client = make_client(tmp_path, empty_repo=True)
    response = client.post(SAVE_URL, json={'name': 'Shelf', 'label_size': '62', 'text': TEXT, 'tags': given})
    assert response.status_code == 400
    assert response.get_json()['message'].startswith('tags must be a list')
    assert not os.path.exists(os.path.join(client.application.config['LABEL_REPOSITORY_DIR'], 'Shelf.json'))


def test_labels_without_tags_or_with_broken_ones_are_listed_without(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    repo = client.application.config['LABEL_REPOSITORY_DIR']
    for name, tags in (('plain.json', None), ('broken.json', 'Kitchen'), ('odd.json', [1, 2])):
        label = {'label_size': '62', 'text': TEXT}
        if tags is not None:
            label['tags'] = tags
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as fh:
            json.dump(label, fh)
    assert _tags_listed(client, 'plain.json') == []
    # Written by hand as text, read as when it is saved so
    assert _tags_listed(client, 'broken.json') == ['Kitchen']
    assert _tags_listed(client, 'odd.json') == []
