"""The Brother VC-500W in the label designer: its color tape, colors, print
modes, the tape left and what it does while it prints."""

import json

from tests.browser.helpers import fits_note, printed, type_line, wait_for_preview

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


def test_tape_left_in_the_printer_panel(designer):
    fake_vc500w(designer)
    designer.click('#printerChip')
    designer.wait_for_selector('.plist')
    assert '4.6 m of 5.0 m left' in designer.inner_text('.plist')
    assert 'answers' not in designer.inner_text('.plist')


def test_connection_in_the_printer_panel(designer):
    fake_vc500w(designer, answers=20, answers_lost=3, data_rate=452300)
    designer.click('#printerChip')
    designer.wait_for_selector('.plist')
    assert '4.6 m of 5.0 m left, last label sent at 452 kB/s, 3 of the last 20 answers lost' in designer.inner_text('.plist')


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
