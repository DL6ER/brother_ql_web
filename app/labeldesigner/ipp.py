"""Status of network printers over IPP (RFC 8010, 8011).

Network printers do not answer status requests on the raw printing port
9100, but many answer IPP on port 631: whether they are ready, why not
(e.g. "cover-open") and which labels are loaded. This module sends a
Get-Printer-Attributes request for these and decodes the answer, with the
standard library only."""

import struct
import http.client
from itertools import count

# Operation and the tags of RFC 8010 that are needed here
GET_PRINTER_ATTRIBUTES = 0x000B
TAG_OPERATION = 0x01
TAG_END = 0x03
TAG_INTEGER = 0x21
TAG_BOOLEAN = 0x22
TAG_ENUM = 0x23
TAG_RANGE = 0x33
TAG_BEGIN_COLLECTION = 0x34
TAG_END_COLLECTION = 0x37
TAG_KEYWORD = 0x44
TAG_URI = 0x45
TAG_CHARSET = 0x47
TAG_LANGUAGE = 0x48
TAG_MEMBER_NAME = 0x4A

STATUS_ATTRIBUTES = (
    'printer-make-and-model',
    'printer-state',
    'printer-state-reasons',
    'media-col-ready',
)

# printer-state
IDLE, PROCESSING, STOPPED = 3, 4, 5

_request_ids = count(1)


class IPPError(Exception):
    """The printer answered, but not with the attributes."""


def _attribute(tag: int, name: str, value: bytes) -> bytes:
    name_bytes = name.encode()
    return struct.pack('>BH', tag, len(name_bytes)) + name_bytes + struct.pack('>H', len(value)) + value


def encode_request(uri: str, attributes=STATUS_ATTRIBUTES) -> bytes:
    """A Get-Printer-Attributes request for the given attributes."""
    body = struct.pack('>BBHI', 2, 0, GET_PRINTER_ATTRIBUTES, next(_request_ids) & 0x7FFFFFFF)
    body += bytes([TAG_OPERATION])
    body += _attribute(TAG_CHARSET, 'attributes-charset', b'utf-8')
    body += _attribute(TAG_LANGUAGE, 'attributes-natural-language', b'en')
    body += _attribute(TAG_URI, 'printer-uri', uri.encode())
    for i, name in enumerate(attributes):
        # Further values of the same attribute have an empty name
        body += _attribute(TAG_KEYWORD, 'requested-attributes' if i == 0 else '', name.encode())
    return body + bytes([TAG_END])


def _value(tag: int, raw: bytes):
    if tag in (TAG_INTEGER, TAG_ENUM) and len(raw) == 4:
        return struct.unpack('>i', raw)[0]
    if tag == TAG_BOOLEAN and len(raw) == 1:
        return raw != b'\x00'
    if tag == TAG_RANGE and len(raw) == 8:
        return struct.unpack('>ii', raw)
    # Strings of all kinds; octetString, dateTime and resolution are not
    # needed here
    return raw.decode('utf-8', 'replace')


def decode_response(data: bytes) -> dict:
    """The attributes of a response, by name. Attributes with several values
    become lists, collections dictionaries of their members."""
    if len(data) < 8:
        raise IPPError('The answer is too short')
    status = struct.unpack('>H', data[2:4])[0]
    # 0x0000 to 0x00FF are successful, e.g. 0x0001 when some requested
    # attributes are not supported
    if status > 0x00FF:
        raise IPPError(f'The printer answers with status 0x{status:04x}')
    attributes: dict = {}
    # Collections being decoded: the dictionary and the name of the member
    # whose value comes next
    stack: list = []
    name = ''
    i = 8
    while i < len(data):
        tag = data[i]
        i += 1
        if tag == TAG_END:
            break
        if tag < 0x10:
            # Start of the next group of attributes
            continue
        if i + 2 > len(data):
            raise IPPError('The answer is cut off')
        name_length = struct.unpack('>H', data[i:i + 2])[0]
        new_name = data[i + 2:i + 2 + name_length].decode('utf-8', 'replace')
        i += 2 + name_length
        value_length = struct.unpack('>H', data[i:i + 2])[0]
        raw = data[i + 2:i + 2 + value_length]
        i += 2 + value_length
        if len(raw) != value_length:
            raise IPPError('The answer is cut off')

        if tag == TAG_MEMBER_NAME:
            stack[-1][1] = raw.decode('utf-8', 'replace')
            continue
        if tag == TAG_END_COLLECTION:
            collection, _ = stack.pop()
            value = collection
            target_name = name if not stack else stack[-1][1]
            _add(stack[-1][0] if stack else attributes, target_name, value)
            continue
        if not stack and new_name:
            name = new_name
        if tag == TAG_BEGIN_COLLECTION:
            stack.append([{}, ''])
            continue
        value = _value(tag, raw)
        _add(stack[-1][0] if stack else attributes, stack[-1][1] if stack else name, value)
    return attributes


def _add(container: dict, name: str, value):
    """Add a value, a second one turns the attribute into a list."""
    if name not in container:
        container[name] = value
    elif isinstance(container[name], list):
        container[name].append(value)
    else:
        container[name] = [container[name], value]


def get_printer_attributes(host: str, port: int = 631, path: str = '/ipp/print',
                           attributes=STATUS_ATTRIBUTES, timeout: float = 3.0) -> dict:
    """Ask a printer for attributes. Raises OSError if it does not answer,
    e.g. ConnectionRefusedError without IPP, and IPPError for an answer
    that is not one."""
    host_in_uri = f'[{host}]' if ':' in host else host
    body = encode_request(f'ipp://{host_in_uri}:{port}{path}', attributes)
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    try:
        connection.request('POST', path, body, {'Content-Type': 'application/ipp'})
        response = connection.getresponse()
        data = response.read()
    finally:
        connection.close()
    if response.status != 200:
        raise IPPError(f'The printer answers with HTTP status {response.status}')
    return decode_response(data)
