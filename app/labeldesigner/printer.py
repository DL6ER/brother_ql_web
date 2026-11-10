"""Printing and the printers: the print queue, the simulator and the
detection of printers. What depends on the protocol of a printer is in its
driver (drivers/)."""

import concurrent.futures
import contextlib
import datetime
import logging
import os
import threading
import time
from urllib.parse import urlsplit

from flask import Config

from . import drivers, mdns
from .drivers import devices
from .label_names import check_label_size

SIMULATED_LABELS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'simulated_labels')

# Number of simulated labels kept in SIMULATED_LABELS_DIR, older ones are
# deleted so that printing to the simulator cannot fill up the disk
MAX_SIMULATED_LABELS = 100

logger = logging.getLogger(__name__)

# Where printers are looked for when PRINTER_PRINTER is '?'. The tests point
# them elsewhere, so that they never find, let alone print on, a real printer.
USB_PRINTER_DEVICES = [f'/dev/usb/lp{i}' for i in range(0, 11)]
ARP_TABLE = '/proc/net/arp'
# Network printers that announce themselves (DNS-SD, as for Bonjour and
# CUPS). Asking takes a second, the answer is kept for a minute.
NETWORK_DISCOVERY = True
DISCOVERY_INTERVAL = 60
# (time.monotonic() of the last discovery, printers found), None before the
# first. The monotonic clock starts at boot, so a time of 0 would count as
# recent in a container started less than a minute ago.
_discovered = None


def configured_printers(config) -> list[tuple[str, str, str]]:
    """The printers of PRINTERS as (model, device, name), the name is empty
    for a printer without one."""
    return [(drivers.canonical(entry[0]), entry[1], entry[2] if len(entry) > 2 else '') for entry in config.get('PRINTERS') or []]


def is_simulation(device_specifier) -> bool:
    return isinstance(device_specifier, str) and device_specifier in ['simulation', '?']


def _prune_simulated_labels():
    # The names start with a timestamp, so sorting by name sorts by age
    labels = sorted((entry for entry in os.scandir(SIMULATED_LABELS_DIR) if entry.name.endswith('.png')), key=lambda entry: entry.name)
    for entry in labels[:-MAX_SIMULATED_LABELS]:
        try:
            os.remove(entry.path)
        except OSError:
            logger.debug('Failed to remove simulated label %s', entry.path, exc_info=True)


class _Simulator:
    """Keeps the labels of a job printed on the simulator as PNG files."""

    def __init__(self):
        os.makedirs(SIMULATED_LABELS_DIR, exist_ok=True)
        self._ts = datetime.datetime.now(datetime.UTC).strftime('%Y%m%d_%H%M%S_%f')

    def __call__(self, image, name: str):
        path = os.path.join(SIMULATED_LABELS_DIR, f'{self._ts}_{name}.png')
        image.save(path, format='PNG')
        logger.info('Saved simulated label to %s', path)


class PrinterQueue:
    def __init__(self, model, device_specifier, label_size):
        # A printer known by its address or device ID is that model
        self.driver, model = drivers.resolve(device_specifier, model)
        # A clear message instead of "Wrong pixel width" from the raster commands
        check_label_size(label_size, model)
        self.model = model
        self.device_specifier = device_specifier
        self.label_size = label_size
        # Cut after each label, the last one or none (see routes._cutting),
        # on PT printers and the VC-500W half cut between labels not cut, on
        # PT printers chain printing
        self.cut = 'each'
        self.half_cut = True
        self.chain = False
        self._print_queue = []

    def add_label_to_queue(self, label, high_res: bool = False, fast: bool = False):
        problem = self.driver.resolution_problem(self.model, high_res)
        if problem:
            raise ValueError(problem)
        self._print_queue.append({
            'label': label,
            'high_res': high_res,
            'fast': fast
        })

    def label_cuts(self) -> list[bool]:
        """Whether the printer cuts after each label of the queue, as
        self.cut says: after each one, the last one or none."""
        last = len(self._print_queue) - 1
        return [self.cut == 'each' or (self.cut == 'last' and i == last) for i in range(last + 1)]

    def process_queue(self) -> str:
        """Print the labels of the queue as one job."""
        if not self._print_queue:
            logger.warning("Print queue is empty.")
            return "Print queue is empty."

        entries = [dict(entry, cut=cut) for entry, cut in zip(self._print_queue, self.label_cuts())]
        self._print_queue.clear()

        # Hold the device for the whole job so that neither status queries nor
        # other jobs get in between
        simulation = is_simulation(self.device_specifier)
        lock = contextlib.nullcontext(True) if simulation else devices.device_lock(self.device_specifier)
        with lock as acquired:
            if not acquired:
                logger.warning('Printer %s is busy with another job', self.device_specifier)
                return "Printer is busy with another print job, please try again"
            logger.info('Printing %d labels', len(entries))
            job = self.driver.render(self, entries, _Simulator() if simulation else None)
            if simulation:
                _prune_simulated_labels()
                return ''
            return self.driver.send(self, job)


def _discovered_label_printers() -> list[tuple[str, str]]:
    """Label printers that announce themselves on the network, as (address,
    model), e.g. tcp://<address>[:<port>] or vc500w://<address>. Kept for
    DISCOVERY_INTERVAL."""
    global _discovered
    if not NETWORK_DISCOVERY:
        return []
    if _discovered is not None and time.monotonic() - _discovered[0] < DISCOVERY_INTERVAL:
        return _discovered[1]
    printers = []
    for service in mdns.discover():
        found = next((found for driver in drivers.DRIVERS if (found := driver.announced(service))), None)
        if found and all(urlsplit(known).hostname != service['address'] for known, _ in printers):
            printers.append(found)
    _discovered = (time.monotonic(), printers)
    return printers


def _red_support(model: str) -> bool:
    return drivers.for_model(model).colors(model) == 'red'


_last_scan_ts = 0
_cached_printers = []


# The configured printers are asked at the same time, the answer waits at most
# STATUS_QUERY_WAIT seconds for them. The .local name of a PT-E550W that was off
# took 10 s not to resolve, the label designer showed nothing meanwhile. A
# query still running is not started again; its printer is told as it was
# last, or as being asked. Its answer is told next time: a PUQU Q00 took 4 s
# and more to connect over Bluetooth, it was never told as ready otherwise.
STATUS_QUERY_WAIT = 4.0
ASKING = 'Asking the printer'
_status_pool = concurrent.futures.ThreadPoolExecutor(max_workers=8, thread_name_prefix='printer-status')
_status_queries = {}
_status_queries_lock = threading.Lock()
# The last answer of each printer, or why it did not answer, by device
_last_states = {}


def _query_configured(model: str, device: str) -> dict:
    driver, known = drivers.resolve(device, model)
    return driver.status(device, known)


def _answer(future, model: str, device: str) -> dict:
    """The state a finished status query got, also when the printer did not answer."""
    try:
        return future.result()
    except Exception as e:
        logger.info('Status query of %s failed: %s', device, e)
        return devices.failed_printer_state(device, e, model)


def _configured_states(printers) -> list[dict]:
    """The status of each configured (model, device), in their order."""
    with _status_queries_lock:
        futures = []
        for model, device in printers:
            if device == 'simulation':
                futures.append(None)
                continue
            future = _status_queries.get(device)
            if future is not None and future.done():
                # It answered after the last status was told
                _last_states[device] = _answer(future, model, device)
                future = None
            if future is None:
                future = _status_pool.submit(_query_configured, model, device)
                _status_queries[device] = future
            futures.append(future)
    concurrent.futures.wait([f for f in futures if f], timeout=STATUS_QUERY_WAIT)
    states = []
    for (model, device), future in zip(printers, futures):
        if future is None:
            states.append(None)
        elif future.done():
            _last_states[device] = _answer(future, model, device)
            states.append(_last_states[device])
        elif device in _last_states:
            states.append(dict(_last_states[device]))
        else:
            asking = devices.failed_printer_state(device, TimeoutError(), model)
            states.append(dict(asking, errors=[], status_type=ASKING))
    return states


def get_ptr_status(config: Config):
    # Simple in-memory cache for detected printers
    global _last_scan_ts, _cached_printers

    device_specifier = config['PRINTER_PRINTER']
    default_model = drivers.canonical(config['PRINTER_MODEL'])

    SIMULATOR_PRINTER = {
        'errors': [],
        'path': 'simulation',
        'media_category': None,
        'media_length': 0,
        'media_type': None,
        'media_width': None,
        'model': default_model,
        'model_code': None,
        'phase_type': 'Simulator',
        'series_code': None,
        'setting': None,
        'status_code': 0,
        'status_type': 'Simulator',
        'tape_color': '',
        'text_color': '',
        'red_support': _red_support(default_model)
    }

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
        if config.get('PRINTERS'):
            # The configured printers, no detection. Each keeps its configured
            # model, a failed query must not move it to another printer. The
            # simulator is listed only when configured, e.g. QL-700=simulation.
            printers = []
            listed = configured_printers(config)
            for (model, device, name), state in zip(listed, _configured_states([(m, d) for m, d, _ in listed])):
                state = state or SIMULATOR_PRINTER.copy()
                state.update({'path': device, 'model': model, 'name': name})
                state['red_support'] = _red_support(model)
                printers.append(state)
            return {
                **status,
                **printers[0],
                'printers': printers,
                'selected': printers[0]['path'],
            }
        # If device_specifier is the default '?', try to auto-detect multiple printers
        if device_specifier == '?':
            now = time.time()
            # Refresh cache every 10 seconds
            if now - _last_scan_ts > 10:
                logger.debug('Auto-detecting printers: scanning local USB and network')
                found_list = []
                for dev in USB_PRINTER_DEVICES:
                    if not os.path.exists(dev):
                        continue
                    spec = f"file://{dev}"
                    try:
                        # The configured model need not be the model of this
                        # printer. If it is busy before it ever answered, its
                        # model is unknown.
                        driver, model = drivers.resolve(spec, 'Unknown')
                        printer_state = driver.status(spec, model)
                        printer_state.setdefault('path', spec)
                        found_list.append(printer_state)
                        logger.debug('Found compatible printer at %s -> %s', spec, printer_state.get('model'))
                    except Exception as e:
                        # Keep the printer in the list with the error, otherwise
                        # the label designer switches to another printer (often
                        # the simulator) without telling why
                        logger.info('Status query of %s failed: %s', spec, e)
                        logger.debug('Status query of %s failed', spec, exc_info=True)
                        found_list.append(devices.failed_printer_state(spec, e))

                # Network printers that announce themselves, and ones in the
                # ARP table with a known MAC address prefix, e.g. where
                # multicast does not reach. The latter are dropped if their
                # IPP model is no label printer.
                candidates = [(spec, model, False, drivers.resolve(spec, model)[0]) for spec, model in _discovered_label_printers()]
                try:
                    with open(ARP_TABLE, 'r') as arp_file:
                        for line in arp_file.readlines()[1:]:  # skip header line
                            parts = line.split()
                            if len(parts) < 4:
                                continue
                            ip, _, _, mac, _, _ = parts
                            known = any(urlsplit(spec).hostname == ip for spec, _, _, _ in candidates)
                            driver = next((driver for driver in drivers.DRIVERS
                                           if any(mac.startswith(prefix) for prefix in driver.mac_address_prefixes)), None)
                            if not known and driver:
                                candidates.append((driver.network_device(ip), default_model, True, driver))
                except Exception:
                    logger.debug('Failed to read ARP table for network printer detection', exc_info=True)
                for spec, model, unconfirmed, driver in candidates:
                    try:
                        printer = driver.status(spec, model, unconfirmed)
                    except driver.errors as e:
                        logger.info('Status query of %s failed: %s', spec, e)
                        printer = devices.failed_printer_state(spec, e, model)
                    if printer is not None:
                        found_list.append(printer)
                        logger.debug('Found network printer at %s -> %s', spec, printer.get('model'))

                _cached_printers = found_list
                # A printer busy with a job is scanned again with the next
                # query instead of being shown as printing for 10 s
                busy = any(p.get('status_type') == devices.BUSY_STATUS_TYPE for p in found_list)
                _last_scan_ts = 0 if busy else now
            # Prepare response: include list of printers and a top-level status for the first one (compatibility)
            # Ensure simulator printer is always present
            sim = SIMULATOR_PRINTER.copy()
            printers = list(_cached_printers)
            # append simulator if not present
            if not any(p.get('path') == 'simulation' for p in printers):
                printers.append(sim)
            if printers:
                # Use first detected printer as default top-level status for backward compatibility
                first = printers[0]
                for key, value in first.items():
                    status[key] = value
                return {
                    'printers': printers,
                    'selected': status.get('path'),
                    **status
                }
            else:
                status['status_type'] = 'Offline'
                status['errors'].append('No compatible printer detected')
                return {
                    'printers': [sim],
                    'selected': None,
                    **status
                }
        driver, model = drivers.resolve(device_specifier, default_model)
        # A Brother printer on USB is shown at the top only, like before the
        # list of printers. The others are listed with the simulator.
        if driver is not drivers.BROTHER or device_specifier.startswith('tcp://'):
            try:
                printer = driver.status(device_specifier, model)
            except driver.errors as e:
                logger.info('Status query of %s failed: %s', device_specifier, e)
                printer = devices.failed_printer_state(device_specifier, e, model)
            status.update(printer)
            status['printers'] = [printer, SIMULATOR_PRINTER.copy()]
            status['selected'] = device_specifier
            return status
        else:
            printer_state = driver.status(device_specifier, model)
            for key, value in printer_state.items():
                status[key] = value
        # Always include simulator in returned printers list
        sim = SIMULATOR_PRINTER.copy()
        status['red_support'] = _red_support(status['model'])
        return {
            'printers': [sim],
            'selected': status.get('path'),
            **status
        }
    except Exception as e:
        logger.exception("Printer status error: %s", e)
        status['errors'] = ['The status of the printer could not be read, the log of the server names the reason']
        return status
