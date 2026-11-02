"""Label sizes brother_ql lacks, added before the label designer reads its
list of labels (app/labeldesigner/__init__.py imports this module first).

brother_ql-inventree is archived, so they are added here: 6 and 9 mm TZe
tapes, with the print head pins of Brother's raster command reference for
the PT-E550W/P750W/P710BT (6 mm: 48 + 32 + 48 pins, 9 mm: 39 + 50 + 39):
https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
"""
import brother_ql.labels
from brother_ql import devicedependent
from brother_ql.labels import FormFactor, Label, LabelsManager

# The pins are those of the 128-pin print head, the PT-P900 series places
# tapes differently on its wider one
SMALL_HEAD_MODELS = ['PT-E550W', 'PT-P700', 'PT-P750W']

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


_add_labels()
