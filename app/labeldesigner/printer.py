import logging
import os
import stat
import time
import hashlib
import datetime
import tempfile
import threading
import contextlib
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

# Experimentally identified MAC address prefixes for Brother network printers
# (may not be exhaustive)
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
            logger.info("Sending %d bytes to printer at %s (batch %d)",
                        len(qlr.data), self.device_specifier, batch_index)
            info = send(qlr.data, self.device_specifier)
            logger.info('Sent %d bytes to printer %s', len(qlr.data), self.device_specifier)
            if network_printer:
                logger.info('Network printer does not provide status information.')
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


def _failed_printer_state(device_specifier: str, error: Exception) -> dict:
    """Status of a printer that did not answer the status query: model and
    media from its last answer, if there was one, and the error."""
    known = _status_cache.get(_lock_key(device_specifier), {})
    return {
        'path': device_specifier,
        'model': known.get('model', 'Unknown'),
        'media_width': known.get('media_width'),
        'media_length': known.get('media_length', 0),
        'media_type': known.get('media_type'),
        'errors': [f'The printer does not answer the status query ({error})'],
        'status_type': 'Error',
        'phase_type': 'Unknown',
    }


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
        # If device_specifier is the default '?', try to auto-detect multiple printers
        if device_specifier == '?':
            now = time.time()
            # Refresh cache every 10 seconds
            if now - _last_scan_ts > 10:
                logger.debug('Auto-detecting printers: scanning local USB and network')
                found_list = []
                for i in range(0, 11):
                    dev = f"/dev/usb/lp{i}"
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

                # scan ARP table for network printers with known Brother MAC
                # address prefixes
                try:
                    with open('/proc/net/arp', 'r') as arp_file:
                        for line in arp_file.readlines()[1:]:  # skip header line
                            parts = line.split()
                            if len(parts) < 4:
                                continue
                            ip, _, _, mac, _, _ = parts
                            if any(mac.startswith(prefix) for prefix in BROTHER_MAC_ADDRESS_PREFIXES):
                                device_specifier = f"tcp://{ip}"
                                printer = SIMULATOR_PRINTER.copy()
                                printer['path'] = device_specifier
                                printer['phase_type'] = 'Network Printer'
                                printer['status_type'] = 'Network Printer'
                                found_list.append(printer)
                                logger.debug('Found network printer candidate at %s -> %s', device_specifier, printer.get('model'))
                except Exception:
                    logger.debug('Failed to read ARP table for network printer detection', exc_info=True)

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
            # TCP printers are not supported for status queries
            status['status_type'] = 'Unknown'
            printer = SIMULATOR_PRINTER.copy()
            printer['path'] = device_specifier
            printer['phase_type'] = 'Network Printer'
            printer['status_type'] = 'Network Printer'
            sim = SIMULATOR_PRINTER.copy()
            status['printers'] = [printer, sim]
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
