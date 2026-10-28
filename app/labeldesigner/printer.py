import logging
import os
import re
import time
import datetime
import tempfile
import threading
import contextlib
from brother_ql.backends.helpers import send
from brother_ql import BrotherQLRaster, create_label
from brother_ql.backends.helpers import get_status
from brother_ql.backends import backend_factory, guess_backend
from flask import Config
from .label import LabelOrientation, LabelType, LabelContent
from brother_ql.models import ALL_MODELS

try:
    import fcntl
except ImportError:  # Not available on Windows
    fcntl = None

SIMULATED_LABELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'simulated_labels')

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

# Directory for the lock files serializing access to the printer devices
LOCK_DIR = tempfile.gettempdir()

_fallback_locks = {}
_fallback_locks_guard = threading.Lock()
_status_cache = {}


@contextlib.contextmanager
def device_lock(device_specifier: str, blocking: bool = True):
    """Serialize access to a printer device.

    The kernel allows only one open handle per USB printer, so a status query
    during a print job (or the other way round) fails with "Device or resource
    busy". A lock file is used so that this also works across several worker
    processes. Yields whether the lock was acquired."""
    fd = None
    if fcntl is not None:
        # The device specifier comes from the request, make sure the lock file
        # stays inside LOCK_DIR
        name = re.sub(r'[^A-Za-z0-9_.-]', '_', device_specifier)
        lock_dir = os.path.normpath(LOCK_DIR)
        path = os.path.normpath(os.path.join(lock_dir, f'brother_ql_web_{name}.lock'))
        if not path.startswith(lock_dir + os.sep):
            raise ValueError(f'Invalid printer: {device_specifier}')
        try:
            fd = os.open(path, os.O_RDONLY | os.O_CREAT, 0o666)
        except OSError as e:
            logger.warning('Cannot open lock file for %s, locking within this process only: %s', device_specifier, e)

    if fd is None:
        with _fallback_locks_guard:
            lock = _fallback_locks.setdefault(device_specifier, threading.Lock())
        acquired = lock.acquire(blocking)
        try:
            yield acquired
        finally:
            if acquired:
                lock.release()
        return

    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX if blocking else fcntl.LOCK_EX | fcntl.LOCK_NB)
            acquired = True
        except BlockingIOError:
            acquired = False
        yield acquired
    finally:
        # Closing the file releases the lock
        os.close(fd)


def is_simulation(device_specifier) -> bool:
    return isinstance(device_specifier, str) and device_specifier in ['simulation', '?']


class PrinterQueue:
    def __init__(self, model, device_specifier, label_size):
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
            img = label.generate(rotate=False)
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

    def _send_raster(self, qlr, generated_images, batch_index=0) -> str:
        """Send rasterized data to the printer or simulator.
        Returns an empty string on success, or an error message."""
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
            logger.warning("Failed to print label (batch %d)", batch_index)
            return f"Failed to print label (batch {batch_index})"
        except Exception as e:
            logger.exception("Exception during sending to printer (batch %d): %s", batch_index, e)
            return f"Exception during sending to printer (batch {batch_index}): {e}"

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

        # Hold the device for the whole job so that neither status queries nor
        # other jobs get in between the batches
        lock = contextlib.nullcontext() if is_simulation(self.device_specifier) else device_lock(self.device_specifier)
        with lock:
            # Split into batches to avoid printer timeouts on large jobs
            for batch_index, start in enumerate(range(0, total, batch_size), start=1):
                batch = entries[start:start + batch_size]
                logger.info('Processing batch %d (%d labels, %d/%d)',
                            batch_index, len(batch), start + len(batch), total)
                qlr, generated_images = self._rasterize_entries(batch)
                status = self._send_raster(qlr, generated_images, batch_index)
                if status:
                    return status

        return ""


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


def query_status(device_specifier: str) -> dict:
    """Query the printer status without waiting for a running print job. While
    a job holds the device, the last known status is returned instead."""
    with device_lock(device_specifier, blocking=False) as acquired:
        if acquired:
            printer = get_printer(device_specifier)
            state = get_status(printer)
            # Release the device before giving up the lock
            del printer
            _status_cache[device_specifier] = state
            return dict(state)
    logger.debug('Printer %s is busy, returning last known status', device_specifier)
    return dict(_status_cache.get(device_specifier, {
        'errors': [],
        'model': 'Unknown',
        'status_type': 'Busy',
        'phase_type': 'Printing state',
    }))


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
                        printer_state = query_status(spec)
                        printer_state.setdefault('path', spec)
                        found_list.append(printer_state)
                        logger.debug('Found compatible printer at %s -> %s', spec, printer_state.get('model'))
                    except Exception:
                        logger.debug('Device %s exists but is not a compatible printer or failed to query', dev, exc_info=True)

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
                _last_scan_ts = now
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
            printer_state = query_status(device_specifier)
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
