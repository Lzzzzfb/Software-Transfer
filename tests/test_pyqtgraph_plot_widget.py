import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from spectrometer.qt import QtWidgets
from spectrometer.ui.pyqtgraph_plot_widget import PyQtGraphSpectrumPlotWidget


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


@pytest.fixture
def plot():
    application()
    widget = PyQtGraphSpectrumPlotWidget()
    widget.resize(800, 500)
    yield widget
    widget.close()
    application().processEvents()


def test_device_updates_reuse_one_plot_data_item(plot):
    x = np.arange(3648, dtype=np.float64)
    first = np.sin(x / 50)
    second = np.cos(x / 50)

    plot.update_device_curve(0, x, first, "SN003")
    item = plot.device_curves[0].item
    plot.update_device_curve(0, x, second, "SN003")

    assert plot.device_curves[0].item is item
    assert np.array_equal(item.xData, x)
    assert np.array_equal(item.yData, second)
    assert item.opts["clipToView"] is True
    assert item.opts["autoDownsample"] is True
    assert item.opts["downsampleMethod"] == "peak"


def test_input_is_contiguous_and_non_finite_values_are_rejected(plot):
    source = np.arange(20, dtype=np.float64)[::2]
    plot.update_device_curve(0, source, source, "device")

    assert plot.device_curves[0].x.flags.c_contiguous
    assert plot.device_curves[0].y.flags.c_contiguous
    with pytest.raises(ValueError, match="有限"):
        plot.update_device_curve(0, (0, 1), (1, np.nan), "device")


def test_manual_and_fixed_ranges_match_legacy_contract(plot):
    x = np.arange(100)
    plot.update_device_curve(0, x, np.sin(x / 10), "device")
    plot.set_view_range(10, 30, -1, 1)
    assert plot._effective_range() == (10.0, 30.0, -1.0, 1.0)
    assert not plot.auto_range_enabled

    plot.set_fixed_y_range(-2, 2)
    assert plot.fixed_y_enabled
    assert plot._effective_range()[2:] == (-2.0, 2.0)
    plot.disable_fixed_y()
    assert not plot.fixed_y_enabled


def test_peak_downsampling_keeps_single_pixel_peak(plot):
    x = np.arange(3648, dtype=np.float64)
    y = np.zeros(3648, dtype=np.float64)
    y[1824] = 60000
    plot.update_device_curve(0, x, y, "peak")
    plot.show()
    application().processEvents()

    _display_x, display_y = plot.device_curves[0].item.getData()
    assert float(np.max(display_y)) == 60000


def test_reference_limit_clear_and_png_export(plot, tmp_path):
    x = np.arange(10)
    for index in range(64):
        assert plot.add_reference_curve(x, x + index, f"reference-{index}")
    assert not plot.add_reference_curve(x, x, "overflow")
    plot.clear_reference_curves()
    assert not plot.reference_curves

    plot.update_device_curve(0, x, x, "device")
    target = tmp_path / "plot.png"
    assert plot.save_image(target)
    assert target.stat().st_size > 0


def test_reports_completed_new_data_paint(plot):
    painted = []
    plot.data_frame_painted.connect(lambda: painted.append(True))
    plot.show()
    plot.update_device_curve(0, np.arange(100), np.arange(100), "device")
    application().processEvents()

    assert painted == [True]
