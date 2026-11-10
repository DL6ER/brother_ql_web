"""Endless labels of a fixed length: their size, how their content is laid
out in it and which lengths are refused."""

import io
import json

import pytest
from PIL import Image

from app.labeldesigner import refusals
from app.labeldesigner.label_names import LABELS, length_limits
from tests.helpers import _upload, EXAMPLE_FORMDATA, make_client, TEXT, verify_image


def _preview(client, **values):
    return client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, **values))


def _size(response) -> tuple:
    assert response.status_code == 200, response.get_json()
    return Image.open(io.BytesIO(response.data)).size


@pytest.mark.parametrize('values, size', [
    # 50 mm as cut, less the margin of 35 dots (3 mm) the printer feeds
    # before and after the print: 590.6 - 70 dots at 300 dpi
    ({}, (696, 521)),
    ({'orientation': 'rotated'}, (521, 696)),
    ({'high_res': '1'}, (1392, 1041)),
    # TZe tape at 180 dpi with a margin of 14 dots (2 mm): 354.3 - 28 dots
    ({'label_size': 'pt12'}, (70, 326)),
    ({'label_size': 'pt12', 'orientation': 'rotated'}, (326, 70)),
])
def test_an_endless_label_has_the_length_given(tmp_path, values, size):
    client = make_client(tmp_path)
    assert _size(_preview(client, label_length='50', **values)) == size


@pytest.mark.parametrize('length', ['', None])
def test_an_endless_label_without_a_length_grows_with_its_content(tmp_path, length):
    client = make_client(tmp_path)
    grown = _size(_preview(client))
    values = {} if length is None else {'label_length': length}
    assert _size(_preview(client, **values)) == grown
    longer = json.dumps([{'font': 'DejaVu Sans,Book', 'text': f'Line {n}', 'size': '40'} for n in range(4)])
    assert _size(_preview(client, text=longer, **values))[1] > grown[1]


def test_a_die_cut_label_keeps_its_own_length(tmp_path):
    # The length is kept with a label whose size is changed back and forth
    client = make_client(tmp_path)
    assert _size(_preview(client, label_size='62x29', label_length='50')) == _size(_preview(client, label_size='62x29'))


def test_the_length_may_have_decimals(tmp_path):
    client = make_client(tmp_path)
    assert _size(_preview(client, label_length='25.5'))[1] == round((25.5 - 2 * 35 / 300 * 25.4) / 25.4 * 300)


@pytest.mark.parametrize('values, reference', [
    pytest.param({}, 'fixed_length.png', id='across'),
    pytest.param({'orientation': 'rotated'}, 'fixed_length_rotated.png', id='along'),
])
def test_the_text_is_in_the_middle_of_a_label_of_a_fixed_length(tmp_path, values, reference):
    client = make_client(tmp_path)
    response = _preview(client, label_length='40', text=TEXT, text_fit='none', **values)
    assert 'X-Label-Warning' not in response.headers
    verify_image(response.data, reference)


def test_text_is_shrunk_to_the_length(tmp_path):
    # As high as the label is long, not more: four lines of 70 on 20 mm
    client = make_client(tmp_path)
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': f'Line {n}', 'size': '70'} for n in range(4)])
    response = _preview(client, label_length='20', text=text, text_fit='none')
    assert 'part of it is cut off' in response.headers['X-Label-Warning']
    response = _preview(client, label_length='20', text=text, text_fit='shrink')
    assert 'X-Label-Warning' not in response.headers
    assert _size(response)[1] == round((20 - 2 * 35 / 300 * 25.4) / 25.4 * 300)
    verify_image(response.data, 'fixed_length_shrunk.png')


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_text_fills_a_label_of_a_fixed_length_both_ways(tmp_path, orientation):
    # Filled, the text takes the length as well as the width: one short line
    # on a long label grows across the tape, a long one along it
    client = make_client(tmp_path)
    short = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Box', 'size': '20'}])
    response = _preview(client, label_length='100', text=short, text_fit='fill', orientation=orientation, border_thickness='0')
    assert 'X-Label-Warning' not in response.headers
    img = Image.open(io.BytesIO(response.data)).convert('L')
    left, top, right, bottom = Image.eval(img, lambda v: 255 - v).getbbox()
    width, height = img.size
    margins = (int(EXAMPLE_FORMDATA['margin_left']), int(EXAMPLE_FORMDATA['margin_right']),
               int(EXAMPLE_FORMDATA['margin_top']), int(EXAMPLE_FORMDATA['margin_bottom']))
    # It reaches the margins one way and stays within them the other
    assert left >= margins[0] - 2 and right <= width - margins[1] + 2
    assert top >= margins[2] - 2 and bottom <= height - margins[3] + 2
    assert right - left > 0.9 * (width - margins[0] - margins[1]) or bottom - top > 0.6 * (height - margins[2] - margins[3])


def test_an_image_is_fitted_into_the_length_as_well(tmp_path):
    # Without a length only the width of the tape counts, the label grows:
    # 300 x 150 dots fitted to 656 dots between the margins
    client = make_client(tmp_path)
    values = {'print_type': 'image', 'image_fit': '1', 'border_thickness': '0', 'text': '[]'}

    def image():
        return _upload(Image.new('RGB', (300, 150), 'black'), 'black.png')
    assert _size(client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, image=image(), **values))) == (696, 328 + 24)
    response = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, image=image(), label_length='30', **values))
    width, height = _size(response)
    assert (width, height) == (696, 284)
    img = Image.open(io.BytesIO(response.data)).convert('L')
    left, top, right, bottom = Image.eval(img, lambda v: 255 - v).getbbox()
    # As high as the label less its margins, in the middle across
    assert (top, bottom) == (12, height - 12)
    assert abs((left + right) / 2 - width / 2) <= 1


@pytest.mark.parametrize('length, message', [
    ('long', 'label_length must be a length in millimeters or empty'),
    ('-5', 'label_length must be a length in millimeters or empty'),
    ('0', 'label_length must be a length in millimeters or empty'),
    ('nan', 'label_length must be a length in millimeters or empty'),
    ('inf', 'label_length must be a length in millimeters or empty'),
    ('3000.5', 'label_length must not be longer than 3000 mm on this tape'),
    # 12.7 mm printed and the margins of about 3 mm before and after it
    ('18.6', 'label_length must be at least 18.7 mm on this tape'),
])
def test_a_length_that_cannot_be_printed_is_refused(tmp_path, length, message):
    client = make_client(tmp_path)
    response = _preview(client, label_length=length)
    assert response.status_code == 400
    assert response.get_json()['message'] == message


@pytest.mark.parametrize('label_size, shortest, refused', [
    ('62', '18.7', '18.6'),
    # 4.4 mm printed on TZe tape, and margins of 2 mm
    ('pt12', '8.4', '8.3'),
])
def test_the_shortest_length_depends_on_the_tape(tmp_path, label_size, shortest, refused):
    client = make_client(tmp_path)
    assert _preview(client, label_size=label_size, label_length=shortest).status_code == 200
    response = _preview(client, label_size=label_size, label_length=refused)
    assert response.get_json()['message'] == f'label_length must be at least {shortest} mm on this tape'


@pytest.mark.parametrize('label_size, refused, longest', [
    # The longest label of the printers of the tape: the QL-1100 series,
    # the QL-800 series for black and red tape, the PT-E550W for TZe tape
    ('62', '3000.5', '3000'),
    ('62red', '1000.5', '1000'),
    ('pt12', '2000.5', '2000'),
])
def test_the_longest_length_depends_on_the_printers_of_the_tape(tmp_path, label_size, refused, longest):
    client = make_client(tmp_path)
    response = _preview(client, label_size=label_size, label_length=refused)
    assert response.get_json()['message'] == f'label_length must not be longer than {longest} mm on this tape'


def test_tze_tape_of_the_pt_p900_series_has_its_margin_at_360_dpi(tmp_path):
    # 14 dots of margin at 360 dpi, about 1 mm: 708.7 - 28 dots
    client = make_client(tmp_path)
    assert _size(_preview(client, label_size='18', label_length='50')) == (234, 681)
    assert _preview(client, label_size='18', label_length='6.3').get_json()['message'] == 'label_length must be at least 6.4 mm on this tape'


def test_every_tape_refuses_a_length_with_a_fixed_message(tmp_path):
    # Answered as it is, the message names the shortest or longest length of
    # the tape
    client = make_client(tmp_path)
    for label in LABELS:
        if length_limits(label):
            for length, start in (('1', 'label_length must be at least '), ('5000', 'label_length must not be longer than ')):
                message = client.post('/labeldesigner/api/preview', data=dict(EXAMPLE_FORMDATA, label_size=label.identifier, label_length=length)).get_json()['message']
                assert message.startswith(start) and message in refusals.MESSAGES, label.identifier


@pytest.mark.parametrize('values', [
    # The label twice and the wrap between, as long as their content
    {'cable_flag': '1'},
    # Labels of the VC-500W and of receipt printers grow with their content
    {'label_size': 'vc25'},
    {'label_size': 'escpos80'},
])
def test_labels_without_a_length_to_set_keep_it_for_later(tmp_path, values):
    client = make_client(tmp_path)
    grown = _preview(client, text=TEXT, **values)
    assert _size(grown)[1] < round((50 - 2 * 35 / 300 * 25.4) / 25.4 * 300)
    assert _preview(client, text=TEXT, label_length='50', **values).data == grown.data
    # Also one too short for any tape, saved for when it applies
    client = make_client(tmp_path / 'saved', empty_repo=True)
    response = client.post('/labeldesigner/api/repository/save', json=dict(EXAMPLE_FORMDATA, name='Grown', text=TEXT, label_length='5', **values))
    assert response.get_json()['success']


def test_the_length_as_a_json_number(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    label = dict(EXAMPLE_FORMDATA, name='Shelf', label_size='62', label_length=50, text=json.loads(TEXT))
    assert client.post('/labeldesigner/api/repository/save', json=label).get_json()['success']
    assert _size(client.post('/labeldesigner/api/repository/preview', json={'name': 'Shelf.json'})) == (696, 521)
    response = client.post('/labeldesigner/api/repository/save', json=dict(label, label_length=True))
    assert response.get_json()['message'] == 'label_length must be a length in millimeters or empty'


def test_a_saved_label_keeps_its_length(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    label = dict(EXAMPLE_FORMDATA, name='Shelf', label_size='62', label_length='40', text=TEXT)
    assert client.post('/labeldesigner/api/repository/save', json=label).get_json()['success']
    assert client.get('/labeldesigner/api/repository/load?name=Shelf.json').get_json()['label_length'] == '40'
    response = client.post('/labeldesigner/api/repository/preview', json={'name': 'Shelf.json'})
    assert _size(response)[1] == round((40 - 2 * 35 / 300 * 25.4) / 25.4 * 300)


@pytest.mark.parametrize('values, message', [
    ({'label_length': 'long'}, 'label_length must be a length in millimeters or empty'),
    ({'label_length': '5'}, 'label_length must be at least 18.7 mm on this tape'),
    ({'label_length': '5', 'label_size': 'pt12'}, 'label_length must be at least 8.4 mm on this tape'),
])
def test_a_label_of_a_length_that_cannot_be_printed_is_not_saved(tmp_path, values, message):
    client = make_client(tmp_path, empty_repo=True)
    response = client.post('/labeldesigner/api/repository/save', json=dict(EXAMPLE_FORMDATA, name='Shelf', text=TEXT, **dict({'label_size': '62'}, **values)))
    assert response.status_code == 400
    assert response.get_json()['message'] == message


def test_a_die_cut_label_is_saved_with_a_length_kept_for_endless_tape(tmp_path):
    # The label designer keeps it for when the label is put on endless tape
    # again, die-cut labels do not use it
    client = make_client(tmp_path, empty_repo=True)
    response = client.post('/labeldesigner/api/repository/save', json=dict(EXAMPLE_FORMDATA, name='Card', text=TEXT, label_size='62x29', label_length='5'))
    assert response.get_json()['success']
