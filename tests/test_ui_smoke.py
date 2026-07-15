import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from spectrometer.qt import QtWidgets
from spectrometer.ui.main_window import MainWindow
from spectrometer.ui.plot_widget import SpectrumPlotWidget


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_main_window_follows_ribbon_sidebar_plot_status_layout(tmp_path):
    app = application()
    window = MainWindow(simulation=True, auto_start_simulation=False, settings_path=tmp_path / "settings.json")
    assert window.ribbon.objectName() == "mainRibbon"
    assert window.sidebar.objectName() == "deviceSidebar"
    assert window.plot_widget.objectName() == "spectrumPlot"
    assert window.sidebar.batch_size.value() == 500
    assert window.sidebar.storage_format.currentData() == "csv_excel"
    assert len(window.sidebar.cards) == 4
    window.close(); app.processEvents()


def test_custom_plot_runs_without_pyqtgraph_and_preserves_manual_range():
    application()
    plot = SpectrumPlotWidget(); plot.resize(800, 500)
    x = np.arange(100); plot.update_device_curve(0, x, np.sin(x / 10), "模拟设备")
    plot.set_view_range(10, 30, -1, 1)
    assert plot._effective_range() == (10, 30, -1, 1)
    assert plot.add_reference_curve(x, np.cos(x / 10), "参考")
