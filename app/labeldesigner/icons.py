"""Material Design Icons (pictogrammers.com) for {{mdi:<name>}} in label
texts. The font, its license and the names are in app/static/mdi, see
tools/mdi_icons.py."""
import functools
import json
import os

MDI_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'static', 'mdi')
FONT_PATH = os.path.join(MDI_DIR, 'materialdesignicons-webfont.ttf')


@functools.cache
def _icons() -> dict:
    with open(os.path.join(MDI_DIR, 'icons.json'), encoding='utf-8') as f:
        return json.load(f)['icons']


def icon(name: str) -> str | None:
    """The character of the icon, None for an unknown name."""
    code = _icons().get(name)
    return chr(int(code, 16)) if code else None


def is_icon(char: str) -> bool:
    """Whether the character is drawn with the icon font: the icons are in
    Supplementary Private Use Area-A, which no text font uses."""
    return 0xF0000 <= ord(char) <= 0xFFFFD
