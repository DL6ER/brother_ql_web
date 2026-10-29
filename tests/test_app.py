import sys

import pytest

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


@pytest.mark.parametrize('key, value', [('PRINT_MAX_COUNT', 'abc'), ('LOG_LEVEL', 'verbose')])
def test_invalid_environment_values(monkeypatch, key, value):
    monkeypatch.setenv(key, value)
    with pytest.raises(ValueError, match=f'Invalid value for the environment variable {key}'):
        create_app()
