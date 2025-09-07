import os
from fontTools.ttLib import TTFont
from collections import defaultdict


class Fonts:
    def __init__(self):
        self.fonts = defaultdict(dict)

    def scan_global_fonts(self, additional_path=''):
        """
        Scan for TTF/OTF fonts using pure Python (fontTools).
        :param additional_path: Directory to search in addition to
            common system font paths.
        """
        search_paths = [
            '/usr/share/fonts', '/usr/local/share/fonts', os.path.expanduser('~/.fonts'),
            os.path.expanduser('~/.local/share/fonts'), '/Library/Fonts', '/System/Library/Fonts',
            'C:\\Windows\\Fonts'
        ]
        if len(additional_path) > 0:
            search_paths.extend(additional_path)

        font_exts = ('.ttf', '.otf')
        for base_path in search_paths:
            if not os.path.isdir(base_path):
                continue
            for root, _, files in os.walk(base_path):
                for file in files:
                    if file.lower().endswith(font_exts):
                        font_path = os.path.join(root, file)
                        try:
                            font = TTFont(font_path)
                            # Get family and style from name table
                            family = None
                            style = None
                            for record in font['name'].names:
                                if record.nameID == 1 and not family:
                                    family = record.toStr()
                                if record.nameID == 2 and not style:
                                    style = record.toStr()
                                if family and style:
                                    break
                            if family and style:
                                self.fonts[family][style] = font_path
                        except Exception:
                            continue

        # Consolidate fonts: Search for fonts that have children, e.g.
        # "DejaVu Sans" and "DejaVu Sans Condensed" and move children
        # under the parent font, adding them as a style instead
        for family, styles in list(self.fonts.items()):
            for other_family in list(self.fonts.keys()):
                if family != other_family and family in other_family:
                    extra_style = other_family.replace(family + ' ', '')
                    for style in self.fonts[other_family].keys():
                        new_style = extra_style + ((' / ' + style) if style != 'Regular' else '')
                        self.fonts[family][new_style] = self.fonts[other_family][style]

                    # Remove the child
                    del self.fonts[other_family]

    def fontfamilies(self):
        """Return a sorted list of font family names."""
        return sorted(self.fonts.keys(), key=str.lower)

    def fontlist(self):
        """Return a sorted list of font styles for each family."""
        fontlist = []
        for family, variants in self.fonts.items():
            style_keys = list(variants.keys())
            # Prioritize 'Book' or 'Regular' in sorting if present
            prioritized = [s for s in style_keys if s.lower() in ('book', 'regular')]
            others = sorted([s for s in style_keys if s.lower() not in ('book', 'regular')], key=str.lower)
            sorted_styles = prioritized + others
            fontlist.append({
                'family': family,
                'styles': sorted_styles
            })
        return fontlist

    def fonts_available(self):
        """Return True if any fonts are available, else False."""
        return bool(self.fonts)
