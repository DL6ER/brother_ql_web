"""Cable flags in the label designer: the checkbox, the diameter of the
cable, and labels that cannot be flags."""

import pytest

from tests.browser.helpers import kept, preview_size, save_as, type_line, wait_for_preview
from tests.browser.test_printing import fake_printer

TAPE = 'Cable flags need continuous tape'


def _flag(page):
    page.check('#edFlag')
    wait_for_preview(page)


def test_cable_flag_shows_diameter_and_hint_and_lengthens_the_preview(designer):
    type_line(designer, 0, 'Router - LAN 1')
    assert designer.locator('#edCable, #edFlagHint').count() == 0
    _, before = preview_size(designer)
    _flag(designer)
    assert designer.evaluate('() => S.edit.design.cable_flag') == '1'
    assert designer.get_attribute('#edCable', 'placeholder') == '6.5'
    assert designer.inner_text('#edFlagHint') == 'Place the cable on the triangles, fold both ends together and press them firmly.'
    # Text across the tape: the flag grows along it
    _, after = preview_size(designer)
    assert after > 2 * before


def test_cable_diameter_changes_the_preview(designer):
    type_line(designer, 0, 'Router')
    _flag(designer)
    _, cat7 = preview_size(designer)
    designer.fill('#edCable', '20')
    wait_for_preview(designer)
    # pi x 20 mm against pi x 6.5 mm at 300 dpi
    assert preview_size(designer)[1] - cat7 == 742 - 241
    # Refused by the server, which says why
    for wrong in ('0', '55'):
        designer.fill('#edCable', wrong)
        wait_for_preview(designer)
        assert designer.evaluate('() => S.edit.design.cable_diameter') == wrong
        assert 'The cable diameter must be a number from 1 to 50 mm' in designer.inner_text('#edStage .err')
        assert designer.is_disabled('#edPrint')
    # No number while it is typed: the last one stays
    designer.fill('#edCable', '20')
    wait_for_preview(designer)
    designer.type('#edCable', 'e')
    assert designer.evaluate('() => S.edit.design.cable_diameter') == '20'
    designer.fill('#edCable', '')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.cable_diameter') == ''
    assert preview_size(designer)[1] == cat7


def test_cable_flag_cannot_be_ticked_on_die_cut_labels(designer):
    designer.select_option('#edSize', '62x29')
    wait_for_preview(designer)
    assert designer.is_disabled('#edFlag')
    assert designer.get_attribute('#edFlagRow', 'title') == TAPE


def test_die_cut_labels_turn_the_cable_flag_off_and_undo_brings_it_back(designer):
    type_line(designer, 0, 'Router')
    _flag(designer)
    kept(designer)
    designer.select_option('#edSize', '62x29')
    wait_for_preview(designer)
    assert not designer.is_checked('#edFlag')
    designer.wait_for_selector('#toast:has-text("Cable flag turned off, it needs continuous tape")')
    designer.click('#toast button')
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.label_size, S.edit.design.cable_flag]') == ['62', '1']
    assert designer.is_checked('#edFlag')


def test_a_stored_cable_flag_on_die_cut_labels_can_be_turned_off(designer):
    designer.evaluate("""() => openEditor({ title: 'Old', design: Object.assign(baseDesign('62x29'),
        { text: [L('Old', 40)], cable_flag: '1' }) })""")
    wait_for_preview(designer)
    assert designer.is_checked('#edFlag') and designer.is_enabled('#edFlag')
    designer.uncheck('#edFlag')
    wait_for_preview(designer)
    assert designer.is_disabled('#edFlag')


@pytest.mark.parametrize('value, on', [('2', True), (' 1 ', True), ('TRUE', True), ('0', False), ('false', False), ('-1', False)])
def test_a_stored_cable_flag_is_read_as_the_server_reads_it(designer, value, on):
    designer.evaluate("""value => openEditor({ title: 'Old', design: Object.assign(baseDesign('62'),
        { text: [L('Old', 40)], cable_flag: value }) })""", value)
    wait_for_preview(designer)
    assert designer.is_checked('#edFlag') == on


def test_switching_to_the_loaded_die_cut_labels_turns_the_cable_flag_off(designer):
    fake_printer(designer, media_width=29, media_length=90)
    type_line(designer, 0, 'Router')
    _flag(designer)
    designer.click('#edUseTape')
    wait_for_preview(designer)
    assert designer.input_value('#edSize') == '29x90' and not designer.is_checked('#edFlag')
    designer.wait_for_selector('#toast:has-text("Cable flag turned off, it needs continuous tape")')


def test_tape_guard_offers_no_die_cut_labels_for_a_cable_flag(designer):
    fake_printer(designer, media_width=29, media_length=90)
    type_line(designer, 0, 'Router')
    _flag(designer)
    designer.click('#edPrint')
    designer.wait_for_selector('.sheet h2:text-is("This label needs 62 mm endless")')
    assert designer.locator('.sheet [data-r="adapt"]').count() == 0
    assert TAPE in designer.inner_text('.sheet')
    designer.click('.sheet [data-r="cancel"]')
    # Without the flag the label can be printed on the labels loaded
    designer.uncheck('#edFlag')
    wait_for_preview(designer)
    designer.click('#edPrint')
    designer.wait_for_selector('.sheet [data-r="adapt"]')
    designer.click('.sheet [data-r="cancel"]')


def test_saved_cable_flag_opens_as_a_cable_flag(designer):
    type_line(designer, 0, 'Patch 12')
    _flag(designer)
    designer.fill('#edCable', '8')
    wait_for_preview(designer)
    save_as(designer, 'Patch')
    designer.wait_for_function("() => document.getElementById('toast').innerText.includes('\"Patch\" is now one of your saved labels')")
    designer.evaluate("""async () => openEditor({ title: 'Patch',
        design: await itemDesign(savedItem({ name: 'Patch.json', title: 'Patch' })) })""")
    wait_for_preview(designer)
    assert designer.is_checked('#edFlag') and designer.input_value('#edCable') == '8'
