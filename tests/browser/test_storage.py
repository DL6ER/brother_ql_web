"""The label kept in the browser for the next visit, with its images."""

import io
import os

import pytest
from PIL import Image

from tests.browser.helpers import kept, png, type_line, wait_for_preview


def test_images_kept_for_the_next_visit_are_not_stored_again_with_the_first_change(designer):
    # Loaded with the label, they were taken for new ones: the first change
    # of each visit wrote them again, megabytes at once
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'shelf.png', 'mimeType': 'image/png', 'buffer': png()}])
    wait_for_preview(designer)
    type_line(designer, 0, 'Before')
    kept(designer)
    designer.reload()
    wait_for_preview(designer)
    designer.evaluate("""() => {
        const set = Storage.prototype.setItem;
        window.imageWrites = 0;
        Storage.prototype.setItem = function (key, value) {
            if (key === 'designer_edit_images') window.imageWrites++;
            return set.call(this, key, value);
        };
    }""")
    type_line(designer, 0, 'After')
    kept(designer)
    assert designer.evaluate('() => [window.imageWrites, loadEdit().design.text[0].text, !!loadEdit().design.image]') == [0, 'After', True]


def test_two_images_alike_in_length_do_not_share_a_key(designer):
    # The key of 32 bits named an image in the undo history and the label
    # kept: two of the same length with the same hash were taken for one
    same = designer.evaluate("""() => {
        const old = s => { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193); return h >>> 0; };
        const seen = new Map();
        for (let i = 0; i < 400000; i++) {
            const s = Math.random().toString(36).slice(2, 12).padEnd(10, 'x');
            const h = old(s);
            if (seen.has(h)) return [seen.get(h), s];
            seen.set(h, s);
        }
        return null;
    }""")
    assert same is not None
    assert designer.evaluate('([a, b]) => fingerprint(a) !== fingerprint(b)', same)


def test_a_label_kept_without_room_for_it_is_not_tried_again_with_each_pause(designer):
    # Its images were stored, the label then found no room: the images were
    # taken back and stored again with each pause in typing
    designer.evaluate("""() => {
        const set = Storage.prototype.setItem;
        window.writes = 0;
        Storage.prototype.setItem = function (key, value) {
            if (key === 'designer_edit_images' && value.length > 1000) window.writes++;
            if (key === 'designer_edit' && (localStorage.getItem('designer_edit_images') || '').length > 1000) throw new DOMException('Full', 'QuotaExceededError');
            return set.call(this, key, value);
        };
    }""")
    designer.click('[data-lay="image"]')
    noise = io.BytesIO()
    Image.frombytes('L', (100, 100), os.urandom(10000)).save(noise, format='PNG')
    designer.set_input_files('#edFile', files=[{'name': 'large.png', 'mimeType': 'image/png', 'buffer': noise.getvalue()}])
    wait_for_preview(designer)
    for text in ('Once', 'Twice', 'Thrice'):
        type_line(designer, 0, text)
        kept(designer)
    assert designer.evaluate('() => window.writes') == 1
    stored = designer.evaluate('() => loadEdit()')
    assert stored['design']['text'][0]['text'] == 'Thrice' and 'image' not in stored['design']


def _stored_image(designer, size):
    noise = io.BytesIO()
    Image.frombytes('L', (size, size), os.urandom(size * size)).save(noise, format='PNG')
    designer.set_input_files('#edFile', files=[{'name': f'noise{size}.png', 'mimeType': 'image/png', 'buffer': noise.getvalue()}])
    wait_for_preview(designer)


@pytest.mark.parametrize('room', ['label', 'label alone', 'none'])
def test_images_of_an_earlier_label_go_where_the_new_ones_do_not_fit(designer, room):
    # They stayed: where the label then found no room, nothing was kept, the
    # next visit brought back older text. Where no label finds room, the one
    # kept before keeps its image.
    designer.click('[data-lay="image"]')
    _stored_image(designer, 60)
    type_line(designer, 0, 'Before')
    kept(designer)
    first = designer.evaluate('() => S.edit.design.image.data')
    designer.evaluate("""room => {
        const set = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key, value) {
            if (key === 'designer_edit_images' && value.length > 30000) throw new DOMException('Full', 'QuotaExceededError');
            if (key === 'designer_edit' && (room === 'none' || (room === 'label alone' && localStorage.getItem('designer_edit_images')))) {
                throw new DOMException('Full', 'QuotaExceededError');
            }
            return set.call(this, key, value);
        };
    }""", room)
    _stored_image(designer, 200)
    type_line(designer, 0, 'After')
    kept(designer)
    stored = designer.evaluate('() => loadEdit()')
    if room == 'none':
        assert stored['design']['text'][0]['text'] == 'Before' and stored['design']['image']['data'] == first
    else:
        assert stored['design']['text'][0]['text'] == 'After' and 'image' not in stored['design']
        assert designer.evaluate("() => localStorage.getItem('designer_edit_images')") is None


def test_the_newest_text_is_kept_where_images_kept_before_take_the_room(designer):
    # The images were kept with the label before, the label then found no
    # room: nothing was kept, the next visit brought back older text
    designer.click('[data-lay="image"]')
    noise = io.BytesIO()
    Image.frombytes('L', (100, 100), os.urandom(10000)).save(noise, format='PNG')
    designer.set_input_files('#edFile', files=[{'name': 'large.png', 'mimeType': 'image/png', 'buffer': noise.getvalue()}])
    wait_for_preview(designer)
    type_line(designer, 0, 'Before')
    kept(designer)
    designer.evaluate("""() => {
        const set = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key, value) {
            if (key === 'designer_edit' && localStorage.getItem('designer_edit_images')) throw new DOMException('Full', 'QuotaExceededError');
            return set.call(this, key, value);
        };
    }""")
    type_line(designer, 0, 'After')
    kept(designer)
    stored = designer.evaluate('() => loadEdit()')
    assert stored['design']['text'][0]['text'] == 'After' and 'image' not in stored['design']


def test_label_is_kept_without_its_images_when_they_do_not_fit(designer):
    # Images too large for the storage: the label is kept without them, not
    # with refs to images not stored. Before, all typed since was lost, and
    # each pause in typing tried to store the images again.
    type_line(designer, 0, 'Before')
    kept(designer)
    designer.evaluate("""() => {
        const set = Storage.prototype.setItem;
        window.tries = 0;
        window.unlimit = () => { Storage.prototype.setItem = set; };
        Storage.prototype.setItem = function (key, value) {
            if (key === 'designer_edit_images' && value.length > 1000) { window.tries++; throw new DOMException('Full', 'QuotaExceededError'); }
            return set.call(this, key, value);
        };
    }""")
    designer.click('[data-lay="image"]')
    noise = io.BytesIO()
    Image.frombytes('L', (100, 100), os.urandom(10000)).save(noise, format='PNG')
    designer.set_input_files('#edFile', files=[{'name': 'large.png', 'mimeType': 'image/png', 'buffer': noise.getvalue()}])
    wait_for_preview(designer)
    type_line(designer, 0, 'After')
    kept(designer)
    type_line(designer, 0, 'Later')
    kept(designer)
    stored = designer.evaluate('() => loadEdit()')
    assert stored['design']['text'][0]['text'] == 'Later' and 'image' not in stored['design']
    assert designer.evaluate('() => window.tries') == 1
    # With room again, a change a minute later keeps the label with its image
    designer.evaluate('() => { unlimit(); imagesTooLarge.time -= 60000; }')
    type_line(designer, 0, 'Again')
    kept(designer)
    designer.reload()
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.text[0].text, !!S.edit.design.image]') == ['Again', True]


def test_images_stored_by_another_tab_are_stored_again(designer):
    # The other tab replaced the stored images, this one stores its own again
    # with its next change instead of a label with refs to images not stored
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'mine.png', 'mimeType': 'image/png', 'buffer': png(200, 100)}])
    wait_for_preview(designer)
    kept(designer)
    mine = designer.evaluate('() => S.edit.design.image.data')
    other = designer.context.new_page()
    other.goto(designer.url)
    wait_for_preview(other)
    other.set_input_files('#edFile', files=[{'name': 'other.png', 'mimeType': 'image/png', 'buffer': png(120, 80)}])
    wait_for_preview(other)
    kept(other)
    other.close()
    type_line(designer, 0, 'Mine')
    kept(designer)
    designer.reload()
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image && S.edit.design.image.data') == mine


def test_label_stays_with_its_images_when_it_cannot_be_kept(designer):
    # The images were stored, the label then was not: the label kept before
    # pointed to images no longer stored
    def image(width):
        out = io.BytesIO()
        Image.frombytes('L', (width, 40), os.urandom(width * 40)).save(out, format='PNG')
        return out.getvalue()
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'a.png', 'mimeType': 'image/png', 'buffer': image(60)}])
    wait_for_preview(designer)
    kept(designer)
    first = designer.evaluate('() => S.edit.design.image.data')
    designer.evaluate("""() => {
        const set = Storage.prototype.setItem;
        Storage.prototype.setItem = function (key, value) {
            if (key === 'designer_edit') throw new DOMException('Full', 'QuotaExceededError');
            return set.call(this, key, value);
        };
    }""")
    designer.set_input_files('#edFile', files=[{'name': 'b.png', 'mimeType': 'image/png', 'buffer': image(80)}])
    wait_for_preview(designer)
    kept(designer)
    assert designer.evaluate('() => loadEdit().design.image.data') == first


def test_instances_below_one_host_keep_their_labels_apart(designer, server):
    # Served below different paths behind a reverse proxy, they shared the
    # label kept for the next visit, the print history and the printer
    designer.evaluate("""() => {
        S.edit.title = 'Of the root';
        storeEdit();
        localStorage.setItem('designer_printer', JSON.stringify('file:///dev/usb/lp7'));
    }""")
    designer.route('**/labeldesigner/', lambda route: route.fulfill(
        response=route.fetch(), body=route.fetch().text().replace('"storage_prefix": ""', '"storage_prefix": "/labels/"')))
    designer.reload()
    wait_for_preview(designer)
    # Neither the label nor the printer of the other instance
    assert designer.input_value('#edTitle') != 'Of the root'
    assert not designer.is_visible('#toast:has-text("/dev/usb/lp7 is not connected")')
    type_line(designer, 0, 'Below /labels')
    kept(designer)
    stored = designer.evaluate("""() => [JSON.parse(localStorage.getItem('/labels/designer_edit')).design.text[0].text,
                                         JSON.parse(localStorage.getItem('designer_edit')).title]""")
    assert stored == ['Below /labels', 'Of the root']
