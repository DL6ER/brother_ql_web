"""ZPL of Zebra label printers, as in the ZPL programming guide of Zebra: the
label as a graphic field (^GFA), the way a Datamax-O'Neil E-4204B with its
ZPL emulation printed labels sent so before."""

import os
import select
import socket
import time

from .. import backends

DPI = 203
# Port of raw printing on the network, and how long connecting may take
PORT = 9100
CONNECT_WAIT = 10
# How long the printer may take to answer ~HS
ANSWER_WAIT = 2
# The host status request, answered with three lines <STX>...<ETX><CR><LF>
HOST_STATUS = b'~HS'
# A label goes to a printer on PyUSB in pieces: PyUSB gives up on a single
# write after 15 s
WRITE_CHUNK = 16384


def label(rows: list[bytes], width: int) -> bytes:
    """One label of rows of packed dots, width bytes each, MSB left, 1
    printed: as wide and long as its image, which starts at its top left."""
    data = b''.join(rows)
    return (f'^XA\n^PW{width * 8}\n^LL{len(rows)}\n'
            f'^FO0,0^GFA,{len(data)},{len(data)},{width},{data.hex().upper()}\n^XZ\n').encode()


def address(device: str) -> tuple[str, int]:
    """Host and port of tcp://<host>[:<port>], an IPv6 address in brackets."""
    rest = device[len('tcp://'):]
    if rest.startswith('['):
        host, _, port = rest[1:].partition(']')
        port = port.removeprefix(':')
    else:
        host, _, port = rest.partition(':')
    return host, int(port or PORT)


def _usb_write(printer, data: bytes):
    for start in range(0, len(data), WRITE_CHUNK):
        printer.write(data[start:start + WRITE_CHUNK])


def write(device: str, data: bytes):
    """Send to tcp://<host>[:<port>], to usb://<vendor>:<product>[/<serial>]
    through PyUSB or to a device file, e.g. usblp's."""
    if device.startswith('usb://'):
        printer = backends.UsbPrinter(device)
        try:
            _usb_write(printer, data)
        finally:
            printer.dispose()
    elif device.startswith('tcp://'):
        with socket.create_connection(address(device), timeout=CONNECT_WAIT) as s:
            s.sendall(data)
    else:
        with open(device.removeprefix('file://'), 'wb') as f:
            f.write(data)


def _read_answer(read) -> bytes:
    """The lines of an answer to ~HS, as many as came within ANSWER_WAIT."""
    answer = b''
    deadline = time.monotonic() + ANSWER_WAIT
    while answer.count(b'\x03') < 3 and (left := deadline - time.monotonic()) > 0:
        try:
            part = read(left)
        except OSError:
            break
        if part is None:
            break
        answer += part
    return answer


def host_status(device: str) -> bytes:
    """The answer of the printer to ~HS, empty if it gave none."""
    if device.startswith('tcp://'):
        with socket.create_connection(address(device), timeout=CONNECT_WAIT) as s:
            s.sendall(HOST_STATUS)

            def read(left):
                s.settimeout(left)
                return s.recv(256) or None
            return _read_answer(read)
    if device.startswith('usb://'):
        printer = backends.UsbPrinter(device)
        try:
            while printer.read(256):
                pass
            printer.write(HOST_STATUS)
            return _read_answer(lambda left: printer.read(256))
        finally:
            printer.dispose()
    fd = os.open(device.removeprefix('file://'), os.O_RDWR)
    try:
        # An answer from before is not taken for this one
        while select.select([fd], [], [], 0)[0] and os.read(fd, 256):
            pass
        os.write(fd, HOST_STATUS)

        # usblp also tells empty USB packets
        def read(left):
            return os.read(fd, 256) if select.select([fd], [], [], left)[0] else None
        return _read_answer(read)
    finally:
        os.close(fd)


def parse_host_status(answer: bytes) -> dict | None:
    """What the first two lines of an answer to ~HS tell, None for no answer.
    Fields as in the ZPL programming guide, e.g. a Datamax-O'Neil E-4204B with
    30 mm labels: 030,0,0,0235,044,0,0,0,000,0,0,0 and
    000,0,0,0,0,1,5,0,00000000,1,000."""
    lines = [part.split(b'\x03')[0].decode('ascii', 'replace').split(',') for part in answer.split(b'\x02')[1:]]
    if len(lines) < 2 or len(lines[0]) < 12 or len(lines[1]) < 4 or not lines[0][3].isdigit():
        return None
    one, two = lines[0], lines[1]
    return {'paper_out': one[1] == '1', 'paused': one[2] == '1', 'length': int(one[3]),
            'under_temperature': one[10] == '1', 'over_temperature': one[11] == '1',
            'head_up': two[2] == '1', 'ribbon_out': two[3] == '1'}


ERRORS = (('paper_out', 'No labels are in the printer'),
          ('head_up', 'The printhead is open'),
          ('ribbon_out', 'The ribbon has run out'),
          ('paused', 'The printer is paused'),
          ('over_temperature', 'The printhead is too hot'),
          ('under_temperature', 'The printhead is too cold'))


def errors(status: dict) -> list[str]:
    return [text for key, text in ERRORS if status[key]]
