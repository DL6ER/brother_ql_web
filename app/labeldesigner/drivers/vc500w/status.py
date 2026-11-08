"""Status of a VC-500W: the tape in it, how much is left, errors, the stage
of a label it prints and whether its Wi-Fi is weak."""

from urllib.parse import urlsplit

from .. import devices
from ..base import network_status
from . import protocol
from .tapes import MODEL, TAPES

# The stage of the label a VC-500W prints, by lock key, while a job holds
# the device: a status query cannot ask it then
jobs = {}


def target(device_specifier: str) -> tuple[str, int]:
    """Host and port of a VC-500W on the network, also configured as
    tcp://<host>, or its path on USB."""
    if device_specifier.startswith(('vc500w://', 'tcp://')):
        url = urlsplit(device_specifier)
        return url.hostname, url.port or protocol.PORT
    if device_specifier.startswith('file://'):
        return device_specifier[len('file://'):], protocol.PORT
    return device_specifier, protocol.PORT


def error_message(error: str) -> str:
    """A message for an error of a VC-500W: its own or the one it reports."""
    if error == protocol.EJECT_JAM:
        # A label lies in the output, also the tape a new cassette feeds
        return protocol.TAKE_OUT
    if error in (protocol.TAKE_OUT, protocol.DAMAGED, protocol.NO_ANSWER, protocol.UNFINISHED, protocol.NO_CASSETTE,
                 protocol.BUSY, protocol.BLOCKED, protocol.TOO_LONG):
        return error
    return f'The printer reports: {error.replace("_", " ").capitalize()}'


def query_status(device_specifier: str) -> dict:
    """Status of a VC-500W: the tape in it, how much is left, errors and
    whether its Wi-Fi is weak. While it prints, the stage of the label
    instead. Raises OSError or VC500WError if it does not answer."""
    key = devices.lock_key(device_specifier)
    where = target(device_specifier)
    # A job of this server holds the printer, it tells the stage right away
    with devices.device_lock(device_specifier, timeout=0 if key in jobs else devices.STATUS_LOCK_TIMEOUT) as acquired:
        if acquired:
            config, status = protocol.get_state(*where)
            state = network_status(device_specifier, config['model'] or MODEL)
            width = config['tape_width_mm']
            if width:
                # The nominal width of the tape, e.g. 25 for 24.8 mm
                state['media_width'] = min(TAPES, key=lambda tape: abs(tape - width))
                state['media_type'] = 'Continuous length tape'
            state['tape_length_mm'] = config['tape_length_mm']
            state['tape_remaining_mm'] = status['tape_remaining_mm']
            error = protocol.ready_error(status)
            state['errors'] = [error_message(error)] if error else []
            busy = status['state'] not in ('', protocol.IDLE)
            state['status_type'] = 'Error occurred' if error else devices.BUSY_STATUS_TYPE if busy else 'Reply to status request'
            state['phase_type'] = 'Printing state' if busy else 'Waiting to receive'
            if busy and not error:
                state['job'] = {'stage': protocol.stage_name(status['stage'])}
            devices.status_cache[key] = state
            return dict(state, **_link(device_specifier, where))
    state = dict(devices.status_cache.get(key, network_status(device_specifier, MODEL)))
    state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
    state.update(_link(device_specifier, where))
    if key in jobs:
        state['job'] = dict(jobs[key])
    return state


def _link(device_specifier: str, where: tuple[str, int]) -> dict:
    # Moving the printer closer to the router helps only on Wi-Fi, not on USB
    if not device_specifier.startswith(('vc500w://', 'tcp://')):
        return {}
    return {'weak_connection': protocol.lost_answers(*where) > 0}


def announces_vc500w(txt: dict) -> bool:
    # It announces only IPP, its model as product, e.g. "Brother VC-500W"
    names = (txt.get('usb_mdl'), txt.get('ty'), (txt.get('product') or '').strip('()'))
    return any((name or '').removeprefix('Brother ').strip() == MODEL for name in names)
