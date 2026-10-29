import re

from brother_ql.labels import ALL_LABELS

from app.labeldesigner.label_names import LABEL_NAMES
from test_labeldesigner_api import make_client


def test_every_label_size_has_a_name():
    # A new label size in brother_ql needs a name with its Brother code
    assert {label.identifier for label in ALL_LABELS} == set(LABEL_NAMES)


def test_names_are_unique_except_for_the_same_roll():
    identifiers_by_name = {}
    for identifier, name in LABEL_NAMES.items():
        identifiers_by_name.setdefault(name, []).append(identifier)
    duplicates = {name: ids for name, ids in identifiers_by_name.items() if len(ids) > 1}
    assert duplicates == {'DK-22246 / DK-2246 (103 mm endless)': ['103', '104']}


def test_label_designer_shows_brother_codes_and_printers(tmp_path):
    html = make_client(tmp_path).get('/labeldesigner/').get_data(as_text=True)
    options = {match.group(1): match.group(0) for match in re.finditer(r'<option value="([^"]+)" data-x[^>]*>[^<]*</option>', html)}
    assert 'DK-11247 / DK-1247 (103 x 164 mm die-cut)' in options['103x164']
    assert 'data-models="QL-1100,QL-1110NWB"' in options['103x164']
    assert 'data-models=""' in options['62']
    assert 'data-ptouch="1"' in options['pt12']
    assert 'data-ptouch="0"' in options['62']
    assert 'data-red="1"' in options['62red']
    assert 'data-red="0"' in options['62']
