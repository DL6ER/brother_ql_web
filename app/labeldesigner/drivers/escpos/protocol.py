"""ESC/POS of Epson receipt printers, as in the ESC/POS command reference
of Epson: the label as a raster image (GS v 0), a cut after it (GS V 65),
and the status the printer tells at once (DLE EOT)."""

import os
import select
import time

from .. import backends

DPI = 203
# Rows per raster image, the printer takes them in pieces
BAND = 256
# How long the printer may take to answer a status request, in s
ANSWER_WAIT = 2
# A job goes to a printer on PyUSB in pieces: PyUSB gives up on a single
# write after 15 s, a long receipt takes longer to print
WRITE_CHUNK = 16384

INIT = b'\x1b@'
# Feed to the cutter and cut. A TM-T20II leaves one corner uncut, as its
# memory switches say, whatever the command.
CUT = b'\x1dVA\x00'
# Offline cause, and the roll paper sensor
OFFLINE = b'\x10\x04\x02'
PAPER = b'\x10\x04\x04'


class EscposError(Exception):
    pass


def raster(rows: list[bytes], width: int) -> bytes:
    """Rows of packed dots, width bytes each, MSB left, as GS v 0 images of
    BAND rows each."""
    out = b''
    for y in range(0, len(rows), BAND):
        band = rows[y:y + BAND]
        out += b'\x1dv0\x00' + bytes([width & 0xff, width >> 8, len(band) & 0xff, len(band) >> 8]) + b''.join(band)
    return out


def job(labels: list[tuple[bool, list[bytes], int]]) -> bytes:
    """(cut after it, rows, bytes per row) of each label as one job."""
    out = INIT
    for cut, rows, width in labels:
        out += raster(rows, width) + (CUT if cut else b'')
    return out


def errors(offline: int, paper: int) -> list[str]:
    found = []
    if offline & 0x04:
        found.append('The cover of the printer is open')
    if offline & 0x20 or paper & 0x60:
        found.append('The printer has no paper')
    if offline & 0x40:
        found.append('The printer reports an error, remove paper stuck at its cutter or switch it off and on')
    return found


class Connection:
    """The printer through the printer interface of the kernel (usblp)."""

    def __init__(self, device: str):
        self.dev = os.open(device.removeprefix('file://'), os.O_RDWR)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        os.close(self.dev)

    def write(self, data: bytes):
        while data:
            data = data[os.write(self.dev, data):]

    def ask(self, request: bytes) -> int:
        """The status byte the printer answers a DLE EOT request with."""
        # A TM-T20II kept an answer from before, which was taken for this one
        while select.select([self.dev], [], [], 0)[0] and os.read(self.dev, 64):
            pass
        self.write(request)
        # usblp also tells empty USB packets, read until an answer comes
        deadline = time.monotonic() + ANSWER_WAIT
        while (left := deadline - time.monotonic()) > 0:
            if select.select([self.dev], [], [], left)[0] and (reply := os.read(self.dev, 64)):
                return reply[-1]
        raise EscposError('no answer to the status request')


class UsbConnection(Connection):
    """The printer through PyUSB, usb://<vendor>:<product>[/<serial>]."""

    def __init__(self, device: str):
        self.printer = backends.UsbPrinter(device)

    def __exit__(self, *args):
        self.printer.dispose()

    def write(self, data: bytes):
        for start in range(0, len(data), WRITE_CHUNK):
            self.printer.write(data[start:start + WRITE_CHUNK])

    def ask(self, request: bytes) -> int:
        """The status byte the printer answers a DLE EOT request with."""
        while self.printer.read(64):
            pass
        self.write(request)
        deadline = time.monotonic() + ANSWER_WAIT
        while time.monotonic() < deadline:
            if reply := self.printer.read(64):
                return reply[-1]
        raise EscposError('no answer to the status request')


def connect(device: str) -> Connection:
    return UsbConnection(device) if device.startswith('usb://') else Connection(device)


def get_errors(c: Connection) -> list[str]:
    return errors(c.ask(OFFLINE), c.ask(PAPER))
