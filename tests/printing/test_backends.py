"""The connections of the Brother driver to USB printers, and the status
answers it cannot read."""

import os
import sys
import types

import pytest

from app.labeldesigner.drivers.brother import backends
from app.labeldesigner.drivers.brother.status import StatusError, parse_status


class Endpoint:
    def __init__(self, address, reads=()):
        self.bEndpointAddress, self.reads, self.written = address, list(reads), []

    def read(self, length):
        return self.reads.pop(0) if self.reads else b''

    def write(self, data, timeout):
        self.written.append((data, timeout))


class Device:
    def __init__(self, product, reads=()):
        self.idVendor, self.idProduct, self.bDeviceClass, self.iSerialNumber = 0x04F9, product, 0, 3
        self.endpoints = [Endpoint(0x81, reads), Endpoint(0x02)]
        self.calls = []

    def __iter__(self):
        return iter([self])

    def is_kernel_driver_active(self, interface):
        return True

    def detach_kernel_driver(self, interface):
        self.calls.append('detach')

    def attach_kernel_driver(self, interface):
        self.calls.append('attach')

    def set_configuration(self):
        self.calls.append('configure')

    def get_active_configuration(self):
        return self


@pytest.fixture
def pyusb(monkeypatch):
    """A fake PyUSB with the devices of its list: each is its own
    configuration with a printer interface."""
    devices = []
    usb = types.ModuleType('usb')
    usb.core = types.SimpleNamespace(find=lambda find_all, custom_match, idVendor: [
        device for device in devices if device.idVendor == idVendor and custom_match(device)])

    def find_descriptor(parent, bInterfaceClass=None, custom_match=None):
        if bInterfaceClass is not None:
            return parent if bInterfaceClass == 7 else None
        return next((e for e in parent.endpoints if custom_match(e)), None)
    usb.util = types.SimpleNamespace(
        find_descriptor=find_descriptor, endpoint_direction=lambda address: address & 0x80,
        ENDPOINT_IN=0x80, ENDPOINT_OUT=0x00,
        dispose_resources=lambda device: device.calls.append('dispose'))
    monkeypatch.setitem(sys.modules, 'usb', usb)
    monkeypatch.setitem(sys.modules, 'usb.core', usb.core)
    monkeypatch.setitem(sys.modules, 'usb.util', usb.util)
    monkeypatch.setattr(backends.UsbPrinter, 'read_timeout', 0.)
    return devices


def test_usb_printer_through_pyusb(pyusb):
    other, printer_ = Device(0x2042), Device(0x209C, reads=[b'', b'status'])
    pyusb.extend([other, printer_])
    printer = backends.open_printer('usb://0x04f9:0x209c')
    assert isinstance(printer, backends.UsbPrinter) and printer.dev is printer_
    assert printer_.calls == ['detach', 'configure'] and other.calls == []
    printer.write(b'job')
    assert printer_.endpoints[1].written == [(b'job', 15000)]
    # An empty read is tried once more
    assert printer.read() == b'status'
    printer.dispose()
    assert printer_.calls[2:] == ['dispose', 'attach']


def test_usb_printer_that_is_not_connected(pyusb):
    pyusb.append(Device(0x2042))
    with pytest.raises(ValueError, match='Device not found'):
        backends.open_printer('usb://0x04f9:0x209c')


def test_printer_through_the_kernel(tmp_path):
    fifo = tmp_path / 'lp0'
    os.mkfifo(fifo)
    printer = backends.open_printer(f'file://{fifo}')
    assert isinstance(printer, backends.KernelPrinter)
    printer.write(b'answer')
    assert printer.read(32) == b'answer'
    printer.dispose()
    with pytest.raises(OSError):
        os.fstat(printer.dev)


@pytest.mark.parametrize('answer', [b'', b'\x80\x20\x42' + bytes(10), bytes(32),
                                    # Media type 0x55, status type 0x99
                                    b'\x80\x20\x42' + bytes(8) + b'\x55' + bytes(20),
                                    b'\x80\x20\x42' + bytes(8) + b'\x0a' + bytes(6) + b'\x99' + bytes(13)])
def test_answers_that_are_no_status(answer):
    with pytest.raises(StatusError):
        parse_status(answer)
