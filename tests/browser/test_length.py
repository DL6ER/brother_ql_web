"""The length of endless labels in the label designer."""

from tests.browser.helpers import fits_note, kept, preview_size, save_as, set_tape, type_line, wait_for_preview
from tests.browser.test_vc500w import fake_vc500w, use_tape


def set_length(page, mm):
    page.fill('#edLength', mm)
    page.dispatch_event('#edLength', 'change')
    wait_for_preview(page)


def test_an_endless_label_gets_the_length_typed(designer):
    type_line(designer, 0, 'Shelf')
    grown = preview_size(designer)
    assert designer.get_attribute('#edLength', 'placeholder') == 'Automatic'
    set_length(designer, '40')
    # 40 mm as cut, the printer leaves about 3 mm before and after the print
    assert preview_size(designer) == (grown[0], round((40 - 2 * 35 / 300 * 25.4) / 25.4 * 300))
    assert designer.inner_text('#edStage .dim-y') == '40 mm'
    assert 'Everything fits.' in fits_note(designer)
    # Empty again, the label is as long as its content
    set_length(designer, '')
    assert preview_size(designer) == grown
    assert designer.evaluate("() => formFor(S.edit.design).get('label_length')") == ''


def test_a_length_that_cannot_be_printed_is_explained(designer):
    type_line(designer, 0, 'Shelf')
    set_length(designer, '10')
    assert 'label_length must be at least 18.7 mm on this tape' in designer.inner_text('#edStage')
    assert designer.is_disabled('#edPrint')
    set_length(designer, '20')
    assert designer.is_enabled('#edPrint')


def test_die_cut_labels_have_their_own_length(designer):
    type_line(designer, 0, 'Shelf')
    set_length(designer, '40')
    set_tape(designer, '62x29')
    wait_for_preview(designer)
    # Disabled with the reason, the length of the label shown instead
    assert designer.is_disabled('#edLength')
    assert designer.get_attribute('#edLength', 'placeholder') == '29'
    assert designer.get_attribute('#edLength', 'title') == 'Die-cut labels have their own length'
    assert 'off' in designer.get_attribute('label[for="edLength"]', 'class')
    # Kept for when the label is on endless tape again
    set_tape(designer, '62')
    wait_for_preview(designer)
    assert designer.input_value('#edLength') == '40'
    assert designer.inner_text('#edStage .dim-y') == '40 mm'


def test_the_length_is_a_step_to_undo(designer):
    type_line(designer, 0, 'Shelf')
    kept(designer)
    set_length(designer, '40')
    kept(designer)
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.input_value('#edLength') == ''
    designer.click('#edRedo')
    wait_for_preview(designer)
    assert designer.input_value('#edLength') == '40'


def test_a_saved_label_keeps_its_length(designer):
    type_line(designer, 0, 'Shelf')
    set_length(designer, '40')
    size = preview_size(designer)
    save_as(designer, 'Shelf of 40 mm')
    designer.wait_for_function("() => S.saved.some(x => x.name === 'Shelf_of_40_mm.json')")
    stored = designer.evaluate("""async () => (await (await fetch(API + '/repository/load?name=Shelf_of_40_mm.json')).json()).label_length""")
    assert stored == '40'
    # Opened again from the saved labels
    designer.click('#edNew')
    wait_for_preview(designer)
    assert designer.input_value('#edLength') == ''
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="saved"]')
    designer.click('#libGrid .thumb:has(.t:text-is("Shelf of 40 mm"))')
    wait_for_preview(designer, '#libStage')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    wait_for_preview(designer)
    assert designer.input_value('#edLength') == '40'
    assert preview_size(designer) == size
    designer.evaluate("() => deleteLabel('Shelf_of_40_mm.json')")


def test_the_length_is_limited_as_the_server_says(designer):
    assert designer.get_attribute('#edLength', 'min') == '18.7'
    assert designer.get_attribute('#edLength', 'max') == '3000'


def test_a_cable_flag_has_no_length_to_set(designer):
    type_line(designer, 0, 'Router')
    set_length(designer, '40')
    designer.check('#edFlag')
    wait_for_preview(designer)
    assert designer.is_disabled('#edLength')
    assert designer.get_attribute('#edLength', 'title') == 'Cable flags are as long as their content'
    assert designer.inner_text('#edStage .dim-y') != '40 mm'
    # Kept for when it is a label again
    designer.uncheck('#edFlag')
    wait_for_preview(designer)
    assert designer.input_value('#edLength') == '40'
    assert designer.inner_text('#edStage .dim-y') == '40 mm'


def test_color_tape_has_no_length_to_set(designer):
    fake_vc500w(designer)
    use_tape(designer)
    assert designer.is_disabled('#edLength')
    assert designer.get_attribute('#edLength', 'title') == 'Labels on this tape are as long as their content'
    assert 'off' in designer.get_attribute('label[for="edLength"]', 'class')
