"""Names of the label sizes in the label designer.

brother_ql names its label definitions by size only, and several share the
same name. The names here add Brother's product codes after the size, so
that users can compare them with the code on their roll. The European codes
have five digits, the US, Canadian and Japanese codes four (DK-22205 /
DK-2205).

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
    '12': '12 mm endless (DK-22214 / DK-2214)',
    '29': '29 mm endless (DK-22210 / DK-2210)',
    '38': '38 mm endless (DK-22225 / DK-2225)',
    '50': '50 mm endless (DK-22223 / DK-2223)',
    '54': '54 mm endless, non-adhesive (DK-N55224 / DK-N5224)',
    '62': '62 mm endless (DK-22205 / DK-2205)',
    '62red': '62 mm endless, black/red/white (DK-22251 / DK-2251)',
    '102': '102 mm endless (DK-22243 / DK-2243)',
    # The same roll in two printer geometries, brother_ql tested "103" on the
    # QL-1100 and "104" on the QL-1050. The label designer shows one of them.
    '103': '103 mm endless (DK-22246 / DK-2246)',
    '104': '103 mm endless (DK-22246 / DK-2246)',
    # Not a Brother product, added to brother_ql without documentation
    '12+17': '12 mm endless (12+17)',
    # Die-cut DK labels
    '17x54': '17 x 54 mm die-cut (DK-11204 / DK-1204)',
    '17x87': '17 x 87 mm die-cut (DK-11203 / DK-1203)',
    '23x23': '23 x 23 mm die-cut (DK-11221 / DK-1221)',
    '29x42': '29 x 42 mm die-cut (DK-1215, Japan)',
    '29x90': '29 x 90 mm die-cut (DK-11201 / DK-1201)',
    '39x90': '38 x 90 mm die-cut (DK-11208 / DK-1208)',
    '39x48': '39 x 48 mm die-cut (DK-1220, Japan)',
    '52x29': '52 x 29 mm die-cut (DK-1226, Japan)',
    '54x29': '54 x 29 mm die-cut, removable (DK-3235)',
    '60x86': '60 x 86 mm die-cut (DK-11234 / DK-1234)',
    '62x29': '62 x 29 mm die-cut (DK-11209 / DK-1209)',
    '62x100': '62 x 100 mm die-cut (DK-11202 / DK-1202)',
    '102x51': '102 x 51 mm die-cut (DK-11240 / DK-1240)',
    '102x152': '102 x 152 mm die-cut (DK-11241 / DK-1241)',
    '103x164': '103 x 164 mm die-cut (DK-11247 / DK-1247)',
    'd12': '12 mm round (DK-11219 / DK-1219)',
    'd24': '24 mm round (DK-11218 / DK-1218)',
    'd58': '58 mm round (DK-11207 / DK-1207)',
    # P-touch TZe tapes, the codes depend on the colour (e.g. TZe-231 is
    # black on white 12 mm). "18" was added to brother_ql for the PT-P950NW.
    '18': '18 mm (TZe tape, PT-P900 series)',
    'pt12': '12 mm (TZe tape)',
    'pt18': '18 mm (TZe tape)',
    'pt24': '24 mm (TZe tape)',
    'pt36': '36 mm (TZe tape)',
}

# Label sizes for P-touch printers. brother_ql does not restrict them to
# these printers, but QL printers cannot use TZe tapes (and P-touch printers
# no DK rolls).
PTOUCH_LABELS = {'18', 'pt12', 'pt18', 'pt24', 'pt36'}


def label_name(label: Label) -> str:
    """Name of a label size, brother_ql's own name for sizes not listed here."""
    return LABEL_NAMES.get(label.identifier, label.name)
