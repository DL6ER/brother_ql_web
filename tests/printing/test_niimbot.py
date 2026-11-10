"""The Niimbot D110_M: its protocol against a fake printer in place of the
RFCOMM socket, and printing and status through the label designer."""
import json
import struct

import pytest

from app.labeldesigner.drivers import devices, niimbot
from app.labeldesigner.drivers.niimbot import protocol
from tests.helpers import make_client

DEVICE = 'bt://00:F4:BA:17:12:06'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '30'}])
# The replies of a D110_M as the script this driver came from got them
REPLIES = {0x21: (0x31, b'\x01'), 0x23: (0x33, b'\x01'), 0x01: (0x02, b'\x01'), 0x13: (0x14, b'\x01'),
           0xe3: (0xe4, b'\x01'), 0xa3: (0xb3, struct.pack('>H', 1)), 0xf3: (0xf4, b'\x01'),
           0x1a: (0x1b, b'\x00'), 0xdc: (0xdd, bytes.fromhex('101f0069006932460104'))}


class FakePrinter:
    def __init__(self):
        self.commands, self.pending = [], b''

    def close(self):
        pass

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        command = data[2]
        self.commands.append(command)
        # The heartbeat while printing is not waited for
        if command in REPLIES and not (command == 0xdc and 0x01 in self.commands):
            self.pending += protocol.packet(*REPLIES[command])

    def recv(self, size):
        data, self.pending = self.pending, b''
        return data


@pytest.fixture(autouse=True)
def answered(monkeypatch):
    monkeypatch.setattr(devices, '_unanswered', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(niimbot, '_asked', {})
    monkeypatch.setattr(devices, 'looked', 0.0)


@pytest.fixture
def fake(monkeypatch):
    printer = FakePrinter()

    def connect(device):
        # The real connection, its socket the fake printer
        c = protocol.Connection.__new__(protocol.Connection)
        c.s, c.received = printer, b''
        return c
    monkeypatch.setattr(protocol, 'connect', connect)
    monkeypatch.setattr(protocol, 'PAGE_POLL', 0.01)
    return printer


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Niimbot/D110_M'
    return client


def test_packets_as_the_script_sent_them():
    assert protocol.packet(0x21, b'\x03') == bytes.fromhex('555521010323aaaa')
    assert protocol.parse(protocol.packet(0x31, b'\x01') + b'\x55')[0] == [(0x31, b'\x01')]
    assert protocol.row_packets([[0] * 96] * 3) == [protocol.packet(0x84, b'\x00\x00\x03')]
    row = [1] + [0] * 95
    assert protocol.row_packets([row, row]) == [protocol.packet(0x85, bytes([0, 0, 1, 0, 0, 2, 0x80]) + bytes(11))]


def test_label_is_printed_in_the_order_of_the_script(client, fake):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': True}
    rows = [c for c in fake.commands if c in (0x84, 0x85)]
    sent = [c for c in fake.commands if c not in (0x84, 0x85)]
    assert rows and sent == [0xdc, 0x1a, 0x21, 0x23, 0x01, 0xdc, 0x13, 0xe3, 0xa3, 0xf3]


def test_rows_are_as_wide_as_the_label_across_the_printhead():
    from PIL import Image

    class _Label:
        label_content = None

        def generate(self):
            # Its start black
            img = Image.new('RGB', (320, 96), 'white')
            img.paste((0, 0, 0), (0, 0, 10, 96))
            return img
    _, rows = niimbot.label_rows(_Label(), 'niimbot12x40')
    assert (len(rows), len(rows[0])) == (320, 96)
    # Its end leaves the printer first, as on the other printers
    assert not any(rows[0]) and all(rows[-1])


def test_status_through_the_label_designer(client, fake):
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['model'], printer_['path'], printer_['errors']) == ('Niimbot/D110_M', DEVICE, [])
    assert fake.commands == [0xdc, 0x1a]
    # A roll without RFID tag has no size
    assert printer_['media_width'] is None


def test_status_tells_the_size_of_a_known_roll(client, fake, monkeypatch):
    tag = bytes(8) + b'\x0801222281' + b'\x10PZ1G910304007921' + struct.pack('>HHB', 192, 87, 1)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['media_width'], printer_['media_length']) == (12, 40)


def test_printer_is_asked_only_when_looking_for_printers(client, fake):
    # It beeps at each connection, the designer asks for the status every 20 s
    client.get('/labeldesigner/api/printer_status')
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands == [0xdc, 0x1a]
    assert client.get('/labeldesigner/api/printer_status?look=1').get_json()['printers'][0]['errors'] == []
    assert fake.commands == [0xdc, 0x1a] * 2
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands == [0xdc, 0x1a] * 2


def beat(lid=1, battery=3):
    # As a D110_M answers, 1 when its lid is closed
    return (0xdd, bytes.fromhex('101f0069006932460000')[:8] + bytes([lid, battery]))


@pytest.mark.parametrize('battery, low', [(0, True), (1, True), (2, False), (4, False)])
def test_status_tells_only_whether_the_battery_needs_charging(client, fake, monkeypatch, battery, low):
    monkeypatch.setitem(REPLIES, 0xdc, beat(battery=battery))
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['battery_low'] is low and 'battery' not in printer_


def test_open_lid_is_an_error(client, fake, monkeypatch):
    error = 'The cover of the printer is open'
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('10220054005432460004')))
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == [error]
    # Not printed then
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == error
    assert 0x21 not in fake.commands


def test_lid_closed_again_is_told_at_the_next_status_query(client, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('10220054005432460004')))
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The cover of the printer is open']
    # Closed, without "Look for printers"
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('101f0069006932460104')))
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == []
    # Then the kept state is told, the printer beeps at each connection
    sent = len(fake.commands)
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands[sent:] == []


def test_no_roll_is_not_an_open_lid(client, fake, monkeypatch):
    # As a D110_M answered with the lid closed and no roll inside
    monkeypatch.setitem(REPLIES, 0xdc, (0xdd, bytes.fromhex('10200097009732460104')))
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == []


def test_printer_that_stops_answering(client, fake, monkeypatch):
    monkeypatch.delitem(REPLIES, 0xe3)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == 'The printer does not answer'
    # Asked again at the next status query
    sent = len(fake.commands)
    client.get('/labeldesigner/api/printer_status')
    assert fake.commands[sent:sent + 1] == [0xdc]


def test_printer_that_does_not_tell_it_printed(client, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0xa3, (0xb3, struct.pack('>H', 0)))
    monkeypatch.setattr(protocol, 'PRINT_WAIT', 0)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['message'] == 'The printer did not tell that it printed the label'


def test_tag_laid_out_otherwise_is_no_tag(client, fake, monkeypatch):
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, bytes(8) + b'\x0801'))
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['errors'], printer_['media_width']) == ([], None)


def test_print_updates_the_status(client, fake, monkeypatch):
    client.get('/labeldesigner/api/printer_status')
    # Another roll was loaded since
    tag = bytes(8) + b'\x0801222281' + b'\x10PZ1G910304007921' + struct.pack('>HHB', 192, 87, 1)
    monkeypatch.setitem(REPLIES, 0x1a, (0x1b, tag))
    client.post('/labeldesigner/api/print', data={'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['media_width'], printer_['media_length']) == (12, 40)


def test_print_that_could_not_reach_the_printer_asks_it_again(client, fake, monkeypatch):
    client.get('/labeldesigner/api/printer_status')

    def unreachable(device):
        raise OSError(112, 'Host is down')
    monkeypatch.setattr(protocol, 'connect', unreachable)
    client.post('/labeldesigner/api/print', data={'label_size': 'niimbot12x40', 'print_type': 'text', 'text': TEXT})
    # No longer Ready
    assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The printer cannot be reached']


def test_printer_that_is_off_is_not_looked_for_at_every_status_query(client, monkeypatch):
    calls = []

    def unreachable(device):
        calls.append(device)
        raise OSError(112, 'Host is down')
    monkeypatch.setattr(protocol, 'connect', unreachable)
    for _ in range(2):
        assert client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]['errors'] == ['The printer cannot be reached']
    assert calls == [DEVICE]


def test_bluetooth_connections_are_set_up_one_at_a_time(monkeypatch):
    # The radio pages one device after another, a printer that was off made
    # the others give up waiting
    import threading
    import time
    from app.labeldesigner.drivers.puqu import protocol as puqu
    paging, most = [], []

    class Socket:
        def __init__(self, *args):
            pass

        def settimeout(self, timeout):
            pass

        def connect(self, address):
            paging.append(address)
            most.append(len(paging))
            time.sleep(0.05)
            paging.remove(address)

        def close(self):
            pass
    monkeypatch.setattr(protocol.socket, 'socket', Socket)
    threads = [threading.Thread(target=protocol.Connection, args=(DEVICE,)),
               threading.Thread(target=puqu.connect, args=('bt://CD:AC:BB:E0:46:4C/2',))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert max(most) == 1 and len(most) == 2
