"""Printing through the API: the simulator, copies, limits, printers and
P-touch tape."""

import base64
import io
import json
import os

import pytest
from PIL import Image
from flask.testing import FlaskClient

from tests.helpers import _pdf, _red_square, _upload, EXAMPLE_FORMDATA, make_client, sent_raster


class TestLabelDesignerAPI:
    @pytest.fixture(autouse=True)
    def client(self, tmp_path):
        yield make_client(tmp_path)

    # We cannot test the print functionality without a physical printer, this
    # will go through the simulator and be a no-op.
    def test_print_label_simulator(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        # Set config for printer
        response = client.post('/labeldesigner/api/print', data=data)
        assert response.status_code == 200
        assert response.is_json
        json_response = response.get_json()
        assert 'success' in json_response
        assert json_response['success'] is True

    # Test printing to an invalid printer path
    def test_print_label_invalid_printer(self, client: FlaskClient):
        data = EXAMPLE_FORMDATA.copy()
        data['printer'] = '/dev/usb/lp99'
        response = client.post('/labeldesigner/api/print', data=data)
        # The printer, not the request: it may work later
        assert response.status_code == 503
        assert response.is_json
        json_response = response.get_json()
        assert 'message' in json_response
        # The path is in the log only
        assert json_response['message'] == 'Could not send the label to the printer: The printer is switched off or not connected'
        assert 'success' in json_response
        assert json_response['success'] is False

    def test_printer_status_returns_simulator(self, client: FlaskClient):
        """Check /api/printer_status includes simulator."""
        response = client.get('/labeldesigner/api/printer_status')
        assert response.status_code == 200
        assert response.is_json
        data = response.get_json()
        assert 'printers' in data
        printers = data['printers']
        assert isinstance(printers, list)
        # There must be at least the simulator printer
        assert any(p.get('path') == 'simulation' for p in printers)
        sim = next((p for p in printers if p.get('path') == 'simulation'), None)
        assert sim is not None
        # Simulator should use the configured PRINTER_MODEL
        assert sim.get('model') == client.application.config['PRINTER_MODEL']


def test_print_count_is_limited(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['print_count'] = str(client.application.config['PRINT_MAX_COUNT'] + 1)
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == f"print_count must not exceed {client.application.config['PRINT_MAX_COUNT']}"


def test_print_requires_post(tmp_path):
    client = make_client(tmp_path)
    response = client.get('/labeldesigner/api/print', query_string=EXAMPLE_FORMDATA)
    assert response.status_code == 405


@pytest.mark.parametrize('device', ['file://{target}', '{target}', 'tcp://127.0.0.1:6379', 'tcp://[::1]:6379', 'tcp://[::1]/x'])
def test_print_rejects_unsupported_printer_devices(tmp_path, device):
    client = make_client(tmp_path)
    target = tmp_path / 'important.json'
    target.write_text('{"keep": true}')
    data = EXAMPLE_FORMDATA.copy()
    data['printer'] = device.format(target=target)
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    # Without the path, which is in the log only
    assert response.get_json()['message'] == 'Unsupported printer'
    assert target.read_text() == '{"keep": true}'


def test_printer_device_with_trailing_newline_is_refused(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['printer'] = '/dev/usb/lp0\n'
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'Unsupported printer'


def test_simulator_keeps_only_the_newest_labels(tmp_path, monkeypatch):
    from app.labeldesigner import printer
    monkeypatch.setattr(printer, 'MAX_SIMULATED_LABELS', 2)
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['print_count'] = '3'
    assert client.post('/labeldesigner/api/print', data=data).get_json() == {'success': True}
    assert len(os.listdir(printer.SIMULATED_LABELS_DIR)) == 2


def test_text_label_is_not_loaded_again_for_every_copy(tmp_path, monkeypatch):
    from app.labeldesigner import routes
    calls = []
    original = routes.load_label_images
    monkeypatch.setattr(routes, 'load_label_images', lambda *args, **kwargs: calls.append(args) or original(*args, **kwargs))
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data['print_count'] = '3'
    assert client.post('/labeldesigner/api/print', data=data).get_json() == {'success': True}
    assert len(calls) == 1


def test_label_size_the_printer_cannot_print_is_refused(tmp_path):
    client = make_client(tmp_path)
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': '103x164', 'model': 'QL-800'})
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The label size 103 x 164 mm die-cut (DK-11247 / DK-1247) cannot be printed on a QL-800'

    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image], 'label_size': '103x164', 'model': 'QL-800'})
    assert response.status_code == 400
    assert response.get_json()['message'] == 'The label size 103 x 164 mm die-cut (DK-11247 / DK-1247) cannot be printed on a QL-800'


@pytest.mark.parametrize('model', ['QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W'])
def test_600_dpi_is_refused_for_the_ql1100_series_and_ptouch_printers(tmp_path, model):
    # Brother's command reference of the QL-1100 series lists 300 dpi only
    # (#103), the P-touch printers print at 180 dpi
    client = make_client(tmp_path)
    label_size = 'pt12' if model.startswith('PT-') else '62'
    data = EXAMPLE_FORMDATA.copy()
    data.update({'label_size': label_size, 'model': model, 'high_res': '1'})
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'

    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
    response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image], 'label_size': label_size, 'model': model, 'high_res': 1})
    assert response.status_code == 400
    assert response.get_json()['message'] == f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'

    # At normal resolution they print
    data['high_res'] = '0'
    assert client.post('/labeldesigner/api/print', data=data).get_json() == {'success': True}


def test_several_files_are_printed_one_after_the_other(tmp_path):
    from app.labeldesigner import printer
    client = make_client(tmp_path)
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'image_fit': '1', 'border_thickness': '0', 'text': '[]',
            'printer': 'simulation', 'print_count': '2', 'pdf_pages': 'all'}
    files = [(io.BytesIO(_pdf((300, 200), (200, 300))), 'two.pdf', 'application/pdf'),
             (io.BytesIO(_upload(Image.new('L', (400, 100), 'black'), 'wide.png').stream.read()), 'wide.png', 'image/png')]
    response = client.post('/labeldesigner/api/print', data={**data, 'image': files})
    assert response.status_code == 200, response.get_json()
    names = sorted(os.listdir(printer.SIMULATED_LABELS_DIR))
    heights = [Image.open(os.path.join(printer.SIMULATED_LABELS_DIR, name)).height for name in names]
    # Both pages of the PDF and the image, twice, collated
    assert len(heights) == 6
    assert heights[:3] == heights[3:]
    assert heights[1] > heights[0] > heights[2]


def test_several_files_count_against_the_print_limit(tmp_path):
    client = make_client(tmp_path)
    data = {**EXAMPLE_FORMDATA, 'print_type': 'image', 'text': '[]', 'printer': 'simulation', 'print_count': '40', 'pdf_pages': 'all',
            'image': [(io.BytesIO(_pdf((300, 200), (200, 300))), 'two.pdf', 'application/pdf'),
                      (io.BytesIO(_pdf((300, 200))), 'one.pdf', 'application/pdf')]}
    response = client.post('/labeldesigner/api/print', data=data)
    assert response.status_code == 400
    assert response.get_json()['message'] == 'A print job must not have more than 100 labels'


@pytest.mark.parametrize('route', ['print', 'webhook', 'repository'])
@pytest.mark.parametrize('fast', [None, '0', '1'])
def test_fast_printing(tmp_path, monkeypatch, route, fast):
    # Unless printing fast, the printer gives priority to print quality, bit
    # 6 of the media and quality command (ESC i z), which makes it slower
    sent = sent_raster(monkeypatch)
    client = make_client(tmp_path)
    options = {} if fast is None else {'fast_printing': fast}
    if route == 'print':
        response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, **options))
    elif route == 'webhook':
        client.application.config['WEBHOOK_PASSWORD'] = 'secret'
        image = base64.b64encode(_upload(_red_square(), 'red.png').stream.read()).decode('ascii')
        response = client.post('/labeldesigner/api/webhook/print', json={'password': 'secret', 'images': [image], 'label_size': '62', **options})
    else:
        response = client.post('/labeldesigner/api/repository/print', data={'name': 'QR-Example.json', **options})
    assert response.get_json()['success'] is True
    data = sent[0]
    flags = data[data.index(b'\x1biz') + 3]
    assert bool(flags & 0x40) == (fast != '1')


@pytest.mark.parametrize('model, label_size, ptouch', [('PT-E550W', 'pt12', True), ('QL-700', '62', False)])
def test_ptouch_printers_get_compressed_lines_and_no_media_type(tmp_path, monkeypatch, model, label_size, ptouch):
    # A PT-E550W hung after the half cut with uncompressed raster lines over
    # Wi-Fi, and with media type 0x00 ("no tape") also over USB
    sent = sent_raster(monkeypatch)
    client = make_client(tmp_path, model=model)
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, label_size=label_size, orientation='rotated'))
    assert response.get_json()['success'] is True
    data = sent[0]
    flags = data[data.index(b'\x1biz') + 3]
    # Valid flag of the media type
    assert bool(flags & 0x02) != ptouch
    assert (b'M\x02' in data) == ptouch


@pytest.mark.parametrize('cut_once', ['0', '1'])
def test_copies_on_ptouch_printers_are_one_job(tmp_path, monkeypatch, cut_once):
    # As jobs of their own, a PT-E550W fed, half cut and cut the tape for
    # every copy, also with cut_once
    sent = sent_raster(monkeypatch)
    client = make_client(tmp_path, model='PT-E550W')
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, label_size='pt12', orientation='rotated', print_count='12', cut_once=cut_once))
    assert response.get_json()['success'] is True
    assert len(sent) == 1
    data = sent[0]
    assert data.count(b'\x1b@') == 1
    # The following labels of a job are not its first page
    assert [data[i + 11] for i in range(len(data)) if data.startswith(b'\x1biz', i)] == [0] + [1] * 11
    # Half cut, no chain printing
    assert [data[i + 3] for i in range(len(data)) if data.startswith(b'\x1biK', i)] == [0x0c] * 12
    assert data.endswith(b'\x1a')
    cut_every = [data[i + 3] for i in range(len(data)) if data.startswith(b'\x1biA', i)]
    # With cut_once half cuts between the labels and a cut after the last,
    # else a cut after every label
    assert cut_every == ([12] * 12 if cut_once == '1' else [1] * 12)


@pytest.mark.parametrize('options, autocut, cut_every, advanced', [
    # Cut after each label, half cut before the first, no chain printing
    ({}, 0x40, [1, 1, 1], 0x0c),
    ({'cut': 'last'}, 0x40, [3, 3, 3], 0x0c),
    ({'cut': 'last', 'half_cut': '0'}, 0x40, [3, 3, 3], 0x08),
    # Not cut at all, the labels only half cut, chain printing: else the
    # printer cuts after the last label
    ({'cut': 'none'}, 0x00, [], 0x04),
    # The last label stays in the printer, the next job cuts it off
    ({'cut': 'last', 'chain': '1'}, 0x40, [3, 3, 3], 0x04),
    ({'cut': 'none', 'half_cut': '0', 'chain': '1'}, 0x00, [], 0x00),
])
def test_cutting_on_ptouch_printers(tmp_path, monkeypatch, options, autocut, cut_every, advanced):
    sent = sent_raster(monkeypatch)
    client = make_client(tmp_path, model='PT-E550W')
    data = dict(EXAMPLE_FORMDATA, label_size='pt12', orientation='rotated', print_count='3', **options)
    assert client.post('/labeldesigner/api/print', data=data).get_json()['success'] is True
    data, = sent
    of = lambda cmd: [data[i + 3] for i in range(len(data)) if data.startswith(cmd, i)]
    # Various mode (ESC i M), cut every n labels (ESC i A), advanced mode (ESC i K)
    assert of(b'\x1biM') == [autocut] * 3
    assert of(b'\x1biA') == cut_every
    assert of(b'\x1biK') == [advanced] * 3


@pytest.mark.parametrize('cut, cuts', [('each', [True, True, True]), ('last', [False, False, True]), ('none', [False, False, False])])
def test_cutting_on_ql_printers(tmp_path, monkeypatch, cut, cuts):
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: queued.extend(self.label_cuts()) or '')
    client = make_client(tmp_path)
    assert client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, print_count='3', cut=cut)).get_json()['success'] is True
    assert queued == cuts


def test_unknown_cut_is_refused(tmp_path):
    client = make_client(tmp_path)
    response = client.post('/labeldesigner/api/print', data=dict(EXAMPLE_FORMDATA, cut='twice'))
    assert response.status_code == 400
    assert response.get_json()['message'] == 'cut must be each, last or none'


def test_ptouch_tape_is_endless(tmp_path):
    # P-touch tapes were taken for round die-cut labels: the label was 1 dot
    # long and the printer stopped after a few millimeters
    client = make_client(tmp_path, model='PT-E550W')
    text = json.dumps([{'text': 'PT-E550W', 'font': 'DejaVu Sans,Book', 'size': 40}])
    response = client.post('/labeldesigner/api/preview?return_format=png', data={
        'printer': 'simulation', 'label_size': 'pt12', 'orientation': 'rotated', 'text': text})
    assert response.status_code == 200
    assert 'X-Label-Warning' not in response.headers
    image = Image.open(io.BytesIO(response.data))
    # Across the tape the printable 70 dots, along it as long as the text
    assert image.height == 70 and image.width > 100


@pytest.mark.parametrize('path, body', [
    ('/labeldesigner/api/print', None),
    ('/labeldesigner/api/repository/print', {'name': 'Shelf.json'}),
    ('/labeldesigner/api/demos/print', {'id': 'signs/001-fire-extinguisher-62.json'}),
    ('/labeldesigner/api/webhook/print', 'webhook'),
])
def test_printer_failures_are_told_from_refused_requests(tmp_path, monkeypatch, path, body):
    # Both were 400, a client could not tell whether to fix its request or
    # to try again later
    from app.labeldesigner import printer
    failure = 'The printer reports: Replace media error'
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self: failure)
    client = make_client(tmp_path, empty_repo=True)
    client.application.config['WEBHOOK_PASSWORD'] = 'secret'
    text = json.dumps([{'text': 'Shelf', 'font': 'DejaVu Sans,Book', 'size': '40'}])
    assert client.post('/labeldesigner/api/repository/save', json={'name': 'Shelf.json', 'label_size': '62', 'text': text}).status_code == 200
    if body is None:
        def post(**values):
            return client.post(path, data=dict(EXAMPLE_FORMDATA, **values))
    elif body == 'webhook':
        image = base64.b64encode(_upload(_red_square(), 'image.png').stream.read()).decode('ascii')

        def post(**values):
            return client.post(path, json=dict({'password': 'secret', 'images': [image]}, **values))
    else:
        def post(**values):
            return client.post(path, json=dict(body, **values))
    response = post()
    assert (response.status_code, response.get_json()['message']) == (503, failure)
    # A request that is refused stays 400
    refused = {'label_size': '99x99'} if body in (None, 'webhook') else {'print_count': 0}
    assert post(**refused).status_code == 400
