"""Access to printer devices, whatever protocol they speak: one job or status
query at a time per device, the last status of each and why a printer did
not answer."""

import contextlib
import errno
import hashlib
import logging
import os
import stat
import tempfile
import threading
import time

from werkzeug.security import safe_join

try:
    import fcntl
except ImportError:  # Not available on Windows
    fcntl = None

logger = logging.getLogger(__name__)

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
# A network printer that is stopped without telling why, e.g. a QL-810W
# briefly at the end of every job
STOPPED_STATUS_TYPE = 'Stopped'

_fallback_locks = {}
_fallback_locks_guard = threading.Lock()
# The last status of each printer, by lock key
status_cache = {}
# Bluetooth connections are set up one at a time. The radio pages one device
# after another, a PUQU Q00 and a P1L that were off took 5 s each before a
# Niimbot D110_M was paged, which then gave up after its 10 s. Each now waits
# for its turn, its time to connect counts from it.
bluetooth_turn = threading.Lock()

# A printer on Bluetooth that did not answer is not looked for again by the
# status queries, only when printing on it or when asked to look for printers
# (look_again). Looking for two PUQU Q00 that had switched themselves off kept
# the radio of a Raspberry Pi 3B busy at every status query, and its Wi-Fi,
# which shares it, slowed down to seconds.
_unanswered = {}
# When the label designer last asked to look for printers. Printers that beep
# at each connection are asked only then and when printing.
looked = 0.0


def unanswered_before(device_specifier: str) -> Exception | None:
    """Why the printer did not answer last time, if it did not."""
    return _unanswered.get(lock_key(device_specifier))


# Errors of a printer that was not found, e.g. switched off. Only those are
# noted: a status query that took too long once made a Q00 that was on look
# off until "Look for printers".
NOT_FOUND = (errno.EHOSTDOWN, errno.EHOSTUNREACH)


def note_answer(device_specifier: str, error: Exception | None = None):
    """Note that the printer answered, or that it was not found."""
    if error is not None and getattr(error, 'errno', None) in NOT_FOUND:
        _unanswered[lock_key(device_specifier)] = error
    else:
        _unanswered.pop(lock_key(device_specifier), None)


def look_again():
    """Look for the printers that did not answer at the next status query."""
    global looked
    looked = time.monotonic()
    _unanswered.clear()


def lock_key(device_specifier: str) -> str:
    """The same key for every spelling of a device that requests may use.
    A printer addressed both as usb://<vendor>:<product> and as
    /dev/usb/lp<N> cannot be recognized as the same device without asking
    it, so it should be addressed in one way only. A host without port is
    one on port 9100, also an IPv6 address in brackets. A Bluetooth printer
    is one device whatever RFCOMM channel names it, and drivers differ in the
    channel they take when none is named, so its key is its MAC address only."""
    key = device_specifier.strip()
    if key.startswith('file://'):
        key = key[len('file://'):]
    elif key.lower().startswith('bt://'):
        key = 'bt://' + key.lower().split('/')[2]
    elif '://' in key:
        key = key.lower()
        address = key.split('://', 1)[1]
        if ':' not in (address.rpartition(']')[2] if address.startswith('[') else address):
            key += ':9100'
    return key


# The IEEE 1284 device ID of a printer on USB, which usblp read when it was
# plugged in
USB_DEVICE_ID = '/sys/class/usbmisc/{name}/device/ieee1284_id'


# The device IDs of printers on usb://, which do not change, asked once
_usb_device_ids: dict[str, str] = {}


def _usb_key(device_specifier: str) -> str:
    """One key per printer on usb://, however it is spelled: hex digits in
    either case, the serial number with or without leading zeros."""
    vendor_product, _, serial = device_specifier.removeprefix('usb://').partition('/')
    return f"{vendor_product.lower()}/{serial.lstrip('0')}"


def usb_device_id(device_specifier: str) -> str:
    """The IEEE 1284 device ID of a printer on USB, /dev/usb/lp<N> or
    usb://..., e.g. MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;..., empty for other
    printers or when it does not tell."""
    if device_specifier.startswith('usb://'):
        key = _usb_key(device_specifier)
        if key not in _usb_device_ids:
            from . import backends
            told = backends.device_id(device_specifier)
            if not told:
                return ''
            _usb_device_ids[key] = told
        return _usb_device_ids[key]
    path = os.path.normpath(device_specifier[len('file://'):] if device_specifier.startswith('file://') else device_specifier)
    if not path.startswith('/dev/usb/lp'):
        return ''
    try:
        with open(USB_DEVICE_ID.format(name=os.path.basename(path))) as file:
            return file.read()
    except OSError:
        return ''


def device_id_fields(device_id: str) -> dict:
    """The fields of an IEEE 1284 device ID by their short names, MFG, CMD,
    MDL, also when it spells them out (MANUFACTURER, COMMAND SET, MODEL)."""
    long_names = {'MANUFACTURER': 'MFG', 'COMMAND SET': 'CMD', 'MODEL': 'MDL'}
    fields = {}
    for field in device_id.split(';'):
        name, colon, value = field.partition(':')
        if colon:
            name = name.strip().upper()
            fields[long_names.get(name, name)] = value.strip()
    return fields


def found_usb_printers() -> list[str]:
    """The printers PyUSB finds that no driver of the kernel holds, as
    usb://..., their device IDs kept."""
    from . import backends
    found = []
    for spec, told in backends.found_printers():
        if told:
            _usb_device_ids[_usb_key(spec)] = told
        found.append(spec)
    return found


def _private_lock_dir() -> str:
    """Directory for the lock files that only this user can access. In a
    shared directory like /tmp other users could hold the locks or leave files
    behind that this user cannot open."""
    path = os.path.join(LOCK_DIR, f'labelburg-{os.getuid()}')
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
    slot = _lock_slot(lock_key(device_specifier))
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


def unanswered_reason(error: Exception) -> str:
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
    return unanswered_reason(error) or 'The printer does not answer the status query, the log of the server names the reason'


def failed_printer_state(device_specifier: str, error: Exception, model: str = 'Unknown') -> dict:
    """Status of a printer that did not answer the status query: model and
    media from its last answer, if there was one, and the error."""
    known = status_cache.get(lock_key(device_specifier), {})
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
