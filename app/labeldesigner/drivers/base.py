"""The interface of a printer driver.

A driver is a package below drivers/ for one protocol, e.g. Brother raster
for the QL and P-touch printers. Its __init__.py has a Driver as DRIVER, and
drivers/__init__.py lists it. The label designer, the API and the printer
detection use printers only through this interface."""


def network_status(device: str, model: str, red_support: bool = False) -> dict:
    """Status of a network printer that tells nothing about itself."""
    return {
        'errors': [],
        'path': device,
        'media_category': None,
        'media_length': 0,
        'media_type': None,
        'media_width': None,
        'model': model,
        'model_code': None,
        'phase_type': 'Network Printer',
        'series_code': None,
        'setting': None,
        'status_code': 0,
        'status_type': 'Network Printer',
        'tape_color': '',
        'text_color': '',
        'red_support': red_support,
    }


def shown(model: str) -> str:
    """A model as texts name it, PUQU Q00 for PUQU/Q00."""
    return model.replace('/', ' ')


class Driver:
    """The printers of one protocol: their models and label sizes, what a
    label looks like on them, their status, and printing."""

    # The maker of its printers, which the label designer names before the
    # model
    vendor: str = ''
    # Printer models, as PRINTER_MODEL, PRINTERS and requests name them:
    # <maker>/<model>, Brother ones without the maker
    models: tuple = ()
    # Label sizes (labels.Label), in the order the label designer
    # lists them, and the names it shows for them
    labels: tuple = ()
    label_names: dict = {}
    # Label sizes the label designer shows only while they are selected
    hidden_labels: frozenset = frozenset()
    # Regular expressions for the addresses of its printers a request may
    # name besides the configured ones
    device_patterns: tuple = ()
    # Exceptions that mean that a printer did not answer
    errors: tuple = (OSError,)
    # The messages it refuses a job with, as they are shown to the user
    refusals: tuple = ()
    # MAC address prefixes of its network printers, for those found in the
    # ARP table that do not announce themselves
    mac_address_prefixes: tuple = ()

    def model_of(self, device: str) -> str | None:
        """The model of a printer known by its address or device ID alone,
        e.g. vc500w://<host>, whatever model is configured. None if the
        address does not tell."""
        return None

    def fits(self, label, model: str) -> bool:
        """Whether the model prints the label size, one of self.labels."""
        raise NotImplementedError

    def colors(self, model: str) -> str:
        """What the model prints: 'black', 'red' (black and red) or 'color'."""
        return 'black'

    def palette(self, label) -> str:
        """What a label size shows: 'black', 'red' or 'color'."""
        return 'black'

    def dpi(self, high_res: bool) -> int:
        """The resolution labels are made with."""
        raise NotImplementedError

    def label_dpi(self, label) -> tuple[int, int]:
        """Dots per inch of the dots of a label size, printed normally and at
        600 dpi. The label designer converts millimeters with them."""
        return self.dpi(False), self.dpi(True)

    def length_rules(self, label) -> tuple[float, float, float] | None:
        """For an endless label of the size of a fixed length, in mm: the
        paper fed before and after its print, each, and the shortest and the
        longest print. None where labels are as long as their content."""
        return None

    def capabilities(self, model: str) -> dict:
        """What the label designer offers for the model: the qualities it
        shows (fast, quality, high for 600 dpi), whether the model prints at
        600 dpi, whether it has a cutter, else whether its labels are cut
        off by hand with a lever, half cuts between labels and chain prints,
        whether it feeds tape before the first label, reports the stage of a
        job it prints, its colors, why it does not leave labels uncut, if it
        does not, whether labels leave it top first, the label designer then
        shows the printer below them, the blank paper in mm before each label
        that is cut, which was between printhead and cutter, and the maker of
        the model."""
        return {'qualities': ['fast', 'quality'], 'high_res': False, 'cutter': True, 'manual_cutter': False,
                'half_cut': False, 'chain': False, 'feed_waste': False, 'job_stages': False, 'colors': self.colors(model),
                'never_cut': '', 'leaves_turned': False, 'lead_mm': 0, 'vendor': self.vendor}

    def resolution_problem(self, model: str, high_res: bool) -> str:
        """Why the model cannot print at the resolution, or an empty string."""
        return ''

    def cut_problem(self, cut: str, model: str) -> str:
        """Why the model cannot cut labels this way (each, last or none), or
        an empty string."""
        return ''

    def status(self, device: str, model: str, unconfirmed: bool = False) -> dict | None:
        """Status of a printer, with the keys of /api/printer_status. With
        unconfirmed, a device found by its MAC address only, None if it is no
        printer of this driver. Raises one of self.errors if it does not
        answer."""
        raise NotImplementedError

    def announced(self, service: dict) -> tuple[str, str] | None:
        """(address, model) of a printer of this driver that announces itself
        on the network as mdns.discover() found it, None for other devices."""
        return None

    def network_device(self, address: str) -> str:
        """The address of a printer found by its MAC address."""
        raise NotImplementedError

    def render(self, queue, entries: list, keep=None):
        """The job for the labels of the queue, as send() takes it. entries:
        the labels of the queue with their settings and whether they are cut
        after them. keep(image, name), on the simulator, gets every label as
        it would be printed."""
        raise NotImplementedError

    def send(self, queue, job) -> str:
        """Print the job on the printer of the queue, which holds its device.
        An empty string on success, else a message for the user."""
        raise NotImplementedError
