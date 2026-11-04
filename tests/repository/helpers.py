"""Helpers shared by the tests of saved labels."""

import base64
import io

from PIL import Image


TEXT = [{'font': 'DejaVu Sans,Book', 'text': 'x', 'size': '40'}]


SAVE_URL = '/labeldesigner/api/repository/save'


def _png_b64(color: str) -> str:
    buffer = io.BytesIO()
    Image.new('RGB', (40, 40), color).save(buffer, 'PNG')
    return base64.b64encode(buffer.getvalue()).decode('ascii')


# A label with blanks as the label designer saves it, with the settings of
# the blanks in "fields"
BLANK_LABEL = {
    'name': 'Freezer', 'label_size': '62', 'print_type': 'qrcode_text', 'code_text': 'freezer/{{blank:What}}',
    'text': [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': '{{blank:What}}'},
             {'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Box {{blank:Box}}, {{blank:Frozen on}}'}],
    'fields': {'Box': {'type': 'number', 'default': '1'}, 'Frozen on': {'type': 'date', 'default': '2026-11-01'}},
}
