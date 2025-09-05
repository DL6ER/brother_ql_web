import unittest
from app.fonts import Fonts


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
        self.assertEqual(font_list, [
           'DejaVu Math TeX Gyre',
           'DejaVu Sans',
           'DejaVu Sans Mono',
           'DejaVu Serif',
           'Droid Sans',
           'Droid Sans Mono',
           'Droid Serif',
           'FreeMono',
           'FreeSans',
           'FreeSerif',
           'Inconsolata',
           'Liberation Mono',
           'Liberation Sans',
           'Liberation Sans Narrow',
           'Liberation Serif',
           'Ligconsolata',
           'Noto Sans',
           'Noto Sans Math',
           'Noto Sans Mono',
           'Noto Sans Symbols',
           'Noto Sans Symbols 2',
           'Noto Serif',
           'Noto Serif Display'
        ])

    def test_fontstyles(self):
        self.fonts.scan_global_fonts()
        font_styles = self.fonts.fontstyles()
        self.assertEqual(font_styles, {'DejaVu Serif': ['bold', 'bold italic', 'book', 'condensed', 'condensed bold', 'condensed bold italic', 'condensed italic', 'italic'], 'Noto Sans Symbols': ['black', 'bold', 'extrabold', 'extralight', 'light', 'medium', 'regular', 'semibold', 'thin'], 'Liberation Serif': ['bold', 'bold italic', 'italic', 'regular'], 'Inconsolata': ['black', 'bold', 'condensed', 'condensed black', 'condensed bold', 'condensed extrabold', 'condensed extralight', 'condensed light', 'condensed medium', 'condensed semibold', 'expanded', 'expanded black', 'expanded bold', 'expanded extrabold', 'expanded extralight', 'expanded light', 'expanded medium', 'expanded semibold', 'extra condensed', 'extra condensed black', 'extra condensed bold', 'extra condensed extrabold', 'extra condensed extralight', 'extra condensed light', 'extra condensed medium', 'extra condensed semibold', 'extra expanded', 'extra expanded black', 'extra expanded bold', 'extra expanded extrabold', 'extra expanded extralight', 'extra expanded light', 'extra expanded medium', 'extra expanded semibold', 'extrabold', 'extralight', 'light', 'medium', 'regular', 'semi condensed', 'semi condensed black', 'semi condensed bold', 'semi condensed extrabold', 'semi condensed extralight', 'semi condensed light', 'semi condensed medium', 'semi condensed semibold', 'semi expanded', 'semi expanded black', 'semi expanded bold', 'semi expanded extrabold', 'semi expanded extralight', 'semi expanded light', 'semi expanded medium', 'semi expanded semibold', 'semibold', 'ultra condensed', 'ultra condensed black', 'ultra condensed bold', 'ultra condensed extrabold', 'ultra condensed extralight', 'ultra condensed light', 'ultra condensed medium', 'ultra condensed semibold', 'ultra expanded', 'ultra expanded black', 'ultra expanded bold', 'ultra expanded extrabold', 'ultra expanded extralight', 'ultra expanded light', 'ultra expanded medium', 'ultra expanded semibold'], 'Liberation Sans': ['bold', 'bold italic', 'italic', 'regular'], 'Liberation Mono': ['bold', 'bold italic', 'italic', 'regular'], 'Noto Sans Mono': ['bold', 'regular'], 'DejaVu Sans Mono': ['bold', 'bold oblique', 'book', 'oblique'], 'Noto Sans Math': ['regular'], 'DejaVu Sans': ['bold', 'bold oblique', 'book', 'condensed', 'condensed bold', 'condensed bold oblique', 'condensed oblique', 'extralight', 'oblique'], 'Droid Serif': ['bold', 'bold italic', 'italic', 'regular'], 'FreeSerif': ['bold', 'bold italic', 'italic', 'regular'], 'Noto Sans': ['bold', 'bold italic', 'italic', 'regular'], 'FreeMono': ['bold', 'bold oblique', 'oblique', 'regular'], 'Liberation Sans Narrow': ['bold', 'bold italic', 'italic', 'regular'], 'Noto Serif': ['bold', 'bold italic', 'italic', 'regular'], 'Noto Serif Display': ['bold', 'bold italic', 'italic', 'regular'], 'Droid Sans': ['bold', 'regular'], 'Noto Sans Symbols 2': ['regular'], 'Ligconsolata': ['bold', 'regular'], 'DejaVu Math TeX Gyre': ['regular'], 'FreeSans': ['bold', 'bold oblique', 'oblique', 'regular'], 'Droid Sans Mono': ['regular']})


if __name__ == '__main__':
    unittest.main()
