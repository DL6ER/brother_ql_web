"""The protocol of the Niimbot D110_M (firmware 4.x) over classic Bluetooth (RFCOMM), the
print sequence of https://github.com/eigger/hass-niimbot
(custom_components/niimbot/niimprint/printer.py). Packets are
55 55 <command> <length> <data> <checksum> aa aa, the checksum the XOR of
command, length and data."""

import socket
import struct
import time

from .. import devices

# 8 dots per mm, the printhead is 15 mm wide
DPI = 203
HEAD = 120
CHANNEL = 1
# Connecting may take a few seconds, an answer comes within ANSWER_WAIT
CONNECT_WAIT = 10
ANSWER_WAIT = 5
# How long a label may take to print, and how often the printer is asked
# meanwhile how many pages it printed, as the script this came from does
PRINT_WAIT = 30
PAGE_POLL = 0.5
# Density 1 to 5
DENSITY = 3
# Label type when the roll has no RFID tag: labels with gaps
WITH_GAPS = 1

ERROR = 0xdb
# Byte 8 of the heartbeat of a D110_M: 1 with the lid closed, with a roll
# inside and without one (10 1f 00 69 00 69 32 46 01 04 and 10 20 00 97 00 97
# 32 46 01 04), 0 with the lid open (10 22 00 54 00 54 32 46 00 04). It tells
# no paper: the bytes that change without a roll change with an open lid too.
LID_OPEN = 0


class NiimbotError(Exception):
    pass


class NoAnswer(NiimbotError):
    pass


class NotPrinted(NiimbotError):
    pass


def address(device: str) -> tuple[str, int]:
    """MAC address and RFCOMM channel of bt://<MAC>[/<channel>]."""
    mac, _, channel = device[len('bt://'):].partition('/')
    return mac.upper(), int(channel or CHANNEL)


def packet(command: int, data: bytes) -> bytes:
    check = command ^ len(data)
    for byte in data:
        check ^= byte
    return bytes([0x55, 0x55, command, len(data), *data, check, 0xaa, 0xaa])


def parse(received: bytes) -> tuple[list[tuple[int, bytes]], bytes]:
    """The complete packets in what was received, as (command, data), and
    the rest."""
    found = []
    while True:
        i = received.find(b'\x55\x55')
        if i < 0:
            return found, b''
        if len(received) < i + 4 or len(received) < i + received[i + 3] + 7:
            return found, received[i:]
        n = received[i + 3]
        found.append((received[i + 2], bytes(received[i + 4:i + 4 + n])))
        received = received[i + n + 7:]


def row_packets(rows: list[list[int]]) -> list[bytes]:
    """Rows of dots, 1 printed, as packets: equal rows that follow each
    other once with their count, bitmap rows (85) with the dots in each
    third of the printhead, empty rows (84) without them."""
    out, y, third = [], 0, HEAD // 8 // 3
    while y < len(rows):
        n = 1
        while y + n < len(rows) and rows[y + n] == rows[y] and n < 255:
            n += 1
        packed = bytearray((len(rows[y]) + 7) // 8)
        for x, dot in enumerate(rows[y]):
            if dot:
                packed[x // 8] |= 0x80 >> (x % 8)
        if any(packed):
            counts = [sum(bin(byte).count('1') for byte in packed[k * third:(k + 1) * third]) for k in range(3)]
            out.append(packet(0x85, struct.pack('>H3BB', y, *counts, n) + bytes(packed)))
        else:
            out.append(packet(0x84, struct.pack('>HB', y, n)))
        y += n
    return out


class Connection:
    def __init__(self, device: str):
        self.s = socket.socket(socket.AF_BLUETOOTH, socket.SOCK_STREAM, socket.BTPROTO_RFCOMM)
        self.s.settimeout(CONNECT_WAIT)
        try:
            with devices.bluetooth_turn:
                self.s.connect(address(device))
        except OSError:
            self.s.close()
            raise
        self.received = b''

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.s.close()

    def send(self, data: bytes):
        self.s.sendall(data)

    def ask(self, command: int, data: bytes, replies) -> bytes:
        """Send a packet, the data of the first reply that is one of
        replies. Raises NiimbotError if the printer reports an error or does
        not answer."""
        replies = {replies} if isinstance(replies, int) else set(replies)
        self.s.sendall(packet(command, data))
        deadline = time.monotonic() + ANSWER_WAIT
        while time.monotonic() < deadline:
            self.s.settimeout(max(0.05, deadline - time.monotonic()))
            try:
                data = self.s.recv(256)
            except socket.timeout:
                break
            if not data:
                break
            found, self.received = parse(self.received + data)
            for reply, content in found:
                if reply == ERROR:
                    raise NiimbotError(f'error 0x{content[0]:02x} on 0x{command:02x}')
                if reply in replies:
                    return content
        raise NoAnswer(f'no answer to 0x{command:02x}')

    def ok(self, command: int, data: bytes, reply: int):
        if not self.ask(command, data, reply)[:1] == b'\x01':
            raise NiimbotError(f'refused 0x{command:02x}')


def connect(device: str) -> Connection:
    return Connection(device)


def heartbeat(c: Connection) -> dict:
    """Lid and battery (0 to 4) in the 10 bytes a D110_M answers, e.g.
    10 1f 00 69 00 69 32 46 01 04; the raw data if the length is another."""
    data = c.ask(0xdc, b'\x01', {0xd9, 0xdd, 0xde, 0xdf})
    if len(data) != 10:
        return {'raw': data.hex()}
    return {'lid': data[8], 'battery': data[9]}


def errors(beat: dict) -> list[str]:
    """What keeps the printer from printing, by its heartbeat."""
    return ['The cover of the printer is open'] if beat.get('lid') == LID_OPEN else []


def roll(c: Connection) -> dict | None:
    """The loaded roll from its RFID tag, None without a tag."""
    data = c.ask(0x1a, b'\x01', 0x1b)
    if len(data) <= 1:
        return None
    try:
        i = 8
        barcode = data[i + 1:i + 1 + data[i]].decode()
        i += 1 + data[i]
        serial = data[i + 1:i + 1 + data[i]].decode()
        i += 1 + data[i]
        total, used, kind = struct.unpack('>HHB', data[i:i + 5])
    except (IndexError, UnicodeDecodeError, struct.error):
        # A tag laid out otherwise is taken as no tag
        return None
    return {'barcode': barcode, 'serial': serial, 'total': total, 'used': used, 'type': kind}


def print_label(c: Connection, rows: list[list[int]], label_type: int):
    """Print one label, rows of dots across the printhead, as the script
    this came from prints one: a job of one page, then the printer is asked
    every PAGE_POLL seconds how many pages it printed."""
    height, width = len(rows), len(rows[0])
    c.ok(0x21, bytes([DENSITY]), 0x31)
    c.ok(0x23, bytes([label_type]), 0x33)
    c.ok(0x01, struct.pack('>H7B', 1, 0, 0, 0, 0, 0, 0, 0), 0x02)
    c.send(packet(0xdc, b'\x01'))
    c.ok(0x13, struct.pack('>HHHHB', height, width, 1, 0, 0), 0x14)
    for data in row_packets(rows):
        c.send(data)
    c.ok(0xe3, b'\x01', 0xe4)
    deadline = time.monotonic() + PRINT_WAIT
    while True:
        time.sleep(PAGE_POLL)
        if struct.unpack('>H', c.ask(0xa3, b'\x01', 0xb3)[:2])[0] >= 1:
            break
        if time.monotonic() > deadline:
            raise NotPrinted('the label was not printed')
    c.ok(0xf3, b'\x01', 0xf4)
