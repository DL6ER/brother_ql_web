import errno
import io
import logging
import os
import stat
import time
import hashlib
import datetime
import tempfile
import threading
import contextlib
import concurrent.futures
import http.client
from urllib.parse import urlsplit
from brother_ql.backends.helpers import send
from brother_ql import BrotherQLRaster, create_label
from brother_ql.reader import interpret_response
from brother_ql.backends import backend_factory, guess_backend
from flask import Config
from PIL import Image, ImageEnhance
from .label import LabelOrientation, LabelType, LabelContent
from .label_names import VC500W, VC500W_LABELS, VC500W_TAPES, check_label_size
from brother_ql.models import ALL_MODELS
from brother_ql.labels import ALL_LABELS
from brother_ql.devicedependent import label_type_specs
from werkzeug.security import safe_join
from . import ipp, mdns, snmp, vc500w

try:
    import fcntl
except ImportError:  # Not available on Windows
    fcntl = None

SIMULATED_LABELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'simulated_labels')

# Number of simulated labels kept in SIMULATED_LABELS_DIR, older ones are
# deleted so that printing to the simulator cannot fill up the disk
MAX_SIMULATED_LABELS = 100

# How long a USB printer may stay silent after a job was sent and between
# its status messages: as long as a page of the job takes to print at the
# slowest printers, about 10 mm/s, at least STATUS_WAIT seconds.
STATUS_WAIT = 10
SLOWEST_PRINT_SPEED = 10
# A job is written in pieces: pyusb gives up on a single write after 15 s,
# a long label that the printer prints while it receives it takes longer
WRITE_CHUNK = 16384
STATUS_HEADER = b'\x80\x20\x42'

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
#   off-center on a QL-1100 (3.5 mm left, 6.5 mm right), -6 centers it. The
#   104 mm endless entry of brother_ql already uses a similar -8.
LABEL_OFFSET_CORRECTIONS = {
    '103x164': -6,
}

# The QL-1100 series prints at 300 dpi only. Brother's raster command
# reference of these models lists no 600 dpi resolution, unlike that of the
# QL-800, and a 600 dpi label came out twice as long on a QL-1100 (#103):
# https://download.brother.com/welcome/docp100366/cv_ql1100_eng_raster_100.pdf
# The P-touch printers print at 180 dpi. For 600 dpi brother_ql sets bit 5
# of ESC i K, which their reference marks as not used:
# https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
MODELS_WITHOUT_600_DPI = ('QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W')


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    if high_res and model in MODELS_WITHOUT_600_DPI:
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'
    return ''


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
    elif key.startswith(('tcp://', 'usb://', 'vc500w://')):
        key = key.lower()
        if key.startswith(('tcp://', 'vc500w://')) and ':' not in key.split('://', 1)[1]:
            key += ':9100'
    return key


# The IEEE 1284 device ID of a printer on USB, which usblp read when it was
# plugged in
USB_DEVICE_ID = '/sys/class/usbmisc/{name}/device/ieee1284_id'


def _usb_device_id(device_specifier: str) -> str:
    path = os.path.normpath(device_specifier[len('file://'):] if device_specifier.startswith('file://') else device_specifier)
    if not path.startswith('/dev/usb/lp'):
        return ''
    try:
        with open(USB_DEVICE_ID.format(name=os.path.basename(path))) as file:
            return file.read()
    except OSError:
        return ''


def is_vc500w(device_specifier, model: str = '') -> bool:
    """A Brother VC-500W: addressed as vc500w://<host>[:<port>], configured
    as one, or on USB with its device ID. It must never get the commands of
    a QL printer."""
    if not isinstance(device_specifier, str) or device_specifier == 'simulation':
        return False
    return (device_specifier.startswith('vc500w://') or model == VC500W
            or vc500w.device_id_is_vc500w(_usb_device_id(device_specifier)))


def _vc500w_target(device_specifier: str) -> tuple[str, int]:
    """Host and port of a VC-500W on the network, or its path on USB."""
    if device_specifier.startswith('vc500w://'):
        url = urlsplit(device_specifier)
        return url.hostname, url.port or vc500w.PORT
    if device_specifier.startswith('file://'):
        return device_specifier[len('file://'):], vc500w.PORT
    return device_specifier, vc500w.PORT


# The stage of the label a VC-500W prints, by printer, while it holds the
# device: a status query cannot ask it then
_vc500w_jobs = {}


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


def flock_within(fd: int, timeout: float, shared: bool = False) -> bool:
    """Lock the file fd for this process, waiting at most timeout seconds,
    shared with other shared locks if shared. Whether it was locked. Raises
    OSError where the file system cannot lock it."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
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
            acquired = flock_within(fd, timeout)
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


class _Raster(BrotherQLRaster):
    """brother_ql gives P-touch tapes the media type 0x00, which PT printers
    read as "no tape": a PT-E550W half cut the tape and hung in the printing
    state. Without it, the printer checks the width of the tape only."""

    # Labels after which a PT printer cuts the tape, between them it only
    # half cuts. brother_ql sends no "cut each * labels" (ESC i A) to PT
    # printers, which then cut after every label.
    cut_every = 1
    # Whether a PT printer cuts the tape at all (ESC i M). brother_ql only
    # sends it to switch cutting on.
    autocut = True

    def add_media_and_quality(self, rnumber):
        if self.model.startswith('PT-') and getattr(self, '_mtype', None) == b'\x00':
            self._mtype = None
        super().add_media_and_quality(rnumber)

    def add_autocut(self, autocut=False):
        super().add_autocut(self.autocut if self.model.startswith('PT-') else autocut)

    def add_cut_every(self, n=1):
        if self.model.startswith('PT-'):
            if self.autocut:
                self.data += b'\x1biA' + bytes([self.cut_every])
        else:
            super().add_cut_every(n)

    def add_print(self, last_page=True):
        super().add_print(last_page)
        # The print information of the following labels says that they are
        # not the first page, brother_ql leaves it at 0
        self.page_number += 1


class PrinterQueue:
    def __init__(self, model, device_specifier, label_size):
        if is_vc500w(device_specifier):
            model = VC500W
        # A clear message instead of "Wrong pixel width" from brother_ql
        check_label_size(label_size, model)
        self.model = model
        self.device_specifier = device_specifier
        self.label_size = label_size
        # Cut after each label, the last one or none (see routes._cutting),
        # on PT printers and the VC-500W half cut between labels not cut, on
        # PT printers chain printing
        self.cut = 'each'
        self.half_cut = True
        self.chain = False
        self._print_queue = []

    def add_label_to_queue(self, label, high_res: bool = False, fast: bool = False):
        problem = resolution_problem(self.model, high_res)
        if problem:
            raise ValueError(problem)
        self._print_queue.append({
            'label': label,
            'high_res': high_res,
            'fast': fast
        })

    def label_cuts(self) -> list[bool]:
        """Whether the printer cuts after each label of the queue, as
        self.cut says: after each one, the last one or none."""
        last = len(self._print_queue) - 1
        return [self.cut == 'each' or (self.cut == 'last' and i == last) for i in range(last + 1)]

    def _rasterize_entries(self, entries):
        """Rasterize the queue entries into one BrotherQLRaster. Each label
        is converted as soon as its job is complete, so that the images of
        a long job are not all kept in memory; the simulator saves them as
        PNG instead."""
        qlr = _Raster(self.model)
        ptouch = self.model.startswith('PT-')
        # The length of each page in mm, at most: how long the printer may
        # take to print it
        qlr.page_lengths = []
        simulation = is_simulation(self.device_specifier)
        if simulation:
            os.makedirs(SIMULATED_LABELS_DIR, exist_ok=True)
            ts = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S_%f')
        job = None
        for i, entry in enumerate(entries):
            label = entry['label']
            if label.label_type == LabelType.ENDLESS_LABEL:
                rotate = 0 if label.label_orientation == LabelOrientation.STANDARD else 90
            else:
                rotate = 'auto'
            dither = label.label_content != LabelContent.IMAGE_BW
            # The labels of a PT printer are one job. As jobs of their own, a
            # PT-E550W fed, half cut and cut the tape for each, also when it
            # was to be cut after the last one only.
            settings = (rotate, dither, entry['high_res'], entry['fast'])
            if job and not (ptouch and job['settings'] == settings):
                self._add_job(qlr, job)
                job = None
            img = label.generate()
            if simulation:
                path = os.path.join(SIMULATED_LABELS_DIR, f'{ts}_{i:03d}.png')
                img.save(path, format='PNG')
                logger.info('Saved simulated label to %s', path)
            qlr.page_lengths.append(max(img.size) / (180 if ptouch else 300) * 25.4)
            if job:
                job['images'].append(img)
                job['cuts'].append(entry['cut'])
            else:
                job = {'settings': settings, 'images': [img], 'cuts': [entry['cut']]}
        if job:
            self._add_job(qlr, job)
        return qlr

    def _add_job(self, qlr, job):
        """Convert the images of a job, labels printed with the same settings."""
        ptouch = self.model.startswith('PT-')
        rotate, dither, high_res, fast = job['settings']
        if ptouch:
            # Cut after every label, or with cut_once after the last one, the
            # printer half cuts between them. ESC i A counts up to 255 labels.
            qlr.cut_every = 1 if all(job['cuts']) else min(len(job['images']), 255)
            qlr.autocut = self.cut != 'none'
            qlr.half_cut = self.half_cut
            # Chain printing: the last label stays in the printer, the
            # next job cuts it off and saves the tape fed before it
            qlr.no_chain_printing = not self.chain
        # Unless printing fast, the printer gives priority to print
        # quality (ESC i z). A QL-810W printed a 29 x 90 mm label in
        # 3.0 s instead of 1.8 s with it, a little sharper.
        # PT printers get compressed raster lines, as from Brother's
        # apps: a PT-E550W read uncompressed ones wrongly over Wi-Fi,
        # half cut the tape and hung in the printing state.
        qlr.page_number = 0
        create_label(
            qlr,
            job['images'],
            self.label_size,
            red='red' in str(self.label_size),
            dither=dither,
            cut=ptouch or any(job['cuts']),
            dpi_600=high_res,
            hq=not fast,
            rotate=rotate,
            compress=ptouch
        )

    def _send_raster(self, qlr) -> str:
        """Send rasterized data to the printer or simulator.
        Returns an empty string on success, or an error message."""
        try:
            # Simulator: the labels were saved as PNG, pretend we sent them
            if is_simulation(self.device_specifier):
                logger.info('Simulated sending %d bytes to simulator printer', len(qlr.data))
                _prune_simulated_labels()
                return ""

            network_printer = isinstance(self.device_specifier, str) and self.device_specifier.startswith('tcp://')
            if network_printer:
                # Not sent to a printer that cannot take it, e.g. with an
                # open cover, the job would be lost
                problem = _network_printer_problem(self.device_specifier, self.model)
                if problem:
                    logger.warning('Not printing on %s: %s', self.device_specifier, problem)
                    return problem
            logger.info("Sending %d bytes to printer at %s", len(qlr.data), self.device_specifier)
            if network_printer:
                send(qlr.data, self.device_specifier)
                logger.info('Sent %d bytes to printer %s', len(qlr.data), self.device_specifier)
                # Port 9100 gives no answer, IPP tells how the job ended
                message = _network_job_result(self.device_specifier, self.model, qlr.page_lengths)
                if message:
                    logger.warning('Printing failed: %s', message)
                    return message
                return ""
            info = send_job(qlr.data, self.device_specifier, qlr.page_lengths)
            logger.info('Sent %d bytes to printer %s', len(qlr.data), self.device_specifier)
            logger.info('Printer response: %s', str(info))
            if info.get('did_print') and info.get('ready_for_next_job'):
                logger.info('Label printed successfully and printer is ready for next job')
                return ""
            message = _print_failure(info)
            logger.warning('Printing failed: %s', message)
            return message
        except Exception as e:
            logger.exception("Exception during sending to printer: %s", e)
            reason = _unanswered_reason(e)
            if reason:
                return f"Could not send the label to the printer: {reason}"
            return "Could not send the label to the printer, the log of the server names the reason"

    def process_queue(self) -> str:
        """Print the labels of the queue as one job."""
        if not self._print_queue:
            logger.warning("Print queue is empty.")
            return "Print queue is empty."

        entries = [dict(entry, cut=cut) for entry, cut in zip(self._print_queue, self.label_cuts())]
        self._print_queue.clear()

        # Hold the device for the whole job so that neither status queries nor
        # other jobs get in between
        lock = contextlib.nullcontext(True) if is_simulation(self.device_specifier) else device_lock(self.device_specifier)
        with lock as acquired:
            if not acquired:
                logger.warning('Printer %s is busy with another job', self.device_specifier)
                return "Printer is busy with another print job, please try again"
            logger.info('Printing %d labels', len(entries))
            if self.model == VC500W:
                return self._print_vc500w(entries)
            return self._send_raster(self._rasterize_entries(entries))

    def _print_vc500w(self, entries) -> str:
        """Print on a VC-500W: one image per label. Labels not cut get a half
        cut, unless half cuts are off, and stay together on a strip."""
        modes = ['fast' if entry['fast'] else 'quality' for entry in entries]
        images = [_vc500w_image(entry['label'], self.label_size, mode) for entry, mode in zip(entries, modes)]
        if is_simulation(self.device_specifier):
            os.makedirs(SIMULATED_LABELS_DIR, exist_ok=True)
            ts = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S_%f')
            for i, image in enumerate(images):
                image.save(os.path.join(SIMULATED_LABELS_DIR, f'{ts}_vc500w_{i}.png'))
            _prune_simulated_labels()
            return ''
        target, port = _vc500w_target(self.device_specifier)
        key = _lock_key(self.device_specifier)
        try:
            for i, (entry, mode, image) in enumerate(zip(entries, modes, images)):
                cut = 'full' if entry['cut'] else 'half' if self.half_cut else 'none'

                def progress(stage, line, i=i, lines=image.height):
                    job = {'stage': stage, 'label': i + 1, 'labels': len(entries)}
                    if stage == vc500w.stage_name('PRINTING'):
                        job['percent'] = min(99, round(100 * line / lines))
                    _vc500w_jobs[key] = job
                progress(vc500w.stage_name('PROCESSING'), 0)
                label = f' (label {i + 1} of {len(entries)})' if len(entries) > 1 else ''
                try:
                    error = vc500w.print_image(target, image, mode, cut, port, progress)
                except vc500w.VC500WError as e:
                    # Its own words name paths of the printer, e.g. "please check /dev/tmp/lutgen.out"
                    logger.warning('Printing on %s failed: %s', self.device_specifier, e)
                    return f'The printer refused the label{label}, the log of the server names the reason'
                except OSError as e:
                    logger.warning('Printing on %s failed: %s', self.device_specifier, e)
                    reason = _unanswered_reason(e)
                    return (f'Could not send the label to the printer{label}: {reason}' if reason
                            else f'Could not send the label to the printer{label}, the log of the server names the reason')
                if error:
                    return f'{_vc500w_error(error)}{label}'
        finally:
            _vc500w_jobs.pop(key, None)
        return ''


# A network printer that is stopped without telling why, e.g. a QL-810W
# briefly at the end of every job
STOPPED_STATUS_TYPE = 'Stopped'

# How long a job on a network printer may take besides printing its labels at
# the slowest printers, and how often its state is asked over IPP meanwhile
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


def _network_job_result(device_specifier: str, model: str, page_lengths: list) -> str:
    """After a job was sent to a network printer: the error it reports over
    IPP, or an empty string once it is idle again. Printers without IPP
    tell nothing. page_lengths: the length of each page in mm."""
    deadline = time.monotonic() + NETWORK_JOB_TIMEOUT + sum(page_lengths) / SLOWEST_PRINT_SPEED
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


def send_job(data: bytes, device_specifier: str, page_lengths: list) -> dict:
    """Send a print job to a USB printer and read its status messages until
    it reports every page printed and that it is ready for the next job, an
    error, or until it stays silent for longer than a page takes to print.

    brother_ql's send() stopped reading 10 s after it began to write: a long
    label, which the printer prints while it receives it, was not confirmed.
    It also stopped at the first
    page, the messages of the other pages were left for the next job.

    page_lengths: the length of each page in mm. Returns, as _print_failure()
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
        start = received.find(STATUS_HEADER)
        if start < 0:
            # The end may be the beginning of a header
            return states, received[-(len(STATUS_HEADER) - 1):]
        received = received[start:]
        if len(received) < 32:
            return states, received
        try:
            states.append(interpret_response(received[:32]))
        except Exception:
            logger.warning('Could not read a status message of the printer: %s', received[:32].hex())
        received = received[32:]


# ZINK paper prints colors duller than a screen shows them. Of 1, 1.5 and 2
# times the saturation, 2 printed red best, darker colors gain the most.
VC500W_SATURATION = 2.0


# The printer places an image this many dots off the middle of the tape,
# toward its first pixel, and its cuts take off this many millimeters of
# both ends (docs/vc500w.md)
VC500W_OFFSET = 3
VC500W_CUT_MM = 0.8


def _repeat_rows(img: Image.Image, before: int, after: int) -> Image.Image:
    """img with its first row repeated before times above it and its last
    row after times below it."""
    out = Image.new(img.mode, (img.width, img.height + before + after))
    if before:
        out.paste(img.crop((0, 0, img.width, 1)).resize((img.width, before), Image.Resampling.NEAREST), (0, 0))
    if after:
        last = img.crop((0, img.height - 1, img.width, img.height))
        out.paste(last.resize((img.width, after), Image.Resampling.NEAREST), (0, before + img.height))
    out.paste(img, (0, before))
    return out


def _vc500w_image(label, label_size: str, mode: str) -> Image.Image:
    """A label as the image the VC-500W prints: each row is a line along the
    tape at the lines per inch of the mode, the first row comes out first,
    so standard labels come out top first, rotated ones with the end of
    their text, as on QL printers. The label is as wide as the tape, its
    edges are repeated beyond those of the tape and beyond both cuts, so a
    color at an edge reaches it. Colors are saturated for the paper."""
    img = label.generate().convert('RGB')
    if label.label_orientation == LabelOrientation.STANDARD:
        img = img.rotate(-90, expand=True)
    # Now along the tape: its right end comes out first, its top becomes
    # the first pixels of the rows
    tape = next(tape for tape in VC500W_LABELS if tape.identifier == label_size)
    lpi = vc500w.MODES[mode][2]
    img = img.resize((round(img.width * lpi / vc500w.DPI), img.height), Image.Resampling.LANCZOS)
    extra = max(tape.dots_total[0] - img.height, 0)
    top = min(extra // 2 + VC500W_OFFSET, extra)
    img = _repeat_rows(img, top, extra - top).transpose(Image.Transpose.TRANSPOSE)
    ends = round(VC500W_CUT_MM / 25.4 * lpi)
    img = _repeat_rows(img, ends, ends).transpose(Image.Transpose.TRANSPOSE)
    img = ImageEnhance.Color(img).enhance(VC500W_SATURATION)
    return img.transpose(Image.Transpose.ROTATE_90)


def _vc500w_error(error: str) -> str:
    """A message for an error of a VC-500W: its own or the one it reports."""
    if error == vc500w.EJECT_JAM:
        # A label lies in the output, also the tape a new cassette feeds
        return vc500w.TAKE_OUT
    if error in (vc500w.TAKE_OUT, vc500w.DAMAGED, vc500w.NO_ANSWER, vc500w.UNFINISHED, vc500w.NO_CASSETTE, vc500w.BUSY,
                 vc500w.BLOCKED, vc500w.TOO_LONG):
        return error
    return f'The printer reports: {error.replace("_", " ").capitalize()}'


def query_vc500w_status(device_specifier: str) -> dict:
    """Status of a VC-500W: the tape in it, how much is left and errors.
    While it prints, the stage of the label instead. Raises OSError or
    VC500WError if it does not answer."""
    key = _lock_key(device_specifier)
    # A job of this server holds the printer, it tells the stage right away
    with device_lock(device_specifier, timeout=0 if key in _vc500w_jobs else STATUS_LOCK_TIMEOUT) as acquired:
        if acquired:
            config, status = vc500w.get_state(*_vc500w_target(device_specifier))
            state = _network_printer(device_specifier, config['model'] or VC500W)
            width = config['tape_width_mm']
            if width:
                # The nominal width of the tape, e.g. 25 for 24.8 mm
                state['media_width'] = min(VC500W_TAPES, key=lambda tape: abs(tape - width))
                state['media_type'] = 'Continuous length tape'
            state['tape_length_mm'] = config['tape_length_mm']
            state['tape_remaining_mm'] = status['tape_remaining_mm']
            error = vc500w.ready_error(status)
            state['errors'] = [_vc500w_error(error)] if error else []
            busy = status['state'] not in ('', vc500w.IDLE)
            state['status_type'] = 'Error occurred' if error else BUSY_STATUS_TYPE if busy else 'Reply to status request'
            state['phase_type'] = 'Printing state' if busy else 'Waiting to receive'
            if busy and not error:
                state['job'] = {'stage': vc500w.stage_name(status['stage'])}
            _status_cache[key] = state
            return dict(state)
    state = dict(_status_cache.get(key, _network_printer(device_specifier, VC500W)))
    state.update({'errors': [], 'status_type': BUSY_STATUS_TYPE, 'phase_type': 'Printing state'})
    if key in _vc500w_jobs:
        state['job'] = dict(_vc500w_jobs[key])
    return state


def _print_failure(info: dict) -> str:
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


def _read_status(printer, timeout: float) -> bytes:
    """Ask for the status, the 32-byte answer or what came until the timeout.

    brother_ql's own helper reads only twice, 10 ms apart, but printers such
    as the QL-1100 need ~100 ms to answer and return empty reads until then.
    """
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
    if is_vc500w(device_specifier, default_model):
        return query_vc500w_status(device_specifier)
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


# The configured printers are asked at the same time, the answer waits at most
# STATUS_QUERY_WAIT seconds for them. The .local name of a PT-E550W that was off
# took 10 s not to resolve, the label designer showed nothing meanwhile. A
# query still running is not started again, its printer does not answer.
STATUS_QUERY_WAIT = 4.0
_status_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='printer-status')
_status_queries = {}
_status_queries_lock = threading.Lock()


def _query_configured(model: str, device: str) -> dict:
    if device.startswith('tcp://'):
        return query_network_status(device, model)
    return query_status(device, model)


def _configured_states(printers) -> list[dict]:
    """The status of each configured (model, device), in their order."""
    with _status_queries_lock:
        futures = []
        for model, device in printers:
            if device == 'simulation':
                futures.append(None)
                continue
            future = _status_queries.get(device)
            if future is None or future.done():
                future = _status_pool.submit(_query_configured, model, device)
                _status_queries[device] = future
            futures.append(future)
    concurrent.futures.wait([f for f in futures if f], timeout=STATUS_QUERY_WAIT)
    states = []
    for (model, device), future in zip(printers, futures):
        if future is None:
            states.append(None)
            continue
        try:
            if not future.done():
                raise TimeoutError(f'no answer within {STATUS_QUERY_WAIT:g} s')
            states.append(future.result())
        except Exception as e:
            logger.info('Status query of %s failed: %s', device, e)
            states.append(_failed_printer_state(device, e, model))
    return states


# Why a printer did not answer, in the words of those who use it. The error
# itself is logged where it is caught.
_UNANSWERED = (
    ((errno.ENOENT, errno.ENODEV, errno.ENXIO), 'The printer is switched off or not connected'),
    ((errno.EACCES, errno.EPERM), 'No permission to use the printer'),
    ((errno.EBUSY,), 'The printer is in use by another program'),
    ((errno.ETIMEDOUT,), 'The printer does not answer'),
    ((errno.ECONNREFUSED,), 'The printer refuses the connection'),
    ((errno.EHOSTUNREACH, errno.ENETUNREACH, errno.EHOSTDOWN), 'The printer cannot be reached'),
)


def _unanswered_reason(error: Exception) -> str:
    """Why a printer did not answer, one of _UNANSWERED, or an empty string.
    Never the text of the error, which may show paths and other details of
    the server."""
    if isinstance(error, TimeoutError):
        return 'The printer does not answer'
    for numbers, text in _UNANSWERED:
        if getattr(error, 'errno', None) in numbers:
            return text
    return ''


def unanswered(error: Exception) -> str:
    """Why a printer did not answer the status query, e.g. 'The printer is
    switched off or not connected' for a device file that is gone."""
    return _unanswered_reason(error) or 'The printer does not answer the status query, the log of the server names the reason'


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
        'errors': [unanswered(error)],
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
    status = _network_printer(device_specifier, model if model in [m.identifier for m in ALL_MODELS] else default_model)
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
        status['status_type'] = BUSY_STATUS_TYPE
    else:
        status['status_type'] = 'Reply to status request' if printer_status in SNMP_READY else STOPPED_STATUS_TYPE
    status['phase_type'] = 'Printing state' if printing else 'Waiting to receive'
    return status


def _label_printer_model(name: str) -> str | None:
    """The brother_ql model of a name like "Brother QL-810W", None for other
    devices, e.g. office printers."""
    model = (name or '').removeprefix('Brother ').strip()
    return model if model in [m.identifier for m in ALL_MODELS] else None


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
    _status_cache[_lock_key(device_specifier)] = status
    return dict(status)


def _ipp_network_status(device_specifier: str, host: str, default_model: str, label_printers_only: bool) -> dict | None:
    """Status of a network printer over IPP, see query_network_status."""
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
            for (model, device), state in zip(config['PRINTERS'], _configured_states(config['PRINTERS'])):
                state = state or SIMULATOR_PRINTER.copy()
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
        elif is_vc500w(device_specifier, default_model):
            try:
                printer = query_vc500w_status(device_specifier)
            except (OSError, vc500w.VC500WError) as e:
                logger.info('Status query of %s failed: %s', device_specifier, e)
                printer = _failed_printer_state(device_specifier, e, VC500W)
            status.update(printer)
            status['printers'] = [printer, SIMULATOR_PRINTER.copy()]
            status['selected'] = device_specifier
            return status
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
        status['errors'] = ['The status of the printer could not be read, the log of the server names the reason']
        return status
