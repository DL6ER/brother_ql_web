"""The Brother VC-500W: its protocol against a fake printer that answers like
a real one (an XML <status>, a line break, a NUL byte and the announced
data), on the network and through a pseudo terminal in place of usblp, and
printing and status through the label designer."""
import base64
import io
import json
import os
import re
import socket
import threading
import time
import tty

import pytest
from PIL import Image

from app import create_app
from app.labeldesigner import printer, vc500w
from app.labeldesigner.label import LabelOrientation
from app.labeldesigner.label_names import LABELS
from tests.helpers import make_client

CONFIG = b'''<?xml version="1.0" encoding="UTF-8"?>
<config>
<model_name>Wedge</model_name>
<crossweb_dpi>320</crossweb_dpi>
<maxprintsize>13056000</maxprintsize>
<media_features>
  <cassette_type>1</cassette_type>
  <width_inches>1.022</width_inches>
  <width_inches_media>0.978</width_inches_media>
  <media_length_initial>197</media_length_initial>
</media_features>
</config>
'''
TEXT = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'X', 'size': '40'}])
# What the print flow tests send: two pixels of a row
IMAGE = Image.new('RGB', (2, 1), (255, 0, 0))


def status_xml(state='IDLE', stage='READY FOR PRINT', error='NONE', remain='180.96', line=0):
    # IDLE, BUSY or ERROR, the stage of the last job, the tape left in inches
    return (f'<?xml version="1.0" encoding="UTF-8"?>\n<status>\n<print_state>{state}</print_state>\n'
            f'<print_job_stage>{stage}</print_job_stage>\n<print_job_error>{error}</print_job_error>\n'
            f'<print_line>{line}</print_line>\n<remain>{remain}</remain>\n</status>\n').encode()


def answer(data=b'', code=0, comment=''):
    head = (f'<?xml version="1.0" encoding="UTF-8"?>\n<status>\n<code>{code}</code>\n'
            + (f'<comment>{comment}</comment>\n' if comment else '')
            + (f'<datasize>{len(data)}</datasize>\n' if data else '')
            + '</status>\n').encode()
    return head + (b'\x00' + data if data else b'')


class _Terminal:
    """The printer's end of a pseudo terminal, with the interface of a
    socket."""

    def __init__(self, fd):
        self._fd = fd

    def recv(self, size):
        try:
            return os.read(self._fd, size)
        except OSError:
            return b''

    def sendall(self, data):
        while data:
            data = data[os.write(self._fd, data):]

    def close(self):
        os.close(self._fd)


class FakeVC500W:
    """Answers the commands of the label designer, on localhost or, with
    usb=True, at 'path' like a printer on USB. 'configs' and 'statuses' are
    the answers to /config.xml and /status.xml one after the other, the last
    one again and again. Received print commands and images are kept. With
    'lose', the first bytes of each image are lost like on USB. A string in
    'refuse_print' refuses every print command, a list the next ones. The
    status questions counted in 'unanswered' (from 1) get no answer, as on a
    weak Wi-Fi. The data is confirmed after 'receive_delay' seconds."""

    def __init__(self, usb=False):
        self.configs = [CONFIG]
        self.statuses = [status_xml()]
        self.refuse_print = ''
        self.lose = 0
        self.unanswered = set()
        self.status_reads = 0
        self.receive_delay = 0
        self.commands = []
        self.images = []
        # When each "ready to receive" was sent and the first byte of the
        # image after it arrived
        self.ready_at = []
        self.data_at = []
        if usb:
            self._master, self._slave = os.openpty()
            tty.setraw(self._slave)
            self.path = os.ttyname(self._slave)
            self.target, self.port = self.path, vc500w.PORT
            self._thread = threading.Thread(target=self._handle, args=(_Terminal(self._master),), daemon=True)
            self._thread.start()
        else:
            self.server = socket.create_server(('127.0.0.1', 0))
            self.target, self.port = '127.0.0.1', self.server.getsockname()[1]
            threading.Thread(target=self._serve, daemon=True).start()

    def _serve(self):
        while True:
            try:
                connection, _ = self.server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True).start()

    def _handle(self, connection):
        try:
            self._answer(connection)
        finally:
            connection.close()

    def _answer(self, connection):
        buffer = b''
        while True:
            end = re.search(rb'</(read|print|lock)>', buffer)
            if not end:
                data = connection.recv(65536)
                if not data:
                    return
                buffer += data
                continue
            command, buffer = buffer[:end.end()].decode(), buffer[end.end():]
            self.commands.append(command)
            if '<path>/config.xml</path>' in command:
                config = self.configs.pop(0) if len(self.configs) > 1 else self.configs[0]
                connection.sendall(answer(config))
            elif '<path>/status.xml</path>' in command:
                self.status_reads += 1
                if self.status_reads in self.unanswered:
                    continue
                status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
                connection.sendall(answer(status))
            elif '<print>' in command:
                refusal = self.refuse_print
                if isinstance(refusal, list):
                    refusal = refusal.pop(0) if refusal else ''
                if refusal:
                    connection.sendall(answer(code=1, comment=refusal))
                    continue
                connection.sendall(answer(comment='ready to receive'))
                self.ready_at.append(time.monotonic())
                size = int(re.search(r'<datasize>(\d+)</datasize>', command).group(1))
                while not buffer:
                    data = connection.recv(65536)
                    if not data:
                        return
                    buffer += data
                self.data_at.append(time.monotonic())
                lost = self.lose
                while len(buffer) < size + lost:
                    data = connection.recv(65536)
                    if not data:
                        return
                    buffer += data
                self.images.append(buffer[lost:size + lost])
                buffer = buffer[size + lost:]
                time.sleep(self.receive_delay)
                connection.sendall(answer(comment='print data received'))

    def close(self):
        if hasattr(self, 'server'):
            self.server.close()
        elif self._slave >= 0:
            # That hangs up the pty: its thread stops reading and closes the
            # other end itself, a later fake may get the descriptor only then
            os.close(self._slave)
            self._slave = -1
            self._thread.join(5)


@pytest.fixture(params=['network', 'usb'])
def fake(request, monkeypatch):
    printer_ = FakeVC500W(usb=request.param == 'usb')
    for name, value in (('POLL_INTERVAL', 0.01), ('DATA_DELAY', 0.05), ('FILL_WAIT', 0.05), ('START_TIMEOUT', 0.05)):
        monkeypatch.setattr(vc500w, name, value)
    monkeypatch.setattr(printer, '_status_cache', {})
    yield printer_
    printer_.close()


def test_configuration_and_status(fake):
    config, status = vc500w.get_state(fake.target, fake.port)
    assert config == {'model': 'VC-500W', 'dpi': 320, 'max_print_size': 13056000, 'tape_width_mm': 24.8,
                      'tape_length_mm': 5004}
    assert status == {'state': 'IDLE', 'stage': 'READY FOR PRINT', 'error': 'NONE', 'line': 0,
                      'tape_remaining_mm': 4596}


NO_CASSETTE_CONFIG = re.sub(rb'<media_features>.*</media_features>\n', b'', CONFIG, flags=re.S)


def no_cassette_status(state='IDLE', stage='IDLE'):
    # As the printer reports it: idle, no error, no tape left
    return re.sub(rb'<remain>.*</remain>\n', b'', status_xml(state, stage))


def test_no_cassette():
    config = vc500w.parse_config(vc500w._element(NO_CASSETTE_CONFIG))
    assert (config['tape_width_mm'], config['tape_length_mm']) == (None, None)
    status = vc500w.parse_status(vc500w._element(no_cassette_status()))
    assert status['tape_remaining_mm'] is None
    assert vc500w.ready_error(status) == vc500w.NO_CASSETTE
    # Busy, e.g. reading a cassette just put in
    assert vc500w.ready_error(vc500w.parse_status(vc500w._element(no_cassette_status('BUSY', 'INITIALIZE')))) == ''
    # The cassette button winds the tape back before it unlocks the cassette
    assert vc500w.stage_name('UNLOADING') == 'Winding the tape back'
    assert vc500w.ready_error(vc500w.parse_status(vc500w._element(status_xml()))) == ''


def test_print_without_cassette(fake):
    fake.statuses = [no_cassette_status()]
    assert vc500w.print_image(fake.target, IMAGE, 'quality', 'full', fake.port) == vc500w.NO_CASSETTE
    assert not [c for c in fake.commands if '<print>' in c]


def test_print_waits_until_the_printer_is_idle(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'START_TIMEOUT', 5)
    # Idle from the label before, then busy with this one
    fake.statuses = [status_xml('IDLE', 'SUCCESS'), status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PROCESSING'),
                     status_xml('BUSY', 'PRINTING', line=16), status_xml('IDLE', 'SUCCESS')]
    stages = []
    image = Image.new('RGB', (3, 2), (0, 128, 255))
    assert vc500w.print_image(fake.target, image, 'fast', 'half', fake.port,
                              lambda stage, line: stages.append((stage, line))) == ''
    assert fake.images == [image.tobytes()]
    assert stages == [('Processing', 0), ('Printing', 16)]
    # Raw RGB, three bytes per pixel, as wide as the image across the tape
    command = next(c for c in fake.commands if '<print>' in c)
    for part in ('<mode>color</mode>', '<lpi>264</lpi>', '<width>3</width>\n<height>2</height>',
                 '<dataformat>rawrgb</dataformat>', '<datasize>18</datasize>', '<cutmode>half</cutmode>'):
        assert part in command


def test_print_waits_while_the_cassette_is_read(fake):
    # As the printer reported a cassette put in
    fake.statuses = [no_cassette_status('BUSY', 'INITIALIZE'), status_xml('BUSY', 'LOADING PAPER', remain='153.00'),
                     status_xml('IDLE', 'READY FOR PRINT', remain='153.00'), status_xml('BUSY', 'PRINTING'),
                     status_xml('IDLE', 'SUCCESS')]
    stages = []
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port,
                             progress=lambda stage, line: stages.append(stage)) == ''
    assert stages == ['Reading the cassette', 'Feeding the tape', 'Printing']
    assert len(fake.images) == 1


def test_printer_that_stays_busy(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'BUSY_TIMEOUT', 0.1)
    fake.statuses = [status_xml('BUSY', 'PRINTING')]
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == vc500w.BUSY
    assert not [c for c in fake.commands if '<print>' in c]


def test_image_follows_ready_to_receive_after_a_pause(fake):
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == ''
    assert fake.data_at[0] - fake.ready_at[0] >= vc500w.DATA_DELAY


def test_lost_data_is_filled_up(fake):
    # The printer waits for the bytes it lost, zeros make up for them
    fake.lose = 700
    assert vc500w.print_image(fake.target, Image.new('RGB', (100, 1), (255, 216, 0)), port=fake.port) == vc500w.DAMAGED
    assert fake.images[0][-200:] == bytes(200)


def test_printer_that_takes_no_data(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'DATA_TIMEOUT', 0.1)
    monkeypatch.setattr(vc500w, 'FILL_LIMIT', 1024)
    fake.lose = 4096
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == vc500w.NO_ANSWER


def test_label_still_in_the_printer_is_waited_for(fake):
    # The label before is still in the printer: it drops this one, it is
    # sent again once the label is taken out
    fake.statuses = [status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PROCESSING'),
                     status_xml('ERROR', 'SUCCESS', 'EJECT JAM'), status_xml('ERROR', 'SUCCESS', 'EJECT JAM'),
                     status_xml('IDLE', 'SUCCESS'), status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PRINTING'),
                     status_xml('IDLE', 'SUCCESS')]
    stages = []
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port,
                             progress=lambda stage, line: stages.append(stage)) == ''
    assert fake.images == [IMAGE.tobytes(), IMAGE.tobytes()]
    assert stages == ['Processing', vc500w.TAKE_OUT, 'Printing']


def test_label_in_the_printer_before_the_job(fake):
    fake.statuses = [status_xml('ERROR', 'SUCCESS', 'EJECT JAM'), status_xml('IDLE', 'SUCCESS'),
                     status_xml('BUSY', 'PRINTING'), status_xml('IDLE', 'SUCCESS')]
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == ''
    assert len(fake.images) == 1


def test_label_that_is_not_taken_out(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'TAKE_OUT_TIMEOUT', 0.1)
    fake.statuses = [status_xml('ERROR', 'SUCCESS', 'EJECT JAM')]
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == vc500w.TAKE_OUT
    assert fake.images == []


def test_printer_without_progress(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'STALL_TIMEOUT', 0.1)
    fake.statuses = [status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PRINTING', line=32)]
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == vc500w.UNFINISHED


def test_long_label_is_not_given_up(fake, monkeypatch):
    # Only a printer that stops making progress fails the job
    monkeypatch.setattr(vc500w, 'STALL_TIMEOUT', 0.2)
    fake.statuses = ([status_xml('IDLE', 'SUCCESS')] + [status_xml('BUSY', 'PRINTING', line=line) for line in range(0, 640, 16)]
                     + [status_xml('IDLE', 'SUCCESS')])
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == ''


def test_printer_that_never_reports_busy(fake):
    # A label it finished between two questions: done after START_TIMEOUT
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == ''
    assert sum('/status.xml' in c for c in fake.commands) > 1


def test_print_error_is_reported(fake):
    fake.statuses = [status_xml(), status_xml('BUSY', 'PRINTING'), status_xml('ERROR', 'PRINTING', 'COVER OPEN')]
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == 'COVER OPEN'


def test_refused_command(fake):
    fake.refuse_print = 'busy'
    with pytest.raises(vc500w.VC500WError, match='The printer refused the command: busy'):
        vc500w.print_image(fake.target, IMAGE, port=fake.port)


def test_printer_receiving_another_job_is_waited_for(fake):
    # It refuses the command while another app sends a job, although idle
    fake.refuse_print = [vc500w.PRINTER_IS_BUSY, vc500w.PRINTER_IS_BUSY]
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == ''
    assert fake.images == [IMAGE.tobytes()]
    assert sum('<print>' in c for c in fake.commands) == 3


def test_printer_that_never_gets_the_other_job(fake, monkeypatch):
    # A job whose data stopped coming blocks it until it is switched off
    monkeypatch.setattr(vc500w, 'BUSY_TIMEOUT', 0.1)
    fake.refuse_print = vc500w.PRINTER_IS_BUSY
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == vc500w.BLOCKED
    assert fake.images == []


def test_lost_status_answer_does_not_fail_the_job(fake, monkeypatch):
    # The printer goes on with the label, the next question goes on a new
    # connection
    monkeypatch.setattr(vc500w, 'TIMEOUT', 0.2)
    fake.statuses = [status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PRINTING', line=16),
                     status_xml('BUSY', 'PRINTING', line=32), status_xml('IDLE', 'SUCCESS')]
    fake.unanswered = {2, 3}
    stages = []
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port,
                              progress=lambda stage, line: stages.append((stage, line))) == ''
    assert stages == [('Printing', 16), ('Printing', 32)]


def test_printer_that_stops_answering_during_the_job(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'TIMEOUT', 0.1)
    monkeypatch.setattr(vc500w, 'STALL_TIMEOUT', 0.5)
    fake.unanswered = set(range(2, 1000))
    assert vc500w.print_image(fake.target, IMAGE, port=fake.port) == vc500w.UNFINISHED


def test_label_too_long_for_the_printer(fake):
    # maxprintsize of /config.xml, in bytes of the image
    fake.configs = [CONFIG.replace(b'13056000', b'100')]
    assert vc500w.print_image(fake.target, Image.new('RGB', (34, 1)), port=fake.port) == vc500w.TOO_LONG
    assert not [c for c in fake.commands if '<print>' in c]
    assert vc500w.print_image(fake.target, Image.new('RGB', (33, 1)), port=fake.port) == ''


def test_more_data_gets_more_time(fake, monkeypatch):
    # Over a weak Wi-Fi the printer takes the data slowly
    monkeypatch.setattr(vc500w, 'DATA_TIMEOUT', 0.1)
    monkeypatch.setattr(vc500w, 'DATA_RATE', 1000)
    fake.receive_delay = 0.3
    assert vc500w.print_image(fake.target, Image.new('RGB', (200, 1)), port=fake.port) == ''


def test_stale_answer_on_usb_is_discarded(monkeypatch):
    printer_ = FakeVC500W(usb=True)
    try:
        # An answer nobody read before
        os.write(printer_._master, answer(status_xml('BUSY', 'PRINTING')))
        time.sleep(0.05)
        assert vc500w.get_state(printer_.path)[1]['state'] == 'IDLE'
    finally:
        printer_.close()


def test_closed_fake_leaves_the_next_one_alone(monkeypatch):
    # The next fake on USB may get the same descriptor, a thread of the
    # closed one that reads once more would answer for it
    late = threading.Event()
    recv = _Terminal.recv

    def slow_recv(self, size):
        if late.is_set():
            time.sleep(0.2)
        return recv(self, size)
    monkeypatch.setattr(_Terminal, 'recv', slow_recv)
    closed = FakeVC500W(usb=True)
    closed.statuses = [status_xml('BUSY', 'PRINTING')]
    late.set()
    assert vc500w.get_state(closed.path)[1]['state'] == 'BUSY'
    closed.close()
    assert not [t for t in threading.enumerate() if getattr(t, '_target', None) == closed._handle]
    late.clear()
    following = FakeVC500W(usb=True)
    try:
        assert vc500w.get_state(following.path)[1]['state'] == 'IDLE'
        assert len(closed.commands) == 2
    finally:
        following.close()


def test_invalid_cut_mode():
    with pytest.raises(ValueError, match='^cut_mode must be full, half or none$'):
        printer.PrinterQueue('VC-500W', 'vc500w://192.0.2.1', 'vc25', cut_mode='sideways')


class _Label:
    def __init__(self, image, orientation):
        self.image = image
        self.label_orientation = orientation

    def generate(self):
        return self.image


def _image(width, height):
    """A label with a red top and a blue left edge."""
    image = Image.new('RGB', (width, height), 'white')
    image.paste((255, 0, 0), (0, 0, width, 10))
    image.paste((0, 0, 255), (0, 10, 10, height))
    return image


def _printed(image, orientation, label_size='vc25', mode='quality'):
    return printer._vc500w_image(_Label(image, orientation), label_size, mode)


def _is(pixel, color):
    return all(abs(a - b) < 60 for a, b in zip(pixel, color))


RED, BLUE, WHITE = (255, 0, 0), (0, 0, 255), (255, 255, 255)


def test_standard_label_comes_out_top_first():
    # Rows are the lines along the tape, the first comes out first. 25 mm
    # tape: 313 dots across it, 333 sent. 600 dots at 320 dpi are 594 lines
    # at 317 lpi, and 10 more at each end where the cuts land.
    image = _printed(_image(313, 600), LabelOrientation.STANDARD)
    assert image.size == (333, 614)
    middle = image.width // 2
    for row in (0, 15):
        assert _is(image.getpixel((middle, row)), RED)
    assert _is(image.getpixel((middle, image.height - 1)), WHITE)
    # The left edge reaches beyond the tape at the first pixels of the rows
    for column in (0, 20):
        assert _is(image.getpixel((column, image.height // 2)), BLUE)
    assert _is(image.getpixel((40, image.height // 2)), WHITE)


def test_rotated_label_is_sent_as_drawn():
    # Its top at the first pixels, its end comes out first
    image = _printed(_image(600, 313), LabelOrientation.ROTATED)
    assert image.size == (333, 614)
    assert _is(image.getpixel((5, image.height // 2)), RED)
    assert _is(image.getpixel((image.width // 2, image.height - 1)), BLUE)
    assert _is(image.getpixel((image.width // 2, 0)), WHITE)


def test_edges_are_repeated_beyond_the_tape_and_the_cuts():
    # The printer places a row 3 dots toward its first pixel: 13 dots
    # beyond that edge of the tape, 7 beyond the other, 10 dots each
    # printed beyond the tape
    label = Image.new('RGB', (640, 313), 'white')
    label.paste(RED, (0, 0, 640, 1))
    label.paste(BLUE, (0, 312, 640, 313))
    image = _printed(label, LabelOrientation.ROTATED)
    row = image.height // 2
    assert [image.getpixel((column, row)) for column in (0, 13, 14, 324, 325, 332)] == [RED, RED, WHITE, WHITE, BLUE, BLUE]
    # The cuts take off 0.8 mm of each end: 10 lines in quality, 8 in fast
    label = Image.new('RGB', (640, 313), 'white')
    label.paste((0, 255, 0), (0, 0, 20, 313))
    label.paste((255, 0, 255), (620, 0, 640, 313))
    for mode, lines, ends in (('quality', 634, 10), ('fast', 528, 8)):
        image = _printed(label, LabelOrientation.ROTATED, mode=mode)
        assert image.height == lines + 2 * ends
        assert _is(image.getpixel((166, 0)), (255, 0, 255)) and _is(image.getpixel((166, ends + 15)), (255, 0, 255))
        assert _is(image.getpixel((166, image.height - 1)), (0, 255, 0))


def test_colors_are_saturated_for_the_paper():
    image = Image.new('RGB', (600, 313), 'white')
    image.paste((192, 0, 0), (0, 0, 200, 313))
    image.paste((128, 128, 128), (200, 0, 400, 313))
    printed = _printed(image, LabelOrientation.ROTATED)
    # The white end comes out first, the dark red, which prints as bright
    # red, last; gray stays
    r, g, b = printed.getpixel((166, printed.height - 110))
    assert r > 245 and g < 15 and b < 15
    r, g, b = printed.getpixel((166, printed.height // 2))
    assert max(r, g, b) - min(r, g, b) < 6
    assert printed.getpixel((166, 110)) == WHITE


@pytest.mark.parametrize('label_size, media_inches', [('vc9', 0.367), ('vc12', 0.489), ('vc19', 0.734), ('vc25', 0.978),
                                                      ('vc50', 1.927)])
def test_labels_are_borderless(label_size, media_inches):
    # As wide as the tape (width_inches_media), the colors at their edges go
    # on 20 dots further, at most to the 640 the printer has
    label = next(label for label in LABELS if label.identifier == label_size)
    dots = label.dots_printable[0]
    assert dots == round(media_inches * 320) and label.dots_total[0] == dots + 20 <= 640
    image = _printed(Image.new('RGB', (100, dots), BLUE), LabelOrientation.ROTATED, label_size)
    assert image.width == dots + 20
    for x in (0, image.width // 2, image.width - 1):
        for y in (0, image.height // 2, image.height - 1):
            assert _is(image.getpixel((x, y)), BLUE)


@pytest.mark.parametrize('mode, lpi', [('quality', 317), ('fast', 264)])
@pytest.mark.parametrize('label_size', ['vc9', 'vc25', 'vc50'])
def test_labels_print_at_their_length(label_size, mode, lpi):
    # A line per row at the lines per inch of the mode: 3 inches of label
    # without the ends the cuts take off
    image = _printed(_image(960, 80), LabelOrientation.ROTATED, label_size, mode)
    ends = round(0.8 / 25.4 * lpi)
    assert (image.height - 2 * ends) / lpi == pytest.approx(3, abs=1 / lpi)


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_short_labels_stay_short(tmp_path, orientation):
    # The printer prints any length, the label is as long as its content
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Hi', 'size': '30', 'color': '#c00000'}])
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': 'vc25', 'print_type': 'text', 'orientation': orientation, 'text': text})
    image = Image.open(io.BytesIO(response.data))
    along = image.height if orientation == 'standard' else image.width
    assert along < 313


@pytest.fixture
def client(fake, tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = (f'vc500w://127.0.0.1:{fake.port}' if fake.target == '127.0.0.1'
                                                     else f'file://{fake.path}')
    client.application.config['PRINTER_MODEL'] = 'VC-500W'
    return client


def test_status_through_the_label_designer(client):
    status = client.get('/labeldesigner/api/printer_status').get_json()
    printer_ = status['printers'][0]
    assert printer_['model'] == 'VC-500W'
    # The nominal width of the tape in the cassette, 24.8 mm wide
    assert (printer_['media_width'], printer_['media_length']) == (25, 0)
    assert printer_['color_support'] is True and printer_['errors'] == []
    assert (printer_['tape_length_mm'], printer_['tape_remaining_mm']) == (5004, 4596)
    # The simulator is the configured model, here a VC-500W, too
    assert status['printers'][1]['model'] == 'VC-500W' and status['printers'][1]['color_support'] is True


def test_stage_of_a_running_job(client, fake):
    path = client.application.config['PRINTER_PRINTER']
    key = printer._lock_key(path)
    printer._status_cache[key] = printer._network_printer(path, 'VC-500W')
    printer._vc500w_jobs[key] = {'stage': 'Preheating', 'label': 2, 'labels': 3}
    try:
        with printer.device_lock(path):
            # Without waiting for the printer the job holds
            started = time.monotonic()
            status = client.get('/labeldesigner/api/printer_status').get_json()
            assert time.monotonic() - started < printer.STATUS_LOCK_TIMEOUT / 2
    finally:
        printer._vc500w_jobs.clear()
    assert status['printers'][0]['job'] == {'stage': 'Preheating', 'label': 2, 'labels': 3}
    assert status['printers'][0]['status_type'] == printer.BUSY_STATUS_TYPE


def test_stage_while_another_app_prints(client, fake):
    fake.statuses = [status_xml('BUSY', 'PREHEAT')]
    status = client.get('/labeldesigner/api/printer_status').get_json()
    assert status['printers'][0]['job'] == {'stage': 'Preheating'}


def test_vc500w_among_the_configured_printers(client, fake, monkeypatch):
    monkeypatch.setenv('PRINTERS', 'VC-500W=vc500w://192.0.2.1, QL-800=simulation')
    assert create_app().config['PRINTERS'] == [('VC-500W', 'vc500w://192.0.2.1'), ('QL-800', 'simulation')]
    client.application.config['PRINTERS'] = [('VC-500W', client.application.config['PRINTER_PRINTER']), ('QL-800', 'simulation')]
    status = client.get('/labeldesigner/api/printer_status').get_json()
    vc, simulator = status['printers']
    assert (vc['model'], vc['media_width'], vc['color_support'], vc['tape_remaining_mm']) == ('VC-500W', 25, True, 4596)
    assert (simulator['model'], simulator['color_support']) == ('QL-800', False)


def test_vc500w_on_usb_is_detected_by_its_device_id(tmp_path, monkeypatch):
    printer_ = FakeVC500W(usb=True)
    try:
        ids = tmp_path / 'lp3'
        ids.write_text('MFG:Brother;MDL:VC-500W;CID:Brother_VC-500W;CMD:LABEL;CLS:PRINTER;SN:000000000000000\n')
        monkeypatch.setattr(printer, 'USB_DEVICE_ID', str(tmp_path / '{name}'))
        monkeypatch.setattr(printer, 'USB_PRINTER_DEVICES', [printer_.path])
        monkeypatch.setattr(printer, '_usb_device_id', lambda device: ids.read_text() if printer_.path in device else '')
        monkeypatch.setattr(printer, '_last_scan_ts', 0)
        monkeypatch.setattr(printer, '_status_cache', {})

        def ql_query(*args):
            raise AssertionError('A VC-500W must not get the commands of a QL printer')
        monkeypatch.setattr(printer, 'get_printer', ql_query)
        client = make_client(tmp_path)
        client.application.config['PRINTER_PRINTER'] = '?'
        status = client.get('/labeldesigner/api/printer_status').get_json()
        assert (status['printers'][0]['path'], status['printers'][0]['model']) == (f'file://{printer_.path}', 'VC-500W')
        # Printed as a VC-500W whatever model the request names
        queue = printer.PrinterQueue('QL-800', f'file://{printer_.path}', 'vc25')
        assert queue.model == 'VC-500W'
    finally:
        printer_.close()


def test_device_id_of_a_usb_printer(tmp_path, monkeypatch):
    (tmp_path / 'lp0').write_text('MFG:Brother;MDL:VC-500W;CMD:LABEL;\n')
    (tmp_path / 'lp1').write_text('MFG:Brother;CMD:PT-CBP;MDL:QL-1100;CLS:PRINTER;\n')
    monkeypatch.setattr(printer, 'USB_DEVICE_ID', str(tmp_path / '{name}'))
    assert printer.is_vc500w('file:///dev/usb/lp0') and printer.is_vc500w('/dev/usb/lp0')
    assert not printer.is_vc500w('file:///dev/usb/lp1')
    # No device ID, e.g. without /sys
    assert not printer.is_vc500w('file:///dev/usb/lp2')
    assert printer.is_vc500w('file:///dev/usb/lp2', 'VC-500W') and printer.is_vc500w('vc500w://192.0.2.1')
    assert not printer.is_vc500w('simulation', 'VC-500W')


def _sent(fake):
    """The images the printer received, rows along the tape."""
    sizes = [tuple(int(re.search(f'<{part}>(\\d+)</{part}>', c).group(1)) for part in ('width', 'height'))
             for c in fake.commands if '<print>' in c]
    return [Image.frombytes('RGB', size, data) for size, data in zip(sizes, fake.images)]


def test_color_label_is_printed_as_raw_rgb(client, fake):
    text = [{'font': 'DejaVu Sans,Book', 'text': 'Color', 'size': '60', 'color': '#1e90ff'}]
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'vc25', 'model': 'VC-500W', 'print_type': 'text', 'text': json.dumps(text),
        'print_count': '2', 'cut_once': '1', 'cut_mode': 'full'})
    assert response.get_json() == {'success': True}
    assert len(fake.images) == 2
    image = _sent(fake)[0]
    # Across 25 mm tape and beyond its edges, the text in its color
    assert image.width == 333
    assert any(abs(r - 0x1e) < 40 and abs(g - 0x90) < 40 and b > 200 for r, g, b in image.get_flattened_data())
    # Cut only after the last label, the others stay together on a strip
    cuts = [re.search(r'<cutmode>(\w+)</cutmode>', c).group(1) for c in fake.commands if '<print>' in c]
    assert cuts == ['half', 'full']
    modes = [re.search(r'<mode>(\w+)</mode>', c).group(1) for c in fake.commands if '<print>' in c]
    assert modes == ['vivid', 'vivid']


def _print_commands(fake, part):
    return [re.search(f'<{part}>(\\w+)</{part}>', c).group(1) for c in fake.commands if '<print>' in c]


def test_saved_label_prints_fast(client, fake, tmp_path):
    client.application.config['LABEL_REPOSITORY_DIR'] = str(tmp_path)
    label = {'name': 'Spices.json', 'label_size': 'vc25', 'print_type': 'text', 'text': TEXT, 'fast_printing': 1}
    assert client.post('/labeldesigner/api/repository/save', json=label).status_code == 200
    assert client.post('/labeldesigner/api/repository/print', json={'name': 'Spices.json'}).get_json() == {'success': True}
    # The request goes before what is stored
    assert client.post('/labeldesigner/api/repository/print?fast_printing=0&cut_mode=half',
                       json={'name': 'Spices.json'}).get_json() == {'success': True}
    assert _print_commands(fake, 'mode') == ['color', 'vivid'] and _print_commands(fake, 'cutmode') == ['full', 'half']


def test_webhook_prints_fast_in_color(client, fake):
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = io.BytesIO()
    Image.new('RGB', (300, 200), (30, 144, 255)).save(image, 'PNG')
    payload = {'password': 'secret', 'label_size': 'vc25', 'model': 'VC-500W', 'image_mode': 'black_and_white', 'high_res': 1,
               'fast_printing': 1, 'cut_mode': 'none', 'images': [base64.b64encode(image.getvalue()).decode('ascii')]}
    assert client.post('/labeldesigner/api/webhook/print', json=payload).get_json() == {'success': True, 'count': 1}
    assert _print_commands(fake, 'mode') == ['color'] and _print_commands(fake, 'cutmode') == ['none']
    # In color whatever image mode is asked, across the tape
    image = _sent(fake)[0]
    assert image.width == 333
    r, g, b = image.getpixel((image.width // 2, image.height // 2))
    assert b > 200 and r < 80
    response = client.post('/labeldesigner/api/webhook/print', json=dict(payload, cut_mode='sideways'))
    assert response.status_code == 400 and response.get_json()['message'] == 'cut_mode must be full, half or none'


def test_printer_error_through_the_label_designer(client, fake):
    fake.statuses = [status_xml(), status_xml('BUSY', 'PRINTING'), status_xml('ERROR', 'PRINTING', 'MEDIA_EMPTY')]
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'vc25', 'model': 'VC-500W', 'print_type': 'text', 'text': TEXT})
    assert response.status_code == 503
    assert response.get_json()['message'] == 'The printer reports: Media empty'


def test_no_cassette_through_the_label_designer(client, fake):
    fake.configs, fake.statuses = [NO_CASSETTE_CONFIG], [no_cassette_status()]
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['There is no cassette in the printer'] and printer_['status_type'] == 'Error occurred'
    assert (printer_['media_width'], printer_['tape_remaining_mm']) == (None, None)
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'vc25', 'model': 'VC-500W', 'print_type': 'text', 'text': TEXT})
    assert response.status_code == 503 and response.get_json()['message'] == 'There is no cassette in the printer'
    assert not [c for c in fake.commands if '<print>' in c]


def test_refused_label_through_the_label_designer(client, fake):
    fake.refuse_print = 'please check /dev/tmp/lutgen.out'
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'vc25', 'model': 'VC-500W', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert response.status_code == 503
    assert response.get_json()['message'] == 'The printer refused the label (label 1 of 2), the log of the server names the reason'


def test_printer_gone_through_the_label_designer(client, fake):
    if fake.target == '127.0.0.1':
        with socket.socket() as unused:
            unused.bind(('127.0.0.1', 0))
            client.application.config['PRINTER_PRINTER'] = f'vc500w://127.0.0.1:{unused.getsockname()[1]}'
    else:
        fake.close()
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'vc25', 'model': 'VC-500W', 'print_type': 'text', 'text': TEXT})
    assert response.status_code == 503
    assert response.get_json()['message'].startswith('Could not send the label to the printer')
    assert 'status query' not in response.get_json()['message']


def test_label_left_in_the_printer_through_the_label_designer(client, fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'TAKE_OUT_TIMEOUT', 0.1)
    fake.statuses = [status_xml('ERROR', 'SUCCESS', 'EJECT JAM')]
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'vc25', 'model': 'VC-500W', 'print_type': 'text', 'text': TEXT, 'print_count': '2'})
    assert response.get_json()['message'] == 'Take the printed label out of the printer (label 1 of 2)'
    # The printer keeps reporting it until the label is taken out
    printer_ = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert printer_['errors'] == ['Take the printed label out of the printer']


def test_qr_code_and_border_in_color(tmp_path):
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': 'vc50', 'print_type': 'qrcode', 'print_color': '#00a000', 'border_color': '#0000ff',
        'border_thickness': '4', 'text': TEXT})
    assert response.status_code == 200
    colors = set(Image.open(io.BytesIO(response.data)).convert('RGB').get_flattened_data())
    assert (0, 160, 0) in colors and (0, 0, 255) in colors


def test_barcode_in_color(tmp_path):
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': 'vc25', 'print_type': 'qrcode', 'barcode_type': 'code128', 'code_text': 'VC500W',
        'print_color': '#c00000', 'text': TEXT})
    assert response.status_code == 200
    colors = set(Image.open(io.BytesIO(response.data)).convert('RGB').get_flattened_data())
    assert (192, 0, 0) in colors and (0, 0, 0) not in colors


@pytest.mark.parametrize('settings', [{'print_color': 'blue'}, {'border_color': '#12345'},
                                      {'text': json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'X', 'size': '40', 'color': [255, 0, 0]}])}])
def test_unknown_color_is_refused(tmp_path, settings):
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': 'vc25', 'print_type': 'text', 'text': TEXT, **settings})
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Unknown color'


@pytest.mark.parametrize('label_size, color, expected', [
    ('vc25', '#1e90ff', (0x1e, 0x90, 0xff)),
    ('62red', '#e01010', (255, 0, 0)),
    ('62red', '#1e90ff', (0, 0, 0)),
    ('62', '#e01010', (0, 0, 0)),
])
def test_colors_are_limited_to_the_label(tmp_path, label_size, color, expected):
    text = [{'font': 'DejaVu Sans,Book', 'text': 'XX', 'size': '80', 'color': color}]
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': label_size, 'print_type': 'text', 'text': json.dumps(text)})
    assert response.status_code == 200
    colors = set(Image.open(io.BytesIO(response.data)).convert('RGB').get_flattened_data())
    assert expected in colors


def test_watch_prints_every_change(fake):
    # The cassette is taken out in the last round
    no_cassette = re.sub(rb'<media_features>.*</media_features>\n', b'', CONFIG, flags=re.S)
    fake.configs = [CONFIG, CONFIG, CONFIG, no_cassette]
    fake.statuses = [status_xml(), status_xml(), status_xml('BUSY', 'PRINTING'),
                     status_xml('IDLE', 'SUCCESS', remain='179.31')]
    lines = []
    vc500w.watch(fake.target, fake.port, interval=0, rounds=4, out=lines.append)
    changes = [line.split(' ', 1)[1] for line in lines]
    # The first round shows every value, the later ones only what changed
    assert 'config/model_name: Wedge' in changes and 'config/media_features/width_inches: 1.022' in changes
    assert changes[-9:-4] == ['status/print_state: BUSY', 'status/print_job_stage: PRINTING', 'status/print_state: IDLE',
                              'status/print_job_stage: SUCCESS', 'status/remain: 179.31']
    assert sorted(changes[-4:]) == ['config/media_features/cassette_type is gone', 'config/media_features/media_length_initial is gone',
                                    'config/media_features/width_inches is gone', 'config/media_features/width_inches_media is gone']


def test_watch_of_a_printer_that_does_not_answer():
    # Nothing listens on that port
    with socket.socket() as unused:
        unused.bind(('127.0.0.1', 0))
        port = unused.getsockname()[1]
    lines = []
    vc500w.watch('127.0.0.1', port, interval=0, rounds=1, out=lines.append)
    assert len(lines) == 1 and ' error: ' in lines[0]


def test_quality_is_the_vivid_mode_of_the_printer(fake):
    assert vc500w.print_image(fake.target, IMAGE, 'quality', 'none', fake.port) == ''
    command = next(c for c in fake.commands if '<print>' in c)
    assert '<mode>vivid</mode>\n<speed>0</speed>\n<lpi>317</lpi>' in command and '<cutmode>none</cutmode>' in command
