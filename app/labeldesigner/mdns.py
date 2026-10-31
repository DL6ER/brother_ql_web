"""Network printers that announce themselves with DNS-SD over multicast DNS
(RFC 6762, 6763), as Brother printers do for Bonjour and CUPS.

A single query is sent from a random port, so that the printers answer
directly to it ("legacy unicast", RFC 6762 section 6.7) and port 5353 can
stay with avahi or any other responder of the machine. Standard library
only. Containers need the network of the host (network_mode: host) for
multicast."""

import socket
import struct
import time

MDNS_ADDRESS = ('224.0.0.251', 5353)
# The raw printing port the label designer prints to, and IPP, which some
# printers announce with more details
SERVICE_TYPES = ('_pdl-datastream._tcp.local', '_ipp._tcp.local')

TYPE_A, TYPE_PTR, TYPE_TXT, TYPE_SRV = 1, 12, 16, 33
CLASS_IN = 1


def encode_query(service_types=SERVICE_TYPES) -> bytes:
    """A query for the instances of the given service types."""
    message = struct.pack('>HHHHHH', 0, 0, len(service_types), 0, 0, 0)
    for service_type in service_types:
        for label in service_type.split('.'):
            message += bytes([len(label)]) + label.encode()
        message += b'\x00' + struct.pack('>HH', TYPE_PTR, CLASS_IN)
    return message


def _read_name(data: bytes, offset: int) -> tuple[str, int]:
    """A name and the offset after it. Names may point to earlier ones
    (compression)."""
    labels = []
    end = None
    for _ in range(128):
        length = data[offset]
        if length & 0xC0 == 0xC0:
            if end is None:
                end = offset + 2
            offset = ((length & 0x3F) << 8) | data[offset + 1]
            continue
        if length == 0:
            return '.'.join(labels), end if end is not None else offset + 1
        labels.append(data[offset + 1:offset + 1 + length].decode('utf-8', 'replace'))
        offset += 1 + length
    raise ValueError('Name pointers loop')


def decode_records(data: bytes) -> list[tuple[str, int, object]]:
    """The answer, authority and additional records of a response as (name,
    type, value): PTR the name it points to, SRV (port, target), TXT a
    dictionary, A the address. Other types are skipped."""
    _, _, questions, *counts = struct.unpack('>HHHHHH', data[:12])
    offset = 12
    for _ in range(questions):
        _, offset = _read_name(data, offset)
        offset += 4
    records = []
    for _ in range(sum(counts)):
        name, offset = _read_name(data, offset)
        record_type, _, _, length = struct.unpack('>HHIH', data[offset:offset + 10])
        offset += 10
        rdata = data[offset:offset + length]
        if len(rdata) != length:
            raise ValueError('The response is cut off')
        if record_type == TYPE_PTR:
            records.append((name, record_type, _read_name(data, offset)[0]))
        elif record_type == TYPE_SRV and length >= 7:
            port = struct.unpack('>H', rdata[4:6])[0]
            records.append((name, record_type, (port, _read_name(data, offset + 6)[0])))
        elif record_type == TYPE_TXT:
            entries = {}
            i = 0
            while i < length:
                entry = rdata[i + 1:i + 1 + rdata[i]].decode('utf-8', 'replace')
                i += 1 + rdata[i]
                key, _, value = entry.partition('=')
                if key:
                    entries.setdefault(key.lower(), value)
            records.append((name, record_type, entries))
        elif record_type == TYPE_A and length == 4:
            records.append((name, record_type, socket.inet_ntoa(rdata)))
        offset += length
    return records


def services(records, sources=None) -> list[dict]:
    """The announced services: instance name, service type, address, port
    and the TXT entries (e.g. "ty" with the model). sources maps an instance
    to the address its answer came from, used without an A record."""
    by_type: dict = {}
    for name, record_type, value in records:
        by_type.setdefault(record_type, {}).setdefault(name.lower(), value)
    found = []
    for name, record_type, instance in records:
        if record_type != TYPE_PTR:
            continue
        key = instance.lower()
        port, target = by_type.get(TYPE_SRV, {}).get(key, (None, None))
        address = by_type.get(TYPE_A, {}).get((target or '').lower()) or (sources or {}).get(key)
        if address:
            found.append({'name': instance, 'service': name, 'address': address, 'port': port,
                          'txt': by_type.get(TYPE_TXT, {}).get(key, {})})
    return found


def discover(timeout: float = 1.0, service_types=SERVICE_TYPES) -> list[dict]:
    """Send one query and collect the answers for timeout seconds. Errors,
    e.g. without a network, find nothing."""
    records = []
    sources = {}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
            sock.sendto(encode_query(service_types), MDNS_ADDRESS)
            deadline = time.monotonic() + timeout
            while (remaining := deadline - time.monotonic()) > 0:
                sock.settimeout(remaining)
                try:
                    data, (address, _) = sock.recvfrom(9000)
                except socket.timeout:
                    break
                try:
                    answer = decode_records(data)
                except (ValueError, IndexError, struct.error):
                    continue
                records.extend(answer)
                for name, record_type, value in answer:
                    if record_type == TYPE_PTR:
                        sources.setdefault(value.lower(), address)
    except OSError:
        return []
    return services(records, sources)
