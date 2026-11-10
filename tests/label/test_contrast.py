"""Stronger contrast for light drawings on the tapes of the VC-500W: the
image conversion, how light images are found, and the preview."""

import io

import pytest
from PIL import Image, ImageDraw

from app.utils import looks_faint, stronger_contrast

from tests.helpers import _upload, make_client

WHITE = (255, 255, 255)


def _drawing() -> Image.Image:
    """Like a drawing in light washes: a pale wash with thin lines on white."""
    image = Image.new('RGB', (300, 400), WHITE)
    draw = ImageDraw.Draw(image)
    draw.ellipse((40, 40, 260, 360), fill=(240, 215, 190))
    for y in range(80, 340, 40):
        draw.line((70, y, 230, y), fill=(170, 160, 150), width=2)
    return image


def _photo() -> Image.Image:
    """Like a photo: dark to light all over."""
    image = Image.new('RGB', (300, 400))
    for y in range(400):
        ImageDraw.Draw(image).line((0, y, 299, y), fill=(20 + y // 2, 30 + y // 2, 25 + y // 2))
    return image


def test_stronger_contrast_keeps_white_and_black():
    image = Image.new('RGB', (400, 100), WHITE)
    image.paste((0, 0, 0), (0, 0, 100, 100))
    image.paste((200, 200, 200), (100, 0, 200, 100))
    image.paste((230, 150, 150), (200, 0, 300, 100))
    out = stronger_contrast(image)
    assert out.getpixel((50, 50)) == (0, 0, 0) and out.getpixel((350, 50)) == WHITE
    # Light gray gets darker and stays gray, pink darker and more colorful
    r, g, b = out.getpixel((150, 50))
    assert 140 < r < 160 and max(r, g, b) - min(r, g, b) <= 1
    r, g, b = out.getpixel((250, 50))
    assert 185 < r < 205 and r - g > 110 and abs(g - b) < 6


def test_paper_keeps_its_grain():
    # Near white paper stays as it is, darkened its grain would print as specks
    image = Image.new('RGB', (100, 100), (255, 253, 248))
    image.paste((252, 250, 243), (0, 0, 50, 100))
    image.paste((200, 200, 200), (40, 40, 60, 60))
    out = stronger_contrast(image)
    for xy in ((10, 10), (90, 90)):
        assert all(abs(a - b) <= 3 for a, b in zip(out.getpixel(xy), image.getpixel(xy)))
    assert out.getpixel((50, 50))[0] < 160


def test_stronger_contrast_blackens_only_the_darkest_bits():
    # Without dark parts nothing becomes black: light gray stays clearly gray
    image = Image.new('RGB', (100, 100), (200, 200, 200))
    assert 110 < stronger_contrast(image).getpixel((50, 50))[0] < 140
    # The darkest 0.5 % of a drawing become black, its lines
    image.paste((60, 60, 60), (0, 0, 100, 1))
    assert stronger_contrast(image).getpixel((50, 0)) == (0, 0, 0)


def test_stronger_contrast_keeps_transparency():
    image = Image.new('RGBA', (100, 100), (0, 0, 0, 0))
    image.paste((200, 200, 200, 255), (20, 20, 80, 80))
    out = stronger_contrast(image)
    assert out.mode == 'RGBA' and out.getchannel('A').getextrema() == (0, 255)
    assert out.getpixel((0, 0))[3] == 0 and out.getpixel((50, 50))[3] == 255
    # The transparent part does not count as dark, the gray is not black
    assert 110 < out.getpixel((50, 50))[0] < 140


def test_light_drawings_look_faint_photos_do_not():
    assert looks_faint(_drawing())
    assert not looks_faint(_photo())
    # Bold black lines around flat colors print strong enough
    bold = _drawing()
    ImageDraw.Draw(bold).ellipse((40, 40, 260, 360), outline=(0, 0, 0), width=14)
    assert not looks_faint(bold)
    # A blank image or a speck on it is nothing to judge
    assert not looks_faint(Image.new('RGB', (300, 400), WHITE))
    speck = Image.new('RGB', (300, 400), WHITE)
    speck.paste((235, 220, 200), (0, 0, 20, 20))
    assert not looks_faint(speck)


def test_a_cut_out_drawing_looks_faint():
    # The transparent background is white paper, whatever color it hides
    image = Image.new('RGBA', (300, 400), (0, 0, 0, 0))
    image.paste(_drawing().convert('RGBA'), (0, 0), _drawing().convert('L').point(lambda v: 255 if v < 250 else 0))
    assert looks_faint(image)


def _preview(client, image, label_size='vc25', **options):
    data = {'print_type': 'image', 'label_size': label_size, 'image_fit': '1', 'text': '[]', **options,
            'image': _upload(image, 'drawing.png')}
    response = client.post('/labeldesigner/api/preview', data=data)
    assert response.status_code == 200
    return response


def _middle(response):
    image = Image.open(io.BytesIO(response.data)).convert('RGB')
    return image.getpixel((image.width // 2, image.height // 2))


def test_preview_recommends_and_shows_stronger_contrast(tmp_path):
    client = make_client(tmp_path)
    plain = _preview(client, _drawing())
    assert plain.headers.get('X-Image-Faint') == '1'
    strong = _preview(client, _drawing(), image_contrast='1')
    assert strong.headers.get('X-Image-Faint') is None
    assert sum(_middle(strong)) < sum(_middle(plain)) - 100
    # Photos print as they are
    assert _preview(client, _photo()).headers.get('X-Image-Faint') is None


@pytest.mark.parametrize('label_size', ['62', '62red'])
def test_other_tapes_ignore_stronger_contrast(tmp_path, label_size):
    client = make_client(tmp_path)
    plain = _preview(client, _drawing(), label_size, image_mode='grayscale')
    strong = _preview(client, _drawing(), label_size, image_mode='grayscale', image_contrast='1')
    assert plain.headers.get('X-Image-Faint') is None
    assert strong.data == plain.data


def test_stronger_contrast_is_saved_with_the_label(tmp_path):
    client = make_client(tmp_path)
    client.application.config['LABEL_REPOSITORY_DIR'] = str(tmp_path)
    label = {'name': 'Drawing.json', 'label_size': 'vc25', 'print_type': 'text', 'text': [{'text': 'Hi', 'size': 40}],
             'image_contrast': True}
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200
    stored = client.get('/labeldesigner/api/repository/load?name=Drawing.json').get_json()
    assert stored['image_contrast'] == '1'
