"""Blanks in the label designer: fields, their settings, dates and values
saved through the API."""

import json
import os
from urllib.parse import parse_qs

import pytest

from tests.browser.helpers import kept, printed, save_as, type_line, wait_for_preview


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
        designer.wait_for_function("() => document.querySelector('[data-bname=\"Frozen on\"] [data-offset-n]').value === ''")
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
        designer.wait_for_function("() => document.querySelector('[data-bname=\"Due\"] [data-offset-n]').value === ''")
        assert designer.evaluate("() => [S.edit.values.Due === today(), 'default' in S.edit.design.fields.Due]") == [True, False]
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Due.json'})


@pytest.mark.parametrize('day, offset, moved', [
    ('2024-01-31', '+1m', '2024-02-29'), ('2023-01-31', '+1m', '2023-02-28'), ('2024-03-31', '-1m', '2024-02-29'),
    ('2024-02-29', '+1y', '2025-02-28'), ('2024-12-30', '+5d', '2025-01-04'), ('2024-01-31', '-2w', '2024-01-17'),
    ('2024-11-15', '+14m', '2026-01-15'), ('2024-01-15', '-13m', '2022-12-15'),
])
def test_a_date_blank_moves_from_today_as_the_server_moves_dates(designer, day, offset, moved):
    # The field starts at the browser's date, the server moves the others
    import datetime
    from app.labeldesigner.label import shift_date
    assert shift_date(datetime.date.fromisoformat(day), offset).isoformat() == moved
    assert designer.evaluate('([day, offset]) => shiftDate(day, offset)', [day, offset]) == moved


def test_a_date_blank_starts_at_a_day_moved_from_today(designer, server, app):
    # A best-before date started at today, the label said "Use within 5 days"
    type_line(designer, 0, 'Use by [Use by]')
    designer.click('#blankSet')
    row = '[data-bname="Use by"]'
    designer.click(f'{row} [data-kind="date"]')
    designer.fill(f'{row} [data-offset-n]', '5')
    designer.dispatch_event(f'{row} [data-offset-n]', 'change')
    designer.wait_for_function("() => (S.edit.design.fields['Use by'] || {}).default === '+5d'")
    designer.select_option(f'{row} [data-offset-unit]', 'm')
    designer.wait_for_function("() => S.edit.design.fields['Use by'].default === '+5m'")
    assert designer.evaluate('() => S.edit.values["Use by"] === shiftDate(today(), "+5m")')
    # Back at today
    designer.fill(f'{row} [data-offset-n]', '0')
    designer.dispatch_event(f'{row} [data-offset-n]', 'change')
    designer.wait_for_function("() => !('default' in S.edit.design.fields['Use by']) && S.edit.values['Use by'] === today()")
    designer.fill(f'{row} [data-offset-n]', '2')
    designer.dispatch_event(f'{row} [data-offset-n]', 'change')
    designer.wait_for_function("() => S.edit.design.fields['Use by'].default === '+2m'")
    designer.click('#blankSet')
    field = '#edFields input[data-blank="Use by"]'
    designer.wait_for_selector(field)
    assert designer.input_value(field) == designer.evaluate('() => shiftDate(today(), "+2m")')
    # Saved with it, the API prints that day as well
    save_as(designer, 'Pesto')
    designer.wait_for_selector('#saveForm', state='detached')
    try:
        with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Pesto.json'), encoding='utf-8') as f:
            assert json.load(f)['fields']['Use by']['default'] == '+2m'
        # Opened again, it starts there
        designer.evaluate("""async () => {
            await loadSaved();
            const item = { kind: 'saved', id: 'Pesto.json' };
            openEditor({ title: 'Pesto', design: await itemDesign(item), origin: item });
        }""")
        designer.wait_for_selector(field)
        assert designer.input_value(field) == designer.evaluate('() => shiftDate(today(), "+2m")')
        designer.click('#blankSet')
        assert [designer.input_value(f'{row} [data-offset-n]'), designer.input_value(f'{row} [data-offset-unit]')] == ['2', 'm']
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Pesto.json'})


def test_a_new_date_blank_can_start_at_a_day_moved_from_today(designer):
    designer.click('#edBlank')
    designer.fill('#bName', 'today+5d')
    designer.click('[data-bkind="date"]')
    designer.click('#bInsert')
    assert designer.inner_text('#bErr') == '[today+5d] is a variable already, choose another name.'
    designer.fill('#bName', 'Use by')
    designer.fill('#bDateStart [data-offset-n]', '2')
    designer.select_option('#bDateStart [data-offset-unit]', 'w')
    designer.click('#bInsert')
    designer.wait_for_selector('#edFields input[data-blank="Use by"]')
    assert designer.evaluate("() => S.edit.design.fields['Use by']") == {'type': 'date', 'default': '+2w'}
    assert designer.evaluate('() => S.edit.values["Use by"] === shiftDate(today(), "+2w")')


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


def test_number_blanks_are_asked_for(designer):
    # An empty number is missing like an empty text
    designer.click('[data-tab="wizard"]')
    designer.fill('#wzText', 'Box [Number]')
    designer.wait_for_function("() => document.querySelectorAll('#fzLooks .tile').length === 6")
    designer.click('#fzLooks [data-plook="0"]')
    designer.wait_for_selector('#libFields input[data-blank="Number"]:focus', timeout=5000)


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
