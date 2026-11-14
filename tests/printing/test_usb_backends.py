"""The connections to USB printers, and the status answers the Brother
driver cannot read."""

import os
import sys
import types

import pytest

from app.labeldesigner import printer
from app.labeldesigner.drivers import backends, devices, escpos
from app.labeldesigner.drivers.brother import usb as brother_usb
from app.labeldesigner.drivers.brother.status import StatusError, parse_status
from tests.helpers import make_client

# The real one, which the conftest replaces in each test
FOUND_USB_PRINTERS = devices.found_usb_printers


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


@pytest.fixture
def detect(pyusb, tmp_path, monkeypatch):
    """The printers PRINTER_PRINTER '?' lists, PyUSB faked."""
    monkeypatch.setattr(devices, 'found_usb_printers', FOUND_USB_PRINTERS)
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    monkeypatch.setattr(devices, 'status_cache', {})
    monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [])
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = '?'
    return lambda: client.get('/labeldesigner/api/printer_status').get_json()['printers']


@pytest.mark.parametrize('error', [USBError(13, 'Access denied (insufficient permissions)'), USBError(5, 'Input/Output Error')])
def test_printer_pyusb_cannot_open_is_left_out(pyusb, error):
    # usblp holds it as /dev/usb/lp<N>, PyUSB may not open it to ask
    def unopened(interface):
        raise error
    held = Device(0x2042, serial=None)
    held.is_kernel_driver_active = unopened
    pyusb.extend([held, Device(0x209c, serial='C2')])
    assert backends.found_printers() == [('usb://0x04f9:0x209c/C2', '')]


def test_brother_office_printer_is_not_listed(detect, pyusb):
    laser = Device(0x0042, serial='E78123', device_id='MFG:Brother;CMD:PJL,PCL,PCLXL,URF;MDL:HL-L2350DW series;CLS:PRINTER;')
    pyusb.append(laser)
    assert [p['path'] for p in detect()] == ['simulation']
    assert laser.endpoints[1].written == []


def test_vc500w_on_pyusb_is_not_listed(detect, pyusb):
    # Its driver reaches it on the network and through usblp only
    pyusb.append(Device(0x2108, serial='A1B2', device_id='MFG:Brother;MDL:VC-500W;CMD:LABEL;CLS:PRINTER;'))
    assert [p['path'] for p in detect()] == ['simulation']


def test_printer_without_winusb_is_left_out(pyusb, monkeypatch):
    # On Windows libusb cannot open a printer that has a driver of its own
    def get_string(device, index):
        if device.serial is None:
            raise NotImplementedError('Operation not supported or unimplemented on this platform')
        return device.serial
    monkeypatch.setattr(sys.modules['usb.util'], 'get_string', get_string)
    pyusb.extend([Device(0x2042, serial=None), Device(0x209c, serial='C2')])
    assert backends.found_printers() == [('usb://0x04f9:0x209c/C2', '')]


def test_label_printers_on_pyusb_are_listed(detect, pyusb, monkeypatch):
    # A QL-1100 telling its device ID, a QL-700 telling none, a TM-T20II
    monkeypatch.setattr(brother_usb, 'query_status', lambda device, model='Unknown': {'path': device, 'model': 'QL', 'errors': []})
    monkeypatch.setattr(escpos.Escpos, 'status', lambda self, device, model, unconfirmed=False: {'path': device, 'model': model, 'errors': []})
    pyusb.extend([Device(0x20a7, serial='000A1', device_id='MFG:Brother;CMD:PT-CBP;MDL:QL-1100;CLS:PRINTER;'),
                  Device(0x2042, serial='000B2'),
                  Device(0x0e15, vendor=0x04b8, serial='5443', device_id='MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;CLS:PRINTER;')])
    assert [p['path'] for p in detect()] == [
        'usb://0x04f9:0x20a7/000A1', 'usb://0x04f9:0x2042/000B2', 'usb://0x04b8:0x0e15/5443', 'simulation']


def test_device_id_asked_once(pyusb, monkeypatch):
    monkeypatch.setattr(devices, 'found_usb_printers', FOUND_USB_PRINTERS)
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    epson = Device(0x0e15, vendor=0x04b8, serial='5443', device_id='MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;')
    asked = []
    ctrl_transfer = epson.ctrl_transfer
    epson.ctrl_transfer = lambda *args: asked.append(args) or ctrl_transfer(*args)
    pyusb.append(epson)
    # The detection keeps the device ID it read
    assert devices.found_usb_printers() == ['usb://0x04b8:0x0e15/5443']
    assert escpos.DRIVER.model_of('usb://0x04b8:0x0e15/5443') == 'Epson/ESC-POS'
    assert len(asked) == 1
    # Configured without serial number: asked once, then kept
    for _ in range(2):
        assert escpos.DRIVER.model_of('usb://0x04b8:0x0e15') == 'Epson/ESC-POS'
    assert len(asked) == 2


def test_device_ids_kept_once_per_printer(pyusb, monkeypatch):
    monkeypatch.setattr(devices, '_usb_device_ids', {})
    pyusb.append(Device(0x0e15, vendor=0x04b8, serial='5443', device_id='MFG:EPSON;CMD:ESC/POS;MDL:TM-T20II;'))
    for zeros in range(50):
        assert devices.usb_device_id(f"usb://0x04b8:0x0e15/{'0' * zeros}5443").endswith('MDL:TM-T20II;')
    assert devices.usb_device_id('usb://0x04B8:0x0E15/5443').endswith('MDL:TM-T20II;')
    assert len(devices._usb_device_ids) == 1
