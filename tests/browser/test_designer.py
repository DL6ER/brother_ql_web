"""The label designer in a browser: the editor, the wizard and what they
share. The API itself is tested without a browser."""
import base64
import io
import json
import os
from urllib.parse import parse_qs

import pytest
from PIL import Image

from browser_helpers import wait_for_preview


def type_line(page, row, text):
    page.fill(f'[data-row="{row}"] .tx', text)
    wait_for_preview(page)


def kept(page):
    """Wait for the pause after a change: it is a step to undo then, and
    kept for the next visit."""
    page.wait_for_function('() => S.hist.last === snapshot(S.edit)')


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
    # Only with text
    assert designer.is_disabled('[data-side="left"]')
    designer.fill('#edCode', 'https://example.com')
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


def test_a_label_without_a_code_is_not_drawn_twice_for_its_first_preview(designer):
    # The side of its code, none, was taken for news: the editor was drawn
    # anew and the preview handled once more
    designer.evaluate("""() => {
        window.drawn = 0;
        const draw = renderEditor;
        renderEditor = () => { window.drawn++; draw(); };
        setEdit(newEdit());
        S.edit.design.text = [L('Pantry', 60)];
        renderEditor();
    }""")
    wait_for_preview(designer)
    designer.wait_for_timeout(200)
    assert designer.evaluate('() => window.drawn') == 1


def test_a_preview_that_waited_in_line_lets_its_job_go_once_done(designer):
    # Its promise, kept for others that ask for the same, held the job and
    # what it was made for, e.g. a design, as long as it was kept
    designer.evaluate("""() => new Promise(done => {
        let releaseAll;
        const release = new Promise(open => { releaseAll = open; });
        const blockers = Array.from({ length: PARALLEL }, () => limit(() => release));
        window.kept = (() => {
            const own = { lines: new Array(10000).fill('Pantry') };
            window.ref = new WeakRef(own);
            return limit(async () => own.lines.length);
        })();
        releaseAll();
        Promise.all([window.kept, ...blockers]).then(() => done());
    })""")
    cdp = designer.context.new_cdp_session(designer)
    for _ in range(3):
        cdp.send('HeapProfiler.collectGarbage')
    assert designer.evaluate('() => window.ref.deref() === undefined')


def test_a_preview_that_takes_too_long_does_not_hold_back_printing(designer):
    # Taken for one the server refused, it kept Print off, also where the
    # server prints the label, e.g. a large photo on a slow host
    type_line(designer, 0, 'Pantry')
    held = []
    designer.evaluate('() => { previewTimeout = 300; }')
    designer.route('**/api/preview', lambda route: held.append(route))
    designer.fill('[data-row="0"] .tx', 'Pantry shelf')
    designer.wait_for_selector('#edStage .err')
    assert 'The preview took too long' in designer.inner_text('#edStage .err')
    assert not designer.is_disabled('#edPrint')
    for route in held:
        try:
            route.abort()
        except Exception:
            # Given up by the page already
            pass
    designer.unroute_all(behavior='ignoreErrors')
    designer.evaluate('() => { previewTimeout = 60000; }')
    # One the server refuses, it cannot print either
    designer.click('[data-lay="code"]')
    designer.select_option('#edBarcode', 'EAN13')
    designer.fill('#edCode', '12')
    wait_for_preview(designer)
    assert designer.is_disabled('#edPrint')


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


def _next_day(designer):
    """Dates from here on are those of the next day."""
    designer.evaluate("""() => {
        const Real = Date;
        window.Date = class extends Real {
            constructor(...args) { if (args.length) super(...args); else super(Real.now() + 86400000); }
            static now() { return Real.now() + 86400000; }
        };
    }""")


@pytest.mark.parametrize('opened', ['detail', 'link'])
def test_a_date_at_today_follows_the_day_where_a_saved_label_is_printed(designer, server, opened):
    # The wizard and the link to a label printed the date of the day they
    # were opened, also the next morning
    designer.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'name': 'Frozen.json', 'title': 'Frozen', 'label_size': '62',
                                           'text': [{'text': 'Frozen {{blank:Frozen on}}', 'size': '40'}], 'fields': {'Frozen on': {'type': 'date'}}}))
    try:
        if opened == 'detail':
            designer.click('[data-tab="wizard"]')
            designer.wait_for_selector('#libDetail', state='attached')
            designer.evaluate("async () => { await loadSaved(); showDetail(savedItem(S.saved.find(x => x.name === 'Frozen.json'))); }")
        else:
            designer.evaluate("async () => { await loadSaved(); location.hash = 'fill/Frozen.json'; }")
        designer.wait_for_selector('#libFields [data-blank="Frozen on"]')
        designer.wait_for_selector('#libPrint')
        _next_day(designer)
        designer.evaluate('() => { window.printJob = job => { window.job = job; return true; }; }')
        designer.click('#libPrint')
        designer.wait_for_function('() => window.job')
        assert designer.evaluate("() => job.values['Frozen on'] === today()")
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Frozen.json'})


def test_an_earlier_preview_does_not_tell_whether_a_newer_label_prints(designer):
    # The preview of the code before arrives while the newer one waits:
    # it must not decide whether the label as it is now can be printed
    designer.click('[data-lay="code"]')
    designer.select_option('#edBarcode', 'EAN13')
    designer.fill('#edCode', '12')
    wait_for_preview(designer)
    assert designer.is_disabled('#edPrint')
    held = []
    designer.route('**/api/preview', lambda route: held.append(route))
    designer.fill('#edCode', '123456789012')
    while not held:
        designer.wait_for_timeout(50)
    designer.fill('#edCode', '13')
    held.pop(0).continue_()
    while not held:
        designer.wait_for_timeout(50)
    assert designer.is_disabled('#edPrint')
    held.pop(0).continue_()
    designer.unroute('**/api/preview')
    wait_for_preview(designer)
    assert designer.is_disabled('#edPrint')


def test_values_of_blanks_saved_through_the_api_stay_with_the_label(designer, server, app):
    # Printed through the API they were filled in, the label designer showed
    # the blanks empty and dropped them when the label was saved again. One
    # left empty on purpose stays empty, a date as well.
    designer.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'name': 'Room.json', 'title': 'Room', 'label_size': '62',
                                           'text': [{'text': 'Shelf {{blank:Room}} {{blank:Frozen on}}', 'size': '40'}],
                                           'blanks': {'Room': 'Garage', 'Frozen on': ''}, 'fields': {'Frozen on': {'type': 'date'}}}))
    try:
        values = designer.evaluate("async () => { await loadSaved(); const item = { kind: 'saved', id: 'Room.json' }; await itemDesign(item); return defaults(item); }")
        assert values == {'Room': 'Garage', 'Frozen on': ''}
        designer.evaluate("async () => { await saveLabel('Room', await itemDesign({ kind: 'saved', id: 'Room.json' }), 'Room.json'); }")
        with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Room.json'), encoding='utf-8') as f:
            assert json.load(f)['blanks'] == {'Room': 'Garage', 'Frozen on': ''}
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Room.json'})


def test_values_of_blanks_saved_through_the_api_are_shown_and_replaced_by_settings(designer, server, app):
    # They won over a default set under Blank settings, which showed none,
    # and were saved again as they were
    designer.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'name': 'Box.json', 'title': 'Box', 'label_size': '62',
                                           'text': [{'text': 'Box {{blank:Box}} {{blank:Frozen on}}', 'size': '40'}],
                                           'blanks': {'Box': '3', 'Frozen on': '2026-01-02'}, 'fields': {'Frozen on': {'type': 'date'}}}))
    try:
        designer.evaluate("""async () => {
            await loadSaved();
            const item = { kind: 'saved', id: 'Box.json' };
            openEditor({ title: 'Box', design: await itemDesign(item), origin: item });
        }""")
        field = '#edFields input[data-blank="Box"]'
        designer.wait_for_selector(field)
        assert designer.input_value(field) == '3'
        designer.click('#blankSet')
        default = '[data-bname="Box"] [data-default]'
        assert designer.input_value(default) == '3'
        assert 'Starts at 2026-01-02' in designer.inner_text('[data-bname="Frozen on"]')
        designer.fill(default, '5')
        designer.dispatch_event(default, 'change')
        designer.wait_for_function("() => !('Box' in (S.edit.design.blanks || {})) && document.querySelector('[data-bname=\"Box\"] [data-default]').value === '5'")
        designer.click('[data-bname="Frozen on"] [data-today]')
        designer.wait_for_function("() => document.querySelector('[data-bname=\"Frozen on\"]').innerText.endsWith('Starts at today')")
        designer.click('#blankSet')
        designer.wait_for_selector(field)
        assert designer.input_value(field) == '5'
        assert designer.evaluate("() => [S.edit.values['Frozen on'] === today(), S.edit.design.blanks]") == [True, None]
        designer.evaluate("async () => { await saveLabel('Box', S.edit.design, 'Box.json'); }")
        with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Box.json'), encoding='utf-8') as f:
            saved = json.load(f)
        assert 'blanks' not in saved and saved['fields']['Box']['default'] == '5'
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Box.json'})


def test_a_date_saved_with_a_default_through_the_api_starts_at_it(designer, server, app):
    # The designer started it at today, printed through the API it was the
    # default, and Start at today kept the default
    designer.request.post(server + '/labeldesigner/api/repository/save', headers={'Content-Type': 'application/json'},
                          data=json.dumps({'name': 'Due.json', 'title': 'Due', 'label_size': '62',
                                           'text': [{'text': 'Due {{blank:Due}}', 'size': '40'}],
                                           'fields': {'Due': {'type': 'date', 'default': '2026-12-24'}}}))
    try:
        designer.evaluate("""async () => {
            await loadSaved();
            const item = { kind: 'saved', id: 'Due.json' };
            openEditor({ title: 'Due', design: await itemDesign(item), origin: item });
        }""")
        designer.wait_for_selector('#edFields [data-blank="Due"]')
        assert designer.evaluate("() => S.edit.values.Due") == '2026-12-24'
        designer.click('#blankSet')
        assert 'Starts at 2026-12-24' in designer.inner_text('[data-bname="Due"]')
        designer.click('[data-bname="Due"] [data-today]')
        designer.wait_for_function("() => document.querySelector('[data-bname=\"Due\"]').innerText.endsWith('Starts at today')")
        assert designer.evaluate("() => [S.edit.values.Due === today(), 'default' in S.edit.design.fields.Due]") == [True, False]
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Due.json'})


def test_flags_of_a_saved_label_are_read_as_the_server_reads_them(designer, app):
    # true and false as text were read as off: a label printed fitted or
    # shrunk through the API opened scaled and as set, and was saved so. One
    # the server refuses was read as As set and saved so, while printing it
    # through the API told it was refused: it stays, the preview tells why.
    repo = app.config['LABEL_REPOSITORY_DIR']
    labels = {
        'Flags1.json': {'image_fit': 'true', 'image_crop': 'true', 'shrink_text': 'true'},
        'Flags2.json': {'image_fit': 'false', 'image_crop': 'false', 'shrink_text': 'FALSE'},
        'Flags3.json': {'image_fit': ' 2 ', 'image_crop': 1, 'shrink_text': '-1'},
        'Flags4.json': {'image_fit': '', 'image_crop': None, 'shrink_text': ''},
        'Flags5.json': {'image_fit': True, 'image_crop': False, 'shrink_text': True},
        'Flags6.json': {'text_fit': 'Shrink'},
        'Flags7.json': {'shrink_text': 'yes'},
    }
    for name, flags in labels.items():
        with open(os.path.join(repo, name), 'w', encoding='utf-8') as f:
            json.dump(dict({'title': name, 'label_size': '62', 'text': [{'text': 'Shelf', 'size': '40'}]}, **flags), f)
    try:
        read = designer.evaluate("""async names => {
            await loadSaved();
            return Promise.all(names.map(async id => {
                const d = await itemDesign({ kind: 'saved', id });
                const told = await preview(d, {}).then(() => '', e => e.message);
                return [d.image_fit, d.image_crop, d.text_fit, d.shrink_text === undefined ? null : d.shrink_text, told];
            }));
        }""", list(labels))
        assert read == [['1', '1', 'shrink', None, ''], ['0', '0', 'none', None, ''], ['1', '1', 'shrink', None, ''], ['1', '0', 'none', None, ''],
                        ['1', '0', 'shrink', None, ''], ['1', '0', 'Shrink', None, 'text_fit must be none, shrink or fill'],
                        ['1', '0', '', 'yes', 'shrink_text must be 0 or 1']]
    finally:
        for name in labels:
            os.remove(os.path.join(repo, name))


def test_a_date_at_today_follows_the_day(designer):
    # The date it started at was kept as if typed: printed past midnight, or
    # opened again the next day, the label had the day before
    type_line(designer, 0, 'Frozen [Frozen on]')
    designer.wait_for_selector('#edFields input[data-blank="Frozen on"]')
    assert designer.evaluate("() => S.edit.values['Frozen on'] === today()")
    designer.evaluate("""() => {
        S.edit.values['Frozen on'] = S.edit.blankDefs['Frozen on'] = '2026-01-01';
        storeEdit();
        window.printJob = job => { window.job = job; };
    }""")
    designer.click('#edPrint')
    assert designer.evaluate("() => job.values['Frozen on'] === today()")
    designer.reload()
    designer.wait_for_selector('#edFields input[data-blank="Frozen on"]')
    assert designer.evaluate("() => S.edit.values['Frozen on'] === today()")
    # One typed stays
    designer.fill('#edFields input[data-blank="Frozen on"]', '2026-02-03')
    kept(designer)
    designer.reload()
    designer.wait_for_selector('#edFields input[data-blank="Frozen on"]')
    assert designer.evaluate("() => S.edit.values['Frozen on']") == '2026-02-03'


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


def test_a_preview_made_before_is_found_without_a_copy_of_the_label(designer):
    # Each call made a copy of the label before it looked for one made
    wait_for_preview(designer)
    copies = designer.evaluate("""async () => {
        await preview(S.edit.design, S.edit.values);
        const parse = JSON.parse;
        let n = 0;
        JSON.parse = function (...args) { n++; return parse.apply(this, args); };
        try {
            await preview(S.edit.design, S.edit.values);
        } finally {
            JSON.parse = parse;
        }
        return n;
    }""")
    assert copies == 0


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


def test_a_preview_that_does_not_come_gives_way_to_the_next(designer):
    # Without a time limit one that never came held back all later ones
    type_line(designer, 0, 'Pantry')
    designer.evaluate('() => { previewTimeout = 500; }')
    held = []
    designer.route('**/api/preview', lambda route: held.append(route) if not held else route.continue_())
    designer.fill('[data-row="0"] .tx', 'Pantry shelf')
    designer.wait_for_function("() => (document.querySelector('#edStage .err') || {}).textContent === ' The preview took too long'")
    designer.fill('[data-row="0"] .tx', 'Pantry shelf 2')
    designer.wait_for_function("() => !document.querySelector('#edStage .err') && document.querySelector('#edStage .paper img')")
    # The one held is let go, its request ended by the time limit
    held[0].abort()
    designer.unroute_all(behavior='ignoreErrors')


def test_a_preview_whose_image_comes_too_late_says_so(designer):
    # The time limit is on the image as well: its error came as it was
    type_line(designer, 0, 'Pantry')
    designer.evaluate("""() => {
        Response.prototype.blob = function () { return Promise.reject(new DOMException('signal timed out', 'TimeoutError')); };
    }""")
    designer.fill('[data-row="0"] .tx', 'Pantry shelf')
    designer.wait_for_function("() => (document.querySelector('#edStage .err') || {}).textContent === ' The preview took too long'")


def test_previews_work_in_browsers_without_a_time_limit_of_their_own(designer):
    # AbortSignal.timeout is missing in Safari before 16: every preview
    # failed, and Print stayed off
    designer.evaluate('() => { AbortSignal.timeout = undefined; }')
    type_line(designer, 0, 'Pantry')
    assert 'Everything fits.' in fits_note(designer)
    assert designer.is_enabled('#edPrint')


def test_a_date_put_in_before_the_same_one_is_the_new_one(designer):
    # Put in before a date shown the same, the new one was taken for the
    # one there was and the formats changed places
    fmt = designer.evaluate('() => dateFormat()')
    assert designer.evaluate("() => fromDisplay('[today][today]', '{{datetime:%Y}}', 7)") == f'{{{{datetime:{fmt}}}}}{{{{datetime:%Y}}}}'


def test_the_time_limit_of_its_own_ends_with_the_preview(designer):
    # Without AbortSignal.timeout each preview left a timer of a minute
    designer.evaluate("""() => {
        AbortSignal.timeout = undefined;
        window.timers = new Set();
        const set = window.setTimeout, clear = window.clearTimeout;
        window.setTimeout = (fn, ms, ...args) => { const id = set(fn, ms, ...args); if (ms === previewTimeout) timers.add(id); return id; };
        window.clearTimeout = id => { timers.delete(id); return clear(id); };
    }""")
    type_line(designer, 0, 'Pantry')
    type_line(designer, 0, 'Pantry shelf')
    assert designer.evaluate('() => timers.size') == 0


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


def test_blanks_are_saved_with_their_kind(designer, server, app):
    # The server guessed the kind of blanks without one from their name,
    # e.g. "Add-on" as a date
    assert designer.evaluate("() => ['Frozen on', 'Add-on', 'On hand', 'Date of birth', 'Box'].map(guessKind)") == ['date', 'text', 'text', 'date', 'number']
    type_line(designer, 0, '[Add-on] [Frozen on]')
    save_as(designer, 'Kinds')
    designer.wait_for_selector('#saveForm', state='detached')
    try:
        with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Kinds.json'), encoding='utf-8') as f:
            fields = json.load(f)['fields']
        assert fields['Add-on'] == {'type': 'text'} and fields['Frozen on']['type'] == 'date'
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Kinds.json'})


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


def test_a_slow_save_closes_only_its_own_dialog(designer, server):
    # Saved after the dialog was closed, it closed the layer open by then
    held = []
    designer.route('**/api/repository/save', lambda route: held.append(route))
    type_line(designer, 0, 'Spices')
    save_as(designer, 'Slow spices')
    while not held:
        designer.wait_for_timeout(50)
    designer.keyboard.press('Escape')
    designer.click('#edSym')
    designer.wait_for_selector('#layer:not([hidden]) [data-sym]')
    held[0].continue_()
    designer.unroute('**/api/repository/save')
    try:
        designer.wait_for_function("() => S.edit.origin && S.edit.origin.kind === 'saved'")
        assert designer.is_visible('#layer [data-sym]')
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Slow_spices.json'})


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


def test_a_save_ends_well_when_the_editor_is_gone(designer, server):
    # Saved after the wizard was opened, it failed on the editor that was
    # gone: the label counted as not saved and was saved twice next time
    held = []
    designer.route('**/api/repository/save', lambda route: held.append(route))
    type_line(designer, 0, 'Oats')
    save_as(designer, 'Slow oats')
    while not held:
        designer.wait_for_timeout(50)
    # e.g. back in the browser
    designer.evaluate("() => { location.hash = 'wizard'; }")
    designer.wait_for_selector('#libGrid')
    held[0].continue_()
    designer.unroute('**/api/repository/save')
    try:
        designer.wait_for_function("() => document.getElementById('toast').innerText.includes('\"Slow oats\" is now one of your saved labels')")
        assert designer.evaluate("() => S.edit.title === 'Slow oats' && S.edit.origin.kind === 'saved'")
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Slow_oats.json'})


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


def test_undo_and_redo(designer):
    type_line(designer, 0, 'One')
    kept(designer)
    type_line(designer, 0, 'Two')
    kept(designer)
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == 'One'
    designer.click('#edRedo')
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == 'Two'


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


def test_stage_follows_typing_while_previews_take_long(designer):
    # Each preview of the text before is shown while the newest is on its
    # way. Thrown away as out of date, the stage showed none while typing.
    shown = designer.evaluate("""async () => {
        const fetched = window.fetch;
        window.fetch = async (...args) => {
            const r = await fetched(...args);
            if (String(args[0]).includes('/preview')) await new Promise(done => setTimeout(done, 150));
            return r;
        };
        const stage = document.querySelector('#edStage'), tx = document.querySelector('[data-row="0"] .tx');
        let shown = 0;
        const watch = new MutationObserver(() => { if (stage.querySelector('img')) shown++; });
        watch.observe(stage, { childList: true });
        for (const text of ['S', 'Sh', 'She', 'Shel', 'Shelf', 'Shelf ', 'Shelf 1', 'Shelf 12', 'Shelf 12a', 'Shelf 12ab']) {
            tx.value = text;
            tx.dispatchEvent(new Event('input', { bubbles: true }));
            await new Promise(done => setTimeout(done, 50));
        }
        watch.disconnect();
        window.fetch = fetched;
        return shown;
    }""")
    assert shown >= 2
    wait_for_preview(designer)
    assert designer.evaluate("() => S.edit.design.text[0].text") == 'Shelf 12ab'


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


def test_preview_waiting_in_line_is_of_the_label_as_asked(designer):
    # The label changed while its preview waited, the preview kept for it
    # showed the change
    sent = designer.evaluate("""async () => {
        const fetched = window.fetch, bodies = [];
        window.fetch = (url, opts) => { if (String(url).includes('/preview')) bodies.push(new URLSearchParams(opts.body)); return fetched(url, opts); };
        running = PARALLEL;
        const design = Object.assign(baseDesign('62'), { text: [L('Waiting {{blank:Room}}', 40)] }), values = { Room: 'Garage' };
        const p = preview(design, values);
        design.text[0].text = 'Changed';
        values.Room = 'Attic';
        running = 0;
        nextJob();
        await p;
        window.fetch = fetched;
        return bodies.map(b => [JSON.parse(b.get('text'))[0].text, b.get('blanks')]);
    }""")
    assert sent == [['Waiting {{blank:Room}}', '{"Room":"Garage"}']]


def test_editor_follows_changes_while_a_preview_takes_long(designer):
    # Fields and buttons do not wait for the preview on its way
    designer.evaluate("""() => {
        const fetched = window.fetch;
        window.fetch = async (...args) => {
            if (String(args[0]).includes('/preview')) await new Promise(done => setTimeout(done, 3000));
            return fetched(...args);
        };
    }""")
    designer.fill('[data-row="0"] .tx', 'Shelf')
    designer.fill('[data-row="0"] .tx', 'Shelf [Room]')
    designer.wait_for_selector('#edFields input[data-blank="Room"]', timeout=1000)
    assert designer.is_enabled('#edPrint')
    designer.fill('[data-row="0"] .tx', '')
    designer.wait_for_function("() => document.querySelector('#edPrint').disabled", timeout=1000)


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


def test_thumbnails_made_before_they_are_on_the_page_render(designer):
    # Old ones are let go, ones made just now may not be on the page yet
    designer.click('[data-tab="wizard"]')
    designer.wait_for_selector('#wzText')
    attached = designer.evaluate("""() => {
        for (let i = 0; i < 1001; i++) lazyTag(baseDesign('62'), {});
        for (const job of LAZY.values()) job.made -= 60000;
        const a = document.createElement('div'), b = document.createElement('div');
        a.innerHTML = lazyTag(baseDesign('62'), {});
        b.innerHTML = lazyTag(baseDesign('62'), {});
        document.body.append(a);
        attachLazy(a);
        document.body.append(b);
        attachLazy(b);
        const result = [!!a.querySelector('img')._render, !!b.querySelector('img')._render, LAZY.size];
        a.remove();
        b.remove();
        return result;
    }""")
    assert attached == [True, True, 0]


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


def test_print_waits_for_a_preview_that_does_not_fail(designer):
    # The server refused the preview, it refuses printing too. Changed, the
    # label is printed once its preview no longer fails.
    designer.evaluate("""() => {
        Object.assign(S.edit.design, { print_type: 'qrcode', barcode_type: 'ean13', code_text: '12' });
        renderEditor();
    }""")
    wait_for_preview(designer)
    assert designer.is_disabled('#edPrint')
    designer.evaluate("""() => {
        const fetched = window.fetch;
        window.fetch = async (...args) => {
            if (String(args[0]).includes('/preview')) await new Promise(done => setTimeout(done, 1000));
            return fetched(...args);
        };
    }""")
    designer.fill('#edCode', '123')
    assert designer.is_disabled('#edPrint')
    wait_for_preview(designer)
    assert designer.is_disabled('#edPrint')
    designer.fill('#edCode', '400638133393')
    wait_for_preview(designer)
    assert designer.is_enabled('#edPrint')


def test_looks_follow_typing_one_batch_at_a_time(designer):
    # Six previews for every key, those on their way could not be stopped
    designer.click('[data-tab="wizard"]')
    designer.wait_for_selector('#wzText')
    made = designer.evaluate("""async () => {
        const fetched = window.fetch, look = window.lookDesign;
        let made = 0;
        window.lookDesign = i => { made++; return look(i); };
        window.fetch = async (...args) => {
            if (String(args[0]).includes('/preview')) await new Promise(done => setTimeout(done, 150));
            return fetched(...args);
        };
        const ta = document.querySelector('#wzText');
        for (const text of ['J', 'Ja', 'Jam', 'Jam ', 'Jam j', 'Jam ja', 'Jam jar', 'Jam jar ', 'Jam jar 2', 'Jam jar 23']) {
            ta.value = text;
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            await new Promise(done => setTimeout(done, 20));
        }
        await new Promise(done => setTimeout(done, 1000));
        window.fetch = fetched;
        window.lookDesign = look;
        return made;
    }""")
    assert 6 <= made <= 18
    assert designer.evaluate("() => [...document.querySelectorAll('#fzLooks .bed')].every(bed => bed._gen === S.typedGen)")


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


def test_preview_with_dates_of_the_server_is_made_again_later(designer):
    # Kept, a preview of a [today] or of a date blank without a value showed
    # yesterday's date after midnight
    asked = designer.evaluate("""async () => {
        const fetched = window.fetch, now = Date.now;
        let asked = [];
        window.fetch = (...args) => { if (String(args[0]).includes('/preview')) asked.push(1); return fetched(...args); };
        const count = async (design, values) => { asked = []; await preview(design, values); return asked.length; };
        const today = Object.assign(baseDesign('62'), { text: [L('Made {{datetime:%d.%m.%Y}}', 40)] });
        const blank = Object.assign(baseDesign('62'), { text: [L('Frozen on {{blank:Frozen on}}', 40)] });
        const plain = Object.assign(baseDesign('62'), { text: [L('Plain', 40)] });
        const result = [await count(today, {}), await count(blank, {}), await count(blank, { 'Frozen on': '2026-11-01' }), await count(plain, {})];
        result.push(await count(today, {}), await count(plain, {}));
        Date.now = () => now() + 24 * 3600 * 1000;
        result.push(await count(today, {}), await count(blank, {}), await count(blank, { 'Frozen on': '2026-11-01' }), await count(plain, {}));
        Date.now = now;
        window.fetch = fetched;
        return result;
    }""")
    assert asked == [1, 1, 1, 1, 0, 0, 1, 1, 0, 0]


def test_blank_left_empty_is_printed_empty(designer):
    # Its default, or a date of today, is not put in instead. The preview
    # shows it as still to fill in.
    sent = designer.evaluate("""() => {
        const design = Object.assign(baseDesign('62'), { text: [L('{{blank:Note}} {{blank:Due}}', 40)],
            fields: { Note: { default: 'n/a' }, Due: { type: 'date' } } });
        return JSON.parse(formFor(design, 1, true, { Note: '', Due: '' }).get('blanks'));
    }""")
    assert sent == {'Note': '', 'Due': ''}


def test_preview_asks_for_the_blanks_its_key_is_made_of(designer):
    # The date format changed while the preview waited in line: it was made
    # with the new one and kept for the old one
    sent = designer.evaluate("""async () => {
        const fetched = window.fetch, bodies = [];
        window.fetch = (url, opts) => { if (String(url).includes('/preview')) bodies.push(new URLSearchParams(opts.body)); return fetched(url, opts); };
        S.settings.dateFmt = '%Y-%m-%d';
        running = PARALLEL;
        const p = preview(Object.assign(baseDesign('62'), { text: [L('Due {{blank:Due}}', 40)], fields: { Due: { type: 'date' } } }), { Due: '2026-11-01' });
        S.settings.dateFmt = '%d.%m.%Y';
        running = 0;
        nextJob();
        await p;
        window.fetch = fetched;
        return bodies.map(b => JSON.parse(b.get('fields')).Due.format);
    }""")
    assert sent == ['%Y-%m-%d']


def test_failed_preview_of_the_text_before_is_not_shown(designer):
    # Fixed while the preview of the wrong code was on its way, the error of
    # that preview showed and disabled Print until the next one came
    designer.evaluate("""() => {
        Object.assign(S.edit.design, { print_type: 'qrcode', barcode_type: 'ean13', code_text: '400638133393' });
        renderEditor();
    }""")
    wait_for_preview(designer)
    errors = designer.evaluate("""() => {
        window.errorsSeen = 0;
        new MutationObserver(() => { if (document.querySelector('#edStage .err')) errorsSeen++; })
            .observe(document.querySelector('#edView') || document.body, { childList: true, subtree: true });
        const fetched = window.fetch;
        window.fetch = async (...args) => {
            if (String(args[0]).includes('/preview')) await new Promise(done => setTimeout(done, 300));
            return fetched(...args);
        };
        const code = document.querySelector('#edCode');
        for (const text of ['40063813339X', '400638133393']) {
            code.value = text;
            code.dispatchEvent(new Event('input', { bubbles: true }));
        }
    }""")
    wait_for_preview(designer)
    assert designer.evaluate('() => errorsSeen') == 0
    assert designer.is_enabled('#edPrint')


def test_change_can_be_undone_before_the_pause(designer):
    # Undo was disabled until the pause after the first change
    designer.fill('[data-row="0"] .tx', 'Quick')
    assert designer.is_enabled('#edUndo')
    designer.click('#edUndo')
    wait_for_preview(designer)
    assert designer.input_value('[data-row="0"] .tx') == ''


def test_thumbnails_drawn_over_are_let_go(designer):
    # Drawn over before they came into view, they stayed observed with their
    # labels. Old jobs never put on the page are found with one query.
    designer.click('[data-tab="wizard"]')
    designer.wait_for_selector('#libGrid .thumb')
    result = designer.evaluate("""() => {
        renderLibGrid();
        renderLibGrid();
        for (let i = 0; i < 1001; i++) lazyTag(baseDesign('62'), {});
        for (const job of LAZY.values()) job.made -= 60000;
        const one = document.querySelector.bind(document), all = document.querySelectorAll.bind(document);
        let queries = 0;
        document.querySelector = s => { queries++; return one(s); };
        document.querySelectorAll = s => { queries++; return all(s); };
        const box = document.createElement('div');
        document.body.append(box);
        attachLazy(box);
        document.querySelector = one;
        document.querySelectorAll = all;
        box.remove();
        return [[...lazyWaiting].every(img => img.isConnected), queries, LAZY.size];
    }""")
    assert result[0] and result[1] <= 3 and result[2] == 0


def test_preview_looked_at_goes_before_thumbnails_also_when_asked_for_as_one(designer):
    # The thumbnail of a template was still waiting when it was chosen: its
    # preview waited behind all thumbnails asked for before
    order = designer.evaluate("""async () => {
        const fetched = window.fetch, sent = [];
        window.fetch = (url, opts) => { if (String(url).includes('/preview')) sent.push(JSON.parse(new URLSearchParams(opts.body).get('text'))[0].text); return fetched(url, opts); };
        const label = text => Object.assign(baseDesign('62'), { text: [L(text, 40)] });
        running = PARALLEL;
        const waiting = ['Thumb 1', 'Thumb 2', 'Chosen'].map(text => preview(label(text), {}));
        const looked = preview(label('Chosen'), {}, null, true);
        running = 0;
        nextJob();
        await Promise.all([...waiting, looked]);
        window.fetch = fetched;
        return sent;
    }""")
    assert order[0] == 'Chosen' and sorted(order) == ['Chosen', 'Thumb 1', 'Thumb 2']


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


def test_code_alone_has_no_text_settings(designer):
    type_line(designer, 0, 'Spices')
    designer.click('[data-lay="code"]')
    wait_for_preview(designer)
    assert designer.is_disabled('[data-tfit="shrink"]')
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
    # It could not be printed either
    assert designer.is_disabled('#edPrint')
    designer.fill('#edPages', '1')
    wait_for_preview(designer)
    assert designer.is_enabled('#edPrint')


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
    # The name is asked for, the title of the label is offered
    assert designer.input_value('#saveName') == 'Küche, Regal 2'
    designer.press('#saveName', 'Enter')
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


def save_as(page, name):
    page.click('#edSave')
    page.fill('#saveName', name)
    page.press('#saveName', 'Enter')


def saved_titles(page) -> list:
    return sorted(page.evaluate('() => S.saved.map(x => x.title)'))


def test_a_new_label_is_named_when_saved_and_replaces_none(designer, app, server):
    # It was saved as "New label", the name of every new label
    repo = app.config['LABEL_REPOSITORY_DIR']
    before = saved_titles(designer)
    type_line(designer, 0, 'Flour')
    designer.click('#edSave')
    assert designer.input_value('#saveName') == 'New label'
    # Selected, typing replaces it
    assert designer.evaluate("() => document.activeElement.id === 'saveName' && document.activeElement.selectionStart === 0 && document.activeElement.selectionEnd === 9")
    designer.fill('#saveName', '  ')
    designer.press('#saveName', 'Enter')
    assert designer.inner_text('#saveErr') == 'Give the label a name.'
    designer.fill('#saveName', 'Pantry')
    designer.press('#saveName', 'Enter')
    designer.wait_for_selector('#saveForm', state='detached')
    designer.wait_for_function("() => document.getElementById('toast').innerText.includes('\"Pantry\" is now one of your saved labels')")
    assert designer.input_value('#edTitle') == 'Pantry' and designer.evaluate('() => S.edit.title') == 'Pantry'
    pantry = designer.evaluate('() => S.edit.origin.id')
    assert saved_titles(designer) == sorted(before + ['Pantry'])
    try:
        # Another new label of the same name replaces it only when asked again
        designer.click('#edNew')
        type_line(designer, 0, 'Sugar')
        save_as(designer, 'pantry')
        assert designer.inner_text('#saveErr') == '"Pantry" is one of your saved labels already. Replace it, or give this label another name.'
        assert designer.inner_text('#saveGo') == 'Replace it'
        # Another name is saved as it is
        designer.fill('#saveName', 'Pantry, top')
        assert designer.is_hidden('#saveErr') and designer.inner_text('#saveGo') == 'Save'
        designer.fill('#saveName', 'Pantry')
        designer.press('#saveName', 'Enter')
        designer.click('#saveGo')
        designer.wait_for_selector('#saveForm', state='detached')
        assert designer.evaluate('() => S.edit.origin.id') == pantry
        assert saved_titles(designer) == sorted(before + ['Pantry'])
        with open(os.path.join(repo, pantry), encoding='utf-8') as f:
            assert json.load(f)['text'][0]['text'] == 'Sugar'
        # Saved in another browser in the meantime: the list is loaded again
        # and the name asked for anew, the other label stays
        designer.click('#edNew')
        type_line(designer, 0, 'Rice')
        designer.request.post(server + '/labeldesigner/api/repository/save', data=json.dumps({'name': 'Cellar.json', 'title': 'Cellar', 'label_size': '62', 'text': [{'text': 'Wine', 'size': '40'}]}),
                              headers={'Content-Type': 'application/json'})
        assert 'Cellar' not in saved_titles(designer)
        save_as(designer, 'Cellar')
        designer.wait_for_function("() => document.getElementById('saveGo').innerText === 'Replace it'")
        with open(os.path.join(repo, 'Cellar.json'), encoding='utf-8') as f:
            assert json.load(f)['text'][0]['text'] == 'Wine'
    finally:
        for name in (pantry, 'Cellar.json'):
            designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': name})


def test_a_label_of_the_wizard_is_named_when_saved(designer, server):
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="signs"]')
    designer.click('#libGrid .thumb >> nth=0')
    wait_for_preview(designer, '#libStage')
    title = designer.inner_text('#libGrid .thumb.sel .t')

    def keep():
        designer.click('#libMore')
        designer.click('#libKeep')
        assert designer.input_value('#saveName') == title
        designer.press('#saveName', 'Enter')
    keep()
    designer.wait_for_selector('#saveForm', state='detached')
    names = designer.evaluate('title => S.saved.filter(x => x.title === title).map(x => x.name)', title)
    try:
        assert len(names) == 1
        # Once more, it is replaced only when asked again
        keep()
        assert designer.inner_text('#saveGo') == 'Replace it'
        designer.fill('#saveName', title + ', copy')
        designer.press('#saveName', 'Enter')
        designer.wait_for_function("title => S.saved.some(x => x.title === title)", arg=title + ', copy')
        names.append(designer.evaluate("title => S.saved.find(x => x.title === title).name", title + ', copy'))
    finally:
        for name in names:
            designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': name})


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
            drawn.push({ el, room: values && values.Room });
            return draw.apply(this, arguments);
        };
        const field = document.querySelector('#libFields input[data-blank="Room"]');
        field.value = 'Garage';
        field.dispatchEvent(new Event('input', { bubbles: true }));
        await chooseLook(1);
    }""")
    designer.wait_for_timeout(600)
    wait_for_preview(designer, '#libStage')
    # The stage of the new look shows it, never the previous one
    rooms = designer.evaluate("() => drawn.filter(d => d.el === document.querySelector('#libStage')).map(d => d.room || '')")
    assert rooms and 'Garage' not in rooms


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
    # What dimmed means is said, where there are dimmed ones
    tape = designer.evaluate('() => sizeName(S.tape)')
    assert designer.is_visible('#libOther')
    assert designer.inner_text('#libOther') == f'Dimmed labels are made for another label size than {tape}.'
    for cat in ('saved', 'printed'):
        designer.click(f'.lib-nav [data-cat="{cat}"]')
        assert designer.is_visible('#libOther') == (designer.locator('#libGrid .thumb.other').count() > 0)
    assert designer.is_hidden('#libOther')
    designer.click('.lib-nav [data-cat="signs"]')
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


def test_title_ending_in_json_keeps_the_other_label(designer):
    # "QR-Example.json" is saved as a label of its own, the saved label
    # QR-Example stays as it was
    name = designer.evaluate("""async () => {
        await loadSaved();
        return saveLabel('QR-Example.json', Object.assign(baseDesign('62'), { text: [L('Copy', 40)] }));
    }""")
    try:
        assert name != 'QR-Example.json'
        titles = designer.evaluate('() => Object.fromEntries(S.saved.map(x => [x.name, x.title]))')
        assert titles['QR-Example.json'] != 'QR-Example.json'
    finally:
        designer.evaluate("""name => fetch(API + '/repository/delete', { method: 'POST', body: JSON.stringify({ name }),
            headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' } })""", name)


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
    designer.click('[data-lay="code"]')
    wait_for_preview(designer)
    # Fitted to the label, the size of a dot does not apply
    assert designer.is_disabled('#edQrSize')
    designer.click('[data-fit="0"]')
    wait_for_preview(designer)
    length = preview_size(designer)[1]
    designer.fill('#edQrSize', '20')
    wait_for_preview(designer)
    assert preview_size(designer)[1] > length


def test_saved_label_looks_the_same_through_the_api(designer):
    # The server fills in the blanks for both: a saved label previewed by
    # name looks as in the designer, its date in the format it was shown in
    result = designer.evaluate("""async () => {
        S.settings.dateFmt = '%d.%m.%Y';
        const design = Object.assign(baseDesign('62'), { print_type: 'qrcode_text', code_text: 'box/{{blank:Box}}',
            text: [L('{{blank:What}} {{blank:Code}}', 60), L('Frozen on {{blank:Frozen on}}', 40)],
            fields: { Box: { type: 'number' }, Code: { type: 'text' } } });
        const values = { What: 'Chili', Box: '4', Code: '2026-11-02', 'Frozen on': '2026-11-01' };
        const sent = JSON.parse(formFor(design, 1, false, values).get('fields'));
        const name = await saveLabel('Same through the API', design);
        const own = await (await fetch((await preview(design, values)).url)).arrayBuffer();
        const api = await (await fetch(API + '/repository/preview?name=' + encodeURIComponent(name) + '&blanks=' + encodeURIComponent(JSON.stringify(values)))).arrayBuffer();
        const saved = S.saved.find(x => x.name === name);
        await deleteLabel(name);
        const b64 = buf => btoa(String.fromCharCode(...new Uint8Array(buf)));
        return { sent, own: b64(own), api: b64(api), saved: !!saved };
    }""")
    assert result['sent']['Frozen on'] == {'type': 'date', 'format': '%d.%m.%Y'}
    assert result['sent']['Code'] == {'type': 'text'} and result['saved']
    own, api = (Image.open(io.BytesIO(base64.b64decode(result[k]))) for k in ('own', 'api'))
    assert own.size == api.size and own.convert('RGB').tobytes() == api.convert('RGB').tobytes()


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


def test_preview_of_a_replaced_stage_is_dropped(designer):
    # The stage is gone by the time its preview arrives
    result = designer.evaluate("""async () => {
        const el = document.createElement('div');
        document.body.append(el);
        let warned = false;
        const done = showOnStage(el, Object.assign(baseDesign('62'), { text: [L('Gone', 40)] }), {}, () => { warned = true; });
        el.remove();
        await done;
        return [warned, el.innerHTML.includes('Label preview')];
    }""")
    assert result == [False, False]


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


def test_number_blanks_are_asked_for(designer):
    # An empty number is missing like an empty text
    designer.click('[data-tab="wizard"]')
    designer.fill('#wzText', 'Box [Number]')
    designer.wait_for_function("() => document.querySelectorAll('#fzLooks .tile').length === 6")
    designer.click('#fzLooks [data-plook="0"]')
    designer.wait_for_selector('#libFields input[data-blank="Number"]:focus', timeout=5000)


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


def test_saved_label_without_a_size_fits(designer, app):
    # Like an older label or one saved through the API: it opens with the
    # default size, so it is not shown as made for another tape
    repo = app.config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, 'QR-Example.json'), encoding='utf-8') as f:
        label = json.load(f)
    label.pop('label_size', None)
    path = os.path.join(repo, 'No-Size.json')
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(label, f)
    try:
        designer.reload()
        designer.click('[data-tab="wizard"]')
        designer.wait_for_selector('#libGrid .thumb:has(.t:text-is("No Size"))')
        assert 'Needs' not in designer.inner_text('#libGrid .thumb:has(.t:text-is("No Size"))')
    finally:
        os.remove(path)


def test_dropped_preview_is_released_without_another_one(designer):
    # Released once no image shows it, also when no other preview follows
    released = designer.evaluate("""async () => {
        const url = URL.createObjectURL(new Blob(['label']));
        release(url);
        await new Promise(done => setTimeout(done, 11500));
        try { await fetch(url); return false; } catch (e) { return true; }
    }""")
    assert released


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


def test_symbols_closed_while_loading_stay_closed(designer):
    designer.click('#edSym')
    designer.keyboard.press('Escape')
    designer.wait_for_timeout(1000)
    assert designer.evaluate("() => document.getElementById('layer').hidden")


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


def test_thumbnails_do_not_pile_up(designer):
    # Each drawing of the wizard made new ones, none were let go
    designer.click('[data-tab="wizard"]')
    for text in ('Jam', 'Jam jar', 'Jam jar 2'):
        designer.fill('#wzText', text)
        designer.wait_for_timeout(700)
    assert designer.evaluate('() => LAZY.size') < 20


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


def test_code_of_empty_blanks_takes_the_text(designer):
    # Like a code left empty, the server puts the text of the label into it.
    # It gets the blanks as they are, with their values.
    bodies = []
    designer.route('**/api/print', lambda route: (bodies.append(route.request.post_data_buffer), route.continue_()))
    designer.evaluate("""() => printJob({ title: 'Link', values: {}, copies: 1,
        design: Object.assign(baseDesign('62'), { print_type: 'qrcode_text', code_text: '{{blank:URL}}', text: [L('Shelf', 40)] }) })""")
    assert printed(designer) == ['printed']
    form = parse_qs(bodies[0].decode(), keep_blank_values=True)
    assert len(bodies) == 1 and form['code_text'] == ['{{blank:URL}}'] and form['blanks'] == ['{}']

