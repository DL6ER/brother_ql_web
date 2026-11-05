"""Previews in the label designer: the queue, time limits, thumbnails and
what a failed one tells."""

from tests.browser.helpers import fits_note, preview_size, type_line, wait_for_preview


def test_preview_of_typed_text(designer):
    type_line(designer, 0, 'Hello')
    assert designer.get_attribute('#edStage .paper img', 'src').startswith('blob:')
    assert 'Everything fits.' in fits_note(designer)
    assert designer.is_enabled('#edPrint')


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


def test_dropped_preview_is_released_without_another_one(designer):
    # Released once no image shows it, also when no other preview follows
    released = designer.evaluate("""async () => {
        const url = URL.createObjectURL(new Blob(['label']));
        release(url);
        await new Promise(done => setTimeout(done, 11500));
        try { await fetch(url); return false; } catch (e) { return true; }
    }""")
    assert released


def test_thumbnails_do_not_pile_up(designer):
    # Each drawing of the wizard made new ones, none were let go
    designer.click('[data-tab="wizard"]')
    for text in ('Jam', 'Jam jar', 'Jam jar 2'):
        designer.fill('#wzText', text)
        designer.wait_for_timeout(700)
    assert designer.evaluate('() => LAZY.size') < 20


def test_a_wrong_check_digit_is_told(designer):
    # It was replaced by the right one: a typo printed another valid code
    designer.click('[data-lay="code"]')
    designer.select_option('#edBarcode', 'EAN13')
    designer.fill('#edCode', '4006381333932')
    wait_for_preview(designer)
    assert 'The check digit of EAN-13 must be 1' in designer.inner_text('#edStage')
    assert designer.is_disabled('#edPrint')
    designer.fill('#edCode', '4006381333931')
    wait_for_preview(designer)
    assert designer.is_enabled('#edPrint')
