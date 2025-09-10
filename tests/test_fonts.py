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


if __name__ == '__main__':
    unittest.main()
