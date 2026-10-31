import re
import sys
from io import BytesIO
from urllib.parse import urljoin

import pytest
from PIL import Image

from app import create_app, parse_args
from config import Config


def test_create_app_ignores_command_line(monkeypatch):
    # WSGI servers like gunicorn leave their own arguments in sys.argv
    monkeypatch.setattr(sys, 'argv', ['gunicorn', '--workers', '3', '--bind', '0.0.0.0:5000', 'wsgi:app'])
    monkeypatch.delenv('PRINTER_PRINTER', raising=False)
    app = create_app()
    assert app.config['PRINTER_PRINTER'] == '?'


def test_environment_overrides_config(monkeypatch):
    monkeypatch.setenv('PRINTER_MODEL', 'QL-700')
    monkeypatch.setenv('LABEL_DEFAULT_SIZE', '29')
    app = create_app()
    assert app.config['PRINTER_MODEL'] == 'QL-700'
    assert app.config['LABEL_DEFAULT_SIZE'] == '29'


def test_empty_environment_variables_keep_defaults(monkeypatch):
    # e.g. PRINTER_PRINTER=${PRINTER:-} in a compose file
    monkeypatch.setenv('PRINTER_PRINTER', '')
    monkeypatch.setenv('PRINTER_MODEL', '')
    app = create_app()
    assert app.config['PRINTER_PRINTER'] == '?'
    assert app.config['PRINTER_MODEL'] == 'QL-800'


def test_command_line_overrides_environment(monkeypatch):
    monkeypatch.setenv('PRINTER_MODEL', 'QL-700')
    app = create_app()
    parse_args(app, ['--model', 'QL-800', 'tcp://192.168.0.23'])
    assert app.config['PRINTER_MODEL'] == 'QL-800'
    assert app.config['PRINTER_PRINTER'] == 'tcp://192.168.0.23'


def test_request_size_is_limited():
    assert create_app().config['MAX_CONTENT_LENGTH'] == 32 * 1024 * 1024


def test_environment_overrides_instance_config(monkeypatch):
    class InstanceConfig(Config):
        PRINT_MAX_COUNT = 100
    monkeypatch.setenv('PRINT_MAX_COUNT', '500')
    monkeypatch.setenv('TRUSTED_HOSTS', 'labels.local, 192.168.0.10')
    app = create_app(InstanceConfig)
    assert app.config['PRINT_MAX_COUNT'] == 500
    assert app.config['TRUSTED_HOSTS'] == ['labels.local', '192.168.0.10']


@pytest.mark.parametrize('key, value', [('PRINT_MAX_COUNT', 'abc'), ('PRINT_MAX_COUNT', '-1'), ('MAX_CONTENT_LENGTH', '0'), ('LOG_LEVEL', 'verbose')])
def test_invalid_environment_values(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=f'Invalid value for the environment variable {key}'):
        create_app()


def test_navbar_shows_commit_and_build_time(monkeypatch):
    monkeypatch.delenv('RELEASE', raising=False)
    monkeypatch.setenv('GIT_COMMIT', '1c0ea1aba5f663c8534b3d44cad4e0ed2af47397')
    monkeypatch.setenv('BUILD_DATE', '2026-10-29T02:37:05Z')
    html = create_app().test_client().get('/labeldesigner/').get_data(as_text=True)
    assert 'href="https://github.com/DL6ER/brother_ql_web/commit/1c0ea1aba5f663c8534b3d44cad4e0ed2af47397"' in html
    assert '>1c0ea1a, 2026-10-29 02:37 UTC</a>' in html


def test_build_info_falls_back_to_the_checkout(monkeypatch):
    from app import build_info
    monkeypatch.delenv('RELEASE', raising=False)
    monkeypatch.setenv('GIT_COMMIT', '')
    monkeypatch.setenv('BUILD_DATE', '')
    monkeypatch.setattr(build_info, '_checkout_commit', lambda: 'ca2bac4')
    assert build_info.get_build_info() == {'release': '', 'commit': 'ca2bac4', 'date': ''}


def test_navbar_shows_the_release_instead_of_the_commit(monkeypatch):
    monkeypatch.setenv('RELEASE', 'v1.0')
    monkeypatch.setenv('GIT_COMMIT', '55e8900424aa9314eb99aa8343cbd35de78b7ca2')
    monkeypatch.setenv('BUILD_DATE', '2026-10-30T15:40:32Z')
    html = create_app().test_client().get('/labeldesigner/').get_data(as_text=True)
    assert 'href="https://github.com/DL6ER/brother_ql_web/releases/tag/v1.0"' in html
    assert 'title="Release v1.0, commit 55e8900424aa9314eb99aa8343cbd35de78b7ca2, built 2026-10-30 15:40 UTC"' in html
    assert '>v1.0, 2026-10-30 15:40 UTC</a>' in html
    assert '/commit/' not in html


@pytest.mark.parametrize('release', ['latest', '1.0', 'v1.0;x', 'v1'])
def test_navbar_ignores_an_invalid_release(monkeypatch, release):
    monkeypatch.setenv('RELEASE', release)
    monkeypatch.setenv('GIT_COMMIT', '1c0ea1aba5f663c8534b3d44cad4e0ed2af47397')
    monkeypatch.setenv('BUILD_DATE', '')
    html = create_app().test_client().get('/labeldesigner/').get_data(as_text=True)
    assert '>1c0ea1a</a>' in html
    assert '/releases/tag/' not in html


@pytest.mark.parametrize('commit, date', [('not a commit', 'yesterday'), ('', '')])
def test_navbar_without_build_info(monkeypatch, commit, date):
    from app import build_info
    monkeypatch.delenv('RELEASE', raising=False)
    monkeypatch.setenv('GIT_COMMIT', commit)
    monkeypatch.setenv('BUILD_DATE', date)
    monkeypatch.setattr(build_info, '_checkout_commit', lambda: '')
    html = create_app().test_client().get('/labeldesigner/').get_data(as_text=True)
    assert 'id="buildInfo"' not in html


def test_label_designer_can_be_installed_as_an_app():
    # What browsers need to offer installing it, see web.dev/articles/install-criteria
    client = create_app().test_client()
    html = client.get('/labeldesigner/').get_data(as_text=True)
    manifest_url = re.search(r'<link rel="manifest" href="([^"]+)"', html).group(1)
    with client.get(manifest_url) as response:
        manifest = response.get_json()
    assert manifest['name'] and manifest['short_name']
    assert manifest['display'] == 'standalone'
    # Relative to the manifest, so that it also works behind a proxy that
    # serves the label designer below a path
    assert urljoin(manifest_url, manifest['start_url']) == '/labeldesigner/'
    widths = {}
    for icon in manifest['icons']:
        with client.get(urljoin(manifest_url, icon['src'])) as response:
            image = Image.open(BytesIO(response.data))
        assert icon['sizes'] == f'{image.width}x{image.height}'
        widths.setdefault(icon['purpose'], set()).add(image.width)
    assert widths == {'any': {192, 512}, 'maskable': {512}}
    # Android offers the installed app in its share menu
    share = manifest['share_target']
    assert urljoin(manifest_url, share['action']) == '/labeldesigner/share'
    assert share['method'] == 'POST' and share['enctype'] == 'multipart/form-data'
    assert share['params']['files'][0]['name'] == 'file'
    # iOS shows transparent parts of the icon as black
    apple_icon = re.search(r'<link rel="apple-touch-icon" href="([^"]+)"', html).group(1)
    with client.get(apple_icon) as response:
        image = Image.open(BytesIO(response.data))
    assert image.size == (180, 180) and image.mode == 'RGB'


def test_printers_from_environment(monkeypatch):
    monkeypatch.setenv('PRINTERS', 'QL-700=usb://0x04f9:0x2042/L9Z378866, QL-1100=file:///dev/usb/lp1')
    assert create_app().config['PRINTERS'] == [
        ('QL-700', 'usb://0x04f9:0x2042/L9Z378866'),
        ('QL-1100', 'file:///dev/usb/lp1'),
    ]
    for value in ('QL-9999=file:///dev/usb/lp0', 'QL-700', 'QL-700='):
        monkeypatch.setenv('PRINTERS', value)
        with pytest.raises(ValueError):
            create_app()



def test_label_designer_below_a_path_behind_a_proxy(monkeypatch):
    headers = {'X-Forwarded-Prefix': '/labels', 'X-Forwarded-Host': 'example.com', 'X-Forwarded-Proto': 'https'}
    # With PROXY_COUNT=0, the default, the headers are not trusted
    monkeypatch.setenv('PROXY_COUNT', '0')
    client = create_app().test_client()
    assert client.get('/', headers=headers).headers['Location'] == '/labeldesigner/'
    monkeypatch.setenv('PROXY_COUNT', '1')
    client = create_app().test_client()
    assert client.get('/', headers=headers).headers['Location'] == '/labels/labeldesigner/'
    html = client.get('/labeldesigner/', headers=headers).get_data(as_text=True)
    manifest_url = re.search(r'<link rel="manifest" href="([^"]+)"', html).group(1)
    assert manifest_url.startswith('/labels/')
    # The API is called below the path too
    assert "url_for_print = '/labels/labeldesigner/api/print'" in html
