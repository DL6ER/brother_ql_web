"""Where the content is between the top and bottom margins of labels with
room to spare: at the top, in the middle or at the bottom."""

import io
import json

import pytest
from PIL import Image

from tests.helpers import _upload, EXAMPLE_FORMDATA, make_client, TEXT, verify_image

# Without a frame, so that only the content has ink
PLAIN = dict(EXAMPLE_FORMDATA, border_thickness='0')
MARGIN_TOP, MARGIN_BOTTOM = int(PLAIN['margin_top']), int(PLAIN['margin_bottom'])


def _preview(client, files=None, **values):
    response = client.post('/labeldesigner/api/preview', data=dict(PLAIN, **values, **(files or {})))
    assert response.status_code == 200, response.get_json()
    return response


def _ink(response) -> tuple:
    """The size of the label and the box (left, top, right, bottom) of
    everything printed on it."""
    img = Image.open(io.BytesIO(response.data)).convert('L')
    return img.size, Image.eval(img, lambda v: 255 - v).getbbox()


@pytest.mark.parametrize('values', [
    {'label_size': '62x29'},
    {'label_size': '62x29', 'orientation': 'rotated'},
    {'label_size': '62x100'},
    # Along endless tape the room is across it
    {'label_size': '62', 'orientation': 'rotated'},
    # Endless tape of a fixed length is laid out as a die-cut label
    {'label_size': '62', 'label_length': '60'},
])
def test_the_content_is_at_the_top_in_the_middle_or_at_the_bottom(tmp_path, values):
    client = make_client(tmp_path)
    (width, height), top = _ink(_preview(client, text=TEXT, vertical_align='top', **values))
    _, middle = _ink(_preview(client, text=TEXT, vertical_align='center', **values))
    _, bottom = _ink(_preview(client, text=TEXT, vertical_align='bottom', **values))
    # The letters start a little below the top of the line and end a little
    # above its bottom
    assert MARGIN_TOP <= top[1] <= MARGIN_TOP + 12
    assert height - MARGIN_BOTTOM - 12 <= bottom[3] <= height - MARGIN_BOTTOM
    assert abs((middle[1] + middle[3]) / 2 - height / 2) <= 6
    assert top[3] - top[1] == middle[3] - middle[1] == bottom[3] - bottom[1]
    # Across, the text stays where it is
    assert top[0] == middle[0] == bottom[0]


@pytest.mark.parametrize('values', [{}, {'label_size': '62x29'}, {'label_size': '62', 'orientation': 'rotated'}])
def test_the_middle_is_where_the_content_was_before(tmp_path, values):
    client = make_client(tmp_path)
    before = _preview(client, text=TEXT, **values).data
    assert _preview(client, text=TEXT, vertical_align='center', **values).data == before
    assert _preview(client, text=TEXT, vertical_align='', **values).data == before


def test_an_endless_label_across_the_tape_has_no_room_to_spare(tmp_path):
    # It grows with its content, the content stays in the middle
    client = make_client(tmp_path)
    labels = {align: _preview(client, text=TEXT, vertical_align=align).data for align in ('top', 'center', 'bottom')}
    assert labels['top'] == labels['center'] == labels['bottom']


def test_a_code_with_text_is_moved_as_a_whole(tmp_path):
    # In the middle the code is at its margin and the text in the middle of
    # the room left. At the top or bottom both are there, at their distance.
    client = make_client(tmp_path)
    values = {'label_size': '62x100', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '0', 'qrcode_size': '6'}
    middle = _preview(client, text=TEXT, **values)
    (_, height), centered = _ink(middle)
    _, top = _ink(_preview(client, text=TEXT, vertical_align='top', **values))
    bottom_response = _preview(client, text=TEXT, vertical_align='bottom', **values)
    _, bottom = _ink(bottom_response)
    assert top[1] == centered[1] == MARGIN_TOP
    assert height - MARGIN_BOTTOM - 12 <= bottom[3] <= height - MARGIN_BOTTOM
    assert top[3] - top[1] == bottom[3] - bottom[1] < centered[3] - centered[1]
    assert bottom_response.headers.get('X-Label-Image-Text-Gap') == middle.headers.get('X-Label-Image-Text-Gap')
    verify_image(bottom_response.data, 'vertical_align_bottom.png')


@pytest.mark.parametrize('values', [
    {'image_position': 'above'},
    {'image_position': 'below'},
])
def test_text_and_code_keep_their_order_and_distance(tmp_path, values):
    client = make_client(tmp_path)
    values = dict(values, label_size='62x100', print_type='qrcode_text', code_text='https://example.com', image_fit='0', qrcode_size='6', image_text_gap='30')
    for align in ('top', 'bottom'):
        img = Image.open(io.BytesIO(_preview(client, text=TEXT, vertical_align=align, **values).data)).convert('L')
        rows = [y for y in range(img.height) if img.crop((0, y, img.width, y + 1)).getextrema()[0] < 128]
        # Two blocks of ink, the code and the text, at least 30 dots apart
        breaks = [(a, b) for a, b in zip(rows, rows[1:]) if b - a > 1]
        assert max(b - a for a, b in breaks) >= 30
        code_first = values['image_position'] == 'above'
        first_block_height = breaks[0][0] - rows[0]
        assert (first_block_height > 100) == code_first


@pytest.mark.parametrize('align', ['top', 'bottom'])
def test_an_image_beside_the_text_shares_its_middle(tmp_path, align):
    # The higher of the two is at the margin, the other one in the middle
    # beside it
    client = make_client(tmp_path)

    def image():
        return {'image': _upload(Image.new('RGB', (60, 160), 'black'), 'black.png')}
    values = {'label_size': '62x100', 'orientation': 'rotated', 'print_type': 'image', 'image_fit': '0', 'image_position': 'left'}
    response = _preview(client, image(), text=TEXT, vertical_align=align, **values)
    img = Image.open(io.BytesIO(response.data)).convert('L')
    width, height = img.size
    inverted = Image.eval(img, lambda v: 255 - v)
    left = inverted.crop((0, 0, int(PLAIN['margin_left']) + 60, height)).getbbox()
    text = inverted.crop((int(PLAIN['margin_left']) + 61, 0, width, height)).getbbox()
    if align == 'top':
        assert left[1] == MARGIN_TOP
    else:
        assert left[3] == height - MARGIN_BOTTOM
    assert abs((left[1] + left[3]) / 2 - (text[1] + text[3]) / 2) <= 6


def test_an_image_with_a_code_moves_with_the_text(tmp_path):
    # The image above the text, the code below it: in the middle at the
    # margins, at the top or bottom together at their distance
    client = make_client(tmp_path)

    def image():
        return {'image': _upload(Image.new('RGB', (60, 60), 'black'), 'black.png')}
    values = {'label_size': '62x100', 'print_type': 'image_qrcode_text', 'image_position': 'above', 'code_text': 'https://example.com',
              'image_fit': '0', 'qrcode_size': '4', 'image_text_gap': '20'}
    (_, height), centered = _ink(_preview(client, image(), text=TEXT, **values))
    _, top = _ink(_preview(client, image(), text=TEXT, vertical_align='top', **values))
    _, bottom = _ink(_preview(client, image(), text=TEXT, vertical_align='bottom', **values))
    assert top[1] == centered[1] == MARGIN_TOP
    assert bottom[3] == centered[3] == height - MARGIN_BOTTOM
    assert top[3] - top[1] == bottom[3] - bottom[1] < centered[3] - centered[1]


@pytest.mark.parametrize('value', ['middle', 'TOP', 'left', '1'])
def test_another_vertical_position_is_refused(tmp_path, value):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/preview', data=dict(PLAIN, text=TEXT, vertical_align=value))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'vertical_align must be top, center or bottom'


def test_a_saved_label_keeps_its_vertical_position(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    label = dict(PLAIN, name='Top', label_size='62x29', vertical_align='top', text=json.loads(TEXT))
    assert client.post('/labeldesigner/api/repository/save', json=label).get_json()['success']
    assert client.get('/labeldesigner/api/repository/load?name=Top.json').get_json()['vertical_align'] == 'top'
    saved = client.post('/labeldesigner/api/repository/preview', json={'name': 'Top.json'})
    assert saved.data == _preview(client, text=TEXT, label_size='62x29', vertical_align='top').data
    response = client.post('/labeldesigner/api/repository/save', json=dict(label, vertical_align='up'))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'vertical_align must be top, center or bottom'
