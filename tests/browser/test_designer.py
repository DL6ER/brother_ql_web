"""The label designer in a browser: the editor, the wizard and what they
share. The API itself is tested without a browser."""
import io
import json
import os

import pytest
from PIL import Image

from browser_helpers import wait_for_preview


def type_line(page, row, text):
    page.fill(f'[data-row="{row}"] .tx', text)
    wait_for_preview(page)


def fits_note(page) -> str:
    return page.inner_text('#edFits')


def preview_size(page, stage='#edStage') -> tuple:
    return tuple(page.eval_on_selector(f'{stage} .paper img', 'img => [img.naturalWidth, img.naturalHeight]'))


def printed(page, count=1):
    page.wait_for_function("count => S.history.length === count && S.history.every(h => h.state !== 'printing')", arg=count)
    return page.evaluate('() => S.history.map(h => h.state)')


def png(width=200, height=100) -> bytes:
    out = io.BytesIO()
    Image.new('L', (width, height), 'black').save(out, format='PNG')
    return out.getvalue()


def pdf(*sizes) -> bytes:
    pages = [Image.new('L', size, 'black') for size in sizes]
    out = io.BytesIO()
    pages[0].save(out, format='PDF', save_all=True, append_images=pages[1:], resolution=100)
    return out.getvalue()


def fake_printer(page, **status):
    """Answer the status queries with a QL-800 that reports the given
    status, e.g. the loaded labels, and load the page again."""
    printer = {'errors': [], 'model': 'QL-800', 'path': 'simulation', 'media_width': None, 'media_length': 0, **status}
    body = json.dumps({**printer, 'printers': [printer], 'selected': 'simulation'})
    page.route('**/api/printer_status*', lambda route: route.fulfill(content_type='application/json', body=body))
    page.reload()
    wait_for_preview(page)


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


def test_preview_of_typed_text(designer):
    type_line(designer, 0, 'Hello')
    assert designer.get_attribute('#edStage .paper img', 'src').startswith('blob:')
    assert 'Everything fits.' in fits_note(designer)
    assert designer.is_enabled('#edPrint')


def test_text_that_does_not_fit_can_be_shrunk(designer):
    designer.click('[data-shrink="0"]')
    type_line(designer, 0, 'A very long line that does not fit on the label at all, not even close')
    designer.select_option('#edSize', '62x29')
    wait_for_preview(designer)
    assert 'does not fit' in fits_note(designer)
    designer.click('#edFits button')
    wait_for_preview(designer)
    assert 'Everything fits.' in fits_note(designer)
    assert 'on' in designer.get_attribute('[data-shrink="1"]', 'class')


def test_blanks_are_filled_in_and_printed(designer, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    type_line(designer, 0, 'Shelf [Room]')
    field = '#edFields input[data-blank="Room"]'
    designer.wait_for_selector(field)
    assert 'need' in designer.get_attribute(field, 'class')
    designer.fill(field, 'Garage')
    wait_for_preview(designer)
    assert 'need' not in designer.get_attribute(field, 'class')
    designer.click('#edPrint')
    assert printed(designer) == ['printed']
    assert len(list(tmp_path.glob('*.png'))) == 1


def test_filled_in_blanks_stay_when_the_design_changes(designer):
    type_line(designer, 0, 'Shelf [Room]')
    field = '#edFields input[data-blank="Room"]'
    designer.wait_for_selector(field)
    designer.fill(field, 'Garage')
    wait_for_preview(designer)
    designer.click('[data-row="0"] [data-act="bold"]')
    wait_for_preview(designer)
    assert designer.input_value(field) == 'Garage'
    assert designer.evaluate('() => S.edit.values.Room') == 'Garage'


@pytest.mark.parametrize('zone', ['Pacific/Kiritimati', 'Pacific/Pago_Pago'])
def test_date_blanks_start_at_the_local_date(browser, server, zone):
    # 25 hours apart, one of them is always on another date than UTC
    context = browser.new_context(locale='en-US', timezone_id=zone)
    page = context.new_page()
    page.goto(server + '/labeldesigner/#edit')
    page.wait_for_load_state('networkidle')
    assert page.evaluate("() => today() === new Date().toLocaleDateString('sv-SE')")
    context.close()


def test_ctrl_enter_prints(designer, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    type_line(designer, 0, 'Hello')
    designer.keyboard.press('Control+Enter')
    assert printed(designer) == ['printed']


def test_copies_buttons(designer, monkeypatch, tmp_path):
    # Never fewer than one copy, each copy prints as a label of its own
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    type_line(designer, 0, 'Hello')
    for _ in range(2):
        designer.click('.ed-top [data-step="1"]')
    assert designer.input_value('#edCopies') == '3'
    for _ in range(5):
        designer.click('.ed-top [data-step="-1"]')
    assert designer.input_value('#edCopies') == '1'
    designer.click('.ed-top [data-step="1"]')
    designer.click('#edPrint')
    assert printed(designer) == ['printed']
    assert len(list(tmp_path.glob('*.png'))) == 2


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
        designer.select_option('[data-row="0"] select.lf', designer.evaluate('() => DEFAULT_FAMILY'))
        wait_for_preview(designer)
        assert 'is not installed' not in fits_note(designer)
    finally:
        os.remove(path)


def test_undo_and_redo(designer):
    type_line(designer, 0, 'One')
    type_line(designer, 0, 'Two')
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == 'One'
    designer.click('#edRedo')
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == 'Two'


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


def test_label_sizes_follow_the_printer_model(designer):
    # The simulator is a QL-800
    offered = designer.eval_on_selector_all('#edSize option', 'options => options.map(o => o.value)')
    assert '62' in offered and '62red' in offered
    assert 'pt36' not in offered and '103x164' not in offered
    # A label made for another printer keeps its size
    designer.evaluate("() => { S.edit.design.label_size = 'pt36'; renderEditor(); }")
    assert designer.input_value('#edSize') == 'pt36'


def test_tape_follows_the_printer(designer):
    # DK and TZe tape of 12 mm have the same size, the model tells them apart
    designer.evaluate("() => { S.printer = { path: 'a', model: 'PT-P750W', media_width: 12, media_length: 0 }; detectTape(); }")
    assert designer.evaluate('() => S.tape') == 'pt12'
    # A tape set by hand belongs to its printer
    designer.evaluate("() => { S.settings.tapes = { a: 'pt24' }; detectTape(); }")
    assert designer.evaluate('() => [S.tape, S.tapeManual]') == ['pt24', True]
    designer.evaluate("() => { S.printer = { path: 'b', model: 'QL-800', media_width: 62, media_length: 0 }; detectTape(); }")
    assert designer.evaluate('() => [S.tape, S.tapeManual]') == ['62', False]


def test_loaded_label_can_be_chosen(designer):
    # The printer reports 29 x 90 mm labels, the new label is for 62 mm tape
    fake_printer(designer, media_width=29, media_length=90)
    type_line(designer, 0, 'Hello')
    assert designer.inner_text('.tape-note') == 'The QL-800 has 29 x 90 mm loaded. Switch to it'
    # Printing asks first
    designer.click('#edPrint')
    designer.wait_for_selector('.sheet h2:text-is("This label needs 62 mm endless")')
    designer.click('.sheet [data-r="cancel"]')
    assert designer.evaluate('() => S.history.length') == 0
    designer.click('#edUseTape')
    wait_for_preview(designer)
    assert designer.input_value('#edSize') == '29x90'
    assert 'Matches the tape in the printer' in designer.inner_text('.tape-note')


def test_configured_printers_are_offered_and_the_choice_is_kept(designer, app, monkeypatch):
    # Not the printers of the machine running the tests, the page would print
    # on them. One that does not answer is offered with the reason.
    assert designer.evaluate('() => S.printers.map(p => p.path)') == ['simulation']
    other = 'file:///nonexistent/lp0'
    monkeypatch.setitem(app.config, 'PRINTERS', [('QL-800', 'simulation'), ('QL-1100', other)])
    designer.reload()
    wait_for_preview(designer)
    designer.click('#printerChip')
    assert designer.eval_on_selector_all('.plist [data-printer]', 'rows => rows.map(r => r.dataset.printer)') == ['simulation', other]
    assert 'does not answer' in designer.inner_text(f'[data-printer="{other}"]')
    designer.click(f'[data-printer="{other}"]')
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    # Kept after reloading, the label sizes follow its model
    designer.reload()
    wait_for_preview(designer)
    assert designer.inner_text('#printerChip b') == 'QL-1100'
    offered = designer.eval_on_selector_all('#edSize option', 'options => options.map(o => o.value)')
    assert '103x164' in offered and '62red' not in offered


def test_no_600_dpi_on_the_ql1100(designer):
    type_line(designer, 0, 'Hello')
    width, _ = preview_size(designer)
    designer.click('#printerChip')
    designer.click('[data-q="high"]')
    designer.keyboard.press('Escape')
    designer.wait_for_function("() => document.querySelector('#edStage .paper img')")
    wait_for_preview(designer)
    assert preview_size(designer)[0] == 2 * width
    # The choice is kept for other printers, this one previews and prints at 300 dpi
    fake_printer(designer, model='QL-1100', media_width=62)
    assert preview_size(designer)[0] == width
    assert designer.evaluate("() => formFor(S.edit.design, 1, true).get('high_res')") == '0'
    designer.click('#printerChip')
    assert designer.is_disabled('[data-q="high"]') and 'on' in designer.get_attribute('[data-q="high"]', 'class')
    assert 'The QL-1100 cannot print at 600 dpi.' in designer.inner_text('.settings')
    # Nor can a P-touch printer
    designer.keyboard.press('Escape')
    fake_printer(designer, model='PT-P750W', media_width=24)
    designer.click('#printerChip')
    assert designer.is_disabled('[data-q="high"]')
    assert 'The PT-P750W cannot print at 600 dpi.' in designer.inner_text('.settings')


def test_settings_that_do_not_apply_are_disabled(designer):
    type_line(designer, 0, 'Hello')
    designer.click('[data-lay="codetext"]')
    wait_for_preview(designer)
    assert designer.is_enabled('#edQrSize')
    designer.select_option('#edBarcode', 'EAN13')
    designer.fill('#edCode', '123456789012')
    wait_for_preview(designer)
    assert designer.is_disabled('#edQrSize') and designer.is_disabled('#edQrCorr')
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'black.png', 'mimeType': 'image/png', 'buffer': png()}])
    wait_for_preview(designer)
    assert designer.is_enabled('#edThr')
    # Red needs black and red tape
    assert designer.is_disabled('[data-imode="red_and_black"]')
    designer.click('[data-imode="grayscale"]')
    wait_for_preview(designer)
    assert designer.is_disabled('#edThr')


def test_code_alone_has_no_text_settings(designer):
    type_line(designer, 0, 'Spices')
    designer.click('[data-lay="code"]')
    wait_for_preview(designer)
    assert designer.is_disabled('[data-shrink="1"]')
    assert designer.locator('.label-s:text-is("Code runs")').count() == 1


def test_ptouch_tape_is_shown_at_180_dpi(designer):
    designer.evaluate("""() => {
        S.printer = { path: 'a', model: 'PT-E550W', media_width: 12, media_length: 0 };
        detectTape();
        S.edit.design.label_size = S.tape;
        renderEditor();
    }""")
    offered = designer.eval_on_selector_all('#edSize option', 'options => options.map(o => o.value)')
    assert 'pt6' in offered and 'pt9' in offered and '62' not in offered
    # 35 dots at 180 dpi
    assert designer.input_value('[data-margin="left"]') == '4.9'
    type_line(designer, 0, 'Hello')
    _, height = preview_size(designer)
    assert designer.inner_text('#edStage .dim-x') == '12 mm'
    assert designer.inner_text('#edStage .dim-y') == f'{round(height / 180 * 25.4)} mm'


def test_switch_shows_how_the_text_runs_on_the_tape(designer):
    def runs():
        wait_for_preview(designer)
        return designer.inner_text('[data-along].on')
    type_line(designer, 0, 'Spices')
    assert runs() == 'Across the tape'
    # 62 x 29 mm labels are wider than long, standard is across the tape
    designer.select_option('#edSize', '62x29')
    assert runs() == 'Across the tape'
    designer.click('[data-along="1"]')
    assert runs() == 'Along'
    assert designer.evaluate('() => S.edit.design.orientation') == 'rotated'
    designer.select_option('#edSize', '29x90')
    assert runs() == 'Along'
    # Round labels are shown round, endless tape keeps the direction of the text
    designer.select_option('#edSize', 'd24')
    assert runs() == 'Across the tape'
    assert 'round' in designer.get_attribute('#edStage', 'class')
    designer.select_option('#edSize', '62')
    assert runs() == 'Across the tape'


def test_red_prints_black_on_tape_without_red(designer):
    type_line(designer, 0, 'Danger')
    designer.select_option('#edSize', '62red')
    wait_for_preview(designer)
    designer.click('[data-row="0"] [data-act="red"]')
    wait_for_preview(designer)
    designer.select_option('#edSize', '62')
    wait_for_preview(designer)
    assert 'red parts print black' in fits_note(designer)
    assert 'nored' in designer.get_attribute('[data-row="0"] [data-act="red"]', 'class')
    assert designer.evaluate("() => JSON.parse(formFor(S.edit.design).get('text'))[0].color") == 'black'
    assert designer.evaluate('() => S.edit.design.text[0].color') == 'red'


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


def test_preview_has_the_resolution_of_the_print(designer):
    type_line(designer, 0, 'Hello')
    width, _ = preview_size(designer)
    designer.click('#printerChip')
    designer.click('[data-q="high"]')
    designer.keyboard.press('Escape')
    designer.wait_for_function("() => document.querySelector('#edStage .paper img')")
    wait_for_preview(designer)
    # Twice the dots across the tape, still 62 mm wide
    assert preview_size(designer)[0] == 2 * width
    assert designer.inner_text('#edStage .dim-x') == '62 mm'


def test_images_of_the_same_size_have_their_own_preview(designer):
    # Same name and length, other content: the preview shows what prints
    same_length, differ = designer.evaluate("""async () => {
        const png = color => {
            const c = document.createElement('canvas');
            c.width = c.height = 40;
            const g = c.getContext('2d');
            g.fillStyle = color;
            g.fillRect(0, 0, 40, 40);
            return c.toDataURL('image/png').split(',')[1];
        };
        const [a, b] = [png('#111'), png('#eee')];
        const design = data => Object.assign(baseDesign('62'), { print_type: 'image', image: { data, mime: 'image/png', name: 'photo.png' } });
        const [pa, pb] = [await preview(design(a), {}), await preview(design(b), {})];
        return [a.length === b.length, pa.url !== pb.url];
    }""")
    assert same_length
    assert differ


def test_pasted_image_becomes_the_label(designer):
    designer.evaluate("""async () => {
        const canvas = document.createElement('canvas');
        canvas.width = 200;
        canvas.height = 100;
        canvas.getContext('2d').fillRect(20, 20, 160, 60);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        const data = new DataTransfer();
        data.items.add(new File([blob], 'image.png', { type: 'image/png' }));
        document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }));
    }""")
    designer.wait_for_selector('[data-lay="image"].on')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image.name') == 'image.png'


def test_several_images_are_printed(designer, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[
        {'name': 'one.png', 'mimeType': 'image/png', 'buffer': png()},
        {'name': 'two.png', 'mimeType': 'image/png', 'buffer': png(100, 200)},
    ])
    designer.wait_for_function("() => document.querySelector('.ed-panel').innerText.includes('2 files, each prints as a label of its own')")
    wait_for_preview(designer)
    designer.click('#edPrint')
    assert printed(designer) == ['printed']
    assert len(list(tmp_path.glob('*.png'))) == 2


def test_dropped_image_becomes_the_label(designer):
    designer.evaluate("""async () => {
        const canvas = document.createElement('canvas');
        canvas.width = 200;
        canvas.height = 100;
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        const data = new DataTransfer();
        data.items.add(new File([blob], 'dropped.png', { type: 'image/png' }));
        document.querySelector('#edStage').dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
    }""")
    designer.wait_for_selector('[data-lay="image"].on')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image.name') == 'dropped.png'


def test_pages_of_a_pdf_can_be_chosen(designer):
    designer.click('[data-lay="image"]')
    wait_for_preview(designer)
    assert designer.is_disabled('#edPages')
    designer.set_input_files('#edFile', files=[{'name': 'pages.pdf', 'mimeType': 'application/pdf', 'buffer': pdf((300, 200), (200, 300), (400, 100))}])
    designer.wait_for_selector('#edPages:enabled')
    wait_for_preview(designer)
    length, _ = preview_size(designer)
    designer.fill('#edPages', '2')
    wait_for_preview(designer)
    # Page 2 is portrait, fitted across the tape it makes a shorter label
    assert preview_size(designer)[0] < length
    designer.fill('#edPages', '7')
    wait_for_preview(designer)
    assert 'The PDF has 3 pages, there is no page 7' in designer.inner_text('#edStage')


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


def share(page, server, **form):
    """What Android does with something shared with the installed app."""
    response = page.request.post(server + '/labeldesigner/share', multipart=form, max_redirects=0)
    assert response.status == 303
    page.goto(server + response.headers['location'])
    page.wait_for_selector('#edStage')
    wait_for_preview(page)
    # The token is used up and gone from the address
    assert 'shared=' not in page.url


def test_shared_image_becomes_the_label(designer, server):
    share(designer, server, file={'name': 'photo.png', 'mimeType': 'image/png', 'buffer': png()})
    assert 'on' in designer.get_attribute('[data-lay="image"]', 'class')
    assert designer.input_value('#edTitle') == 'photo'


def test_shared_link_becomes_a_qr_code(designer, server):
    share(designer, server, text='https://example.com/shelf/12')
    assert 'on' in designer.get_attribute('[data-lay="code"]', 'class')
    assert designer.input_value('#edCode') == 'https://example.com/shelf/12'


def test_shared_text_becomes_the_label(designer, server):
    share(designer, server, text='Tomato soup\nFrozen')
    assert designer.evaluate('() => S.edit.design.text.map(l => l.text)') == ['Tomato soup', 'Frozen']


def test_shared_content_is_only_handed_out_once(designer, server):
    designer.goto(server + '/labeldesigner/?shared=used')
    designer.wait_for_function("() => document.getElementById('toast').innerText.includes('no longer available')")


def test_saved_label_is_printed_from_the_wizard(designer, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="saved"]')
    designer.click('#libGrid .thumb:has(.t:text-is("QR Example"))')
    wait_for_preview(designer, '#libStage')
    designer.click('#libPrint')
    assert printed(designer) == ['printed']
    assert len(list(tmp_path.glob('*.png'))) == 1
    # It is in the printed labels, ready to print again
    designer.click('.lib-nav [data-cat="printed"]')
    assert designer.locator('#libGrid .thumb').count() == 1


def test_saved_label_keeps_its_title_and_changes(designer, server):
    designer.fill('#edTitle', 'Küche, Regal 2')
    type_line(designer, 0, 'Spices')
    designer.click('#edSave')
    designer.wait_for_function("() => S.edit.origin && S.saved.some(x => x.title === 'Küche, Regal 2')")
    name = designer.evaluate('() => S.edit.origin.id')
    try:
        designer.click('[data-tab="wizard"]')
        designer.click('.lib-nav [data-cat="saved"]')
        designer.click('#libGrid .thumb:has(.t:text-is("Küche, Regal 2"))')
        wait_for_preview(designer, '#libStage')
        # Changed in the editor, the wizard shows the change
        designer.click('#libEdit')
        designer.wait_for_selector('#edStage')
        type_line(designer, 0, 'Tea')
        designer.click('#edSave')
        designer.wait_for_function("() => document.getElementById('toast').innerText.startsWith('Saved')")
        designer.click('[data-tab="wizard"]')
        wait_for_preview(designer, '#libStage')
        assert designer.input_value('#libFields input') == 'Tea'
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': name})


def test_opening_another_label_starts_a_new_undo_history(designer):
    type_line(designer, 0, 'One')
    type_line(designer, 0, 'Two')
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


def test_text_in_the_wizard_shows_looks_and_templates(designer):
    designer.click('[data-tab="wizard"]')
    designer.fill('#wzText', 'Kitchen shelf')
    designer.wait_for_function("() => document.querySelectorAll('#fzLooks .tile').length === 6")
    # Templates with a matching name or category come first
    designer.wait_for_function("() => document.getElementById('libTitle').innerText === 'Templates for \"Kitchen shelf\"'")
    # A look is shown next to the list, its design can be changed in the editor
    designer.click('#fzLooks .tile:first-child .thumb')
    wait_for_preview(designer, '#libStage')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    assert designer.input_value('[data-row="0"] .tx') == 'Kitchen shelf'


def test_choosing_another_look_drops_the_pending_refresh(designer):
    # A field changed just before another look was chosen must not draw the
    # previous look into the stage of the new one
    designer.click('[data-tab="wizard"]')
    designer.fill('#wzText', 'Shelf [Room]')
    designer.wait_for_function("() => document.querySelectorAll('#fzLooks .tile').length === 6")
    designer.click('#fzLooks [data-look="0"]')
    designer.wait_for_selector('#libFields input[data-blank="Room"]')
    wait_for_preview(designer, '#libStage')
    designer.evaluate("""async () => {
        window.drawn = [];
        const draw = showOnStage;
        window.showOnStage = function (el, design, values) {
            drawn.push({ connected: el.isConnected, room: values && values.Room });
            return draw.apply(this, arguments);
        };
        const field = document.querySelector('#libFields input[data-blank="Room"]');
        field.value = 'Garage';
        field.dispatchEvent(new Event('input', { bubbles: true }));
        await chooseLook(1);
    }""")
    designer.wait_for_timeout(600)
    wait_for_preview(designer, '#libStage')
    # The new look was drawn, the previous one not
    assert designer.evaluate("() => drawn.some(d => d.connected)")
    assert not designer.evaluate("() => drawn.some(d => d.connected && d.room === 'Garage')")


def test_look_with_blanks_asks_for_them_before_printing(designer):
    designer.click('[data-tab="wizard"]')
    designer.fill('#wzText', 'Shelf [Room]')
    designer.wait_for_function("() => document.querySelectorAll('#fzLooks .tile').length === 6")
    designer.click('#fzLooks [data-plook="0"]')
    designer.wait_for_selector('#libFields input[data-blank="Room"]:focus')
    assert designer.evaluate('() => S.history.length') == 0
    # The tiles open with the keyboard as well
    designer.focus('#fzLooks [data-look="5"]')
    designer.keyboard.press('Enter')
    designer.wait_for_function("() => S.libSel && S.libSel.id === 'look5'")
    wait_for_preview(designer, '#libStage')


def test_wizard_counts_what_was_printed(designer, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="all"]')
    designer.fill('#wzText', 'Shelf\nTools')
    designer.wait_for_function("() => document.querySelectorAll('#fzLooks .tile').length === 6")
    # Two lines stay visible above the long list
    assert designer.evaluate("() => { const t = document.getElementById('wzText'); return t.scrollHeight <= t.clientHeight; }")
    designer.click('#fzLooks [data-plook="0"]')
    assert printed(designer) == ['printed']
    assert designer.inner_text('.lib-nav [data-cat="printed"] .n') == '1'


def test_demo_labels(designer):
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="signs"]')
    assert designer.inner_text('#libTitle') == 'Safety signs'
    # Those for the tape in the printer come first and are counted, the others dimmed
    fits = designer.eval_on_selector_all('#libGrid .thumb', 'thumbs => thumbs.map(t => !t.classList.contains("other"))')
    count = int(designer.inner_text('.lib-nav [data-cat="signs"] .n'))
    assert 0 < count < len(fits) and fits == [True] * count + [False] * (len(fits) - count)
    designer.wait_for_function("() => document.querySelector('#libGrid .thumb img').src.startsWith('blob:')")
    designer.click('#libGrid .thumb:has(.t:text-is("Fire extinguisher"))')
    wait_for_preview(designer, '#libStage')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.label_size, S.edit.design.image.name]') == ['62', 'fire-extinguisher.png']
    assert designer.evaluate('() => S.edit.design.text.map(l => l.text)') == ['Fire', 'extinguisher']


def test_demo_labels_in_german(designer):
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="signs"]')
    titles = "() => Array.from(document.querySelectorAll('#libGrid .thumb .t')).map(t => t.textContent)"
    # In the language of the browser
    assert designer.input_value('#libLang') == 'en'
    assert 'Fire extinguisher' in designer.evaluate(titles)
    count = designer.inner_text('.lib-nav [data-cat="signs"] .n')
    designer.select_option('#libLang', 'de')
    names = designer.evaluate(titles)
    assert 'Feuerlöscher' in names and 'Fire extinguisher' not in names
    # Every label is there in both languages
    assert designer.inner_text('.lib-nav [data-cat="signs"] .n') == count
    # Kept for the next visit
    designer.reload()
    designer.wait_for_selector('#libLang')
    assert designer.input_value('#libLang') == 'de'
    assert 'Feuerlöscher' in designer.evaluate(titles)


def test_search_finds_labels_and_commands(designer):
    designer.keyboard.press('Control+k')
    designer.fill('#palQ', 'QR')
    designer.wait_for_function("() => document.getElementById('palRes').innerText.includes('QR Example')")
    designer.fill('#palQ', '>dark')
    designer.wait_for_function("() => document.getElementById('palRes').innerText.includes('Dark mode')")
    designer.keyboard.press('Enter')
    assert designer.get_attribute('html', 'data-theme') == 'dark'
    # Remembered
    designer.reload()
    assert designer.get_attribute('html', 'data-theme') == 'dark'


@pytest.mark.parametrize('view', ['edit', 'wizard'])
def test_phone_width_does_not_scroll_sideways(browser, server, view):
    context = browser.new_context(viewport={'width': 390, 'height': 844}, locale='en-US')
    page = context.new_page()
    page.goto(f'{server}/labeldesigner/#{view}')
    page.wait_for_load_state('networkidle')
    assert page.evaluate('() => document.documentElement.scrollWidth') <= 390
    context.close()
