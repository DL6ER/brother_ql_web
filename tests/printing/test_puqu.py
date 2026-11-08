"""The PUQU Q00 (also the Reekon P1 Tagger): its protocol against a fake printer in
place of the RFCOMM socket, and printing and status through the label
designer."""
import json

import pytest

from app.labeldesigner.drivers import puqu
from app.labeldesigner.drivers.puqu import protocol
from app.labeldesigner.label import LabelOrientation
from tests.helpers import make_client

DEVICE = 'bt://CD:AC:BB:E0:46:4C/2'
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'OK', 'size': '60'}])


def reply(flags=0):
    # A state reply: its flags, the darkness in the top bits of the next byte
    return bytes([0x3a, flags, 0x54, 0, 60, 60, 0, 0x0a])


class FakePrinter:
    """Answers each state query with the next of its answers, the last one
    again once they are used up. After the rows of a label it tells the
    next of its reports on its own, if it has one."""

    def __init__(self, answers=(), reports=(), endless=b''):
        self.answers, self.reports, self.endless = list(answers) or [reply()], list(reports), endless
        self.sent, self.connected, self.pending = [], [], b''

    def __call__(self, device):
        self.connected.append(device)
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def settimeout(self, timeout):
        pass

    def sendall(self, data):
        self.sent.append(data)
        if data == protocol.STATE_QUERY:
            self.pending += self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        elif data[:1] != b'\x3a' and self.reports:
            self.pending += self.reports.pop(0)

    def recv(self, size):
        # A printer that keeps telling that it prints
        data, self.pending = self.pending or self.endless, b''
        return data

    def queries(self):
        return self.sent.count(protocol.STATE_QUERY)

    def headers(self):
        return [data for data in self.sent if data[:1] == b'\x3a' and data != protocol.STATE_QUERY]


@pytest.fixture
def fake(monkeypatch):
    def install(answers=(), reports=(), endless=b''):
        printer = FakePrinter(answers, reports, endless)
        monkeypatch.setattr(protocol, 'connect', printer)
        for name in ('PRINT_WAIT', 'SECONDS_PER_MM', 'LABEL_WAIT', 'ANSWER_WAIT', 'HEADER_PAUSE', 'QUIET'):
            monkeypatch.setattr(protocol, name, 0.01)
        return printer
    return install


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    client.application.config['PRINTER_MODEL'] = 'Q00'
    return client


def test_rows_are_compressed():
    # Bytes as the script printed them that this driver came from
    assert protocol.compress([[0] * 400]) == bytes([0x3c, 1, 0, 0, 50])
    row = [1] + [0] * 399
    assert protocol.compress([row, row]) == bytes([0x3c, 2, 0, 0, 49, 0x80])
    # A row with dots in its middle only
    row = [0] * 16 + [1] * 8 + [0] * 16
    assert protocol.compress([row]) == bytes([0x3c, 1, 0, 2, 2, 0xff])


def test_label_header_tells_its_size():
    data = protocol.label_data([[0] * 384] * 240)
    assert data[:8] == bytes([0x3a, 48, 240, 0, 5, 0, 0, 5])
    assert data[8:] == bytes([0x3c, 240, 0, 0, 48])


def test_state_replies_and_their_errors():
    received = b'\x00\x3a' + reply(protocol.NO_PAPER | protocol.COVER_OPEN) + reply()
    assert protocol.replies(received) == [reply(protocol.NO_PAPER | protocol.COVER_OPEN), reply()]
    assert protocol.errors(reply(protocol.NO_PAPER | protocol.COVER_OPEN)) == [
        'The printer has no labels', 'The cover of the printer is open']
    # A low battery still prints
    assert protocol.errors(reply(protocol.LOW_BATTERY)) == []


def test_address_and_channel():
    assert protocol.address('bt://cd:ac:bb:e0:46:4c') == ('CD:AC:BB:E0:46:4C', 2)
    assert protocol.address('bt://CD:AC:BB:E0:46:4C/1') == ('CD:AC:BB:E0:46:4C', 1)


class _Label:
    label_content = None

    def __init__(self, image, orientation=LabelOrientation.STANDARD):
        self.image, self.label_orientation = image, orientation

    def generate(self):
        return self.image


def test_rows_lie_across_the_printhead():
    from PIL import Image
    image = Image.new('RGB', (320, 240), 'white')
    image.putpixel((0, 0), (0, 0, 0))
    _, rows = puqu.label_rows(_Label(image), 'puqu40x30')
    # The 40 mm label lies 1 mm from the left of the printhead
    assert (len(rows), len(rows[0])) == (240, protocol.HEAD)
    assert rows[0].index(1) == 8
    # Rotated labels are turned across the printhead
    _, rows = puqu.label_rows(_Label(Image.new('RGB', (240, 384), 'white'), LabelOrientation.ROTATED), 'puqu50x30')
    assert (len(rows), len(rows[0])) == (240, protocol.HEAD)


def test_labels_longer_than_wide_are_turned_across_the_printhead():
    # The label designer draws a 50 x 80 mm label lying, 640 x 384 dots: it
    # was sent as 384 rows of its first 384 dots, its middle off to a side
    from PIL import Image
    image = Image.new('RGB', (640, 384), 'white')
    image.putpixel((320, 192), (0, 0, 0))
    _, rows = puqu.label_rows(_Label(image), 'puqu50x80')
    assert (len(rows), len(rows[0])) == (640, protocol.HEAD)
    dots = [(y, row.index(1)) for y, row in enumerate(rows) if 1 in row]
    assert len(dots) == 1 and abs(dots[0][0] - 320) <= 1 and abs(dots[0][1] - 192) <= 1


def test_labels_are_printed_one_after_the_other(client, fake):
    # A printer that tells nothing on its own is asked once per label, after it
    printer = fake()
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'puqu50x30', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert response.get_json() == {'success': True}
    header = bytes([0x3a, 48, 240, 0])
    kinds = ['query' if data == protocol.STATE_QUERY else 'header' if data[:4] == header else 'rows' for data in printer.sent]
    assert kinds == ['header', 'rows', 'query', 'header', 'rows', 'query']
    assert printer.connected == [DEVICE]


def test_printer_is_asked_once_it_is_quiet(client, fake):
    # A state query while it prints is taken for an empty label, a Q00 fed
    # one for each. A Reekon P1L told that it prints every 0.2 s, then nothing.
    printing = reply(protocol.PRINTING) * 3
    printer = fake(reports=[printing, printing])
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'puqu50x30', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert response.get_json() == {'success': True}
    kinds = ['query' if data == protocol.STATE_QUERY else 'label' for data in printer.sent]
    assert kinds == ['label', 'label', 'query', 'label', 'label', 'query']


def test_printer_that_keeps_printing_is_not_asked(client, fake):
    printer = fake(endless=reply(protocol.PRINTING))
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'puqu50x30', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert response.get_json() == {'success': False, 'message': 'The printer did not tell that it printed the label'}
    assert len(printer.headers()) == 1 and printer.queries() == 0


def test_error_while_printing_stops_the_job(client, fake):
    printer = fake(reports=[reply(protocol.NO_PAPER)])
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'puqu50x30', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert response.get_json() == {'success': False, 'message': 'The printer has no labels'}
    assert len(printer.headers()) == 1


def test_printer_that_does_not_answer_after_a_label(client, fake):
    printer = fake(answers=[b''])
    response = client.post('/labeldesigner/api/print', data={'label_size': 'puqu50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json() == {'success': False, 'message': 'The printer does not answer'}
    assert printer.queries() == 1


def test_printer_that_is_off(client, monkeypatch):
    def unreachable(device):
        raise OSError(112, 'Host is down')
    monkeypatch.setattr(protocol, 'connect', unreachable)
    response = client.post('/labeldesigner/api/print', data={'label_size': 'puqu50x30', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['success'] is False


def test_status_through_the_label_designer(client, fake):
    fake(answers=[reply(protocol.COVER_OPEN)])
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (printer_['model'], printer_['path']) == ('Q00', DEVICE)
    assert printer_['errors'] == ['The cover of the printer is open']


def test_600_dpi_is_refused(client, fake):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'puqu50x30', 'print_type': 'text', 'text': TEXT, 'high_res': '1'})
    assert response.get_json() == {'success': False, 'message': 'The Q00 cannot print at 600 dpi, turn off 600 dpi printing'}


def test_only_its_printers_print_its_labels(client):
    response = client.post('/labeldesigner/api/print', data={
        'label_size': '62', 'print_type': 'text', 'text': TEXT})
    assert response.get_json()['success'] is False
