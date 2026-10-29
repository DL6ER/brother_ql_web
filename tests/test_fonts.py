import os
import unittest
from app.fonts import Fonts
import json
import logging


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


if __name__ == '__main__':
    unittest.main()
