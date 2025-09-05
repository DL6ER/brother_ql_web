import subprocess
import sys
from collections import defaultdict


class Fonts:
    def __init__(self):
        self.fonts = defaultdict(dict)

    def parse_fonts(self, raw):
        """
        Adds the found fonts to the fonts list.
        :param raw: CompletedProcess from subprocess.run
        :return: True if fonts were added, False otherwise
        """
        if raw.returncode != 0:
            sys.stderr.write('Error occurred while processing fonts\n')
            return False

        for line in raw.stdout.decode('utf-8').split('\n'):
            font = line.split(':')
            if len(font) < 3:
                continue
            # Only consider TrueType and OpenType fonts
            if any(ext in font[0].lower() for ext in ('.ttf', '.otf')):
                fontname = font[1].replace('\\', '').strip()
                fontpath = font[0].strip()
                fontstyle = font[2][6:].strip().split(',')[0]
                if ',' in fontname:
                    fontname = fontname.split(',')[0].strip()
                self.fonts[fontname][fontstyle] = fontpath

    def scan_global_fonts(self):
        """
        Get a list of all fonts available to the user who runs this.
        """
        command = ['fc-list']
        try:
            raw = subprocess.run(command, stdout=subprocess.PIPE)
        except FileNotFoundError:
            sys.stderr.write('fc-list not found\n')
            sys.exit(2)
        self.parse_fonts(raw)

    def scan_fonts_folder(self, folder):
        """
        Get a list of all fonts in the specified folder.
        """
        cmd = ['fc-scan', '--format', '%{file}:%{family}:style=%{style}\n', folder]
        try:
            raw = subprocess.run(cmd, stdout=subprocess.PIPE)
        except FileNotFoundError:
            sys.stderr.write('fc-scan not found\n')
            sys.exit(2)
        self.parse_fonts(raw)

    def fontlist(self):
        """Return a sorted list of font family names."""
        return sorted(self.fonts.keys(), key=str.lower)

    def fontstyles(self):
        """Return a sorted list of font styles for each family."""
        styles = defaultdict(list)
        for family, variants in self.fonts.items():
            styles[family].extend(variants.keys())
        return {family: sorted(set(variant.lower() for variant in variants), key=str.lower)
                for family, variants in styles.items()}

    def fonts_available(self):
        """Return True if any fonts are available, else False."""
        return bool(self.fonts)
