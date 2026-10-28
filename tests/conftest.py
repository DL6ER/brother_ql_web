import pytest

from app.labeldesigner import printer


@pytest.fixture(autouse=True)
def printer_output(tmp_path, monkeypatch):
    # Keep lock files and simulated labels of the tests out of the system temp
    # directory and the working tree
    monkeypatch.setattr(printer, 'LOCK_DIR', str(tmp_path))
    monkeypatch.setattr(printer, 'SIMULATED_LABELS_DIR', str(tmp_path / 'simulated_labels'))
