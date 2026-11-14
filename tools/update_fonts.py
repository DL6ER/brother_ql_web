"""Copy the icon fonts in app/static from the npm packages of the versions in
tools/fonts/package-lock.json, which Dependabot raises, and write the names of
the Material Design Icons used for {{mdi:<name>}} in label texts to
app/static/mdi/icons.json:

    python3 tools/update_fonts.py           # writes the files
    python3 tools/update_fonts.py --check   # fails while they differ

On a pull request of Dependabot, .github/workflows/fonts.yml runs it.
"""
import base64
import hashlib
import io
import json
import os
import re
import sys
import tarfile
import urllib.request

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), '..')
STATIC = os.path.join(ROOT, 'app', 'static')
LOCK = os.path.join(ROOT, 'tools', 'fonts', 'package-lock.json')
REGISTRY = 'https://registry.npmjs.org/'

# The files of each package, by their path in it and in app/static
FILES = {
    '@mdi/font': {
        'fonts/materialdesignicons-webfont.ttf': 'mdi/materialdesignicons-webfont.ttf',
        'LICENSE': 'mdi/LICENSE',
    },
    '@fortawesome/fontawesome-free': {
        'css/all.min.css': 'fontawesome/css/all.min.css',
        **{f'webfonts/{name}.woff2': f'fontawesome/webfonts/{name}.woff2'
           for name in ('fa-brands-400', 'fa-regular-400', 'fa-solid-900', 'fa-v4compatibility')},
    },
}


def locked(lock_path: str = LOCK) -> dict:
    """The version, address and integrity of each package in the lock file."""
    with open(lock_path, encoding='utf-8') as f:
        packages = json.load(f)['packages']
    return {name: packages[f'node_modules/{name}'] for name in FILES}


def fetch(package: dict) -> bytes:
    """The package from the npm registry, refused unless it matches its
    integrity in the lock file."""
    url = package['resolved']
    if not url.startswith(REGISTRY):
        raise ValueError(f'{url} is not on the npm registry')
    with urllib.request.urlopen(url, timeout=60) as response:
        data = response.read()
    algorithm, _, digest = package['integrity'].partition('-')
    if algorithm != 'sha512' or base64.b64decode(digest) != hashlib.sha512(data).digest():
        raise ValueError(f'{url} does not match its integrity in the lock file')
    return data


def mdi_icons(css: str, version: str) -> bytes:
    """icons.json: the names and code points in the CSS of @mdi/font."""
    icons = dict(re.findall(r'\.mdi-([a-z0-9-]+)::before\s*\{\s*content:\s*"\\([0-9A-Fa-f]+)"', css))
    # Labels take the icons from Supplementary Private Use Area-A only, which
    # leaves out "blank", an invisible icon in the BMP
    icons = {name: code for name, code in icons.items() if int(code, 16) >= 0xF0000}
    if not icons:
        raise ValueError('No icons found')
    data = {'version': version, 'icons': {name: icons[name].upper() for name in sorted(icons)}}
    return (json.dumps(data, separators=(',', ':')) + '\n').encode()


def wanted(lock_path: str = LOCK) -> dict:
    """The contents of the files in app/static, by their path there."""
    files = {}
    for name, package in locked(lock_path).items():
        with tarfile.open(fileobj=io.BytesIO(fetch(package))) as tar:
            def read(path):
                return tar.extractfile(f'package/{path}').read()
            for source, target in FILES[name].items():
                files[target] = read(source)
            if name == '@mdi/font':
                files['mdi/icons.json'] = mdi_icons(read('css/materialdesignicons.css').decode(), package['version'])
    return files


def main(argv: list, static: str = STATIC, lock_path: str = LOCK) -> int:
    check = '--check' in argv
    differing = []
    for path, data in wanted(lock_path).items():
        target = os.path.join(static, path)
        try:
            with open(target, 'rb') as f:
                if f.read() == data:
                    continue
        except FileNotFoundError:
            pass
        differing.append(path)
        if not check:
            os.makedirs(os.path.dirname(target), exist_ok=True)
            with open(target, 'wb') as f:
                f.write(data)
    if check and differing:
        print(f"Not the versions in tools/fonts/package-lock.json: {', '.join(differing)}\n"
              'Run python3 tools/update_fonts.py', file=sys.stderr)
        return 1
    print(f"{'Written' if differing and not check else 'Up to date'}: {', '.join(differing) or 'all files'}")
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
