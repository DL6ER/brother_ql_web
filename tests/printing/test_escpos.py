"""Epson receipt printers on ESC/POS: the bytes of a job, against a fake
printer in place of /dev/usb/lp<N>, and printing and status through the
label designer."""
import json

import pytest

from app.labeldesigner.drivers import escpos
from app.labeldesigner.drivers.escpos import protocol
from tests.helpers import make_client

DEVICE = 'file:///dev/usb/lp3'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])
# The fixed bits of each status byte
QUIET = 0x12


class FakePrinter:
    def __init__(self, offline=QUIET, paper=QUIET):
        self.sent, self.replies = b'', {protocol.OFFLINE: offline, protocol.PAPER: paper}

    def __call__(self, device):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def write(self, data):
        self.sent += data

    def ask(self, request):
        self.write(request)
        return self.replies[request]


@pytest.fixture
def fake(monkeypatch):
    def install(**replies):
        printer = FakePrinter(**replies)
        monkeypatch.setattr(protocol, 'connect', printer)
        return printer
    return install


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Epson/TM-T20II'
    return client


def test_raster_in_bands():
    rows = [b'\xff\x00'] * 300
    data = protocol.raster(rows, 2)
    assert data[:8] == b'\x1dv0\x00\x02\x00\x00\x01'
    assert data[8 + 512:8 + 512 + 8] == b'\x1dv0\x00\x02\x00\x2c\x00'
    assert len(data) == 2 * 8 + 600


def test_job_cuts_where_asked():
    rows = [b'\x80']
    assert protocol.job([(False, rows, 1), (True, rows, 1)]) == b'\x1b@' + protocol.raster(rows, 1) * 2 + b'\x1dVA\x00'


def test_answer_kept_from_before_is_not_taken_for_the_status(monkeypatch):
    import socket
    import threading
    ours, printer = socket.socketpair()
    c = protocol.Connection.__new__(protocol.Connection)
    c.dev = ours.fileno()
    printer.sendall(bytes([QUIET | 0x20]))

    def answer():
        assert printer.recv(3) == protocol.PAPER
        printer.sendall(bytes([QUIET]))
    thread = threading.Thread(target=answer)
    thread.start()
    assert c.ask(protocol.PAPER) == QUIET
    thread.join()
    ours.close(), printer.close()


def test_printer_through_pyusb(monkeypatch):
    class Printer:
        def __init__(self, device):
            assert device == 'usb://0x04b8:0x0e15/TM1'
            self.reads, self.written, self.disposed = [bytes([QUIET | 0x20]), b'', b'', bytes([QUIET])], [], False

        def read(self, length):
            return self.reads.pop(0) if self.reads else b''

        def write(self, data):
            self.written.append(data)

        def dispose(self):
            self.disposed = True
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Printer)
    monkeypatch.setattr(protocol, 'WRITE_CHUNK', 4)
    with protocol.connect('usb://0x04b8:0x0e15/TM1') as c:
        # An answer kept from before is not taken for the status
        assert c.ask(protocol.PAPER) == QUIET
        c.write(b'0123456789')
    assert c.printer.written == [protocol.PAPER, b'0123', b'4567', b'89'] and c.printer.disposed


def test_status_bytes():
    assert protocol.errors(QUIET, QUIET) == []
    assert protocol.errors(QUIET | 0x04, QUIET | 0x60) == ['The cover of the printer is open', 'The printer has no paper']
    # Paper near its end still prints
    assert protocol.errors(QUIET, QUIET | 0x0c) == []
    assert protocol.errors(QUIET | 0x40, QUIET) == ['The printer reports an error, remove paper stuck at its cutter or switch it off and on']


def test_rows_are_packed_black_printed():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            image = Image.new('RGB', (420, 10), 'white')
            image.putpixel((0, 0), (0, 0, 0))
            image.putpixel((419, 0), (0, 0, 0))
            return image
    _, rows, width = escpos.label_rows(_Label(), 'escpos58')
    assert (len(rows), width) == (10, 53)
    assert rows[0][0] == 0x80 and rows[0][-1] == 0x10 and rows[1] == bytes(53)


def test_label_is_printed_and_cut(client, fake):
    printer = fake()
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT, 'print_count': '2', 'cut': 'last'})
    assert response.get_json() == {'success': True}
    assert printer.sent.startswith(protocol.OFFLINE + protocol.PAPER + protocol.INIT)
    assert printer.sent.count(b'\x1dv0\x00') == 2 and printer.sent.count(protocol.CUT) == 1
    assert printer.sent.endswith(protocol.CUT)


def test_no_paper_does_not_print(client, fake):
    printer = fake(paper=QUIET | 0x60)
    response = client.post('/labeldesigner/api/print', data={'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': False, 'message': 'The printer has no paper'}
    assert protocol.INIT not in printer.sent


def test_paper_is_cut_at_least_after_the_last_label(client, fake):
    fake()
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'escpos58', 'print_type': 'text', 'text': TEXT, 'cut': 'none'})
    assert response.get_json() == {'success': False, 'message': 'The Epson TM-T20II cuts the paper at least after the last label'}


def test_status_through_the_label_designer(client, fake):
    fake(offline=QUIET | 0x04)
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['model'], printer_['path']) == ('Epson/TM-T20II', DEVICE)
    assert printer_['errors'] == ['The cover of the printer is open']
