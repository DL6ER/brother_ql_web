"""The Brother VC-500W, a full color printer for ZINK tape, which speaks a
protocol of its own (protocol.py) on the network and on USB."""

import re

from .. import devices
from ..base import Driver
from . import image, printing, protocol, status, tapes

# It keeps labels it does not cut until the next print, which follows them
# without a cut
NEVER_CUT = 'The VC-500W cannot leave the labels uncut, cut them after the last label'
NEVER_WHY = 'The VC-500W would keep the labels inside until the next print'


class VC500W(Driver):
    models = (tapes.MODEL,)
    labels = tapes.LABELS
    label_names = tapes.NAMES
    device_patterns = (
        re.compile(r'(file://)?/dev/usb/lp\d+'),
        re.compile(r'vc500w://[A-Za-z0-9.-]+(:9100)?'),
    )
    errors = (OSError, protocol.VC500WError)

    def model_of(self, device):
        # It must never get the commands of a QL printer
        if not isinstance(device, str) or device == 'simulation':
            return None
        if device.startswith('vc500w://') or protocol.device_id_is_vc500w(devices.usb_device_id(device)):
            return tapes.MODEL
        return None

    def fits(self, label, model):
        return label in tapes.LABELS

    def cut_problem(self, cut, model):
        return NEVER_CUT if cut == 'none' else ''

    def colors(self, model):
        return 'color'

    def palette(self, label):
        return 'color'

    def dpi(self, high_res):
        return protocol.DPI

    def capabilities(self, model):
        # A label takes about 40 s, the label designer shows its stages
        return dict(super().capabilities(model), half_cut=True, job_stages=True, never_cut=NEVER_WHY)

    def status(self, device, model, unconfirmed=False):
        return status.query_status(device)

    def announced(self, service):
        if status.announces_vc500w(service['txt']):
            return f"vc500w://{service['address']}", tapes.MODEL
        return None

    def render(self, queue, entries, keep=None):
        job = []
        for i, entry in enumerate(entries):
            mode = 'fast' if entry['fast'] else 'quality'
            job.append((entry['cut'], mode, image.label_image(entry['label'], queue.label_size, mode)))
            if keep:
                keep(job[-1][2], f'vc500w_{i}')
        return job

    def send(self, queue, job):
        return printing.print_labels(queue, job)


DRIVER = VC500W()
