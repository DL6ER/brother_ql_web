"""Brother raster printers on USB: jobs and their status messages, and
status queries."""

import logging
import threading
import time

from .. import devices
from ..backends import open_printer
from .status import HEADER, LENGTH, parse_status

logger = logging.getLogger(__name__)

# How long a USB printer may stay silent after a job was sent and between
# its status messages: as long as a page of the job takes to print at the
# slowest printers, about 10 mm/s, at least STATUS_WAIT seconds.
STATUS_WAIT = 10
SLOWEST_PRINT_SPEED = 10
# A job is written in pieces: pyusb gives up on a single write after 15 s,
# a long label that the printer prints while it receives it takes longer
WRITE_CHUNK = 16384


def send_job(data: bytes, device_specifier: str, page_lengths: list) -> dict:
    """Send a print job to a USB printer and read its status messages until
    it reports every page printed and that it is ready for the next job, an
    error, or until it stays silent for longer than a page takes to print.
    A long label is printed while it is received, a job of several labels
    sends messages for each of them.

    page_lengths: the length of each page in mm. Returns, as print_failure()
    reads it: did_print, ready_for_next_job, the last printer_state, and the
    pages of the job and how many the printer confirmed."""
    info = {'did_print': False, 'ready_for_next_job': False, 'printer_state': None,
            'pages': len(page_lengths), 'confirmed': 0}
    printer = get_printer(device_specifier)
    try:
        _drain(printer)
        received = _write_reading(printer, data)
        # A page may take as long as the longest one, the whole job as long
        # as all its pages
        page_wait = STATUS_WAIT + max(page_lengths, default=0) / SLOWEST_PRINT_SPEED
        give_up = time.monotonic() + sum(STATUS_WAIT + length / SLOWEST_PRINT_SPEED for length in page_lengths)
        deadline = min(time.monotonic() + page_wait, give_up)
        printing = False
        while time.monotonic() < deadline:
            states, received = _status_messages(received + (printer.read(32) or b''))
            if not states:
                time.sleep(0.02)
                continue
            deadline = min(time.monotonic() + page_wait, give_up)
            for state in states:
                info['printer_state'] = state
                if state['errors']:
                    return info
                if state['status_type'] == 'Printing completed':
                    info['confirmed'] += 1
                    printing = False
                elif state['phase_type'] == 'Printing state':
                    printing = True
                elif printing:
                    # The QL-800 and QL-1100 report only the last page of a job
                    # completed, the others end with them waiting to receive again
                    info['confirmed'] += 1
                    printing = False
                if state['status_type'] == 'Phase change':
                    # "Printing" before each page, "waiting to receive" after it
                    info['ready_for_next_job'] = state['phase_type'] == 'Waiting to receive'
            info['did_print'] = info['confirmed'] >= info['pages']
            if info['did_print'] and info['ready_for_next_job']:
                return info
        return info
    finally:
        _release(printer)


def _write_reading(printer, data: bytes) -> bytes:
    """Write a job in pieces and return what the printer said meanwhile. A
    QL-700 takes a job only as fast as it prints it and keeps just two status
    messages that are not read, so they are read while the job is written."""
    received, failed = [], []
    written = threading.Event()

    def read():
        try:
            while not written.is_set():
                data = printer.read(32)
                if data:
                    received.append(data)
                else:
                    written.wait(0.005)
        except Exception as e:
            failed.append(e)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        for start in range(0, len(data), WRITE_CHUNK):
            printer.write(data[start:start + WRITE_CHUNK])
    finally:
        written.set()
        reader.join()
    if failed:
        raise failed[0]
    return b''.join(received)


def _drain(printer):
    """Read what the printer still has to say, e.g. the status messages of an
    earlier job, so that they are not taken for those of this one."""
    for _ in range(10):
        if not printer.read(32):
            return


def _status_messages(received: bytes) -> tuple[list, bytes]:
    """The status messages in what was read from the printer, and the rest,
    which may be the beginning of the next one. Anything before a message is
    skipped."""
    states = []
    while True:
        start = received.find(HEADER)
        if start < 0:
            # The end may be the beginning of a header
            return states, received[-(len(HEADER) - 1):]
        received = received[start:]
        if len(received) < 32:
            return states, received
        try:
            states.append(parse_status(received[:LENGTH]))
        except Exception:
            logger.warning('Could not read a status message of the printer: %s', received[:32].hex())
        received = received[32:]


def print_failure(info: dict) -> str:
    """Why a print job was not confirmed, from the status read back from the
    printer while printing."""
    state = info.get('printer_state') or {}
    if state.get('errors'):
        return 'The printer reports: ' + ', '.join(state['errors'])
    if 0 < info.get('confirmed', 0) < info.get('pages', 0):
        return f"The printer confirmed {info['confirmed']} of {info['pages']} labels, check whether the others were printed"
    if info.get('did_print'):
        return 'The label was printed, but the printer did not report that it is ready for the next one'
    if state:
        return f"The printer did not confirm that the label was printed (last status: {state.get('status_type')}, {state.get('phase_type')})"
    return 'The printer did not answer after receiving the label, check whether it was printed'


def get_printer(device_specifier: str):
    """A connection to the printer, see backends.open_printer()."""
    return open_printer(device_specifier)


def _read_status(printer, timeout: float) -> bytes:
    """Ask for the status, the 32-byte answer or what came until the timeout.
    Printers such as the QL-1100 need about 100 ms to answer and return empty
    reads until then."""
    printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
    data = b''
    deadline = time.time() + timeout
    while len(data) < 32 and time.time() < deadline:
        data += printer.read(32 - len(data)) or b''
        if len(data) < 32:
            time.sleep(0.02)
    return data


def get_status(printer, timeout: float = 2.0):
    """Request and parse the printer status."""
    data = _read_status(printer, timeout)
    if len(data) < 32:
        # The QL-560 answers only the first request after power-on. Invalidate
        # and initialize only then: "ESC @" also cancels printing.
        printer.write(b"\x00" * 200 + b"\x1b\x40")
        data = _read_status(printer, timeout)
    if len(data) < 32:
        raise TimeoutError(f"No complete status response within {timeout} s")
    return parse_status(data)


def _release(printer):
    """Close the printer handle right away, also when the status query failed
    and a traceback still references the printer. The backend closes the
    handle again when the object is garbage collected, which would close an
    unrelated file if the descriptor number was reused in the meantime, so
    that second close is turned into a no-op."""
    printer.dispose()
    printer.dispose = lambda: None


def query_status(device_specifier: str, default_model: str = 'Unknown') -> dict:
    """Query the printer status without waiting for a running print job. While
    a job holds the device, the last known status is returned instead."""
    key = devices.lock_key(device_specifier)
    with devices.device_lock(device_specifier, timeout=devices.STATUS_LOCK_TIMEOUT) as acquired:
        if acquired:
            printer = get_printer(device_specifier)
            try:
                state = get_status(printer)
            finally:
                # Release the device before giving up the lock
                _release(printer)
            devices.status_cache[key] = state
            return dict(state)
    logger.debug('Printer %s is busy, returning last known status', device_specifier)
    # Model and media from the last query, but the state of a running job:
    # errors or the idle state from before the job no longer apply
    state = dict(devices.status_cache.get(key, {'model': default_model, 'media_width': None, 'media_length': 0}))
    state.update({'errors': [], 'status_type': devices.BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
    return state
