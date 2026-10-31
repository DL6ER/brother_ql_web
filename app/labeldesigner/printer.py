import logging
import os
import stat
import time
import hashlib
import datetime
import tempfile
import threading
import contextlib
import http.client
from urllib.parse import urlsplit
from brother_ql.backends.helpers import send
from brother_ql import BrotherQLRaster, create_label
from brother_ql.reader import interpret_response
from brother_ql.backends import backend_factory, guess_backend
from flask import Config
from .label import LabelOrientation, LabelType, LabelContent
from .label_names import check_label_size
from brother_ql.models import ALL_MODELS
from brother_ql.labels import ALL_LABELS
from brother_ql.devicedependent import label_type_specs
from werkzeug.security import safe_join
from . import ipp, mdns

try:
    import fcntl
except ImportError:  # Not available on Windows
    fcntl = None

SIMULATED_LABELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'simulated_labels')

# Number of simulated labels kept in SIMULATED_LABELS_DIR, older ones are
# deleted so that printing to the simulator cannot fill up the disk
MAX_SIMULATED_LABELS = 100

# Default maximum number of labels to rasterize and send in a single batch.
# Sending too many labels at once can cause printer timeouts/failures.
# Override via PRINT_BATCH_SIZE environment variable or Flask config.
DEFAULT_BATCH_SIZE = 5

logger = logging.getLogger(__name__)

# Where printers are looked for when PRINTER_PRINTER is '?'. The tests point
# them elsewhere, so that they never find, let alone print on, a real printer.
USB_PRINTER_DEVICES = [f'/dev/usb/lp{i}' for i in range(0, 11)]
ARP_TABLE = '/proc/net/arp'
# Network printers that announce themselves (DNS-SD, as for Bonjour and
# CUPS). Asking takes a second, the answer is kept for a minute.
NETWORK_DISCOVERY = True
DISCOVERY_INTERVAL = 60
# (time.monotonic() of the last discovery, printers found), None before the
# first. The monotonic clock starts at boot, so a time of 0 would count as
# recent in a container started less than a minute ago.
_discovered = None

# MAC address prefix of the Wi-Fi module of a Brother QL-810W. Brother
# buys these modules, the prefix belongs to their maker and also turns up in
# other devices, so a printer found by it is checked over IPP where possible.
BROTHER_MAC_ADDRESS_PREFIXES = [
    "ac:f2:3c",  # Brother QL-810W
]

# Horizontal offsets (offset_r, in dots) that differ from the label table of
# brother_ql, measured on real printers. Remove an entry once brother_ql
# ships the corrected value.
# - 103x164 (103 x 164 mm die-cut): with brother_ql's 12 the content is 1.5 mm
#   off-centre on a QL-1100 (3.5 mm left, 6.5 mm right), -6 centres it. The
#   104 mm endless entry of brother_ql already uses a similar -8.
LABEL_OFFSET_CORRECTIONS = {
    '103x164': -6,
}


def _apply_label_offset_corrections():
    for label in ALL_LABELS:
        if label.identifier in LABEL_OFFSET_CORRECTIONS:
            label.offset_r = LABEL_OFFSET_CORRECTIONS[label.identifier]
    # brother_ql copies the offsets into label_type_specs when it is imported,
    # the conversion to raster data reads them from there
    for identifier, offset in LABEL_OFFSET_CORRECTIONS.items():
        if identifier in label_type_specs:
            label_type_specs[identifier]['right_margin_dots'] = offset


_apply_label_offset_corrections()

# Base directory for the lock files serializing access to the printer devices
LOCK_DIR = tempfile.gettempdir()

# Seconds a print job waits for another job or a status query to release the
# printer before it gives up
DEVICE_LOCK_TIMEOUT = 10

# Seconds a status query waits for the printer. A bit longer than a status
# query itself may take, so that two queries do not get in each other's way.
# If the printer is still locked after that, a print job is running.
STATUS_LOCK_TIMEOUT = 2.5
BUSY_STATUS_TYPE = 'Printing'

_fallback_locks = {}
_fallback_locks_guard = threading.Lock()
_status_cache = {}


def _lock_key(device_specifier: str) -> str:
    """The same key for every spelling of a device that requests may use.
    A printer addressed both as usb://<vendor>:<product> and as
    /dev/usb/lp<N> cannot be recognized as the same device without asking
    it, so it should be addressed in one way only."""
    key = device_specifier.strip()
    if key.startswith('file://'):
        key = key[len('file://'):]
    elif key.startswith(('tcp://', 'usb://')):
        key = key.lower()
        if key.startswith('tcp://') and ':' not in key[len('tcp://'):]:
            key += ':9100'
    return key


def _private_lock_dir() -> str:
    """Directory for the lock files that only this user can access. In a
    shared directory like /tmp other users could hold the locks or leave files
    behind that this user cannot open."""
    path = os.path.join(LOCK_DIR, f'brother_ql_web-{os.getuid()}')
    os.makedirs(path, mode=0o700, exist_ok=True)
    info = os.lstat(path)
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise PermissionError(f'{path} is not a private directory')
    return os.path.normpath(path)


def _lock_slot(key: str) -> str:
    """Map a device to one of 4096 lock slots. Requests can name any number of
    devices, the slots keep the number of lock files bounded. Two devices
    sharing a slot (unlikely with a handful of printers) are serialized."""
    return hashlib.sha256(key.encode()).hexdigest()[:3]


def _open_lock_file(slot: str):
    if fcntl is None:
        return None
    try:
        path = safe_join(_private_lock_dir(), f'printer-{slot}.lock')
        if path is None:
            raise ValueError(f'Invalid lock slot: {slot}')
        return os.open(path, os.O_RDONLY | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    except OSError as e:
        logger.warning('Cannot open lock file, locking within this process only: %s', e)
        return None


def _flock(fd: int, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except BlockingIOError:
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)


@contextlib.contextmanager
def device_lock(device_specifier: str, timeout: float | None = None):
    """Serialize access to a printer device.

    The kernel allows only one open handle per USB printer, so a status query
    during a print job (or the other way round) fails with "Device or resource
    busy". A lock file is used so that this also works across several worker
    processes. Waits at most timeout seconds (default DEVICE_LOCK_TIMEOUT,
    0 = do not wait) and yields whether the lock was acquired."""
    if timeout is None:
        timeout = DEVICE_LOCK_TIMEOUT
    slot = _lock_slot(_lock_key(device_specifier))
    fd = _open_lock_file(slot)
    if fd is not None:
        try:
            acquired = _flock(fd, timeout)
        except OSError as e:
            # e.g. ENOLCK on file systems without flock() support
            logger.warning('Cannot lock lock file, locking within this process only: %s', e)
            os.close(fd)
            fd = None

    if fd is None:
        with _fallback_locks_guard:
            lock = _fallback_locks.setdefault(slot, threading.Lock())
        acquired = lock.acquire(timeout=timeout) if timeout > 0 else lock.acquire(blocking=False)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
        return

    try:
        yield acquired
    finally:
        # Closing the file releases the lock
        os.close(fd)


def is_simulation(device_specifier) -> bool:
    return isinstance(device_specifier, str) and device_specifier in ['simulation', '?']


def _prune_simulated_labels():
    # The names start with a timestamp, so sorting by name sorts by age
    labels = sorted((entry for entry in os.scandir(SIMULATED_LABELS_DIR) if entry.name.endswith('.png')), key=lambda entry: entry.name)
    for entry in labels[:-MAX_SIMULATED_LABELS]:
        try:
            os.remove(entry.path)
        except OSError:
            logger.debug('Failed to remove simulated label %s', entry.path, exc_info=True)


class PrinterQueue:
    def __init__(self, model, device_specifier, label_size):
        # A clear message instead of "Wrong pixel width" from brother_ql
        check_label_size(label_size, model)
        self.model = model
        self.device_specifier = device_specifier
        self.label_size = label_size
        self._print_queue = []

    def add_label_to_queue(self, label, cut: bool = True, high_res: bool = False):
        self._print_queue.append({
            'label': label,
            'cut': cut,
            'high_res': high_res
        })

    def _rasterize_entries(self, entries):
        """Rasterize a list of queue entries into a BrotherQLRaster and
        return ``(qlr, generated_images)``."""
        qlr = BrotherQLRaster(self.model)
        generated_images = []
        simulation = is_simulation(self.device_specifier)
        for entry in entries:
            label = entry['label']
            cut = entry['cut']
            high_res = entry['high_res']
            if label.label_type == LabelType.ENDLESS_LABEL:
                rotate = 0 if label.label_orientation == LabelOrientation.STANDARD else 90
            else:
                rotate = 'auto'
            img = label.generate()
            if simulation:
                generated_images.append(img)
            dither = label.label_content != LabelContent.IMAGE_BW
            create_label(
                qlr,
                img,
                self.label_size,
                red='red' in str(self.label_size),
                dither=dither,
                cut=cut,
                dpi_600=high_res,
                rotate=rotate
            )
        return qlr, generated_images

    def _send_raster(self, qlr, generated_images, batch_index=0, batch_count=1) -> str:
        """Send rasterized data to the printer or simulator.
        Returns an empty string on success, or an error message."""
        # Only jobs split into several batches name the batch
        batch = f' (batch {batch_index} of {batch_count})' if batch_count > 1 else ''
        try:
            # Simulator: pretend we sent data, save labels as PNG, and return success
            if is_simulation(self.device_specifier):
                logger.info('Simulated sending %d bytes to simulator printer (batch %d)',
                            len(qlr.data), batch_index)
                os.makedirs(SIMULATED_LABELS_DIR, exist_ok=True)
                ts = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S_%f')
                for i, img in enumerate(generated_images):
                    path = os.path.join(SIMULATED_LABELS_DIR, f'{ts}_b{batch_index}_{i}.png')
                    img.save(path, format='PNG')
                    logger.info('Saved simulated label to %s', path)
                _prune_simulated_labels()
                return ""

            network_printer = isinstance(self.device_specifier, str) and self.device_specifier.startswith('tcp://')
            if network_printer:
                # Not sent to a printer that cannot take it, e.g. with an
                # open cover, the job would be lost
                problem = _network_printer_problem(self.device_specifier, self.model)
                if problem:
                    logger.warning('Not printing on %s: %s', self.device_specifier, problem)
                    return problem + batch
            logger.info("Sending %d bytes to printer at %s (batch %d)",
                        len(qlr.data), self.device_specifier, batch_index)
            info = send(qlr.data, self.device_specifier)
            logger.info('Sent %d bytes to printer %s', len(qlr.data), self.device_specifier)
            if network_printer:
                # Port 9100 gives no answer, IPP tells how the job ended
                message = _network_job_result(self.device_specifier, self.model)
                if message:
                    logger.warning('Printing failed: %s', message)
                    return message + batch
                return ""
            logger.info('Printer response: %s', str(info))
            if info.get('did_print') and info.get('ready_for_next_job'):
                logger.info('Label printed successfully and printer is ready for next job')
                return ""
            message = _print_failure(info) + batch
            logger.warning('Printing failed: %s', message)
            return message
        except Exception as e:
            logger.exception("Exception during sending to printer (batch %d): %s", batch_index, e)
            return f"Could not send the label to the printer{batch}: {e}"

    def process_queue(self, batch_size: int = 0) -> str:
        if not self._print_queue:
            logger.warning("Print queue is empty.")
            return "Print queue is empty."

        if batch_size < 1:
            batch_size = int(os.environ.get('PRINT_BATCH_SIZE', DEFAULT_BATCH_SIZE))
        if batch_size < 1:
            batch_size = DEFAULT_BATCH_SIZE

        total = len(self._print_queue)
        entries = list(self._print_queue)
        self._print_queue.clear()

        batch_count = (total + batch_size - 1) // batch_size
        # Hold the device for the whole job so that neither status queries nor
        # other jobs get in between the batches
        lock = contextlib.nullcontext(True) if is_simulation(self.device_specifier) else device_lock(self.device_specifier)
        with lock as acquired:
            if not acquired:
                logger.warning('Printer %s is busy with another job', self.device_specifier)
                return "Printer is busy with another print job, please try again"
            # Split into batches to avoid printer timeouts on large jobs. Each
            # batch is rasterized right before it is sent, so only one batch
            # is kept in memory at a time.
            for batch_index, start in enumerate(range(0, total, batch_size), start=1):
                batch = entries[start:start + batch_size]
                logger.info('Processing batch %d (%d labels, %d/%d)',
                            batch_index, len(batch), start + len(batch), total)
                qlr, generated_images = self._rasterize_entries(batch)
                status = self._send_raster(qlr, generated_images, batch_index, batch_count)
                if status:
                    return status

        return ""


# A network printer that is stopped without telling why, e.g. a QL-810W
# briefly at the end of every job
STOPPED_STATUS_TYPE = 'Stopped'

# How long a job on a network printer may take, and how often its state is
# asked over IPP meanwhile
NETWORK_JOB_TIMEOUT = 120
NETWORK_JOB_POLL = 0.5


def _network_printer_problem(device_specifier: str, model: str) -> str:
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


def _network_job_result(device_specifier: str, model: str) -> str:
    """After a job was sent to a network printer: the error it reports over
    IPP, or an empty string once it is idle again. Printers without IPP
    tell nothing."""
    deadline = time.monotonic() + NETWORK_JOB_TIMEOUT
    while True:
        time.sleep(NETWORK_JOB_POLL)
        try:
            status = query_network_status(device_specifier, model)
        except OSError:
            return 'The printer did not answer after receiving the label, check whether it was printed'
        logger.debug('State of %s after the job: %s %s', device_specifier, status['status_type'], status['errors'])
        if status['errors']:
            return 'The printer reports: ' + ', '.join(status['errors'])
        if status['status_type'] not in (BUSY_STATUS_TYPE, STOPPED_STATUS_TYPE):
            return ''
        if time.monotonic() > deadline:
            return 'The printer did not report that it finished the label, check whether it was printed'


def _print_failure(info: dict) -> str:
    """Why a print job was not confirmed, from the status brother_ql read back
    from the printer while printing."""
    state = info.get('printer_state') or {}
    if state.get('errors'):
        return 'The printer reports: ' + ', '.join(state['errors'])
    if info.get('did_print'):
        return 'The label was printed, but the printer did not report that it is ready for the next one'
    if state:
        return f"The printer did not confirm that the label was printed (last status: {state.get('status_type')}, {state.get('phase_type')})"
    return 'The printer did not answer after receiving the label, check whether it was printed'


def get_printer(printer_identifier=None, backend_identifier=None):
    """
    Instantiate a printer object for communication. Only bidirectional transport backends are supported.

    :param str printer_identifier: Identifier for the printer.
    :param str backend_identifier: Can enforce the use of a specific backend.1
    """

    selected_backend = None
    if backend_identifier:
        selected_backend = backend_identifier
    else:
        try:
            selected_backend = guess_backend(printer_identifier)
        except ValueError:
            logger.info("No backend stated. Selecting the default linux_kernel backend.")
            selected_backend = "linux_kernel"

    be = backend_factory(selected_backend)
    BrotherQLBackend = be["backend_class"]
    printer = BrotherQLBackend(printer_identifier)
    return printer


def get_status(printer, timeout: float = 2.0):
    """Request and parse the printer status.

    brother_ql's own helper reads only twice, 10 ms apart, but printers such
    as the QL-1100 need ~100 ms to answer and return empty reads until then.
    Poll until the full 32-byte response arrives or the timeout expires.
    """
    printer.write(b"\x1b\x69\x53")  # "ESC i S" Status information request
    data = b''
    deadline = time.time() + timeout
    while len(data) < 32 and time.time() < deadline:
        data += printer.read(32 - len(data)) or b''
        if len(data) < 32:
            time.sleep(0.02)
    if len(data) < 32:
        raise TimeoutError(f"No complete status response within {timeout} s")
    return interpret_response(data)


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
    key = _lock_key(device_specifier)
    with device_lock(device_specifier, timeout=STATUS_LOCK_TIMEOUT) as acquired:
        if acquired:
            printer = get_printer(device_specifier)
            try:
                state = get_status(printer)
            finally:
                # Release the device before giving up the lock
                _release(printer)
            _status_cache[key] = state
            return dict(state)
    logger.debug('Printer %s is busy, returning last known status', device_specifier)
    # Model and media from the last query, but the state of a running job:
    # errors or the idle state from before the job no longer apply
    state = dict(_status_cache.get(key, {'model': default_model, 'media_width': None, 'media_length': 0}))
    state.update({'errors': [], 'status_type': BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
    return state


def _failed_printer_state(device_specifier: str, error: Exception, model: str = 'Unknown') -> dict:
    """Status of a printer that did not answer the status query: model and
    media from its last answer, if there was one, and the error."""
    known = _status_cache.get(_lock_key(device_specifier), {})
    return {
        'path': device_specifier,
        'model': known.get('model', model),
        'media_width': known.get('media_width'),
        'media_length': known.get('media_length', 0),
        'media_type': known.get('media_type'),
        'errors': [f'The printer does not answer the status query ({error})'],
        'status_type': 'Error',
        'phase_type': 'Unknown',
    }


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


def _network_printer(device_specifier: str, model: str) -> dict:
    """Status of a network printer that tells nothing about itself."""
    return {
        'errors': [],
        'path': device_specifier,
        'media_category': None,
        'media_length': 0,
        'media_type': None,
        'media_width': None,
        'model': model,
        'model_code': None,
        'phase_type': 'Network Printer',
        'series_code': None,
        'setting': None,
        'status_code': 0,
        'status_type': 'Network Printer',
        'tape_color': '',
        'text_color': '',
        'red_support': model in [m.identifier for m in ALL_MODELS if m.two_color],
    }


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


def _label_printer_model(name: str) -> str | None:
    """The brother_ql model of a name like "Brother QL-810W", None for other
    devices, e.g. office printers."""
    model = (name or '').removeprefix('Brother ').strip()
    return model if model in [m.identifier for m in ALL_MODELS] else None


def query_network_status(device_specifier: str, default_model: str = 'Unknown',
                         label_printers_only: bool = False) -> dict | None:
    """Status of a network printer over IPP: model, loaded labels and why it
    cannot print. Printers without IPP only take jobs on port 9100 and tell
    nothing about themselves. With label_printers_only, None for a device
    whose IPP model is no label printer. Raises OSError if the printer does
    not answer at all."""
    host = urlsplit(device_specifier).hostname
    try:
        attributes = ipp.get_printer_attributes(host, IPP_PORT)
    except (ConnectionRefusedError, ipp.IPPError, http.client.HTTPException) as e:
        logger.debug('No IPP status from %s: %s', device_specifier, e)
        return _network_printer(device_specifier, default_model)
    model = _label_printer_model(str(attributes.get('printer-make-and-model') or ''))
    if model is None:
        if label_printers_only:
            logger.debug('%s is no label printer: %s', device_specifier, attributes.get('printer-make-and-model'))
            return None
        model = default_model
    status = _network_printer(device_specifier, model)
    status['media_width'], status['media_length'], status['media_type'] = _ipp_media(attributes.get('media-col-ready'))
    status['errors'] = _ipp_errors(attributes)
    state = attributes.get('printer-state')
    busy = state == ipp.PROCESSING
    if status['errors']:
        status['status_type'] = 'Error occurred'
    elif busy:
        status['status_type'] = BUSY_STATUS_TYPE
    else:
        status['status_type'] = STOPPED_STATUS_TYPE if state == ipp.STOPPED else 'Reply to status request'
    status['phase_type'] = 'Printing state' if busy else 'Waiting to receive'
    # Kept for when the printer does not answer
    _status_cache[_lock_key(device_specifier)] = status
    return dict(status)


def _discovered_label_printers() -> list[tuple[str, str]]:
    """Label printers that announce themselves on the network, as
    (tcp://<address>[:<port>], model). Kept for DISCOVERY_INTERVAL."""
    global _discovered
    if not NETWORK_DISCOVERY:
        return []
    if _discovered is not None and time.monotonic() - _discovered[0] < DISCOVERY_INTERVAL:
        return _discovered[1]
    printers = []
    for service in mdns.discover():
        txt = service['txt']
        model = _label_printer_model(txt.get('usb_mdl') or '') or _label_printer_model(txt.get('ty') or '')
        if model is None:
            continue
        # IPP announces its own port, jobs go to the raw port
        port = service['port'] if service['service'].startswith('_pdl-datastream.') else None
        spec = f"tcp://{service['address']}" + (f':{port}' if port and port != 9100 else '')
        if all(urlsplit(known).hostname != service['address'] for known, _ in printers):
            printers.append((spec, model))
    _discovered = (time.monotonic(), printers)
    return printers


_last_scan_ts = 0
_cached_printers = []


def get_ptr_status(config: Config):
    # Simple in-memory cache for detected printers
    global _last_scan_ts, _cached_printers

    device_specifier = config['PRINTER_PRINTER']
    default_model = config['PRINTER_MODEL']

    SIMULATOR_PRINTER = {
        'errors': [],
        'path': 'simulation',
        'media_category': None,
        'media_length': 0,
        'media_type': None,
        'media_width': None,
        'model': default_model,
        'model_code': None,
        'phase_type': 'Simulator',
        'series_code': None,
        'setting': None,
        'status_code': 0,
        'status_type': 'Simulator',
        'tape_color': '',
        'text_color': '',
        'red_support': default_model in [m.identifier for m in ALL_MODELS if m.two_color]
    }

    status = {
        "errors": [],
        "path": device_specifier,
        "media_category": None,
        "media_length": 0,
        "media_type": None,
        "media_width": None,
        "model": "Unknown",
        "model_code": None,
        "phase_type": "Unknown",
        "series_code": None,
        "setting": None,
        "status_code": 0,
        "status_type": "Unknown",
        "tape_color": "",
        "text_color": "",
        "red_support": False
    }
    try:
        if config.get('PRINTERS'):
            # The configured printers, no detection. Each keeps its configured
            # model, a failed query must not move it to another printer. The
            # simulator is listed only when configured, e.g. QL-700=simulation.
            printers = []
            for model, device in config['PRINTERS']:
                try:
                    if device == 'simulation':
                        state = SIMULATOR_PRINTER.copy()
                    elif device.startswith('tcp://'):
                        state = query_network_status(device, model)
                    else:
                        state = query_status(device, model)
                except Exception as e:
                    logger.info('Status query of %s failed: %s', device, e)
                    state = _failed_printer_state(device, e)
                state.update({'path': device, 'model': model})
                state['red_support'] = model in [m.identifier for m in ALL_MODELS if m.two_color]
                printers.append(state)
            return {
                **status,
                **printers[0],
                'printers': printers,
                'selected': printers[0]['path'],
            }
        # If device_specifier is the default '?', try to auto-detect multiple printers
        if device_specifier == '?':
            now = time.time()
            # Refresh cache every 10 seconds
            if now - _last_scan_ts > 10:
                logger.debug('Auto-detecting printers: scanning local USB and network')
                found_list = []
                for dev in USB_PRINTER_DEVICES:
                    if not os.path.exists(dev):
                        continue
                    spec = f"file://{dev}"
                    try:
                        # The configured model need not be the model of this
                        # printer. If it is busy before it ever answered, its
                        # model is unknown.
                        printer_state = query_status(spec, 'Unknown')
                        printer_state.setdefault('path', spec)
                        found_list.append(printer_state)
                        logger.debug('Found compatible printer at %s -> %s', spec, printer_state.get('model'))
                    except Exception as e:
                        # Keep the printer in the list with the error, otherwise
                        # the label designer switches to another printer (often
                        # the simulator) without telling why
                        logger.info('Status query of %s failed: %s', spec, e)
                        logger.debug('Status query of %s failed', spec, exc_info=True)
                        found_list.append(_failed_printer_state(spec, e))

                # Network printers that announce themselves, and ones in the
                # ARP table with a known MAC address prefix, e.g. where
                # multicast does not reach. The latter are dropped if their
                # IPP model is no label printer.
                candidates = [(spec, model, False) for spec, model in _discovered_label_printers()]
                try:
                    with open(ARP_TABLE, 'r') as arp_file:
                        for line in arp_file.readlines()[1:]:  # skip header line
                            parts = line.split()
                            if len(parts) < 4:
                                continue
                            ip, _, _, mac, _, _ = parts
                            known = any(urlsplit(spec).hostname == ip for spec, _, _ in candidates)
                            if not known and any(mac.startswith(prefix) for prefix in BROTHER_MAC_ADDRESS_PREFIXES):
                                candidates.append((f"tcp://{ip}", default_model, True))
                except Exception:
                    logger.debug('Failed to read ARP table for network printer detection', exc_info=True)
                for spec, model, unconfirmed in candidates:
                    try:
                        printer = query_network_status(spec, model, label_printers_only=unconfirmed)
                    except OSError as e:
                        logger.info('Status query of %s failed: %s', spec, e)
                        printer = _failed_printer_state(spec, e, model)
                    if printer is not None:
                        found_list.append(printer)
                        logger.debug('Found network printer at %s -> %s', spec, printer.get('model'))

                _cached_printers = found_list
                # A printer busy with a job is scanned again with the next
                # query instead of being shown as printing for 10 s
                busy = any(p.get('status_type') == BUSY_STATUS_TYPE for p in found_list)
                _last_scan_ts = 0 if busy else now
            # Prepare response: include list of printers and a top-level status for the first one (compatibility)
            # Ensure simulator printer is always present
            sim = SIMULATOR_PRINTER.copy()
            printers = list(_cached_printers)
            # append simulator if not present
            if not any(p.get('path') == 'simulation' for p in printers):
                printers.append(sim)
            if printers:
                # Use first detected printer as default top-level status for backward compatibility
                first = printers[0]
                for key, value in first.items():
                    status[key] = value
                return {
                    'printers': printers,
                    'selected': status.get('path'),
                    **status
                }
            else:
                status['status_type'] = 'Offline'
                status['errors'].append('No compatible printer detected')
                return {
                    'printers': [sim],
                    'selected': None,
                    **status
                }
        elif device_specifier.startswith('tcp://'):
            try:
                printer = query_network_status(device_specifier, default_model)
            except OSError as e:
                logger.info('Status query of %s failed: %s', device_specifier, e)
                printer = _failed_printer_state(device_specifier, e)
            status.update(printer)
            status['printers'] = [printer, SIMULATOR_PRINTER.copy()]
            status['selected'] = device_specifier
            return status
        else:
            printer_state = query_status(device_specifier, default_model)
            for key, value in printer_state.items():
                status[key] = value
        # Always include simulator in returned printers list
        sim = SIMULATOR_PRINTER.copy()
        status['red_support'] = status['model'] in [model.identifier for model in ALL_MODELS if model.two_color]
        return {
            'printers': [sim],
            'selected': status.get('path'),
            **status
        }
    except Exception as e:
        logger.exception("Printer status error: %s", e)
        status['errors'] = [str(e)]
        return status
