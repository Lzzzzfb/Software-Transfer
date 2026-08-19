import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
import pytest

from spectrometer.qt import QtCore, QtWidgets
from spectrometer.ui.plot_widget import SpectrumPlotWidget


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def plot_widget():
    application()
    plot = SpectrumPlotWidget()
    plot.resize(900, 560)
    plot.update_device_curve(0, np.arange(101), np.linspace(10, 20, 101), "设备")
    return plot


class MouseEvent:
    def __init__(self, position, *, button=QtCore.Qt.LeftButton, wheel=0):
        self._position = position
        self._button = button
        self._wheel = wheel
        self.accepted = False

    def pos(self):
        return self._position.toPoint()

    def button(self):
        return self._button

    def angleDelta(self):
        return QtCore.QPoint(0, self._wheel)

    def accept(self):
        self.accepted = True


class RecordingPainter:
    def __init__(self):
        self.polygons = []

    def save(self):
        pass

    def restore(self):
        pass

    def setClipRect(self, _rect):
        pass

    def setRenderHint(self, _hint, _enabled):
        pass

    def setPen(self, _pen):
        pass

    def drawPolyline(self, polygon):
        self.polygons.append(
            [(float(point.x()), float(point.y())) for point in polygon]
        )


def test_left_drag_selection_zooms_both_axes():
    plot = plot_widget()
    plot.set_view_range(0, 100, 0, 100)
    rect = plot._plot_rect()
    origin = QtCore.QPointF(
        rect.left() + rect.width() * 0.25,
        rect.top() + rect.height() * 0.25,
    )
    current = QtCore.QPointF(
        rect.left() + rect.width() * 0.75,
        rect.top() + rect.height() * 0.75,
    )

    assert plot._apply_selection_zoom(origin, current)
    x_min, x_max, y_min, y_max = plot._effective_range()
    assert np.allclose((x_min, x_max, y_min, y_max), (25, 75, 25, 75))
    assert not plot.auto_range_enabled


def test_tiny_selection_is_treated_as_click():
    plot = plot_widget()
    before = plot._effective_range()
    rect = plot._plot_rect()
    origin = QtCore.QPointF(rect.center())

    assert not plot._apply_selection_zoom(
        origin, QtCore.QPointF(origin.x() + 3, origin.y() + 3)
    )
    assert plot._effective_range() == before


def test_fixed_y_ignores_new_frame_auto_range_but_allows_manual_zoom():
    plot = plot_widget()
    plot.set_fixed_y_range(-100, 50000)
    initial = plot._effective_range()
    assert initial[2:] == (-100, 50000)

    plot.set_view_range(10, 30, 1000, 2000)
    plot.update_device_curve(0, np.arange(101), np.linspace(-1e6, 1e6, 101), "设备")
    assert plot._effective_range() == (10, 30, 1000, 2000)

    plot.reset_initial_view()
    reset = plot._effective_range()
    assert reset[0] < 0 and reset[1] > 100
    assert reset[2:] == (-100, 50000)


def test_disabling_fixed_y_restores_live_auto_range():
    plot = plot_widget()
    plot.set_fixed_y_range(0, 100)
    plot.disable_fixed_y()
    plot.update_device_curve(0, np.arange(101), np.linspace(200, 800, 101), "设备")

    assert plot.auto_range_enabled
    assert plot._effective_range()[2] < 200
    assert plot._effective_range()[3] > 800


def test_wheel_is_ignored_and_double_click_still_restores_base():
    plot = plot_widget()
    plot.set_fixed_y_range(0, 1000)
    rect = plot._plot_rect()
    before = plot._effective_range()
    wheel = MouseEvent(rect.center(), wheel=120)
    plot.wheelEvent(wheel)
    assert wheel.accepted
    assert plot._effective_range() == before

    plot.set_view_range(10, 30, 100, 200)
    plot.update_device_curve(0, np.arange(101), np.linspace(-1e6, 1e6, 101), "设备")
    plot.mouseDoubleClickEvent(MouseEvent(rect.center()))
    assert plot._effective_range()[2:] == (0, 1000)
    assert not plot.auto_range_enabled


def test_right_button_is_ignored_without_starting_selection():
    plot = plot_widget()
    before = plot._effective_range()
    right_button = getattr(QtCore.Qt, "RightButton", None)
    if right_button is None:
        right_button = QtCore.Qt.MouseButton.RightButton
    event = MouseEvent(
        plot._plot_rect().center(), button=right_button
    )

    plot.mousePressEvent(event)
    plot.mouseReleaseEvent(event)

    assert event.accepted
    assert plot._selection_origin is None
    assert plot._effective_range() == before


def test_axis_formatter_never_uses_scientific_notation_or_negative_zero():
    values = (1_234_567_890.25, 0.0000001234, -0.0, -987654.5)
    labels = [SpectrumPlotWidget.format_axis_value(value) for value in values]

    assert all("e" not in label.lower() for label in labels)
    assert labels[0] == "1234567890.25"
    assert labels[1] == "0.0000001234"
    assert labels[2] == "0"
    assert SpectrumPlotWidget.format_axis_value(10922.5, prefer_integer=True) == "10923"


def test_large_full_decimal_labels_reduce_ticks_and_render_without_clipping(tmp_path):
    plot = plot_widget()
    plot.resize(520, 360)
    plot.set_view_range(1_000_000_000, 1_000_000_100, -2_000_000, 2_000_000)
    rect, x_ticks, y_ticks = plot._axis_layout()

    assert rect.left() > 68
    assert x_ticks < 7
    assert y_ticks <= 7
    assert plot.save_image(str(tmp_path / "full-decimal-axis.png"))


def test_unchanged_axis_labels_do_not_schedule_redundant_repaint():
    plot = plot_widget()
    updates = []
    plot.update = lambda: updates.append(True)

    plot.set_axis_labels(plot.x_label, plot.y_label)
    assert updates == []
    plot.set_axis_labels("波长 (nm)", "吸光度")
    assert updates == [True]


def test_curve_storage_keeps_all_adjacent_peaks_at_any_frame_length():
    plot = plot_widget()
    x = np.arange(6144, dtype=np.float64)
    y = np.zeros(x.size, dtype=np.float64)
    y[[1945, 1948, 1951]] = [60000, 50000, 40000]
    y[[618, 624, 633]] = [45000, 30000, 15000]

    plot.update_device_curve(0, x, y, "多峰")

    curve = plot.device_curves[0]
    assert curve.x.size == 6144
    assert curve.y[[1945, 1948, 1951]].tolist() == [60000, 50000, 40000]
    assert curve.y[[618, 624, 633]].tolist() == [45000, 30000, 15000]


def test_invalid_curve_update_preserves_previous_legacy_frame():
    plot = plot_widget()
    previous = plot.device_curves[0]

    with pytest.raises(ValueError, match="X.*有限"):
        plot.update_device_curve(0, (0, np.nan), (1, 2), "错误帧")

    assert plot.device_curves[0] is previous


def test_legacy_reference_and_baseline_share_shape_validation():
    plot = plot_widget()

    with pytest.raises(ValueError, match="一维且长度相同"):
        plot.add_reference_curve((0, 1), (1,), "错误参考")
    with pytest.raises(ValueError, match="一维且长度相同"):
        plot.set_baseline_curve(0, (0, 1), (1,), True)


def test_legacy_all_nonfinite_y_uses_safe_default_bounds():
    plot = SpectrumPlotWidget()
    plot.update_device_curve(0, (), (), "空曲线")
    assert plot._calculate_bounds() == (0.0, 4095.0, 0.0, 65535.0)
    plot.update_device_curve(0, np.arange(3), (np.nan, np.inf, np.nan), "断点")

    assert plot._calculate_bounds() == (0.0, 4095.0, 0.0, 65535.0)


def test_legacy_draw_curve_sends_every_full_view_point_to_painter():
    plot = SpectrumPlotWidget()
    x = np.arange(6144, dtype=np.float64)
    plot.update_device_curve(0, x, x, "完整曲线")
    painter = RecordingPainter()

    plot._draw_curve(
        painter,
        QtCore.QRectF(0, 0, 1000, 500),
        (-1, 6144, -1, 6144),
        plot.device_curves[0],
    )

    assert [len(polygon) for polygon in painter.polygons] == [6144]


def test_legacy_draw_curve_never_bridges_nonfinite_y():
    plot = SpectrumPlotWidget()
    x = np.arange(10, dtype=np.float64)
    y = x.copy()
    y[5] = np.nan
    plot.update_device_curve(0, x, y, "断点曲线")
    painter = RecordingPainter()

    plot._draw_curve(
        painter,
        QtCore.QRectF(0, 0, 1000, 500),
        (-1, 10, -1, 10),
        plot.device_curves[0],
    )

    assert [len(polygon) for polygon in painter.polygons] == [5, 4]
