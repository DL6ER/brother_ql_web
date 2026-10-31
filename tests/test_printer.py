import errno
import os
import threading
import time

import pytest
from brother_ql.backends import linux_kernel

from app.labeldesigner import printer
from test_labeldesigner_api import EXAMPLE_FORMDATA, make_client

DEVICE = 'file:///dev/usb/lp0'


def _status_packet(status: int, phase: int = 0) -> bytes:
    """32 byte status message as sent by Brother QL printers"""
    packet = bytearray(32)
    packet[0:3] = b'\x80\x20\x42'
    packet[10], packet[11], packet[18], packet[19] = 62, 0x0A, status, phase
    return bytes(packet)


class ExclusiveUsbPrinter:
    """Fake printer device. Like the kernel usblp driver, it refuses a second
    open handle with EBUSY."""
    in_use = threading.Lock()
    print_seconds = 1.0
    status_seconds = 0.0

    def __init__(self, device_specifier):
        if not ExclusiveUsbPrinter.in_use.acquire(blocking=False):
            raise OSError(errno.EBUSY, os.strerror(errno.EBUSY), device_specifier)
        self.is_open = True
        self.responses = []

    def write(self, data):
        if data == b'\x1b\x69\x53':  # Status information request
            time.sleep(self.status_seconds)
            self.responses.append(_status_packet(0x00))
        else:
            time.sleep(self.print_seconds)
            self.responses += [_status_packet(0x01), _status_packet(0x06, 0x00)]

    def read(self, length=32):
        return self.responses.pop(0) if self.responses else b''

    def dispose(self):
        if getattr(self, 'is_open', False):
            self.is_open = False
            ExclusiveUsbPrinter.in_use.release()

    __del__ = dispose


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(linux_kernel, 'BrotherQLBackendLinuxKernel', ExclusiveUsbPrinter)
    monkeypatch.setattr(printer, '_status_cache', {})
    # Tests holding the device lock should not wait for the status long
    monkeypatch.setattr(printer, 'STATUS_LOCK_TIMEOUT', 0.2)
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    return client


def test_status_query_during_print(client):
    result = {}

    def print_label():
        result['print'] = client.application.test_client().post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA).get_json()

    thread = threading.Thread(target=print_label)
    thread.start()
    time.sleep(0.3)
    status = client.get('/labeldesigner/api/printer_status').get_json()
    thread.join()
    assert status['errors'] == []
    assert result['print'] == {'success': True}


def test_print_during_status_query(client, monkeypatch):
    monkeypatch.setattr(ExclusiveUsbPrinter, 'status_seconds', 0.5)
    monkeypatch.setattr(ExclusiveUsbPrinter, 'print_seconds', 0.1)
    thread = threading.Thread(target=lambda: client.application.test_client().get('/labeldesigner/api/printer_status'))
    thread.start()
    time.sleep(0.1)
    response = client.post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA)
    thread.join()
    assert response.get_json() == {'success': True}


def test_device_lock_is_exclusive():
    with printer.device_lock(DEVICE) as first:
        assert first
        with printer.device_lock(DEVICE, timeout=0) as second:
            assert not second
    with printer.device_lock(DEVICE, timeout=0) as again:
        assert again


@pytest.mark.parametrize('held, requested', [
    ('/dev/usb/lp0', 'file:///dev/usb/lp0'),
    ('tcp://Printer.local', 'tcp://printer.local:9100'),
    ('usb://0x04F9:0x209B', 'usb://0x04f9:0x209b'),
])
def test_device_lock_covers_all_spellings(held, requested):
    with printer.device_lock(held):
        with printer.device_lock(requested, timeout=0) as acquired:
            assert not acquired


def test_device_lock_uses_private_directory(tmp_path):
    with printer.device_lock(DEVICE):
        lock_dir = tmp_path / f'brother_ql_web-{os.getuid()}'
        assert lock_dir.is_dir()
        assert lock_dir.stat().st_mode & 0o777 == 0o700


def test_device_lock_refuses_shared_directory(tmp_path):
    # A directory other users can write to is not used for the lock files
    lock_dir = tmp_path / f'brother_ql_web-{os.getuid()}'
    lock_dir.mkdir(mode=0o777)
    lock_dir.chmod(0o777)
    with printer.device_lock(DEVICE) as acquired:
        assert acquired
        assert list(lock_dir.iterdir()) == []


def test_print_gives_up_when_printer_stays_busy(client, monkeypatch):
    monkeypatch.setattr(printer, 'DEVICE_LOCK_TIMEOUT', 0.2)
    with printer.device_lock(DEVICE):
        response = client.post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Printer is busy with another print job, please try again'


def test_failed_status_query_releases_device(client, monkeypatch):
    def broken_read(self, length=32):
        raise OSError('read failed')
    monkeypatch.setattr(ExclusiveUsbPrinter, 'read', broken_read)
    try:
        printer.query_status(DEVICE)
    except OSError:
        # The traceback still references the printer, the device must be closed anyway
        assert not ExclusiveUsbPrinter.in_use.locked()
    else:
        pytest.fail('query_status() did not raise')


def test_status_during_print_keeps_configured_model(client):
    with printer.device_lock(DEVICE):
        status = client.get('/labeldesigner/api/printer_status').get_json()
    assert status['model'] == client.application.config['PRINTER_MODEL']
    assert status['red_support'] is True
    assert status['media_width'] is None


def test_lock_files_are_bounded(tmp_path):
    for i in range(300):
        with printer.device_lock(f'tcp://printer-{i}.example', timeout=0):
            pass
    assert len(list((tmp_path / f'brother_ql_web-{os.getuid()}').iterdir())) <= 300
    # However many devices requests name, there are at most 4096 lock files
    assert len({printer._lock_slot(f'tcp://printer-{i}.example') for i in range(50000)}) <= 4096


def test_device_lock_without_flock_support(monkeypatch):
    def no_flock(fd, operation):
        raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))
    monkeypatch.setattr(printer.fcntl, 'flock', no_flock)
    with printer.device_lock(DEVICE) as acquired:
        assert acquired
        with printer.device_lock(DEVICE, timeout=0) as second:
            assert not second


def test_status_during_print_does_not_show_old_state(client):
    printer._status_cache[printer._lock_key(DEVICE)] = {
        'model': 'QL-800', 'media_width': 62, 'media_length': 0,
        'errors': ['No media when printing'], 'status_type': 'Error occurred', 'phase_type': 'Waiting to receive',
    }
    with printer.device_lock(DEVICE):
        status = client.get('/labeldesigner/api/printer_status').get_json()
    assert status['errors'] == []
    assert status['status_type'] == 'Printing'
    assert status['media_width'] == 62


def test_overlapping_status_queries(client, monkeypatch):
    monkeypatch.setattr(printer, 'STATUS_LOCK_TIMEOUT', 2.5)
    monkeypatch.setattr(ExclusiveUsbPrinter, 'status_seconds', 0.5)
    result = {}
    thread = threading.Thread(target=lambda: result.update(first=client.application.test_client().get('/labeldesigner/api/printer_status').get_json()))
    thread.start()
    time.sleep(0.1)
    second = client.get('/labeldesigner/api/printer_status').get_json()
    thread.join()
    # Neither query is mistaken for a running print job
    assert result['first']['status_type'] == second['status_type'] == 'Reply to status request'


def test_busy_printer_is_scanned_again(client, monkeypatch):
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path == '/dev/usb/lp0' or real_exists(path))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'

    def lp0(status):
        return next(p for p in status['printers'] if p['path'] == DEVICE)

    with printer.device_lock(DEVICE):
        assert lp0(client.get('/labeldesigner/api/printer_status').get_json())['status_type'] == 'Printing'
    # Right after the job the printer is queried again instead of the cached busy state
    assert lp0(client.get('/labeldesigner/api/printer_status').get_json())['status_type'] == 'Reply to status request'


def test_label_offset_corrections():
    from brother_ql.devicedependent import label_type_specs
    # Measured on a QL-1100: -6 centers the 103 x 164 mm die-cut label
    assert label_type_specs['103x164']['right_margin_dots'] == -6
    # Other labels keep the offsets of brother_ql
    assert label_type_specs['104']['right_margin_dots'] == -8
    assert label_type_specs['102x152']['right_margin_dots'] == 12


def test_printer_without_status_answer_stays_listed(client, monkeypatch):
    # A printer that does not answer is shown with its error instead of
    # disappearing, the label designer would otherwise switch to the simulator
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path == '/dev/usb/lp0' or real_exists(path))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'
    printer._status_cache[printer._lock_key(DEVICE)] = {'model': 'QL-1100', 'media_width': 104, 'media_length': 164}

    def no_answer(device):
        raise TimeoutError('No complete status response within 2.0 s')
    monkeypatch.setattr(printer, 'get_status', no_answer)

    status = client.get('/labeldesigner/api/printer_status').get_json()
    lp0 = next(p for p in status['printers'] if p['path'] == DEVICE)
    assert lp0['model'] == 'QL-1100'
    assert lp0['media_width'] == 104
    assert lp0['errors'] == ['The printer does not answer the status query (No complete status response within 2.0 s)']


def test_print_error_of_the_printer_is_reported(client, monkeypatch):
    def replace_media(self, data):
        # Error information 2, bit 0: "Replace media error"
        packet = bytearray(_status_packet(0x02))
        packet[9] = 0x01
        self.responses.append(bytes(packet))
    monkeypatch.setattr(ExclusiveUsbPrinter, 'write', replace_media)
    response = client.post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The printer reports: Replace media error'


@pytest.mark.parametrize('info, message', [
    ({'printer_state': None, 'did_print': False},
     'The printer did not answer after receiving the label, check whether it was printed'),
    ({'printer_state': {'errors': [], 'status_type': 'Phase change', 'phase_type': 'Printing state'}, 'did_print': False},
     'The printer did not confirm that the label was printed (last status: Phase change, Printing state)'),
    ({'printer_state': {'errors': [], 'status_type': 'Printing completed', 'phase_type': 'Printing state'}, 'did_print': True},
     'The label was printed, but the printer did not report that it is ready for the next one'),
])
def test_print_failure_messages(info, message):
    assert printer._print_failure(info) == message


def test_detected_printer_busy_before_first_answer_has_unknown_model(client, monkeypatch):
    # The configured model need not be the model of a detected printer
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path == '/dev/usb/lp0' or real_exists(path))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'
    with printer.device_lock(DEVICE):
        status = client.get('/labeldesigner/api/printer_status').get_json()
    lp0 = next(p for p in status['printers'] if p['path'] == DEVICE)
    assert lp0['status_type'] == 'Printing'
    assert lp0['model'] == 'Unknown'
