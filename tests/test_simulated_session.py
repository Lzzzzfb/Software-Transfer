import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.processing.references import ReferenceRepository
from spectrometer.qt import QtWidgets
from spectrometer.ui.main_window import MainWindow


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_simulated_multi_device_session_exports_default_layout(tmp_path):
    app = application()
    window = MainWindow(simulation=True, auto_start_simulation=False, settings_path=tmp_path / "settings.json")
    window.settings["storage_path"] = str(tmp_path)
    window.storage_manager.set_output_directory(tmp_path)
    window.reference_repository = ReferenceRepository(tmp_path / "references")
    window.settings["batch_size"] = 2
    window.settings["auto_store"] = True
    window.start_acquisition()
    window._simulation_tick(); window._simulation_tick()
    window.stop_acquisition()
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline and (
        len(list(tmp_path.glob("*.xlsx"))) < 1 or list(tmp_path.glob("*.part"))
    ):
        app.processEvents()
        time.sleep(0.005)

    assert len(list(tmp_path.glob("*.csv"))) == 0
    assert len(list(tmp_path.glob("*.xlsx"))) == 1
    assert not list(tmp_path.glob("*.part"))
    window.close(); app.processEvents()
