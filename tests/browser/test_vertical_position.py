"""The vertical position of the content in the label designer."""

import base64
import io

from PIL import Image

from tests.browser.helpers import kept, set_tape, type_line, wait_for_preview


def ink_top(page) -> int:
    """Where the ink begins on the label of the preview."""
    data = page.evaluate("""async () => {
        const buf = await (await fetch(document.querySelector('#edStage .paper img').src)).arrayBuffer();
        return btoa(String.fromCharCode(...new Uint8Array(buf)));
    }""")
    img = Image.open(io.BytesIO(base64.b64decode(data))).convert('L')
    return Image.eval(img, lambda v: 255 - v).getbbox()[1]


def test_the_content_can_be_at_the_top_or_bottom_of_a_die_cut_label(designer):
    set_tape(designer, '62x29')
    type_line(designer, 0, 'Shelf')
    assert 'on' in designer.get_attribute('[data-valign="center"]', 'class')
    middle = ink_top(designer)
    designer.click('[data-valign="top"]')
    wait_for_preview(designer)
    top = ink_top(designer)
    designer.click('[data-valign="bottom"]')
    wait_for_preview(designer)
    assert top < middle < ink_top(designer)
    assert designer.evaluate("() => formFor(S.edit.design).get('vertical_align')") == 'bottom'


def test_an_endless_label_across_the_tape_has_no_room_to_place_the_content_in(designer):
    type_line(designer, 0, 'Shelf')
    assert designer.is_disabled('[data-valign="top"]')
    reason = 'Endless labels end where the content ends, there is no room to place it in'
    assert designer.get_attribute('div:has(> [data-valign]) >> xpath=..', 'title') == reason
    # Along the tape the room is across it
    designer.click('[data-along="1"]')
    wait_for_preview(designer)
    assert designer.is_enabled('[data-valign="top"]')
    # Across the tape with a length it is as long as that
    designer.click('[data-along="0"]')
    wait_for_preview(designer)
    assert designer.is_disabled('[data-valign="top"]')
    designer.fill('#edLength', '60')
    designer.dispatch_event('#edLength', 'change')
    wait_for_preview(designer)
    assert designer.is_enabled('[data-valign="top"]')


def test_the_vertical_position_is_a_step_to_undo_and_kept_with_the_label(designer):
    set_tape(designer, '62x29')
    type_line(designer, 0, 'Shelf')
    kept(designer)
    designer.click('[data-valign="top"]')
    wait_for_preview(designer)
    kept(designer)
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert 'on' in designer.get_attribute('[data-valign="center"]', 'class')
    designer.click('#edRedo')
    wait_for_preview(designer)
    assert 'on' in designer.get_attribute('[data-valign="top"]', 'class')
    # Saved and loaded as the server reads it, labels of before in the middle
    assert designer.evaluate("""async () => {
        const name = await saveLabel('Top shelf', S.edit.design);
        const raw = await (await fetch(API + '/repository/load?name=' + encodeURIComponent(name))).json();
        await deleteLabel(name);
        return [normalize(raw).vertical_align, normalize({}).vertical_align];
    }""") == ['top', 'center']
