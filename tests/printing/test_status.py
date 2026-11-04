"""The status of a printer that answers late or only after a reset."""

import pytest


class FakeSlowPrinter:
    """Printer that returns empty reads before answering, like the QL-1100."""

    def __init__(self, empty_reads: int, response: bytes):
        self.empty_reads = empty_reads
        self.response = response
        self.written = b''

    def write(self, data: bytes):
        self.written += data

    def read(self, length: int) -> bytes:
        if self.empty_reads > 0:
            self.empty_reads -= 1
            return b''
        chunk, self.response = self.response[:length], self.response[length:]
        return chunk


class FakeResetPrinter(FakeSlowPrinter):
    """Printer that answers only after "ESC @", like the QL-560 after its
    first status request."""

    def __init__(self, response: bytes):
        super().__init__(empty_reads=0, response=response)

    def read(self, length: int) -> bytes:
        if b'\x1b\x40' not in self.written:
            return b''
        return super().read(length)


def make_status_packet() -> bytes:
    packet = bytearray(32)
    packet[0:3] = b'\x80\x20\x42'  # header
    packet[3] = 0x34  # series code
    packet[4] = 0x38  # model code
    packet[10] = 62  # media width
    packet[11] = 0x0A  # continuous length tape
    return bytes(packet)


class TestPrinterStatus:
    def test_get_status_waits_for_delayed_response(self):
        from app.labeldesigner.printer import get_status
        printer = FakeSlowPrinter(empty_reads=5, response=make_status_packet())
        status = get_status(printer)
        assert printer.written == b'\x1b\x69\x53'
        assert status['media_width'] == 62
        assert status['errors'] == []

    def test_get_status_resets_a_printer_that_does_not_answer(self):
        from app.labeldesigner.printer import get_status
        printer = FakeResetPrinter(make_status_packet())
        status = get_status(printer, timeout=0.1)
        assert printer.written == b'\x1b\x69\x53' + b'\x00' * 200 + b'\x1b\x40\x1b\x69\x53'
        assert status['media_width'] == 62

    def test_get_status_times_out_without_response(self):
        from app.labeldesigner.printer import get_status
        printer = FakeSlowPrinter(empty_reads=10**6, response=b'')
        with pytest.raises(TimeoutError):
            get_status(printer, timeout=0.1)


@pytest.mark.parametrize('code, model', [(0x66, 'PT-E550W'), (0x67, 'PT-P700'), (0x68, 'PT-P750W')])
def test_ptouch_models_are_told_apart(code, model):
    # brother_ql gave the PT-E550W the code of the PT-P750W, a PT-E550W was
    # an unknown printer and a PT-P750W was taken for a PT-E550W
    from app.labeldesigner.printer import get_status
    packet = bytearray(make_status_packet())
    packet[3], packet[4] = 0x30, code
    packet[10], packet[11], packet[24], packet[25] = 12, 0x01, 0x01, 0x08  # 12 mm laminated, white, black text
    assert get_status(FakeSlowPrinter(empty_reads=0, response=bytes(packet)))['model'] == model
