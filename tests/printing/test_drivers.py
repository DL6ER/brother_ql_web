"""The printer drivers behind the interface of drivers/base.py, and which
one drives a printer."""

import socket
import threading

import pytest

from app.labeldesigner import drivers
from app.labeldesigner.drivers import devices
from app.labeldesigner.drivers.base import Driver
from app.labeldesigner.drivers.brother import network
from app.labeldesigner.label_names import label_name, label_printers


@pytest.mark.parametrize('driver', drivers.DRIVERS, ids=lambda driver: type(driver).__name__)
def test_driver_describes_its_printers(driver):
    assert driver.models and driver.labels and driver.device_patterns and driver.dpi(False) > 0
    for model in driver.models:
        assert drivers.for_model(model) is driver
        assert driver.colors(model) in ('black', 'red', 'color')
    for label in driver.labels:
        assert drivers.for_label(label.identifier) is driver
        assert label_name(label) == driver.label_names[label.identifier]
        assert driver.palette(label) in ('black', 'red', 'color')
        # Only its own printers print its label sizes
        assert set(label_printers(label)) <= set(driver.models)


def test_models_and_label_sizes_belong_to_one_driver():
    assert len(set(drivers.MODELS)) == len(drivers.MODELS)
    assert len({label.identifier for label in drivers.LABELS}) == len(drivers.LABELS)


def test_driver_of_a_printer():
    # The address or device ID tells first, then the model
    assert drivers.resolve('vc500w://192.0.2.1', 'QL-800') == (drivers.VC500W, 'VC-500W')
    assert drivers.resolve('tcp://192.0.2.1', 'VC-500W') == (drivers.VC500W, 'VC-500W')
    assert drivers.resolve('simulation', 'VC-500W') == (drivers.VC500W, 'VC-500W')
    assert drivers.resolve('tcp://192.0.2.1', 'QL-810W') == (drivers.BROTHER, 'QL-810W')
    # Unknown models are left to the Brother driver
    assert drivers.resolve('file:///dev/usb/lp0', 'QL-9999') == (drivers.BROTHER, 'QL-9999')
    assert drivers.for_label('99x99') is None


def test_a_driver_states_what_it_does_not_do():
    driver = Driver()
    assert driver.model_of('bt://AA:BB:CC:DD:EE:FF/2') is None
    assert driver.announced({'address': '192.0.2.1', 'port': 631, 'service': '_ipp._tcp.local', 'txt': {}}) is None
    assert driver.resolution_problem('Any', True) == ''
    for call in (lambda: driver.fits(None, 'Any'), lambda: driver.dpi(False), lambda: driver.status('bt://x', 'Any'),
                 lambda: driver.render(None, []), lambda: driver.send(None, None), lambda: driver.network_device('x')):
        with pytest.raises(NotImplementedError):
            call()


@pytest.mark.parametrize('device, key', [
    ('file:///dev/usb/lp0', '/dev/usb/lp0'),
    ('/dev/usb/lp0', '/dev/usb/lp0'),
    ('TCP://Printer.local', 'tcp://printer.local:9100'),
    ('tcp://192.0.2.1:9100', 'tcp://192.0.2.1:9100'),
    ('vc500w://192.0.2.1', 'vc500w://192.0.2.1:9100'),
    ('usb://0x04F9:0x2042', 'usb://0x04f9:0x2042'),
    # One Bluetooth printer whatever channel names it
    ('bt://AA:BB:CC:DD:EE:FF/2', 'bt://aa:bb:cc:dd:ee:ff'),
    ('bt://AA:BB:CC:DD:EE:FF', 'bt://aa:bb:cc:dd:ee:ff'),
    ('bt://aa:bb:cc:dd:ee:ff/1', 'bt://aa:bb:cc:dd:ee:ff'),
])
def test_every_spelling_of_a_device_has_one_lock(device, key):
    assert devices.lock_key(device) == key


def test_job_is_written_to_port_9100_of_a_network_printer():
    server = socket.create_server(('127.0.0.1', 0))
    received = []

    def accept():
        connection, _ = server.accept()
        with connection:
            data = b''
            while chunk := connection.recv(65536):
                data += chunk
            received.append(data)
    thread = threading.Thread(target=accept)
    thread.start()
    job = bytes(range(256)) * 1000
    try:
        network.send(job, f'tcp://127.0.0.1:{server.getsockname()[1]}')
        thread.join(5)
    finally:
        server.close()
    assert received == [job]
