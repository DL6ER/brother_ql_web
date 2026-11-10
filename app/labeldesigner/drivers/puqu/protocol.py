"""The protocol of the PUQU Q00, also sold as the Reekon P1 Tagger (P1L), over
Bluetooth SPP (RFCOMM), as https://github.com/MatinxHT/puquprintJS
(src/protocol.ts) sends it. A label is a header and its rows, run-length
compressed. State replies are 8 bytes, 3a .. .. .. .. .. .. 0a."""

import socket
import time

from .. import devices

# 8 dots per mm, the printhead is 48 mm wide
DPI = 203
HEAD = 384
# The printers seen so far listen on RFCOMM channel 2
CHANNEL = 2
STATE_QUERY = bytes([0x3a, 0x5a, 0, 0, 0, 0, 0, 0x0a])

# Flags of a state reply, the ones that keep it from printing
NO_PAPER, COVER_OPEN, BAD_DATA, LOW_BATTERY, PRINTING, PRINT_ERROR, CANCELING = 0x80, 0x40, 0x20, 0x10, 0x08, 0x04, 0x02
ERRORS = {
    NO_PAPER: 'The printer has no labels',
    COVER_OPEN: 'The cover of the printer is open',
    BAD_DATA: 'The printer could not read the label',
    PRINT_ERROR: 'The printer reports a print error',
}

# Connecting took about 4 s, also with the printer close by
CONNECT_WAIT = 10
# How long the printer may take to answer, and how long a label takes at
# most: at least as long as the script this came from waited, 3 s for a
# 30 mm label, else as long as it takes per mm at the slowest
ANSWER_WAIT = 3
PRINT_WAIT = 3
SECONDS_PER_MM = 0.1
# A label that is still printing after that is waited for this long more
LABEL_WAIT = 10
# Between the header of a label and its rows, as the app does
HEADER_PAUSE = 0.003
# While it prints, a Reekon P1L told its state on its own every 0.2 s, printing,
# for 1.3 s for a 30 mm label, then nothing. It has printed once it was
# quiet this long, 4 of those reports.
QUIET = 0.8

class PuquError(Exception):
    pass


def address(device: str) -> tuple[str, int]:
    """MAC address and RFCOMM channel of bt://<MAC>[/<channel>]."""
    mac, _, channel = device[len('bt://'):].partition('/')
    return mac.upper(), int(channel or CHANNEL)


def compress(rows: list[list[int]]) -> bytes:
    """Rows of dots, 1 printed, as the printer takes them: equal rows that
    follow each other once with their count, and without the empty bytes at
    both ends."""
    out, y = bytearray(), 0
    while y < len(rows):
        n = 1
        while y + n < len(rows) and n < 0xffff and rows[y + n] == rows[y]:
            n += 1
        packed = bytearray((len(rows[y]) + 7) // 8)
        for x, dot in enumerate(rows[y]):
            if dot:
                packed[x // 8] |= 0x80 >> (x % 8)
        left = len(packed) - len(packed.lstrip(b'\0'))
        right = len(packed) - len(packed.rstrip(b'\0'))
        if left + right >= len(packed):
            left, right = 0, len(packed)
        out += bytes([0x3c, n & 0xff, n >> 8, left, right]) + packed[left:len(packed) - right]
        y += n
    return bytes(out)


def label_data(rows: list[list[int]]) -> bytes:
    """A label: the header with its size, then its rows."""
    body = compress(rows)
    width, height = len(rows[0]), len(rows)
    header = bytes([0x3a, width // 8, height & 0xff, height >> 8,
                    len(body) & 0xff, (len(body) >> 8) & 0xff, len(body) >> 16, 5])
    return header + body


def replies(data: bytes) -> list[bytes]:
    """The state replies in what the printer sent."""
    found, i = [], data.find(0x3a)
    while 0 <= i <= len(data) - 8:
        if data[i + 7] == 0x0a:
            found.append(data[i:i + 8])
            i += 8
        else:
            i += 1
        i = data.find(0x3a, i)
    return found


def flags(reply: bytes) -> int:
    return reply[1]


def errors(reply: bytes) -> list[str]:
    return [message for flag, message in ERRORS.items() if flags(reply) & flag]


def connect(device: str) -> socket.socket:
    s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
    s.settimeout(CONNECT_WAIT)
    try:
        with devices.bluetooth_turn:
            s.connect(address(device))
    except OSError:
        s.close()
        raise
    s.settimeout(ANSWER_WAIT)
    return s


def _read(s: socket.socket) -> bytes:
    try:
        return s.recv(256)
    except socket.timeout:
        return b''


def get_state(device: str) -> bytes:
    """The state reply of the printer. Raises OSError or PuquError if it
    does not answer."""
    with connect(device) as s:
        s.sendall(STATE_QUERY)
        received, deadline = b'', time.monotonic() + ANSWER_WAIT
        while time.monotonic() < deadline:
            received += _read(s)
            found = replies(received)
            if found:
                return found[-1]
        raise PuquError('no state reply')


def label_parts(rows: list[list[int]]) -> tuple[bytes, bytes]:
    """A label: the header with its size, and its rows."""
    data = label_data(rows)
    return data[:8], data[8:]


def print_labels(device: str, labels: list[list[list[int]]], mm_per_label: list[float]) -> str:
    """Print the labels, rows of dots each, one after the other, each once
    the printer has printed the one before. An empty string when all were
    printed, else the error the printer reports.

    The state is asked once per label, after it is printed: the printer
    takes a state query while it prints for a label and feeds an empty one."""
    with connect(device) as s:
        for rows, length in zip(labels, mm_per_label):
            header, body = label_parts(rows)
            s.sendall(header)
            time.sleep(HEADER_PAUSE)
            s.sendall(body)
            error = _wait_printed(s, max(PRINT_WAIT, length * SECONDS_PER_MM))
            if error:
                return error
    return ''


def _listen(s: socket.socket, wait: float, quiet: float = 0) -> tuple[list[bytes], bool]:
    """The state replies the printer sends within wait seconds, and whether
    it went quiet: with quiet, once it sent something and then nothing for
    that long."""
    received, deadline, last = b'', time.monotonic() + wait, None
    while time.monotonic() < deadline:
        if quiet and last is not None and time.monotonic() - last > quiet:
            break
        s.settimeout(max(min(deadline - time.monotonic(), 0.2), 0.01))
        data = _read(s)
        if data:
            received, last = received + data, time.monotonic()
    s.settimeout(ANSWER_WAIT)
    went_quiet = bool(quiet and last is not None and time.monotonic() - last > quiet)
    return replies(received), went_quiet


def _answer(s: socket.socket) -> list[bytes]:
    """The state reply to a query, as soon as it came."""
    received, deadline = b'', time.monotonic() + ANSWER_WAIT
    while time.monotonic() < deadline and not replies(received):
        s.settimeout(max(deadline - time.monotonic(), 0.01))
        received += _read(s)
    s.settimeout(ANSWER_WAIT)
    return replies(received)


def _wait_printed(s: socket.socket, wait: float) -> str:
    """Wait until the printer printed a label: an empty string, or the error
    it reports. While it prints it tells so on its own, then it is quiet.
    Only then it is asked its state once; one that tells nothing on its own
    after wait, as the script this came from asked 3 s after a label. While
    it still tells that it prints, it is never asked."""
    sent = time.monotonic()
    heard, quiet = _listen(s, wait, QUIET)
    if heard and not quiet:
        more, quiet = _listen(s, LABEL_WAIT, QUIET)
        heard += more
        if not quiet:
            return 'The printer did not tell that it printed the label'
    for reply in heard:
        if errors(reply):
            return errors(reply)[0]
    if not heard:
        time.sleep(max(sent + wait - time.monotonic(), 0))
    s.sendall(STATE_QUERY)
    found = _answer(s)
    if not found:
        raise PuquError('no state reply')
    if errors(found[-1]):
        return errors(found[-1])[0]
    return ''
