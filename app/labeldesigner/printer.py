import logging
from brother_ql.backends.helpers import send
from brother_ql import BrotherQLRaster, create_label
from brother_ql.backends.helpers import get_printer, get_status
from flask import Config
from .label import LabelOrientation, LabelType, LabelContent
from brother_ql.models import ALL_MODELS

logger = logging.getLogger(__name__)


class PrinterQueue:
    def __init__(self, model, device_specifier, label_size):
        self.model = model
        self.device_specifier = device_specifier
        self.label_size = label_size
        self._print_queue = []

    def add_label_to_queue(self, label, cut: bool = True, high_res: bool = False):
        self._print_queue.append({
            'label': label,
            'cut': cut,
            'high_res': high_res
        })

    def process_queue(self, offline: bool = False) -> bool:
        if not self._print_queue:
            logger.warning("Print queue is empty.")
            return False
        qlr = BrotherQLRaster(self.model)
        for entry in self._print_queue:
            label = entry['label']
            cut = entry['cut']
            high_res = entry['high_res']
            if label.label_type == LabelType.ENDLESS_LABEL:
                rotate = 0 if label.label_orientation == LabelOrientation.STANDARD else 90
            else:
                rotate = 'auto'
            img = label.generate(rotate=False)
            dither = label.label_content != LabelContent.IMAGE_BW
            create_label(
                qlr,
                img,
                self.label_size,
                red='red' in str(self.label_size),
                dither=dither,
                cut=cut,
                dpi_600=high_res,
                rotate=rotate
            )
        self._print_queue.clear()
        try:
            if offline:
                logger.warning("Printer is offline. Skipping actual printing.")
                return True
            else:
                info = send(qlr.data, self.device_specifier)
                logger.info('Sent %d bytes to printer %s', len(qlr.data), self.device_specifier)
                logger.info('Printer response: %s', str(info))
                if info.get('did_print') and info.get('ready_for_next_job'):
                    logger.info('Label printed successfully and printer is ready for next job')
                    return True
                logger.warning("Failed to print label")
            return False
        except Exception as e:
            logger.exception("Exception during sending to printer: %s", e)
            return False


def get_ptr_status(config: Config):
    device_specifier = config['PRINTER_PRINTER']
    default_model = config['PRINTER_MODEL']
    printer_offline = config['PRINTER_OFFLINE']
    status = {
        "errors": [],
        "path": device_specifier,
        "media_category": None,
        "media_length": 0,
        "media_type": None,
        "media_width": None,
        "model": "Unknown",
        "model_code": None,
        "phase_type": "Unknown",
        "series_code": None,
        "setting": None,
        "status_code": 0,
        "status_type": "Unknown",
        "tape_color": "",
        "text_color": "",
        "red_support": False
    }
    try:
        if printer_offline:
            status['model'] = default_model
            status['status_type'] = 'Offline'
        else:
            printer = get_printer(device_specifier)
            printer_state = get_status(printer)
            for key, value in printer_state.items():
                status[key] = value
        status['red_support'] = status['model'] in [model.identifier for model in ALL_MODELS if model.two_color]
        return status
    except Exception as e:
        logger.exception("Printer status error: %s", e)
        status['errors'] = [str(e)]
        return status
