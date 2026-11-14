"""The wizard of the label designer: looks, templates, demo labels and
search."""

from tests.browser.helpers import printed, wait_for_preview
from tests.browser.test_printing import fake_printer


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
    # Those for the tape in the printer come first and are counted. The
    # simulator does not say which tape it has, none is placed on it.
    fits = designer.evaluate("() => libItems('signs').map(i => fitsTape(i.size))")
    count = int(designer.inner_text('.lib-nav [data-cat="signs"] .n'))
    assert 0 < count < len(fits) and fits == [True] * count + [False] * (len(fits) - count)
    subs = designer.eval_on_selector_all('#libGrid .thumb .s', 'subs => subs.map(s => s.textContent)')
    assert not any(sub.startswith('Made for ') for sub in subs)
    designer.wait_for_function("() => document.querySelector('#libGrid .thumb img').src.startsWith('blob:')")
    designer.click('#libGrid .thumb:has(.t:text-is("Fire extinguisher"))')
    wait_for_preview(designer, '#libStage')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    wait_for_preview(designer)
    assert designer.evaluate('() => [S.edit.design.label_size, S.edit.design.image.name]') == ['62', 'fire-extinguisher.png']
    assert designer.evaluate('() => S.edit.design.text.map(l => l.text)') == ['Fire', 'extinguisher']


def test_templates_are_shown_on_the_tape_in_the_printer(designer):
    fake_printer(designer, media_width=62, media_length=0)
    asked = []
    designer.on('request', lambda request: asked.append(request.url))
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="doors"]')
    door = designer.locator('#libGrid .thumb:has(.t:text-is("Meeting room")):has(.s:text-is("Made for 29 x 90 mm"))')
    door.scroll_into_view_if_needed()
    designer.wait_for_function('img => img.complete && img.naturalWidth > 0', arg=door.locator('img').element_handle())
    # Drawn here on the tape in the printer, not by the server on its own
    assert any('/demos/load?id=doors%2F002-meeting-room-29x90.json' in url for url in asked)
    assert not any('/demos/preview?id=doors%2F002-meeting-room-29x90.json' in url for url in asked)
    door.click()
    wait_for_preview(designer, '#libStage')
    assert designer.inner_text('#libDetail .badge') == 'Made for 29 x 90 mm'
    assert designer.inner_text('#libStage .dim-y') == '62 mm'


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


def test_saved_labels_are_grouped_by_their_tags(designer, app):
    import json
    import os
    designer.fill('[data-row="0"] .tx', 'Peas')
    designer.click('#edSave')
    designer.fill('#saveName', 'Tagged peas')
    designer.fill('#saveTags', 'Freezer, Kitchen')
    designer.press('#saveName', 'Enter')
    designer.wait_for_function("() => S.saved.some(s => s.title === 'Tagged peas' && s.tags.length === 2)")
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="tag:Freezer"]')
    assert designer.inner_text('#libTitle') == 'Freezer'
    assert designer.locator('#libGrid .thumb .t').all_inner_texts() == ['Tagged peas']
    # Its tags are changed in the menu of the label, its design stays
    designer.click('#libGrid .thumb')
    designer.click('#libMore')
    designer.click('#libTags')
    assert designer.input_value('#saveTags') == 'Freezer, Kitchen'
    designer.click('#tagChips [data-tag="Freezer"]')
    assert designer.input_value('#saveTags') == 'Kitchen'
    designer.click('#tagsGo')
    designer.wait_for_function("() => S.saved.some(s => s.title === 'Tagged peas' && s.tags.join() === 'Kitchen')")
    assert designer.locator('.lib-nav [data-cat="tag:Freezer"]').count() == 0
    with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Tagged_peas.json'), encoding='utf-8') as fh:
        saved = json.load(fh)
    assert saved['tags'] == ['Kitchen'] and 'Peas' in json.dumps(saved['text'])
    # Saved again from the editor, it keeps its tags
    designer.click('.lib-nav [data-cat="tag:Kitchen"]')
    designer.click('#libGrid .thumb')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    designer.fill('[data-row="0"] .tx', 'Beans')
    designer.click('#edSave')
    designer.wait_for_function("async () => (await (await fetch(API + '/repository/load?name=Tagged_peas.json')).text()).includes('Beans')")
    with open(os.path.join(app.config['LABEL_REPOSITORY_DIR'], 'Tagged_peas.json'), encoding='utf-8') as fh:
        assert json.load(fh)['tags'] == ['Kitchen']
    designer.evaluate("() => deleteLabel('Tagged_peas.json')")


def test_tags_changed_elsewhere_stay_when_the_label_is_saved_again(designer):
    designer.evaluate("() => saveLabel('Stale', normalize({ text: [{ text: 'x' }] }), null, ['Stale'])")
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="tag:Stale"]')
    designer.click('#libGrid .thumb')
    designer.click('#libEdit')
    designer.wait_for_selector('#edStage')
    # Another browser adds a tag in the meantime
    designer.evaluate("""() => fetch(API + '/repository/save', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Stale.json', text: [{ text: 'x' }], tags: ['Stale', 'Garage'] }) })""")
    designer.fill('[data-row="0"] .tx', 'Changed')
    designer.click('#edSave')
    designer.wait_for_selector('#toast:has-text("Saved")')
    assert designer.evaluate("() => S.saved.find(s => s.name === 'Stale.json').tags") == ['Stale', 'Garage']
    designer.evaluate("() => deleteLabel('Stale.json')")


def test_the_tags_dialog_changes_only_the_tags(designer):
    designer.evaluate("""() => fetch(API + '/repository/save', { method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: 'Api', text: [{ text: 'x' }], tags: ['Api'], print_count: 3, collate: 0 }) }).then(loadSaved)""")
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="tag:Api"]')
    designer.click('#libGrid .thumb')
    designer.click('#libMore')
    designer.click('#libTags')
    designer.fill('#saveTags', 'Api, Garage')
    designer.click('#tagsGo')
    designer.wait_for_selector('#tagsForm', state='detached')
    stored = designer.evaluate("() => fetch(API + '/repository/load?name=Api.json').then(r => r.json())")
    assert (stored['tags'], stored['print_count'], stored['collate']) == (['Api', 'Garage'], 3, 0)
    designer.evaluate("() => deleteLabel('Api.json')")


def test_a_group_of_a_tag_keeps_the_order_of_the_saved_labels(designer):
    designer.evaluate("""async () => {
        await saveLabel('Aa small', normalize({ label_size: '29', text: [{ text: 'x' }] }), null, ['Order']);
        await saveLabel('Bb wide', normalize({ label_size: '62', text: [{ text: 'x' }] }), null, ['Order']);
    }""")
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="tag:Order"]')
    assert designer.locator('#libGrid .thumb .t').all_inner_texts() == ['Aa small', 'Bb wide']
    designer.evaluate("async () => { await deleteLabel('Aa_small.json'); await deleteLabel('Bb_wide.json'); }")


def test_a_group_no_label_has_any_more_shows_the_saved_labels(designer):
    designer.evaluate("() => saveLabel('Last', normalize({ text: [{ text: 'x' }] }), null, ['Lonely'])")
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="tag:Lonely"]')
    designer.click('#libGrid .thumb')
    designer.click('#libMore')
    designer.click('#libTags')
    designer.fill('#saveTags', '')
    designer.click('#tagsGo')
    designer.wait_for_selector('#tagsForm', state='detached')
    assert designer.inner_text('#libTitle') == 'Saved labels'
    assert designer.get_attribute('.lib-nav button.on', 'data-cat') == 'saved'
    designer.evaluate("() => deleteLabel('Last.json')")


def test_a_tag_given_to_a_ready_made_label_shows_its_group(designer):
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="all"]')
    designer.locator('#libGrid .thumb').first.click()
    designer.click('#libMore')
    designer.click('#libKeep')
    designer.fill('#saveName', 'Kept demo')
    designer.fill('#saveTags', 'Brandnew')
    designer.press('#saveName', 'Enter')
    designer.wait_for_selector('.lib-nav [data-cat="tag:Brandnew"]', timeout=5000)
    designer.evaluate("() => deleteLabel('Kept_demo.json')")


def test_undo_of_removing_a_label_brings_back_its_tags(designer):
    designer.evaluate("() => saveLabel('Undo', normalize({ text: [{ text: 'x' }] }), null, ['Undo'])")
    designer.click('[data-tab="wizard"]')
    designer.click('.lib-nav [data-cat="tag:Undo"]')
    designer.click('#libGrid .thumb')
    designer.click('#libMore')
    designer.click('#libDelete')
    designer.click('#toast button:has-text("Undo")')
    designer.wait_for_function("() => S.saved.some(s => s.name === 'Undo.json')")
    assert designer.evaluate("() => S.saved.find(s => s.name === 'Undo.json').tags") == ['Undo']
    designer.evaluate("() => deleteLabel('Undo.json')")
