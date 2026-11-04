"""Printing in the label designer: the printer panel, tapes, label sizes and
copies."""

import base64
import json

from tests.browser.helpers import fits_note, png, preview_size, printed, type_line, wait_for_preview


def fake_printer(page, **status):
    """Answer the status queries with a QL-800 that reports the given
    status, e.g. the loaded labels, and load the page again."""
    printer = {'errors': [], 'model': 'QL-800', 'path': 'simulation', 'media_width': None, 'media_length': 0, **status}
    body = json.dumps({**printer, 'printers': [printer], 'selected': 'simulation'})
    page.route('**/api/printer_status*', lambda route: route.fulfill(content_type='application/json', body=body))
    page.reload()
    wait_for_preview(page)


def test_ctrl_enter_prints(designer, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    type_line(designer, 0, 'Hello')
    designer.keyboard.press('Control+Enter')
    assert printed(designer) == ['printed']


def test_printer_failure_is_shown(designer, monkeypatch):
    # The server answers 503 for a print the printer did not do, the message
    # is shown as for a refused request
    monkeypatch.setattr('app.labeldesigner.printer.PrinterQueue.process_queue', lambda self, batch_size=0: 'The printer reports: Replace media error')
    type_line(designer, 0, 'Hello')
    designer.click('#edPrint')
    assert printed(designer) == ['failed']
    designer.wait_for_selector('#layer h2:has-text("Not printed")')
    assert 'The printer reports: Replace media error' in designer.inner_text('#layer')


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


def test_printer_panel_replaced_by_another_layer(designer):
    # Opened by its link, replaced by search, it does not come back with the
    # link. A setting changed in it shows once search is closed.
    designer.evaluate("() => { location.hash = '#printer'; }")
    designer.wait_for_selector('#layer:not([hidden]) .settings')
    designer.click('[data-q="fast"]')
    designer.evaluate("() => { document.querySelector('#view').firstElementChild.dataset.before = '1'; }")
    designer.keyboard.press('Control+k')
    designer.wait_for_selector('#layer:not([hidden]) #palRes')
    assert designer.evaluate('() => location.hash') == '#wizard'
    designer.keyboard.press('Escape')
    designer.wait_for_function("() => document.getElementById('layer').hidden")
    assert designer.evaluate("() => !document.querySelector('#view [data-before]')")


def test_view_is_not_drawn_anew_long_after_the_printer_panel(designer):
    # A setting changed in the printer panel, which search took the place of.
    # Search closed for a command, the view was drawn anew at a later close.
    designer.click('#printerChip')
    designer.wait_for_selector('#layer:not([hidden]) .settings')
    designer.click('[data-q="fast"]')
    designer.keyboard.press('Control+k')
    designer.wait_for_selector('#layer:not([hidden]) #palRes')
    # Drawn once right after what search closed for, so that the change shows
    designer.evaluate("""() => {
        window.routed = 0;
        const draw = route;
        route = () => { window.routed++; draw(); };
        closeLayer(true);
    }""")
    designer.wait_for_function('() => window.routed === 1')
    designer.evaluate("""() => {
        document.querySelector('#view').firstElementChild.dataset.before = '1';
        openLayer('<div class="pop">Another</div>', 'pop-layer');
        closeLayer();
    }""")
    designer.wait_for_timeout(200)
    assert designer.evaluate("() => !!document.querySelector('#view [data-before]') && window.routed === 1")


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
    assert 'switched off or not connected' in designer.inner_text(f'[data-printer="{other}"]')
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


def test_job_printed_again_keeps_its_image(designer):
    # Printed again, the ninth job moves to the front and the history keeps
    # its image no longer, the request still has it. Jobs whose image was
    # not kept are not printed again without it.
    bodies = []
    designer.route('**/api/print', lambda route: (bodies.append(route.request.post_data_buffer), route.continue_()))
    designer.evaluate("""data => {
        S.history = Array.from({ length: 11 }, (_, i) => ({ id: 'h' + i, time: Date.now() - i * 1000, title: 'Job ' + i, values: {}, copies: 1,
            source: null, printer: 'QL-800', state: 'printed', message: '',
            design: Object.assign(baseDesign('62'), { print_type: 'image', image: { data, mime: 'image/png', name: 'job.png' } }) }));
        saveHistory();
    }""", base64.b64encode(png()).decode())
    assert designer.evaluate('() => S.history.map(h => !!h.noImage)') == [False] * 9 + [True] * 2
    designer.evaluate('() => again(S.history[8])')
    printed(designer, 12)
    assert len(bodies) == 1 and b'filename="job.png"' in bodies[0]
    designer.evaluate('() => again(S.history[11])')
    designer.wait_for_timeout(300)
    assert len(bodies) == 1


def test_copies_stay_when_the_editor_is_drawn_again(designer):
    type_line(designer, 0, 'Jars')
    designer.fill('#edCopies', '2')
    designer.click('.ed-top [data-step="1"]')
    # Bold draws the editor again
    designer.click('[data-row="0"] [data-act="bold"]')
    wait_for_preview(designer)
    assert designer.input_value('#edCopies') == '3'
    designer.click('#edPrint')
    printed(designer)
    assert designer.evaluate('() => S.history[0].copies') == 3


def test_printer_that_is_not_connected_is_reported(designer):
    # The chosen printer was unplugged: the labels print on another one, not
    # without a word
    designer.evaluate("() => localStorage.setItem('designer_printer', JSON.stringify('file:///dev/usb/lp7'))")
    designer.reload()
    designer.wait_for_selector('#toast:has-text("/dev/usb/lp7 is not connected, labels print on the QL-800")')


def test_black_and_red_roll_is_not_a_mismatch(designer):
    # A QL-800 reports a black and red roll as 62 mm endless, without a color
    fake_printer(designer, media_width=62, media_length=0, media_type='Continuous length tape')
    designer.select_option('#edSize', '62red')
    wait_for_preview(designer)
    assert designer.locator('.tape-note.warn').count() == 0
    assert designer.evaluate("() => fitsTape('62red')")


def test_loaded_label_that_is_not_offered(designer):
    # The simulator reports no tape, the assumed one matches nothing
    assert designer.locator('.tape-note').count() == 0
    assert not designer.evaluate("() => [...document.querySelectorAll('#edSize option')].some(o => o.textContent.includes('in the printer'))")
    fake_printer(designer, media_width=40, media_length=0)
    assert designer.inner_text('.tape-note') == 'The QL-800 reports 40 mm tape, which is not offered here.'
    designer.click('#printerChip')
    assert 'The printer reports 40 mm tape, which is not offered here.' in designer.inner_text('.pp')


def test_tape_set_by_hand_is_known(designer):
    # A roll the designer does not offer, or one reported without its color,
    # says nothing about a tape set by hand
    designer.evaluate("() => { S.printer = { path: 'a', model: 'QL-800', media_width: 40, media_length: 0 }; detectTape(); }")
    assert designer.evaluate('() => S.tapeUnknown') == '40 mm'
    designer.evaluate("() => { S.settings.tapes = { a: '62' }; detectTape(); }")
    assert designer.evaluate('() => [S.tapeUnknown, S.tapeManual]') == ['', True]
    designer.evaluate("() => { S.settings.tapes = {}; S.printer.media_width = 62; detectTape(); }")
    assert designer.evaluate("() => fitsTape('62red')")
    designer.evaluate("() => { S.settings.tapes = { a: '62' }; detectTape(); }")
    assert not designer.evaluate("() => fitsTape('62red')")


def test_tape_question_is_settled_when_something_else_opens(designer):
    # Print waited for an answer forever when the search took its place
    result = designer.evaluate("""async () => {
        const job = printJob({ title: 'Box', design: Object.assign(baseDesign('29x90'), { text: [L('Box', 40)] }), values: {}, copies: 1 });
        await new Promise(done => setTimeout(done, 300));
        const asked = !!document.querySelector('#layer [data-r="anyway"]');
        openSearch();
        return [asked, await Promise.race([job, new Promise(done => setTimeout(() => done('waiting'), 1000))])];
    }""")
    assert result == [True, False]


def test_printer_panel_of_a_link_stays_closed(designer):
    designer.evaluate("() => { location.hash = '#printer'; }")
    designer.wait_for_selector('#layer:not([hidden]) .settings')
    designer.click('[data-q="fast"]')
    designer.keyboard.press('Escape')
    designer.wait_for_timeout(500)
    assert designer.evaluate("() => document.getElementById('layer').hidden")
