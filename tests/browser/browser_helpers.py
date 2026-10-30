"""Helpers shared by the browser tests and their fixtures."""


def wait_until_ready(page):
    """Wait until the code types and the printers are loaded and the first
    preview is done."""
    page.wait_for_function("() => document.getElementById('barcode_type').options.length > 0")
    page.wait_for_function("() => document.getElementById('printer').options.length > 0")
    page.wait_for_load_state('networkidle')
