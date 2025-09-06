#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
This is a web service to print labels on Brother QL label printers.
"""

import sys
import random
import argparse

from flask import Flask
from flask_bootstrap import Bootstrap
from brother_ql.models import ALL_MODELS

from . import fonts
from config import Config

bootstrap = Bootstrap()


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.config.from_pyfile('application.py', silent=True)

    app.logger.setLevel(app.config.get('LOG_LEVEL', 'INFO'))

    init_fonts_and_args(app)

    app.config['BOOTSTRAP_SERVE_LOCAL'] = True
    bootstrap.init_app(app)

    from app.main import bp as main_bp
    app.register_blueprint(main_bp)

    from app.labeldesigner import bp as labeldesigner_bp
    app.register_blueprint(labeldesigner_bp, url_prefix='/labeldesigner')

    from app.errors import bp as errors_bp
    app.register_blueprint(errors_bp)

    return app


def init_fonts_and_args(app):
    global FONTS
    FONTS = fonts.Fonts()
    FONTS.scan_global_fonts()

    # Only parse command-line arguments if not running under pytest
    if not any('pytest' in arg for arg in sys.argv[0:1]):
        parse_args(app)

    font_folder = app.config.get('FONT_FOLDER')
    if font_folder:
        FONTS.scan_fonts_folder(font_folder)

    if not FONTS.fonts_available():
        app.logger.error("No fonts found on your system. Please install some.")
        sys.exit(2)

    default_family = app.config.get('LABEL_DEFAULT_FONT_FAMILY')
    default_style = app.config.get('LABEL_DEFAULT_FONT_STYLE')
    if default_family in FONTS.fonts and default_style in FONTS.fonts[default_family]:
        app.logger.debug(f"Selected the following default font: {default_family}")
    else:
        app.logger.warning('Could not find any of the default fonts. Choosing a random one.')
        family = random.choice(list(FONTS.fonts.keys()))
        style = random.choice(list(FONTS.fonts[family].keys()))
        app.config['LABEL_DEFAULT_FONT_FAMILY'] = family
        app.config['LABEL_DEFAULT_FONT_STYLE'] = style
        app.logger.warning(f'The default font is now set to: {family} ({style})')


def parse_args(app):
    models = [model.identifier for model in ALL_MODELS]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--default-label-size', default=None,
                        help='Label size inserted in your printer. Defaults to 62.')
    parser.add_argument('--default-orientation', default=None, choices=('standard', 'rotated'),
                        help='Label orientation, defaults to "standard". To turn your text by 90°, state "rotated".')
    parser.add_argument('--model', default=None, choices=models,
                        help='The model of your printer (default: QL-500)')
    parser.add_argument('printer', nargs='?', default=None,
                        help='String descriptor for the printer to use (like tcp://192.168.0.23:9100 or file:///dev/usb/lp0)')
    args = parser.parse_args()

    if args.printer:
        app.config['PRINTER_PRINTER'] = args.printer
    if args.model:
        app.config['PRINTER_MODEL'] = args.model
    if args.default_label_size:
        app.config['LABEL_DEFAULT_SIZE'] = args.default_label_size
    if args.default_orientation:
        app.config['LABEL_DEFAULT_ORIENTATION'] = args.default_orientation
