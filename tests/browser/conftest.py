"""Fixtures for the browser tests: the label designer runs in a thread, a
headless Chromium (Playwright) opens it.

The tests are skipped where Playwright is not installed. Install it with
"pip install -r requirements-browser.txt" and "python -m playwright install
chromium". To use a Chromium that is already running instead, set
BROWSER_CDP_URL, e.g. to http://127.0.0.1:9222.
"""
import os
import shutil
import threading

import pytest

sync_api = pytest.importorskip('playwright.sync_api')

from werkzeug.serving import make_server  # noqa: E402

from app import create_app  # noqa: E402
from browser_helpers import wait_until_ready  # noqa: E402

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope='session')
def app(tmp_path_factory):
    app = create_app()
    # The simulator is the only printer, with a fixed model
    app.config['PRINTER_PRINTER'] = '?'
    app.config['PRINTER_MODEL'] = 'QL-800'
    # A copy of the sample labels, the tests must not change the working tree
    repo = tmp_path_factory.mktemp('labels')
    for name in os.listdir(os.path.join(REPO_ROOT, 'labels')):
        shutil.copy(os.path.join(REPO_ROOT, 'labels', name), repo)
    app.config['LABEL_REPOSITORY_DIR'] = str(repo)
    return app


@pytest.fixture(scope='session')
def server(app):
    srv = make_server('127.0.0.1', 0, app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{srv.server_port}'
    srv.shutdown()


@pytest.fixture(scope='session')
def browser():
    with sync_api.sync_playwright() as playwright:
        cdp_url = os.environ.get('BROWSER_CDP_URL')
        browser = playwright.chromium.connect_over_cdp(cdp_url) if cdp_url else playwright.chromium.launch()
        yield browser
        browser.close()


@pytest.fixture
def page(browser, server):
    """The label designer in a fresh browser context, without stored
    settings. Fails the test if the page throws a JavaScript error."""
    context = browser.new_context(viewport={'width': 1400, 'height': 900})
    page = context.new_page()
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    # e.g. the confirmation of "Reset"
    page.on('dialog', lambda dialog: dialog.accept())
    page.goto(server + '/labeldesigner/')
    wait_until_ready(page)
    yield page
    context.close()
    assert errors == []
