"""The connections to printers on USB, through the printer interface of
the kernel (/dev/usb/lp<N>, Linux only) or PyUSB
(usb://<vendor>:<product>[/<serial>], also on macOS, on Windows untested), after the
backends of brother_ql by Philipp Klaus."""

import errno
import logging
import os
import select
import time

logger = logging.getLogger(__name__)


class Printer:
    """A connection to a printer: write, read up to length bytes, b'' when
    there is nothing to read, and dispose."""

    def write(self, data: bytes):
        logger.debug('Writing %d bytes.', len(data))
        self._write(data)

    def read(self, length: int = 32) -> bytes:
        data = self._read(length)
        if data:
            logger.debug('Read %d bytes.', len(data))
        return data

    def dispose(self):
        try:
            self._dispose()
        except Exception:
            pass

    def __del__(self):
        self.dispose()


class KernelPrinter(Printer):
    """A printer through the printer interface of the kernel (usblp)."""

    # A read waits this long for an answer before it tries once more
    read_timeout = 0.01

    def __init__(self, device_specifier: str):
        self.dev = os.open(device_specifier.removeprefix('file://'), os.O_RDWR)

    def _write(self, data: bytes):
        os.write(self.dev, data)

    def _read(self, length: int) -> bytes:
        start = time.time()
        while time.time() - start < self.read_timeout:
            ready, _, _ = select.select([self.dev], [], [], 0)
            if self.dev in ready:
                data = os.read(self.dev, length)
                if data:
                    return data
            time.sleep(0.001)
        return os.read(self.dev, length)

    def _dispose(self):
        os.close(self.dev)


class UsbPrinter(Printer):
    """A printer through PyUSB, as usb://<vendor>:<product>[/<serial>]: the
    serial number tells printers of the same model apart."""

    # In ms
    read_timeout = 10.
    write_timeout = 15000.
    answer_timeout = 100.

    def __init__(self, device_specifier: str):
        import usb.core
        import usb.util
        self._usb = usb
        self.dev = None
        vendor_product, _, serial = device_specifier.removeprefix('usb://').partition('/')
        vendor, _, product = vendor_product.partition(':')
        vendor, product = int(vendor, 16), int(product, 16)
        for printer in _usb_printers(usb, vendor):
            if printer.idProduct == product and (not serial or _same_serial(_serial_number(usb, printer), serial)):
                self.dev = printer
                break
        if self.dev is None:
            raise FileNotFoundError(errno.ENODEV, 'Device not found', device_specifier)
        try:
            assert self.dev.is_kernel_driver_active(0)
            self.dev.detach_kernel_driver(0)
            self.was_kernel_driver_active = True
        except (NotImplementedError, AssertionError):
            self.was_kernel_driver_active = False
        self.dev.set_configuration()
        interface = usb.util.find_descriptor(self.dev.get_active_configuration(), bInterfaceClass=7)
        if interface is None:
            raise ValueError('The device has no printer interface')
        direction = usb.util.endpoint_direction
        self.read_dev = usb.util.find_descriptor(
            interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_IN)
        self.write_dev = usb.util.find_descriptor(
            interface, custom_match=lambda e: direction(e.bEndpointAddress) == usb.util.ENDPOINT_OUT)
        if self.read_dev is None or self.write_dev is None:
            raise ValueError('The printer interface has no endpoints to read and write')

    def _write(self, data: bytes):
        self.write_dev.write(data, int(self.write_timeout))

    def _read(self, length: int) -> bytes:
        data = self._read_once(length)
        if data:
            return data
        time.sleep(self.read_timeout / 1000.)
        return self._read_once(length)

    def _read_once(self, length: int) -> bytes:
        # A printer with nothing to tell may let the read time out instead
        # of answering it empty
        try:
            return bytes(self.read_dev.read(length, int(self.answer_timeout)))
        except self._usb.core.USBTimeoutError:
            return b''

    def _dispose(self):
        self._usb.util.dispose_resources(self.dev)
        del self.write_dev, self.read_dev
        if self.was_kernel_driver_active:
            self.dev.attach_kernel_driver(0)
        del self.dev


def _usb_printers(usb, vendor: int):
    """The devices of a maker with a printer interface (class 7)."""
    def is_printer(device):
        if device.bDeviceClass == 7:
            return True
        return any(usb.util.find_descriptor(config, bInterfaceClass=7) is not None for config in device)
    return usb.core.find(find_all=True, custom_match=is_printer, idVendor=vendor)


def _same_serial(told: str | None, given: str) -> bool:
    """Whether a printer that tells its serial number is the one given. A
    QL-700 tells 000L9Z378866, its sticker says L9Z378866: leading zeros do
    not count."""
    return told is not None and told.lstrip('0') == given.lstrip('0')


def _serial_number(usb, device) -> str | None:
    """The serial number of a device, which USB tells as the index of a
    string, or None when it cannot be read."""
    try:
        return usb.util.get_string(device, device.iSerialNumber)
    except (ValueError, usb.core.USBError):
        return None


def open_printer(device_specifier: str) -> Printer:
    """A connection to the printer on USB, usb://... through PyUSB, any other
    device through the printer interface of the kernel."""
    if device_specifier.startswith(('usb://', '0x')):
        return UsbPrinter(device_specifier)
    return KernelPrinter(device_specifier)
