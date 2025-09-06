import unittest
from app.fonts import Fonts
import json


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
        self.fonts = Fonts()

    def test_fonts_available_empty(self):
        # Should be False when no fonts are loaded
        self.fonts.fonts.clear()
        self.assertFalse(self.fonts.fonts_available())

    def test_fonts_available_nonempty(self):
        # Should be True when fonts are loaded
        self.fonts.fonts['TestFont']['Regular'] = '/path/to/font.ttf'
        self.assertTrue(self.fonts.fonts_available())

    def test_fontlist(self):
        self.fonts.scan_global_fonts()
        font_list = self.fonts.fontlist()
        expected_font_list = read_testfile('font_list', font_list)
        self.assertEqual(font_list, expected_font_list)

    def test_fontstyles(self):
        self.fonts.scan_global_fonts()
        font_styles = self.fonts.fontstyles()
        expected_font_styles = read_testfile('font_styles', font_styles)
        self.assertEqual(font_styles, expected_font_styles)


if __name__ == '__main__':
    unittest.main()
