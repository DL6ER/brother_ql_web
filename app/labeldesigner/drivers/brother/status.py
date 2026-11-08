"""The status of a Brother raster printer, the 32 bytes it answers "ESC i S"
with over USB and SNMP, after the reader of brother_ql by Philipp Klaus and
Brother's raster command references."""

from .models import MODELS

HEADER = b'\x80\x20\x42'
LENGTH = 32

ERRORS_1 = {
    0: 'No media when printing',
    1: 'End of media (die-cut size only)',
    2: 'Tape cutter jam',
    3: 'Weak batteries',
    4: 'Main unit in use (QL-560/650TD/1050)',
    5: 'Printer turned off',
    6: 'High-voltage adapter (not used)',
    7: "Fan doesn't work (QL-1050/1060N)",
}

ERRORS_2 = {
    0: 'Replace media error',
    1: 'Expansion buffer full error',
    2: 'Transmission / Communication error',
    3: 'Communication buffer full error (not used)',
    4: 'Cover opened while printing (Except QL-500)',
    5: 'Cancel key (not used) or Overheating error (PT-E550W/P750W/P710BT)',
    6: 'Media cannot be fed (also when the media end is detected)',
    7: 'System error',
}

MEDIA_TYPES = {
    0x00: ('No media', 'No media'),
    0x01: ('Laminated tape', 'TZe'),
    0x03: ('Non-laminated type', 'TZe'),
    0x11: ('Heat-Shrink Tube (HS 2:1)', 'TZe'),
    0x17: ('Heat-Shrink Tube (HS 3:1)', 'TZe'),
    0x0A: ('Continuous length tape', 'DK'),
    0x0B: ('Die-cut labels', 'DK'),
    0x4A: ('Continuous length tape', 'RD'),
    0x4B: ('Die-cut labels', 'RD'),
    0xFF: ('Incompatible tape', 'Incompatible'),
}

TAPE_COLORS = {
    0x01: 'White', 0x02: 'Other', 0x03: 'Clear', 0x04: 'Red', 0x05: 'Blue', 0x06: 'Yellow', 0x07: 'Green',
    0x08: 'Black', 0x09: 'Clear(White text)', 0x20: 'Matte White', 0x21: 'Matte Clear', 0x22: 'Matte Silver',
    0x23: 'Satin Gold', 0x24: 'Satin Silver', 0x30: 'Blue(D)', 0x31: 'Red(D)', 0x40: 'Fluorescent Orange',
    0x41: 'Fluorescent Yellow', 0x50: 'Berry Pink(S)', 0x51: 'Light Gray(S)', 0x52: 'Lime Green(S)',
    0x60: 'Yellow(F)', 0x61: 'Pink(F)', 0x62: 'Blue(F)', 0x70: 'White(Heat-shrink Tube)', 0x90: 'White(Flex. ID)',
    0x91: 'Yellow(Flex. ID)', 0xF0: 'Cleaning', 0xF1: 'Stencil', 0xFF: 'Incompatible',
}

TEXT_COLORS = {
    0x01: 'White', 0x02: 'Other', 0x04: 'Red', 0x05: 'Blue', 0x08: 'Black', 0x0A: 'Gold', 0x62: 'Blue(F)',
    0xF0: 'Cleaning', 0xF1: 'Stencil', 0xFF: 'Incompatible',
}

STATUS_TYPES = {
    0x00: 'Reply to status request',
    0x01: 'Printing completed',
    0x02: 'Error occurred',
    0x03: 'Exit IF mode',
    0x04: 'Turned off',
    0x05: 'Notification',
    0x06: 'Phase change',
    0xF0: 'Settings report',
}

PHASE_TYPES = {
    0x00: 'Waiting to receive',
    0x01: 'Printing state',
}


class StatusError(ValueError):
    """An answer that is no status the printer could have sent."""


def parse_status(data: bytes) -> dict:
    """The status in an answer of the printer: its model, the loaded media,
    the errors, the status and phase type. Raises StatusError for an answer
    that is no status or has values that are not known."""
    data = bytes(data)
    if len(data) < LENGTH or not data.startswith(HEADER):
        raise StatusError(f'No status: {data.hex()}')
    errors = [message for bit, message in ERRORS_1.items() if data[8] & (1 << bit)]
    errors += [message for bit, message in ERRORS_2.items() if data[9] & (1 << bit)]
    if data[11] not in MEDIA_TYPES:
        raise StatusError(f'Unknown media type {data[11]:02X}')
    if data[18] not in STATUS_TYPES:
        raise StatusError(f'Unknown status type {data[18]:02X}')
    media_type, media_category = MEDIA_TYPES[data[11]]
    tape_color = text_color = ''
    if media_category == 'TZe':
        if data[24] not in TAPE_COLORS or data[25] not in TEXT_COLORS:
            raise StatusError(f'Unknown tape colors {data[24]:02X} {data[25]:02X}')
        tape_color, text_color = TAPE_COLORS[data[24]], TEXT_COLORS[data[25]]
    series_code, model_code = data[3], data[4]
    model = next((model.identifier for model in MODELS.values()
                  if model.series_code == series_code and model.model_code == model_code), 'Unknown')
    return {
        'series_code': series_code,
        'model_code': model_code,
        'model': model,
        'status_type': STATUS_TYPES[data[18]],
        'status_code': data[18],
        # An unknown phase stays a number
        'phase_type': PHASE_TYPES.get(data[19], data[19]),
        'media_type': media_type,
        'media_category': media_category,
        'tape_color': tape_color,
        'text_color': text_color,
        'media_width': data[10],
        'media_length': data[17],
        'setting': data[30] if data[18] == 0xF0 else None,
        'errors': errors,
    }
