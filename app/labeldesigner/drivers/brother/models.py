"""The Brother printers that speak the raster protocol, and what each of them
can do, after the model table of brother_ql by Philipp Klaus."""

from dataclasses import dataclass


@dataclass(frozen=True)
class Model:
    """A printer model: the bytes of a raster line across its print head, the
    dots its print is moved further off the right edge, the commands it takes
    (mode setting, cutting, expanded mode, compression), whether it prints
    red, the zero bytes that clear its command buffer, and the codes it gives
    in its status (series and model) and on USB (product). Its longest label
    has max_length_dots lines at dpi."""
    identifier: str
    max_length_dots: int = 11811
    dpi: int = 300
    number_bytes_per_row: int = 90
    additional_offset_r: int = 0
    mode_setting: bool = True
    cutting: bool = True
    expanded_mode: bool = True
    compression_support: bool = True
    two_color: bool = False
    num_invalidate_bytes: int = 200
    series_code: int = 0xFFFF
    model_code: int = 0xFFFF
    product_id: int = 0xFFFF


MODELS = {model.identifier: model for model in (
    Model('QL-500', mode_setting=False, cutting=False, expanded_mode=False, compression_support=False,
          series_code=0x30, model_code=0x4F, product_id=0x2015),
    Model('QL-550', mode_setting=False, compression_support=False, series_code=0x30, model_code=0x4F, product_id=0x2016),
    Model('QL-560', mode_setting=False, compression_support=False, series_code=0x34, model_code=0x31, product_id=0x2027),
    Model('QL-570', mode_setting=False, compression_support=False, series_code=0x34, model_code=0x32, product_id=0x2028),
    Model('QL-580N', series_code=0x34, model_code=0x33, product_id=0x2029),
    Model('QL-600', series_code=0x34, model_code=0x47, product_id=0x20C0),
    Model('QL-650TD', series_code=0x30, model_code=0x51, product_id=0x201B),
    Model('QL-700', mode_setting=False, compression_support=False, series_code=0x34, model_code=0x35, product_id=0x2042),
    Model('QL-710W', series_code=0x34, model_code=0x36, product_id=0x2043),
    Model('QL-720NW', series_code=0x34, model_code=0x37, product_id=0x2044),
    Model('QL-800', compression_support=False, two_color=True, num_invalidate_bytes=400,
          series_code=0x34, model_code=0x38, product_id=0x209B),
    Model('QL-810W', two_color=True, num_invalidate_bytes=400, series_code=0x34, model_code=0x39, product_id=0x209C),
    Model('QL-820NWB', two_color=True, num_invalidate_bytes=400, series_code=0x34, model_code=0x41, product_id=0x209D),
    Model('QL-1050', max_length_dots=35433, number_bytes_per_row=162, additional_offset_r=44, series_code=0x30, model_code=0x50, product_id=0x2020),
    Model('QL-1060N', max_length_dots=35433, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x34, product_id=0x202A),
    Model('QL-1100', max_length_dots=35434, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x43, product_id=0x20A7),
    Model('QL-1110NWB', max_length_dots=35434, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x44, product_id=0x20A8),
    Model('QL-1115NWB', max_length_dots=35434, number_bytes_per_row=162, additional_offset_r=44, series_code=0x34, model_code=0x45, product_id=0x20AB),
    # Model codes after Brother's raster command reference of the PT-E550W,
    # PT-P750W and PT-P710BT: 66h is the PT-E550W, 68h the PT-P750W
    # https://download.brother.com/welcome/docp100064/cv_pte550wp750wp710bt_eng_raster_102.pdf
    Model('PT-E550W', max_length_dots=14172, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x66, product_id=0x2060),
    Model('PT-P700', max_length_dots=7086, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x67, product_id=0x2061),
    Model('PT-P750W', max_length_dots=7086, dpi=180, number_bytes_per_row=16, series_code=0x30, model_code=0x68, product_id=0x2062),
    Model('PT-P900W', max_length_dots=28346, dpi=360, number_bytes_per_row=70, series_code=0x30, model_code=0x69, product_id=0x2085),
    Model('PT-P950NW', max_length_dots=28346, dpi=360, number_bytes_per_row=70, series_code=0x30, model_code=0x70, product_id=0x2086),
)}

# The QL-1100 series prints at 300 dpi only. Brother's raster command
# reference of these models lists no 600 dpi resolution, unlike that of the
# QL-800, and a 600 dpi label came out twice as long on a QL-1100 (#103):
# https://download.brother.com/welcome/docp100366/cv_ql1100_eng_raster_100.pdf
# The P-touch printers print at 180 dpi, their reference has no 600 dpi.
MODELS_WITHOUT_600_DPI = ('QL-1100', 'QL-1110NWB', 'QL-1115NWB', 'PT-E550W', 'PT-P700', 'PT-P750W')

# P-touch printers with a half cutter, the PT-P700 has none
HALF_CUTTERS = ('PT-E550W', 'PT-P750W', 'PT-P900W', 'PT-P950NW')

# Brother's raster command references: a print of at least 12.7 mm (150
# dots) on QL printers, 4.4 mm (31 dots at 180 dpi) on TZe tape
MIN_PRINT_LENGTH = 12.7
MIN_PTOUCH_PRINT_LENGTH = 4.4


def longest_label(model: str) -> int:
    """The longest label the model prints, in whole mm."""
    spec = MODELS[model]
    return round(spec.max_length_dots / spec.dpi * 25.4)


def length_problem(model: str) -> str:
    """Why a label is too long for the model."""
    return f'Labels on the {model} can be at most {longest_label(model)} mm long'


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    if high_res and model in MODELS_WITHOUT_600_DPI:
        return f'The {model} cannot print at 600 dpi, turn off 600 dpi printing'
    return ''
