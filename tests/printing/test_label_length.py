"""Labels longer than a printer can print are refused before they are sent."""

import io

import pytest
from PIL import Image

from app.labeldesigner.drivers.brother.commands import Raster
from app.labeldesigner.drivers.brother.labels import BY_ID
from app.labeldesigner.drivers.brother.raster import convert
from tests.helpers import make_client


@pytest.mark.parametrize('model, label_size, longest, millimeters', [
    ('QL-800', '62', 11811, 1000),
    ('QL-1100', '102', 35434, 3000),
    ('PT-E550W', 'pt12', 14172, 2000),
    ('PT-P900W', 'pt36', 28346, 2000),
])
def test_longest_label_of_a_model(model, label_size, longest, millimeters):
    width = BY_ID[label_size].dots_printable[0]
    convert(Raster(model), [Image.new('1', (width, longest), 1)], label_size)
    with pytest.raises(ValueError, match=f'^Labels on the {model} can be at most {millimeters} mm long$'):
        convert(Raster(model), [Image.new('1', (width, longest + 1), 1)], label_size)


def test_longest_label_at_600_dpi():
    # Twice the lines for the same length
    convert(Raster('QL-800'), [Image.new('1', (2 * 696, 2 * 11811), 1)], '62', dpi_600=True)
    with pytest.raises(ValueError, match='at most 1000 mm long'):
        convert(Raster('QL-800'), [Image.new('1', (2 * 696, 2 * 11812), 1)], '62', dpi_600=True)


def test_label_too_long_is_refused_with_its_reason(tmp_path):
    client = make_client(tmp_path)
    image = io.BytesIO()
    Image.new('1', (696, 30000), 1).save(image, 'PNG')
    image.seek(0)
    response = client.post('/labeldesigner/api/print', data={
        'printer': 'simulation', 'model': 'QL-800', 'label_size': '62', 'print_type': 'image',
        'image': (image, 'long.png')}, content_type='multipart/form-data')
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Labels on the QL-800 can be at most 1000 mm long'
