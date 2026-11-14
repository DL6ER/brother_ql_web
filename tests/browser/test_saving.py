"""Saving labels in the label designer: names, titles, dialogs and labels
saved through the API."""

import base64
import io
import json
import os

from PIL import Image

from tests.browser.helpers import save_as, type_line, wait_for_preview


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
        designer.wait_for_function("() => document.getElementById('toast').innerText.includes('Saved \"Slow oats\" to your labels')")
        assert designer.evaluate("() => S.edit.title === 'Slow oats' && S.edit.origin.kind === 'saved'")
    finally:
        designer.request.post(server + '/labeldesigner/api/repository/delete', form={'name': 'Slow_oats.json'})


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
    designer.wait_for_function("() => document.getElementById('toast').innerText.includes('Saved \"Pantry\" to your labels')")
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
