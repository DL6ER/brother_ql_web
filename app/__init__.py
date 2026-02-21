#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
This is a web service to print labels on Brother QL label printers.
"""

import os
import sys
import argparse

from flask import Flask
from brother_ql.models import ALL_MODELS

from . import fonts
from config import Config

FONTS = None


def create_app(config_class=Config) -> Flask:
    global FONTS
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.config.from_pyfile('application.py', silent=True)

    app.logger.setLevel(app.config.get('LOG_LEVEL', 'INFO'))

    FONTS = init_fonts(app)

    # Only parse command-line arguments if not running under pytest
    if not any('pytest' in arg for arg in sys.argv[0:1]):
        parse_args(app)

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.labeldesigner import bp as labeldesigner_bp
    app.register_blueprint(labeldesigner_bp, url_prefix='/labeldesigner')

    from app.errors import bp as errors_bp
    app.register_blueprint(errors_bp)

    # Register printer power control API
    from app.printer_power import bp as printer_power_bp
    app.register_blueprint(printer_power_bp)

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


def parse_args(app):
    models = [model.identifier for model in ALL_MODELS]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--default-label-size', default=os.getenv('LABEL_DEFAULT_SIZE', app.config['LABEL_DEFAULT_SIZE']),
                        help='Label size inserted in your printer. Defaults to 62.')
    parser.add_argument('--default-orientation', default=os.getenv('LABEL_DEFAULT_ORIENTATION', app.config['LABEL_DEFAULT_ORIENTATION']), choices=('standard', 'rotated'),
                        help='Label orientation, defaults to "standard". To turn your text by 90°, state "rotated".')
    parser.add_argument('--model', default=os.getenv('PRINTER_MODEL', app.config['PRINTER_MODEL']), choices=models,
                        help='The model of your printer (default: QL-500)')
    parser.add_argument('printer', nargs='?', default=os.environ.get('PRINTER_PRINTER', app.config['PRINTER_PRINTER']),
                        help='String descriptor for the printer to use (like tcp://192.168.0.23:9100 or file:///dev/usb/lp0), may be left out to use auto-detection.')
    args = parser.parse_args()

    if args.printer:
        app.config['PRINTER_PRINTER'] = args.printer
    if args.model:
        app.config['PRINTER_MODEL'] = args.model
    if args.default_label_size:
        app.config['LABEL_DEFAULT_SIZE'] = args.default_label_size
    if args.default_orientation:
        app.config['LABEL_DEFAULT_ORIENTATION'] = args.default_orientation
