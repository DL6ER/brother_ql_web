"""Text sized to fit through the API: shrunk, filled, and told where it fits
at no size."""

import io
import json

import pytest
from PIL import Image, ImageOps

from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _upload, EXAMPLE_FORMDATA, make_client


def test_text_below_a_code_on_endless_tape_is_fitted_once(tmp_path, monkeypatch):
    # Its room does not depend on the distance
    from app.labeldesigner import label as label_module
    fits = []
    original = label_module.SimpleLabel._shrink_text_to_fit
    monkeypatch.setattr(label_module.SimpleLabel, '_shrink_text_to_fit', lambda self, *args: fits.append(1) or original(self, *args))
    client = make_client(tmp_path)
    data = {'label_size': '62', 'print_type': 'qrcode_text', 'orientation': 'standard', 'code_text': 'https://example.com',
            'shrink_text': '1', 'image_fit': '0',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'size': '120', 'text': 'A very long line of text that needs shrinking to fit'}])}
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200
    assert len(fits) == 1


FILL_LINES = json.dumps([{'font': 'DejaVu Sans,Book', 'size': '60', 'text': 'Kitchen'},
                         {'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'spices, top row'}])


@pytest.mark.parametrize('settings', [
    # Its widest line as wide as the tape
    {'label_size': '62'},
    # Along the tape, as high as the tape
    {'label_size': '62', 'orientation': 'rotated'},
    # As large as fits both ways
    {'label_size': '29x90'},
    # Below a code, at the distance that suits the text as printed
    {'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'https://example.com'},
])
def test_text_fills_the_label(tmp_path, settings):
    # All lines by the same factor, they keep the ratio of their sizes
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=FILL_LINES, text_fit='fill', **settings)
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.generate()
        assert label.warnings == []
        sizes = [label._line_size(line) for line in label.text]
        assert sizes[0] > 60 and sizes[1] == int((sizes[0] + 0.5) * 40 / 60)
        if settings.get('print_type') == 'qrcode_text':
            assert label.image_text_gap == max(round(0.4 * sizes[0]), 20)
        else:
            # Within the margins, one size more does not fit
            def fits(size):
                lines = [dict(line, size=str(int((size + 0.5) * int(line['size']) / 60))) for line in json.loads(FILL_LINES)]
                sized = create_label_from_request(dict(data, text_fit='none', text=json.dumps(lines)))
                sized.process_templates()
                textsize = sized._compute_bbox(sized._draw_text(None, []))
                max_width, max_height = sized._text_space(sized._width, sized._height, 0, 0, 0)
                return (max_width is None or textsize[2] <= max_width) and (max_height is None or textsize[3] <= max_height)
            assert fits(sizes[0]) and not fits(sizes[0] + 1)


def test_text_fit_and_the_shrink_text_of_before(tmp_path):
    # text_fit wins, without it shrink_text tells shrink or as set
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='29x90', text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '80', 'text': 'A line that is far too long for this label, by far'}]))
    with client.application.test_request_context():
        def printed(**values):
            label = create_label_from_request(dict(data, **values))
            label.generate()
            return label._line_size(label.text[0]), bool(label.warnings)
        assert printed() == printed(text_fit='none') == (80, True)
        shrunk = printed(shrink_text='1')
        assert shrunk == printed(text_fit='shrink') and shrunk[0] < 80 and not shrunk[1]
        assert printed(shrink_text='1', text_fit='none') == (80, True)
    response = client.post('/labeldesigner/api/preview', data=dict(data, text_fit='grow'))
    assert response.status_code == 400 and response.get_json()['message'] == 'text_fit must be none, shrink or fill'


def test_lines_that_came_out_empty_fill_nothing(tmp_path):
    # Their height alone grew to the largest size, a long piece of empty tape
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', text_fit='fill', blanks=json.dumps({'Name': '', 'Note': ''}),
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Name}}'},
                                 {'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:Note}}'}]))
    with client.application.test_request_context():
        label = create_label_from_request(data)
        image = label.generate()
    assert [label._line_size(line) for line in label.text] == [40, 40] and image.height < 300


def test_fill_measures_each_size_once(tmp_path, monkeypatch):
    # The distance to a code is searched, each size is measured once for all
    # distances tried. With a distance set, the text is filled in once.
    from app.labeldesigner import label as label_module
    measured, filled = [], []
    draw, fill = label_module.SimpleLabel._draw_text, label_module.SimpleLabel._fill_room
    monkeypatch.setattr(label_module.SimpleLabel, '_draw_text', lambda self, img=None, *args: (measured.append(self._text_scale) if img is None else None) or draw(self, img, *args))
    monkeypatch.setattr(label_module.SimpleLabel, '_fill_room', lambda self, *args, **bounds: filled.append(args) or fill(self, *args, **bounds))
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62x29', orientation='rotated', print_type='qrcode_text', code_text='https://example.com',
                text_fit='fill', text=FILL_LINES)
    with client.application.test_request_context():
        create_label_from_request(data).generate()
        fills = [scale for scale in measured if scale != 1.0]
        assert len(fills) == len(set(fills)) and len(filled) > 1
        filled.clear()
        create_label_from_request(dict(data, image_text_gap='20')).generate()
        assert len(filled) == 1


def test_fill_at_other_distances_looks_only_between_the_sizes_known(tmp_path, monkeypatch):
    # Each distance tried searched all sizes again, dozens of layouts for a
    # preview
    from app.labeldesigner import label as label_module
    bounds = []
    fill = label_module.SimpleLabel._fill_room

    def recorded(self, *args, at_least=1, at_most=label_module.MAX_FILL_SIZE):
        bounds.append((at_least, at_most))
        return fill(self, *args, at_least=at_least, at_most=at_most)
    monkeypatch.setattr(label_module.SimpleLabel, '_fill_room', recorded)
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'print_type': 'qrcode_text', 'code_text': 'https://example.com', 'image_fit': '0',
            'image_position': 'above', 'text_fit': 'fill', 'text': FILL_LINES}
    with client.application.test_request_context():
        create_label_from_request(data).generate()
    assert len(bounds) > 2 and all(bound != (1, label_module.MAX_FILL_SIZE) for bound in bounds[1:])


def test_empty_text_fills_nothing(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text_fit='fill', text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '40', 'text': ' '}]))
    assert client.post('/labeldesigner/api/preview', data=data).status_code == 200


@pytest.mark.parametrize('values, warning', [
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A' * 40, 'size': '60'}])},
     'The text does not fit on the label, part of it is cut off.'),
    ({'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Fits', 'size': '60'}])}, None),
    ({'print_type': 'image', 'image_fit': '0', 'image_scaling_factor': '400', 'text': '[]'},
     'The image does not fit on the label, part of it is cut off.'),
])
def test_preview_warns_about_content_that_does_not_fit(tmp_path, values, warning):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update(values)
    if data['print_type'] == 'image':
        data['image'] = _upload(Image.new('L', (400, 100), 'black'), 'wide.png')
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') == warning


@pytest.mark.parametrize('text_fit', ['shrink', 'fill'])
@pytest.mark.parametrize('values, warning', [
    # Margins that leave the text no room: made as small as it gets it was a
    # dot in the margin without a warning
    ({'print_type': 'text', 'label_size': '62x29', 'margin_top': '140', 'margin_bottom': '140'}, 'The text does not fit on the label at any size.'),
    ({'label_size': '62x29', 'image_fit': '0'}, None),
    ({'label_size': '62', 'image_fit': '1'}, None),
])
def test_text_without_room_at_any_size_is_warned_about(tmp_path, text_fit, values, warning):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, print_type='qrcode_text', code_text='https://example.com', qrcode_size='6', text_fit=text_fit,
                text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili', 'size': '160'}]))
    response = client.post('/labeldesigner/api/preview', data=dict(data, **values))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') == warning


@pytest.mark.parametrize('text_fit', ['shrink', 'fill'])
@pytest.mark.parametrize('label_size, image_fit', [('62', '1'), ('62x100', '0'), ('29x90', '0')])
def test_the_text_is_laid_out_once_per_set_of_sizes(tmp_path, monkeypatch, text_fit, label_size, image_fit):
    # Fitted next to a code at several distances, the same sizes came out
    # again and were laid out again
    from app.labeldesigner import label as label_module
    sizes = []
    draw = label_module.SimpleLabel._draw_text

    def counted(self, img=None, *args, **kwargs):
        if img is None:
            sizes.append(tuple(self._line_size(line) for line in self.text))
        return draw(self, img, *args, **kwargs)
    monkeypatch.setattr(label_module.SimpleLabel, '_draw_text', counted)
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size=label_size, print_type='qrcode_text', code_text='https://example.com', image_fit=image_fit, qrcode_size='6',
                text_fit=text_fit, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili con carne', 'size': '160'}]))
    with client.application.test_request_context():
        create_label_from_request(data).generate()
    assert sizes and len(sizes) == len(set(sizes))


def _ink_box(png: bytes):
    """Bounding box of everything that is not white."""
    image = Image.open(io.BytesIO(png)).convert('L')
    return image.size, ImageOps.invert(image).getbbox()


@pytest.mark.parametrize('values', [
    # Too wide for 62 mm endless
    {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A' * 40, 'size': '60'}])},
    # Too high for a 62 x 29 mm die-cut label
    {'label_size': '62x29', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': f'Line {n}', 'size': '60'} for n in range(6)])},
    # Too high across 62 mm endless when rotated
    {'orientation': 'rotated', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': f'Line {n}', 'size': '80'} for n in range(10)])},
])
def test_text_that_does_not_fit_is_shrunk(tmp_path, values):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, border_thickness='0', **values)
    assert client.post('/labeldesigner/api/preview', data=data).headers.get('X-Label-Warning')
    response = client.post('/labeldesigner/api/preview', data=dict(data, shrink_text='1'))
    assert response.status_code == 200
    assert 'X-Label-Warning' not in response.headers
    (width, height), (left, top, right, bottom) = _ink_box(response.data)
    # Within the margins across the tape, and along it on die-cut labels
    if data['orientation'] == 'standard':
        assert left >= 20 and right <= width - 20
    else:
        assert top >= 12 and bottom <= height - 12
    if data['label_size'] == '62x29':
        assert top >= 12 and bottom <= height - 12


def test_text_that_fits_is_not_shrunk(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Fits', 'size': '60'}]))
    expected = client.post('/labeldesigner/api/preview', data=data).data
    assert client.post('/labeldesigner/api/preview', data=dict(data, shrink_text='1')).data == expected


def test_shrunk_text_is_the_same_on_every_copy(tmp_path):
    # The printer generates the same label once per copy
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, label_size='62', shrink_text='1', text=json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A' * 40, 'size': '60'}]))
    with client.application.test_request_context('/labeldesigner/api/preview', method='POST', data=data):
        label = create_label_from_request(data)
        first = label.generate()
        assert label.generate().tobytes() == first.tobytes()


@pytest.mark.parametrize('orientation, text, qrcode_size', [('standard', 'Chili con carne', '6'), ('rotated', 'Chili', '5')])
def test_distance_next_to_shrunk_text_is_found_quickly(tmp_path, monkeypatch, orientation, text, qrcode_size):
    # Each fit narrows the range from both sides, it took 5 and 7 fits to
    # halve it. It is still the smallest distance the fitted text keeps to.
    from app.labeldesigner import label as label_module
    fits = []
    original = label_module.SimpleLabel._shrink_text_to_fit
    monkeypatch.setattr(label_module.SimpleLabel, '_shrink_text_to_fit', lambda self, *args: fits.append(1) or original(self, *args))
    client = make_client(tmp_path)
    data = {'label_size': '29x90', 'print_type': 'qrcode_text', 'orientation': orientation, 'code_text': 'https://example.com',
            'qrcode_size': qrcode_size, 'image_fit': '0', 'shrink_text': '1',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': text, 'size': '120'}])}
    with client.application.test_request_context():
        label = create_label_from_request(data)
        label.generate()
        assert len(fits) <= 4
        closer = create_label_from_request(dict(data, image_text_gap=str(label.image_text_gap - 1)))
        closer.generate()
        assert max(round(0.4 * closer._line_size(closer.text[0])), 2 * int(qrcode_size)) > label.image_text_gap - 1


def test_text_filling_endless_tape_keeps_the_label_within_its_size(tmp_path, monkeypatch):
    # A long line as high as the tape it runs along made a label too large to
    # make, shrunk it worked
    from app.labeldesigner import label as label_module
    monkeypatch.setattr(label_module, 'MAX_LABEL_PIXELS', 4_000_000)
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'text_fit': 'fill', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry ' * 10, 'size': '40'}])}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    width, height = Image.open(io.BytesIO(response.data)).size
    assert width * height <= 4_000_000 and height == 696
    # As large as a label may be, near enough
    assert width * height > 3_000_000


def test_sizes_tried_to_fit_the_text_do_not_push_out_those_drawn(tmp_path):
    # Filling the label tried many sizes in the cache of the fonts drawn
    from app.labeldesigner import label as label_module
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'text_fit': 'fill', 'text': FILL_LINES}
    with client.application.test_request_context():
        label_module._load_font.cache_clear()
        label = create_label_from_request(data)
        label.generate()
    drawn = {label._line_size(line) for line in label.text}
    assert label_module._load_font.cache_info().currsize == len(drawn)
    assert label_module._load_measured_font.cache_info().currsize > len(drawn)
    # Also the font of icons, the size drawn and that of the text
    icons = dict(data, text=json.dumps([{'font': 'DejaVu Sans,Book', 'size': '60', 'text': 'Kitchen {{mdi:star}}'}]))
    with client.application.test_request_context():
        label_module._load_font.cache_clear()
        create_label_from_request(icons).generate()
    assert label_module._load_font.cache_info().currsize == 2


def test_text_filled_into_endless_tape_makes_a_label_of_about_a_meter_at_most(tmp_path):
    # Many short lines across the tape grew the label to 7 m, each line as
    # wide as the tape
    from app.labeldesigner import label as label_module
    client = make_client(tmp_path)
    data = {'label_size': '62', 'text_fit': 'fill', 'margin_top': '20', 'margin_bottom': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'i', 'size': '40'}] * 40)}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    assert Image.open(io.BytesIO(response.data)).height <= label_module.MAX_FILLED_RATIO * 696 + 40
    # At 600 dpi as long, not half as long
    response = client.post('/labeldesigner/api/preview', data=dict(data, high_res='1'))
    assert label_module.MAX_FILLED_RATIO * 1392 * 0.9 < Image.open(io.BytesIO(response.data)).height <= label_module.MAX_FILLED_RATIO * 1392 + 80


def test_text_filled_next_to_a_code_searches_the_distance_where_only_the_length_changes(monkeypatch):
    # Along endless tape the rooms differ only in the length the label may
    # grow to, which only filled text has: compared without it, the search
    # ended at once at the largest distance
    from app.labeldesigner import label as label_module
    label = label_module.SimpleLabel(fill_text=True)
    monkeypatch.setattr(label, '_text_space', lambda width, height, img_width, img_height, gap, largest=False: (1000 - gap if largest else None, 100))
    tried = []

    def fill(max_width, max_height, at_least=1, at_most=label_module.MAX_FILL_SIZE):
        tried.append(max_width)
        label._text_scale = max_width / 1000
        label._filled_size = int(max_width)
        return [], (0, 0, 0, 0)
    monkeypatch.setattr(label, '_fill_room', fill)
    monkeypatch.setattr(label, '_gap', lambda img_width, img_height: round(40 * label._text_scale))
    label._shrink_text_next_to_image([], (0, 0, 0, 0), 100, 100, 10, 10)
    assert len(set(tried)) > 2
