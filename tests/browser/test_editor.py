"""The editor of the label designer: lines, fonts, codes and images, undo
and the focus."""

import io
import json
import os

import pytest
from PIL import Image

from tests.browser.helpers import fits_note, kept, png, preview_size, type_line, wait_for_preview


def edit_saved_label(page, title):
    page.click('[data-tab="wizard"]')
    page.click('.lib-nav [data-cat="saved"]')
    page.click(f'#libGrid .thumb:has(.t:text-is("{title}"))')
    wait_for_preview(page, '#libStage')
    page.click('#libEdit')
    page.wait_for_selector('#edStage')
    wait_for_preview(page)


def test_new_label_is_empty_and_cannot_be_printed(designer):
    assert designer.input_value('[data-row="0"] .tx') == ''
    assert designer.locator('#edStage .blank-paper').count() == 1
    assert designer.is_disabled('#edPrint')
    assert designer.is_disabled('#edSave')


def test_text_that_does_not_fit_can_be_shrunk(designer):
    designer.click('[data-tfit="none"]')
    type_line(designer, 0, 'A very long line that does not fit on the label at all, not even close')
    designer.select_option('#edSize', '62x29')
    wait_for_preview(designer)
    assert 'does not fit' in fits_note(designer)
    designer.click('#edFits button')
    wait_for_preview(designer)
    assert 'Everything fits.' in fits_note(designer)
    assert 'on' in designer.get_attribute('[data-tfit="shrink"]', 'class')


def test_text_next_to_a_code_on_a_small_die_cut_label(designer):
    # The note was guessed from the size of the label, also where the text
    # fits. The code fitted to the label took all of it, it leaves the text
    # half now.
    designer.click('[data-lay="codetext"]')
    designer.fill('#edCode', 'https://example.com')
    type_line(designer, 0, 'Chili')
    designer.select_option('#edSize', '62x29')
    designer.click('[data-tfit="shrink"]')
    designer.click('[data-fit="0"]')
    designer.fill('#edQrSize', '4')
    wait_for_preview(designer)
    assert 'Everything fits.' in fits_note(designer)
    designer.click('[data-fit="1"]')
    wait_for_preview(designer)
    assert 'Everything fits.' in fits_note(designer)


def test_the_code_can_be_on_any_side_of_the_text(designer):
    designer.click('[data-lay="codetext"]')
    # Only with text, from the moment it is typed
    assert designer.is_disabled('[data-side="left"]')
    designer.fill('#edCode', 'https://example.com')
    type_line(designer, 0, 'Chili')
    assert designer.is_enabled('[data-side="left"]')
    assert designer.get_attribute('#edSide', 'title') == 'Where the code is next to the text'
    assert 'off' not in designer.get_attribute('#edSide .label-s', 'class')
    type_line(designer, 0, '')
    assert designer.is_disabled('[data-side="left"]')
    assert designer.get_attribute('#edSide', 'title') == 'Only with text on the label'
    type_line(designer, 0, 'Chili')
    designer.select_option('#edSize', '62')
    wait_for_preview(designer)
    side = "() => document.querySelector('[data-side].on').dataset.side"
    # Without a choice above the text across the tape, left of it along
    for along, default in (('0', 'above'), ('1', 'left')):
        designer.click(f'[data-along="{along}"]')
        wait_for_preview(designer)
        assert designer.evaluate(side) == default
    designer.click('[data-side="right"]')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image_position') == 'right'
    assert 'Everything fits.' in fits_note(designer)
    # The choice stays when the text runs the other way
    designer.click('[data-along="0"]')
    wait_for_preview(designer)
    assert designer.evaluate(side) == 'right'


def test_a_label_keeps_the_side_of_its_code_when_opened(designer):
    # Dropped when a label was loaded: the badge showed its barcode below the
    # text in the list, above it in the editor and on the label printed
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="names"]')
    designer.click('#libGrid .thumb:has(.t:text-is("Employee badge with barcode"))')
    wait_for_preview(designer, '#libStage')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image_position') == 'below'
    assert 'on' in designer.get_attribute('[data-side="below"]', 'class')


def test_a_code_fitted_beside_the_text_across_endless_tape_leaves_it_room(designer):
    # Filling the tape it left the text no room
    designer.click('[data-lay="codetext"]')
    type_line(designer, 0, 'Pantry')
    designer.select_option('#edSize', '62')
    designer.click('[data-along="0"]')
    designer.click('[data-fit="1"]')
    designer.click('[data-side="left"]')
    wait_for_preview(designer)
    assert 'on' in designer.get_attribute('[data-fit="1"]', 'class')
    assert 'Everything fits.' in fits_note(designer)


def test_a_layer_opened_after_a_silent_close_is_not_closed_by_the_view_drawn_anew(designer):
    # Drawn anew after a label was chosen in search that took the place of
    # the printer panel, the question before printing that the label had
    # opened was closed, and nothing printed
    designer.evaluate("""() => {
        window.routed = 0;
        const draw = route;
        route = () => { window.routed++; draw(); };
        openLayer('<div class="sheet"></div>');
        S.routeLater = true;
        closeLayer(true);
        openLayer('<div class="sheet" id="asked"></div>');
    }""")
    designer.wait_for_timeout(200)
    assert designer.is_visible('#asked') and designer.evaluate('() => window.routed') == 0
    designer.evaluate('() => closeLayer()')
    designer.wait_for_function('() => window.routed === 1')


def test_the_side_the_server_chose_is_shown_without_drawing_the_editor_anew(designer):
    # The editor was drawn anew for it, and the preview handled once more
    designer.click('[data-lay="codetext"]')
    type_line(designer, 0, 'Pantry')
    designer.select_option('#edSize', '62')
    designer.click('[data-along="0"]')
    wait_for_preview(designer)
    designer.evaluate("""() => {
        window.drawn = 0;
        const draw = renderEditor;
        renderEditor = () => { window.drawn++; draw(); };
    }""")
    designer.click('[data-along="1"]')
    wait_for_preview(designer)
    designer.wait_for_function("() => document.querySelector('[data-side].on') && document.querySelector('[data-side].on').dataset.side === 'left'")
    designer.wait_for_timeout(200)
    assert designer.evaluate('() => window.drawn') == 1


def test_settings_of_an_image_do_not_reach_a_code_through_the_text_layout(designer):
    # Reset only when the layout changed from image to code at once, the
    # rotation and crop of an image went on to a code after the text layout
    designer.click('[data-lay="image"]')
    designer.click('[data-rot="90"]')
    designer.click('[data-crop="1"]')
    designer.click('[data-lay="text"]')
    designer.click('[data-lay="codetext"]')
    look = '() => [S.edit.design.image_rotation, S.edit.design.image_crop, S.edit.design.image_scaling_factor]'
    assert designer.evaluate(look) == ['0', '0', '100']
    # Back to the image after the text layout, its own settings stay
    designer.click('[data-lay="image"]')
    designer.click('[data-rot="180"]')
    designer.click('[data-lay="text"]')
    designer.click('[data-lay="image"]')
    assert designer.evaluate('() => S.edit.design.image_rotation') == '180'


def test_settings_of_an_image_brought_back_by_undo_stay(designer):
    # The image or code they belong to was kept apart from the undo history:
    # after undo of the change to a code, the image lost them on the way back
    designer.click('[data-lay="image"]')
    designer.click('[data-rot="90"]')
    kept(designer)
    designer.click('[data-lay="codetext"]')
    kept(designer)
    designer.click('#edUndo')
    assert designer.evaluate('() => [S.edit.design.print_type, S.edit.design.image_rotation]') == ['image', '90']
    designer.click('[data-lay="text"]')
    designer.click('[data-lay="image"]')
    assert designer.evaluate('() => S.edit.design.image_rotation') == '90'


def test_a_date_put_in_before_the_same_one_is_the_new_one(designer):
    # Put in before a date shown the same, the new one was taken for the
    # one there was and the formats changed places
    fmt = designer.evaluate('() => dateFormat()')
    assert designer.evaluate("() => fromDisplay('[today][today]', '{{datetime:%Y}}', 7)") == f'{{{{datetime:{fmt}}}}}{{{{datetime:%Y}}}}'


def test_a_layer_closed_for_something_else_draws_the_view_anew_after_it(designer):
    # A tape changed in the printer panel was not shown, where another layer
    # took the place of the panel and closed without a sound
    designer.evaluate("""() => {
        window.routed = 0;
        const draw = route;
        route = () => { window.routed++; draw(); };
        openLayer('<div class="sheet"></div>');
        S.routeLater = true;
        closeLayer(true);
    }""")
    designer.wait_for_function('() => window.routed === 1')
    # Drawn by what it was closed for, only once
    designer.evaluate("() => { openLayer('<div class=\"sheet\"></div>'); S.routeLater = true; route(); }")
    designer.wait_for_timeout(200)
    assert designer.evaluate('() => window.routed') == 2


def test_a_barcode_of_a_fixed_size_can_be_scaled(designer):
    # It had no size of its own, the size of a dot is for QR codes
    design = '() => [S.edit.design.barcode_type, S.edit.design.image_scaling_factor]'
    designer.click('[data-lay="codetext"]')
    designer.fill('#edCode', '00021329')
    type_line(designer, 0, 'Visitor')
    designer.select_option('#edBarcode', 'CODE128')
    designer.click('[data-fit="1"]')
    assert designer.is_disabled('#edCodeScale')
    designer.click('[data-fit="0"]')
    designer.fill('#edCodeScale', '150')
    wait_for_preview(designer)
    assert designer.evaluate(design) == ['CODE128', '150']
    assert 'Everything fits.' in fits_note(designer)
    # A QR code has the size of a dot, the scale would make it blurred
    designer.select_option('#edBarcode', 'QR')
    wait_for_preview(designer)
    assert designer.evaluate(design) == ['QR', '100']
    assert designer.is_enabled('#edQrSize') and designer.locator('#edCodeScale').count() == 0


def test_a_date_typed_into_a_template_keeps_its_format(designer):
    # Typed into the wizard, [today] got the format of the printer panel
    # unless it was at the same place as the date of the template
    typed = designer.evaluate("""() => {
        S.wizardText = '[today] Milk';
        const design = { print_type: 'text', text: [{ text: 'Frozen {{datetime:%d.%m.%y}}' }] };
        return applyTyped({ kind: 'demo' }, design, {}).design.text[0].text;
    }""")
    assert typed == '{{datetime:%d.%m.%y}} Milk'
    # One kept at its place, the next takes the format of the date not kept,
    # not of the first one
    assert designer.evaluate("() => fromDisplay('[today] y [today]!', '{{datetime:%d.%m.}} x {{datetime:%Y}}', null, true)") == '{{datetime:%d.%m.}} y {{datetime:%Y}}!'


def test_the_caret_stays_where_it_was_when_the_editor_is_drawn_anew(designer):
    type_line(designer, 0, 'Pantry shelf')
    designer.focus('[data-row="0"] .tx')
    designer.evaluate("() => document.querySelector('[data-row=\"0\"] .tx').setSelectionRange(3, 5)")
    designer.evaluate('() => renderEditor()')
    assert designer.evaluate('() => [document.activeElement.selectionStart, document.activeElement.selectionEnd]') == [3, 5]


def test_the_scale_of_a_barcode_does_not_go_on_to_an_image(designer):
    designer.click('[data-lay="codetext"]')
    designer.fill('#edCode', '00021329')
    type_line(designer, 0, 'Visitor')
    designer.select_option('#edBarcode', 'CODE128')
    designer.click('[data-fit="0"]')
    designer.fill('#edCodeScale', '300')
    designer.click('[data-lay="image"]')
    assert designer.evaluate('() => S.edit.design.image_scaling_factor') == '100'


def test_settings_of_an_image_do_not_turn_a_code(designer):
    # Rotation, cropping and scale of the image went on to act on a code,
    # where they are not shown
    designer.click('[data-lay="image"]')
    designer.click('[data-rot="90"]')
    designer.click('[data-crop="1"]')
    designer.click('[data-fit="0"]')
    designer.fill('#edScale', '50')
    designer.click('[data-lay="codetext"]')
    look = '() => [S.edit.design.image_rotation, S.edit.design.image_crop, S.edit.design.image_scaling_factor]'
    assert designer.evaluate(look) == ['0', '0', '100']


def test_sizes_of_each_line_are_kept(designer):
    # A saved label keeps the font and size of each line, also after reloading
    edit_saved_label(designer, 'Template Example')
    lines = "() => S.edit.design.text.map(l => l.font + ' ' + l.size)"
    expected = ['DejaVu Sans,Book 40', 'DejaVu Sans,Book 40', 'DejaVu Sans,Book 25', 'DejaVu Sans,Book 40', 'DejaVu Sans,Book 40']
    assert designer.evaluate(lines) == expected
    # An emptied size keeps the last one. Stored empty, every preview failed.
    designer.fill('[data-row="2"] input.mini', '')
    wait_for_preview(designer)
    designer.reload()
    wait_for_preview(designer)
    assert designer.evaluate(lines) == expected
    assert designer.input_value('[data-row="2"] input.mini') == '25'
    assert designer.get_attribute('#edStage .paper img', 'src').startswith('blob:')


def test_label_with_a_font_that_is_not_installed(designer, app):
    # The preview failed with "Unknown font family". The label keeps its font
    # until another one is chosen.
    repo = app.config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, 'QR-Example.json'), encoding='utf-8') as f:
        label = json.load(f)
    for line in label['text']:
        line['font'] = 'Not Installed,Regular'
    path = os.path.join(repo, 'Other-Fonts.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(label, f)
    try:
        designer.reload()
        wait_for_preview(designer)
        edit_saved_label(designer, 'Other Fonts')
        assert 'The font Not Installed (Regular) is not installed' in fits_note(designer)
        assert designer.get_attribute('#edStage .paper img', 'src').startswith('blob:')
        assert designer.evaluate('() => S.edit.design.text[0].font') == 'Not Installed,Regular'
        assert designer.input_value('[data-row="0"] select.lf') == 'Not Installed'
        assert designer.input_value('#edFamily') == 'Not Installed'
        # Bold is kept as a wish, the font stays
        designer.click('[data-row="0"] [data-act="bold"]')
        wait_for_preview(designer)
        assert designer.evaluate('() => S.edit.design.text[0].font') == 'Not Installed,Regular'
        designer.select_option('[data-row="0"] select.lf', designer.evaluate('() => DEFAULT_FAMILY'))
        wait_for_preview(designer)
        assert 'is not installed' not in fits_note(designer)
    finally:
        os.remove(path)


def test_a_pause_in_typing_is_a_step_to_undo(designer):
    # Changes without a pause between them are one step. Undo right after a
    # change, before the pause, takes back that change.
    for text in ('O', 'On', 'One'):
        designer.fill('[data-row="0"] .tx', text)
    kept(designer)
    designer.evaluate("""() => {
        const tx = document.querySelector('[data-row="0"] .tx');
        tx.value = 'Two';
        tx.dispatchEvent(new Event('input', { bubbles: true }));
        document.querySelector('#edUndo').click();
    }""")
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == 'One'
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == ''
    assert designer.is_disabled('#edUndo')
    for text in ('One', 'Two'):
        designer.click('#edRedo')
        wait_for_preview(designer)
        assert designer.input_value('[data-row="0"] .tx') == text


def test_templates_stay_as_they_were_where_the_text_is_unchanged(designer):
    # Of two dates the one deleted is told by the cursor, the other keeps its
    # format. A blank named like a template stays a blank.
    two = '{{datetime:%d.%m.%Y}} {{datetime:%Y-%m-%d}}'
    assert designer.evaluate("""two => (S.settings.dateFmt = '%Y-%m-%d') && [
        fromDisplay('[today]', two, 0), fromDisplay('[today]', two, 7),
        fromDisplay('[today] xy', '{{blank:today}} x', 10),
        fromDisplay('[id] [counter:3]!', '{{blank:id}} {{blank:counter:3}}', 17),
        fromDisplay('[today] [today]', '{{datetime:%d.%m.%Y}}', 15)]""", two) == [
        '{{datetime:%Y-%m-%d}}', '{{datetime:%d.%m.%Y}}', '{{blank:today}} xy', '{{blank:id}} {{blank:counter:3}}!',
        '{{datetime:%d.%m.%Y}} {{datetime:%Y-%m-%d}}']
    # In the field of a line, deleted from the start
    designer.evaluate("""two => { S.settings.dateFmt = '%Y-%m-%d'; S.edit.design.text[0].text = two; renderEditor(); }""", two)
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == '[today] [today]'
    designer.evaluate("""() => {
        const tx = document.querySelector('[data-row="0"] .tx');
        tx.value = '[today]';
        tx.setSelectionRange(0, 0);
        tx.dispatchEvent(new Event('input', { bubbles: true }));
    }""")
    assert designer.evaluate('() => S.edit.design.text[0].text') == '{{datetime:%Y-%m-%d}}'


def test_undo_history_looks_at_steps_only_when_they_come_and_go(designer):
    # A new step looked at all the steps kept for the images they refer to.
    # An image only steps no longer kept refer to is let go.
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'first.png', 'mimeType': 'image/png', 'buffer': png(120, 60)}])
    wait_for_preview(designer)
    kept(designer)
    result = designer.evaluate("""() => {
        const first = imageKey(S.edit.design.image);
        S.edit.design.image = { data: S.edit.design.image.data.slice(0, -4) + 'AAA=', mime: 'image/png', name: 'second.png' };
        for (let i = 0; i < 62; i++) { S.edit.design.text = [L('Step ' + i, 40)]; recordHistory(S.edit); }
        const scan = String.prototype.matchAll;
        let scans = 0;
        String.prototype.matchAll = function (...args) { scans++; return scan.apply(this, args); };
        S.edit.design.text = [L('One more', 40)];
        recordHistory(S.edit);
        String.prototype.matchAll = scan;
        return [scans, histImages.has(first), histImages.size];
    }""")
    assert result[0] <= 3 and result[1:] == [False, 1]


def test_change_can_be_undone_before_the_pause(designer):
    # Undo was disabled until the pause after the first change
    designer.fill('[data-row="0"] .tx', 'Quick')
    assert designer.is_enabled('#edUndo')
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == ''


def test_settings_keep_the_panel_where_it_was_scrolled_to(designer):
    # Drawn anew for the setting, the panel jumped back to the top. A new
    # label starts at the top.
    designer.set_viewport_size({'width': 1400, 'height': 600})
    type_line(designer, 0, 'Shelf')
    panel_top = "() => document.querySelector('.ed-panel').scrollTop"
    for button in ('[data-along="1"]', '[data-along="0"]', '[data-tfit="fill"]'):
        designer.locator(button).scroll_into_view_if_needed()
        top = designer.evaluate(panel_top)
        assert top > 0
        designer.click(button)
        wait_for_preview(designer)
        assert designer.evaluate(panel_top) == top
        assert designer.evaluate(f"() => document.activeElement === document.querySelector('{button}')")
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.evaluate(panel_top) == top
    designer.evaluate('() => { setEdit(newEdit()); renderEditor(); }')
    wait_for_preview(designer)
    assert designer.evaluate(panel_top) == 0


def test_a_control_of_a_line_keeps_the_focus_on_its_line(designer):
    # The controls of all lines are alike: drawn anew, the focus went to the
    # one of the first line, and the next Enter changed the first line
    for row, text in enumerate(('Flour', 'Sugar', 'Salt')):
        if row:
            designer.click('#edAdd')
        type_line(designer, row, text)
    row_of_focus = "() => document.activeElement.closest('[data-row]') && document.activeElement.closest('[data-row]').dataset.row"
    bold = '() => S.edit.design.text.map(wantsBold)'
    before = designer.evaluate(bold)
    designer.focus('[data-row="1"] [data-act="bold"]')
    designer.keyboard.press('Enter')
    wait_for_preview(designer)
    assert designer.evaluate(row_of_focus) == '1'
    designer.keyboard.press('Enter')
    wait_for_preview(designer)
    assert designer.evaluate(bold) == before
    # Also a control without an id or data attribute
    designer.focus('[data-row="2"] select.lsp')
    designer.select_option('[data-row="2"] select.lsp', '150')
    wait_for_preview(designer)
    assert designer.evaluate(row_of_focus) == '2'
    assert designer.evaluate("() => document.activeElement.matches('select.lsp')")
    # The line removed, the one now in its place has it, the last line
    # removed, the line now last
    designer.focus('[data-row="0"] [data-act="del"]')
    designer.keyboard.press('Enter')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.text.map(l => l.text)') == ['Sugar', 'Salt']
    assert designer.evaluate(row_of_focus) == '0'
    assert designer.evaluate("() => document.activeElement.dataset.act") == 'del'
    designer.focus('[data-row="1"] [data-act="del"]')
    designer.keyboard.press('Enter')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.text.map(l => l.text)') == ['Sugar']
    assert designer.evaluate(row_of_focus) == '0'


def test_each_line_keeps_its_font(designer):
    families = designer.evaluate('() => Object.keys(FONTS)')
    if len(families) < 2:
        pytest.skip('Needs two font families')
    type_line(designer, 0, 'One')
    designer.click('#edAdd')
    type_line(designer, 1, 'Two')
    other = next(f for f in families if f != designer.evaluate('() => familyOf(S.edit.design.text[0].font)'))
    designer.select_option('[data-row="1"] select.lf', other)
    wait_for_preview(designer)
    fonts = designer.evaluate('() => S.edit.design.text.map(l => familyOf(l.font))')
    assert fonts[1] == other and fonts[0] != other
    assert designer.input_value('#edFamily') == ''


def test_bold_comes_back_with_a_font_that_has_it(designer):
    # The line was regular after a font without bold, also when going back
    without = designer.evaluate("() => (Object.entries(FONTS).find(([f, styles]) => !styles.some(s => /bold/i.test(s))) || [])[0]")
    if not without:
        pytest.skip('Needs a font family without bold')
    family = designer.evaluate('() => familyOf(S.edit.design.text[0].font)')
    type_line(designer, 0, 'Bold')
    designer.select_option('[data-row="0"] select.lf', without)
    wait_for_preview(designer)
    assert 'nobold' in designer.get_attribute('[data-row="0"] [data-act="bold"]', 'class')
    designer.select_option('[data-row="0"] select.lf', family)
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.text[0].font') == family + ',Bold'


def test_new_lines_use_the_configured_font(browser, server, app, monkeypatch):
    fonts = app.extensions['fonts']
    family = next((f for f in fonts.fontlist() if f['family'] != fonts.get_default_font()[0]), None)
    if not family:
        pytest.skip('Needs two font families')
    monkeypatch.setattr(fonts, 'default_family', family['family'])
    monkeypatch.setattr(fonts, 'default_style', family['styles'][0])
    context = browser.new_context(locale='en-US')
    page = context.new_page()
    page.goto(server + '/labeldesigner/#edit')
    wait_for_preview(page)
    assert page.evaluate('() => familyOf(S.edit.design.text[0].font)') == family['family']
    context.close()


def test_settings_that_do_not_apply_are_disabled(designer):
    type_line(designer, 0, 'Hello')
    designer.click('[data-lay="codetext"]')
    wait_for_preview(designer)
    # A QR code fitted to the label has no size of a dot
    assert designer.is_disabled('#edQrSize') and designer.is_enabled('#edQrCorr')
    designer.click('[data-fit="0"]')
    wait_for_preview(designer)
    assert designer.is_enabled('#edQrSize')
    designer.select_option('#edBarcode', 'EAN13')
    designer.fill('#edCode', '123456789012')
    wait_for_preview(designer)
    # A barcode has a scale instead, and no error correction
    assert designer.is_enabled('#edCodeScale') and designer.is_disabled('#edQrCorr')
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'black.png', 'mimeType': 'image/png', 'buffer': png()}])
    wait_for_preview(designer)
    # The configured mode, grayscale, has no threshold
    assert designer.evaluate('() => S.edit.design.image_mode') == 'grayscale'
    assert designer.is_disabled('#edThr')
    # Red needs black and red tape
    assert designer.is_disabled('[data-imode="red_and_black"]')
    designer.click('[data-imode="black_and_white"]')
    wait_for_preview(designer)
    assert designer.is_enabled('#edThr')


def test_text_can_fill_the_label(designer):
    # As set, shrunk or as large as fits: the server sizes it. Of labels
    # saved before with shrink_text it reads the size when they are loaded.
    type_line(designer, 0, 'Jam')
    designer.click('[data-tfit="none"]')
    wait_for_preview(designer)
    small = preview_size(designer)
    designer.click('[data-tfit="fill"]')
    wait_for_preview(designer)
    assert preview_size(designer)[1] > 2 * small[1]
    assert 'Everything fits.' in fits_note(designer)
    assert designer.evaluate("() => formFor(S.edit.design).get('text_fit')") == 'fill'
    assert designer.evaluate("() => [normalize({ text_fit: 'fill' }).text_fit, textFit(normalize({}))]") == ['fill', 'none']


def test_a_code_alone_is_a_code_with_its_text_left_empty(designer):
    assert designer.locator('.lay').all_inner_texts() == ['Text', 'Image', 'Code', 'Image + Code']
    designer.click('[data-lay="codetext"]')
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.code_text, S.edit.design.text.map(l => l.text)]') == ['https://example.com', ['']]
    assert designer.is_enabled('[data-tfit="shrink"]')


def test_a_code_alone_left_in_the_editor_is_a_code_with_its_text_left_empty(designer):
    designer.evaluate("""() => {
        S.edit = { title: 'Kept', design: Object.assign(baseDesign(S.tape), { print_type: 'qrcode', code_text: 'https://example.com', text: [L('Not printed', 40)] }), origin: null };
        storeEdit();
    }""")
    designer.reload()
    wait_for_preview(designer)
    assert 'on' in designer.get_attribute('[data-lay="codetext"]', 'class')
    assert designer.evaluate('() => [S.edit.design.print_type, S.edit.design.text.map(l => l.text)]') == ['qrcode_text', ['']]
    # Printed before, without a code of its own the code took the lines:
    # they are shown now
    designer.evaluate("""() => openEditor({ design: Object.assign(baseDesign(S.tape), { print_type: 'qrcode', code_text: '', text: [L('Shelf 12', 40)] }) })""")
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.print_type, S.edit.design.text.map(l => l.text)]') == ['qrcode_text', ['Shelf 12']]


def test_a_saved_code_alone_is_edited_as_a_code_with_its_text_left_empty(designer, app):
    path = os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Contact.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump({'title': 'Contact', 'label_size': '62', 'print_type': 'qrcode', 'code_text': '',
                   'text': [{'font': 'DejaVu Sans,Bold', 'size': '50', 'text': 'BEGIN:VCARD'}, {'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'END:VCARD'}]}, f)
    try:
        designer.reload()
        wait_for_preview(designer)
        designer.click('[data-tab="wizard"]')
        designer.click('.lib-nav [data-cat="saved"]')
        designer.click('#libGrid .thumb:has(.t:text-is("Contact"))')
        wait_for_preview(designer, '#libStage')
        assert designer.input_value('#lnCode') == 'BEGIN:VCARD\nEND:VCARD'
        designer.click('#libEdit')
        designer.wait_for_selector('#edStage')
        wait_for_preview(designer)
        assert 'on' in designer.get_attribute('[data-lay="codetext"]', 'class')
        assert designer.evaluate('() => [S.edit.design.code_text, S.edit.design.text.map(l => [l.text, l.font])]') == ['BEGIN:VCARD\nEND:VCARD', [['', 'DejaVu Sans,Bold']]]
        # Changed, the code keeps its lines
        assert designer.input_value('#edCode') == 'BEGIN:VCARD\nEND:VCARD'
        designer.focus('#edCode')
        designer.keyboard.press('Control+Home')
        designer.keyboard.press('End')
        designer.keyboard.type('\nFN:Jane Doe')
        wait_for_preview(designer)
        assert designer.evaluate('() => S.edit.design.code_text') == 'BEGIN:VCARD\nFN:Jane Doe\nEND:VCARD'
    finally:
        os.remove(path)


def test_a_code_of_several_lines(designer):
    # E.g. a vCard: Enter starts a new line, the field grows with them
    designer.click('[data-lay="codetext"]')
    designer.fill('#edCode', 'BEGIN:VCARD\nFN:Jane Doe')
    designer.focus('#edCode')
    designer.keyboard.press('Control+End')
    designer.keyboard.press('Enter')
    designer.keyboard.type('END:VCARD')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.code_text') == 'BEGIN:VCARD\nFN:Jane Doe\nEND:VCARD'
    assert designer.get_attribute('#edCode', 'rows') == '3'
    # Drawn anew, also with a line break first
    designer.evaluate("() => { S.edit.design.code_text = '\\nBEGIN:VCARD\\nEND:VCARD'; renderEditor(); }")
    assert designer.input_value('#edCode') == '\nBEGIN:VCARD\nEND:VCARD'
    assert designer.get_attribute('#edCode', 'rows') == '3'


def test_distance_between_code_and_text(designer):
    designer.click('[data-lay="codetext"]')
    wait_for_preview(designer)
    # Without text there is nothing to keep a distance from
    assert designer.is_disabled('#edGap')
    type_line(designer, 0, 'Shelf')
    assert designer.is_enabled('#edGap')
    # Left empty, the field shows the distance chosen by the server
    assert designer.get_attribute('#edGap', 'placeholder').startswith('Automatic, ')
    designer.fill('#edGap', '0')
    wait_for_preview(designer)
    without = sum(preview_size(designer))
    designer.fill('#edGap', '10')
    wait_for_preview(designer)
    assert sum(preview_size(designer)) - without == round(10 * 300 / 25.4)
    assert designer.evaluate('() => S.edit.design.image_text_gap') == '118'


def test_automatic_orientation_is_explained(designer):
    # kinghat in #103: the direction of the text does nothing to an image
    # fitted to the label
    designer.select_option('#edSize', '62x29')
    wait_for_preview(designer)
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'portrait.png', 'mimeType': 'image/png', 'buffer': png(100, 300)}])
    designer.wait_for_function("() => document.getElementById('edFits').innerText.includes('Turned automatically to fit the label best.')")
    # Scaled, the direction counts again. The hint follows the preview with a timeout.
    designer.click('[data-fit="0"]')
    wait_for_preview(designer)
    designer.evaluate('() => new Promise(resolve => setTimeout(resolve))')
    assert 'Turned automatically' not in fits_note(designer)


def test_opening_another_label_starts_a_new_undo_history(designer):
    type_line(designer, 0, 'One')
    kept(designer)
    type_line(designer, 0, 'Two')
    kept(designer)
    assert designer.is_enabled('#edUndo')
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="saved"]')
    designer.click('#libGrid .thumb:has(.t:text-is("QR Example"))')
    wait_for_preview(designer, '#libStage')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    wait_for_preview(designer)
    assert designer.is_disabled('#edUndo')


def test_counter_keeps_its_start_value(designer):
    type_line(designer, 0, 'Box [counter:100]')
    assert designer.evaluate('() => S.edit.design.text[0].text') == 'Box {{counter:100}}'
    designer.evaluate('() => renderEditor()')
    assert designer.input_value('[data-row="0"] .tx') == 'Box [counter:100]'
    type_line(designer, 0, 'Crate [counter:100]')
    assert designer.evaluate('() => S.edit.design.text[0].text') == 'Crate {{counter:100}}'


def test_symbols_are_offered(designer):
    designer.click('[data-row="0"] .tx')
    designer.click('#edSym')
    designer.wait_for_selector('.sym-grid .sym')
    assert designer.locator('.sym-grid .sym').count() > 50


def test_icons_are_offered_with_the_symbols(designer):
    type_line(designer, 0, 'Light ')
    designer.click('#edSym')
    designer.wait_for_selector('#icoGrid .ico')
    # The first ones, all of them on request
    count = "() => document.querySelectorAll('#icoGrid .ico').length"
    assert designer.evaluate(count) == 120
    designer.click('#icoAll')
    assert designer.evaluate(count) > 7000
    designer.fill('#icoQ', 'lightbulb')
    assert designer.get_attribute('#icoGrid .ico', 'title') == 'mdi:lightbulb'
    designer.click('#icoGrid .ico')
    assert designer.input_value('[data-row="0"] .tx') == 'Light {{mdi:lightbulb}}'
    wait_for_preview(designer)
    assert 'Everything fits.' in fits_note(designer)


@pytest.mark.parametrize('view', ['edit', 'wizard'])
def test_phone_width_does_not_scroll_sideways(browser, server, view):
    context = browser.new_context(viewport={'width': 390, 'height': 844}, locale='en-US')
    page = context.new_page()
    page.goto(f'{server}/labeldesigner/#{view}')
    page.wait_for_load_state('networkidle')
    assert page.evaluate('() => document.documentElement.scrollWidth') <= 390
    context.close()


def test_new_labels_start_with_the_configured_defaults(browser, server, app, monkeypatch):
    for key, value in (('LABEL_DEFAULT_MARGIN_LEFT', 0), ('LABEL_DEFAULT_FONT_SIZE', 55), ('LABEL_DEFAULT_QR_SIZE', 6),
                       ('IMAGE_DEFAULT_MODE', 'black_and_white'), ('IMAGE_DEFAULT_BW_THRESHOLD', 90)):
        monkeypatch.setitem(app.config, key, value)
    context = browser.new_context(locale='en-US')
    page = context.new_page()
    page.goto(server + '/labeldesigner/#edit')
    wait_for_preview(page)
    design = page.evaluate('() => S.edit.design')
    assert (design['margin_left'], design['text'][0]['size'], design['qrcode_size']) == ('0', '55', '6')
    assert (design['image_mode'], design['image_bw_threshold']) == ('black_and_white', '90')
    context.close()


def test_size_of_a_dot_applies_to_a_code_of_fixed_size(designer):
    designer.click('[data-lay="codetext"]')
    wait_for_preview(designer)
    # Fitted to the label, the size of a dot does not apply
    assert designer.is_disabled('#edQrSize')
    designer.click('[data-fit="0"]')
    wait_for_preview(designer)
    # The code runs along the tape, its length grows
    length = preview_size(designer)[0]
    designer.fill('#edQrSize', '20')
    wait_for_preview(designer)
    assert preview_size(designer)[0] > length


def test_symbols_closed_while_loading_stay_closed(designer):
    designer.click('#edSym')
    designer.keyboard.press('Escape')
    designer.wait_for_timeout(1000)
    assert designer.evaluate("() => document.getElementById('layer').hidden")


def test_dates_keep_their_formats_when_one_is_added(designer):
    # A new [today] gets the format of the printer panel, the others keep theirs
    added, inserted = designer.evaluate("""() => {
        S.settings.dateFmt = '%d.%m.%Y';
        const one = '{{datetime:%B %Y}}', two = '{{datetime:%d.%m.%Y}}, {{datetime:%H:%M}}';
        return [fromDisplay(toDisplay(one) + ' [today]', one), fromDisplay('[today] ' + toDisplay(two), two)];
    }""")
    assert added == '{{datetime:%B %Y}} {{datetime:%d.%m.%Y}}'
    assert inserted == '{{datetime:%d.%m.%Y}} {{datetime:%d.%m.%Y}}, {{datetime:%H:%M}}'


def test_undo_history_keeps_an_image_once(designer):
    # Every step held a copy of the image, as did the label stored for the
    # next visit on every change
    out = io.BytesIO()
    Image.frombytes('L', (300, 300), os.urandom(90000)).save(out, format='PNG')
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'noise.png', 'mimeType': 'image/png', 'buffer': out.getvalue()}])
    wait_for_preview(designer)
    for i in range(3):
        type_line(designer, 0, f'Step {i}')
        kept(designer)
    data = designer.evaluate('() => S.edit.design.image.data.length')
    assert designer.evaluate('() => [S.hist.last, ...S.hist.undo].reduce((n, s) => n + s.length, 0)') < data
    assert designer.evaluate("() => localStorage.getItem('designer_edit').length") < data
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image.data.length') == data
    designer.reload()
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image.data.length') == data


def test_links_to_the_internet_ask_first(designer):
    # The label designer works without the internet, a link to a page there
    # asks before it opens it, also with the middle button
    designer.evaluate("() => { window.opened = []; window.open = (...args) => { window.opened.push(args); }; }")
    pages = len(designer.context.pages)
    designer.click('#githubLink')
    assert designer.inner_text('#layer .sheet h2') == 'Open github.com?'
    designer.click('#layer [data-close]')
    assert designer.is_hidden('#layer')
    designer.click('#githubLink', button='middle')
    designer.click('#layer [data-open]')
    assert designer.is_hidden('#layer')
    assert designer.evaluate('() => window.opened') == [['https://github.com/DL6ER/labelburg', '_blank', 'noopener']]
    assert len(designer.context.pages) == pages
    # Links within the label designer do not ask
    designer.click('#tabs [data-tab="wizard"]')
    assert designer.is_hidden('#layer')


def test_date_and_time_codes_are_explained_offline(designer):
    # The help on variables links to no page on the internet, the codes of
    # {{datetime:<format>}} are shown with examples from the server
    designer.click('#tplInfoBtn')
    assert designer.locator('#tplInfo a[href]').count() == 0
    designer.click('#tplInfo [data-dtcodes]')
    assert designer.inner_text('#layer h2') == 'Date and time codes'
    assert 'The examples are for Saturday, 7 March 2026, 14:05:09.' in designer.inner_text('#layer .dialog-h p')
    rows = designer.evaluate("() => [...document.querySelectorAll('#layer .codes tbody tr')].map(r => [...r.cells].map(c => c.innerText))")
    assert len(rows) == 21
    assert ['%-d', 'Day of the month, no leading zero', '7'] in rows and ['%V', 'Calendar week, starting on Monday', '10'] in rows
    designer.keyboard.press('Escape')
    assert designer.is_hidden('#layer')
    # The same help in the wizard, open there as well
    designer.click('#tabs [data-tab="wizard"]')
    designer.click('#wzVarsBox [data-dtcodes]')
    assert designer.inner_text('#layer h2') == 'Date and time codes'


def test_image_code_and_text_have_the_settings_of_both(designer):
    type_line(designer, 0, 'Pantry')
    designer.click('[data-lay="imagecode"]')
    wait_for_preview(designer)
    assert designer.locator('.layouts .lay').count() == 4
    assert designer.locator('#edCode').count() == 1 and designer.locator('#edFile').count() == 1
    # The server tells what is missing
    assert designer.inner_text('#edStage .err').strip() == 'The label has no image'
    assert 'Fix the label to print it.' in fits_note(designer)
    designer.set_input_files('#edFile', files=[{'name': 'black.png', 'mimeType': 'image/png', 'buffer': png()}])
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.print_type') == 'image_qrcode_text'
    assert designer.locator('#edStage .err').count() == 0
    assert 'Everything fits.' in fits_note(designer)
    assert designer.evaluate("() => { const form = formFor(S.edit.design); return [form.get('print_type'), form.get('image') instanceof Blob]; }") == \
        ['image_qrcode_text', True]
    # The image left of the text, the code right of it, along the tape
    assert designer.evaluate("() => document.querySelector('[data-side].on').dataset.side") == 'left'
    width, height = preview_size(designer)
    assert width > 2 * height
    designer.click('[data-side="right"]')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image_position') == 'right'
    # One size for both, under Image: a QR code has the size of a dot when
    # the image is scaled
    assert designer.locator('[data-fit="1"]').count() == 1 and designer.locator('#edCodeScale').count() == 0
    assert designer.is_disabled('#edQrSize')
    designer.click('[data-fit="0"]')
    designer.fill('#edScale', '50')
    wait_for_preview(designer)
    assert designer.is_enabled('#edQrSize')
    # The scale is the image's, another kind of code leaves it
    designer.select_option('#edBarcode', 'CODE128')
    designer.select_option('#edBarcode', 'QR')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image_scaling_factor') == '50'
    assert 'Everything fits.' in fits_note(designer)


def test_image_code_and_text_keep_the_settings_of_the_image(designer):
    # The rotation is the image's: kept from the image layout, not from a code
    designer.click('[data-lay="image"]')
    designer.click('[data-rot="90"]')
    designer.click('[data-lay="imagecode"]')
    assert designer.evaluate('() => S.edit.design.image_rotation') == '90'
    designer.click('[data-lay="codetext"]')
    designer.click('[data-lay="imagecode"]')
    assert designer.evaluate('() => S.edit.design.image_rotation') == '0'
    # An image chosen keeps the code
    designer.set_input_files('#edFile', files=[{'name': 'black.png', 'mimeType': 'image/png', 'buffer': png()}])
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.print_type') == 'image_qrcode_text'
    # Without text the image keeps its place and distance to the code
    type_line(designer, 0, '')
    designer.evaluate('() => renderEditor()')
    wait_for_preview(designer)
    assert designer.is_enabled('[data-side="right"]') and designer.is_enabled('#edGap')
