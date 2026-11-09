"""The Niimbot D110_M, a thermal printer for small die-cut labels on Bluetooth
(protocol.py). It has no cutter, the labels are torn off. Not the D110, which
niimbluelib prints with other commands."""

import logging
import re
import time

from PIL import Image

from .. import devices
from ..base import Driver, network_status
from ..labels import FormFactor, Label
from ...enums import LabelContent
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('D110_M',)
DOTS_PER_MM = 8

# Width x length in mm of the rolls sold for the D11 and D110 printers,
# e.g. T12*40 for 12 x 40 mm. Only 12 x 40 mm was printed on.
SIZES = ((12, 22), (12, 40), (14, 22), (14, 40), (14, 60), (15, 30), (15, 50))
# Width x length of the rolls by the barcode in their RFID tag, the label
# designer then picks them. Niimbot tells it only online, at
# print.niimbot.com, the label designer stays offline.
ROLLS = {'01222281': (12, 40)}  # T12*40-155WHITE
# It beeps at each connection: its status is asked at most this often, in s
STATUS_EVERY = 300
_asked: dict[str, float] = {}


def _label(width: int, length: int) -> Label:
    dots = (width * DOTS_PER_MM, length * DOTS_PER_MM)
    return Label(f'niimbot{width}x{length}', (width, length), FormFactor.DIE_CUT, dots, dots, 0, models=MODELS)


LABELS = tuple(_label(width, length) for width, length in SIZES)
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (Niimbot)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[list[int]]]:
    """A label as the printer prints it: rows across the printhead, as wide
    as the label, black is printed."""
    img = label.generate()
    # The label designer draws labels longer than wide lying, e.g. 12 x 40
    # mm as 320 x 96 dots; turned as by the other drivers, their end leaves
    # the printer first
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    if img.width != across:
        img = img.rotate(90, expand=True)
    dither = label.label_content != LabelContent.IMAGE_BW
    img = img.convert('L').convert('1', dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    rows = [[int(img.getpixel((x, y)) == 0) for x in range(min(img.width, protocol.HEAD))] for y in range(img.height)]
    return img, rows


class Niimbot(Driver):
    vendor = 'Niimbot'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # bt://<MAC>[/<RFCOMM channel>]
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),)
    errors = (OSError, protocol.NiimbotError)

    def fits(self, label, model):
        return label in LABELS

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True)

    def status(self, device, model, unconfirmed=False):
        # One that did not answer is not looked for at every status query
        error = devices.unanswered_before(device)
        if error:
            raise error.with_traceback(None)
        cached = devices.status_cache.get(devices.lock_key(device))
        if cached and time.monotonic() - _asked.get(device, 0) < STATUS_EVERY:
            return dict(cached)
        try:
            state = self._status(device, model)
        except self.errors as e:
            devices.note_answer(device, e)
            raise
        devices.note_answer(device)
        _asked[device] = time.monotonic()
        return state

    def _status(self, device, model):
        state = network_status(device, model)
        state.update({'media_type': 'Die-cut labels', 'phase_type': 'Waiting to receive',
                      'status_type': 'Reply to status request'})
        with devices.device_lock(device, timeout=devices.STATUS_LOCK_TIMEOUT) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(devices.lock_key(device), {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            with protocol.connect(device) as c:
                state['battery'] = protocol.heartbeat(c).get('battery')
                roll = protocol.roll(c)
        # The label designer then picks the labels of the roll
        if roll and roll['barcode'] in ROLLS:
            state['media_width'], state['media_length'] = ROLLS[roll['barcode']]
        devices.status_cache[devices.lock_key(device)] = state
        return state

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            img, rows = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'niimbot_{i}')
            job.append(rows)
        return job

    def send(self, queue, job) -> str:
        """Print each label as a job of its own, the way the script this
        came from prints one, with the label type of the roll."""
        device = queue.device_specifier
        try:
            with protocol.connect(device) as c:
                loaded = protocol.roll(c)
                label_type = loaded['type'] if loaded else protocol.WITH_GAPS
                for rows in job:
                    protocol.print_label(c, rows, label_type)
            devices.note_answer(device)
        except protocol.NiimbotError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'The printer refused the label, the log of the server names the reason'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            reason = devices.unanswered_reason(e)
            return (f'Could not send the label to the printer: {reason}' if reason
                    else 'Could not send the label to the printer, the log of the server names the reason')
        return ''


DRIVER = Niimbot()
