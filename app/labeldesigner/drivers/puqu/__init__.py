"""The PUQU Q00, a thermal printer for die-cut labels on Bluetooth
(protocol.py), also sold as the Reekon P1 Tagger (P1L), configured as a
Q00 as well. It has no cutter, the labels are torn off."""

import logging
import re

from PIL import Image

from .. import devices
from ..base import Driver, network_status
from ..labels import FormFactor, Label
from ...enums import LabelContent
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('Q00',)

# Width x length in mm, and how far from the left of the printhead the
# label lies, in mm. Labels narrower than the printhead are held by a slider
# against one side, a 40 mm label on a Reekon P1L came out right 1 mm from it.
SIZES = {(50, 30): 0, (50, 80): 0, (40, 30): 1, (40, 50): 1}
OFFSETS = {f'puqu{w}x{h}': offset for (w, h), offset in SIZES.items()}
DOTS_PER_MM = 8


def _label(width: int, length: int, offset: int) -> Label:
    total = (width * DOTS_PER_MM, length * DOTS_PER_MM)
    printable = (min(total[0], protocol.HEAD - offset * DOTS_PER_MM), total[1])
    return Label(f'puqu{width}x{length}', (width, length), FormFactor.DIE_CUT, total, printable, 0,
                 models=MODELS)


LABELS = tuple(_label(width, length, offset) for (width, length), offset in SIZES.items())
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (PUQU)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[list[int]]]:
    """A label as the printer prints it: rows across the printhead, the
    first one comes out first, black is printed."""
    img = label.generate()
    # Turned across the printhead: rotated labels, and labels longer than
    # wide, which the label designer draws lying, e.g. 50 x 80 mm as 640 x
    # 384 dots
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    if img.width != across:
        img = img.rotate(90, expand=True)
    dither = label.label_content != LabelContent.IMAGE_BW
    img = img.convert('L').convert('1', dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    offset = OFFSETS[label_size] * DOTS_PER_MM
    rows = [([0] * offset + [int(img.getpixel((x, y)) == 0) for x in range(img.width)] + [0] * protocol.HEAD)[:protocol.HEAD]
            for y in range(img.height)]
    return img, rows


class Puqu(Driver):
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # bt://<MAC>[/<RFCOMM channel>]
    device_patterns = (re.compile(r'bt://([0-9A-Fa-f]{2}:){5}[0-9A-Fa-f]{2}(/\d+)?'),)
    errors = (OSError, protocol.PuquError)

    def fits(self, label, model):
        return label in LABELS

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off. They leave it readable, the other way round
        # than a QL printer.
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True)

    def status(self, device, model, unconfirmed=False):
        # One that did not answer is not looked for at every status query
        error = devices.unanswered_before(device)
        if error:
            raise error
        try:
            state = self._status(device, model)
        except self.errors as e:
            devices.note_answer(device, e)
            raise
        devices.note_answer(device)
        return state

    def _status(self, device, model):
        state = network_status(device, model)
        state.update({'media_type': 'Die-cut labels', 'phase_type': 'Waiting to receive'})
        with devices.device_lock(device, timeout=devices.STATUS_LOCK_TIMEOUT) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(devices.lock_key(device), {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            reply = protocol.get_state(device)
        state['errors'] = protocol.errors(reply)
        state['status_type'] = 'Error occurred' if state['errors'] else 'Reply to status request'
        devices.status_cache[devices.lock_key(device)] = state
        return state

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            img, rows = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'puqu_{i}')
            job.append(rows)
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            error = protocol.print_labels(device, job, [len(rows) / DOTS_PER_MM for rows in job])
            devices.note_answer(device)
        except protocol.PuquError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            return 'The printer does not answer'
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            reason = devices.unanswered_reason(e)
            return (f'Could not send the label to the printer: {reason}' if reason
                    else 'Could not send the label to the printer, the log of the server names the reason')
        if error:
            logger.warning('Printing on %s failed: %s', device, error)
        return error


DRIVER = Puqu()
