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


def test_server_port_from_environment(monkeypatch):
    monkeypatch.setenv('SERVER_PORT', '8700')
    assert create_app().config['SERVER_PORT'] == 8700
    for value in ('http', '0', '70000'):
        monkeypatch.setenv('SERVER_PORT', value)
        with pytest.raises(ValueError, match='SERVER_PORT'):
            create_app()


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
    assert 'href="https://github.com/DL6ER/labelburg/commit/1c0ea1aba5f663c8534b3d44cad4e0ed2af47397"' in html
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
    assert 'href="https://github.com/DL6ER/labelburg/releases/tag/v1.0"' in html
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


def test_printers_with_names_from_environment(monkeypatch):
    monkeypatch.setenv('PRINTERS', 'Kitchen:QL-700=usb://0x04f9:0x2042, Office desk 2 : QL-1100=file:///dev/usb/lp1,QL-800=simulation')
    assert create_app().config['PRINTERS'] == [
        ('QL-700', 'usb://0x04f9:0x2042', 'Kitchen'),
        ('QL-1100', 'file:///dev/usb/lp1', 'Office desk 2'),
        ('QL-800', 'simulation'),
    ]
    for value in (
            ':QL-700=file:///dev/usb/lp0',  # an empty name
            'Kitchen:QL-700=file:///dev/usb/lp0,kitchen:QL-1100=file:///dev/usb/lp1',  # the same name twice
            'Simulation:QL-700=file:///dev/usb/lp0',  # the name of the simulator
            'Kit:chen:QL-700=file:///dev/usb/lp0',
            'Kitchen/1:QL-700=file:///dev/usb/lp0',
            '-Kitchen:QL-700=file:///dev/usb/lp0',
            'Kitchen:QL-9999=file:///dev/usb/lp0',
            'A' * 41 + ':QL-700=file:///dev/usb/lp0'):
        monkeypatch.setenv('PRINTERS', value)
        with pytest.raises(ValueError, match='PRINTERS'):
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
    assert '"base": "/labels/labeldesigner"' in html
    # What it keeps in the browser is apart from an instance at the root,
    # which keeps the names it always had
    assert '"storage_prefix": "/labels/"' in html
    assert '"storage_prefix": ""' in client.get('/labeldesigner/').get_data(as_text=True)
    # Bookmarks of the previous designer open this one
    assert client.get('/labeldesigner/classic', headers=headers).headers['Location'] == '/labels/labeldesigner/'


def test_unknown_page_leads_to_the_label_designer():
    client = create_app().test_client()
    response = client.get('/labeldesigner/nothing-here')
    assert response.status_code == 404
    html = response.get_data(as_text=True)
    assert '<h1>Not found</h1>' in html
    assert 'href="/">' in html
    assert 'designer/designer.css' in html


def test_font_settings_from_environment(monkeypatch):
    monkeypatch.setenv('FONT_FOLDER', '/opt/fonts')
    monkeypatch.setenv('SYSTEM_FONTS', 'false')
    monkeypatch.setattr('app.fonts.Fonts.fonts_available', lambda self: True)
    config = create_app().config
    assert config['FONT_FOLDER'] == '/opt/fonts'
    assert config['SYSTEM_FONTS'] is False
    monkeypatch.setenv('SYSTEM_FONTS', 'maybe')
    with pytest.raises(ValueError, match='SYSTEM_FONTS'):
        create_app()


def test_models_are_named_with_their_maker(monkeypatch, tmp_path):
    # Short model names like D110 or P1 are used by more than one maker
    monkeypatch.setenv('PRINTERS', 'Brother/QL-700=usb://0x04f9:0x2042, PUQU/Q00=bt://CD:AC:BB:E0:46:4C,'
                       'Kitchen:Epson/TM-T20II=file:///dev/usb/lp0, Q00=bt://CD:AC:BB:E0:AA:29')
    assert create_app().config['PRINTERS'] == [
        ('QL-700', 'usb://0x04f9:0x2042'),
        ('PUQU/Q00', 'bt://CD:AC:BB:E0:46:4C'),
        # ESC/POS is one model, its name before is taken
        ('Epson/ESC-POS', 'file:///dev/usb/lp0', 'Kitchen'),
        # Its name before, taken for a while
        ('PUQU/Q00', 'bt://CD:AC:BB:E0:AA:29'),
    ]
    # The maker is required for models other than Brother ones
    for value in ('TM-T20II=file:///dev/usb/lp0', 'Epson TM-T20II=file:///dev/usb/lp0', 'Brother/Q00=bt://CD:AC:BB:E0:46:4C'):
        monkeypatch.setenv('PRINTERS', value)
        with pytest.raises(ValueError, match='PRINTERS'):
            create_app()
    monkeypatch.delenv('PRINTERS')
    monkeypatch.setenv('PRINTER_MODEL', 'Brother/QL-700')
    app = create_app()
    assert app.config['PRINTER_MODEL'] == 'QL-700'
    parse_args(app, ['--model', 'Brother/QL-800'])
    assert app.config['PRINTER_MODEL'] == 'QL-800'


def test_request_names_a_model_with_its_maker(tmp_path):
    from tests.helpers import make_client
    client = make_client(tmp_path)
    image = BytesIO()
    Image.new('1', (696, 30000), 1).save(image, 'PNG')
    image.seek(0)
    response = client.post('/labeldesigner/api/print', data={
        'printer': 'simulation', 'model': 'Brother/QL-800', 'label_size': '62', 'print_type': 'image',
        'image': (image, 'long.png')}, content_type='multipart/form-data')
    # Taken for the QL-800, which refuses labels that long
    assert response.get_json()['message'] == 'Labels on the QL-800 can be at most 1000 mm long'


@pytest.mark.parametrize('model', ['TM-T20II', 'Epson TM-T20II'])
def test_unknown_printer_model_is_refused_at_the_start(monkeypatch, model):
    # The status poll took it for a Brother printer and sent an Epson ESC i, a cut
    monkeypatch.setenv('PRINTER_MODEL', model)
    with pytest.raises(ValueError, match='PRINTER_MODEL'):
        create_app()
