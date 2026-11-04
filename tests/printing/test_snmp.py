"""Status of Brother printers over SNMP, with messages recorded from a
QL-810W and Brother's app, and a fake agent for the network part."""
import os
import socket
import threading

import pytest

from app import create_app
from app.labeldesigner import printer, snmp
from tests.printing.test_ipp import fake_printer, fixture as ipp_fixture, network_job  # noqa: F401

FIXTURES = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures', 'snmp')
STATUS, PRINTER_STATUS = printer.SNMP_STATUS_OID, printer.SNMP_PRINTER_STATUS_OID
OTHER, IDLE, PRINTING = 1, 3, 4


def fixture(name: str) -> bytes:
    with open(os.path.join(FIXTURES, name), 'rb') as f:
        return f.read()


def status(name: str) -> bytes:
    """The status in a recorded answer."""
    return snmp.decode_message(fixture(name))[3][0][1]


READY_29x90 = status('ql810w-status-ready-29x90.bin')
CANNOT_FEED = status('ql810w-status-cannot-feed.bin')
COVER_OPEN = status('ql810w-status-cover-open.bin')
# Read from a QL-810W without a roll, and after a job for 62 mm endless
# labels with 29 x 90 mm labels loaded (error information 2: replace media)
NO_ROLL = bytes.fromhex('80 20 42 34 39 30 04 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 00 01 00 00 00 00 00 00')
REPLACE_MEDIA = COVER_OPEN[:9] + b'\x01' + COVER_OPEN[10:]


def endless_62(block: bytes) -> bytes:
    """The status with a 62 mm endless roll, as the raster command
    reference describes it: width, media type and length 0."""
    return block[:10] + b'\x3e\x0a' + block[12:17] + b'\x00' + block[18:]


ENDLESS_62 = endless_62(READY_29x90)


@pytest.fixture
def agent(fake_printer, monkeypatch):
    """An SNMP agent on localhost that answers get requests with what the
    test sets: the values by OID in 'answers', one after the other (the
    last one again and again), or 'error'. 'ignore' requests are not
    answered. After the fake IPP printer, which turns SNMP off."""
    state = {'answers': [{}], 'error': 0, 'ignore': 0, 'requests': 0}
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('127.0.0.1', 0))

    def serve():
        while True:
            try:
                data, address = sock.recvfrom(65535)
            except OSError:
                return
            state['requests'] += 1
            if state['ignore']:
                state['ignore'] -= 1
                continue
            _, request_id, _, values = snmp.decode_message(data)
            answer = state['answers'].pop(0) if len(state['answers']) > 1 else state['answers'][0]
            values = [(oid, answer.get(oid)) for oid, _ in values]
            sock.sendto(snmp.encode_message(snmp.GET_RESPONSE, request_id, values, error=state['error']), address)

    threading.Thread(target=serve, daemon=True).start()
    monkeypatch.setattr(printer, 'SNMP_PORT', sock.getsockname()[1])
    monkeypatch.setattr(printer, '_snmp_unavailable', {})
    state['port'] = sock.getsockname()[1]
    yield state
    sock.close()


def printer_state(block: bytes, printer_status: int = IDLE) -> dict:
    return {STATUS: block, PRINTER_STATUS: printer_status}


def test_request_like_the_app_of_brother():
    assert snmp.encode_message(snmp.GET_REQUEST, 0, [(STATUS, None)]) == fixture('ql810w-get-status.bin')


@pytest.mark.parametrize('name', ['ql810w-status-ready-29x90.bin', 'ql810w-status-cannot-feed.bin',
                                  'ql810w-status-cover-open.bin', 'ql810w-no-such-name.bin'])
def test_recorded_answers_are_decoded_and_encoded_alike(name):
    pdu, request_id, error, values = snmp.decode_message(fixture(name))
    assert pdu == snmp.GET_RESPONSE and request_id == 0
    assert snmp.encode_message(pdu, request_id, values, error=error) == fixture(name)


@pytest.mark.parametrize('data', [b'', b'\x30\x10\x02\x01', b'\x04\x00', bytes(fixture('ql810w-get-status.bin')[:-3])])
def test_no_snmp_message(data):
    with pytest.raises(snmp.SNMPError):
        snmp.decode_message(data)


def test_large_numbers_and_long_values():
    oid = '1.3.6.1.4.1.2435.2.4.3.2435.5.39.1.0'
    message = snmp.encode_message(snmp.GET_RESPONSE, 2 ** 31 - 1, [(oid, b'x' * 300), ('1.3.6.1.2.1.1.3.0', -129)])
    assert snmp.decode_message(message) == (snmp.GET_RESPONSE, 2 ** 31 - 1, 0, [(oid, b'x' * 300), ('1.3.6.1.2.1.1.3.0', -129)])


def test_get(agent):
    agent['answers'] = [printer_state(READY_29x90)]
    assert snmp.get('127.0.0.1', STATUS, port=agent['port']) == READY_29x90
    assert snmp.get_values('127.0.0.1', [PRINTER_STATUS, STATUS], port=agent['port']) == [IDLE, READY_29x90]
    assert agent['requests'] == 2


def test_get_sends_again_when_the_first_request_is_lost(agent):
    # An idle QL-810W missed the first request
    agent['answers'], agent['ignore'] = [printer_state(READY_29x90)], 1
    assert snmp.get('127.0.0.1', STATUS, port=agent['port'], resend=0.2) == READY_29x90
    assert agent['requests'] == 2


def test_value_the_device_does_not_have(agent):
    agent['error'] = snmp.NO_SUCH_NAME
    with pytest.raises(snmp.SNMPError, match='error status 2'):
        snmp.get('127.0.0.1', STATUS, port=agent['port'])


def test_device_without_snmp():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as unused:
        unused.bind(('127.0.0.1', 0))
        port = unused.getsockname()[1]
    with pytest.raises(OSError):
        snmp.get('127.0.0.1', STATUS, port=port, timeout=0.5)


def query(agent, block: bytes, printer_status: int = IDLE, model: str = 'QL-810W') -> dict:
    agent['answers'] = [printer_state(block, printer_status)]
    return printer.query_network_status('tcp://127.0.0.1', model)


def test_status_over_snmp(fake_printer, agent):
    # The configured model need not be the one of the printer
    state = query(agent, READY_29x90, model='QL-800')
    for key, value in {'model': 'QL-810W', 'media_width': 29, 'media_length': 90, 'media_type': 'Die-cut labels',
                       'errors': [], 'status_type': 'Reply to status request', 'phase_type': 'Waiting to receive',
                       'red_support': True}.items():
        assert state[key] == value
    assert fake_printer['requests'] == 0


@pytest.mark.parametrize('block, expected', [
    (CANNOT_FEED, [printer.FEED_ERROR]),
    (COVER_OPEN, ['The cover is open']),
    (REPLACE_MEDIA, ['The loaded labels do not fit the job']),
])
def test_cause_of_an_error(agent, block, expected):
    state = query(agent, block, OTHER)
    assert state['errors'] == expected and state['status_type'] == 'Error occurred'


def test_endless_roll(agent):
    state = query(agent, ENDLESS_62)
    assert (state['media_width'], state['media_length'], state['media_type']) == (62, 0, 'Continuous length tape')
    assert state['errors'] == []


def test_no_roll(agent):
    state = query(agent, NO_ROLL)
    assert state['errors'] == ['There are no labels in the printer'] and state['status_type'] == 'Error occurred'


@pytest.mark.parametrize('printer_status, status_type, phase_type', [
    (PRINTING, 'Printing', 'Printing state'),
    # Briefly at the end of every job
    (OTHER, 'Stopped', 'Waiting to receive'),
])
def test_printing(agent, printer_status, status_type, phase_type):
    state = query(agent, READY_29x90, printer_status)
    assert (state['status_type'], state['phase_type'], state['errors']) == (status_type, phase_type, [])


def test_ipp_for_a_printer_without_snmp(fake_printer, agent, monkeypatch):
    fake_printer['body'] = ipp_fixture('ql810w-ready-62x29.bin')
    agent['error'] = snmp.NO_SUCH_NAME
    for _ in range(3):
        state = printer.query_network_status('tcp://127.0.0.1', 'QL-810W')
        assert (state['media_width'], state['media_length']) == (62, 29)
    # Not asked again for a while
    assert (agent['requests'], fake_printer['requests']) == (1, 3)
    agent['error'], agent['answers'] = 0, [printer_state(READY_29x90)]
    monkeypatch.setattr(printer, 'SNMP_RETRY', 0)
    state = printer.query_network_status('tcp://127.0.0.1', 'QL-810W')
    assert (state['media_width'], state['media_length']) == (29, 90)


def test_ipp_for_a_device_without_the_status(fake_printer, agent):
    # e.g. an office printer of Brother
    fake_printer['body'] = ipp_fixture('ql810w-ready-62x29.bin')
    agent['answers'] = [{PRINTER_STATUS: IDLE}]
    assert printer.query_network_status('tcp://127.0.0.1', 'QL-810W')['media_width'] == 62


def test_job_waits_until_the_printer_finished(network_job, agent):
    answer, sent, print_label = network_job
    agent['answers'] = [printer_state(ENDLESS_62, status) for status in (IDLE, PRINTING, PRINTING, OTHER, IDLE)]
    assert print_label().get_json() == {'success': True}
    assert sent == ['tcp://127.0.0.1']
    # Checked before, then asked until it was idle again, without IPP
    assert (agent['requests'], answer['requests']) == (5, 0)


def test_job_with_labels_that_cannot_be_fed(network_job, agent):
    answer, sent, print_label = network_job
    agent['answers'] = [printer_state(ENDLESS_62), printer_state(ENDLESS_62, PRINTING),
                        printer_state(endless_62(CANNOT_FEED), OTHER)]
    response = print_label()
    assert response.status_code == 503
    # The printer prints the label once the labels are fed
    assert response.get_json()['message'] == 'The printer reports: ' + printer.FEED_ERROR
    assert 'Do not print it again' in response.get_json()['message']


def test_job_is_not_sent_with_the_cover_open(network_job, agent):
    answer, sent, print_label = network_job
    agent['answers'] = [printer_state(endless_62(COVER_OPEN), OTHER)]
    response = print_label()
    assert response.status_code == 503
    assert response.get_json()['message'] == 'The printer reports: The cover is open'
    assert sent == []


def test_status_of_a_network_printer_without_a_roll(agent):
    agent['answers'] = [printer_state(NO_ROLL)]
    app = create_app()
    app.config['PRINTER_PRINTER'] = 'tcp://127.0.0.1'
    state = app.test_client().get('/labeldesigner/api/printer_status').get_json()
    assert state['errors'] == ['There are no labels in the printer']
    assert state['printers'][0]['errors'] == ['There are no labels in the printer']
