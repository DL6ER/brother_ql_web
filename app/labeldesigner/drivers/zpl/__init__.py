"""Label printers on ZPL (protocol.py), e.g. a Zebra GK420t or a
Datamax-O'Neil E-4204B with its ZPL emulation, tested on the Datamax only.
Die-cut labels, which the printer finds by their gaps, torn off. The label
is sent as an image. The printer is asked for its status with ~HS: its
errors and the length of the labels it measured, which picks their size."""

import logging
import os
import re
import time

from PIL import Image

from .. import devices
from ..base import Driver, network_status, shown
from ..labels import FormFactor, Label
from ...enums import LabelContent
from . import protocol

logger = logging.getLogger(__name__)

MODELS = ('Zebra/ZPL',)
DOTS_PER_MM = 8
# A printhead of 104 mm, as on a GK420t, a 105 mm label loses 1 mm
HEAD = 832
# Width x length in mm of common labels, metric ones and inch ones, e.g.
# 105 x 148 mm (A6) and 102 x 152 mm (4 x 6 in) shipping labels. The printer
# does not tell which are loaded.
SIZES = ((105, 148),
         (102, 152), (102, 102), (102, 76), (102, 51), (102, 25),
         (100, 150), (100, 100), (100, 70), (100, 50), (100, 30),
         (80, 50), (76, 51), (76, 25), (60, 40), (58, 40), (57, 32),
         (51, 25), (50, 30), (50, 25), (40, 30), (40, 20), (38, 25), (30, 20))


def _label(width: int, length: int) -> Label:
    total = (width * DOTS_PER_MM, length * DOTS_PER_MM)
    return Label(f'zpl{width}x{length}', (width, length), FormFactor.DIE_CUT, total,
                 (min(total[0], HEAD), total[1]), 0, models=MODELS)


# A Datamax-O'Neil E-4204B leaves ~HS unanswered within about 5 s of the one
# before: a printer is asked at most every ASK_EVERY seconds, told as it was
# in between
ASK_EVERY = 10
_asked: dict[str, float] = {}
NO_ANSWER = 'The printer does not answer, is its printhead open?'

# A measured length further off than this from all sizes is not taken
LENGTH_TOLERANCE = 2


def loaded(length: int) -> tuple[int, int] | None:
    """Width and length in mm of the labels of a length the printer measured
    in dots, the first size of the nearest length, e.g. 235 dots (29.4 mm)
    100 x 30 mm. The printer does not tell their width."""
    mm = length / DOTS_PER_MM
    size = min(SIZES, key=lambda size: abs(size[1] - mm))
    return size if abs(size[1] - mm) <= LENGTH_TOLERANCE else None


LABELS = tuple(_label(width, length) for width, length in SIZES)
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (ZPL)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots across the
    printhead, the last one comes out first, and the bytes of each row."""
    img = label.generate()
    # The bottom of a ZPL label leaves the printer first, on a Zebra GK420t as
    # on a Datamax-O'Neil E-4204B. Turned upside down its top does, as on the other
    # printers. Labels longer than wide, which the label designer draws lying,
    # are turned as by the other drivers, their end leaves first.
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    img = img.rotate(-90 if img.width != across else 180, expand=True)
    dither = label.label_content != LabelContent.IMAGE_BW
    img = img.convert('L').convert('1', dither=Image.Dither.FLOYDSTEINBERG if dither else Image.Dither.NONE)
    # 1 bit images of PIL are packed MSB first with white 1, ZPL prints 1
    width = (img.width + 7) // 8
    data = bytes(255 - byte for byte in img.tobytes())
    rows = [data[y * width:(y + 1) * width] for y in range(img.height)]
    # The padding of the last byte of each row is white
    if img.width % 8:
        mask = (0xff << (8 - img.width % 8)) & 0xff
        rows = [row[:-1] + bytes([row[-1] & mask]) for row in rows]
    return img, rows, width


class Zpl(Driver):
    vendor = 'Zebra'
    models = MODELS
    labels = LABELS
    label_names = NAMES
    # usblp's device, also a name a udev rule gives it, e.g.
    # /dev/usb/lp_datamax, or the printer on the network on the port of raw
    # printing only, as the other drivers
    device_patterns = (re.compile(r'(file://)?/dev/usb/lp\w+'),
                       re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
                       re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'))

    def fits(self, label, model):
        return label in LABELS

    def dpi(self, high_res):
        return protocol.DPI

    def resolution_problem(self, model, high_res):
        return f'The {shown(model)} printer cannot print at 600 dpi, turn off 600 dpi printing' if high_res else ''

    def capabilities(self, model):
        # The labels are torn off, their top leaves it first
        return dict(super().capabilities(model), qualities=['quality'], cutter=False, leaves_turned=True)

    def status(self, device, model, unconfirmed=False):
        path = device.removeprefix('file://')
        if not device.startswith('tcp://') and not os.path.exists(path):
            raise FileNotFoundError(2, 'No such device', path)
        state = dict(network_status(device, model), media_type='Die-cut labels')
        cached = devices.status_cache.get(devices.lock_key(device))
        if cached and time.monotonic() - _asked.get(device, 0) < ASK_EVERY:
            return dict(cached)
        with devices.device_lock(device, timeout=devices.STATUS_LOCK_TIMEOUT) as acquired:
            if not acquired:
                state.update(devices.status_cache.get(devices.lock_key(device), {}))
                state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
                return state
            answer = protocol.parse_host_status(protocol.host_status(device))
            _asked[device] = time.monotonic()
        if answer is None:
            # A printer that never answered ~HS is told as before, without
            # errors. A Datamax-O'Neil E-4204B with its printhead open does
            # not answer.
            if cached:
                state.update(cached)
                state.update({'errors': [NO_ANSWER], 'status_type': 'Error occurred'})
                devices.status_cache[devices.lock_key(device)] = state
            return state
        state['errors'] = protocol.errors(answer)
        state['status_type'] = 'Error occurred' if state['errors'] else 'Reply to status request'
        state['phase_type'] = 'Waiting to receive'
        # The label designer then picks the labels
        size = loaded(answer['length'])
        if size:
            state['media_width'], state['media_length'] = size
        devices.status_cache[devices.lock_key(device)] = state
        return state

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            img, rows, width = label_rows(entry['label'], queue.label_size)
            if keep:
                keep(img, f'zpl_{i}')
            job.append(protocol.label(rows, width))
        return job

    def send(self, queue, job) -> str:
        device = queue.device_specifier
        try:
            protocol.write(device, b''.join(job))
        except OSError as e:
            logger.warning('Printing on %s failed: %s', device, e)
            reason = devices.unanswered_reason(e)
            return (f'Could not send the label to the printer: {reason}' if reason
                    else 'Could not send the label to the printer, the log of the server names the reason')
        return ''


DRIVER = Zpl()
