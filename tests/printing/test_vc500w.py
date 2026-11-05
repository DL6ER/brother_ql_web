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
from PIL import Image, JpegImagePlugin

from app import create_app
from app.labeldesigner import printer, vc500w
from app.labeldesigner.label import LabelOrientation
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


class FakeVC500W:
    """Answers the commands of the label designer, on localhost or, with
    usb=True, at 'path' like a printer on USB. 'configs' and 'statuses' are
    the answers to /config.xml and /status.xml one after the other, the last
    one again and again. Received print commands and images are kept. With
    'lose', the first bytes of each image are lost like on USB."""

    def __init__(self, usb=False):
        self.configs = [CONFIG]
        self.statuses = [status_xml()]
        self.refuse_print = ''
        self.lose = 0
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
            threading.Thread(target=self._handle, args=(_Terminal(self._master),), daemon=True).start()
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
                status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
                connection.sendall(answer(status))
            elif '<print>' in command:
                if self.refuse_print:
                    connection.sendall(answer(code=1, comment=self.refuse_print))
                    continue
                connection.sendall(answer(comment='ready to receive'))
                self.ready_at.append(time.monotonic())
                size = int(re.search(r'<datasize>(\d+)</datasize>', command).group(1))
                while not buffer:
                    buffer += connection.recv(65536)
                self.data_at.append(time.monotonic())
                lost = self.lose
                while len(buffer) < size + lost:
                    data = connection.recv(65536)
                    if not data:
                        return
                    buffer += data
                self.images.append(buffer[lost:size + lost])
                buffer = buffer[size + lost:]
                connection.sendall(answer(comment='print data received'))

    def close(self):
        if hasattr(self, 'server'):
            self.server.close()
        elif self._master >= 0:
            os.close(self._slave)
            os.close(self._master)
            self._master = -1


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
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', 'quality', 'full', fake.port) == vc500w.NO_CASSETTE
    assert not [c for c in fake.commands if '<print>' in c]


def test_print_waits_until_the_printer_is_idle(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'START_TIMEOUT', 5)
    # Idle from the label before, then busy with this one
    fake.statuses = [status_xml('IDLE', 'SUCCESS'), status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PROCESSING'),
                     status_xml('BUSY', 'PRINTING', line=16), status_xml('IDLE', 'SUCCESS')]
    stages = []
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8 jpeg data', 'fast', 'half', fake.port,
                             lambda stage, line: stages.append((stage, line))) == ''
    assert fake.images == [b'\xff\xd8 jpeg data']
    assert stages == [('Processing', 0), ('Printing', 16)]
    command = next(c for c in fake.commands if '<print>' in c)
    for part in ('<mode>color</mode>', '<lpi>264</lpi>', '<datasize>12</datasize>', '<cutmode>half</cutmode>', '<autofit>1</autofit>'):
        assert part in command


def test_print_waits_while_the_cassette_is_read(fake):
    # As the printer reported a cassette put in
    fake.statuses = [no_cassette_status('BUSY', 'INITIALIZE'), status_xml('BUSY', 'LOADING PAPER', remain='153.00'),
                     status_xml('IDLE', 'READY FOR PRINT', remain='153.00'), status_xml('BUSY', 'PRINTING'),
                     status_xml('IDLE', 'SUCCESS')]
    stages = []
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port,
                             progress=lambda stage, line: stages.append(stage)) == ''
    assert stages == ['Reading the cassette', 'Feeding the tape', 'Printing']
    assert len(fake.images) == 1


def test_printer_that_stays_busy(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'BUSY_TIMEOUT', 0.1)
    fake.statuses = [status_xml('BUSY', 'PRINTING')]
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == vc500w.BUSY
    assert not [c for c in fake.commands if '<print>' in c]


def test_image_follows_ready_to_receive_after_a_pause(fake):
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == ''
    assert fake.data_at[0] - fake.ready_at[0] >= vc500w.DATA_DELAY


def test_lost_data_is_filled_up(fake):
    # The printer waits for the bytes it lost, zeros make up for them
    fake.lose = 700
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8' * 100, port=fake.port) == vc500w.DAMAGED
    assert fake.images[0][-200:] == bytes(200)


def test_printer_that_takes_no_data(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'DATA_TIMEOUT', 0.1)
    monkeypatch.setattr(vc500w, 'FILL_LIMIT', 1024)
    fake.lose = 4096
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == vc500w.NO_ANSWER


def test_label_still_in_the_printer_is_waited_for(fake):
    # The label before is still in the printer: it drops this one, it is
    # sent again once the label is taken out
    fake.statuses = [status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PROCESSING'),
                     status_xml('ERROR', 'SUCCESS', 'EJECT JAM'), status_xml('ERROR', 'SUCCESS', 'EJECT JAM'),
                     status_xml('IDLE', 'SUCCESS'), status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PRINTING'),
                     status_xml('IDLE', 'SUCCESS')]
    stages = []
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port,
                             progress=lambda stage, line: stages.append(stage)) == ''
    assert fake.images == [b'\xff\xd8', b'\xff\xd8']
    assert stages == ['Processing', vc500w.TAKE_OUT, 'Printing']


def test_label_in_the_printer_before_the_job(fake):
    fake.statuses = [status_xml('ERROR', 'SUCCESS', 'EJECT JAM'), status_xml('IDLE', 'SUCCESS'),
                     status_xml('BUSY', 'PRINTING'), status_xml('IDLE', 'SUCCESS')]
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == ''
    assert len(fake.images) == 1


def test_label_that_is_not_taken_out(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'TAKE_OUT_TIMEOUT', 0.1)
    fake.statuses = [status_xml('ERROR', 'SUCCESS', 'EJECT JAM')]
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == vc500w.TAKE_OUT
    assert fake.images == []


def test_printer_without_progress(fake, monkeypatch):
    monkeypatch.setattr(vc500w, 'STALL_TIMEOUT', 0.1)
    fake.statuses = [status_xml('IDLE', 'SUCCESS'), status_xml('BUSY', 'PRINTING', line=32)]
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == vc500w.UNFINISHED


def test_long_label_is_not_given_up(fake, monkeypatch):
    # Only a printer that stops making progress fails the job
    monkeypatch.setattr(vc500w, 'STALL_TIMEOUT', 0.2)
    fake.statuses = ([status_xml('IDLE', 'SUCCESS')] + [status_xml('BUSY', 'PRINTING', line=line) for line in range(0, 640, 16)]
                     + [status_xml('IDLE', 'SUCCESS')])
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == ''


def test_printer_that_never_reports_busy(fake):
    # A label it finished between two questions: done after START_TIMEOUT
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == ''
    assert sum('/status.xml' in c for c in fake.commands) > 1


def test_print_error_is_reported(fake):
    fake.statuses = [status_xml(), status_xml('BUSY', 'PRINTING'), status_xml('ERROR', 'PRINTING', 'COVER OPEN')]
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port) == 'COVER OPEN'


def test_refused_command(fake):
    fake.refuse_print = 'busy'
    with pytest.raises(vc500w.VC500WError, match='The printer refused the command: busy'):
        vc500w.print_jpeg(fake.target, b'\xff\xd8', port=fake.port)


def test_stale_answer_on_usb_is_discarded(monkeypatch):
    printer_ = FakeVC500W(usb=True)
    try:
        # An answer nobody read before
        os.write(printer_._master, answer(status_xml('BUSY', 'PRINTING')))
        time.sleep(0.05)
        assert vc500w.get_state(printer_.path)[1]['state'] == 'IDLE'
    finally:
        printer_.close()


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


def test_standard_label_comes_out_top_first():
    # 25 mm tape: 290 dots printable across, 327 across what it prints. Along
    # the tape 600 dots at 320 dpi are 587 lines at 313 lpi.
    jpeg = Image.open(io.BytesIO(printer._vc500w_jpeg(_Label(_image(290, 600), LabelOrientation.STANDARD), 'vc25')))
    assert JpegImagePlugin.get_sampling(jpeg) == 2
    assert jpeg.height == 327
    # Along the tape with white at both ends, the top on the right, which
    # comes out first, the left edge at the top of the tape
    assert jpeg.width == 587 + 2 * printer.VC500W_END_MARGIN
    rgb = jpeg.convert('RGB')
    middle = jpeg.height // 2
    assert rgb.getpixel((2, middle)) == (255, 255, 255)
    r, g, b = rgb.getpixel((jpeg.width - printer.VC500W_END_MARGIN - 4, middle))
    assert r > 200 and g < 60 and b < 60
    r, g, b = rgb.getpixel((jpeg.width // 2, (327 - 290) // 2 + 4))
    assert b > 200 and r < 60 and g < 60


def test_rotated_label_is_sent_as_drawn():
    jpeg = Image.open(io.BytesIO(printer._vc500w_jpeg(_Label(_image(600, 290), LabelOrientation.ROTATED), 'vc25')))
    assert jpeg.size == (587 + 2 * printer.VC500W_END_MARGIN, 327)
    r, g, b = jpeg.convert('RGB').getpixel((300, (327 - 290) // 2 + 4))
    assert r > 200 and g < 60 and b < 60


@pytest.mark.parametrize('orientation', ['standard', 'rotated'])
def test_labels_on_color_tape_are_as_long_as_wide(tmp_path, orientation):
    # As printed: the printer would turn a shorter one, the preview shows
    # the content in the middle of it
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'Hi', 'size': '30', 'color': '#c00000'}])
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': 'vc25', 'print_type': 'text', 'orientation': orientation, 'text': text})
    image = Image.open(io.BytesIO(response.data)).convert('RGB')
    assert image.size == (290, 290)
    red = [(x, y) for y in range(image.height) for x in range(image.width) if image.getpixel((x, y))[0] > 150 > image.getpixel((x, y))[1]]
    along = [y for _, y in red] if orientation == 'standard' else [x for x, _ in red]
    assert abs(min(along) - (290 - 1 - max(along))) < 12
    # Longer content makes it longer
    text = json.dumps([{'font': 'DejaVu Sans,Book', 'text': 'A long text along the tape', 'size': '30'}])
    response = make_client(tmp_path).post('/labeldesigner/api/preview', data={
        'label_size': 'vc25', 'print_type': 'text', 'orientation': 'rotated', 'text': text})
    assert Image.open(io.BytesIO(response.data)).width > 290


def test_colors_are_saturated_for_the_paper():
    image = Image.new('RGB', (600, 290), 'white')
    image.paste((192, 0, 0), (0, 0, 300, 290))
    image.paste((128, 128, 128), (300, 0, 600, 290))
    jpeg = Image.open(io.BytesIO(printer._vc500w_jpeg(_Label(image, LabelOrientation.ROTATED), 'vc25'))).convert('RGB')
    # Dark red prints as bright red, gray and white stay
    r, g, b = jpeg.getpixel((150, 164))
    assert r > 245 and g < 15 and b < 15
    r, g, b = jpeg.getpixel((450, 164))
    assert max(r, g, b) - min(r, g, b) < 6
    assert jpeg.getpixel((5, 5)) == (255, 255, 255)


def test_short_label_is_made_wider_than_high():
    # The printer turns an image that is not wider than high, also a square one
    jpeg = Image.open(io.BytesIO(printer._vc500w_jpeg(_Label(_image(290, 100), LabelOrientation.STANDARD), 'vc25')))
    assert jpeg.size == (328, 327)
    jpeg = Image.open(io.BytesIO(printer._vc500w_jpeg(_Label(_image(290, 290), LabelOrientation.STANDARD), 'vc25')))
    assert jpeg.size == (328, 327)


@pytest.mark.parametrize('label_size, width_inches, lpi', [('vc9', 0.413, 313), ('vc12', 0.535, 313), ('vc19', 0.779, 313),
                                                           ('vc25', 1.022, 313), ('vc50', 2.0, 308.5)])
def test_labels_print_at_their_length(label_size, width_inches, lpi):
    # The printer prints a JPEG as high as width_inches of the cassette at 320
    # dpi unscaled, at most its 640 dots, and its lines at the lpi measured on
    # the tape: 2 inches of label, a 60 mm frame measured 60.0 mm on each
    jpeg = Image.open(io.BytesIO(printer._vc500w_jpeg(_Label(_image(640, 80), LabelOrientation.ROTATED), label_size)))
    assert jpeg.height == round(width_inches * 320) <= 640
    assert (jpeg.width - 2 * printer.VC500W_END_MARGIN) / lpi == pytest.approx(2, abs=1 / lpi)


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


def test_color_label_is_printed_as_jpeg(client, fake):
    text = [{'font': 'DejaVu Sans,Book', 'text': 'Color', 'size': '60', 'color': '#1e90ff'}]
    response = client.post('/labeldesigner/api/print', data={
        'label_size': 'vc25', 'model': 'VC-500W', 'print_type': 'text', 'text': json.dumps(text),
        'print_count': '2', 'cut_once': '1', 'cut_mode': 'full'})
    assert response.get_json() == {'success': True}
    assert len(fake.images) == 2
    image = Image.open(io.BytesIO(fake.images[0]))
    assert image.format == 'JPEG' and image.mode == 'RGB' and JpegImagePlugin.get_sampling(image) == 2
    # Across what it prints on 25 mm tape, the text in its color
    assert image.height == 327 and image.width >= 327
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
    # In color whatever image mode is asked, across what it prints
    jpeg = Image.open(io.BytesIO(fake.images[0]))
    assert jpeg.height == 327
    r, g, b = jpeg.convert('RGB').getpixel((jpeg.width // 2, jpeg.height // 2))
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
    assert vc500w.print_jpeg(fake.target, b'\xff\xd8', 'quality', 'none', fake.port) == ''
    command = next(c for c in fake.commands if '<print>' in c)
    assert '<mode>vivid</mode>\n<speed>0</speed>\n<lpi>317</lpi>' in command and '<cutmode>none</cutmode>' in command
