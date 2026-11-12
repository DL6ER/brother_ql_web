"""Brother QL and P-touch printers, which speak Brother's raster protocol
(commands.py, raster.py), on USB (usb.py, ../backends.py) and on the network
(network.py). Based on brother_ql by Philipp Klaus and its fork
brother_ql-inventree by Matthias Mair, both GPLv3."""

import logging
import re

from .. import devices
from ..base import Driver
from ..labels import Color, FormFactor
from . import labels as tables, names, network, raster, usb
from .models import HALF_CUTTERS, MIN_PRINT_LENGTH, MIN_PTOUCH_PRINT_LENGTH, MODELS, length_problem, longest_label, resolution_problem

logger = logging.getLogger(__name__)


class BrotherRaster(Driver):
    vendor = 'Brother'
    models = tuple(MODELS)
    labels = tables.LABELS
    label_names = names.LABEL_NAMES
    hidden_labels = tables.HIDDEN_LABELS
    device_patterns = (
        re.compile(r'(file://)?/dev/usb/lp\d+'),
        re.compile(r'usb://0x[0-9a-fA-F]{4}:0x[0-9a-fA-F]{4}(/[A-Za-z0-9_-]+)?'),
        re.compile(r'tcp://[A-Za-z0-9.-]+(:9100)?'),
        # An IPv6 address in brackets
        re.compile(r'tcp://\[[0-9A-Fa-f:.]+\](:9100)?'),
    )
    mac_address_prefixes = network.MAC_ADDRESS_PREFIXES
    refusals = tuple(length_problem(model) for model in MODELS)

    def fits(self, label, model):
        return tables.label_fits(label, model)

    def colors(self, model):
        return 'red' if model in MODELS and MODELS[model].two_color else 'black'

    def palette(self, label):
        return 'red' if label.color == Color.BLACK_RED_WHITE else 'black'

    def dpi(self, high_res):
        return 600 if high_res else 300

    def label_dpi(self, label):
        # The dpi of the printers taking the label: 180 for the small P-touch
        # print head, 360 for the PT-P900 series, 300 for QL printers
        dpi = next((spec.dpi for model, spec in MODELS.items() if tables.label_fits(label, model)), 300)
        return dpi, dpi * 2

    def length_rules(self, label):
        if label.form_factor not in (FormFactor.ENDLESS, FormFactor.PTOUCH_ENDLESS):
            return None
        shortest = MIN_PTOUCH_PRINT_LENGTH if label.identifier in tables.PTOUCH_LABELS else MIN_PRINT_LENGTH
        # The longest of the printers taking the tape, each refuses more than it prints
        longest = max(longest_label(model) for model in MODELS if tables.label_fits(label, model))
        # The printer feeds the margin before and after the print
        return label.feed_margin / self.label_dpi(label)[0] * 25.4, shortest, longest

    def capabilities(self, model):
        ptouch = model.startswith('PT-')
        # The QL-500 takes no cut commands, its labels are cut with a lever
        cutting = MODELS[model].cutting
        return dict(super().capabilities(model), qualities=['fast', 'quality', 'high'],
                    high_res=not resolution_problem(model, True), cutter=cutting, manual_cutter=not cutting,
                    half_cut=model in HALF_CUTTERS, chain=ptouch, feed_waste=ptouch,
                    # The top of a label leaves a QL or P-touch printer first
                    leaves_turned=True)

    def resolution_problem(self, model, high_res):
        return resolution_problem(model, high_res)

    def status(self, device, model, unconfirmed=False):
        if device.startswith('tcp://'):
            return network.query_network_status(device, model, label_printers_only=unconfirmed)
        return usb.query_status(device, model)

    def announced(self, service):
        return network.announced(service)

    def network_device(self, address):
        return f'tcp://{address}'

    def render(self, queue, entries, keep=None):
        return raster.render(queue, entries, keep)

    def send(self, queue, job) -> str:
        """Send the raster data, the status messages of a USB printer and the
        IPP status of a network printer tell whether it was printed."""
        device = queue.device_specifier
        try:
            network_printer = device.startswith('tcp://')
            if network_printer:
                # Not sent to a printer that cannot take it, e.g. with an
                # open cover, the job would be lost
                problem = network.printer_problem(device, queue.model)
                if problem:
                    logger.warning('Not printing on %s: %s', device, problem)
                    return problem
            logger.info("Sending %d bytes to printer at %s", len(job.data), device)
            if network_printer:
                network.send(job.data, device)
                logger.info('Sent %d bytes to printer %s', len(job.data), device)
                # Port 9100 gives no answer, IPP tells how the job ended
                message = network.job_result(device, queue.model, job.page_lengths)
                if message:
                    logger.warning('Printing failed: %s', message)
                    return message
                return ""
            info = usb.send_job(job.data, device, job.page_lengths)
            logger.info('Sent %d bytes to printer %s', len(job.data), device)
            logger.info('Printer response: %s', str(info))
            if info.get('did_print') and info.get('ready_for_next_job'):
                logger.info('Label printed successfully and printer is ready for next job')
                return ""
            message = usb.print_failure(info)
            logger.warning('Printing failed: %s', message)
            return message
        except Exception as e:
            logger.exception("Exception during sending to printer: %s", e)
            reason = devices.unanswered_reason(e)
            if reason:
                return f"Could not send the label to the printer: {reason}"
            return "Could not send the label to the printer, the log of the server names the reason"


DRIVER = BrotherRaster()
