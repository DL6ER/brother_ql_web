"""Helpers shared by the browser tests and their fixtures."""


def wait_until_ready(page):
    """The previous label designer: wait until the code types and the
    printers are loaded and the first preview is done."""
    page.wait_for_function("() => document.getElementById('barcode_type').options.length > 0")
    page.wait_for_function("() => document.getElementById('printer').options.length > 0")
    page.wait_for_load_state('networkidle')


def wait_for_preview(page, stage='#edStage'):
    """The label designer: wait until the stage shows the newest preview.
    Every change marks the stage as busy right away."""
    page.wait_for_function("stage => { const el = document.querySelector(stage); return el && !el.classList.contains('busy'); }", arg=stage)
