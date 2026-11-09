"""Images, PDFs and shared content in the label designer: pasted, dropped
and shared."""

import io
import re

import pytest
from PIL import Image

from tests.browser.helpers import png, preview_size, printed, wait_for_preview


def pdf(*sizes) -> bytes:
    pages = [Image.new('L', size, 'black') for size in sizes]
    out = io.BytesIO()
    pages[0].save(out, format='PDF', save_all=True, append_images=pages[1:], resolution=100)
    return out.getvalue()


@pytest.mark.parametrize('way, name', [('paste', 'image.png'), ('drop', 'dropped.png')])
def test_pasted_or_dropped_image_becomes_the_label(designer, way, name):
    designer.evaluate("""async ([way, name]) => {
        const canvas = document.createElement('canvas');
        canvas.width = 200;
        canvas.height = 100;
        canvas.getContext('2d').fillRect(20, 20, 160, 60);
        const blob = await new Promise(resolve => canvas.toBlob(resolve, 'image/png'));
        const data = new DataTransfer();
        data.items.add(new File([blob], name, { type: 'image/png' }));
        if (way === 'paste') document.body.dispatchEvent(new ClipboardEvent('paste', { clipboardData: data, bubbles: true }));
        else document.querySelector('#edStage').dispatchEvent(new DragEvent('drop', { dataTransfer: data, bubbles: true, cancelable: true }));
    }""", [way, name])
    designer.wait_for_selector('[data-lay="image"].on')
    wait_for_preview(designer)
    assert designer.evaluate('() => S.edit.design.image.name') == name


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


def test_each_of_several_files_can_be_previewed(designer):
    # They print with the same settings, each can be checked before printing
    designer.route('**/api/print', lambda route: route.fulfill(content_type='application/json', body='{"success": true}'))
    designer.click('[data-lay="image"]')
    designer.set_input_files('#edFile', files=[
        {'name': 'one.png', 'mimeType': 'image/png', 'buffer': png(200, 100)},
        {'name': 'two.png', 'mimeType': 'image/png', 'buffer': png(100, 200)},
        {'name': 'three.png', 'mimeType': 'image/png', 'buffer': png(300, 100)},
    ])
    designer.wait_for_selector('#edFileStep:not([hidden])')
    wait_for_preview(designer)
    assert designer.inner_text('#edFileOf') == 'File 1 of 3: one.png'
    first = preview_size(designer)
    # The preview of the second file is made of that file alone
    with designer.expect_request('**/api/preview') as request:
        designer.click('#edFileStep [data-file="1"]')
    assert re.findall(rb'filename="([^"]*)"', request.value.post_data_buffer) == [b'two.png']
    wait_for_preview(designer)
    assert designer.inner_text('#edFileOf') == 'File 2 of 3: two.png'
    assert preview_size(designer) != first
    # With the arrow keys, from the first file on to the last
    designer.focus('#edFileStep [data-file="-1"]')
    designer.keyboard.press('ArrowLeft')
    wait_for_preview(designer)
    assert preview_size(designer) == first
    designer.keyboard.press('ArrowLeft')
    wait_for_preview(designer)
    assert designer.inner_text('#edFileOf') == 'File 3 of 3: three.png'
    assert designer.evaluate('() => document.activeElement.dataset.file') == '-1'
    # Also in the large preview
    designer.click('#edStage .zoom')
    assert designer.is_visible('#edFileStep') and 'large' in designer.get_attribute('#edStage', 'class').split()
    # All three are printed
    with designer.expect_request('**/api/print') as request:
        designer.click('#edPrint')
    assert re.findall(rb'filename="([^"]*)"', request.value.post_data_buffer) == [b'one.png', b'two.png', b'three.png']
    # New files are previewed from the first, a single one needs no steps
    designer.set_input_files('#edFile', files=[{'name': 'four.png', 'mimeType': 'image/png', 'buffer': png()}])
    designer.wait_for_selector('#edFileStep', state='hidden')
    assert designer.evaluate('() => fileShown(S.edit)') == 0


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
    assert 'The PDF has no such page' in designer.inner_text('#edStage')
    # It could not be printed either
    assert designer.is_disabled('#edPrint')
    designer.fill('#edPages', '1')
    wait_for_preview(designer)
    assert designer.is_enabled('#edPrint')


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
