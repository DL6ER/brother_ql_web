"""Status of network printers over IPP, with answers recorded from a
QL-810W and a fake printer for the network part."""
import os
import socket
import struct
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from app import create_app
from app.labeldesigner import ipp, printer

FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'fixtures', 'ipp')


def fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), 'rb') as f:
        return f.read()


def attribute(tag: int, name: str, value: bytes) -> bytes:
    return struct.pack('>BH', tag, len(name)) + name.encode() + struct.pack('>H', len(value)) + value


def response(*attributes: bytes, status: int = 0) -> bytes:
    """An IPP answer with the given printer attributes."""
    return struct.pack('>BBHI', 2, 0, status, 1) + b'\x04' + b''.join(attributes) + b'\x03'


def keywords(name: str, *values: str) -> bytes:
    return b''.join(attribute(ipp.TAG_KEYWORD, name if i == 0 else '', value.encode()) for i, value in enumerate(values))


def state(value: int) -> bytes:
    return attribute(ipp.TAG_ENUM, 'printer-state', struct.pack('>i', value))


def media(x: int, y: int | None, media_type: str) -> bytes:
    """media-col-ready with the size in hundredths of a millimetre."""
    def member(name, tag, value):
        return attribute(ipp.TAG_MEMBER_NAME, '', name.encode()) + attribute(tag, '', value)
    size = member('x-dimension', ipp.TAG_INTEGER, struct.pack('>i', x))
    if y is not None:
        size += member('y-dimension', ipp.TAG_INTEGER, struct.pack('>i', y))
    return (attribute(ipp.TAG_BEGIN_COLLECTION, 'media-col-ready', b'')
            + member('media-type', ipp.TAG_KEYWORD, media_type.encode())
            + attribute(ipp.TAG_MEMBER_NAME, '', b'media-size') + attribute(ipp.TAG_BEGIN_COLLECTION, '', b'')
            + size + attribute(ipp.TAG_END_COLLECTION, '', b'')
            + attribute(ipp.TAG_END_COLLECTION, '', b''))


def test_ready_ql_810w_is_decoded():
    attributes = ipp.decode_response(fixture('ql810w-ready-62x29.bin'))
    assert attributes['printer-make-and-model'] == 'Brother QL-810W'
    assert attributes['printer-state'] == ipp.IDLE
    assert attributes['printer-state-reasons'] == 'none'
    loaded = attributes['media-col-ready']
    assert loaded['media-type'] == 'labels'
    assert loaded['media-size'] == {'x-dimension': 2900, 'y-dimension': 6200}
    assert loaded['media-source-properties']['media-source-feed-direction'] == 'long-edge-first'
    # Several values become a list
    assert 'om_brother-label-62x100mm_62x100mm' in attributes['media-supported']
    # The 62 mm wide DK-11209, as the USB status reports it
    assert printer._ipp_media(loaded) == (62, 29, 'Die-cut labels')
    assert printer._ipp_errors(attributes) == []


def test_address_labels_are_fed_short_edge_first():
    # DK-11201: the 29 mm side runs across the print head
    attributes = ipp.decode_response(fixture('ql810w-ready-29x90.bin'))
    loaded = attributes['media-col-ready']
    assert loaded['media-source-properties']['media-source-feed-direction'] == 'short-edge-first'
    assert printer._ipp_media(loaded) == (29, 90, 'Die-cut labels')


def test_stopped_ql_810w_reports_an_error():
    # After a job for endless labels on a die-cut roll
    attributes = ipp.decode_response(fixture('ql810w-stopped-other-error.bin'))
    assert attributes['printer-state'] == ipp.STOPPED
    assert printer._ipp_errors(attributes) == ['See the lights or display of the printer']


def test_open_cover_of_a_ql_810w():
    # The QL-810W gives no reason more specific than "other"
    attributes = ipp.decode_response(fixture('ql810w-cover-open.bin'))
    assert attributes['printer-state'] == ipp.STOPPED
    assert printer._ipp_errors(attributes) == ['See the lights or display of the printer']
    # The labels are still named
    assert printer._ipp_media(attributes['media-col-ready']) == (62, 29, 'Die-cut labels')


def test_endless_roll_in_a_ql_810w_is_not_reported():
    # media-ready is empty and media-col-ready missing, the label designer
    # must not offer a label instead
    attributes = ipp.decode_response(fixture('ql810w-ready-endless.bin'))
    assert attributes['media-ready'] == ''
    assert 'media-col-ready' not in attributes
    assert printer._ipp_media(attributes.get('media-col-ready')) == (None, 0, None)
    assert printer._ipp_errors(attributes) == []


def test_request_asks_for_the_status_attributes():
    request = ipp.encode_request('ipp://192.0.2.1:631/ipp/print')
    assert request[:4] == b'\x02\x00\x00\x0b'
    attributes = ipp.decode_response(request)
    assert attributes['printer-uri'] == 'ipp://192.0.2.1:631/ipp/print'
    assert attributes['requested-attributes'] == list(ipp.STATUS_ATTRIBUTES)


@pytest.mark.parametrize('reasons, expected', [
    (['none'], []),
    (['cover-open-error'], ['The cover is open']),
    (['media-empty-error', 'media-needed'], ['There are no labels in the printer']),
    # Warnings and reports do not stop the printer
    (['media-low-warning', 'other-report'], []),
    (['toner-gone-error'], ['The printer reports "toner-gone"']),
])
def test_reasons_that_stop_the_printer(reasons, expected):
    attributes = ipp.decode_response(response(state(ipp.STOPPED if expected else ipp.IDLE), keywords('printer-state-reasons', *reasons)))
    assert printer._ipp_errors(attributes) == expected


def test_stopped_printer_without_reason():
    attributes = ipp.decode_response(response(state(ipp.STOPPED)))
    assert printer._ipp_errors(attributes) == ['The printer is stopped']


@pytest.mark.parametrize('x, y, media_type, expected', [
    (6200, None, 'continuous', (62, 0, 'Continuous length tape')),
    (2900, 0, 'roll', (29, 0, 'Continuous length tape')),
    (6200, 10000, 'labels', (62, 100, 'Die-cut labels')),
])
def test_loaded_media(x, y, media_type, expected):
    attributes = ipp.decode_response(response(media(x, y, media_type)))
    assert printer._ipp_media(attributes['media-col-ready']) == expected


def test_error_status_is_refused():
    with pytest.raises(ipp.IPPError, match='0x0400'):
        ipp.decode_response(response(status=0x0400))


@pytest.fixture
def fake_printer(monkeypatch):
    """An IPP server on localhost that answers with what the test sets."""
    answer = {'body': b'', 'status': 200}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(answer['status'])
            self.send_header('Content-Type', 'application/ipp')
            self.end_headers()
            self.wfile.write(answer['body'])

        def log_message(self, *args):
            pass

    server = HTTPServer(('127.0.0.1', 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(printer, 'IPP_PORT', server.server_port)
    yield answer
    server.shutdown()


def test_network_printer_status(fake_printer):
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    app = create_app()
    app.config['PRINTER_PRINTER'] = 'tcp://127.0.0.1'
    status = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert status['printers'][0]['path'] == 'tcp://127.0.0.1'
    for key, value in {'model': 'QL-810W', 'media_width': 62, 'media_length': 29, 'media_type': 'Die-cut labels',
                       'errors': [], 'red_support': True}.items():
        assert status[key] == value
        assert status['printers'][0][key] == value


def test_network_printer_without_ipp(monkeypatch):
    # Printers that only take jobs on port 9100 refuse the connection
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        port = unused.getsockname()[1]
    monkeypatch.setattr(printer, 'IPP_PORT', port)
    status = printer.query_network_status('tcp://127.0.0.1', 'QL-720NW')
    assert status['model'] == 'QL-720NW' and status['errors'] == [] and status['media_width'] is None


def test_network_printer_that_does_not_answer_keeps_its_last_status(fake_printer, monkeypatch):
    fake_printer['body'] = fixture('ql810w-ready-62x29.bin')
    printer.query_network_status('tcp://127.0.0.1', 'QL-800')

    def no_answer(*args, **kwargs):
        raise TimeoutError('timed out')
    monkeypatch.setattr(ipp, 'get_printer_attributes', no_answer)
    app = create_app()
    app.config['PRINTER_PRINTER'] = 'tcp://127.0.0.1'
    status = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert status['errors'] == ['The printer does not answer the status query (timed out)']
    assert (status['model'], status['media_width'], status['media_length']) == ('QL-810W', 62, 29)
