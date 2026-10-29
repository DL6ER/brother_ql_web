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
