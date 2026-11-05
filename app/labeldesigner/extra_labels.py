"""Label sizes brother_ql lacks and model codes it has wrong, fixed before
the label designer reads its lists of labels and models
(app/labeldesigner/__init__.py imports this module first).

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
from brother_ql.labels import FormFactor, Label, LabelsManager
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
