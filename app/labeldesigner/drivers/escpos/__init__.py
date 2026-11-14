"""Receipt printers on ESC/POS (protocol.py), e.g. an Epson TM-T20II, on
endless 58 or 80 mm paper, which they cut. ESC/POS is the same on any of
them, so it is one model for each resolution of their heads, not one per
printer. The label is sent as an image."""

import logging
import re

from PIL import Image

from .. import devices
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from ...enums import LabelContent
from . import protocol

logger = logging.getLogger(__name__)

# Most heads print 203 dpi, those of the TM-T88 series 180 dpi
MODELS = ('Epson/ESC-POS', 'Epson/ESC-POS-180')
DPI = {MODELS[0]: 203, MODELS[1]: 180}
# Paper width in mm and the dots across it, the first one assumed, as the
# printer does not tell. 384 on 58 mm paper as most printers print, a
# TM-T20II would print 420.
WIDTHS = {MODELS[0]: {80: 576, 58: 384}, MODELS[1]: {80: 512, 58: 360}}

LABELS = tuple(Label(f'escpos{width}' + ('-180' if model == MODELS[1] else ''), (width, 0), FormFactor.ENDLESS,
                     (dots, 0), (dots, 0), 0, models=(model,))
               for model, widths in WIDTHS.items() for width, dots in widths.items())
# Paper fed to the cutter after each label that is cut, blank before the
# next one: on a TM-T20II a 15 mm label took 27 mm of paper, two of them
# not cut between them 42 mm. Other printers have their head nearer to or
# further from the cutter.
LEAD_MM = 12
# Below the cutter, inside the printer
NEVER_WHY = 'The paper would stay inside the printer until it is cut'
NAMES = {label.identifier: f'{label.tape_size[0]} mm receipt paper (ESC/POS, {DPI[label.models[0]]} dpi)'
         for label in LABELS}


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
    # usblp's device, also a name a udev rule gives it, e.g. /dev/usb/lp_epson,
    # on Linux only, the printer through PyUSB, e.g. usb://0x04b8:0x0e15, or
    # on the network on the port of raw printing, as the other drivers
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
                       re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
                       re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'))
    errors = (OSError, protocol.EscposError)

    def model_of(self, device):
        # By the device ID on USB, e.g. MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;...
        fields = devices.device_id_fields(devices.usb_device_id(device)) if isinstance(device, str) else {}
        commands = [command.strip().upper() for command in fields.get('CMD', '').split(',')]
        if 'ESC/POS' not in commands:
            return None
        return MODELS[1] if fields.get('MDL', '').startswith('TM-T88') else MODELS[0]

    def fits(self, label, model):
        return label in LABELS and model in label.models

    def dpi(self, high_res):
        return DPI[MODELS[0]]

    def label_dpi(self, label):
        return DPI[label.models[0]], DPI[label.models[0]]

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} printer cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def cut_problem(self, cut, model):
        return f'The {shown(model)} printer cuts the paper at least after the last label' if cut == 'none' else ''

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
