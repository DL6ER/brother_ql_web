"""Cable flags: the label twice, with room between them to wrap around the
cable, and both ends stuck together back to back."""

import io
import json

import pytest
from PIL import Image, ImageChops

from app.labeldesigner import label as label_module
from app.labeldesigner.cable_flag import wrap_dots
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _red_square, _upload, EXAMPLE_FORMDATA, make_client, TEXT

FLAG_TAPE = 'Cable flags need continuous tape'
DIAMETER = 'The cable diameter must be a number from 1 to 50 mm'
# 6.5 mm at 300 dpi
WRAP = 241


@pytest.fixture
def client(tmp_path):
    return make_client(tmp_path)


def _data(**settings) -> dict:
    data = dict(EXAMPLE_FORMDATA, label_size='62', border_thickness='0', text=TEXT)
    data.update(settings)
    return data


def _render(client, **settings) -> Image.Image:
    with client.application.test_request_context():
        return create_label_from_request(_data(**settings)).generate()


def _flag_and_plain(client, **settings) -> tuple:
    return _render(client, cable_flag='1', **settings), _render(client, **settings)


def _halves(flag: Image.Image, plain: Image.Image, along: bool) -> tuple:
    if along:
        return flag.crop((0, 0, plain.width, flag.height)), flag.crop((flag.width - plain.width, 0, flag.width, flag.height))
    return flag.crop((0, 0, flag.width, plain.height)), flag.crop((0, flag.height - plain.height, flag.width, flag.height))


def _same(a: Image.Image, b: Image.Image) -> bool:
    return a.size == b.size and ImageChops.difference(a.convert('RGB'), b.convert('RGB')).getbbox() is None


@pytest.mark.parametrize('diameter, dpi, dots', [
    (6.5, 180, 145), (6.5, 300, 241), (6.5, 320, 257), (6.5, 360, 289), (6.5, 600, 482), (10, 300, 371)])
def test_wrap_follows_cable_and_dpi(diameter, dpi, dots):
    assert wrap_dots(diameter, dpi) == dots


def test_flag_along_the_tape_repeats_the_label(client):
    flag, plain = _flag_and_plain(client, orientation='rotated')
    assert flag.size == (2 * plain.width + WRAP, plain.height)
    a, b = _halves(flag, plain, True)
    assert _same(a, plain) and _same(b, plain)


def test_flag_across_the_tape_turns_the_second_half(client):
    # Folded around the cable, "up" along the tape turns around
    flag, plain = _flag_and_plain(client, orientation='standard')
    assert flag.size == (plain.width, 2 * plain.height + WRAP)
    a, b = _halves(flag, plain, False)
    assert _same(a, plain) and _same(b, plain.rotate(180))


def test_wrap_is_blank_apart_from_the_marker(client):
    flag, plain = _flag_and_plain(client, orientation='rotated')
    band = flag.crop((plain.width, 0, plain.width + WRAP, flag.height)).convert('L')
    width, height = band.size
    center = width // 2
    # 62 mm tape at 300 dpi: marks of 2.5 mm, ticks of 0.3 mm, a line of 0.15 mm
    mark, tick, line = 30, 4, 2
    for x, y in ((0, 0), (width - 1, 0), (0, height - 1), (width - 1, height - 1), (center, 0), (center, height - 1)):
        assert band.getpixel((x, y)) < 128, (x, y)
    middle = band.crop((center - line, mark, center + line + 1, height - mark))
    assert middle.getextrema()[0] < 128
    # The dots of the line leave gaps, so it looks gray
    assert sum(middle.histogram()[128:]) > middle.width * middle.height / 3
    rest = band.copy()
    for box in ((0, 0, tick, mark), (0, height - mark, tick, height),
                (width - tick, 0, width, mark), (width - tick, height - mark, width, height),
                (center - mark, 0, center + mark + 1, mark + 1), (center - mark, height - mark - 1, center + mark + 1, height),
                (center - line, 0, center + line + 1, height)):
        rest.paste(255, box)
    assert rest.getextrema() == (255, 255)


@pytest.mark.parametrize('diameter', [None, '', '6.5', '6,5'])
def test_empty_diameter_is_a_cat7_cable(client, diameter):
    settings = {'orientation': 'rotated'}
    if diameter is not None:
        settings['cable_diameter'] = diameter
    flag, plain = _flag_and_plain(client, **settings)
    assert flag.width - 2 * plain.width == WRAP


@pytest.mark.parametrize('model, label_size, wrap', [('PT-E550W', 'pt12', 145), ('PT-P900W', 'pt36', 289), ('VC-500W', 'vc25', 257)])
def test_flag_on_ptouch_and_vc500w_tapes(tmp_path, model, label_size, wrap):
    client = make_client(tmp_path, model=model)
    flag, plain = _flag_and_plain(client, label_size=label_size, orientation='rotated')
    assert flag.width - 2 * plain.width == wrap


def test_flag_at_600_dpi(client):
    flag, plain = _flag_and_plain(client, orientation='rotated', high_res='1')
    assert flag.width - 2 * plain.width == 482


def test_both_halves_come_from_one_render(client):
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': '{{random:12}}', 'size': '40', 'align': 'center'}])
    flag = _render(client, orientation='rotated', cable_flag='1', text=text)
    half = (flag.width - WRAP) // 2
    assert _same(flag.crop((0, 0, half, flag.height)), flag.crop((flag.width - half, 0, flag.width, flag.height)))


@pytest.mark.parametrize('settings', [
    {'print_type': 'text'},
    {'print_type': 'qrcode_text', 'code_text': 'https://example.com'},
    {'print_type': 'qrcode', 'code_text': 'https://example.com'},
])
def test_every_layout_makes_a_flag(client, settings):
    flag, plain = _flag_and_plain(client, orientation='rotated', **settings)
    assert flag.width == 2 * plain.width + WRAP


def test_image_label_makes_a_flag(client):
    data = _data(orientation='rotated', print_type='image', image_mode='grayscale', cable_flag='1',
                 image=_upload(_red_square(), 'red.png'))
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    plain = client.post('/labeldesigner/api/preview', data=dict(data, cable_flag='0', image=_upload(_red_square(), 'red.png')))
    width = Image.open(io.BytesIO(response.data)).width
    assert width == 2 * Image.open(io.BytesIO(plain.data)).width + WRAP


def test_black_and_red_flag(client):
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Router', 'size': '60', 'align': 'center', 'color': 'red'}])
    flag, plain = _flag_and_plain(client, label_size='62red', orientation='rotated', text=text)
    a, b = _halves(flag, plain, True)
    red = (255, 0, 0)
    assert red in [color for _, color in a.getcolors(1 << 20)] and red in [color for _, color in b.getcolors(1 << 20)]
    band = flag.crop((plain.width, 0, plain.width + WRAP, flag.height))
    assert {color for _, color in band.getcolors(1 << 20)} == {(0, 0, 0), (255, 255, 255)}


@pytest.mark.parametrize('label_size, high_res', [('62', '0'), ('29', '0'), ('62', '1')])
def test_thin_cable_keeps_the_marker_on_the_wrap(client, label_size, high_res):
    flag, plain = _flag_and_plain(client, label_size=label_size, high_res=high_res, orientation='rotated', cable_diameter='1')
    a, b = _halves(flag, plain, True)
    assert _same(a, plain) and _same(b, plain)


def test_flag_longer_than_the_printer_takes_is_refused(tmp_path):
    client = make_client(tmp_path, model='PT-P700')
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'X' * 120, 'size': '60', 'align': 'center'}])
    data = _data(label_size='pt12', orientation='rotated', text=text, printer='simulation', model='PT-P700')
    # About 700 mm, twice that does not fit
    assert client.post('/labeldesigner/api/print', data=data).get_json() == {'success': True}
    response = client.post('/labeldesigner/api/print', data=dict(data, cable_flag='1'))
    assert response.status_code == 400 and response.get_json()['message'] == 'Labels on the PT-P700 can be at most 1000 mm long'


def test_cable_flag_on_die_cut_labels_is_refused(client):
    response = client.post('/labeldesigner/api/preview', data=_data(label_size='62x29', cable_flag='1'))
    assert response.status_code == 400 and response.get_json()['message'] == FLAG_TAPE


@pytest.mark.parametrize('diameter', ['abc', '0', '-3', '51', 'nan', 'inf'])
def test_bad_cable_diameters_are_refused(client, diameter):
    response = client.post('/labeldesigner/api/preview', data=_data(cable_flag='1', cable_diameter=diameter))
    assert response.status_code == 400 and response.get_json()['message'] == DIAMETER
    # Only checked for a flag
    response = client.post('/labeldesigner/api/preview', data=_data(cable_flag='0', cable_diameter=diameter))
    assert response.status_code == 200


def test_too_large_flag_is_refused(client, monkeypatch):
    plain = _render(client)
    monkeypatch.setattr(label_module, 'MAX_LABEL_PIXELS', 2 * plain.width * plain.height)
    with pytest.raises(ValueError, match='Label is too large'):
        _render(client, cable_flag='1')
