"""The connections to Brother raster printers on USB, through the printer
interface of the kernel (/dev/usb/lp<N>) or PyUSB (usb://<vendor>:<product>),
after the backends of brother_ql by Philipp Klaus."""

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
    """A printer through PyUSB, as usb://<vendor>:<product>[/<serial>]."""

    # In ms
    read_timeout = 10.
    write_timeout = 15000.

    def __init__(self, device_specifier: str):
        import usb.core
        import usb.util
        self._usb = usb
        self.dev = None
        vendor_product, _, serial = device_specifier.removeprefix('usb://').partition('/')
        vendor, _, product = vendor_product.partition(':')
        vendor, product = int(vendor, 16), int(product, 16)
        for printer in _usb_printers(usb):
            if printer.idVendor == vendor and printer.idProduct == product or (serial and printer.iSerialNumber == serial):
                self.dev = printer
                break
        if self.dev is None:
            raise ValueError('Device not found')
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
        data = bytes(self.read_dev.read(length))
        if data:
            return data
        time.sleep(self.read_timeout / 1000.)
        return bytes(self.read_dev.read(length))

    def _dispose(self):
        self._usb.util.dispose_resources(self.dev)
        del self.write_dev, self.read_dev
        if self.was_kernel_driver_active:
            self.dev.attach_kernel_driver(0)
        del self.dev


def _usb_printers(usb):
    """The Brother devices with a printer interface (class 7)."""
    def is_printer(device):
        if device.bDeviceClass == 7:
            return True
        return any(usb.util.find_descriptor(config, bInterfaceClass=7) is not None for config in device)
    return usb.core.find(find_all=True, custom_match=is_printer, idVendor=0x04f9)


def open_printer(device_specifier: str) -> Printer:
    """A connection to the printer on USB, usb://... through PyUSB, any other
    device through the printer interface of the kernel."""
    if device_specifier.startswith(('usb://', '0x')):
        return UsbPrinter(device_specifier)
    return KernelPrinter(device_specifier)
