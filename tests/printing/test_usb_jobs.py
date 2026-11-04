"""Sending a job to a USB printer: long labels, several labels, errors and
printers that do not answer. The clock is a fake one, a job of minutes
takes no time."""

import pytest
from brother_ql.backends import helpers, linux_kernel

from app.labeldesigner import printer
from tests.helpers import EXAMPLE_FORMDATA, make_client

COMPLETED, PHASE_CHANGE, ERROR = 0x01, 0x06, 0x02
PRINTING, WAITING = 0x01, 0x00


def status(kind, phase=WAITING, error=0):
    """A status message of a QL-800 with 62 mm endless labels."""
    packet = bytearray(32)
    packet[0:5] = b'\x80\x20\x42\x34\x38'
    packet[9], packet[10], packet[11], packet[18], packet[19] = error, 62, 0x0A, kind, phase
    return bytes(packet)


def page(printing_seconds=0.0):
    """The messages of a page: printing, then after printing_seconds
    completed and waiting to receive."""
    return [(0, status(PHASE_CHANGE, PRINTING)), (printing_seconds, status(COMPLETED)), (0, status(PHASE_CHANGE, WAITING))]


class Clock:
    def __init__(self):
        self.now = 1000.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds

    time = monotonic


class FakePrinter:
    """Answers a job with messages, each after the given seconds of silence
    once the job was written. writing_seconds: how long the write takes,
    e.g. a label printed while it is received."""

    def __init__(self, clock, messages, writing_seconds=0.0, left_over=b''):
        self.clock, self.messages, self.writing_seconds = clock, list(messages), writing_seconds
        self.unread = left_over
        self.written = b''
        self.due = None
        self.released = False

    def write(self, data):
        self.written += data
        self.clock.sleep(self.writing_seconds)
        self.due = self.clock.now

    def read(self, length=32):
        if not self.unread and self.due is not None and self.messages and self.clock.now >= self.due + self.messages[0][0]:
            delay, message = self.messages.pop(0)
            self.due += delay
            self.unread = message
        data, self.unread = self.unread[:length], self.unread[length:]
        return data

    def dispose(self):
        self.released = True


@pytest.fixture
def clock(monkeypatch):
    clock = Clock()
    monkeypatch.setattr(printer, 'time', clock)
    return clock


def send(monkeypatch, fake, page_lengths, data=b'job'):
    monkeypatch.setattr(printer, 'get_printer', lambda device: fake)
    info = printer.send_job(data, 'file:///dev/usb/lp0', page_lengths)
    assert fake.released
    return info


def test_label_printed_while_it_is_received_for_a_minute(clock, monkeypatch):
    # The printer prints while it receives the label, the write took longer
    # than the 10 s brother_ql waited: it was not confirmed
    fake = FakePrinter(clock, page(), writing_seconds=60)
    info = send(monkeypatch, fake, [9000])
    assert (info['did_print'], info['ready_for_next_job']) == (True, True)


def test_long_label_printed_after_it_was_received(clock, monkeypatch):
    # A printer that prints after receiving: a 2 m label takes 50 s at
    # 40 mm/s, silent meanwhile
    fake = FakePrinter(clock, page(printing_seconds=50))
    start = clock.now
    info = send(monkeypatch, fake, [2000])
    assert (info['did_print'], info['ready_for_next_job']) == (True, True)
    assert clock.now - start == pytest.approx(50, abs=0.1)


def test_silence_is_given_up_after_the_time_a_page_takes(clock, monkeypatch):
    # A 5 cm label printing for a minute: the printer hangs
    fake = FakePrinter(clock, page(printing_seconds=60))
    start = clock.now
    info = send(monkeypatch, fake, [50])
    assert not info['did_print']
    assert clock.now - start == pytest.approx(printer.STATUS_WAIT + 50 / printer.SLOWEST_PRINT_SPEED, abs=0.1)
    assert printer._print_failure(info) == 'The printer did not confirm that the label was printed (last status: Phase change, Printing state)'


def test_printer_that_does_not_answer(clock, monkeypatch):
    fake = FakePrinter(clock, [])
    start = clock.now
    info = send(monkeypatch, fake, [100])
    assert clock.now - start == pytest.approx(printer.STATUS_WAIT + 100 / printer.SLOWEST_PRINT_SPEED, abs=0.1)
    assert printer._print_failure(info) == 'The printer did not answer after receiving the label, check whether it was printed'


def test_error_while_printing(clock, monkeypatch):
    # Error information 2, bit 4: "Cover opened while printing"
    fake = FakePrinter(clock, [(0, status(PHASE_CHANGE, PRINTING)), (5, status(ERROR, error=0x10)), (0, status(COMPLETED))])
    info = send(monkeypatch, fake, [100])
    assert not info['did_print']
    assert printer._print_failure(info).startswith('The printer reports: Cover opened while printing')


def test_every_label_of_a_job_is_confirmed(clock, monkeypatch):
    # brother_ql stopped at the first label, the messages of the others were
    # left for the next job
    fake = FakePrinter(clock, page(20) + page(20) + page(20))
    info = send(monkeypatch, fake, [500, 500, 500])
    assert (info['did_print'], info['ready_for_next_job'], info['confirmed']) == (True, True, 3)
    assert fake.messages == [] and fake.unread == b''


def test_labels_of_a_job_that_were_not_confirmed(clock, monkeypatch):
    fake = FakePrinter(clock, page() + page())
    info = send(monkeypatch, fake, [50, 50, 50])
    assert not info['did_print']
    assert printer._print_failure(info) == 'The printer confirmed 2 of 3 labels, check whether the others were printed'


def test_messages_left_from_before_do_not_confirm_a_job(clock, monkeypatch):
    fake = FakePrinter(clock, [], left_over=status(COMPLETED) + status(PHASE_CHANGE, WAITING))
    info = send(monkeypatch, fake, [50])
    assert not info['did_print']
    assert printer._print_failure(info) == 'The printer did not answer after receiving the label, check whether it was printed'


def test_messages_read_in_pieces_and_with_noise(clock, monkeypatch):
    messages = b''.join(message for _, message in page())
    fake = FakePrinter(clock, [(0, b'\x00\x01' + messages[:20]), (0, messages[20:50]), (0, messages[50:])])
    info = send(monkeypatch, fake, [50])
    assert (info['did_print'], info['ready_for_next_job']) == (True, True)


def test_job_is_written_in_pieces(clock, monkeypatch):
    # pyusb gives up on a single write after 15 s
    writes = []
    fake = FakePrinter(clock, page())
    real_write = fake.write
    fake.write = lambda data: writes.append(len(data)) or real_write(data)
    data = bytes(range(256)) * 200
    send(monkeypatch, fake, [50], data=data)
    assert max(writes) <= printer.WRITE_CHUNK and fake.written == data


def test_long_labels_printed_through_the_api(clock, monkeypatch, tmp_path):
    # Each job is printed while it is received, which takes 30 s: brother_ql
    # did not confirm the first one and dropped the second
    monkeypatch.setattr(helpers, 'time', clock)
    jobs = []

    class Backend(FakePrinter):
        def __init__(self, device_specifier):
            super().__init__(clock, [], writing_seconds=0)
            jobs.append(self)

        def write(self, data):
            super().write(data)
            if self.written.endswith(b'\x1a'):
                # The end of the job: it took as long as printing it
                clock.sleep(30)
                self.due = clock.now
                self.messages = page() * self.written.count(b'\x1biz')
    monkeypatch.setattr(linux_kernel, 'BrotherQLBackendLinuxKernel', Backend)
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = 'file:///dev/usb/lp0'
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, print_count='7'))
    assert response.get_json() == {'success': True}
    assert [job.written.count(b'\x1biz') for job in jobs] == [5, 2]
