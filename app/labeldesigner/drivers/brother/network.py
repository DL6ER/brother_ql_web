"""Brother raster printers on the network: jobs go to port 9100, which
answers nothing, the status comes over SNMP or IPP."""

import http.client
import logging
import time
from urllib.parse import urlsplit

from brother_ql.reader import interpret_response

from ... import ipp, snmp
from .. import devices
from ..base import network_status
from . import usb
from .labels import MODELS

logger = logging.getLogger(__name__)

# MAC address prefix of the Wi-Fi module of a Brother QL-810W. Brother
# buys these modules, the prefix belongs to their maker and also turns up in
# other devices, so a printer found by it is checked over IPP where possible.
MAC_ADDRESS_PREFIXES = (
    "ac:f2:3c",  # Brother QL-810W
)

# How long a job on a network printer may take besides printing its labels at
# the slowest printers, and how often its state is asked over IPP meanwhile
NETWORK_JOB_TIMEOUT = 120
NETWORK_JOB_POLL = 0.5

# Network printers answer status requests over IPP, if at all
IPP_PORT = 631

# Messages for the reasons a network printer gives over IPP why it cannot
# print (printer-state-reasons, RFC 8011), without the -error suffix
IPP_REASON_MESSAGES = {
    'cover-open': 'The cover is open',
    'media-empty': 'There are no labels in the printer',
    'media-needed': 'There are no labels in the printer',
    'media-jam': 'The labels are jammed',
    'offline': 'The printer is offline',
    'paused': 'The printer is paused',
    'shutdown': 'The printer is shutting down',
    # e.g. a QL-810W after a job for labels other than the loaded ones
    'other': 'See the lights or display of the printer',
}

# media-type values of endless rolls (PWG 5100.7)
IPP_ENDLESS_MEDIA_TYPES = {'continuous', 'continuous-long', 'continuous-short', 'roll'}

# Brother printers answer SNMP requests like those of Brother's apps: with
# the status they send back over USB for "ESC i S", which tells the cause
# of an error and the loaded roll, and whether they print (Host Resources
# MIB). Over IPP a QL-810W tells neither the cause of some errors (only
# "other", e.g. for labels that cannot be fed) nor endless rolls or a
# missing roll (no media for both), so IPP is asked only when a printer
# does not answer over SNMP. The phase in the status is not reliable over
# the network, a QL-810W reports waiting while it prints.
SNMP_PORT = 161
SNMP_STATUS_OID = '1.3.6.1.4.1.2435.3.3.9.1.6.1.0'
# hrPrinterStatus (RFC 2790): 1 other (an error, or briefly at the end of
# every job), 2 unknown, 3 idle, 4 printing, 5 warmup
SNMP_PRINTER_STATUS_OID = '1.3.6.1.2.1.25.3.5.1.1.1'
SNMP_READY = (2, 3)
SNMP_PRINTING = 4
# An idle QL-810W answered only after several seconds
SNMP_TIMEOUT = 3.0
# A printer without SNMP is asked again after this many seconds
SNMP_RETRY = 60
_snmp_unavailable = {}

NO_LABELS = IPP_REASON_MESSAGES['media-empty']
# The printer keeps the job and prints it once the labels are fed
FEED_ERROR = ('The labels cannot be fed. Feed them into the slot (or insert a new roll) and close the cover, '
              'then the printer prints the waiting label by itself. Do not print it again')

# Messages for the errors of the status, by the names brother_ql gives them
STATUS_ERROR_MESSAGES = {
    'No media when printing': NO_LABELS,
    'End of media (die-cut size only)': 'The labels are used up',
    'Tape cutter jam': 'The cutter is jammed',
    'Replace media error': 'The loaded labels do not fit the job',
    'Cover opened while printing (Except QL-500)': IPP_REASON_MESSAGES['cover-open'],
    'Media cannot be fed (also when the media end is detected)': FEED_ERROR,
}


def network_printer(device_specifier: str, model: str) -> dict:
    """Status of a network printer that tells nothing about itself."""
    return network_status(device_specifier, model, model in MODELS and MODELS[model].two_color)


def printer_problem(device_specifier: str, model: str) -> str:
    """Why a network printer cannot take a job, as far as it tells over IPP,
    or an empty string. A printer that does not answer is left to the
    sending, which fails with a clearer message."""
    try:
        status = query_network_status(device_specifier, model)
    except OSError:
        return ''
    if status['errors']:
        return 'The printer reports: ' + ', '.join(status['errors'])
    return ''


def job_result(device_specifier: str, model: str, page_lengths: list) -> str:
    """After a job was sent to a network printer: the error it reports over
    IPP, or an empty string once it is idle again. Printers without IPP
    tell nothing. page_lengths: the length of each page in mm."""
    deadline = time.monotonic() + NETWORK_JOB_TIMEOUT + sum(page_lengths) / usb.SLOWEST_PRINT_SPEED
    while True:
        time.sleep(NETWORK_JOB_POLL)
        try:
            status = query_network_status(device_specifier, model)
        except OSError:
            return 'The printer did not answer after receiving the label, check whether it was printed'
        logger.debug('State of %s after the job: %s %s', device_specifier, status['status_type'], status['errors'])
        if status['errors']:
            return 'The printer reports: ' + ', '.join(status['errors'])
        if status['status_type'] not in (devices.BUSY_STATUS_TYPE, devices.STOPPED_STATUS_TYPE):
            return ''
        if time.monotonic() > deadline:
            return 'The printer did not report that it finished the label, check whether it was printed'


def _ipp_media(media) -> tuple:
    """Width across the print head and length of the loaded labels in mm
    (length 0 for endless rolls), and their type, from media-col-ready.
    IPP gives the size of the label as x by y, and with
    long-edge-first the longer side runs across the print head, e.g.
    29 x 62 mm for the 62 mm wide DK-11209."""
    if isinstance(media, list):
        media = media[0] if media else None
    if not isinstance(media, dict):
        return None, 0, None
    size = media.get('media-size') or {}
    x, y = size.get('x-dimension'), size.get('y-dimension')
    if not isinstance(x, int) or x <= 0:
        return None, 0, None
    feed = (media.get('media-source-properties') or {}).get('media-source-feed-direction')
    endless = media.get('media-type') in IPP_ENDLESS_MEDIA_TYPES or not isinstance(y, int) or y <= 0
    if endless:
        return round(x / 100), 0, 'Continuous length tape'
    across, along = (max(x, y), min(x, y)) if feed == 'long-edge-first' else (x, y)
    return round(across / 100), round(along / 100), 'Die-cut labels'


def _ipp_errors(attributes: dict) -> list:
    """Messages for the reasons why the printer cannot print. Reasons with
    the suffix -report or -warning do not stop it, the others do. A
    QL-810W is briefly "stopped" without a reason at the end of every job,
    so only reasons count."""
    reasons = attributes.get('printer-state-reasons') or []
    if isinstance(reasons, str):
        reasons = [reasons]
    errors = []
    for reason in reasons:
        if reason == 'none' or reason.endswith(('-report', '-warning')):
            continue
        keyword = reason.removesuffix('-error')
        message = IPP_REASON_MESSAGES.get(keyword, f'The printer reports "{keyword}"')
        if message not in errors:
            errors.append(message)
    return errors


def _snmp_network_status(device_specifier: str, host: str, default_model: str) -> dict | None:
    """Status of a Brother printer over SNMP: model, loaded labels, why it
    cannot print and whether it prints. None if it does not answer."""
    failed = _snmp_unavailable.get(host)
    if failed is not None and time.monotonic() - failed < SNMP_RETRY:
        return None
    try:
        data, printer_status = snmp.get_values(host, [SNMP_STATUS_OID, SNMP_PRINTER_STATUS_OID],
                                               port=SNMP_PORT, timeout=SNMP_TIMEOUT)
        if not isinstance(data, bytes) or not isinstance(printer_status, int):
            raise snmp.SNMPError(f'No status: {data!r}, {printer_status!r}')
        state = interpret_response(data)
    except (OSError, snmp.SNMPError, NameError) as e:
        # brother_ql raises NameError for an answer that is no status
        logger.debug('No status over SNMP from %s: %s', host, e)
        _snmp_unavailable[host] = time.monotonic()
        return None
    _snmp_unavailable.pop(host, None)
    model = state.get('model')
    status = network_printer(device_specifier, model if model in MODELS else default_model)
    errors = [STATUS_ERROR_MESSAGES.get(error, error) for error in state['errors']]
    if state['media_width']:
        status['media_width'] = state['media_width']
        status['media_length'] = state['media_length']
        status['media_type'] = state['media_type']
    elif not errors:
        errors = [NO_LABELS]
    status['errors'] = errors
    printing = printer_status == SNMP_PRINTING
    if errors:
        status['status_type'] = 'Error occurred'
    elif printing:
        status['status_type'] = devices.BUSY_STATUS_TYPE
    else:
        status['status_type'] = 'Reply to status request' if printer_status in SNMP_READY else devices.STOPPED_STATUS_TYPE
    status['phase_type'] = 'Printing state' if printing else 'Waiting to receive'
    return status


def label_printer_model(name: str) -> str | None:
    """The brother_ql model of a name like "Brother QL-810W", None for other
    devices, e.g. office printers."""
    model = (name or '').removeprefix('Brother ').strip()
    return model if model in MODELS else None


def query_network_status(device_specifier: str, default_model: str = 'Unknown',
                         label_printers_only: bool = False) -> dict | None:
    """Status of a network printer: model, loaded labels, why it cannot
    print and whether it prints, over SNMP like Brother's apps ask for it,
    else over IPP. Printers that answer neither only take jobs on port 9100
    and tell nothing about themselves. With label_printers_only, None for
    a device whose IPP model is no label printer. Raises OSError if the
    printer does not answer at all."""
    host = urlsplit(device_specifier).hostname
    status = _snmp_network_status(device_specifier, host, default_model)
    if status is None:
        return _ipp_network_status(device_specifier, host, default_model, label_printers_only)
    # Kept for when the printer does not answer
    devices.status_cache[devices.lock_key(device_specifier)] = status
    return dict(status)


def _ipp_network_status(device_specifier: str, host: str, default_model: str, label_printers_only: bool) -> dict | None:
    """Status of a network printer over IPP, see query_network_status."""
    try:
        attributes = ipp.get_printer_attributes(host, IPP_PORT)
    except (ConnectionRefusedError, ipp.IPPError, http.client.HTTPException) as e:
        logger.debug('No IPP status from %s: %s', device_specifier, e)
        return network_printer(device_specifier, default_model)
    model = label_printer_model(str(attributes.get('printer-make-and-model') or ''))
    if model is None:
        if label_printers_only:
            logger.debug('%s is no label printer: %s', device_specifier, attributes.get('printer-make-and-model'))
            return None
        model = default_model
    status = network_printer(device_specifier, model)
    status['media_width'], status['media_length'], status['media_type'] = _ipp_media(attributes.get('media-col-ready'))
    status['errors'] = _ipp_errors(attributes)
    state = attributes.get('printer-state')
    busy = state == ipp.PROCESSING
    if status['errors']:
        status['status_type'] = 'Error occurred'
    elif busy:
        status['status_type'] = devices.BUSY_STATUS_TYPE
    else:
        status['status_type'] = devices.STOPPED_STATUS_TYPE if state == ipp.STOPPED else 'Reply to status request'
    status['phase_type'] = 'Printing state' if busy else 'Waiting to receive'
    # Kept for when the printer does not answer
    devices.status_cache[devices.lock_key(device_specifier)] = status
    return dict(status)


def announced(service: dict) -> tuple[str, str] | None:
    """(tcp://<address>[:<port>], model) of a Brother label printer that
    announces itself, None for other devices."""
    txt = service['txt']
    model = label_printer_model(txt.get('usb_mdl') or '') or label_printer_model(txt.get('ty') or '')
    if model is None:
        return None
    # IPP announces its own port, jobs go to the raw port
    port = service['port'] if service['service'].startswith('_pdl-datastream.') else None
    return f"tcp://{service['address']}" + (f':{port}' if port and port != 9100 else ''), model
