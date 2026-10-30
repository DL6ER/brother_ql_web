"""The label designer in a browser. Most tests cover a bug that the tests of
the API could not find, because it was in the page itself."""
import base64
import io
import json
import os

from PIL import Image

from browser_helpers import wait_until_ready

SETTINGS = 'labeldesigner_settings_v1'


def type_text(page, text):
    page.fill('#label_text', text)
    page.dispatch_event('#label_text', 'input')


def wait_for_status(page, text):
    page.wait_for_function("text => document.getElementById('statusPanel').innerText.includes(text)", arg=text)


def stored_settings(page) -> dict:
    return json.loads(page.evaluate('key => localStorage.getItem(key)', SETTINGS) or '{}')


def store_settings(page, settings: dict):
    page.evaluate('([key, value]) => localStorage.setItem(key, value)', [SETTINGS, json.dumps(settings)])


def reload(page):
    page.reload()
    wait_until_ready(page)


def load_saved_label(page, name):
    page.click('#openRepoBtn')
    page.click(f'.repo-load[data-name="{name}"]')
    page.wait_for_selector('#repoModal', state='hidden')
    page.wait_for_load_state('networkidle')


def offered_label_sizes(page, model) -> list:
    page.evaluate("model => { document.getElementById('label_size').value = '62'; filterLabelSizes(model); }", model)
    return page.evaluate("() => Array.from(document.getElementById('label_size').options).filter(o => !o.hidden).map(o => o.value)")


def test_only_the_simulator_is_offered(page):
    # The page chooses the first printer, the tests printed on a real printer
    # of the machine running them
    printers = "() => Array.from(document.getElementById('printer').options).map(o => o.value)"
    assert page.evaluate(printers) == ['simulation']


def test_preview_of_typed_text(page):
    assert page.is_visible('#labelEmpty')
    type_text(page, 'Hello')
    wait_for_status(page, 'Preview updated')
    assert page.get_attribute('#previewImg', 'src').startswith('data:image/png;base64,')
    assert not page.is_visible('#labelEmpty')


def test_status_does_not_stay_pending(page):
    # A preview without changes is not sent, it left "Updating preview"
    type_text(page, 'Hello')
    wait_for_status(page, 'Preview updated')
    page.evaluate('preview()')
    page.wait_for_timeout(300)
    assert page.inner_text('#statusPanel').strip() == 'Preview updated'


def test_text_that_does_not_fit_is_reported(page):
    type_text(page, 'This line is much too long for a 62 mm label')
    wait_for_status(page, 'The text does not fit on the label, part of it is cut off.')


def test_configured_default_font_is_selected(page, app):
    family, style = app.extensions['fonts'].get_default_font()
    assert page.input_value('#font') == f'{family},{style}'


def test_fonts_of_each_line_are_kept(page):
    # They were neither saved nor restored, all lines but the first got the
    # default font
    load_saved_label(page, 'Template-Example.json')
    sizes = "() => fontSettingsPerLine.map(line => line.font + ' ' + line.size)"
    expected = ['DejaVu Sans,Book 40', 'DejaVu Sans,Book 40', 'DejaVu Sans,Book 25', 'DejaVu Sans,Book 40', 'DejaVu Sans,Book 40']
    assert page.evaluate(sizes) == expected
    reload(page)
    assert page.evaluate(sizes) == expected


def test_emptied_font_size_falls_back_to_the_default(page):
    # The empty value made every preview fail after reloading
    type_text(page, 'Hello')
    wait_for_status(page, 'Preview updated')
    settings = stored_settings(page)
    settings['font_size'] = ''
    store_settings(page, settings)
    reload(page)
    assert page.input_value('#font_size') == '70'
    wait_for_status(page, 'Preview updated')


def test_loading_a_saved_label_keeps_the_printer(page):
    printer = page.input_value('#printer')
    load_saved_label(page, 'EAN-Label.json')
    assert page.input_value('#printer') == printer
    assert not page.is_visible('#printerStatusPanel')


def test_printer_that_is_not_available_is_reported(page):
    # The label designer switched to another printer without saying so
    type_text(page, 'Hello')
    settings = stored_settings(page)
    settings['printer'] = 'file:///dev/usb/lp7'
    store_settings(page, settings)
    reload(page)
    page.wait_for_function("() => document.getElementById('printerStatusPanel').innerText.includes('not available')")
    assert 'The printer /dev/usb/lp7 is not available.' in page.inner_text('#printerStatusPanel')


def test_dark_mode_is_remembered(page):
    theme = "() => document.documentElement.getAttribute('data-bs-theme')"
    before = page.evaluate(theme)
    page.click('#darkModeToggle')
    after = page.evaluate(theme)
    assert {before, after} == {'light', 'dark'}
    reload(page)
    assert page.evaluate(theme) == after


def test_template_help_shows_the_placeholders(page):
    # The browser took <start>, <var> and <len> for HTML tags
    page.click('button[data-bs-target="#templateInfoBox"]')
    help_text = page.inner_text('#templateInfoBox')
    for placeholder in ('<start>', '<var>', '<len>'):
        assert placeholder in help_text


def test_label_sizes_follow_the_printer_model(page):
    ql800 = offered_label_sizes(page, 'QL-800')
    assert '62red' in ql800 and '103x164' not in ql800
    ql1100 = offered_label_sizes(page, 'QL-1100')
    assert '103x164' in ql1100 and '104' in ql1100 and '103' not in ql1100
    # 36 mm TZe tape needs a PT-P900 series printer
    ptp700 = offered_label_sizes(page, 'PT-P700')
    assert 'pt24' in ptp700 and 'pt36' not in ptp700


def test_copies_buttons(page):
    page.click('#printCountUp')
    page.click('#printCountUp')
    assert page.input_value('#print_count') == '3'
    for _ in range(5):
        page.click('#printCountDown')
    assert page.input_value('#print_count') == '1'


def test_600_dpi_printing_is_explained(page):
    page.click('button[data-bs-target="#accordionLabelSize"]')
    page.click('button[data-bs-target="#highResInfo"]')
    page.wait_for_selector('#highResInfo.show')
    assert 'only confirmed for the QL-800' in page.inner_text('#highResInfo')
    # The info button does not change the option
    assert not page.is_checked('#high_res')


def fake_printer(page, **media):
    """Answer the status queries with a QL-800 that reports the given media."""
    status = {'errors': [], 'model': 'QL-800', 'path': 'simulation', 'red_support': True,
              'media_width': None, 'media_length': 0, **media}
    body = json.dumps({**status, 'printers': [status], 'selected': 'simulation'})
    page.route('**/api/printer_status*', lambda route: route.fulfill(content_type='application/json', body=body))
    reload(page)
    page.click('button[data-bs-target="#accordionLabelSize"]')


def test_label_name_keeps_its_hyphens(page):
    # Non-breaking hyphens showed as gaps in fonts that do not have them
    assert page.inner_text('#previewLabelName') == '62 mm endless (DK-22205 / DK-2205)'
    assert page.locator('#previewLabelName .text-nowrap').count() == 2


def test_simulator_reports_no_loaded_label(page):
    page.click('button[data-bs-target="#accordionLabelSize"]')
    assert not page.is_visible('#detectedLabel')
    assert not page.is_visible('#labelMismatch')
    assert not page.is_visible('#labelMismatchIcon')


def test_loaded_label_can_be_chosen(page):
    fake_printer(page, media_width=29, media_length=90)
    assert page.inner_text('#detectedLabelName') == '29 x 90 mm die-cut (DK-11201 / DK-1201)'
    assert page.is_visible('#labelMismatch') and page.is_visible('#labelMismatchIcon')
    page.click('#useDetectedLabel')
    assert page.input_value('#label_size') == '29x90'
    assert not page.is_visible('#labelMismatch') and not page.is_visible('#labelMismatchIcon')
    assert page.is_visible('#detectedLabel')


def test_loaded_black_and_red_label_is_not_a_mismatch(page):
    # The printer reports the same size for both 62 mm endless labels
    fake_printer(page, media_width=62, media_length=0)
    page.select_option('#label_size', '62red')
    page.wait_for_load_state('networkidle')
    assert page.is_visible('#detectedLabel')
    assert not page.is_visible('#labelMismatch')


def test_loaded_label_that_is_not_offered(page):
    fake_printer(page, media_width=40, media_length=0)
    assert page.inner_text('#detectedLabelName') == '40 mm endless'
    assert page.is_visible('#labelMismatch')
    assert not page.is_visible('#useDetectedLabel')


def test_ctrl_enter_prints(page, monkeypatch, tmp_path):
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    type_text(page, 'Hello')
    wait_for_status(page, 'Preview updated')
    page.keyboard.press('Control+Enter')
    wait_for_status(page, 'Printed')
    assert len(list(tmp_path.glob('*.png'))) == 1


def test_pasted_image_becomes_the_label(page):
    page.evaluate("""async () => {
        const canvas = document.createElement('canvas');
        canvas.width = 200;
        canvas.height = 100;
        canvas.getContext('2d').fillRect(20, 20, 160, 60);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        const data = new DataTransfer();
        data.items.add(new File([blob], 'image.png', { type: 'image/png' }));
        document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }));
    }""")
    page.wait_for_function("() => !document.getElementById('labelStage').classList.contains('is-empty')")
    wait_for_status(page, 'Preview updated')
    assert page.is_checked('#printTypeImage')
    assert page.evaluate('imageDropZone.files.length') == 1


def test_label_with_a_font_that_is_not_installed(page, app):
    # The preview failed with "Unknown font family"
    repo = app.config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, 'QR-Example.json'), encoding='utf-8') as f:
        label = json.load(f)
    for line in label['text']:
        line['font'] = 'Not Installed,Regular'
    with open(os.path.join(repo, 'Other-Fonts.json'), 'w', encoding='utf-8') as f:
        json.dump(label, f)
    try:
        load_saved_label(page, 'Other-Fonts.json')
        wait_for_status(page, 'is not installed')
        assert 'The font Not Installed (Regular) is not installed' in page.inner_text('#statusPanel')
        assert page.get_attribute('#previewImg', 'src').startswith('data:image/png;base64,')
        # The dropdown was empty, now it names the font. The label keeps it.
        assert page.evaluate("() => document.getElementById('font').selectedOptions[0].text") == \
            'Not Installed (Regular), not installed'
        assert page.evaluate('() => fontSettingsPerLine[0].font') == 'Not Installed,Regular'
        # Choosing an installed font ends the warning
        page.click('button[data-bs-target="#accordionFontSettings"]')
        page.select_option('#font', page.get_attribute('#font', 'data-default'))
        wait_for_status(page, 'Preview updated')
    finally:
        os.remove(os.path.join(repo, 'Other-Fonts.json'))


def test_text_that_does_not_fit_can_be_shrunk(page):
    type_text(page, 'A very long line that does not fit on the label at all')
    wait_for_status(page, 'does not fit')
    page.check('#shrink_text')
    wait_for_status(page, 'Preview updated')
    reload(page)
    # The choice is kept like the other settings
    assert page.is_checked('#shrink_text')


def share(page, server, **form):
    """What Android does with something shared with the installed app."""
    response = page.request.post(server + '/labeldesigner/share', multipart=form, max_redirects=0)
    assert response.status == 303
    page.goto(server + response.headers['location'])
    wait_until_ready(page)


def test_shared_image_becomes_the_label(page, server):
    png = io.BytesIO()
    Image.new('L', (200, 100), 'black').save(png, format='PNG')
    share(page, server, file={'name': 'photo.png', 'mimeType': 'image/png', 'buffer': png.getvalue()})
    page.wait_for_function("() => imageDropZone.files.length === 1")
    wait_for_status(page, 'Preview updated')
    assert page.is_checked('#printTypeImage')
    # The token is used up and gone from the address
    assert 'shared=' not in page.url


def test_shared_link_becomes_a_qr_code(page, server):
    share(page, server, text='https://example.com/shelf/12')
    page.wait_for_function("() => document.getElementById('label_text').value === 'https://example.com/shelf/12'")
    assert page.is_checked('#printTypeQrCode')
    assert page.input_value('#barcode_type') == 'QR'
    wait_for_status(page, 'Preview updated')


def add_file(page, data: bytes, name: str, mime: str):
    page.click('label[for=printTypeImage]')
    page.evaluate("""([b64, name, mime]) => {
        const bytes = Uint8Array.from(atob(b64), c => c.charCodeAt(0));
        imageDropZone.addFile(new File([bytes], name, { type: mime }));
    }""", [base64.b64encode(data).decode('ascii'), name, mime])


def pdf(*sizes) -> bytes:
    pages = [Image.new('L', size, 'black') for size in sizes]
    out = io.BytesIO()
    pages[0].save(out, format='PDF', save_all=True, append_images=pages[1:], resolution=100)
    return out.getvalue()


def test_pages_of_a_pdf_can_be_chosen(page):
    add_file(page, pdf((300, 200), (200, 300), (400, 100)), 'pages.pdf', 'application/pdf')
    page.wait_for_selector('#pdfPagesGroup', state='visible')
    assert page.inner_text('#pdfPageCount') == '3'
    height = "() => document.getElementById('previewImg').naturalHeight"
    first = page.evaluate(height)
    page.fill('#pdf_pages', '2')
    page.dispatch_event('#pdf_pages', 'change')
    # Page 2 is portrait, fitted to the label width it is higher
    page.wait_for_function(f"() => document.getElementById('previewImg').naturalHeight > {first}")
    page.fill('#pdf_pages', '7')
    page.dispatch_event('#pdf_pages', 'change')
    wait_for_status(page, 'The PDF has 3 pages, there is no page 7')


def test_page_choice_is_hidden_for_a_single_page(page):
    add_file(page, pdf((300, 200)), 'one.pdf', 'application/pdf')
    wait_for_status(page, 'Preview updated')
    assert not page.is_visible('#pdfPagesGroup')


def test_automatic_orientation_is_explained(page):
    # kinghat in #103: the orientation does nothing with auto-fit
    page.click('button[data-bs-target="#accordionLabelSize"]')
    page.select_option('#label_size', '62x29')
    png = io.BytesIO()
    Image.new('L', (100, 300), 'black').save(png, format='PNG')
    add_file(page, png.getvalue(), 'portrait.png', 'image/png')
    page.wait_for_selector('#autoOrientation', state='visible')
    assert page.inner_text('#autoOrientation') == 'Turned automatically to fit the label best'
    assert page.is_disabled('#orientation_rotated')
    # Without auto-fit the orientation counts again. An older preview still
    # running answered after the newer one and turned the hint on again.
    page.click('button[data-bs-target="#accordionImageSettings"]')
    page.uncheck('#image_fit')
    page.wait_for_load_state('networkidle')
    page.wait_for_function("() => document.getElementById('autoOrientation').hidden"
                           " && !document.getElementById('orientation_rotated').disabled")


def test_several_files_are_printed(page, monkeypatch, tmp_path):
    # kinghat in #103: several shipping labels at once
    monkeypatch.setattr('app.labeldesigner.printer.SIMULATED_LABELS_DIR', str(tmp_path))
    add_file(page, pdf((300, 200)), 'first.pdf', 'application/pdf')
    add_file(page, pdf((200, 300), (400, 100)), 'second.pdf', 'application/pdf')
    page.wait_for_selector('#imageFilesNote', state='visible')
    assert page.inner_text('#imageFilesNote') == '2 files, each is printed as a label of its own. The preview shows the first.'
    wait_for_status(page, 'Preview updated')
    page.click('#printButton')
    wait_for_status(page, 'Printed')
    # The first page of each PDF
    assert len(list(tmp_path.glob('*.png'))) == 2


def test_pasted_image_replaces_the_files(page):
    add_file(page, pdf((300, 200)), 'first.pdf', 'application/pdf')
    add_file(page, pdf((300, 200)), 'second.pdf', 'application/pdf')
    page.wait_for_function("() => imageDropZone.files.length === 2")
    page.evaluate("""async () => {
        const canvas = document.createElement('canvas');
        canvas.width = 50;
        canvas.height = 20;
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        const data = new DataTransfer();
        data.items.add(new File([blob], 'image.png', { type: 'image/png' }));
        document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }));
    }""")
    page.wait_for_function("() => imageDropZone.files.length === 1 && imageDropZone.files[0].name === 'image.png'")
    assert not page.is_visible('#imageFilesNote')


def test_saved_labels_of_the_selected_label_size(page, app):
    repo = app.config['LABEL_REPOSITORY_DIR']
    with open(os.path.join(repo, 'QR-Example.json'), encoding='utf-8') as f:
        label = json.load(f)
    label['label_size'] = '29'
    with open(os.path.join(repo, 'Small.json'), 'w', encoding='utf-8') as f:
        json.dump(label, f)
    del label['label_size']
    with open(os.path.join(repo, 'No-Size.json'), 'w', encoding='utf-8') as f:
        json.dump(label, f)
    shown = "() => Array.from(document.querySelectorAll('#repoListBody .repo-item')).filter(i => !i.hidden).map(i => i.querySelector('.repo-load').dataset.name)"
    try:
        page.click('#openRepoBtn')
        page.wait_for_selector('.repo-load[data-name="Small.json"]', state='attached')
        # 62 mm is selected: no 29 mm or black/red labels, but those without a size
        names = page.evaluate(shown)
        assert 'QR-Example.json' in names and 'No-Size.json' in names
        assert 'Small.json' not in names and 'URGENT-Text.json' not in names
        page.check('#repoAllSizes')
        names = page.evaluate(shown)
        assert 'Small.json' in names and 'URGENT-Text.json' in names
    finally:
        os.remove(os.path.join(repo, 'Small.json'))
        os.remove(os.path.join(repo, 'No-Size.json'))
