import re

from PIL import Image
from brother_ql.conversion import convert
from brother_ql.labels import ALL_LABELS
from brother_ql.raster import BrotherQLRaster

from app.labeldesigner.label_names import LABEL_NAMES, label_printers
from test_labeldesigner_api import make_client


def _label(identifier):
    return next(label for label in ALL_LABELS if label.identifier == identifier)


def test_every_label_size_has_a_name():
    # A new label size in brother_ql needs a name with its Brother code
    assert {label.identifier for label in ALL_LABELS} == set(LABEL_NAMES)


def test_names_are_unique():
    assert len(set(LABEL_NAMES.values())) == len(LABEL_NAMES)


def test_label_printers():
    assert label_printers(_label('103x164')) == ['QL-1100', 'QL-1110NWB']
    # DK-22246 is offered as "104" only
    assert label_printers(_label('103')) == []
    assert 'QL-1100' in label_printers(_label('104'))
    # TZe tapes only on P-touch printers with a wide enough print head
    assert label_printers(_label('pt36')) == ['PT-P900W', 'PT-P950NW']
    assert label_printers(_label('18')) == ['PT-P900W', 'PT-P950NW']
    assert 'PT-P700' in label_printers(_label('pt24'))
    assert 'PT-P900W' not in label_printers(_label('62'))
    # The black/red roll only on printers that print red
    assert label_printers(_label('62red')) == ['QL-800', 'QL-810W', 'QL-820NWB']


def test_every_offered_label_size_can_be_printed():
    for label in ALL_LABELS:
        for model in label_printers(label):
            image = Image.new('1', (label.dots_printable[0], label.dots_printable[1] or 100), 1)
            convert(BrotherQLRaster(model), [image], label.identifier, cut=True)


def test_label_designer_shows_brother_codes_and_printers(tmp_path):
    html = make_client(tmp_path).get('/labeldesigner/').get_data(as_text=True)
    options = {match.group(1): match.group(0) for match in re.finditer(r'<option value="([^"]+)" data-x[^>]*>[^<]*</option>', html)}
    assert '103 x 164 mm die-cut (DK-11247 / DK-1247)' in options['103x164']
    assert 'data-models="QL-1100,QL-1110NWB"' in options['103x164']
    assert 'data-models=""' in options['103']
    assert 'data-models="PT-P900W,PT-P950NW"' in options['pt36']
