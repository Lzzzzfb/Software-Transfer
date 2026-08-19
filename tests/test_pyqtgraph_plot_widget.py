import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

pytest.importorskip("pyqtgraph")

from spectrometer.qt import QtCore, QtWidgets
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
    assert item.opts["autoDownsample"] is False
    assert item.opts["connect"] == "finite"


def test_input_is_contiguous_and_nonfinite_y_is_kept_as_gap(plot):
    source = np.arange(20, dtype=np.float64)[::2]
    plot.update_device_curve(0, source, source, "device")

    assert plot.device_curves[0].x.flags.c_contiguous
    assert plot.device_curves[0].y.flags.c_contiguous
    plot.update_device_curve(0, (0, 1, 2), (1, np.nan, 3), "device")
    assert np.isnan(plot.device_curves[0].y[1])
    assert plot.device_curves[0].item.opts["connect"] == "finite"


def test_invalid_x_preserves_previous_pyqtgraph_frame(plot):
    plot.update_device_curve(0, (0, 1), (10, 20), "device")
    previous = plot.device_curves[0]

    with pytest.raises(ValueError, match="X.*有限"):
        plot.update_device_curve(0, (0, np.nan), (30, 40), "bad")

    assert plot.device_curves[0] is previous
    assert np.array_equal(previous.y, (10, 20))


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


def test_full_and_zoomed_views_keep_all_adjacent_peaks(plot):
    x = np.arange(6144, dtype=np.float64)
    y = np.zeros(6144, dtype=np.float64)
    y[[1945, 1948, 1951]] = [60000, 50000, 40000]
    plot.update_device_curve(0, x, y, "peaks")
    plot.show()
    application().processEvents()

    display_x, display_y = plot.device_curves[0].item.getData()
    full = {
        int(x_value): float(y_value)
        for x_value, y_value in zip(display_x, display_y)
        if x_value in (1945, 1948, 1951)
    }
    assert full == {1945: 60000.0, 1948: 50000.0, 1951: 40000.0}

    plot.set_view_range(1940, 1956, -1000, 65000)
    application().processEvents()
    display_x, display_y = plot.device_curves[0].item.getData()
    zoomed = {
        int(x_value): float(y_value)
        for x_value, y_value in zip(display_x, display_y)
        if x_value in (1945, 1948, 1951)
    }
    assert zoomed == full


def test_different_device_pixel_counts_are_kept_independently(plot):
    for device_id, length in enumerate((17, 3648, 4096, 6144)):
        x = np.arange(length, dtype=np.float64)
        plot.update_device_curve(device_id, x, x + device_id, f"device-{device_id}")

    assert {
        device_id: curve.x.size
        for device_id, curve in plot.device_curves.items()
    } == {0: 17, 1: 3648, 2: 4096, 3: 6144}


def test_all_nonfinite_y_uses_safe_default_bounds(plot):
    plot.update_device_curve(0, (), (), "empty")
    assert plot._calculate_bounds() == (0.0, 4095.0, 0.0, 65535.0)
    plot.update_device_curve(0, np.arange(3), (np.nan, np.inf, np.nan), "gaps")

    assert plot._calculate_bounds() == (0.0, 4095.0, 0.0, 65535.0)


def test_zoomed_view_preserves_unequal_peak_values_and_order(plot):
    x = np.arange(2048, dtype=np.float64)
    y = np.zeros(x.size, dtype=np.float64)
    y[[618, 624, 633]] = [45000, 30000, 15000]
    plot.update_device_curve(0, x, y, "unequal-peaks")
    plot.set_view_range(610, 640, -1000, 50000)
    plot.show()
    application().processEvents()

    display_x, display_y = plot.device_curves[0].item.getData()
    peaks = {
        int(x_value): float(y_value)
        for x_value, y_value in zip(display_x, display_y)
        if x_value in (618, 624, 633)
    }

    assert peaks == {618: 45000.0, 624: 30000.0, 633: 15000.0}


def test_double_click_reset_restores_range_without_enabling_live_auto_range(plot):
    x = np.arange(100, dtype=np.float64)
    plot.update_device_curve(0, x, x, "device")
    plot.set_view_range(20, 30, 20, 30)

    plot._view_box.reset_requested.emit()

    assert plot._effective_range()[0] < 0
    assert plot._effective_range()[1] > 99
    assert not plot.auto_range_enabled


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


class IgnoredPointerEvent:
    def __init__(self, button=None):
        self._button = button
        self.accepted = False

    def button(self):
        return self._button

    def accept(self):
        self.accepted = True


def test_view_box_ignores_wheel_and_right_drag(plot):
    wheel = IgnoredPointerEvent()
    plot._view_box.wheelEvent(wheel)
    assert wheel.accepted

    right_button = getattr(QtCore.Qt, "RightButton", None)
    if right_button is None:
        right_button = QtCore.Qt.MouseButton.RightButton
    drag = IgnoredPointerEvent(right_button)
    plot._view_box.mouseDragEvent(drag)
    assert drag.accepted

    click = IgnoredPointerEvent(right_button)
    plot._view_box.mouseClickEvent(click)
    assert click.accepted
