import sys

from app import create_app, parse_args


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


def test_command_line_overrides_environment(monkeypatch):
    monkeypatch.setenv('PRINTER_MODEL', 'QL-700')
    app = create_app()
    parse_args(app, ['--model', 'QL-800', 'tcp://192.168.0.23'])
    assert app.config['PRINTER_MODEL'] == 'QL-800'
    assert app.config['PRINTER_PRINTER'] == 'tcp://192.168.0.23'


def test_request_size_is_limited():
    assert create_app().config['MAX_CONTENT_LENGTH'] == 32 * 1024 * 1024
