"""The Brother VC-500W in the label designer: its color tape, colors, print
modes, the tape left and what it does while it prints."""

import io
import json

import pytest
from PIL import Image, ImageDraw

from tests.browser.helpers import fits_note, png, preview_size, printed, type_line, wait_for_preview

VC = {'errors': [], 'model': 'VC-500W', 'path': 'file:///dev/usb/lp0', 'media_width': 25, 'media_length': 0,
      'media_type': 'Continuous length tape', 'tape_length_mm': 5004, 'tape_remaining_mm': 4596, 'color_support': True}


def fake_vc500w(page, **status):
    """Answer the status queries with a VC-500W and load the page again."""
    printer = {**VC, **status}
    body = json.dumps({**printer, 'printers': [printer], 'selected': printer['path']})
    page.route('**/api/printer_status*', lambda route: route.fulfill(content_type='application/json', body=body))
    page.reload()
    wait_for_preview(page)


def use_tape(page):
    page.evaluate('() => { setSize(S.edit.design, S.tape); renderEditor(); }')
    wait_for_preview(page)


def pick(page, selector, color):
    page.eval_on_selector(selector, '(el, color) => { el.value = color; el.dispatchEvent(new Event("change")); }', color)
    wait_for_preview(page)


def test_color_tape_of_the_printer(designer):
    fake_vc500w(designer)
    assert designer.evaluate('() => S.tape') == 'vc25'
    offered = designer.eval_on_selector_all('#edSize option', 'options => options.map(o => o.value)')
    assert offered == ['62', 'vc9', 'vc12', 'vc19', 'vc25', 'vc50']
    assert designer.eval_on_selector('#edSize optgroup:last-child', 'g => g.label') == 'Color tape'
    use_tape(designer)
    assert designer.inner_text('.tape-note').strip() == 'Matches the tape in the printer, 4.6 m of 5.0 m left'
    # 35 dots of the margin at 320 dpi
    assert designer.input_value('[data-margin="left"]') == '2.8'
    assert designer.inner_text('#printerChip .tape') == '25 mm color tape'


def test_any_color_on_color_tape(designer):
    fake_vc500w(designer)
    use_tape(designer)
    type_line(designer, 0, 'Blue')
    assert designer.query_selector('[data-row="0"] [data-act="red"]') is None
    pick(designer, '[data-row="0"] input.lcol', '#1e90ff')
    assert designer.evaluate("() => JSON.parse(formFor(S.edit.design).get('text'))[0].color") == '#1e90ff'
    assert 'red parts' not in fits_note(designer) and 'one of them' not in fits_note(designer)
    # The frame and the code take any color too
    designer.click('[data-frame="square"]')
    wait_for_preview(designer)
    pick(designer, '#edFrameColor', '#00a000')
    designer.click('[data-lay="codetext"]')
    wait_for_preview(designer)
    pick(designer, '#edCodeColor', '#c00000')
    form = designer.evaluate("() => Object.fromEntries(['border_color', 'print_color'].map(k => [k, formFor(S.edit.design).get(k)]))")
    assert form == {'border_color': '#00a000', 'print_color': '#c00000'}
    # Black is kept by name
    pick(designer, '#edCodeColor', '#000000')
    assert designer.evaluate('() => S.edit.design.print_color') == 'black'


def test_colors_on_tape_without_them(designer):
    fake_vc500w(designer)
    use_tape(designer)
    type_line(designer, 0, 'Blue')
    pick(designer, '[data-row="0"] input.lcol', '#1e90ff')
    # A label made for another printer
    designer.evaluate("() => { S.edit.design.label_size = '62'; renderEditor(); }")
    wait_for_preview(designer)
    assert 'red parts print black' in fits_note(designer)
    assert designer.evaluate("() => JSON.parse(formFor(S.edit.design).get('text'))[0].color") == 'black'
    # On black and red tape the server prints it in one of them
    designer.evaluate("() => { S.edit.design.label_size = '62red'; renderEditor(); }")
    wait_for_preview(designer)
    assert 'other colors print in one of them' in fits_note(designer)
    assert designer.evaluate("() => JSON.parse(formFor(S.edit.design).get('text'))[0].color") == '#1e90ff'


def test_images_print_in_color(designer):
    fake_vc500w(designer)
    use_tape(designer)
    designer.click('[data-lay="image"]')
    wait_for_preview(designer)
    assert designer.query_selector('[data-imode]') is None and designer.query_selector('#edThr') is None
    assert 'Images print in full color on this tape.' in designer.inner_text('.ed-panel')


def test_fast_or_quality_without_600_dpi(designer):
    fake_vc500w(designer)
    designer.evaluate("() => { S.settings.quality = 'high'; }")
    designer.click('#printerChip')
    designer.wait_for_selector('[data-q]')
    assert designer.eval_on_selector_all('[data-q]', 'buttons => buttons.map(b => b.textContent)') == ['Fast', 'Quality']
    # Labels set for 600 dpi print in quality
    assert designer.inner_text('[data-q].on') == 'Quality'
    assert designer.evaluate("() => ['high_res', 'fast_printing'].map(k => formFor(S.edit.design, 1, true).get(k))") == ['0', '0']
    designer.click('[data-q="fast"]')
    assert designer.evaluate("() => formFor(S.edit.design, 1, true).get('fast_printing')") == '1'
    designer.click('#qInfoBtn')
    assert 'colors come out lighter' in designer.inner_text('#qInfo')


def test_half_cuts_but_no_chain_printing(designer):
    # Labels not cut get a half cut unless switched off, chain printing does
    # not exist on this printer
    fake_vc500w(designer)
    designer.evaluate("() => { S.settings.cut = 'last'; }")
    designer.click('#printerChip')
    designer.wait_for_selector('.pp [data-c]')
    assert designer.locator('.pp [data-c="half_cut"][data-v="1"].on:enabled').count() == 1
    chain = designer.locator('.pp [data-c="chain"][data-v="0"]')
    assert chain.get_attribute('class') == 'on' and chain.is_disabled()
    assert designer.get_attribute('.pp div:has(> .seg [data-c="chain"])', 'title') == 'Only for P-touch printers'
    assert designer.get_attribute('.pp .cut-strip', 'title') == 'Half cut between the labels, cut after the last.'
    designer.click('.pp [data-c="half_cut"][data-v="0"]')
    assert designer.get_attribute('.pp .cut-strip', 'title') == 'Cut after the last label.'


def test_never_cut_is_not_offered(designer):
    # The printer would keep the labels until the next print, which follows
    # them without a cut. Set before for all printers, it cuts after the last.
    fake_vc500w(designer)
    designer.evaluate("() => { S.settings.cut = 'none'; }")
    designer.click('#printerChip')
    designer.wait_for_selector('.pp [data-c]')
    never = designer.locator('.pp [data-c="cut"][data-v="none"]')
    assert never.is_disabled() and never.get_attribute('title') == 'The VC-500W would keep the labels inside until the next print'
    assert designer.inner_text('.pp [data-c="cut"].on') == 'After the last label'
    assert designer.get_attribute('.pp .cut-strip', 'title') == 'Half cut between the labels, cut after the last.'
    assert designer.evaluate("() => [cutOf({ cut: 'none', label_size: 'vc25' }).cut, cutOf({ cut: 'none', label_size: 'pt12' }).cut]") == ['last', 'none']
    form = designer.evaluate("() => Object.fromEntries(formFor(Object.assign({}, S.edit.design, { cut: 'none', label_size: 'vc25' }), 1, true))")
    assert form['cut'] == 'last'


def test_tape_left_in_the_printer_panel(designer):
    fake_vc500w(designer)
    designer.click('#printerChip')
    designer.wait_for_selector('.plist')
    assert '4.6 m of 5.0 m left' in designer.inner_text('.plist')
    assert 'Wi-Fi' not in designer.inner_text('.plist')


def test_connection_in_the_printer_panel(designer):
    fake_vc500w(designer, weak_connection=True)
    designer.click('#printerChip')
    designer.wait_for_selector('.plist')
    assert 'The Wi-Fi connection seems weak, try moving the printer closer to the Wi-Fi router.' in designer.inner_text('.plist')


def test_stages_are_shown_while_printing(designer):
    fake_vc500w(designer, job={'stage': 'Preheating', 'label': 1, 'labels': 2})
    use_tape(designer)
    type_line(designer, 0, 'Spices')
    pending = []
    designer.route('**/api/print', lambda route: pending.append(route))
    designer.click('#edPrint')
    designer.wait_for_function("() => document.querySelector('#toast').textContent === 'Preheating, label 1 of 2'")
    # The answer ends it
    pending[0].fulfill(content_type='application/json', body=json.dumps({'success': True}))
    assert printed(designer) == ['printed']
    assert designer.inner_text('#toast').startswith('Printed 1 label')


def test_stage_with_how_far_the_label_is(designer):
    assert designer.evaluate("() => jobText({ stage: 'Printing', percent: 40, label: 2, labels: 3 })") == 'Printing 40 %, label 2 of 3'
    assert designer.evaluate("() => jobText({ stage: 'Take the printed label out of the printer', label: 2, labels: 2 })") == \
        'Take the printed label out of the printer, label 2 of 2'
    assert designer.evaluate("() => jobText({ stage: 'Preheating' })") == 'Preheating'


def test_large_preview_of_colors_is_smooth(designer):
    fake_vc500w(designer)
    use_tape(designer)
    type_line(designer, 0, 'A')
    designer.click('#edStage .zoom')
    designer.evaluate("() => { Object.defineProperty(window, 'devicePixelRatio', { get: () => 3 }); placeOnStage($('#edStage'), true); }")
    assert designer.get_attribute('#edStage .paper img', 'class') is None


@pytest.mark.parametrize('lang, title', [('en', 'Labelburg sticker'), ('de', 'Labelburg-Aufkleber')])
def test_sticker_of_this_project_for_the_50_mm_tape(designer, lang, title):
    fake_vc500w(designer, media_width=50)
    designer.click('[data-tab="wizard"]')
    designer.select_option('#libLang', lang)
    designer.click('.lib-nav [data-cat="events"]')
    sticker = f'#libGrid .thumb:has(.t:text-is("{title}"))'
    # Among those for the tape in the printer
    assert 'other' not in designer.get_attribute(sticker, 'class')
    designer.click(sticker)
    wait_for_preview(designer, '#libStage')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.label_size, S.edit.design.print_type, S.edit.design.image.name]') == \
        ['vc50', 'image_qrcode_text', 'labelburg.png']
    assert 'on' in designer.get_attribute('[data-lay="imagecode"]', 'class')
    assert designer.input_value('#edCode') == 'https://github.com/DL6ER/labelburg'
    assert 'Everything fits.' in fits_note(designer)
    # About 140 mm long at 320 dpi
    width, height = preview_size(designer)
    assert height == 617 and 135 < width / 320 * 25.4 < 145


def _light_drawing() -> bytes:
    image = Image.new('RGB', (300, 400), 'white')
    ImageDraw.Draw(image).ellipse((40, 40, 260, 360), fill=(240, 215, 190))
    out = io.BytesIO()
    image.save(out, format='PNG')
    return out.getvalue()


def test_stronger_contrast_for_light_drawings(designer):
    fake_vc500w(designer)
    use_tape(designer)
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[{'name': 'black.png', 'mimeType': 'image/png', 'buffer': png()}])
    wait_for_preview(designer)
    # A dark image prints as it is, nothing is suggested
    assert not designer.is_checked('#edContrast') and designer.is_hidden('#edContrastHint')
    designer.set_input_files('#edFile', files=[{'name': 'drawing.png', 'mimeType': 'image/png', 'buffer': _light_drawing()}])
    wait_for_preview(designer)
    assert designer.is_visible('#edContrastHint')
    designer.click('#edContrastOn')
    wait_for_preview(designer)
    assert designer.is_checked('#edContrast') and designer.is_hidden('#edContrastHint')
    assert designer.evaluate("() => formFor(S.edit.design, 1, true).get('image_contrast')") == '1'
    designer.uncheck('#edContrast')
    wait_for_preview(designer)
    assert designer.evaluate("() => S.edit.design.image_contrast") == '0' and designer.is_visible('#edContrastHint')
