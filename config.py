"""
This are the default settings. (DONT CHANGE THIS FILE)
Adjust your settings in 'instance/application.py'
"""

import os
import logging

basedir = os.path.abspath(os.path.dirname(__file__))

class Config(object):
    DEBUG = False
    LOG_LEVEL = logging.WARNING

    SERVER_PORT = 8013
    SERVER_HOST = '0.0.0.0'

    PRINTER_MODEL = 'QL-800'
    PRINTER_PRINTER = '?'

    LABEL_DEFAULT_ORIENTATION = 'standard'
    LABEL_DEFAULT_SIZE = '62'
    LABEL_DEFAULT_FONT_SIZE = 70
    LABEL_DEFAULT_QR_SIZE = 10
    LABEL_DEFAULT_LINE_SPACING = 100
    LABEL_DEFAULT_FONT_FAMILY = 'DejaVu Serif'
    LABEL_DEFAULT_FONT_STYLE = 'Book'

    IMAGE_DEFAULT_MODE = 'grayscale'
    IMAGE_DEFAULT_BW_THRESHOLD = 70

    LABEL_DEFAULT_MARGIN_TOP = 24
    LABEL_DEFAULT_MARGIN_BOTTOM = 24
    LABEL_DEFAULT_MARGIN_LEFT = 35
    LABEL_DEFAULT_MARGIN_RIGHT = 35

    LABEL_REPOSITORY_DIR = os.path.join(basedir, 'labels')

    # Environment variables label templates may insert via {{env:<var>}}
    # (comma-separated). All other variables are replaced by an empty string,
    # otherwise anyone with access to the preview could read secrets like
    # WEBHOOK_PASSWORD.
    LABEL_TEMPLATE_ENV_VARS = os.environ.get('LABEL_TEMPLATE_ENV_VARS', '')

    # Maximum number of labels a single print request may produce
    PRINT_MAX_COUNT = int(os.environ.get('PRINT_MAX_COUNT', 100))

    # Maximum size of a request (uploaded images, JSON payloads) in bytes
    MAX_CONTENT_LENGTH = int(os.environ.get('MAX_CONTENT_LENGTH', 32 * 1024 * 1024))

    FONT_FOLDER = ''

    # Webhook print endpoint password. The webhook is disabled when empty.
    # Generate a secure password with:
    #   python3 -c "import secrets; print(secrets.token_urlsafe(32))"
    WEBHOOK_PASSWORD = os.environ.get('WEBHOOK_PASSWORD', '')
