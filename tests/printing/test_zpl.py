"""Label printers on ZPL: the bytes of a label, sent to a device file and
over the network, and printing and status through the label designer."""
import json
import socket
import threading

import pytest

from app.labeldesigner.drivers import zpl
from app.labeldesigner.drivers.zpl import protocol
from tests.helpers import make_client

DEVICE = 'file:///dev/usb/lp_zpl'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '40'}])


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Zebra/ZPL'
    return client


@pytest.fixture
def sent(monkeypatch):
    out = []
    monkeypatch.setattr(protocol, 'write', lambda device, data: out.append((device, data)))
    return out


def test_label_as_a_graphic_field():
    # As labelprint sent its labels to a Datamax-O'Neil E-4204B
    assert protocol.label([b'\x80\x00', b'\x00\x01'], 2) == b'^XA\n^PW16\n^LL2\n^FO0,0^GFA,4,4,2,80000001\n^XZ\n'


@pytest.mark.parametrize('device, address', [('tcp://zebra.lan', ('zebra.lan', 9100)), ('tcp://192.0.2.1:6101', ('192.0.2.1', 6101)),
                                             ('tcp://[2001:db8::1]', ('2001:db8::1', 9100)), ('tcp://[2001:db8::1]:9100', ('2001:db8::1', 9100))])
def test_network_address(device, address):
    assert protocol.address(device) == address


def test_label_is_printed_as_large_as_it_is(client, sent):
    response = client.post('/labeldesigner/api/print', data={'label_size': 'zpl102x76', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}
    (device, data), = sent
    assert device == DEVICE
    assert data.startswith(b'^XA\n^PW816\n^LL608\n^FO0,0^GFA,62016,62016,102,') and data.endswith(b'\n^XZ\n')
    # Black is printed
    assert b'F' in data


def test_wider_label_than_the_printhead(client, sent):
    # A6 labels are 105 mm wide, a GK420t prints 104 mm
    client.post('/labeldesigner/api/print', data={'label_size': 'zpl105x148', 'print_type': 'text', 'text': TEXT})
    assert sent[0][1].startswith(b'^XA\n^PW832\n^LL1184\n')


def test_copies_are_one_label_each(client, sent):
    client.post('/labeldesigner/api/print', data={'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert sent[0][1].count(b'^XA') == 2


def test_rows_end_of_a_lying_label_first():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            # A 102 x 152 mm label drawn lying, its start black
            img = Image.new('RGB', (1216, 816), 'white')
            img.paste((0, 0, 0), (0, 0, 10, 816))
            return img
    _, rows, width = zpl.label_rows(_Label(), 'zpl102x152')
    assert (len(rows), width) == (1216, 102)
    # Its end leaves the printer first, as on the other printers, the last
    # row of a ZPL label does
    assert all(byte == 0xff for byte in rows[0]) and not any(rows[-1])


def test_rows_top_of_a_label_wider_than_long_first():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            # A 102 x 51 mm label, its top black
            img = Image.new('RGB', (816, 408), 'white')
            img.paste((0, 0, 0), (0, 0, 816, 10))
            return img
    _, rows, width = zpl.label_rows(_Label(), 'zpl102x51')
    assert (len(rows), width) == (408, 102)
    # Its top leaves the printer first, as on the other printers, the last
    # row of a ZPL label does
    assert not any(rows[0]) and all(byte == 0xff for byte in rows[-1])


def test_label_over_the_network(client, monkeypatch):
    server = socket.create_server(('127.0.0.1', 0))
    received = []

    def accept():
        conn, _ = server.accept()
        with conn:
            while data := conn.recv(65536):
                received.append(data)
    thread = threading.Thread(target=accept)
    thread.start()
    monkeypatch.setattr(protocol, 'PORT', server.getsockname()[1])
    device = 'tcp://127.0.0.1'
    response = client.post('/labeldesigner/api/print', data={
        'printer': device, 'label_size': 'zpl51x25', 'print_type': 'text', 'text': TEXT})
    thread.join(5)
    server.close()
    assert response.get_json() == {'success': True}
    assert b''.join(received).startswith(b'^XA\n^PW408\n^LL200\n')


def test_printer_on_usb_that_is_not_there(client, sent):
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['The printer is switched off or not connected']
    response = client.post('/labeldesigner/api/print', data={'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT})
    # Printing does not ask it
    assert response.get_json() == {'success': True}


def test_600_dpi_is_refused(client, sent):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'zpl57x32', 'print_type': 'text', 'text': TEXT, 'high_res': '1'})
    assert response.get_json() == {'success': False, 'message': 'The Zebra ZPL printer cannot print at 600 dpi, turn off 600 dpi printing'}
    assert sent == []


# As a Datamax-O'Neil E-4204B with 100 x 30 mm labels answered ~HS
DATAMAX_HS = (b'\x02030,0,0,0235,044,0,0,0,000,0,0,0\x03\r\n'
              b'\x02000,0,0,0,0,1,5,0,00000000,1,000\x03\r\n\x020000,0\x03\r\n')


def test_host_status():
    assert protocol.parse_host_status(DATAMAX_HS) == {
        'paper_out': False, 'paused': False, 'length': 235, 'under_temperature': False,
        'over_temperature': False, 'head_up': False, 'ribbon_out': False}
    assert protocol.parse_host_status(b'') is None


@pytest.mark.parametrize('length, size', [(235, (100, 30)), (1214, (102, 152)), (1184, (105, 148)), (500, None)])
def test_labels_by_their_length(length, size):
    # 4 x 6 in labels measured 1214 dots on a GK420t
    assert zpl.loaded(length) == size


def _answering(monkeypatch, answer):
    server = socket.create_server(('127.0.0.1', 0))

    def serve():
        conn, _ = server.accept()
        with conn:
            assert conn.recv(16) == b'~HS'
            conn.sendall(answer)
    thread = threading.Thread(target=serve)
    thread.start()
    monkeypatch.setattr(protocol, 'PORT', server.getsockname()[1])
    monkeypatch.setattr(protocol, 'ANSWER_WAIT', 0.5)
    return thread, server


@pytest.fixture(autouse=True)
def asked_afresh(monkeypatch):
    from app.labeldesigner.drivers import devices
    monkeypatch.setattr(zpl, '_asked', {})
    monkeypatch.setattr(devices, 'status_cache', {})


def test_status_over_the_network(monkeypatch):
    # The head of a GK420t with 4 x 6 in labels open
    answer = DATAMAX_HS.replace(b'0235', b'1214').replace(b'000,0,0,0,0', b'000,0,1,0,0')
    thread, server = _answering(monkeypatch, answer)
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    thread.join(5)
    server.close()
    assert state['errors'] == ['The printhead is open']
    assert (state['media_width'], state['media_length']) == (102, 152)


def test_status_without_an_answer(monkeypatch):
    thread, server = _answering(monkeypatch, b'')
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    thread.join(5)
    server.close()
    assert state['errors'] == [] and state['media_width'] is None


def test_asked_at_most_every_10_s_then_silence_is_an_error(monkeypatch):
    thread, server = _answering(monkeypatch, DATAMAX_HS)
    assert zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')['media_length'] == 30
    thread.join(5)
    server.close()
    # Not asked again within 10 s, told as it was
    assert zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')['media_length'] == 30
    # A Datamax with its printhead open does not answer
    monkeypatch.setitem(zpl._asked, 'tcp://127.0.0.1', 0)
    thread, server = _answering(monkeypatch, b'')
    state = zpl.DRIVER.status('tcp://127.0.0.1', 'Zebra/ZPL')
    thread.join(5)
    server.close()
    assert state['errors'] == [zpl.NO_ANSWER] and state['media_length'] == 30


def test_printer_through_pyusb(monkeypatch):
    printers = []

    class Printer:
        def __init__(self, device):
            assert device == 'usb://0x0a5f:0x0080/12J123456'
            self.reads, self.written, self.disposed = [b'old', b'', DATAMAX_HS], [], False
            printers.append(self)

        def read(self, length):
            return self.reads.pop(0) if self.reads else b''

        def write(self, data):
            self.written.append(data)

        def dispose(self):
            self.disposed = True
    monkeypatch.setattr(protocol.backends, 'UsbPrinter', Printer)
    monkeypatch.setattr(protocol, 'WRITE_CHUNK', 4)
    device = 'usb://0x0a5f:0x0080/12J123456'
    # An answer from before is not taken for this one
    assert protocol.host_status(device) == DATAMAX_HS
    protocol.write(device, b'^XA^XZ\n')
    assert printers[0].written == [b'~HS'] and printers[1].written == [b'^XA^', b'XZ\n']
    assert all(printer.disposed for printer in printers)


@pytest.mark.parametrize('device_id, model', [
    ('MFG:Zebra Technologies;CMD:ZPL;MDL:GK420t;CLS:PRINTER;', 'Zebra/ZPL'),
    ('MANUFACTURER:Zebra Technologies;COMMAND SET:EPL,ZPL;MODEL:ZD420;', 'Zebra/ZPL'),
    ("MFG:DatamaxONeil ;CMD:DPL;MDL:E4204B Mark III;CLS:PRINTER;", None),
    ('MFG:Brother;CMD:PT-CBP;MDL:QL-700;CLS:PRINTER;', None)])
def test_known_by_its_device_id(monkeypatch, device_id, model):
    from app.labeldesigner.drivers import devices
    monkeypatch.setattr(devices, 'usb_device_id', lambda device: device_id)
    assert zpl.DRIVER.model_of('file:///dev/usb/lp0') == model
