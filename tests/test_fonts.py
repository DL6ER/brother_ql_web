import os
import struct
import unittest
from app.fonts import Fonts, font_names
import json
import logging

import pytest


def read_testfile(file_path, data):
    file_path = f'tests/fixtures/{file_path}.json'
    try:
        with open(file_path, 'r') as f:
            return json.load(f)
    except FileNotFoundError:
        with open(file_path, 'w') as f:
            json.dump(data, f, indent=4)
        return data


class TestFonts(unittest.TestCase):
    def setUp(self):
        logger = logging.getLogger('TestFonts')
        logging.basicConfig(level=logging.DEBUG)
        self.fonts = Fonts(logger)

    def test_fonts_available_empty(self):
        # Should be False when no fonts are loaded
        self.fonts.fonts.clear()
        self.assertFalse(self.fonts.fonts_available())

    def test_fonts_available_nonempty(self):
        # Should be True when fonts are loaded
        self.fonts.fonts['TestFont']['Regular'] = '/path/to/font.ttf'
        self.assertTrue(self.fonts.fonts_available())

    def test_fontfamilies(self):
        font_families = self.fonts.fontfamilies()
        expected_font_families = read_testfile('font_families', font_families)
        self.assertEqual(font_families, expected_font_families)

    def test_fontlist(self):
        font_styles = self.fonts.fontlist()
        expected_font_styles = read_testfile('font_list', font_styles)
        self.assertEqual(font_styles, expected_font_styles)


def test_font_folder_is_scanned(monkeypatch):
    scanned = []
    monkeypatch.setattr(os.path, 'isdir', lambda path: scanned.append(path) or False)
    Fonts(logging.getLogger('TestFonts'), additional_path='/opt/myfonts')
    assert '/opt/myfonts' in scanned
    assert '/' not in scanned


def test_font_folder_list_is_scanned(monkeypatch):
    scanned = []
    monkeypatch.setattr(os.path, 'isdir', lambda path: scanned.append(path) or False)
    Fonts(logging.getLogger('TestFonts'), additional_path=['/opt/fonts', '/srv/fonts'])
    assert scanned[-2:] == ['/opt/fonts', '/srv/fonts']


def test_missing_default_style_keeps_family():
    family = Fonts(logging.getLogger('TestFonts')).fontlist()[-1]['family']
    fonts = Fonts(logging.getLogger('TestFonts'), default_family=family, default_style='No Such Style')
    assert fonts.get_default_font()[0] == family


def test_no_fonts_found(monkeypatch):
    monkeypatch.setattr(os.path, 'isdir', lambda path: False)
    fonts = Fonts(logging.getLogger('TestFonts'))
    assert not fonts.fonts_available()


def test_missing_default_font_is_replaced():
    fonts = Fonts(logging.getLogger('TestFonts'), default_family='No Such Font', default_style='Book')
    family, style = fonts.get_default_font()
    assert style in fonts.fonts.get(family, {})
    # Every worker process of a WSGI server has to pick the same font
    assert Fonts(logging.getLogger('TestFonts'), default_family='No Such Font').get_default_font() == (family, style)


def test_only_the_font_folder_without_system_fonts(monkeypatch):
    scanned = []
    monkeypatch.setattr(os.path, 'isdir', lambda path: scanned.append(path) or False)
    Fonts(logging.getLogger('TestFonts'), additional_path='/opt/myfonts', system_fonts=False)
    assert scanned == ['/opt/myfonts']


def sfnt(records, tables=(b'name',)):
    """A font file with only a table directory and a name table of the given
    (platform, encoding, name ID, bytes) records"""
    strings = b''.join(raw for *_, raw in records)
    name = struct.pack('>HHH', 0, len(records), 6 + 12 * len(records))
    start = 0
    for platform, encoding, name_id, raw in records:
        name += struct.pack('>6H', platform, encoding, 0x409 if platform == 3 else 0, name_id, len(raw), start)
        start += len(raw)
    name += strings
    offset = 12 + 16 * len(tables)
    directory = b''.join(struct.pack('>4sIII', tag, 0, offset, len(name) if tag == b'name' else 0) for tag in tables)
    return struct.pack('>IHHHH', 0x10000, len(tables), 0, 0, 0) + directory + name


def win(name_id, text):
    return 3, 1, name_id, text.encode('utf_16_be')


def test_font_names_of_an_installed_font():
    path = Fonts(logging.getLogger('TestFonts')).get_path('DejaVu Sans,Bold')
    assert font_names(path) == ('DejaVu Sans', 'Bold')


@pytest.mark.parametrize('records, names', [
    ([win(1, 'Family'), win(2, 'Style')], ('Family', 'Style')),
    # The first record of each wins, Macintosh Roman as well
    ([(1, 0, 1, 'Café'.encode('mac_roman')), (1, 0, 2, b'Bold'), win(1, 'Other'), win(2, 'Other')], ('Café', 'Bold')),
    # Empty records and those in other encodings are left out
    ([win(1, ''), (3, 2, 1, b'\x82\xa0'), (1, 1, 2, b'\x82\xa0'), win(1, 'Family'), win(2, 'Style')], ('Family', 'Style')),
    ([(0, 3, 1, 'Unicode'.encode('utf_16_be')), win(4, 'Full name')], ('Unicode', None)),
    ([], (None, None)),
])
def test_font_names(tmp_path, records, names):
    path = tmp_path / 'font.ttf'
    path.write_bytes(sfnt(records))
    assert font_names(path) == names


def test_font_names_without_a_name_table(tmp_path):
    path = tmp_path / 'font.ttf'
    path.write_bytes(sfnt([win(1, 'Family')], tables=(b'cmap',)))
    assert font_names(path) == (None, None)


@pytest.mark.parametrize('data', [b'', b'\x00\x01\x00\x00\x00\x01', sfnt([win(1, 'Family')])[:-4],
                                  sfnt([(3, 1, 1, b'\xd8\x00')])])
def test_broken_fonts_raise(tmp_path, data):
    """The scanner leaves out fonts it cannot read"""
    path = tmp_path / 'font.ttf'
    path.write_bytes(data)
    with pytest.raises((ValueError, struct.error)):
        font_names(path)


def test_broken_fonts_are_left_out(tmp_path):
    (tmp_path / 'broken.ttf').write_bytes(b'not a font')
    (tmp_path / 'good.otf').write_bytes(sfnt([win(1, 'Good'), win(2, 'Regular')]))
    fonts = Fonts(logging.getLogger('TestFonts'), additional_path=str(tmp_path), system_fonts=False)
    assert fonts.fonts == {'Good': {'Regular': str(tmp_path / 'good.otf')}}


if __name__ == '__main__':
    unittest.main()
