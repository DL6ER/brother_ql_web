import logging
from brother_ql.backends.helpers import send
from brother_ql import BrotherQLRaster, create_label
from brother_ql.backends.helpers import get_printer, get_status
from .label import LabelOrientation, LabelType, LabelContent
from brother_ql.models import ALL_MODELS

logger = logging.getLogger(__name__)

class PrinterQueue:

    _printQueue = []
    _cutQueue = []

    def __init__(
            self,
            model,
            device_specifier,
            label_size):
        self.model = model
        self.device_specifier = device_specifier
        self.label_size = label_size

    @property
    def model(self):
        return self._model

    @model.setter
    def model(self, value):
        self._model = value

    @property
    def device_specifier(self):
        return self._device_specifier

    @device_specifier.setter
    def device_specifier(self, value):
        self._device_specifier = value

    @property
    def label_size(self):
        return self._label_size

    @label_size.setter
    def label_size(self, value):
        self._label_size = value

    def add_label_to_queue(self, label, count, cut_once=False, high_res: bool = False):
        for cnt in range(0, count):
            cut = (cut_once == False) or (cut_once and cnt == count-1)

            self._printQueue.append(
                {'label': label,
                 'cut': cut,
                 'high_res': high_res
                 })

    def process_queue(self) -> bool:
        qlr = BrotherQLRaster(self._model)

        for queue_entry in self._printQueue:
            if queue_entry['label'].label_type == LabelType.ENDLESS_LABEL:
                if queue_entry['label'].label_orientation == LabelOrientation.STANDARD:
                    rotate = 0
                else:
                    rotate = 90
            else:
                rotate = 'auto'

            img = queue_entry['label'].generate(rotate=False)

            if queue_entry['label'].label_content == LabelContent.IMAGE_BW:
                dither = False
            else:
                dither = True

            create_label(
                qlr,
                img,
                self.label_size,
                red='red' in self.label_size,
                dither=dither,
                cut=queue_entry['cut'],
                dpi_600=queue_entry['high_res'],
                rotate=rotate)

        self._printQueue.clear()

        info = send(qlr.data, self._device_specifier)
        logger.info('Sent %d bytes to printer %s', len(qlr.data), self._device_specifier)
        logger.info('Printer response: %s', str(info))
        
        if info['did_print'] and info['ready_for_next_job']:
            logger.info('Label printed successfully and printer is ready for next job')
            return True

        logger.warning("Failed to print label")
        return False

def get_ptr_status(device_specifier):
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
        printer = get_printer(device_specifier)
        printer_state = get_status(printer)
        for key in printer_state:
            status[key] = printer_state.get(key, status[key])
            if key == 'model':
                status['red_support'] = printer_state['model'] in [model.identifier for model in ALL_MODELS if model.two_color]
        return status
    except Exception as e:
        logger.exception(e)
        status['errors'] = [str(e)]
        return status
