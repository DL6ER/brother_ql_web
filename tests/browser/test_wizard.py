"""The wizard of the label designer: looks, templates, demo labels and
search."""

from tests.browser.helpers import printed, wait_for_preview


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
