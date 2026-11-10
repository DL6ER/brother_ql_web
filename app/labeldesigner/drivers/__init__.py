"""The printer drivers, see base.py, and which one drives a printer."""

from .base import Driver
from .brother import DRIVER as BROTHER
from .vc500w import DRIVER as VC500W
from .puqu import DRIVER as PUQU
from .niimbot import DRIVER as NIIMBOT
from .escpos import DRIVER as ESCPOS
from .zpl import DRIVER as ZPL

# In the order the label designer lists their models and label sizes
DRIVERS: tuple[Driver, ...] = (BROTHER, VC500W, PUQU, NIIMBOT, ESCPOS, ZPL)

MODELS = tuple(model for driver in DRIVERS for model in driver.models)
LABELS = tuple(label for driver in DRIVERS for label in driver.labels)
DEVICE_PATTERNS = tuple(pattern for driver in DRIVERS for pattern in driver.device_patterns)

_BY_MODEL = {model: driver for driver in DRIVERS for model in driver.models}
_BY_LABEL = {label.identifier: driver for driver in DRIVERS for label in driver.labels}
# Models other than Brother ones are named with their maker, e.g. PUQU/Q00,
# Brother ones also are, and without it as before. Q00 alone was its name
# before, it is taken for a while.
_ALIASES = {f'{driver.vendor}/{model}': model for driver in (BROTHER, VC500W) for model in driver.models}
_ALIASES['Q00'] = 'PUQU/Q00'


def canonical(model: str) -> str:
    """A model as the drivers name it, from a configuration or a request:
    Brother/QL-700 is QL-700."""
    return _ALIASES.get(model, model) if isinstance(model, str) else model


def for_model(model: str) -> Driver:
    """The driver of a model. Unknown models are left to the Brother driver."""
    return _BY_MODEL.get(model, BROTHER)


def for_label(label_size: str) -> Driver | None:
    """The driver whose printers print a label size, None if none does."""
    return _BY_LABEL.get(label_size)


def resolution_problem(model: str, high_res: bool) -> str:
    """Why the model cannot print at the resolution, or an empty string."""
    return for_model(model).resolution_problem(model, high_res)


def resolve(device: str, model: str) -> tuple[Driver, str]:
    """The driver and the model of a printer: the one its address or device
    ID tells, else the configured or requested model."""
    for driver in DRIVERS:
        known = driver.model_of(device)
        if known:
            return driver, known
    return for_model(model), model
