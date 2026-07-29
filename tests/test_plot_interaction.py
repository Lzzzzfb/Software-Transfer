import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

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


def test_curve_rendering_uses_at_most_one_point_per_horizontal_pixel():
    assert SpectrumPlotWidget._curve_point_limit(1400) == 1400
    assert SpectrumPlotWidget._curve_point_limit(300) == 400
