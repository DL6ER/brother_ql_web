"""Network printers that announce themselves with DNS-SD over multicast
DNS, decoded from answers built like those of a Brother printer."""
import struct

from app.labeldesigner import mdns


def name(*labels: str) -> bytes:
    return b''.join(bytes([len(label)]) + label.encode() for label in labels) + b'\x00'


def record(owner: bytes, record_type: int, rdata: bytes) -> bytes:
    return owner + struct.pack('>HHIH', record_type, 0x8001, 4500, len(rdata)) + rdata


def answer() -> bytes:
    """An answer with the question repeated, a PTR, and SRV, TXT and A as
    additional records, with compressed names like real responders use."""
    header = struct.pack('>HHHHHH', 0, 0x8400, 1, 1, 0, 3)
    question = name('_pdl-datastream', '_tcp', 'local') + struct.pack('>HH', mdns.TYPE_PTR, 1)
    # Offset 12 is the service type of the question
    service_type = b'\xc0\x0c'
    instance = bytes([15]) + b'Brother QL-810W' + service_type
    ptr = record(service_type, mdns.TYPE_PTR, instance)
    # The instance name starts in the PTR record
    instance_offset = 12 + len(question) + 2 + 10
    instance_pointer = struct.pack('>H', 0xC000 | instance_offset)
    host = name('BRWACF23C60C9B7', 'local')
    srv = record(instance_pointer, mdns.TYPE_SRV, struct.pack('>HHH', 0, 0, 9100) + host)
    txt = record(instance_pointer, mdns.TYPE_TXT, b''.join(
        bytes([len(entry)]) + entry for entry in (b'txtvers=1', b'ty=Brother QL-810W', b'usb_MDL=QL-810W')))
    a = record(host, mdns.TYPE_A, bytes([192, 0, 2, 94]))
    return header + question + ptr + srv + txt + a


def test_query_asks_for_the_services():
    query = mdns.encode_query(('_pdl-datastream._tcp.local',))
    assert query[:12] == struct.pack('>HHHHHH', 0, 0, 1, 0, 0, 0)
    assert query[12:] == name('_pdl-datastream', '_tcp', 'local') + struct.pack('>HH', mdns.TYPE_PTR, 1)


def test_announced_printer_is_decoded():
    records = mdns.decode_records(answer())
    assert mdns.services(records) == [{
        'name': 'Brother QL-810W._pdl-datastream._tcp.local',
        'service': '_pdl-datastream._tcp.local',
        'address': '192.0.2.94',
        'port': 9100,
        # Keys are case-insensitive
        'txt': {'txtvers': '1', 'ty': 'Brother QL-810W', 'usb_mdl': 'QL-810W'},
    }]


def test_address_of_the_answer_is_used_without_a_record():
    records = [r for r in mdns.decode_records(answer()) if r[1] != mdns.TYPE_A]
    found = mdns.services(records, {'brother ql-810w._pdl-datastream._tcp.local': '192.0.2.95'})
    assert found[0]['address'] == '192.0.2.95'


def test_looping_name_pointers_are_refused():
    data = struct.pack('>HHHHHH', 0, 0x8400, 0, 1, 0, 0) + b'\xc0\x0c'
    try:
        mdns.decode_records(data)
    except ValueError:
        pass
    else:
        raise AssertionError('A name pointing to itself was accepted')
