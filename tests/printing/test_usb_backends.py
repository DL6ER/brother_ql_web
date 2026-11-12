"""The connections to USB printers, and the status answers the Brother
driver cannot read."""

import os
import sys
import types

import pytest

from app.labeldesigner.drivers import backends
from app.labeldesigner.drivers.brother.status import StatusError, parse_status


class Endpoint:
    def __init__(self, address, reads=()):
        self.bEndpointAddress, self.reads, self.written = address, list(reads), []

    def read(self, length, timeout=None):
        reply = self.reads.pop(0) if self.reads else b''
        if reply is None:
            raise USBTimeoutError('Operation timed out')
        return reply

    def write(self, data, timeout):
        self.written.append((data, timeout))


class USBError(OSError):
    pass


class USBTimeoutError(USBError):
    pass


class Device:
    def __init__(self, product, reads=(), vendor=0x04F9, serial='A1', device_id='', bound=False):
        self.idVendor, self.idProduct, self.bDeviceClass, self.iSerialNumber = vendor, product, 0, 3
        self.serial, self.device_id, self.bound = serial, device_id, bound
        self.bInterfaceNumber, self.bAlternateSetting = 0, 0
        self.endpoints = [Endpoint(0x81, reads), Endpoint(0x02)]
        self.calls = []

    def __iter__(self):
        return iter([self])

    def is_kernel_driver_active(self, interface):
        return self.bound

    def ctrl_transfer(self, request_type, request, value, index, length, timeout):
        assert (request_type, request) == (0xA1, 0)
        told = self.device_id.encode()
        return (len(told) + 2).to_bytes(2, 'big') + told

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
    usb.core = types.SimpleNamespace(find=lambda find_all, custom_match, idVendor=None: [
        device for device in devices if idVendor in (None, device.idVendor) and custom_match(device)],
        USBError=USBError, USBTimeoutError=USBTimeoutError)

    def find_descriptor(parent, bInterfaceClass=None, custom_match=None):
        if bInterfaceClass is not None:
            return parent if bInterfaceClass == 7 else None
        return next((e for e in parent.endpoints if custom_match(e)), None)
    usb.util = types.SimpleNamespace(
        find_descriptor=find_descriptor, endpoint_direction=lambda address: address & 0x80,
        ENDPOINT_IN=0x80, ENDPOINT_OUT=0x00,
        dispose_resources=lambda device: device.calls.append('dispose'),
        get_string=lambda device, index: device.serial)
    monkeypatch.setitem(sys.modules, 'usb', usb)
    monkeypatch.setitem(sys.modules, 'usb.core', usb.core)
    monkeypatch.setitem(sys.modules, 'usb.util', usb.util)
    monkeypatch.setattr(backends.UsbPrinter, 'read_timeout', 0.)
    return devices


def test_usb_printer_through_pyusb(pyusb):
    other, printer_ = Device(0x2042), Device(0x209C, reads=[b'', b'status'], bound=True)
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
    with pytest.raises(FileNotFoundError, match='Device not found'):
        backends.open_printer('usb://0x04f9:0x209c')


def test_printers_of_the_same_model_by_their_serial_number(pyusb):
    first, second = Device(0x2042, serial='000L9Z111111'), Device(0x2042, serial='000L9Z378866')
    pyusb.extend([first, second])
    # As on the sticker of the printer, or as it tells it over USB
    assert backends.open_printer('usb://0x04f9:0x2042/L9Z378866').dev is second
    assert backends.open_printer('usb://0x04f9:0x2042/000L9Z378866').dev is second
    assert backends.open_printer('usb://0x04f9:0x2042').dev is first
    with pytest.raises(FileNotFoundError):
        backends.open_printer('usb://0x04f9:0x2042/L9Z000000')


def test_printers_of_other_makers(pyusb):
    # An Epson TM-T20II, its reads time out while it has nothing to tell
    epson = Device(0x0e15, vendor=0x04b8, reads=[None, None, b'\x12'])
    pyusb.extend([Device(0x0e15), epson])
    printer = backends.open_printer('usb://0x04b8:0x0e15')
    assert printer.dev is epson
    assert printer.read() == b''
    assert printer.read() == b'\x12'


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


def test_printers_found_that_usblp_does_not_hold(pyusb):
    from app.labeldesigner.drivers import devices
    epson = Device(0x0e15, vendor=0x04b8, serial='544338592994570000',
                   device_id='MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;')
    # On Linux usblp holds it, it is /dev/usb/lp<N>
    held = Device(0x2042, bound=True)
    pyusb.extend([epson, held, Device(0x0081, vendor=0x0a5f, serial='with space')])
    assert backends.found_printers() == [
        ('usb://0x04b8:0x0e15/544338592994570000', 'MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;'),
        ('usb://0x0a5f:0x0081', '')]
    assert backends.device_id('usb://0x04b8:0x0e15') == epson.device_id
    assert devices.device_id_fields(epson.device_id)['MDL'] == 'TM-T20II'
    assert devices.device_id_fields('MANUFACTURER:Zebra Technologies;COMMAND SET:ZPL;MODEL:GK420t;')['CMD'] == 'ZPL'
