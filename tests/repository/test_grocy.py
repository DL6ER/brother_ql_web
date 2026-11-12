"""Saved labels printed by Grocy: its webhook sends the values as fields of
their own, which fill the blanks of the same name."""

import os

import pytest

from app.labeldesigner import demos
from tests.helpers import make_client
from tests.repository.helpers import SAVE_URL

PRINT_URL = '/labeldesigner/api/repository/print'
# What Grocy 4.7.1 sent for the labels of its demo data, with
# LABEL_PRINTER_HOOK_JSON as JSON, without it as a form
RECORDED = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'fixtures', 'grocy')

# A label for the stock entries of Grocy
GROCY_LABEL = {
    'name': 'Grocy', 'label_size': '62', 'print_type': 'qrcode_text', 'code_text': '{{blank:grocycode}}',
    'text': [{'font': 'DejaVu Sans,Book', 'size': '60', 'text': '{{blank:product}}'},
             {'font': 'DejaVu Sans,Book', 'size': '40', 'text': 'Best before {{blank:stock_entry.best_before_date}}'},
             {'font': 'DejaVu Sans,Book', 'size': '30', 'text': '{{blank:due_date}}'}],
    'fields': {'stock_entry.best_before_date': {'type': 'date', 'format': '%d.%m.%Y'}},
}

# What Grocy sends for a stock entry (StockApiController::StockEntryPrintLabel),
# with its LABEL_PRINTER_PARAMS: the name of the saved label, and the font
# of its default
GROCY_REQUEST = {
    'product': 'Pesto', 'grocycode': 'grcy:p:12:5f3a1b2c',
    'details': {'product': {'id': 12, 'name': 'Pesto'}, 'location': {'id': 2, 'name': 'Fridge'}},
    'stock_entry': {'id': 3, 'product_id': 12, 'amount': '1', 'best_before_date': '2026-11-10', 'stock_id': '5f3a1b2c'},
    'due_date': 'DD: 2026-11-10',
    'name': 'Grocy.json', 'font_family': 'Source Sans Pro (Regular)',
}


@pytest.fixture
def printed(tmp_path, monkeypatch):
    """A client with GROCY_LABEL saved, and the labels it prints, their
    templates filled in."""
    from app.labeldesigner import printer
    queued = []
    monkeypatch.setattr(printer.PrinterQueue, 'add_label_to_queue', lambda self, label, cut=True, high_res=False, fast=False: queued.append(label))
    monkeypatch.setattr(printer.PrinterQueue, 'process_queue', lambda self, batch_size=0: '')
    client = make_client(tmp_path, empty_repo=True)
    assert client.post(SAVE_URL, json=GROCY_LABEL).status_code == 200

    def post(**kwargs):
        queued.clear()
        response = client.post(PRINT_URL, **kwargs)
        assert response.get_json() == {'success': True}
        label = queued[0]
        label.process_templates()
        return [line['text'] for line in label.text], label.code_text
    post.client = client
    return post


def _form(data: dict, prefix: str = '') -> dict:
    """data as a form, its objects written as PHP writes arrays, e.g.,
    stock_entry[best_before_date]. Grocy's own forms have only the plain
    values of details so, see test_grocy_forms_leave_out_its_objects."""
    form = {}
    for key, value in data.items():
        name = f'{prefix}[{key}]' if prefix else key
        form.update(_form(value, name) if isinstance(value, dict) else {name: str(value)})
    return form


def _recorded(kind: str, mode: str) -> dict:
    """The request Grocy sent for the label of kind, as mode (json or
    form), to the address of the documentation."""
    with open(os.path.join(RECORDED, f'{kind}.{mode}'), 'rb') as f:
        body = f.read()
    content_type = 'application/json' if mode == 'json' else 'application/x-www-form-urlencoded'
    return {'query_string': {'name': 'Grocy.json'}, 'data': body, 'content_type': content_type}


@pytest.mark.parametrize('request_kwargs', [
    {'json': GROCY_REQUEST},
    {'data': _form(GROCY_REQUEST)},
    # Set by environment variables, Grocy sends no name: it is in the address
    {'query_string': {'name': 'Grocy.json'}, 'json': {key: value for key, value in GROCY_REQUEST.items() if key != 'name'}},
])
def test_grocy_fills_the_blanks(printed, request_kwargs):
    lines, code = printed(**request_kwargs)
    # A date blank writes the date in its format, a field inside an object
    # is named with dots
    assert lines == ['Pesto', 'Best before 10.11.2026', 'DD: 2026-11-10']
    assert code == 'grcy:p:12:5f3a1b2c'


def test_blanks_of_the_request_go_before_its_fields(printed):
    lines, code = printed(json=dict(GROCY_REQUEST, blanks={'product': 'Basil pesto'}))
    assert lines[0] == 'Basil pesto'


def test_fields_go_before_the_values_saved_with_the_label(printed):
    assert printed.client.post(SAVE_URL, json=dict(GROCY_LABEL, blanks={'product': 'Jam', 'due_date': 'soon'})).status_code == 200
    lines, _ = printed(json={'name': 'Grocy.json', 'product': 'Pesto'})
    assert (lines[0], lines[2]) == ('Pesto', 'soon')


def test_parameters_and_objects_are_no_blanks(printed):
    label = dict(GROCY_LABEL, text=[{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:printer}}|{{blank:name}}|{{blank:details}}|{{blank:flag}}|{{blank:No.}}'}],
                 fields={'printer': {'default': 'P'}, 'details': {'default': 'D'}})
    assert printed.client.post(SAVE_URL, json=label).status_code == 200
    lines, _ = printed(json=dict(GROCY_REQUEST, printer='simulation', flag=True, **{'No.': 7}))
    # The parameters of the request are no values, nor are objects or true
    # and false. A name with a dot that is a field of its own is that field.
    assert lines == ['P||D||7']


def test_cutting_options_are_no_blanks(printed):
    label = dict(GROCY_LABEL, text=[{'font': 'DejaVu Sans,Book', 'size': '40', 'text': '{{blank:cut}}|{{blank:half_cut}}|{{blank:chain}}'}], fields={})
    assert printed.client.post(SAVE_URL, json=label).status_code == 200
    lines, _ = printed(json=dict(GROCY_REQUEST, cut='last', half_cut=0, chain=0))
    assert lines == ['||']


def test_the_preview_takes_the_fields_as_well(printed):
    client = printed.client
    by_fields = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Grocy.json', 'product': 'Pesto', 'grocycode': 'grcy:p:12'})
    by_blanks = client.get('/labeldesigner/api/repository/preview', query_string={'name': 'Grocy.json', 'blanks': '{"product": "Pesto", "grocycode": "grcy:p:12"}'})
    assert by_fields.status_code == by_blanks.status_code == 200
    assert by_fields.data == by_blanks.data


@pytest.mark.parametrize('mode', ['json', 'form'])
@pytest.mark.parametrize('kind, lines, code', [
    ('product', ['Gummy bears', ''], 'grcy:p:3'),
    ('stock-entry', ['Gummy bears', 'DD: 2027-05-03'], 'grcy:p:3:6a0b1c2d3e4f5'),
    ('chore', ['Change towels in the bathroom', ''], 'grcy:c:1'),
    ('battery', ['Battery1', ''], 'grcy:b:1'),
    ('recipe', ['Pizza', ''], 'grcy:r:1'),
])
def test_the_demo_label_takes_what_grocy_sends(printed, mode, kind, lines, code):
    # Grocy prints everything with the same label: the name it sends, and
    # the due date only for stock entries, also in a form
    label = dict(demos.load('kitchen/015-grocy-62.json'), name='Grocy')
    assert printed.client.post(SAVE_URL, json=label).status_code == 200
    assert printed(**_recorded(kind, mode)) == (lines, code)


def test_grocy_forms_leave_out_its_objects(printed):
    # Without LABEL_PRINTER_HOOK_JSON neither the stock entry nor the
    # location are sent, only the plain values of details
    label = dict(GROCY_LABEL, text=[{'font': 'DejaVu Sans,Book', 'size': '40',
                                     'text': '{{blank:details.stock_amount}}|{{blank:details.location.name}}|{{blank:stock_entry.amount}}'}])
    assert printed.client.post(SAVE_URL, json=label).status_code == 200
    assert printed(**_recorded('stock-entry', 'json'))[0] == ['5|Candy cupboard|1']
    assert printed(**_recorded('stock-entry', 'form'))[0] == ['5||']
