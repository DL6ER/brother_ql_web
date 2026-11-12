"""Labels with an image, a code and text (print_type image_qrcode_text): the
image at its side of the text, the code at the other one."""

import base64
import io
import json

import pytest
from PIL import Image, ImageChops, ImageFilter

from app.labeldesigner.enums import LabelContent, LabelOrientation
from app.labeldesigner.label import _code_image
from app.labeldesigner.routes import create_label_from_request

from tests.helpers import _upload, make_client, verify_image
from tests.repository.helpers import SAVE_URL

URL = 'https://example.com'
SIDES = ('above', 'below', 'left', 'right')
OTHER = {'above': 'below', 'below': 'above', 'left': 'right', 'right': 'left'}


def _red_image(size=(120, 80)) -> Image.Image:
    return Image.new('RGB', size, (255, 0, 0))


def _png(image: Image.Image) -> bytes:
    out = io.BytesIO()
    image.save(out, 'PNG')
    return out.getvalue()


def _color_label(**values) -> dict:
    """A red image, a blue code of 25 x 4 dots and green text on the 50 mm
    color tape of the VC-500W, along the tape."""
    return {'label_size': 'vc50', 'orientation': 'rotated', 'print_type': 'image_qrcode_text', 'image_fit': '0',
            'code_text': URL, 'qrcode_size': '4', 'qrcode_correction': 'M', 'print_color': '#0000ff', 'text_fit': 'shrink',
            'margin_top': '20', 'margin_bottom': '20', 'margin_left': '20', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60', 'color': '#00c000', 'align': 'left'}]),
            'image': (io.BytesIO(_png(_red_image())), 'red.png', 'image/png'), **values}


def _preview(client, data):
    return client.post('/labeldesigner/api/preview', data=data, content_type='multipart/form-data')


def _boxes(png: bytes) -> dict:
    """The boxes of the red image, the blue code and the green text."""
    image = Image.open(io.BytesIO(png)).convert('RGB')
    channels = dict(zip('rgb', image.split()))

    def box(high, threshold):
        mask = channels[high].point(lambda v: 255 if v > threshold else 0)
        for other in 'rgb':
            if other != high:
                mask = ImageChops.multiply(mask, channels[other].point(lambda v: 255 if v < 80 else 0))
        return mask.getbbox()
    return {'image': box('r', 200), 'code': box('b', 200), 'text': box('g', 150)}


def _distance(a, b, side) -> int:
    """How far box b is from box a on the side of a."""
    return {'above': a[1] - b[3], 'below': b[1] - a[3], 'left': a[0] - b[2], 'right': b[0] - a[2]}[side]


@pytest.mark.parametrize('side', SIDES)
@pytest.mark.parametrize('orientation', ['rotated', 'standard'])
def test_the_image_is_on_its_side_of_the_text_and_the_code_on_the_other(tmp_path, orientation, side):
    client = make_client(tmp_path)
    response = _preview(client, _color_label(orientation=orientation, image_position=side))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    assert response.headers['X-Label-Image-Position'] == side
    gap = int(response.headers['X-Label-Image-Text-Gap'])
    width, height = Image.open(io.BytesIO(response.data)).size
    boxes = _boxes(response.data)
    image, code, text = boxes['image'], boxes['code'], boxes['text']
    # Both as they are, the code at 4 dots a module
    assert (image[2] - image[0], image[3] - image[1]) == (120, 80)
    assert (code[2] - code[0], code[3] - code[1]) == (100, 100)
    # The text between them, at the distance from both
    assert _distance(image, text, OTHER[side]) >= gap and _distance(text, code, OTHER[side]) >= gap
    # Each at the margin on its side, all in the middle across
    edge = {'left': lambda box: box[0] == 20, 'right': lambda box: box[2] == width - 20,
            'above': lambda box: box[1] == 20, 'below': lambda box: box[3] == height - 20}
    assert edge[side](image) and edge[OTHER[side]](code)
    middle = (lambda box: abs(box[1] + box[3] - height)) if side in ('left', 'right') else (lambda box: abs(box[0] + box[2] - width))
    assert middle(image) <= 1 and middle(code) <= 1 and middle(text) <= 12
    # Endless tape grows along to hold them, side by side or one after the other
    along = width if orientation == 'rotated' else height
    extent = (lambda box: box[2] - box[0]) if orientation == 'rotated' else (lambda box: box[3] - box[1])
    if (side in ('left', 'right')) == (orientation == 'rotated'):
        assert extent(image) + extent(text) + extent(code) + 2 * gap + 40 <= along <= extent(image) + extent(text) + extent(code) + 2 * gap + 40 + 20
    else:
        # The room at the edges of the letters
        assert max(extent(image), extent(text), extent(code)) + 40 <= along <= max(extent(image), extent(text), extent(code)) + 40 + 20


@pytest.mark.parametrize('orientation', ['rotated', 'standard'])
def test_the_image_is_left_of_the_text_without_a_position(tmp_path, orientation):
    # Also across the tape, where an image alone is above the text
    client = make_client(tmp_path)
    response = _preview(client, _color_label(orientation=orientation))
    assert response.headers['X-Label-Image-Position'] == 'left'
    boxes = _boxes(response.data)
    assert boxes['image'][2] < boxes['text'][0] < boxes['text'][2] < boxes['code'][0]


@pytest.mark.parametrize('values, colors', [
    # Black and red tape: the code in red, the image in red and black
    ({'label_size': '62red', 'print_color': 'red', 'image_mode': 'red_and_black'}, {'code': (255, 0, 0), 'image': (152, 0, 0)}),
    # Black tape: in black and gray
    ({'label_size': '62', 'print_color': 'red', 'image_mode': 'grayscale'}, {'code': (0, 0, 0), 'image': (76, 76, 76)}),
    # Color tape: any color
    ({}, {'code': (0, 0, 255), 'image': (255, 0, 0)}),
])
def test_image_and_code_keep_the_colors_the_tape_shows(tmp_path, values, colors):
    client = make_client(tmp_path)
    data = _color_label(**{'image_position': 'left', 'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Pantry', 'size': '60'}]), **values})
    response = _preview(client, data)
    assert response.status_code == 200
    preview = Image.open(io.BytesIO(response.data))
    # In color where the label has any
    assert preview.mode == ('L' if values.get('label_size') == '62' else 'RGB')
    preview = preview.convert('RGB')
    # The image at the margin, the code at the other one, with a module of a
    # finder pattern in its upper corners
    width, height = preview.size
    assert preview.getpixel((20, height // 2)) == colors['image']
    assert preview.getpixel((width - 21, (height - 100) // 2)) == colors['code']


def test_the_code_is_that_of_its_content_and_else_of_the_text(tmp_path):
    client = make_client(tmp_path)

    def code_of(**values):
        response = _preview(client, _color_label(image_position='left', **values))
        assert response.status_code == 200
        # Its finder patterns are in three of its corners, its box is that
        # of the whole code
        return Image.open(io.BytesIO(response.data)).convert('RGB').crop(_boxes(response.data)['code'])

    def expected(text):
        return _code_image('QR', text, 'M', 4, (0, 0, 255)).convert('RGB')
    assert code_of().tobytes() == expected(URL).tobytes()
    # Without content of its own it holds the text, as next to text alone
    assert code_of(code_text='').tobytes() == expected('Pantry').tobytes()
    # Its templates are filled in like those of the text
    assert code_of(code_text='Box {{counter}}').tobytes() == expected('Box 1').tobytes()


def test_a_fitted_image_and_code_take_a_third_of_a_die_cut_label_each(tmp_path):
    # Next to text alone the image takes half of it, the text and the code
    # would have no room
    client = make_client(tmp_path)
    data = {'label_size': '62x29', 'orientation': 'standard', 'print_type': 'image_qrcode_text', 'image_mode': 'grayscale', 'image_fit': '1',
            'code_text': URL, 'image_position': 'left', 'text_fit': 'shrink', 'border_thickness': '0',
            'margin_top': '20', 'margin_bottom': '20', 'margin_left': '20', 'margin_right': '20',
            'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili con carne', 'size': '60'}])}
    response = _preview(client, dict(data, image=(io.BytesIO(_png(Image.new('L', (100, 100), 128))), 'gray.png', 'image/png')))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    preview = Image.open(io.BytesIO(response.data)).convert('L')
    width, height = preview.size
    third = (width - 40) // 3
    # Without the edges of the letters in the same gray
    inner = preview.point(lambda v: 255 if v == 128 else 0).filter(ImageFilter.MinFilter(5)).getbbox()
    image = (inner[0] - 2, inner[1] - 2, inner[2] + 2, inner[3] + 2)
    assert image[0] == 20 and image[2] - image[0] == third
    # The code as large, at the other margin, the text between them
    dark = preview.point(lambda v: 255 if v < 100 else 0)
    assert dark.getbbox()[2] == width - 20
    assert dark.crop((width - 20 - third, 0, width, height)).getbbox() == (0, (height - third) // 2, third, (height - third) // 2 + third)
    assert dark.crop((image[2], 0, width - 20 - third, height)).getbbox() is not None


def test_a_fitted_image_and_code_fill_the_tape_they_run_along(tmp_path):
    client = make_client(tmp_path)
    response = _preview(client, _color_label(image_fit='1', image_position='left'))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    boxes = _boxes(response.data)
    assert boxes['image'][3] - boxes['image'][1] == 617 - 40
    # The modules of the code become larger, it keeps two of them away
    assert boxes['code'][3] - boxes['code'][1] == 617 - 40
    assert int(response.headers['X-Label-Image-Text-Gap']) == round(2 * 4 * 577 / 100)


def test_the_scale_rotation_and_crop_are_those_of_the_image(tmp_path):
    client = make_client(tmp_path)
    framed = Image.new('RGB', (160, 120), 'white')
    framed.paste(_red_image(), (20, 20))
    data = _color_label(image_position='left', image_scaling_factor='50', image_rotation='90', image_crop='1',
                        image=(io.BytesIO(_png(framed)), 'framed.png', 'image/png'))
    response = _preview(client, data)
    assert response.status_code == 200
    boxes = _boxes(response.data)
    # Cropped to the red, turned and half as large
    assert (boxes['image'][2] - boxes['image'][0], boxes['image'][3] - boxes['image'][1]) == (40, 60)
    # The code keeps the size of its modules
    assert (boxes['code'][2] - boxes['code'][0], boxes['code'][3] - boxes['code'][1]) == (100, 100)


def test_the_distance_is_kept_on_both_sides_of_the_text(tmp_path):
    client = make_client(tmp_path)
    widths, gaps = {}, {}
    for gap in ('', '0', '60'):
        response = _preview(client, _color_label(image_position='left', image_text_gap=gap))
        assert response.status_code == 200
        widths[gap] = Image.open(io.BytesIO(response.data)).width
        gaps[gap] = response.headers.get('X-Label-Image-Text-Gap')
    assert widths['60'] - widths['0'] == 2 * 60
    # Without a distance two fifths of the text, a QR code keeps two of its
    # modules where that is more
    assert gaps == {'': '24', '0': None, '60': '60'}
    response = _preview(client, _color_label(image_position='left', qrcode_size='20'))
    assert response.headers['X-Label-Image-Text-Gap'] == '40'
    boxes = _boxes(response.data)
    assert boxes['text'][0] - boxes['image'][2] >= 40 and boxes['code'][0] - boxes['text'][2] >= 40


def test_without_text_the_image_and_the_code_keep_the_distance_to_each_other(tmp_path):
    client = make_client(tmp_path)
    response = _preview(client, _color_label(image_position='right', image_text_gap='30', text='[]'))
    assert response.status_code == 200
    assert response.headers.get('X-Label-Warning') is None
    # The position applies without text as well
    assert response.headers['X-Label-Image-Position'] == 'right'
    boxes = _boxes(response.data)
    assert boxes['code'][0] == 20 and boxes['image'][0] - boxes['code'][2] == 30
    assert Image.open(io.BytesIO(response.data)).width == 20 + 100 + 30 + 120 + 20


@pytest.mark.parametrize('values, warning', [
    # Text drawn as set over what is right of it
    ({'label_size': '62x29', 'orientation': 'standard', 'text_fit': 'none', 'qrcode_size': '9',
      'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili con car', 'size': '60'}])},
     'The text does not fit next to the code, it overlaps it.'),
    ({'label_size': '62x29', 'orientation': 'standard', 'text_fit': 'none', 'qrcode_size': '9', 'image_position': 'right',
      'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Chili con car', 'size': '60'}])},
     'The text does not fit next to the image, it overlaps it.'),
    # Image and code too large for the label without text
    ({'label_size': '62x29', 'orientation': 'standard', 'qrcode_size': '10', 'text': '[]', 'image': (io.BytesIO(_png(_red_image((500, 100)))), 'red.png', 'image/png')},
     'The code does not fit next to the image, it overlaps it.'),
    # A code higher than the tape
    ({'qrcode_size': '30'}, 'The code does not fit on the label, part of it is cut off.'),
])
def test_what_does_not_fit_is_warned_about(tmp_path, values, warning):
    client = make_client(tmp_path)
    data = _color_label(**{'image_mode': 'grayscale', **values})
    response = _preview(client, data)
    assert response.status_code == 200
    assert warning in response.headers.get('X-Label-Warning', '')


def test_a_label_without_an_image_is_refused(tmp_path):
    client = make_client(tmp_path)
    data = _color_label(label_size='62')
    del data['image']
    for url in ('/labeldesigner/api/preview', '/labeldesigner/api/print'):
        response = client.post(url, data=data)
        assert response.status_code == 400
        assert response.get_json()['message'] == 'The label has no image'


@pytest.mark.parametrize('barcode_type, message', [('QR', 'The QR code is empty'), ('CODE128', 'The barcode is empty')])
def test_a_label_without_content_of_its_code_is_refused(tmp_path, barcode_type, message):
    # Without content of its own the code holds the text, without text it
    # has nothing
    client = make_client(tmp_path)
    response = _preview(client, _color_label(code_text='', barcode_type=barcode_type, text='[]'))
    assert response.status_code == 400
    assert response.get_json()['message'] == message


def test_a_die_cut_label_is_not_turned_for_its_image(tmp_path):
    # An image alone is turned to come out largest, beside a code it stays
    client = make_client(tmp_path)
    data = {'label_size': '103x164', 'orientation': 'standard', 'print_type': 'image_qrcode_text', 'image_fit': '1', 'code_text': URL, 'text': '[]'}
    files = {'image': _upload(Image.new('L', (1200, 1800), 'black'), 'tall.png')}
    with client.application.test_request_context():
        label = create_label_from_request(data, files)
    assert label.label_orientation == LabelOrientation.STANDARD and not label.auto_orientation
    assert label.label_content == LabelContent.IMAGE_GRAYSCALE


def test_reference_image(tmp_path):
    client = make_client(tmp_path)
    data = {'label_size': '62', 'orientation': 'rotated', 'print_type': 'image_qrcode_text', 'image_mode': 'grayscale', 'image_fit': '1',
            'code_text': URL, 'qrcode_size': '6', 'border_thickness': '0', 'margin_top': '24', 'margin_bottom': '24', 'margin_left': '24', 'margin_right': '24',
            'text': json.dumps([{'font': 'DejaVu Sans,Bold', 'text': 'Pantry', 'size': '120', 'align': 'left'},
                                {'font': 'DejaVu Sans,Book', 'text': 'Shelf 3', 'size': '60', 'align': 'left'}]),
            'image': (io.BytesIO(_png(Image.radial_gradient('L').resize((200, 200)))), 'round.png', 'image/png')}
    response = _preview(client, data)
    assert response.status_code == 200 and response.headers.get('X-Label-Warning') is None
    verify_image(response.data, 'image_qrcode_text.png')


def test_the_layout_is_described_in_the_api(tmp_path):
    client = make_client(tmp_path)
    spec = client.get('/labeldesigner/api/openapi.json').get_json()
    assert spec['components']['schemas']['Label']['properties']['print_type']['enum'] == ['text', 'qrcode', 'qrcode_text', 'image', 'image_qrcode_text']


def test_print_has_image_and_code_on_every_label(tmp_path, monkeypatch):
    # Several files print a label each, all with the code
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: '')
    client = make_client(tmp_path)
    data = _color_label(label_size='62', image_position='left', printer='simulation',
                        image=[(io.BytesIO(_png(_red_image())), 'one.png', 'image/png'), (io.BytesIO(_png(_red_image((60, 60)))), 'two.png', 'image/png')])
    response = client.post('/labeldesigner/api/print', data=data, content_type='multipart/form-data')
    assert response.get_json() == {'success': True}
    assert len(queued) == 2
    for label, size in zip(queued, ((120, 80), (60, 60))):
        image = label.generate()
        assert label.image_side == 'left' and label.code_text == URL
        assert image.convert('L').crop((20, 0, 20 + size[0], image.height)).getbbox() is not None


def test_saved_label_keeps_its_layout(tmp_path):
    client = make_client(tmp_path, empty_repo=True)
    data = {k: v for k, v in _color_label(image_position='right').items() if k != 'image'}
    data['text'] = json.loads(data['text'])
    body = {**data, 'name': 'Pantry', 'image_data': base64.b64encode(_png(_red_image())).decode('ascii'), 'image_mime': 'image/png', 'image_name': 'red.png'}
    response = client.post(SAVE_URL, json=body)
    assert response.get_json() == {'success': True, 'name': 'Pantry.json'}
    loaded = client.get('/labeldesigner/api/repository/load', query_string={'name': 'Pantry.json'}).get_json()
    assert (loaded['print_type'], loaded['image_position'], loaded['code_text']) == ('image_qrcode_text', 'right', URL)
    assert base64.b64decode(loaded['image_data']) == _png(_red_image())
    # Previewed and printed as it was made
    stored = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Pantry.json'})
    made = _preview(client, _color_label(image_position='right'))
    assert stored.status_code == 200 and stored.data == made.data
    assert stored.headers['X-Label-Image-Position'] == 'right'
