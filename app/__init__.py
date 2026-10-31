#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
This is a web service to print labels on Brother QL label printers.
"""

import os
import sys
import logging
import argparse

from flask import Flask, current_app
from brother_ql.models import ALL_MODELS

from . import fonts
from .build_info import get_build_info
from config import Config


def _host_list(value: str) -> list[str] | None:
    return [host.strip() for host in value.split(',') if host.strip()] or None


def _positive_int(value: str) -> int:
    number = int(value)
    if number < 1:
        raise ValueError(value)
    return number


def _port(value: str) -> int:
    number = int(value)
    if not 1 <= number <= 65535:
        raise ValueError(value)
    return number


def _printer_list(value: str) -> list[tuple[str, str]]:
    """Parse "MODEL=DEVICE,MODEL=DEVICE" into (model, device) pairs."""
    models = [model.identifier for model in ALL_MODELS]
    printers = []
    for entry in value.split(','):
        if not entry.strip():
            continue
        model, sep, device = (part.strip() for part in entry.partition('='))
        if not sep or not device or model not in models:
            raise ValueError(entry)
        printers.append((model, device))
    return printers


def _log_level(value: str) -> str:
    level = value.strip().upper()
    if level not in logging.getLevelNamesMapping():
        raise ValueError(level)
    return level


# Settings that can be overridden by environment variables of the same name,
# with the function converting the value. They take precedence over the
# config files, command-line arguments (see parse_args) take precedence over
# them.
ENV_OVERRIDES = {
    'SERVER_PORT': _port,
    'PRINTER_MODEL': str,
    'PRINTER_PRINTER': str,
    'PRINTERS': _printer_list,
    'LABEL_DEFAULT_SIZE': str,
    'LABEL_DEFAULT_ORIENTATION': str,
    'LOG_LEVEL': _log_level,
    'TRUSTED_HOSTS': _host_list,
    'LABEL_TEMPLATE_ENV_VARS': str,
    'PRINT_MAX_COUNT': _positive_int,
    'MAX_CONTENT_LENGTH': _positive_int,
    'WEBHOOK_PASSWORD': str,
}


def create_app(config_class=Config) -> Flask:
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.config.from_pyfile('application.py', silent=True)
    for key, convert in ENV_OVERRIDES.items():
        # Empty variables (e.g. PRINTER_PRINTER= in a compose file) keep the default
        value = os.environ.get(key)
        if value:
            try:
                app.config[key] = convert(value)
            except ValueError:
                raise ValueError(f'Invalid value for the environment variable {key}: {value!r}') from None

    app.logger.setLevel(app.config.get('LOG_LEVEL', 'INFO'))

    app.extensions['fonts'] = init_fonts(app)

    build = get_build_info()

    @app.context_processor
    def inject_build_info():
        return {'build': build}

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.labeldesigner import bp as labeldesigner_bp
    app.register_blueprint(labeldesigner_bp, url_prefix='/labeldesigner')

    from app.errors import bp as errors_bp
    app.register_blueprint(errors_bp)

    return app


def init_fonts(app: Flask):
    FONTS = fonts.Fonts(app.logger,
                        app.config.get('LABEL_DEFAULT_FONT_FAMILY'),
                        app.config.get('LABEL_DEFAULT_FONT_STYLE'),
                        app.config.get('FONT_FOLDER'))
    if not FONTS.fonts_available():
        app.logger.error("No fonts found on your system. Please install some.")
        sys.exit(2)

    return FONTS


def get_fonts() -> fonts.Fonts:
    """Return the fonts of the current application."""
    return current_app.extensions['fonts']


def parse_args(app, argv=None):
    """Apply command-line arguments to the app config. Only called by run.py,
    WSGI servers like gunicorn pass their own arguments."""
    models = [model.identifier for model in ALL_MODELS]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--default-label-size', default=app.config['LABEL_DEFAULT_SIZE'],
                        help='Label size inserted in your printer. Defaults to 62.')
    parser.add_argument('--default-orientation', default=app.config['LABEL_DEFAULT_ORIENTATION'], choices=('standard', 'rotated'),
                        help='Label orientation, defaults to "standard". To turn your text by 90°, state "rotated".')
    parser.add_argument('--model', default=app.config['PRINTER_MODEL'], choices=models,
                        help='The model of your printer (default: QL-500)')
    parser.add_argument('printer', nargs='?', default=app.config['PRINTER_PRINTER'],
                        help='String descriptor for the printer to use (like tcp://192.168.0.23:9100 or file:///dev/usb/lp0), may be left out to use auto-detection.')
    args = parser.parse_args(argv)

    if args.printer:
        app.config['PRINTER_PRINTER'] = args.printer
    if args.model:
        app.config['PRINTER_MODEL'] = args.model
    if args.default_label_size:
        app.config['LABEL_DEFAULT_SIZE'] = args.default_label_size
    if args.default_orientation:
        app.config['LABEL_DEFAULT_ORIENTATION'] = args.default_orientation
