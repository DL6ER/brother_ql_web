"""ZPL of Zebra label printers, as in the ZPL programming guide of Zebra: the
label as a graphic field (^GFA), the way a Datamax-O'Neil E-4204B with its
ZPL emulation printed labels sent so before."""

import socket

DPI = 203
# Port of raw printing on the network, and how long connecting may take
PORT = 9100
CONNECT_WAIT = 10


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


def write(device: str, data: bytes):
    """Send to tcp://<host>[:<port>] or to a device file, e.g. usblp's."""
    if device.startswith('tcp://'):
        with socket.create_connection(address(device), timeout=CONNECT_WAIT) as s:
            s.sendall(data)
    else:
        with open(device.removeprefix('file://'), 'wb') as f:
            f.write(data)
