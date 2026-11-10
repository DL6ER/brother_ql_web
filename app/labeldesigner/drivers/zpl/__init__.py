"""Label printers on ZPL (protocol.py), e.g. a Zebra GK420t or a
Datamax-O'Neil E-4204B with its ZPL emulation, tested on the Datamax only.
Die-cut labels, which the printer finds by their gaps, torn off. The label
is sent as an image. The printer is not asked for its status."""

import logging
import os
import re

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
# Width x length in mm, e.g. 105 x 148 mm (A6) and 102 x 152 mm (4 x 6 in)
# shipping labels
SIZES = ((105, 148), (102, 152), (100, 150), (102, 76), (102, 51), (57, 32), (51, 25))


def _label(width: int, length: int) -> Label:
    total = (width * DOTS_PER_MM, length * DOTS_PER_MM)
    return Label(f'zpl{width}x{length}', (width, length), FormFactor.DIE_CUT, total,
                 (min(total[0], HEAD), total[1]), 0, models=MODELS)


LABELS = tuple(_label(width, length) for width, length in SIZES)
NAMES = {label.identifier: f'{label.tape_size[0]} x {label.tape_size[1]} mm die-cut (ZPL)' for label in LABELS}


def label_rows(label, label_size: str) -> tuple[Image.Image, list[bytes], int]:
    """A label as the printer prints it: rows of packed dots across the
    printhead, the first one comes out first, and the bytes of each row."""
    img = label.generate()
    # Labels longer than wide, which the label designer draws lying, are
    # turned as by the other drivers, their end leaves the printer first
    across = next(size.dots_printable[0] for size in LABELS if size.identifier == label_size)
    if img.width != across:
        img = img.rotate(90, expand=True)
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
        # Not asked: only a printer on USB that is not there is told
        path = device.removeprefix('file://')
        if not device.startswith('tcp://') and not os.path.exists(path):
            raise FileNotFoundError(2, 'No such device', path)
        return dict(network_status(device, model), media_type='Die-cut labels')

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
