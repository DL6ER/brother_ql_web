"""Printing in the label designer: the printer panel, tapes, label sizes and
copies."""

import base64
import json
from urllib.parse import parse_qs

import pytest

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
    monkeypatch.setattr('app.labeldesigner.printer.PrinterQueue.process_queue', lambda self: 'The printer reports: Replace media error')
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
    assert designer.inner_text('.tape-note') == 'The Brother QL-800 has 29 x 90 mm loaded. Switch to it'
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
    assert designer.inner_text('#printerChip b') == 'Brother QL-1100'
    offered = designer.eval_on_selector_all('#edSize option', 'options => options.map(o => o.value)')
    assert '103x164' in offered and '62red' not in offered


def test_configured_printers_are_shown_by_their_names(designer, app, monkeypatch):
    other = 'file:///nonexistent/lp0'
    monkeypatch.setitem(app.config, 'PRINTERS', [('QL-800', 'simulation', 'Desk'), ('QL-1100', other, 'Shelf'), ('QL-700', 'file:///nonexistent/lp1')])
    designer.reload()
    wait_for_preview(designer)
    assert designer.inner_text('#printerChip b') == 'Desk'
    assert designer.inner_text('#edStage .printer .model') == 'Desk'
    designer.click('#printerChip')
    rows = designer.eval_on_selector_all('.plist [data-printer] .grow', 'rows => rows.map(r => [r.querySelector("b").textContent, r.querySelector(".sm").textContent])')
    # The model next to the name, a printer without one shows its model
    assert [name for name, _ in rows] == ['Desk', 'Shelf', 'Brother QL-700']
    assert rows[0][1].startswith('Brother QL-800, ') and rows[1][1].startswith('Brother QL-1100, ')
    assert not rows[2][1].startswith('Brother QL-700')
    designer.click(f'[data-printer="{other}"]')
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    assert designer.inner_text('#printerChip b') == 'Shelf'


@pytest.mark.parametrize('name, called', [('Kitchen', 'Kitchen'), ('', 'The Brother QL-800')])
def test_sentences_call_a_printer_by_its_name(designer, name, called):
    # "the" before a model, not before a name
    fake_printer(designer, name=name, media_width=29, media_length=90)
    designer.select_option('#edSize', '62')
    wait_for_preview(designer)
    assert designer.inner_text('.tape-note').startswith(f'{called} has 29 x 90 mm')


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
    assert 'The Brother QL-1100 cannot print at 600 dpi.' in designer.inner_text('.settings')
    # Nor can a P-touch printer
    designer.keyboard.press('Escape')
    fake_printer(designer, model='PT-P750W', media_width=24)
    designer.click('#printerChip')
    assert designer.is_disabled('[data-q="high"]')
    assert 'The Brother PT-P750W cannot print at 600 dpi.' in designer.inner_text('.settings')


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


def test_wide_ptouch_tape_is_shown_at_360_dpi(designer):
    designer.evaluate("""() => {
        S.printer = { path: 'a', model: 'PT-P900W', media_width: 36, media_length: 0 };
        detectTape();
        S.edit.design.label_size = S.tape;
        renderEditor();
    }""")
    assert designer.input_value('#edSize') == 'pt36'
    # 35 dots at 360 dpi
    assert designer.input_value('[data-margin="left"]') == '2.5'
    type_line(designer, 0, 'Hello')
    _, height = preview_size(designer)
    assert designer.inner_text('#edStage .dim-x') == '36 mm'
    assert designer.inner_text('#edStage .dim-y') == f'{round(height / 360 * 25.4)} mm'


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
    designer.wait_for_selector('#toast:has-text("/dev/usb/lp7 is not connected, labels print on the Brother QL-800")')


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
    assert designer.inner_text('.tape-note') == 'The Brother QL-800 reports 40 mm tape, which is not offered here.'
    designer.click('#printerChip')
    # The assumed tape is named, not called the default
    assert designer.inner_text('.pp #setTape + .muted') == 'The printer reports 40 mm tape, which is not offered here. ' + designer.evaluate('() => sizeName(S.tape)') + ' is assumed.'


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


def test_cutting_per_printer_and_per_label(designer):
    # Set in the printer panel for each printer, half cuts and chain printing
    # only for P-touch printers. A label can keep its own cutting.
    def sent():
        with designer.expect_request('**/api/print') as request:
            designer.click('#edPrint')
        # Without files a plain form
        form = parse_qs(request.value.post_data)
        return {k: form[k][0] for k in ('cut', 'half_cut', 'chain')}

    designer.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    designer.evaluate("() => { S.settings.cut = 'last'; }")
    type_line(designer, 0, 'Hello')
    # Printers without a setting of their own keep the one set before for all
    assert sent() == {'cut': 'last', 'half_cut': '1', 'chain': '0'}
    designer.evaluate("() => openPrinterPanel()")
    # Only P-touch printers half cut and chain print, shown off
    assert designer.locator('[data-c="half_cut"][data-v="0"].on:disabled, [data-c="chain"][data-v="0"].on:disabled').count() == 2
    # The 62 mm label is printed on it as set by hand: a PT-E550W does not
    # report its tape over the network, and tape it takes would be assumed
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { model: 'PT-E550W' });
        S.settings.tapes = Object.assign({}, S.settings.tapes, { [printerKey()]: '62' }); closeLayer(true); openPrinterPanel(); }""")
    # Not cut is chain printing on P-touch printers
    designer.click('[data-c="cut"][data-v="none"]')
    assert designer.locator('[data-c="chain"][data-v="1"].on:disabled').count() == 1
    assert designer.get_attribute('.pp .cut-strip', 'title') == ('Half cut between the labels, not cut. The last label stays in the printer, the next print cuts it off. '
                                                                 'The tape fed before the first label is waste, short labels get some extra tape.')
    designer.click('[data-c="cut"][data-v="last"]')
    designer.click('[data-c="chain"][data-v="1"]')
    designer.keyboard.press('Escape')
    assert sent() == {'cut': 'last', 'half_cut': '1', 'chain': '1'}
    # Saved with the label, it is cut its own way
    designer.select_option('#edCut', 'each')
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.cut, S.edit.design.chain]') == ['each', '1']
    assert sent() == {'cut': 'each', 'half_cut': '1', 'chain': '1'}
    designer.select_option('#edCut', '')
    wait_for_preview(designer)
    assert sent()['cut'] == 'last'


def test_no_half_cut_on_a_ptouch_printer_without_a_half_cutter(designer):
    # The PT-P700 cuts and chain prints, but has no half cutter: half cuts
    # are shown off and disabled with the reason, also when they are on
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'PT-P700' }); S.settings.cut = 'last'; openPrinterPanel(); }")
    half = designer.locator('.pp [data-c="half_cut"][data-v="0"]')
    assert half.get_attribute('class') == 'on' and half.is_disabled()
    assert designer.get_attribute('.pp div:has(> .seg [data-c="half_cut"])', 'title') == 'This printer has no half cutter'
    assert designer.is_enabled('.pp [data-c="chain"][data-v="1"]')
    assert designer.get_attribute('.pp .cut-strip', 'title').startswith('Cut after the last label.')


def test_look_for_printers_looks_also_for_those_that_did_not_answer(designer):
    # The status every 20 s leaves Bluetooth printers that did not answer alone
    with designer.expect_request('**/api/printer_status*') as request:
        designer.evaluate('() => refreshPrinters()')
    assert 'look=1' not in request.value.url
    designer.evaluate('() => openPrinterPanel()')
    with designer.expect_request('**/api/printer_status*') as request:
        designer.click('#ppRefresh')
    assert 'look=1' in request.value.url


def test_printer_with_a_low_battery_asks_to_charge_it(designer):
    hint = 'The battery is low, charge the printer.'
    for low in (True, False):
        designer.evaluate(f"""() => {{ S.printers = [Object.assign({{}}, S.printer, {{ status_type: 'Reply to status request', battery_low: {str(low).lower()} }})]; S.printer = S.printers[0]; openPrinterPanel(); }}""")
        assert (hint in designer.inner_text('.plist')) is low
        assert designer.evaluate('() => printerState(S.printer).text') == 'Ready'
        designer.evaluate('() => closeLayer(true)')


def test_printer_still_being_asked_is_not_shown_as_failing(designer):
    # A printer on Bluetooth takes a few seconds to answer, it was red meanwhile
    assert designer.evaluate("() => printerState({ status_type: 'Asking the printer', errors: [] })") == {'cls': 'off', 'text': 'Asking the printer…'}


def test_labels_of_a_q00_leave_it_turned(designer):
    # Their top comes out first
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { model: 'PUQU/Q00' }); S.tape = 'puqu50x30';
        openEditor({ title: 'Q', design: Object.assign(baseDesign('puqu50x30'), { text: [L('Q', 40)] }) }); }""")
    wait_for_preview(designer)
    assert 'turned' in designer.get_attribute('#edStage', 'class').split()
    # The printer stands below the label, which leaves it upwards
    assert designer.evaluate("() => getComputedStyle(document.querySelector('#edStage')).flexDirection") == 'column-reverse'
    assert designer.locator('#edStage .flow .fa-arrow-up-long').count() == 1
    # Labels that leave it sideways have the printer to their left
    designer.evaluate("() => { S.tape = 'puqu50x80'; openEditor({ title: 'Q', design: Object.assign(baseDesign('puqu50x80'), { text: [L('Q', 40)] }) }); }")
    wait_for_preview(designer)
    classes = designer.get_attribute('#edStage', 'class').split()
    assert 'side' in classes and 'turned' not in classes
    # As those of a QL or P-touch printer, their top first, unlike the VC-500W
    for model, tape, turned in (('QL-700', '62', True), ('PT-P700', 'pt12', True), ('VC-500W', 'vc25', False)):
        designer.evaluate(f"() => {{ S.printer = Object.assign({{}}, S.printer, {{ model: '{model}' }}); S.tape = '{tape}'; openEditor({{ title: 'Q', design: Object.assign(baseDesign('{tape}'), {{ text: [L('Q', 40)] }}) }}); }}")
        wait_for_preview(designer)
        assert ('turned' in designer.get_attribute('#edStage', 'class').split()) is turned, model


def test_labels_can_leave_the_printer_bottom_first(designer):
    designer.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    q00 = """tape => { S.printer = Object.assign({}, S.printer, { model: 'PUQU/Q00' }); S.tape = tape;
        openEditor({ title: 'Q', design: Object.assign(baseDesign(tape), { text: [L('Q', 40)] }) }); }"""
    designer.evaluate(q00, 'puqu50x30')
    wait_for_preview(designer)
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    assert b'leaves_first=top' in request.value.post_data_buffer
    designer.evaluate("() => openPrinterPanel()")
    designer.click('.pp [data-lf="bottom"]')
    assert designer.locator('.pp [data-lf="bottom"].on').count() == 1
    # The test label names its ends, printed as set
    with designer.expect_request('**/api/print') as request:
        designer.click('.pp #setLfTest')
    body = request.value.post_data_buffer
    assert b'leaves_first=bottom' in body and b'TOP' in body and b'BOTTOM' in body
    designer.keyboard.press('Escape')
    wait_for_preview(designer)
    # The printer stands above the label, which leaves it downwards
    assert 'turned' not in designer.get_attribute('#edStage', 'class').split()
    assert designer.locator('#edStage .flow .fa-arrow-down-long').count() == 1
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    assert b'leaves_first=bottom' in request.value.post_data_buffer
    # Labels that leave it sideways have the printer to their right
    designer.evaluate(q00, 'puqu50x80')
    wait_for_preview(designer)
    assert {'side', 'back'} <= set(designer.get_attribute('#edStage', 'class').split())
    assert designer.evaluate("() => getComputedStyle(document.querySelector('#edStage')).flexDirection") == 'row-reverse'
    assert designer.locator('#edStage .flow .fa-arrow-left-long').count() == 1


def test_receipt_shows_the_blank_paper_before_it(designer):
    # A TM-T20II feeds 12 mm to its cutter after a label, blank before the next
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { model: 'Epson/TM-T20II' }); S.tape = 'escpos80';
        openEditor({ title: 'R', design: Object.assign(baseDesign('escpos80'), { text: [L('R', 40)] }) }); }""")
    wait_for_preview(designer)
    assert 'turned' in designer.get_attribute('#edStage', 'class').split()
    assert designer.locator('#edStage .paper .lead').count() == 1
    shown = int(designer.text_content('#edStage .dim-y').split()[0])
    img = designer.evaluate("() => document.querySelector('#edStage .paper img').naturalHeight")
    assert shown == round(img / 203 * 25.4) + 12
    # Also at the right end of a label that leaves sideways, which leaves first
    designer.evaluate("""() => openEditor({ title: 'R', design: Object.assign(baseDesign('escpos80'), { orientation: 'rotated', text: [L('Receipt', 40)] }) })""")
    wait_for_preview(designer)
    assert 'side' in designer.get_attribute('#edStage', 'class').split()
    assert designer.locator('#edStage .paper img + .lead').count() == 1
    shown = int(designer.text_content('#edStage .dim-x').split()[0])
    img = designer.evaluate("() => document.querySelector('#edStage .paper img').naturalWidth")
    assert shown == round(img / 203 * 25.4) + 12
    # Drawn 12 mm long, at the scale of the label
    drawn = designer.evaluate("""() => { const r = s => document.querySelector('#edStage .paper ' + s).getBoundingClientRect().width;
        return r('.lead') / r('img') * document.querySelector('#edStage .paper img').naturalWidth; }""")
    assert abs(drawn - 12 / 25.4 * 203) < 3


def test_cutting_is_disabled_on_a_printer_without_a_cutter(designer):
    # The labels of a PUQU Q00 are torn off, cutting after each or the last
    # label was offered all the same
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'PUQU/Q00' }); S.settings.cut = 'each'; openPrinterPanel(); }")
    cuts = designer.locator('.pp [data-c="cut"]')
    assert cuts.count() == 3 and all(cuts.nth(i).is_disabled() for i in range(3))
    assert designer.locator('.pp [data-c="cut"].on').count() == 0
    assert designer.get_attribute('.pp div:has(> .seg [data-c="cut"])', 'title') == 'This printer has no cutter'
    assert designer.get_attribute('.pp .cut-strip', 'title') == 'No cutter, the labels are torn off.'
    # Labels saved with their own cutting are torn off as well
    assert designer.inner_text('.pp .cut-show .muted') == 'No cutter, the labels are torn off.'
    # Not cut is what is sent, also for a label saved with its own cutting
    assert designer.evaluate("() => [printerCut().cut, cutOf({ cut: 'each', label_size: 'puqu50x30' }).cut]") == ['none', 'none']
    assert 'disabled title="This printer has no cutter"' in designer.evaluate("() => cutField({ cut: '', label_size: 'puqu50x30' })")
    # It prints in one quality, Fast did nothing
    designer.evaluate("() => { S.settings.quality = 'fast'; closeLayer(true); openPrinterPanel(); }")
    fast, quality = designer.locator('.pp [data-q="fast"]'), designer.locator('.pp [data-q="quality"]')
    assert fast.is_disabled() and quality.is_disabled() and 'on' in quality.get_attribute('class').split()
    assert quality.get_attribute('title') == 'This printer prints in one quality only'


def test_ql500_labels_are_cut_by_hand(designer):
    # The QL-500 has a lever instead of a cutter, cutting was offered
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'QL-500' }); S.settings.cut = 'each'; openPrinterPanel(); }")
    cuts = designer.locator('.pp [data-c="cut"]')
    assert cuts.count() == 3 and all(cuts.nth(i).is_disabled() for i in range(3))
    assert designer.get_attribute('.pp div:has(> .seg [data-c="cut"])', 'title') == 'This printer has no automatic cutter'
    assert designer.inner_text('.pp .cut-show .muted') == 'No automatic cutter, cut the labels off with the lever on the printer.'
    assert designer.evaluate("() => [printerCut().cut, cutOf({ cut: 'each', label_size: '62' }).cut]") == ['none', 'none']
    assert 'disabled title="This printer has no automatic cutter"' in designer.evaluate("() => cutField({ cut: '', label_size: '62' })")
    # Next to the preview
    designer.evaluate("() => { closeLayer(true); S.tape = '62'; openEditor({ title: 'Q', design: Object.assign(baseDesign('62'), { text: [L('Q', 40)] }) }); }")
    wait_for_preview(designer)
    assert designer.inner_text('#edStage .cuts > span:not(.cut-strip)') == 'Cut by hand'
    # Labels for it and other QL printers are cut as these cut them
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'PT-E550W' }); }")
    assert designer.evaluate("() => noCutter(sizeCaps('62'))") is False


def test_assumed_tape_is_one_the_printer_takes(designer):
    # A PUQU Q00 does not report its labels, 62 mm endless tape was assumed
    designer.evaluate("() => { S.settings.tapes = {}; S.printer = Object.assign({}, S.printer, { model: 'PUQU/Q00', media_width: null, media_length: 0 }); detectTape(); }")
    assert designer.evaluate('() => [S.tape, S.tapeAssumed]') == ['puqu50x30', True]
    # The printer panel names it, "the default is assumed" was 62 mm endless
    designer.evaluate('() => openPrinterPanel()')
    assert designer.inner_text('.pp #setTape + .muted') == 'The printer does not report its tape, 50 x 30 mm is assumed.'
    designer.evaluate('() => closeLayer(true)')
    designer.evaluate("() => { S.printer = Object.assign({}, S.printer, { model: 'QL-700', media_width: null }); detectTape(); }")
    assert designer.evaluate('() => S.tape') == '62'


def test_printer_of_another_driver_gets_what_it_offers(designer):
    # The label designer knows printers only by what their driver tells
    designer.evaluate("""() => {
        C.models['Other-1'] = { qualities: ['fast', 'quality'], high_res: false, half_cut: false, chain: false,
                                feed_waste: false, job_stages: false, colors: 'black', never_cut: 'It keeps them.' };
        SIZES['o48'] = Object.assign({}, SIZES['62'], { id: 'o48', models: ['Other-1'], dpi: [203, 203] });
        S.printer = Object.assign({}, S.printer, { model: 'Other-1' }); S.settings.quality = 'high'; S.settings.cut = 'last';
        openPrinterPanel(); }""")
    assert designer.eval_on_selector_all('.pp [data-q]', 'buttons => buttons.map(b => b.textContent)') == ['Fast', 'Quality']
    assert designer.inner_text('.pp [data-q].on') == 'Quality'
    assert designer.evaluate("() => [highRes(), dpiOf('o48'), dpiOf('o48', true)]") == [False, 203, 203]
    assert designer.get_attribute('.pp div:has(> .seg [data-c="half_cut"])', 'title') == 'This printer has no half cutter'
    assert designer.get_attribute('.pp div:has(> .seg [data-c="chain"])', 'title') == 'Only for P-touch printers'
    assert designer.get_attribute('.pp .cut-strip', 'title') == 'Cut after the last label.'
    never = designer.locator('.pp [data-c="cut"][data-v="none"]')
    assert never.is_disabled() and never.get_attribute('title') == 'It keeps them.'
    # A driver that names no maker: the model alone
    assert designer.evaluate("() => printerName(S.printer)") == 'Other-1'


def test_printers_are_named_with_their_maker(designer):
    designer.click('#printerChip')
    designer.wait_for_selector('.plist')
    assert 'Brother QL-800' in designer.inner_text('.plist')
    assert designer.inner_text('#printerChip b') == 'Brother QL-800'
    # Phones show the model only
    designer.set_viewport_size({'width': 390, 'height': 800})
    assert designer.inner_text('#printerChip b') == 'QL-800'
    # Also a printer of another maker
    assert designer.evaluate("() => printerName({ model: 'PUQU/Q00' })") == 'PUQU Q00'


def test_cutting_saved_through_the_api(designer, server):
    # half_cut 0 was shown and printed as on, chain 1 was sent but not shown
    designer.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'name': 'Own.json', 'title': 'Own', 'label_size': 'pt12', 'text': [{'text': 'Own', 'size': '40'}],
                                           'cut': 'last', 'half_cut': 0, 'chain': True}))
    designer.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    designer.evaluate("""async () => {
        S.printer = Object.assign({}, S.printer, { model: 'PT-E550W' });
        S.tape = 'pt12';
        openEditor({ title: 'Own', design: await itemDesign(savedItem({ name: 'Own.json', title: 'Own' })) });
    }""")
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.cut, S.edit.design.half_cut, S.edit.design.chain]') == ['last', '0', '1']
    assert designer.locator('[data-lcut="half_cut"][data-v="0"].on, [data-lcut="chain"][data-v="1"].on').count() == 2
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    form = parse_qs(request.value.post_data)
    assert {k: form[k][0] for k in ('cut', 'half_cut', 'chain')} == {'cut': 'last', 'half_cut': '0', 'chain': '1'}


def test_cutting_does_not_render_the_preview_again(designer):
    # The fields went into the preview request and its cache key
    type_line(designer, 0, 'Hello')
    asked = []
    designer.on('request', lambda r: asked.append(r) if '/api/preview' in r.url else None)
    designer.select_option('#edCut', 'none')
    wait_for_preview(designer)
    assert asked == []


def test_printer_on_the_stage_follows_its_status(designer):
    # It kept the status of the last preview, red while the printer list was green
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { status_type: 'Reply to status request', errors: ['The cover of the printer is open'] }); renderPrinterChip(); }""")
    assert designer.get_attribute('#edStage .printer .led', 'class') == 'led bad'
    designer.evaluate("""() => { S.printer = Object.assign({}, S.printer, { errors: [] }); renderPrinterChip(); }""")
    assert designer.get_attribute('#edStage .printer .led', 'class') == 'led '
    assert designer.get_attribute('#edStage .printer .led', 'title') == 'Ready'


def test_open_printer_panel_follows_the_status(designer):
    # It showed the status of when it was opened until "Look for printers"
    printer = {'path': 'simulation', 'model': 'QL-800', 'status_type': 'Reply to status request', 'errors': []}
    designer.route('**/api/printer_status*', lambda route: route.fulfill(json={'printers': [printer]}))
    designer.evaluate("() => refreshPrinters()")
    designer.evaluate("() => openPrinterPanel()")
    designer.focus('.pp .prow')
    designer.evaluate("() => refreshPrinters()")
    assert designer.evaluate("() => document.activeElement.matches('.pp .prow')")
    printer.update(errors=['The cover of the printer is open'], media_width=29, media_length=90)
    designer.evaluate("() => refreshPrinters()")
    assert 'The cover of the printer is open' in designer.inner_text('.pp .plist')
    assert designer.locator('.pp .plist .dot.bad').count() == 1
    assert designer.evaluate("() => $('#setTape').selectedOptions[0].textContent") == 'Automatic: 29 x 90 mm'


def test_tape_note_follows_the_tape_the_printer_reports(designer):
    # It still offered to switch to the tape assumed before the roll was read
    printer = {'path': 'simulation', 'model': 'QL-800', 'status_type': 'Reply to status request', 'errors': [], 'media_width': 62, 'media_length': 0}
    designer.route('**/api/printer_status*', lambda route: route.fulfill(json={'printers': [printer]}))
    in_printer = "() => $$('#edSize option').filter(o => o.textContent.endsWith('(in the printer)')).map(o => o.value)"
    designer.evaluate("() => refreshPrinters()")
    assert 'Matches the tape in the printer' in designer.inner_text('.tape-note')
    assert designer.locator('#edUseTape').count() == 0
    printer.update(media_width=29, media_length=90)
    designer.evaluate("() => refreshPrinters()")
    assert 'has 29 x 90 mm' in designer.inner_text('.tape-note')
    assert designer.evaluate(in_printer) == ['29x90']
    designer.focus('#edUseTape')
    designer.evaluate("() => refreshPrinters()")
    assert designer.evaluate("() => document.activeElement.id") == 'edUseTape'
    designer.click('#edUseTape')
    wait_for_preview(designer)
    assert designer.input_value('#edSize') == '29x90'


def test_sheet_after_printing_on_a_phone_names_the_printer_with_its_maker(designer):
    # It showed "on the PUQU/Q00"
    designer.set_viewport_size({'width': 700, 'height': 900})
    designer.evaluate("() => announce({ state: 'printed', copies: 1, title: 'Q', values: {}, design: baseDesign('puqu50x30'), printer: 'PUQU/Q00' })")
    assert 'Q on the PUQU Q00' in designer.inner_text('body')
