from spectrometer.domain.models import SpectrumFrame
from spectrometer.qt import QtWidgets
from spectrometer.storage.spool import SpoolWriter
from spectrometer.ui.main_window import MainWindow


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_main_window_recovers_part_to_csv_and_excel(tmp_path, monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    application()
    part = tmp_path / "session.part"
    with SpoolWriter(part, {"session_id": "session"}, flush_every=1) as writer:
        writer.append(SpectrumFrame.create(0, 1 << 8, [1, 2]))
        writer.append(SpectrumFrame.create(1, 1 << 8, [3, 4]))
    window = MainWindow(simulation=True, auto_start_simulation=False, settings_path=tmp_path / "settings.json")
    outputs, recovery = window._recover_spool_file(part)
    assert len(recovery.frames) == 2
    assert len(outputs) == 3
    assert all(path.exists() for path in outputs)
    window.close()
