"""The label sizes and models of brother_ql, with the label sizes it lacks
and the model codes and offsets it has wrong fixed when this module is
imported (app/labeldesigner/__init__.py imports the drivers first), and
which model prints which label size.

brother_ql-inventree is archived, so it is fixed here, after Brother's raster
command reference for the PT-E550W/P750W/P710BT:
https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf

- 6 and 9 mm TZe tapes, with the print head pins of the reference (6 mm:
  48 + 32 + 48 pins, 9 mm: 39 + 50 + 39).
- The model code of the PT-E550W (status byte 4): 66h, brother_ql has 68h,
  the code of the PT-P750W. A PT-E550W was an unknown printer, a PT-P750W
  was taken for a PT-E550W.
"""
import brother_ql.labels
from brother_ql import devicedependent
from brother_ql.devicedependent import label_type_specs
from brother_ql.labels import Color, FormFactor, Label, LabelsManager
from brother_ql.models import ALL_MODELS

# The pins are those of the 128-pin print head, the PT-P900 series places
# tapes differently on its wider one
SMALL_HEAD_MODELS = ['PT-E550W', 'PT-P700', 'PT-P750W']
# brother_ql's tapes made for the 128-pin print head at 180 dpi. Offered to
# the PT-P900 series as well, with its 560 pins at 360 dpi they printed half
# as large and in the wrong place. Its 18 and 36 mm tapes ("18", pt36) are
# made for its head.
SMALL_HEAD_TAPES = ('pt12', 'pt18', 'pt24')

EXTRA_LABELS = (
    Label('pt6', (6, 0), FormFactor.PTOUCH_ENDLESS, (128, 0), (32, 0), 48, feed_margin=14,
          restricted_to_models=SMALL_HEAD_MODELS),
    Label('pt9', (9, 0), FormFactor.PTOUCH_ENDLESS, (128, 0), (50, 0), 39, feed_margin=14,
          restricted_to_models=SMALL_HEAD_MODELS),
)


def _add_labels():
    known = {label.identifier for label in brother_ql.labels.ALL_LABELS}
    new = tuple(label for label in EXTRA_LABELS if label.identifier not in known)
    if not new:
        return
    # Before the 12 mm tape, the narrowest one brother_ql has
    labels = list(brother_ql.labels.ALL_LABELS)
    position = next((i for i, label in enumerate(labels) if label.identifier == 'pt12'), len(labels))
    labels[position:position] = new
    brother_ql.labels.ALL_LABELS = tuple(labels)
    LabelsManager.DEFAULT_ELEMENTS = tuple(labels)
    # The conversion to raster data reads the labels from label_type_specs,
    # which brother_ql fills when it is imported
    devicedependent._populate_label_legacy_structures()


MODEL_CODES = {'PT-E550W': 0x66}


def _correct_model_codes():
    # brother_ql reads a status with these same objects
    for model in ALL_MODELS:
        if model.identifier in MODEL_CODES:
            model.model_code = MODEL_CODES[model.identifier]


def _restrict_tapes():
    for label in brother_ql.labels.ALL_LABELS:
        if label.identifier in SMALL_HEAD_TAPES:
            label.restricted_to_models = SMALL_HEAD_MODELS


_add_labels()
_correct_model_codes()
_restrict_tapes()


# Horizontal offsets (offset_r, in dots) that differ from the label table of
# brother_ql, measured on real printers. Remove an entry once brother_ql
# ships the corrected value.
# - 103x164 (103 x 164 mm die-cut): with brother_ql's 12 the content is 1.5 mm
#   off-center on a QL-1100 (3.5 mm left, 6.5 mm right), -6 centers it. The
#   104 mm endless entry of brother_ql already uses a similar -8.
LABEL_OFFSET_CORRECTIONS = {
    '103x164': -6,
}


def _apply_label_offset_corrections():
    for label in brother_ql.labels.ALL_LABELS:
        if label.identifier in LABEL_OFFSET_CORRECTIONS:
            label.offset_r = LABEL_OFFSET_CORRECTIONS[label.identifier]
    # brother_ql copies the offsets into label_type_specs when it is imported,
    # the conversion to raster data reads them from there
    for identifier, offset in LABEL_OFFSET_CORRECTIONS.items():
        if identifier in label_type_specs:
            label_type_specs[identifier]['right_margin_dots'] = offset


_apply_label_offset_corrections()

LABELS = brother_ql.labels.ALL_LABELS
MODELS = {model.identifier: model for model in ALL_MODELS}

# Label sizes for P-touch printers. brother_ql does not restrict them to
# these printers, but QL printers cannot use TZe tapes (and P-touch printers
# no DK rolls).
PTOUCH_LABELS = {'18', 'pt6', 'pt9', 'pt12', 'pt18', 'pt24', 'pt36'}

# Label sizes only shown while selected, e.g. in saved settings. "103" is the
# same roll as "104", but brother_ql places it 18 dots (1.5 mm) further from
# the center of the QL-1100 print head. "104" is 2 dots from the position
# measured for the 103 x 164 mm die-cut label (see LABEL_OFFSET_CORRECTIONS).
HIDDEN_LABELS = frozenset({'103'})

# The QL-1100 series prints at 300 dpi only. Brother's raster command
# reference of these models lists no 600 dpi resolution, unlike that of the
# QL-800, and a 600 dpi label came out twice as long on a QL-1100 (#103):
# https://download.brother.com/welcome/docp100366/cv_ql1100_eng_raster_100.pdf
# The P-touch printers print at 180 dpi. For 600 dpi brother_ql sets bit 5
# of ESC i K, which their reference marks as not used:
# https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
MODELS_WITHOUT_600_DPI = ('QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W')

# P-touch printers with a half cutter, the PT-P700 has none
HALF_CUTTERS = ('PT-E550W', 'PT-P750W', 'PT-P900W', 'PT-P950NW')


def label_fits(label: Label, model: str) -> bool:
    """Whether a printer model can print a label size."""
    printer_model = MODELS[model]
    return (
        (not label.restricted_to_models or model in label.restricted_to_models)
        and (label.identifier in PTOUCH_LABELS) == model.startswith('PT-')
        and (label.color != Color.BLACK_RED_WHITE or printer_model.two_color)
        # brother_ql does not restrict the TZe tapes to the printers with a
        # wide enough print head (36 mm needs a PT-P900 series printer)
        and label.dots_printable[0] <= printer_model.number_bytes_per_row * 8
    )


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    if high_res and model in MODELS_WITHOUT_600_DPI:
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'
    return ''
