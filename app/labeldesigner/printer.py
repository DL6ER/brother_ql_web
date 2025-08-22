import logging
from brother_ql.backends.helpers import send
from brother_ql import BrotherQLRaster, create_label
from .label import LabelOrientation, LabelType, LabelContent

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

    def add_label_to_queue(self, label, count, cut_once=False):
        for cnt in range(0, count):
            cut = (cut_once == False) or (cut_once and cnt == count-1)

            self._printQueue.append(
                {'label': label,
                 'cut': cut
                 })

    def process_queue(self):
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
                rotate=rotate)

        info = send(qlr.data, self._device_specifier)
        logger.info('Sent %d bytes to printer %s', len(qlr.data), self._device_specifier)
        logger.info('Printer response: %s', str(info))
