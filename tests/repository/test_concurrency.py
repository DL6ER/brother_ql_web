"""Saved labels while others are saved: locks, replacing, staged files and
hard links."""

import json
import os

import pytest

from tests.helpers import make_client
from tests.repository.helpers import _png_b64, SAVE_URL, TEXT


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
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
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


def test_a_label_removed_meanwhile_is_not_found(tmp_path, monkeypatch):
    # Removed by another request after it was looked for, its removal failed
    # with an error of the server
    client = make_client(tmp_path, empty_repo=True)
    exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: True if str(path).endswith('Gone.json') else exists(path))
    response = client.post('/labeldesigner/api/repository/delete', json={'name': 'Gone.json'})
    assert response.status_code == 404 and response.get_json()['message'] == 'Not found'


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
