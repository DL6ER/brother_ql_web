"""Helpers shared by the browser tests and their fixtures."""


def wait_for_preview(page, stage='#edStage'):
    """The label designer: wait until the stage shows the newest preview.
    Every change marks the stage as busy right away."""
    page.wait_for_function("stage => { const el = document.querySelector(stage); return el && !el.classList.contains('busy'); }", arg=stage)
