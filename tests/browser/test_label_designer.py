"""The label designer in a browser. Most tests cover a bug that the tests of
the API could not find, because it was in the page itself."""
import json

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
