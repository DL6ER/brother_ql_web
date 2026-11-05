"""Brother VC-500W, a full color label printer for ZINK tape, on the network
or on USB.

It takes short XML commands on TCP port 9100 or on its USB printer interface
(usblp, e.g., /dev/usb/lp0): reading /config.xml (model, resolution, the tape
in the cassette) and /status.xml (state, stage of the job, remaining tape),
and printing a JPEG that it scales to the tape. Every answer starts with a
<status> element with the result code and the size of the data that follows.
Lengths of tape are in inches. The protocol was worked out by Andrea
Micheloni for labelprinter
(https://m7i.org/projects/labelprinter-linux-python-for-vc-500w/), this is
an implementation of our own. Standard library only."""

import os
import select
import socket
import time
import xml.etree.ElementTree as ET

PORT = 9100
# Resolution across the tape (crossweb_dpi in /config.xml)
DPI = 320
# Fast and quality printing: the mode the printer uses, speed and lines per
# inch along the tape. More lines heat the ZINK paper longer, colors come out
# deeper, printing is slower.
MODES = {
    'fast': ('color', 1, 264),
    'quality': ('vivid', 0, 317),
    # Not its bw mode: at 317 lpi it prints the same as vivid in the same
    # time, at 400 lpi the firmware refuses it
}
DEFAULT_MODE = 'quality'
# A full cut leaves a tab of the backing in front of the label (tab_length_mm
# in /config.xml), a half cut keeps the label on the tape
CUT_MODES = ('full', 'half', 'none')
# The printer calls itself by its internal name
MODEL_NAMES = {'Wedge': 'VC-500W'}
# Its IEEE 1284 device ID on USB names the model
DEVICE_ID_MODEL = 'MDL:VC-500W;'

TIMEOUT = 5
# Data that arrives right after "ready to receive" is partly lost on USB when
# the job follows another one, the printer then waits for it forever
DATA_DELAY = 2.0
DATA_TIMEOUT = 15
# A job fails when the printer reports no progress for this long. It
# prepares a label for about 12 s without a change.
STALL_TIMEOUT = 60
# How long it may stay idle after the data before it starts the label
START_TIMEOUT = 10
# How long a printed label may wait to be taken out
TAKE_OUT_TIMEOUT = 300
# How long a job waits while the printer is busy, e.g., 6 s reading a
# cassette just put in
BUSY_TIMEOUT = 120
# A printer that lost data waits for it: zeros are sent until it answers.
# About 2.5 KB were lost of an 8 KB JPEG.
FILL_LIMIT = 32768
FILL_WAIT = 0.5
POLL_INTERVAL = 0.5
IDLE = 'IDLE'
EJECT_JAM = 'EJECT JAM'

# The stages of a job in /status.xml, as the label designer shows them
STAGES = {
    'INITIALIZE': 'Reading the cassette',
    'LOADING PAPER': 'Feeding the tape',
    'PROCESSING': 'Processing',
    'PREPARING PRINT': 'Preparing',
    'PREHEAT': 'Preheating',
    'PRINTING': 'Printing',
    'EJECTING': 'Ejecting',
    'UNLOADING': 'Winding the tape back',
}
TAKE_OUT = 'Take the printed label out of the printer'
DAMAGED = 'The printer lost part of the label and may have printed it damaged, print it again'
NO_ANSWER = 'The printer does not answer, switch it off and on again'
UNFINISHED = 'The printer did not report that it finished the label, check whether it was printed'
NO_CASSETTE = 'There is no cassette in the printer'
BUSY = 'The printer is busy, print again once it is ready'

XML_DECLARATION = '<?xml version="1.0" encoding="UTF-8"?>\n'


class VC500WError(Exception):
    """The printer refused a command or reported an error."""


def _element(xml: bytes) -> ET.Element:
    # ElementTree resolves no external entities, and the expat of current
    # Python versions limits the expansion of internal ones (billion laughs)
    try:
        return ET.fromstring(xml)
    except ET.ParseError as e:
        raise VC500WError(f'The printer answered with invalid XML: {e}') from None


def _text(element: ET.Element, path: str, default: str = '') -> str:
    found = element.find(path)
    return (found.text or '').strip() if found is not None else default


def is_device(target: str) -> bool:
    """Whether target is the path of a printer on USB, not a host."""
    return target.startswith('/')


def device_id_is_vc500w(device_id: str) -> bool:
    return DEVICE_ID_MODEL in device_id.strip() + ';'


class _Device:
    """The printer on USB through usblp, with the interface of a socket."""

    def __init__(self, path: str, timeout: float):
        self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
        # An answer someone before did not read would be taken for ours
        self._timeout = 0.1
        try:
            while self.recv(65536):
                pass
        except TimeoutError:
            pass
        self._timeout = timeout

    def settimeout(self, timeout: float):
        self._timeout = timeout

    def recv(self, size: int) -> bytes:
        # usblp reads nothing on a zero-length packet, that is no end
        deadline = time.monotonic() + self._timeout
        while True:
            ready, _, _ = select.select([self._fd], [], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                raise TimeoutError('The printer did not answer in time')
            try:
                data = os.read(self._fd, size)
            except BlockingIOError:
                continue
            if data:
                return data

    def sendall(self, data: bytes):
        view = memoryview(data)
        deadline = time.monotonic() + self._timeout
        while view:
            _, ready, _ = select.select([], [self._fd], [], max(0.0, deadline - time.monotonic()))
            if not ready:
                raise TimeoutError('The printer did not take the data in time')
            try:
                view = view[os.write(self._fd, view):]
            except BlockingIOError:
                continue

    def close(self):
        os.close(self._fd)


class Connection:
    """One connection to the printer, for one or a few commands: on the
    network to a host, on USB to the path of the printer."""

    def __init__(self, target: str, port: int = PORT, timeout: float = TIMEOUT):
        if is_device(target):
            self._socket = _Device(target, timeout)
        else:
            self._socket = socket.create_connection((target, port), timeout=timeout)
        self._timeout = timeout
        self._buffer = b''

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._socket.close()

    def _receive(self, timeout: float | None = None):
        self._socket.settimeout(self._timeout if timeout is None else timeout)
        data = self._socket.recv(65536)
        if not data:
            raise VC500WError('The printer closed the connection')
        self._buffer += data

    def answer(self, timeout: float | None = None) -> tuple[ET.Element, bytes]:
        """The next answer: its <status> element, and the data announced by
        <datasize>, which follows after a line break and a NUL byte."""
        end = self._buffer.find(b'</status>')
        while end < 0:
            self._receive(timeout)
            end = self._buffer.find(b'</status>')
        end += len(b'</status>')
        status = _element(self._buffer[:end])
        self._buffer = self._buffer[end:].lstrip(b'\n')
        code = _text(status, 'code', '0')
        if code != '0':
            comment = _text(status, 'comment')
            raise VC500WError(f'The printer refused the command: {comment or "code " + code}')
        size = int(_text(status, 'datasize', '0') or 0)
        data = b''
        if size > 0:
            while len(self._buffer) < size + 1:
                self._receive(timeout)
            self._buffer = self._buffer.lstrip(b'\x00')
            while len(self._buffer) < size:
                self._receive(timeout)
            data, self._buffer = self._buffer[:size], self._buffer[size:]
        return status, data

    def send(self, data: bytes):
        self._socket.settimeout(self._timeout)
        self._socket.sendall(data)

    def command(self, body: str, timeout: float | None = None) -> tuple[ET.Element, bytes]:
        self.send((XML_DECLARATION + body).encode())
        return self.answer(timeout)

    def read(self, path: str) -> ET.Element:
        """A file of the printer, e.g., /config.xml, as XML."""
        _, data = self.command(f'<read>\n<path>{path}</path>\n</read>')
        return _element(data.rstrip(b'\x00'))


def parse_config(config: ET.Element) -> dict:
    """The parts of /config.xml the label designer uses. Lengths of the tape
    in millimeters, None without a cassette."""
    def number(path):
        try:
            return float(_text(config, path))
        except ValueError:
            return None
    width = number('media_features/width_inches_media')
    initial = number('media_features/media_length_initial')
    name = _text(config, 'model_name')
    return {
        'model': MODEL_NAMES.get(name, name),
        'dpi': int(number('crossweb_dpi') or DPI),
        'max_print_size': int(number('maxprintsize') or 0),
        'tape_width_mm': round(width * 25.4, 1) if width else None,
        'tape_length_mm': round(initial * 25.4) if initial else None,
    }


def parse_status(status: ET.Element) -> dict:
    """The parts of /status.xml the label designer uses. The tape left in
    millimeters, None without a cassette."""
    try:
        remain = round(float(_text(status, 'remain')) * 25.4)
    except ValueError:
        remain = None
    try:
        line = int(_text(status, 'print_line'))
    except ValueError:
        line = 0
    return {
        'state': _text(status, 'print_state'),
        'stage': _text(status, 'print_job_stage'),
        'error': _text(status, 'print_job_error'),
        'line': line,
        'tape_remaining_mm': remain,
    }


def get_state(target: str, port: int = PORT) -> tuple[dict, dict]:
    """Configuration and status of the printer."""
    with Connection(target, port) as connection:
        config = parse_config(connection.read('/config.xml'))
        status = parse_status(connection.read('/status.xml'))
    return config, status


def print_error(status: dict) -> str:
    """Why a job failed, from the status, or an empty string."""
    error = status.get('error', '')
    if error and error.upper() not in ('NONE', 'NO_ERROR', '0'):
        return error
    return ''


def ready_error(status: dict) -> str:
    """Why the printer cannot print now, or an empty string. Without a
    cassette it is idle and reports no error, only no tape left."""
    if status['state'] == IDLE and status['tape_remaining_mm'] is None:
        return NO_CASSETTE
    return print_error(status)


def stage_name(stage: str) -> str:
    return STAGES.get(stage, stage.capitalize())


def _no_progress(stage: str, line: int):
    pass


def _take_out(connection: Connection, progress) -> str:
    """Wait until the label before is taken out of the printer: '' or why
    not."""
    progress(TAKE_OUT, 0)
    deadline = time.monotonic() + TAKE_OUT_TIMEOUT
    while time.monotonic() < deadline:
        time.sleep(POLL_INTERVAL)
        if print_error(parse_status(connection.read('/status.xml'))) != EJECT_JAM:
            return ''
    return TAKE_OUT


def _ready(connection: Connection, progress) -> str:
    """Wait while the printer is busy: '' once it can print, or why not."""
    deadline = time.monotonic() + BUSY_TIMEOUT
    while True:
        status = parse_status(connection.read('/status.xml'))
        if status['state'] in ('', IDLE) or print_error(status):
            return ready_error(status)
        if time.monotonic() > deadline:
            return BUSY
        progress(stage_name(status['stage']), status['line'])
        time.sleep(POLL_INTERVAL)


def _fill(connection: Connection) -> bool:
    """Send zeros until the printer has the data it waits for. Whether it
    answered."""
    for _ in range(FILL_LIMIT // 512):
        connection.send(bytes(512))
        try:
            connection.answer(timeout=FILL_WAIT)
            return True
        except TimeoutError:
            pass
    return False


def print_jpeg(target: str, jpeg: bytes, mode: str = DEFAULT_MODE, cut: str = 'full', port: int = PORT,
               progress=_no_progress) -> str:
    """Print a JPEG and wait until the printer is idle again. A printed label
    still in the printer is waited for, then the job is sent again. progress
    is called with each stage and the line printed. Returns the error the
    printer reports, or an empty string."""
    name, speed, lpi = MODES[mode]
    if cut not in CUT_MODES:
        raise ValueError(f'Unknown cut mode: {cut}')
    command = (f'<print>\n<mode>{name}</mode>\n<speed>{speed}</speed>\n<lpi>{lpi}</lpi>\n'
               f'<width>0</width>\n<height>0</height>\n<dataformat>jpeg</dataformat>\n'
               f'<autofit>1</autofit>\n<datasize>{len(jpeg)}</datasize>\n<cutmode>{cut}</cutmode>\n</print>')
    with Connection(target, port) as connection:
        for attempt in range(2):
            error = _ready(connection, progress)
            if error == EJECT_JAM:
                error = _take_out(connection, progress)
            if error:
                return error
            connection.command(command)
            time.sleep(DATA_DELAY)
            connection.send(jpeg)
            try:
                connection.answer(timeout=DATA_TIMEOUT)
            except TimeoutError:
                # Otherwise it waits until it is switched off
                return DAMAGED if _fill(connection) else NO_ANSWER
            error = _follow(connection, progress)
            if error != EJECT_JAM:
                return error
            # It dropped the label, the one before is still in it
            error = _take_out(connection, progress)
            if error:
                return error
    return TAKE_OUT


def _follow(connection: Connection, progress) -> str:
    """Follow a job until the printer is idle again: its error or ''."""
    # Until it starts the label, the printer may still be IDLE from before
    started = last_change = time.monotonic()
    busy = False
    last = None
    while True:
        status = parse_status(connection.read('/status.xml'))
        error = print_error(status)
        if error:
            return error
        now = time.monotonic()
        if (status['stage'], status['line']) != last:
            last = (status['stage'], status['line'])
            last_change = now
            if status['state'] != IDLE:
                progress(stage_name(status['stage']), status['line'])
        if status['state'] != IDLE:
            busy = True
        elif busy or now - started > START_TIMEOUT:
            return ''
        if now - last_change > STALL_TIMEOUT:
            return UNFINISHED
        time.sleep(POLL_INTERVAL)


def _values(element: ET.Element, prefix: str = '') -> dict:
    """All values of an XML element by their path, e.g.,
    media_features/width_inches."""
    values = {}
    for child in element:
        if len(child):
            values.update(_values(child, prefix + child.tag + '/'))
        else:
            values[prefix + child.tag] = (child.text or '').strip()
    return values


def watch(target: str, port: int = PORT, interval: float = 0.5, rounds: int | None = None, out=print):
    """Read the configuration and the status again and again, and print every
    value that changes, with the time. Read-only, to find out what the printer
    reports. Every round connects anew, so that other apps can still connect
    in between."""
    last: dict = {}
    count = 0
    while rounds is None or count < rounds:
        count += 1
        try:
            with Connection(target, port) as connection:
                values = {'config/' + path: value for path, value in _values(connection.read('/config.xml')).items()}
                values.update({'status/' + path: value for path, value in _values(connection.read('/status.xml')).items()})
        except (OSError, VC500WError) as e:
            values = {'error': str(e)}
        now = time.strftime('%H:%M:%S')
        for path, value in values.items():
            if last.get(path) != value:
                out(f'{now} {path}: {value}')
        for path in last.keys() - values.keys():
            out(f'{now} {path} is gone')
        last = values
        time.sleep(interval)


if __name__ == '__main__':
    # Read-only check of a printer: python -m app.labeldesigner.vc500w <host>
    # or <path on USB>. With --watch, every change of the configuration or the
    # status is printed until Ctrl+C.
    import sys
    if '--watch' in sys.argv[2:]:
        try:
            watch(sys.argv[1])
        except KeyboardInterrupt:
            pass
    else:
        config, status = get_state(sys.argv[1])
        print(config)
        print(status)
