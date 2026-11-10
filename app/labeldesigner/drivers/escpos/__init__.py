"""Epson receipt printers on ESC/POS (protocol.py), e.g. the TM-T20II on
endless 58 or 80 mm paper, which it cuts. The label is sent as an image."""

import logging
import re

from PIL import Image

from .. import devices
from ..base import Driver, network_status
from ..labels import FormFactor, Label
from ...enums import LabelContent
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('TM-T20II',)
# Paper width in mm and the dots across it the TM-T20II prints, the first
# one assumed, as the printer does not tell
WIDTHS = {80: 576, 58: 420}

LABELS = tuple(Label(f'escpos{width}', (width, 0), FormFactor.ENDLESS, (dots, 0), (dots, 0), 0, models=MODELS)
               for width, dots in WIDTHS.items())
# Paper fed to the cutter after each label that is cut, blank before the
# next one: on a TM-T20II a 15 mm label took 27 mm of paper, two of them
# not cut between them 42 mm
LEAD_MM = 12
# Below the cutter, inside the printer
NEVER_WHY = 'The paper would stay inside the printer until it is cut'
NAMES = {label.identifier: f'{label.tape_size[0]} mm receipt paper (Epson)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots across the
    paper, the first one comes out first, and the bytes of each row."""
    img = label.generate()
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    if img.width != across:
        img = img.rotate(90, expand=True)
    dither = label.label_content != LabelContent.IMAGE_BW
    img = img.convert('L').convert('1', dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    # 1 bit images of PIL are packed MSB first with white 1, ESC/POS prints 1
    width = (img.width + 7) // 8
    data = bytes(255 - byte for byte in img.tobytes())
    rows = [data[y * width:(y + 1) * width] for y in range(img.height)]
    # The padding of the last byte of each row is white
    if img.width % 8:
        mask = (0xff << (8 - img.width % 8)) & 0xff
        rows = [row[:-1] + bytes([row[-1] & mask]) for row in rows]
    return img, rows, width


class Escpos(Driver):
    vendor = 'Epson'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # Also a name a udev rule gives it, e.g. /dev/usb/lp_epson
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),)
    errors = (OSError, protocol.EscposError)

    def fits(self, label, model):
        return label in LABELS

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def cut_problem(self, cut, model):
        return f'The {model} cuts the paper at least after the last label' if cut == 'none' else ''

    def capabilities(self, model):
        return dict(super().capabilities(model), qualities=['quality'], never_cut=NEVER_WHY,
                    # The top of the label leaves it first
                    leaves_turned=True, lead_mm=LEAD_MM)

    def status(self, device, model, unconfirmed=False):
        state = network_status(device, model)
        state.update({'media_type': 'Receipt paper', 'phase_type': 'Waiting to receive'})
        with devices.device_lock(device, timeout=devices.STATUS_LOCK_TIMEOUT) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(devices.lock_key(device), {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            with protocol.connect(device) as c:
                state['errors'] = protocol.get_errors(c)
        state['status_type'] = 'Error occurred' if state['errors'] else 'Reply to status request'
        devices.status_cache[devices.lock_key(device)] = state
        return state

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            img, rows, width = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'escpos_{i}')
            job.append((entry['cut'], rows, width))
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            with protocol.connect(device) as c:
                problems = protocol.get_errors(c)
                if problems:
                    return problems[0]
                c.write(protocol.job(job))
        except protocol.EscposError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'The printer does not answer'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'Could not send the label to the printer, the log of the server names the reason'
        return ''


DRIVER = Escpos()
