import os

import pytest

from app.labeldesigner import printer


@pytest.fixture(autouse=True)
def printer_output(tmp_path, monkeypatch):
    # Keep lock files and simulated labels of the tests out of the system temp
    # directory and the working tree
    monkeypatch.setattr(printer, 'LOCK_DIR', str(tmp_path))
    monkeypatch.setattr(printer, 'SIMULATED_LABELS_DIR', str(tmp_path / 'simulated_labels'))
    # Network printers of the machine running the tests are neither detected
    # nor discovered.
    # Tests of USB detection fake the devices they need.
    monkeypatch.setattr(printer, 'ARP_TABLE', os.devnull)
    monkeypatch.setattr(printer, 'NETWORK_DISCOVERY', False)
