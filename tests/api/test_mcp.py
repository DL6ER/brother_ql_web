"""The MCP endpoint /mcp: its tools print through the API, with a cap, and
only on the label size the call expects."""

import base64
import io
import os
import re

import pytest
from PIL import Image

from app.labeldesigner import mcp_tools
from tests.helpers import make_client

HEADERS = {'Accept': 'application/json, text/event-stream', 'MCP-Protocol-Version': '2025-06-18'}


@pytest.fixture
def client(tmp_path):
    client = make_client(tmp_path)
    client.application.config['PRINTERS'] = [('QL-800', 'simulation', 'Kitchen')]
    return client


def call(client, method, params=None, **headers):
    response = client.post('/mcp', json={'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params or {}},
                           headers={**HEADERS, **headers})
    return response.status_code, response.get_json(silent=True)


def tool(client, tool_name, **arguments):
    status, answer = call(client, 'tools/call', {'name': tool_name, 'arguments': arguments})
    assert status == 200
    return answer['result']


def printed(tmp_path):
    folder = tmp_path / 'simulated_labels'
    return len(os.listdir(folder)) if folder.exists() else 0


def test_tools_reading_are_marked_and_printing_ones_confirmed(client):
    status, answer = call(client, 'tools/list')
    tools = {t['name']: t for t in answer['result']['tools']}
    assert set(tools) == {'list_printers', 'list_labels', 'preview_label', 'print_label', 'print_text'}
    for name in ('list_printers', 'list_labels', 'preview_label'):
        assert tools[name]['annotations']['readOnlyHint'] is True
    for name in ('print_label', 'print_text'):
        assert tools[name]['annotations']['readOnlyHint'] is False
        assert tools[name]['_meta'] == {'anthropic/requiresUserInteraction': True}


def test_print_text_with_a_code_returns_the_image(client, tmp_path):
    result = tool(client, 'print_text', printer='Kitchen', label_size='62', count=2, lines=['Wi-Fi'],
                  code='WIFI:T:WPA;S:home;P:secret;;')
    assert not result.get('isError')
    assert result['content'][0]['text'].splitlines()[0] == 'Printed 2 labels on Kitchen'
    image = Image.open(io.BytesIO(base64.b64decode(result['content'][1]['data'])))
    assert image.width > 0
    assert printed(tmp_path) == 2


def test_at_most_mcp_max_count_labels_per_call(client, tmp_path):
    client.application.config['MCP_MAX_COUNT'] = 3
    result = tool(client, 'print_label', name='QR-Example.json', printer='Kitchen', label_size='62', count=2,
                  fillings=[{}, {}])
    assert result['isError'] and 'At most 3 labels per call' in result['content'][0]['text']
    assert printed(tmp_path) == 0


def test_print_label_once_for_each_filling(client, tmp_path):
    result = tool(client, 'print_label', name='QR-Example.json', printer='Kitchen', label_size='62', count=1,
                  fillings=[{}, {}, {}])
    assert not result.get('isError')
    assert len(result['content']) == 4
    assert printed(tmp_path) == 3


def test_refused_on_another_label_size_than_loaded(client, tmp_path, monkeypatch):
    monkeypatch.setattr(mcp_tools, 'loaded_label_sizes', lambda printer: ['29'])
    result = tool(client, 'print_text', printer='Kitchen', label_size='62', count=1, lines=['x'])
    assert result['isError'] and 'The printer has 29 loaded, not 62' in result['content'][0]['text']
    assert printed(tmp_path) == 0


def test_an_unknown_label_size_tells_those_the_printer_takes(client, tmp_path):
    result = tool(client, 'print_text', printer='Kitchen', label_size='100x30', count=1, lines=['x'])
    text = result['content'][0]['text']
    assert result['isError'] and 'No label size 100x30 for this printer, it takes ' in text and '29x90' in text
    assert printed(tmp_path) == 0


def test_errors_are_those_of_the_api(client):
    result = tool(client, 'preview_label', lines=['x'], label_size='62', code='abc', code_type='EAN13')
    assert result['isError'] and 'EAN-13 takes 12 digits' in result['content'][0]['text']


def test_list_labels_with_their_blanks(client, tmp_path):
    repo = tmp_path / 'repo'
    repo.mkdir()
    (repo / 'Jar.json').write_text('{"label_size": "62", "tags": ["Kitchen"], "text": [{"text": "{{blank:Content}}", "size": "60"},'
                                   ' {"text": "{{blank:Made}}", "size": "40"}], "fields": {"Made": {"type": "date"}}}')
    client.application.config['LABEL_REPOSITORY_DIR'] = str(repo)
    labels = tool(client, 'list_labels')['structuredContent']['result']
    assert labels == [{'name': 'Jar.json', 'title': 'Jar.json', 'label_size': '62', 'tags': ['Kitchen'],
                       'blanks': [{'name': 'Content'}, {'name': 'Made', 'type': 'date'}]}]


@pytest.mark.parametrize('status, sizes', [
    ({'model': 'QL-800', 'media_width': 62, 'media_length': 0, 'text_color': ''}, ['62', '62red']),
    ({'model': 'QL-800', 'media_width': 62, 'media_length': 0, 'text_color': 'Red'}, ['62red']),
    ({'model': 'QL-800', 'media_width': 29, 'media_length': 90, 'text_color': ''}, ['29x90']),
    ({'model': 'QL-800', 'media_width': None}, []),
    ({'model': 'QL-800', 'media_width': None, 'media_length': 90}, ['29x90', '39x90'])])
def test_label_sizes_loaded(status, sizes):
    assert mcp_tools.loaded_label_sizes(status) == sizes


@pytest.mark.parametrize('host, allowed', [
    ('localhost', True), ('printserver:8013', True), ('labels.local', True), ('x.lan', True),
    ('x.home.arpa', True), ('192.168.1.5:8013', True), ('[fd00::1]:8013', True),
    ('example.com', False), ('labels.local.example.com', False), ('', False)])
def test_host_names_of_the_home_network(host, allowed):
    assert mcp_tools.host_allowed(host, None) is allowed


def test_further_names_with_mcp_hosts_and_only_trusted_ones():
    assert mcp_tools.host_allowed('labels.example.com', ['labels.example.com'])
    assert mcp_tools.host_allowed('a.example.com:8013', ['.example.com'])
    assert not mcp_tools.host_allowed('example.org', ['.example.com'])
    assert not mcp_tools.host_allowed('labels.local', None, ['labels.example.com'])
    assert mcp_tools.host_allowed('labels.example.com', ['labels.example.com'], ['labels.example.com'])


def test_other_hosts_and_origins_are_refused(client):
    assert call(client, 'tools/list', Origin='http://evil.example')[0] == 403
    assert call(client, 'tools/list', Origin='http://labels.local')[0] == 200
    response = client.post('/mcp', json={}, headers={**HEADERS, 'Host': 'evil.example'})
    assert response.status_code == 403


def test_printers_with_what_they_report_on_usb(tmp_path, monkeypatch):
    device = tmp_path / '1-1.3'
    device.mkdir()
    for name, value in (('idVendor', '0b0b'), ('idProduct', '106e'), ('serial', '0024624'),
                        ('manufacturer', "Datamax-O`Neil"), ('product', "Datamax-O'Neil E-Class MarkIII")):
        (device / name).write_text(value + '\n')
    monkeypatch.setattr(mcp_tools, 'USB_DEVICES', str(tmp_path))
    assert mcp_tools.usb_info('usb://0x0b0b:0x106e/24624') == {
        'maker': 'Datamax-O`Neil', 'model': "Datamax-O'Neil E-Class MarkIII", 'serial': '0024624'}
    assert mcp_tools.usb_info('usb://0x0b0b:0x106e/1') == {}
    assert mcp_tools.usb_info('tcp://printer') == {}


@pytest.mark.parametrize('wanted', ['Kitchen', 'kitchen', 'QL-800', 'simulation', 'kitch'])
def test_a_printer_by_alias_device_driver_or_part(wanted):
    printers = [{'path': 'simulation', 'name': 'Kitchen', 'model': 'QL-800'},
                {'path': 'usb://0x04f9:0x2042/L9Z378866', 'name': '', 'model': 'QL-700'}]
    assert mcp_tools.find_printer(printers, wanted) is printers[0]


def test_a_name_of_several_printers_is_refused():
    from mcp.server.mcpserver.exceptions import ToolError
    printers = [{'path': 'simulation', 'name': 'Kitchen', 'model': 'QL-800'},
                {'path': 'usb://0x04f9:0x2042/L9Z378866', 'name': '', 'model': 'QL-700'}]
    with pytest.raises(ToolError, match='names several printers'):
        mcp_tools.find_printer(printers, 'QL')
    assert mcp_tools.find_printer(printers, 'Zebra') is None


def test_print_by_alias_prints_on_its_device(client, tmp_path):
    result = tool(client, 'print_text', printer='kitchen', label_size='62', count=1, lines=['x'])
    assert result['content'][0]['text'].splitlines()[0] == 'Printed 1 label on Kitchen'
    assert printed(tmp_path) == 1


@pytest.mark.parametrize('label_size, size', [('62', '62'), ('29x90', '29x90'), ('29 x 90 mm', '29x90'), ('29×90', '29x90')])
def test_label_sizes_as_written(label_size, size):
    assert mcp_tools.label_size_for('QL-800', label_size) == size


def test_label_sizes_without_the_prefix_of_their_driver(monkeypatch):
    sizes = [type('Label', (), {'identifier': i}) for i in ('zpl100x30', 'zpl50x30', 'zpl100x50')]
    monkeypatch.setattr(mcp_tools, 'LABELS', sizes)
    monkeypatch.setattr(mcp_tools, 'label_printers', lambda label: ['Zebra/ZPL'])
    assert mcp_tools.label_size_for('Zebra/ZPL', '100x30') == 'zpl100x30'
    assert mcp_tools.label_size_for('Zebra/ZPL', '100x30mm') == 'zpl100x30'
    from mcp.server.mcpserver.exceptions import ToolError
    with pytest.raises(ToolError, match='it takes zpl100x30'):
        mcp_tools.label_size_for('Zebra/ZPL', '30')


def _address(result):
    return re.search(r'!\[Label\]\((\S+)\)', result['content'][0]['text']).group(1)


@pytest.mark.parametrize('prefix', ['', '/labels'])
def test_previews_also_by_their_address(client, prefix):
    # Behind a reverse proxy below /labels
    response = client.post('/mcp', headers=HEADERS, base_url=f'http://localhost{prefix}/', json={
        'jsonrpc': '2.0', 'id': 1, 'method': 'tools/call',
        'params': {'name': 'preview_label', 'arguments': {'lines': ['x'], 'label_size': '62'}}})
    result = response.get_json()['result']
    address = _address(result)
    assert address.startswith(f'http://localhost{prefix}/mcp/preview.png?r=')
    image = client.get(address.removeprefix(f'http://localhost{prefix}'))
    assert image.mimetype == 'image/png'
    assert image.data == base64.b64decode(result['content'][1]['data'])


def test_only_previews_by_address(client):
    import json
    spec = base64.urlsafe_b64encode(json.dumps({'path': '/print', 'data': {}}).encode()).decode()
    assert client.get(f'/mcp/preview.png?r={spec}').status_code == 400
    assert client.get('/mcp/preview.png?r=nonsense').status_code == 400
    assert client.get('/mcp/preview.png', headers={'Host': 'evil.example'}).status_code == 403


def test_label_sizes_of_any_printer_without_one(monkeypatch):
    from mcp.server.mcpserver.exceptions import ToolError
    sizes = [type('Label', (), {'identifier': i}) for i in ('62', 'zpl100x30', 'zpl50x30', 'puqu50x30')]
    monkeypatch.setattr(mcp_tools, 'LABELS', sizes)
    assert mcp_tools.label_size_for(None, '100x30') == 'zpl100x30'
    assert mcp_tools.label_size_for(None, '62') == '62'
    with pytest.raises(ToolError, match='one of zpl50x30, puqu50x30, give the printer'):
        mcp_tools.label_size_for(None, '50x30')
    with pytest.raises(ToolError, match='No label size 7x7, give the printer'):
        mcp_tools.label_size_for(None, '7x7')
