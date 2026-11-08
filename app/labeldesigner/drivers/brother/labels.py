"""The label sizes of the Brother raster printers, after the label table of
brother_ql by Philipp Klaus, and which model prints which."""

from ..labels import Color, FormFactor, Label
from .models import MODELS

# DK rolls of 102 to 104 mm, for the QL-1050 and QL-1100 series
WIDE = ('QL-1050', 'QL-1060N', 'QL-1100', 'QL-1110NWB', 'QL-1115NWB')
# TZe tapes of 6 to 24 mm on the 128 pins of the print head at 180 dpi, placed
# as in Brother's raster command reference of the PT-E550W, PT-P750W and
# PT-P710BT (6 mm: 48 + 32 + 48 pins, 9 mm: 39 + 50 + 39). The PT-P900 series
# has a print head of its own, its tapes are "18" and pt36.
# https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
SMALL_HEAD = ('PT-E550W', 'PT-P700', 'PT-P750W')

LABELS = (
    Label('12', (12, 0), FormFactor.ENDLESS, (142, 0), (106, 0), 29, feed_margin=35),
    Label('12+17', (12, 0), FormFactor.ENDLESS, (342, 0), (306, 0), 29, feed_margin=35),
    Label('18', (18, 0), FormFactor.ENDLESS, (256, 0), (234, 0), 171, feed_margin=14),
    Label('29', (29, 0), FormFactor.ENDLESS, (342, 0), (306, 0), 6, feed_margin=35),
    Label('38', (38, 0), FormFactor.ENDLESS, (449, 0), (413, 0), 12, feed_margin=35),
    Label('50', (50, 0), FormFactor.ENDLESS, (590, 0), (554, 0), 12, feed_margin=35),
    Label('54', (54, 0), FormFactor.ENDLESS, (636, 0), (590, 0), 0, feed_margin=35),
    Label('62', (62, 0), FormFactor.ENDLESS, (732, 0), (696, 0), 12, feed_margin=35),
    Label('62red', (62, 0), FormFactor.ENDLESS, (732, 0), (696, 0), 12, feed_margin=35, color=Color.BLACK_RED_WHITE),
    Label('102', (102, 0), FormFactor.ENDLESS, (1200, 0), (1164, 0), 12, feed_margin=35, models=WIDE),
    Label('103', (104, 0), FormFactor.ENDLESS, (1224, 0), (1200, 0), 12, feed_margin=35, models=('QL-1100', 'QL-1110NWB')),
    Label('104', (104, 0), FormFactor.ENDLESS, (1227, 0), (1200, 0), -8, feed_margin=35, models=WIDE),
    Label('17x54', (17, 54), FormFactor.DIE_CUT, (201, 636), (165, 566), 0),
    Label('17x87', (17, 87), FormFactor.DIE_CUT, (201, 1026), (165, 956), 0),
    Label('23x23', (23, 23), FormFactor.DIE_CUT, (272, 272), (202, 202), 42),
    Label('29x42', (29, 42), FormFactor.DIE_CUT, (342, 495), (306, 425), 6),
    Label('29x90', (29, 90), FormFactor.DIE_CUT, (342, 1061), (306, 991), 6),
    Label('39x90', (38, 90), FormFactor.DIE_CUT, (449, 1061), (413, 991), 12),
    Label('39x48', (39, 48), FormFactor.DIE_CUT, (461, 565), (425, 495), 6),
    Label('52x29', (52, 29), FormFactor.DIE_CUT, (614, 341), (578, 271), 0),
    Label('54x29', (54, 29), FormFactor.DIE_CUT, (630, 341), (598, 271), 60),
    Label('60x86', (60, 87), FormFactor.DIE_CUT, (708, 1024), (672, 954), 18),
    Label('62x29', (62, 29), FormFactor.DIE_CUT, (732, 341), (696, 271), 12),
    Label('62x100', (62, 100), FormFactor.DIE_CUT, (732, 1179), (696, 1109), 12),
    Label('102x51', (102, 51), FormFactor.DIE_CUT, (1200, 596), (1164, 526), 12, models=WIDE),
    Label('102x152', (102, 153), FormFactor.DIE_CUT, (1200, 1804), (1164, 1660), 12, models=WIDE),
    # The roll is 104 mm wide. With an offset of -6 the print is centered on a
    # QL-1100, measured.
    Label('103x164', (104, 164), FormFactor.DIE_CUT, (1224, 1941), (1200, 1822), -6, models=('QL-1100', 'QL-1110NWB')),
    Label('d12', (12, 12), FormFactor.ROUND_DIE_CUT, (142, 142), (94, 94), 113, feed_margin=35),
    Label('d24', (24, 24), FormFactor.ROUND_DIE_CUT, (284, 284), (236, 236), 42),
    Label('d58', (58, 58), FormFactor.ROUND_DIE_CUT, (688, 688), (618, 618), 51),
    Label('pt6', (6, 0), FormFactor.PTOUCH_ENDLESS, (128, 0), (32, 0), 48, feed_margin=14, models=SMALL_HEAD),
    Label('pt9', (9, 0), FormFactor.PTOUCH_ENDLESS, (128, 0), (50, 0), 39, feed_margin=14, models=SMALL_HEAD),
    Label('pt12', (12, 0), FormFactor.PTOUCH_ENDLESS, (128, 0), (70, 0), 29, feed_margin=14, models=SMALL_HEAD),
    Label('pt18', (18, 0), FormFactor.PTOUCH_ENDLESS, (128, 0), (112, 0), 8, feed_margin=14, models=SMALL_HEAD),
    Label('pt24', (24, 0), FormFactor.PTOUCH_ENDLESS, (128, 0), (128, 0), 0, feed_margin=14, models=SMALL_HEAD),
    Label('pt36', (36, 0), FormFactor.PTOUCH_ENDLESS, (512, 0), (454, 0), 61, feed_margin=14),
)
BY_ID = {label.identifier: label for label in LABELS}

# The tapes of the P-touch printers, which QL printers cannot use (nor the
# P-touch printers DK rolls)
PTOUCH_LABELS = {'18', 'pt6', 'pt9', 'pt12', 'pt18', 'pt24', 'pt36'}

# Label sizes only shown while selected, e.g. in saved settings. "103" is the
# same roll as "104", but placed 18 dots (1.5 mm) further from the center of
# the QL-1100 print head. "104" is 2 dots from the position measured for the
# 103 x 164 mm die-cut label.
HIDDEN_LABELS = frozenset({'103'})


def label_fits(label: Label, model: str) -> bool:
    """Whether a printer model can print a label size."""
    printer_model = MODELS[model]
    return (
        (not label.models or model in label.models)
        and (label.identifier in PTOUCH_LABELS) == model.startswith('PT-')
        and (label.color != Color.BLACK_RED_WHITE or printer_model.two_color)
        # 36 mm tape needs the wide print head of the PT-P900 series
        and label.dots_printable[0] <= printer_model.number_bytes_per_row * 8
    )
