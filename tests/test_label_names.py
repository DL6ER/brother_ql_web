import json
import pytest
import re

from PIL import Image
from brother_ql.conversion import convert
from brother_ql.labels import ALL_LABELS
from brother_ql.raster import BrotherQLRaster

from app.labeldesigner.label_names import LABEL_NAMES, PTOUCH_LABELS, check_label_size, label_printers
from tests.helpers import make_client


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


@pytest.mark.parametrize('identifier', ['pt12', 'pt18', 'pt24'])
def test_tapes_of_the_small_print_head_are_not_offered_to_the_pt_p900_series(identifier):
    # Made for the 128-pin print head at 180 dpi, the PT-P900 series printed
    # them half as large and in the wrong place
    assert label_printers(_label(identifier)) == ['PT-E550W', 'PT-P700', 'PT-P750W']
    with pytest.raises(ValueError, match='cannot be printed on a PT-P900W$'):
        check_label_size(identifier, 'PT-P900W')
    # Its own tapes stay
    assert sorted(size for size in PTOUCH_LABELS if 'PT-P950NW' in label_printers(_label(size))) == ['18', 'pt36']


def test_every_offered_label_size_can_be_printed():
    for label in ALL_LABELS:
        for model in label_printers(label):
            image = Image.new('1', (label.dots_printable[0], label.dots_printable[1] or 100), 1)
            convert(BrotherQLRaster(model), [image], label.identifier, cut=True)


def test_label_designer_shows_brother_codes_and_printers(tmp_path):
    client = make_client(tmp_path)
    html = client.get('/labeldesigner/').get_data(as_text=True)
    config = json.loads(re.search(r'const DESIGNER_CONFIG = (.*);</script>', html).group(1))
    sizes = {size['id']: size for size in config['label_sizes']}
    assert sizes['103x164']['name'] == '103 x 164 mm die-cut (DK-11247 / DK-1247)'
    assert sizes['103x164']['models'] == ['QL-1100', 'QL-1110NWB']
    assert sizes['103']['models'] == []
    assert sizes['pt36']['models'] == ['PT-P900W', 'PT-P950NW']


def test_check_label_size():
    from app.labeldesigner.label_names import check_label_size
    check_label_size('103x164', 'QL-1100')
    # "103" is not offered in the label designer, but saved labels may use it
    check_label_size('103', 'QL-1100')
    # Models brother_ql does not know are left to brother_ql
    check_label_size('62', 'QL-9999')
    with pytest.raises(ValueError, match=r'^The label size 103 x 164 mm die-cut \(DK-11247 / DK-1247\) cannot be printed on a QL-800$'):
        check_label_size('103x164', 'QL-800')
    with pytest.raises(ValueError, match='cannot be printed on a PT-P700'):
        check_label_size('pt36', 'PT-P700')
    with pytest.raises(ValueError, match='^Unknown label size: 99x99$'):
        check_label_size('99x99', 'QL-800')


@pytest.mark.parametrize('identifier, pins', [('pt6', range(48, 80)), ('pt9', range(39, 89)), ('pt12', range(29, 99))])
def test_narrow_tze_tapes_use_the_pins_of_the_raster_command_reference(identifier, pins):
    # brother_ql has no 6 and 9 mm tapes, the label designer adds them
    printers = label_printers(_label(identifier))
    assert 'PT-E550W' in printers
    assert 'PT-P900W' not in printers
    image = Image.new('1', (len(pins), 40), 0)
    data = convert(BrotherQLRaster('PT-E550W'), [image], identifier, cut=True, rotate=0)
    used = set()
    i = data.index(b'M\x00') + 2
    while data[i] == 0x47:
        n = int.from_bytes(data[i + 1:i + 3], 'little')
        bits = int.from_bytes(data[i + 3:i + 3 + n], 'big')
        used |= {pin for pin in range(128) if bits >> (127 - pin) & 1}
        i += 3 + n
    assert used == set(pins)
