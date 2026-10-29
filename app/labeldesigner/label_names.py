"""Names of the label sizes in the label designer.

brother_ql names its label definitions by size only, and several share the
same name. The names here start with Brother's product codes, so that users
can compare them with the code on their roll. The European codes have five
digits, the US, Canadian and Japanese codes four (DK-22205 / DK-2205).

The codes were checked on Brother's own pages and documents: the EU
consumables catalogue, the support.brother.com consumables lists of the
QL-1100 and QL-820NWB (UK and US), brother.ca product pages, Brother Japan's
QL-800 supplies page and the QL-800 raster command reference. Sizes follow
brother_ql (tape width x label length) with Brother's official values where
brother_ql rounds differently.
"""

from brother_ql.labels import Label

LABEL_NAMES = {
    # Endless DK rolls
    '12': 'DK-22214 / DK-2214 (12 mm endless)',
    '29': 'DK-22210 / DK-2210 (29 mm endless)',
    '38': 'DK-22225 / DK-2225 (38 mm endless)',
    '50': 'DK-22223 / DK-2223 (50 mm endless)',
    '54': 'DK-N55224 / DK-N5224 (54 mm endless, non-adhesive)',
    '62': 'DK-22205 / DK-2205 (62 mm endless)',
    '62red': 'DK-22251 / DK-2251 (62 mm endless, black/red/white)',
    '102': 'DK-22243 / DK-2243 (102 mm endless)',
    # The same roll in two printer geometries, brother_ql tested "103" on the
    # QL-1100 and "104" on the QL-1050. The label designer shows one of them.
    '103': 'DK-22246 / DK-2246 (103 mm endless)',
    '104': 'DK-22246 / DK-2246 (103 mm endless)',
    # Not a Brother product, added to brother_ql without documentation
    '12+17': '12 mm endless (12+17)',
    # Die-cut DK labels
    '17x54': 'DK-11204 / DK-1204 (17 x 54 mm die-cut)',
    '17x87': 'DK-11203 / DK-1203 (17 x 87 mm die-cut)',
    '23x23': 'DK-11221 / DK-1221 (23 x 23 mm die-cut)',
    '29x42': 'DK-1215 (29 x 42 mm die-cut, Japan)',
    '29x90': 'DK-11201 / DK-1201 (29 x 90 mm die-cut)',
    '39x90': 'DK-11208 / DK-1208 (38 x 90 mm die-cut)',
    '39x48': 'DK-1220 (39 x 48 mm die-cut, Japan)',
    '52x29': 'DK-1226 (52 x 29 mm die-cut, Japan)',
    '54x29': 'DK-3235 (54 x 29 mm die-cut, removable)',
    '60x86': 'DK-11234 / DK-1234 (60 x 86 mm die-cut)',
    '62x29': 'DK-11209 / DK-1209 (62 x 29 mm die-cut)',
    '62x100': 'DK-11202 / DK-1202 (62 x 100 mm die-cut)',
    '102x51': 'DK-11240 / DK-1240 (102 x 51 mm die-cut)',
    '102x152': 'DK-11241 / DK-1241 (102 x 152 mm die-cut)',
    '103x164': 'DK-11247 / DK-1247 (103 x 164 mm die-cut)',
    'd12': 'DK-11219 / DK-1219 (12 mm round)',
    'd24': 'DK-11218 / DK-1218 (24 mm round)',
    'd58': 'DK-11207 / DK-1207 (58 mm round)',
    # P-touch TZe tapes, the codes depend on the colour (e.g. TZe-231 is
    # black on white 12 mm). "18" was added to brother_ql for the PT-P950NW.
    '18': 'TZe tape (18 mm, PT-P900 series)',
    'pt12': 'TZe tape (12 mm)',
    'pt18': 'TZe tape (18 mm)',
    'pt24': 'TZe tape (24 mm)',
    'pt36': 'TZe tape (36 mm)',
}

# Label sizes for P-touch printers. brother_ql does not restrict them to
# these printers, but QL printers cannot use TZe tapes (and P-touch printers
# no DK rolls).
PTOUCH_LABELS = {'18', 'pt12', 'pt18', 'pt24', 'pt36'}


def label_name(label: Label) -> str:
    """Name of a label size, brother_ql's own name for sizes not listed here."""
    return LABEL_NAMES.get(label.identifier, label.name)
