"""Values of network devices over SNMP version 1 (RFC 1157).

Brother printers answer SNMP requests on UDP port 161 like those of
Brother's apps: with the status they send back over USB, e.g. the cause of
an error and the loaded labels, and whether they print. This module sends
a get request and decodes the answer, with the standard library only."""

import socket
import time
from itertools import count

PORT = 161
COMMUNITY = 'public'

# Tags of the encoding (BER, X.690) and the PDUs of RFC 1157
INTEGER = 0x02
OCTET_STRING = 0x04
NULL = 0x05
OBJECT_IDENTIFIER = 0x06
SEQUENCE = 0x30
GET_REQUEST = 0xA0
GET_RESPONSE = 0xA2
# Counter, gauge and time ticks of RFC 1155, all numbers
UNSIGNED = (0x41, 0x42, 0x43)

# Error status: the device does not have the value
NO_SUCH_NAME = 2

_request_ids = count(1)


class SNMPError(Exception):
    """An answer that is not the value asked for."""


def _encode(tag: int, value: bytes) -> bytes:
    length = len(value)
    if length < 0x80:
        return bytes([tag, length]) + value
    size = length.to_bytes((length.bit_length() + 7) // 8, 'big')
    return bytes([tag, 0x80 | len(size)]) + size + value


def _integer(number: int) -> bytes:
    return _encode(INTEGER, number.to_bytes(number.bit_length() // 8 + 1, 'big', signed=True))


def _object_identifier(oid: str) -> bytes:
    numbers = [int(part) for part in oid.split('.')]
    data = bytearray([40 * numbers[0] + numbers[1]])
    for number in numbers[2:]:
        # Base 128, the high bit set on all but the last byte
        chunk = [number & 0x7F]
        number >>= 7
        while number:
            chunk.insert(0, 0x80 | number & 0x7F)
            number >>= 7
        data += bytes(chunk)
    return _encode(OBJECT_IDENTIFIER, bytes(data))


def _value(value) -> bytes:
    if value is None:
        return _encode(NULL, b'')
    if isinstance(value, bytes):
        return _encode(OCTET_STRING, value)
    return _integer(value)


def encode_message(pdu: int, request_id: int, values: list, community: str = COMMUNITY, error: int = 0) -> bytes:
    """A message with the (oid, value) pairs, value None for a request."""
    bindings = b''.join(_encode(SEQUENCE, _object_identifier(oid) + _value(value)) for oid, value in values)
    body = _integer(request_id) + _integer(error) + _integer(1 if error else 0) + _encode(SEQUENCE, bindings)
    return _encode(SEQUENCE, _integer(0) + _encode(OCTET_STRING, community.encode()) + _encode(pdu, body))


def _read(data: bytes, offset: int) -> tuple[int, bytes, int]:
    """Tag, value and the offset after it of the element at offset."""
    if offset + 2 > len(data):
        raise SNMPError('The answer is cut off')
    tag, length = data[offset], data[offset + 1]
    offset += 2
    if length & 0x80:
        size = length & 0x7F
        length = int.from_bytes(data[offset:offset + size], 'big')
        offset += size
    if offset + length > len(data):
        raise SNMPError('The answer is cut off')
    return tag, data[offset:offset + length], offset + length


def _elements(data: bytes) -> list[tuple[int, bytes]]:
    elements, offset = [], 0
    while offset < len(data):
        tag, value, offset = _read(data, offset)
        elements.append((tag, value))
    return elements


def _decode_object_identifier(data: bytes) -> str:
    if not data:
        raise SNMPError('Empty object identifier')
    numbers, number = [data[0] // 40, data[0] % 40], 0
    for byte in data[1:]:
        number = number << 7 | byte & 0x7F
        if not byte & 0x80:
            numbers.append(number)
            number = 0
    return '.'.join(map(str, numbers))


def _decode_value(tag: int, data: bytes):
    if tag == INTEGER:
        return int.from_bytes(data, 'big', signed=True)
    if tag in UNSIGNED:
        return int.from_bytes(data, 'big')
    if tag == OBJECT_IDENTIFIER:
        return _decode_object_identifier(data)
    if tag == NULL:
        return None
    # Strings and everything else as bytes
    return data


def decode_message(data: bytes) -> tuple[int, int, int, list]:
    """PDU, request id, error status and the (oid, value) pairs of a
    message."""
    try:
        tag, message, _ = _read(data, 0)
        if tag != SEQUENCE:
            raise SNMPError('No SNMP message')
        # Version, community and the PDU
        (_, _), (_, _), (pdu, body) = _elements(message)
        (_, request_id), (_, error), (_, _), (_, bindings) = _elements(body)
        values = []
        for _, binding in _elements(bindings):
            (_, oid), (value_tag, value) = _elements(binding)
            values.append((_decode_object_identifier(oid), _decode_value(value_tag, value)))
    except (TypeError, ValueError) as e:
        raise SNMPError(f'No SNMP message: {e}') from e
    return pdu, int.from_bytes(request_id, 'big', signed=True), int.from_bytes(error, 'big'), values


def get(host: str, oid: str, **kwargs):
    """The value of oid on a device, see get_values."""
    return get_values(host, [oid], **kwargs)[0]


def get_values(host: str, oids: list, port: int = PORT, community: str = COMMUNITY, timeout: float = 2.0,
               resend: float = 0.5) -> list:
    """The values of oids on a device, in one request: bytes for strings,
    an int for numbers. The request is sent again every resend seconds,
    an idle QL-810W can miss the first one. Raises OSError if it does not
    answer, e.g. TimeoutError without SNMP, and SNMPError if it does not
    have one of the values."""
    request_id = next(_request_ids)
    request = encode_message(GET_REQUEST, request_id, [(oid, None) for oid in oids], community)
    family, kind, protocol, _, address = socket.getaddrinfo(host, port, type=socket.SOCK_DGRAM)[0]
    with socket.socket(family, kind, protocol) as sock:
        # Connected, so that a closed port is refused instead of timing out
        sock.connect(address)
        deadline = time.monotonic() + timeout
        next_send = 0.0
        while True:
            now = time.monotonic()
            if now >= deadline:
                raise TimeoutError('timed out')
            if now >= next_send:
                sock.send(request)
                next_send = now + resend
            sock.settimeout(min(next_send, deadline) - now)
            try:
                pdu, answer_id, error, values = decode_message(sock.recv(65535))
            except (TimeoutError, SNMPError):
                continue
            # Answers to earlier requests are skipped, answers to a resent
            # one have the same id
            if pdu == GET_RESPONSE and answer_id == request_id:
                break
    if error:
        raise SNMPError(f'The device answers with error status {error}')
    answers = dict(values)
    missing = [oid for oid in oids if oid not in answers]
    if missing:
        raise SNMPError(f'The answer has no value for {", ".join(missing)}')
    return [answers[oid] for oid in oids]
