"""Helpers shared by the browser tests and their fixtures."""

import io

from PIL import Image


def type_line(page, row, text):
    page.fill(f'[data-row="{row}"] .tx', text)
    wait_for_preview(page)


def kept(page):
    """Wait for the pause after a change: it is a step to undo then, and
    kept for the next visit."""
    page.wait_for_function('() => S.hist.last === snapshot(S.edit)')


def fits_note(page) -> str:
    return page.inner_text('#edFits')


def preview_size(page, stage='#edStage') -> tuple:
    return tuple(page.eval_on_selector(f'{stage} .paper img', 'img => [img.naturalWidth, img.naturalHeight]'))


def printed(page, count=1):
    page.wait_for_function("count => S.history.length === count && S.history.every(h => h.state !== 'printing')", arg=count)
    return page.evaluate('() => S.history.map(h => h.state)')


def png(width=200, height=100) -> bytes:
    out = io.BytesIO()
    Image.new('L', (width, height), 'black').save(out, format='PNG')
    return out.getvalue()


def set_tape(page, size):
    """Set the tape in the printer by hand in the printer panel, from the
    editor. Its label is placed on that tape."""
    page.click('#edTapeChange')
    page.select_option('#setTape', size)
    page.keyboard.press('Escape')
    page.wait_for_selector('.pp', state='detached')
    wait_for_preview(page)


def save_as(page, name):
    page.click('#edSave')
    page.fill('#saveName', name)
    page.press('#saveName', 'Enter')


def wait_for_preview(page, stage='#edStage'):
    """The label designer: wait until the stage shows the newest preview.
    Every change marks the stage as busy right away."""
    page.wait_for_function("stage => { const el = document.querySelector(stage); return el && !el.classList.contains('busy'); }", arg=stage)
