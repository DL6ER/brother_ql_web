import errno
import os
import threading
import time

import pytest
from PIL import Image

from app.labeldesigner import printer
from app.labeldesigner.enums import LabelContent, LabelOrientation, LabelType
from app.labeldesigner.drivers import devices
from app.labeldesigner.drivers.brother import backends
from app.labeldesigner.drivers.brother import network
from app.labeldesigner.drivers.brother import raster
from app.labeldesigner.drivers.brother import usb
from tests.helpers import EXAMPLE_FORMDATA, make_client

DEVICE = 'file:///dev/usb/lp0'


def _status_packet(status: int, phase: int = 0) -> bytes:
    """32 byte status message as sent by Brother QL printers"""
    packet = bytearray(32)
    packet[0:3] = b'\x80\x20\x42'
    packet[10], packet[11], packet[18], packet[19] = 62, 0x0A, status, phase
    return bytes(packet)


class ExclusiveUsbPrinter:
    """Fake printer device. Like the kernel usblp driver, it refuses a second
    open handle with EBUSY."""
    in_use = threading.Lock()
    print_seconds = 1.0
    status_seconds = 0.0

    def __init__(self, device_specifier):
        if not ExclusiveUsbPrinter.in_use.acquire(blocking=False):
            raise OSError(errno.EBUSY, os.strerror(errno.EBUSY), device_specifier)
        self.is_open = True
        self.responses = []

    def write(self, data):
        if data == b'\x1b\x69\x53':  # Status information request
            time.sleep(self.status_seconds)
            self.responses.append(_status_packet(0x00))
        else:
            time.sleep(self.print_seconds)
            self.responses += [_status_packet(0x01), _status_packet(0x06, 0x00)]

    def read(self, length=32):
        return self.responses.pop(0) if self.responses else b''

    def dispose(self):
        if getattr(self, 'is_open', False):
            self.is_open = False
            ExclusiveUsbPrinter.in_use.release()

    __del__ = dispose


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(backends, 'KernelPrinter', ExclusiveUsbPrinter)
    monkeypatch.setattr(devices, 'status_cache', {})
    # Tests holding the device lock should not wait for the status long
    monkeypatch.setattr(devices, 'STATUS_LOCK_TIMEOUT', 0.2)
    client = make_client(tmp_path)
    client.application.config['PRINTER_PRINTER'] = DEVICE
    return client


def test_status_query_during_print(client):
    result = {}

    def print_label():
        result['print'] = client.application.test_client().post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA).get_json()

    thread = threading.Thread(target=print_label)
    thread.start()
    time.sleep(0.3)
    status = client.get('/labeldesigner/api/printer_status').get_json()
    thread.join()
    assert status['errors'] == []
    assert result['print'] == {'success': True}


def test_print_during_status_query(client, monkeypatch):
    monkeypatch.setattr(ExclusiveUsbPrinter, 'status_seconds', 0.5)
    monkeypatch.setattr(ExclusiveUsbPrinter, 'print_seconds', 0.1)
    thread = threading.Thread(target=lambda: client.application.test_client().get('/labeldesigner/api/printer_status'))
    thread.start()
    time.sleep(0.1)
    response = client.post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA)
    thread.join()
    assert response.get_json() == {'success': True}


def test_device_lock_is_exclusive():
    with devices.device_lock(DEVICE) as first:
        assert first
        with devices.device_lock(DEVICE, timeout=0) as second:
            assert not second
    with devices.device_lock(DEVICE, timeout=0) as again:
        assert again


@pytest.mark.parametrize('held, requested', [
    ('/dev/usb/lp0', 'file:///dev/usb/lp0'),
    ('tcp://Printer.local', 'tcp://printer.local:9100'),
    ('usb://0x04F9:0x209B', 'usb://0x04f9:0x209b'),
])
def test_device_lock_covers_all_spellings(held, requested):
    with devices.device_lock(held):
        with devices.device_lock(requested, timeout=0) as acquired:
            assert not acquired


def test_device_lock_uses_private_directory(tmp_path):
    with devices.device_lock(DEVICE):
        lock_dir = tmp_path / f'labelburg-{os.getuid()}'
        assert lock_dir.is_dir()
        assert lock_dir.stat().st_mode & 0o777 == 0o700


def test_device_lock_refuses_shared_directory(tmp_path):
    # A directory other users can write to is not used for the lock files
    lock_dir = tmp_path / f'labelburg-{os.getuid()}'
    lock_dir.mkdir(mode=0o777)
    lock_dir.chmod(0o777)
    with devices.device_lock(DEVICE) as acquired:
        assert acquired
        assert list(lock_dir.iterdir()) == []


def test_print_gives_up_when_printer_stays_busy(client, monkeypatch):
    monkeypatch.setattr(devices, 'DEVICE_LOCK_TIMEOUT', 0.2)
    with devices.device_lock(DEVICE):
        response = client.post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA)
    assert response.status_code == 503
    assert response.get_json()['message'] == 'Printer is busy with another print job, please try again'


def test_failed_status_query_releases_device(client, monkeypatch):
    def broken_read(self, length=32):
        raise OSError('read failed')
    monkeypatch.setattr(ExclusiveUsbPrinter, 'read', broken_read)
    try:
        usb.query_status(DEVICE)
    except OSError:
        # The traceback still references the printer, the device must be closed anyway
        assert not ExclusiveUsbPrinter.in_use.locked()
    else:
        pytest.fail('query_status() did not raise')


def test_status_during_print_keeps_configured_model(client):
    with devices.device_lock(DEVICE):
        status = client.get('/labeldesigner/api/printer_status').get_json()
    assert status['model'] == client.application.config['PRINTER_MODEL']
    assert status['red_support'] is True
    assert status['media_width'] is None


def test_lock_files_are_bounded(tmp_path):
    for i in range(300):
        with devices.device_lock(f'tcp://printer-{i}.example', timeout=0):
            pass
    assert len(list((tmp_path / f'labelburg-{os.getuid()}').iterdir())) <= 300
    # However many devices requests name, there are at most 4096 lock files
    assert len({devices._lock_slot(f'tcp://printer-{i}.example') for i in range(50000)}) <= 4096


def test_device_lock_without_flock_support(monkeypatch):
    def no_flock(fd, operation):
        raise OSError(errno.ENOLCK, os.strerror(errno.ENOLCK))
    monkeypatch.setattr(devices.fcntl, 'flock', no_flock)
    with devices.device_lock(DEVICE) as acquired:
        assert acquired
        with devices.device_lock(DEVICE, timeout=0) as second:
            assert not second


def test_status_during_print_does_not_show_old_state(client):
    devices.status_cache[devices.lock_key(DEVICE)] = {
        'model': 'QL-800', 'media_width': 62, 'media_length': 0,
        'errors': ['No media when printing'], 'status_type': 'Error occurred', 'phase_type': 'Waiting to receive',
    }
    with devices.device_lock(DEVICE):
        status = client.get('/labeldesigner/api/printer_status').get_json()
    assert status['errors'] == []
    assert status['status_type'] == 'Printing'
    assert status['media_width'] == 62


def test_overlapping_status_queries(client, monkeypatch):
    monkeypatch.setattr(devices, 'STATUS_LOCK_TIMEOUT', 2.5)
    monkeypatch.setattr(ExclusiveUsbPrinter, 'status_seconds', 0.5)
    result = {}
    thread = threading.Thread(target=lambda: result.update(first=client.application.test_client().get('/labeldesigner/api/printer_status').get_json()))
    thread.start()
    time.sleep(0.1)
    second = client.get('/labeldesigner/api/printer_status').get_json()
    thread.join()
    # Neither query is mistaken for a running print job
    assert result['first']['status_type'] == second['status_type'] == 'Reply to status request'


def test_busy_printer_is_scanned_again(client, monkeypatch):
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path == '/dev/usb/lp0' or real_exists(path))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'

    def lp0(status):
        return next(p for p in status['printers'] if p['path'] == DEVICE)

    with devices.device_lock(DEVICE):
        assert lp0(client.get('/labeldesigner/api/printer_status').get_json())['status_type'] == 'Printing'
    # Right after the job the printer is queried again instead of the cached busy state
    assert lp0(client.get('/labeldesigner/api/printer_status').get_json())['status_type'] == 'Reply to status request'


def test_label_offset_corrections():
    from app.labeldesigner.drivers.brother.labels import BY_ID
    # Measured on a QL-1100: -6 centers the 103 x 164 mm die-cut label
    assert BY_ID['103x164'].offset_r == -6
    # The others as in brother_ql's label table
    assert BY_ID['104'].offset_r == -8
    assert BY_ID['102x152'].offset_r == 12


def test_printer_without_status_answer_stays_listed(client, monkeypatch):
    # A printer that does not answer is shown with its error instead of
    # disappearing, the label designer would otherwise switch to the simulator
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path == '/dev/usb/lp0' or real_exists(path))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'
    devices.status_cache[devices.lock_key(DEVICE)] = {'model': 'QL-1100', 'media_width': 104, 'media_length': 164}

    def no_answer(device):
        raise TimeoutError('No complete status response within 2.0 s')
    monkeypatch.setattr(usb, 'get_status', no_answer)

    status = client.get('/labeldesigner/api/printer_status').get_json()
    lp0 = next(p for p in status['printers'] if p['path'] == DEVICE)
    assert lp0['model'] == 'QL-1100'
    assert lp0['media_width'] == 104
    assert lp0['errors'] == ['The printer does not answer']



@pytest.mark.parametrize('error, reason', [
    # The panel showed e.g. "([Errno 2] No such file or directory:
    # '/dev/usb/lp0')": a printer switched off has no device file
    (FileNotFoundError(errno.ENOENT, 'No such file or directory', '/dev/usb/lp0'), 'The printer is switched off or not connected'),
    # As pyusb raises them
    (OSError(errno.ENODEV, 'No such device (it may have been disconnected)'), 'The printer is switched off or not connected'),
    (PermissionError(errno.EACCES, 'Permission denied', '/dev/usb/lp0'), 'No permission to use the printer'),
    (OSError(errno.EBUSY, 'Resource busy'), 'The printer is in use by another program'),
    (TimeoutError('timed out'), 'The printer does not answer'),
    (ConnectionRefusedError(errno.ECONNREFUSED, 'Connection refused'), 'The printer refuses the connection'),
    (OSError(errno.EHOSTUNREACH, 'No route to host'), 'The printer cannot be reached'),
    # Other errors only in the log, they may show paths of the server
    (ValueError('Unexpected status in /app/instance'), 'The printer does not answer the status query, the log of the server names the reason'),
])
def test_why_a_printer_does_not_answer_is_told_in_plain_words(error, reason):
    assert devices.unanswered(error) == reason


def test_print_error_of_the_printer_is_reported(client, monkeypatch):
    def replace_media(self, data):
        # Error information 2, bit 0: "Replace media error"
        packet = bytearray(_status_packet(0x02))
        packet[9] = 0x01
        self.responses.append(bytes(packet))
    monkeypatch.setattr(ExclusiveUsbPrinter, 'write', replace_media)
    response = client.post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA)
    assert response.status_code == 503
    assert response.get_json()['message'] == 'The printer reports: Replace media error'


@pytest.mark.parametrize('info, message', [
    ({'printer_state': None, 'did_print': False},
     'The printer did not answer after receiving the label, check whether it was printed'),
    ({'printer_state': {'errors': [], 'status_type': 'Phase change', 'phase_type': 'Printing state'}, 'did_print': False},
     'The printer did not confirm that the label was printed (last status: Phase change, Printing state)'),
    ({'printer_state': {'errors': [], 'status_type': 'Printing completed', 'phase_type': 'Printing state'}, 'did_print': True},
     'The label was printed, but the printer did not report that it is ready for the next one'),
])
def test_print_failure_messages(info, message):
    assert usb.print_failure(info) == message


def test_detected_printer_busy_before_first_answer_has_unknown_model(client, monkeypatch):
    # The configured model need not be the model of a detected printer
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path == '/dev/usb/lp0' or real_exists(path))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'
    with devices.device_lock(DEVICE):
        status = client.get('/labeldesigner/api/printer_status').get_json()
    lp0 = next(p for p in status['printers'] if p['path'] == DEVICE)
    assert lp0['status_type'] == 'Printing'
    assert lp0['model'] == 'Unknown'


def test_detected_pt_e550w_prints(client, monkeypatch):
    # It reported the model code brother_ql gave the PT-P750W to no printer,
    # the label designer sent "Unknown" and printing failed without a message
    def ptouch(status, phase=0):
        packet = bytearray(_status_packet(status, phase))
        packet[3], packet[4] = 0x30, 0x66
        packet[10], packet[11], packet[24], packet[25] = 12, 0x01, 0x01, 0x08  # 12 mm laminated, white, black text
        return bytes(packet)

    def answer(self, data):
        self.responses += [ptouch(0x00)] if data == b'\x1b\x69\x53' else [ptouch(0x01), ptouch(0x06)]
    monkeypatch.setattr(ExclusiveUsbPrinter, 'write', answer)
    real_exists = os.path.exists
    monkeypatch.setattr(os.path, 'exists', lambda path: path == '/dev/usb/lp0' or real_exists(path))
    monkeypatch.setattr(printer, '_last_scan_ts', 0)
    monkeypatch.setattr(printer, '_cached_printers', [])
    client.application.config['PRINTER_PRINTER'] = '?'
    status = client.get('/labeldesigner/api/printer_status').get_json()
    lp0 = next(p for p in status['printers'] if p['path'] == DEVICE)
    assert (lp0['model'], lp0['media_width']) == ('PT-E550W', 12)
    data = dict(EXAMPLE_FORMDATA, printer=DEVICE, model=lp0['model'], label_size='pt12')
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.get_json() == {'success': True}


def test_unknown_model_is_refused_with_a_message(client):
    # brother_ql refused it without a message, the label designer showed an
    # empty error
    for model, message in [('Unknown', 'The printer does not report a model this server knows. Name its model in PRINTERS, e.g. QL-700=file:///dev/usb/lp0'),
                           ('QL-9000', 'Unknown printer model')]:
        response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, printer='simulation', model=model))
        assert (response.status_code, response.get_json()['message']) == (400, message)


def test_configured_printers(client, monkeypatch):
    from app.labeldesigner import ipp, routes
    LP1 = 'file:///dev/usb/lp1'
    NET = 'tcp://127.0.0.1'

    def no_answer(*args, **kwargs):
        raise TimeoutError('timed out')
    monkeypatch.setattr(ipp, 'get_printer_attributes', no_answer)
    client.application.config['PRINTERS'] = [('QL-700', DEVICE), ('QL-1100', LP1), ('QL-810W', NET), ('QL-570', 'simulation')]
    status = client.get('/labeldesigner/api/printer_status').get_json()
    assert [(p['model'], p['path']) for p in status['printers']] == \
        [('QL-700', DEVICE), ('QL-1100', LP1), ('QL-810W', NET), ('QL-570', 'simulation')]
    # Network printers are asked over IPP
    assert status['printers'][2]['errors'] == ['The printer does not answer']
    assert status['selected'] == DEVICE
    with client.application.test_request_context():
        # The model of a listed printer is the configured one, unless requested
        assert routes._printer(LP1, None) == (LP1, 'QL-1100')
        assert routes._printer(None, None) == (DEVICE, 'QL-700')
        assert routes._printer(LP1, 'QL-800') == (LP1, 'QL-800')


def test_a_printer_that_does_not_answer_does_not_hold_up_the_others(client, monkeypatch):
    # The .local name of a PT-E550W that was off took 10 s not to resolve,
    # the label designer showed nothing meanwhile
    NET = 'tcp://off.local'
    release, calls = threading.Event(), []

    def hangs(device, model, *args, **kwargs):
        calls.append(device)
        release.wait(10)
        raise OSError('Name or service not known')
    monkeypatch.setattr(network, 'query_network_status', hangs)
    monkeypatch.setattr(printer, 'STATUS_QUERY_WAIT', 0.2)
    client.application.config['PRINTERS'] = [('QL-570', 'simulation'), ('PT-E550W', NET)]
    try:
        for _ in range(2):
            start = time.monotonic()
            status = client.get('/labeldesigner/api/printer_status').get_json()
            assert time.monotonic() - start < 2
            assert status['printers'][0]['status_type'] == 'Simulator'
            # Not told as not answering while it is still being asked
            assert (status['printers'][1]['status_type'], status['printers'][1]['errors']) == (printer.ASKING, [])
        # The query still running is not started again
        assert calls == [NET]
    finally:
        release.set()
    # Once it ended, its answer is told
    time.sleep(0.1)
    status = client.get('/labeldesigner/api/printer_status').get_json()
    assert status['printers'][1]['errors'] == ['The printer does not answer the status query, the log of the server names the reason']


def test_an_answer_after_the_wait_is_told_next_time(client, monkeypatch):
    # A PUQU Q00 took 4 s and more to connect over Bluetooth, longer than the
    # wait: it was told as not answering every time, though it answered
    NET = 'tcp://slow.local'

    def slow(device, model, *args, **kwargs):
        time.sleep(0.3)
        return dict(network.network_printer(device, model), status_type='Reply to status request')
    monkeypatch.setattr(network, 'query_network_status', slow)
    monkeypatch.setattr(printer, 'STATUS_QUERY_WAIT', 0.1)
    client.application.config['PRINTERS'] = [('PT-E550W', NET)]
    first = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert first['status_type'] == printer.ASKING
    time.sleep(0.4)
    second = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert (second['status_type'], second['errors']) == ('Reply to status request', [])
    # While it is asked again, its last answer is told
    third = client.get('/labeldesigner/api/printer_status').get_json()['printers'][0]
    assert third['status_type'] == 'Reply to status request'


def test_send_errors_are_told_without_their_text(client, monkeypatch, caplog):
    # The text of an error may show paths of the server, only the log has it
    for error, message in ((FileNotFoundError(errno.ENOENT, 'No such file or directory', '/dev/usb/lp0'),
                            'Could not send the label to the printer: The printer is switched off or not connected'),
                           (RuntimeError('/app/instance/secret.json is gone'),
                            'Could not send the label to the printer, the log of the server names the reason')):
        def fail(*args, **kwargs):
            raise error
        monkeypatch.setattr(usb, 'send_job', fail)
        caplog.clear()
        response = client.post('/labeldesigner/api/print', data=EXAMPLE_FORMDATA)
        assert (response.status_code, response.get_json()['message']) == (503, message)
        assert any(str(error) in record.getMessage() for record in caplog.records)


def test_status_errors_are_told_without_their_text(client, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise RuntimeError('/app/instance/secret.json is gone')
    monkeypatch.setattr(usb, 'query_status', fail)
    status = printer.get_ptr_status(client.application.config)
    assert status['errors'] == ['The status of the printer could not be read, the log of the server names the reason']
    assert any('/app/instance/secret.json is gone' in record.getMessage() for record in caplog.records)


class _Label:
    """A label that notes when it is generated."""
    label_type = LabelType.ENDLESS_LABEL
    label_orientation = LabelOrientation.STANDARD
    label_content = LabelContent.TEXT_ONLY

    def __init__(self, events, width):
        self.events, self.width = events, width

    def generate(self):
        self.events.append('generate')
        return Image.new('RGB', (self.width, 20), 'white')


@pytest.mark.parametrize('model, label_size, width, expected', [
    # Each label is converted before the next one is generated, so that a
    # long job does not keep all its images
    ('QL-700', '62', 696, ['generate', 'convert 1'] * 3),
    # The labels of a PT printer are one job
    ('PT-E550W', 'pt12', 70, ['generate'] * 3 + ['convert 3']),
])
def test_labels_are_converted_as_they_are_generated(monkeypatch, model, label_size, width, expected):
    events = []
    monkeypatch.setattr(raster, 'convert', lambda job, images, *args, **kwargs: events.append(f'convert {len(images)}'))
    queue = printer.PrinterQueue(model, 'file:///dev/usb/lp0', label_size)
    for _ in range(3):
        queue.add_label_to_queue(_Label(events, width))
    raster.render(queue, [dict(entry, cut=False) for entry in queue._print_queue])
    assert events == expected


def test_cut_count_of_a_ptouch_job_fits_its_byte(monkeypatch):
    # ESC i A counts up to 255 labels, cut_once with more cuts every 255
    counts = []
    monkeypatch.setattr(raster, 'convert', lambda job, images, *args, **kwargs: counts.append(kwargs['cut_every']))
    queue = printer.PrinterQueue('PT-E550W', 'file:///dev/usb/lp0', 'pt12')
    job = {'settings': (0, True, False, False), 'images': [None] * 300, 'cuts': [False] * 300}
    raster._add_job(queue, raster.Raster('PT-E550W'), job)
    assert counts == [255]


def test_copies_on_ql_printers_are_sent_at_once(client, monkeypatch):
    sent = []
    monkeypatch.setattr(usb, 'send_job', lambda data, device, page_lengths: sent.append(len(page_lengths)) or {'did_print': True, 'ready_for_next_job': True})
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, print_count='12', cut_once='1'))
    assert response.get_json()['success'] is True
    assert sent == [12]


def test_simulated_labels_are_saved_in_their_order(tmp_path):
    client = make_client(tmp_path)
    data = dict(EXAMPLE_FORMDATA, printer='simulation', print_count='12')
    assert client.post('/labeldesigner/api/print', data=data).get_json()['success'] is True
    names = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    assert [name[-7:] for name in names] == [f'{i:03d}.png' for i in range(12)]


def test_configured_printers_with_names(client, monkeypatch):
    from app.labeldesigner import routes
    LP1 = 'file:///dev/usb/lp1'
    client.application.config['PRINTERS'] = [('QL-700', DEVICE, 'Kitchen'), ('QL-1100', LP1), ('QL-800', 'simulation', 'Office desk')]
    status = client.get('/labeldesigner/api/printer_status').get_json()
    assert [(p['name'], p['model'], p['path']) for p in status['printers']] == \
        [('Kitchen', 'QL-700', DEVICE), ('', 'QL-1100', LP1), ('Office desk', 'QL-800', 'simulation')]
    with client.application.test_request_context():
        # A request may name the printer instead of its device, in any case
        assert routes._printer('Kitchen', None) == (DEVICE, 'QL-700')
        assert routes._printer('office DESK', None) == ('simulation', 'QL-800')
        assert routes._printer(LP1, None) == (LP1, 'QL-1100')
        with pytest.raises(ValueError, match='Unsupported printer: Garage'):
            routes._printer('Garage', None)
    # Printed on the printer of that name
    data = dict(EXAMPLE_FORMDATA, printer='Office desk')
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.get_json()['success'] is True
    assert len(os.listdir(printer.SIMULATED_LABELS_DIR)) == 1
