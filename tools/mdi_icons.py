"""Write the names and code points of the Material Design Icons used for
{{mdi:<name>}} in label texts to app/static/mdi/icons.json, from the CSS of
the @mdi/font package whose font is app/static/mdi/materialdesignicons-webfont.ttf:

    curl -sO https://cdn.jsdelivr.net/npm/@mdi/font@7.4.47/css/materialdesignicons.css
    python3 tools/mdi_icons.py materialdesignicons.css 7.4.47
"""
import json
import os
import re
import sys

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', 'app', 'static', 'mdi', 'icons.json')

css, version = open(sys.argv[1], encoding='utf-8').read(), sys.argv[2]
icons = dict(re.findall(r'\.mdi-([a-z0-9-]+)::before\s*\{\s*content:\s*"\\([0-9A-Fa-f]+)"', css))
# Labels take the icons from Supplementary Private Use Area-A only, which
# leaves out "blank", an invisible icon in the BMP
icons = {name: code for name, code in icons.items() if int(code, 16) >= 0xF0000}
if not icons:
    sys.exit('No icons found')
with open(OUT, 'w', encoding='utf-8') as f:
    json.dump({'version': version, 'icons': {name: icons[name].upper() for name in sorted(icons)}}, f, separators=(',', ':'))
    f.write('\n')
print(f'{len(icons)} icons of version {version} written to {OUT}')
