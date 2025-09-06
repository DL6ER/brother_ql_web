from enum import Enum, auto


class LabelContent(Enum):
    TEXT_ONLY = auto()
    QRCODE_ONLY = auto()
    TEXT_QRCODE = auto()
    IMAGE_BW = auto()
    IMAGE_GRAYSCALE = auto()
    IMAGE_RED_BLACK = auto()
    IMAGE_COLORED = auto()


class LabelOrientation(Enum):
    STANDARD = auto()
    ROTATED = auto()


class LabelType(Enum):
    ENDLESS_LABEL = auto()
    DIE_CUT_LABEL = auto()
    ROUND_DIE_CUT_LABEL = auto()
