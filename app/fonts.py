import os
import logging
import struct
from collections import defaultdict

# Name records in these (platform, encoding) pairs can be read: Unicode,
# Windows Unicode and Macintosh Roman
NAME_ENCODINGS = {**{(0, encoding): 'utf_16_be' for encoding in range(7)},
                  (3, 0): 'utf_16_be', (3, 1): 'utf_16_be', (3, 10): 'utf_16_be', (1, 0): 'mac_roman'}


class FontNotFound(LookupError):
    """A font given as "<family>,<style>" that is not installed."""


def font_names(path) -> tuple[str | None, str | None]:
    """Family and style of a TrueType or OpenType font: the first readable,
    non-empty records with name IDs 1 and 2 in its name table. A broken file
    raises ValueError or struct.error."""
    with open(path, 'rb') as fh:
        def read(offset: int, length: int) -> bytes:
            fh.seek(offset)
            data = fh.read(length)
            if len(data) < length:
                raise ValueError(f'Font file too short: {path}')
            return data

        tables = struct.unpack('>H', read(4, 2))[0]
        directory = read(12, 16 * tables)
        for i in range(tables):
            tag, _, offset, length = struct.unpack_from('>4sIII', directory, 16 * i)
            if tag == b'name':
                break
        else:
            return None, None
        table = read(offset, length)
    _, count, strings = struct.unpack_from('>HHH', table)
    names = {}
    for i in range(count):
        platform, encoding, _, name_id, size, start = struct.unpack_from('>6H', table, 6 + 12 * i)
        codec = NAME_ENCODINGS.get((platform, encoding))
        if name_id in (1, 2) and name_id not in names and codec:
            raw = table[strings + start:strings + start + size]
            if len(raw) < size:
                raise ValueError(f'Font name table too short: {path}')
            if text := raw.decode(codec):
                names[name_id] = text
    return names.get(1), names.get(2)


class Fonts:
    def __init__(self,
                 logger: logging.Logger,
                 default_family: str = 'DejaVu Serif',
                 default_style: str = 'Book',
                 additional_path: str | list[str] = '',
                 system_fonts: bool = True):
        self.fonts = defaultdict(dict)
        self.default_family = default_family
        self.default_style = default_style
        self.additional_path = additional_path

        # Scan for TTF/OTF fonts, see font_names().
        # :param additional_path: Directory (or list of directories) to search
        #     in addition to common system font paths.
        # :param system_fonts: Whether to search the system font paths at all.
        search_paths = [
            '/usr/share/fonts', '/usr/local/share/fonts', os.path.expanduser('~/.fonts'),
            os.path.expanduser('~/.local/share/fonts'), '/Library/Fonts', '/System/Library/Fonts',
            'C:\\Windows\\Fonts'
        ] if system_fonts else []
        # FONT_FOLDER may be a single folder or a list of folders
        if isinstance(additional_path, str):
            additional_path = [additional_path] if additional_path else []
        search_paths.extend(additional_path or [])

        font_exts = ('.ttf', '.otf')
        for base_path in search_paths:
            if not os.path.isdir(base_path):
                continue
            for root, _, files in os.walk(base_path):
                for file in files:
                    if file.lower().endswith(font_exts):
                        font_path = os.path.join(root, file)
                        try:
                            family, style = font_names(font_path)
                            if family and style:
                                self.fonts[family][style] = font_path
                        except Exception:
                            continue

        # Sort fonts alphabetically by family name
        self.fonts = defaultdict(dict, {k: self.fonts[k] for k in sorted(self.fonts.keys(), key=str.lower)})

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

        # Check if the default family/style is available, if not, pick the
        # first available one. The choice must not be random, otherwise every
        # worker process of a WSGI server could end up with another default.
        if default_family in self.fonts and default_style in self.fonts[default_family]:
            logger.debug(f"Selected the following default font: {default_family}")
        elif self.fonts:
            logger.warning('Could not find any of the default fonts. Choosing the first available one.')
            fontlist = self.fontlist()
            # Prefer another style of the configured family
            first = next((f for f in fontlist if f['family'] == default_family), fontlist[0])
            self.default_family = first['family']
            self.default_style = first['styles'][0]
            logger.warning(f'The default font is now set to: {self.default_family} ({self.default_style})')

    def get_default_font(self):
        """Return the default font family and style."""
        return (self.default_family, self.default_style)

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

    def get_path(self, font: str):
        if ',' not in font:
            raise LookupError(f'Font must be given as "<family>,<style>": {font}')
        family_name, style_name = font.split(",", 1)
        if family_name not in self.fonts:
            raise FontNotFound(f"Unknown font family: {family_name}")
        if style_name not in self.fonts[family_name]:
            raise FontNotFound(f"Unknown font style: {style_name} for font {family_name}")
        return self.fonts[family_name][style_name]
