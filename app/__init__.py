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

from brother_ql.devicedependent import models

from . import fonts
from config import Config

bootstrap = Bootstrap()


def create_app(config_class=Config):
    app = Flask(__name__, instance_relative_config=True)
    app.config.from_object(config_class)
    app.config.from_pyfile('application.py', silent=True)

    app.logger.setLevel(app.config['LOG_LEVEL'])

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

    if app.config['FONT_FOLDER']:
        FONTS.scan_fonts_folder(app.config['FONT_FOLDER'])

    if not FONTS.fonts_available():
        app.logger.error(
            "Not a single font was found on your system. Please install some.\n")
        sys.exit(2)

    if app.config['LABEL_DEFAULT_FONT_FAMILY'] in FONTS.fonts.keys() and app.config['LABEL_DEFAULT_FONT_STYLE'] in FONTS.fonts[app.config['LABEL_DEFAULT_FONT_FAMILY']].keys():
        app.logger.debug(
            "Selected the following default font: {}".format(app.config['LABEL_DEFAULT_FONT_FAMILY']))

    else:
        app.logger.warn(
            'Could not find any of the default fonts. Choosing a random one.\n')
        family = random.choice(list(FONTS.fonts.keys()))
        style = random.choice(list(FONTS.fonts[family].keys()))
        app.config['LABEL_DEFAULT_FONT_FAMILY'] = family
        app.config['LABEL_DEFAULT_FONT_STYLE'] = style
        app.logger.warn(
            'The default font is now set to: {} ({})\n'.format(family, style))


def parse_args(app):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--default-label-size', default=False,
                        help='Label size inserted in your printer. Defaults to 62.')
    parser.add_argument('--default-orientation', default=False, choices=('standard', 'rotated'),
                        help='Label orientation, defaults to "standard". To turn your text by 90°, state "rotated".')
    parser.add_argument('--model', default=False, choices=models,
                        help='The model of your printer (default: QL-500)')
    parser.add_argument('printer',  nargs='?', default=False,
                        help='String descriptor for the printer to use (like tcp://192.168.0.23:9100 or file:///dev/usb/lp0)')
    args = parser.parse_args()

    if args.printer:
        app.config.update(
            PRINTER_PRINTER=args.printer
        )

    if args.model:
        app.config.update(
            PRINTER_MODEL=args.model
        )

    if args.default_label_size:
        app.config.update(
            LABEL_DEFAULT_SIZE=args.default_label_size
        )

    if args.default_orientation:
        app.config.update(
            LABEL_DEFAULT_ORIENTATION=args.default_orientation
        )
