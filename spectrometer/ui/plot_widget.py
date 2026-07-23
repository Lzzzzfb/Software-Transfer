"""无需第三方绘图库的高性能光谱绘图控件。"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from ..qt import QT_API, QtCore, QtGui, QtWidgets, Signal


COLOR_PALETTE = [
    "#1367D1", "#E65100", "#18864B", "#9C27B0", "#D32F2F", "#00838F",
    "#5D4037", "#3949AB", "#7CB342", "#F9A825", "#6D4C41", "#546E7A",
]


@dataclass
class _Curve:
    x: np.ndarray
    y: np.ndarray
    color: QtGui.QColor
    label: str
    visible: bool = True


def _event_position(event):
    position = getattr(event, "position", None)
    return position() if position else event.pos()


def _text_width(metrics, text):
    method = getattr(metrics, "horizontalAdvance", None) or metrics.width
    return method(text)


class SpectrumPlotWidget(QtWidgets.QWidget):
    user_zoomed = Signal()
    view_reset = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("spectrumPlot")
        self.setMinimumSize(500, 360)
        self.setToolTip("左键拖动框选放大；左键双击恢复初始视图；滚轮以光标为中心缩放")
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.device_curves: Dict[int, _Curve] = {}
        self.reference_curves: Dict[str, _Curve] = {}
        self.baseline_curves: Dict[int, _Curve] = {}
        self.line_width = 1.4
        self.x_label = "像素序号"
        self.y_label = "强度（计数）"
        self.auto_range_enabled = True
        self.fixed_y_enabled = False
        self.fixed_y_range: Optional[Tuple[float, float]] = None
        self._view_range: Optional[Tuple[float, float, float, float]] = None
        self._selection_origin = None
        self._selection_current = None
        self._cursor_pos = None
        self._show_crosshair = True

    def update_device_curve(self, device_id: int, x, y, label: str = ""):
        x_array = np.asarray(x, dtype=np.float64)
        y_array = np.asarray(y, dtype=np.float64)
        if x_array.shape != y_array.shape or x_array.ndim != 1:
            raise ValueError("绘图 x/y 必须是一维且长度相同")
        color = QtGui.QColor(COLOR_PALETTE[device_id % len(COLOR_PALETTE)])
        existing = self.device_curves.get(device_id)
        self.device_curves[device_id] = _Curve(
            x_array.copy(), y_array.copy(), existing.color if existing else color,
            label or f"设备 {device_id}", existing.visible if existing else True,
        )
        if self.auto_range_enabled:
            self._view_range = self._initial_view_range()
        self.update()

    update_spectrum = update_device_curve

    def remove_device_curve(self, device_id: int):
        self.device_curves.pop(device_id, None)
        self.baseline_curves.pop(device_id, None)
        if self.auto_range_enabled:
            self._view_range = self._initial_view_range()
        self.update()

    def add_reference_curve(self, x, y, label: str = "对比光谱") -> bool:
        if len(self.reference_curves) >= 64:
            return False
        key = f"reference_{len(self.reference_curves) + 1}"
        color = QtGui.QColor(COLOR_PALETTE[(len(self.reference_curves) + 5) % len(COLOR_PALETTE)])
        self.reference_curves[key] = _Curve(
            np.asarray(x, dtype=np.float64).copy(),
            np.asarray(y, dtype=np.float64).copy(),
            color,
            label,
        )
        self.update()
        return True

    def set_baseline_curve(self, device_id: int, x, y, visible: bool = True):
        self.baseline_curves[device_id] = _Curve(
            np.asarray(x, dtype=np.float64).copy(),
            np.asarray(y, dtype=np.float64).copy(),
            QtGui.QColor("#8894A4"),
            f"设备 {device_id} 基线",
            visible,
        )
        self.update()

    def clear_reference_curves(self):
        self.reference_curves.clear()
        self.update()

    def clear_all(self):
        self.device_curves.clear()
        self.reference_curves.clear()
        self.baseline_curves.clear()
        self._view_range = self._initial_view_range()
        self.update()

    def set_axis_labels(self, x_label: str, y_label: str):
        self.x_label, self.y_label = x_label, y_label
        self.update()

    def set_line_width(self, width: float):
        self.line_width = max(0.5, float(width))
        self.update()

    def set_device_color(self, device_id: int, color):
        if device_id in self.device_curves:
            self.device_curves[device_id].color = QtGui.QColor(color)
            self.update()

    def auto_range(self):
        self.reset_initial_view()

    def enable_auto_range(self, enabled: bool):
        self.auto_range_enabled = bool(enabled)
        if enabled:
            self.reset_initial_view()

    def set_fixed_y_range(self, minimum: float, maximum: float):
        minimum = float(minimum)
        maximum = float(maximum)
        if not np.isfinite(minimum) or not np.isfinite(maximum):
            raise ValueError("固定 Y 轴范围必须是有限数值")
        if minimum >= maximum:
            raise ValueError("固定 Y 轴最小值必须小于最大值")
        self.fixed_y_enabled = True
        self.fixed_y_range = (minimum, maximum)
        self.reset_initial_view()

    def disable_fixed_y(self):
        self.fixed_y_enabled = False
        self.fixed_y_range = None
        self.reset_initial_view()

    def reset_initial_view(self):
        self.auto_range_enabled = True
        self._view_range = self._initial_view_range()
        self.view_reset.emit()
        self.update()

    def set_x_range(self, minimum: float, maximum: float):
        current = self._effective_range()
        self._view_range = (minimum, maximum, current[2], current[3])
        self.auto_range_enabled = False
        self.update()

    def set_y_range(self, minimum: float, maximum: float):
        current = self._effective_range()
        self._view_range = (current[0], current[1], minimum, maximum)
        self.auto_range_enabled = False
        self.update()

    def set_view_range(self, x_min, x_max, y_min, y_max):
        self._view_range = (x_min, x_max, y_min, y_max)
        self.auto_range_enabled = False
        self.update()

    def save_image(self, path: str) -> bool:
        image = QtGui.QImage(self.size(), QtGui.QImage.Format_ARGB32)
        image.fill(QtGui.QColor("white"))
        painter = QtGui.QPainter(image)
        self._paint(painter)
        painter.end()
        return image.save(path)

    def _all_curves(self):
        return list(self.device_curves.values()) + list(self.reference_curves.values()) + list(self.baseline_curves.values())

    def _calculate_bounds(self):
        visible = [curve for curve in self._all_curves() if curve.visible and curve.x.size]
        if not visible:
            return (0.0, 4095.0, 0.0, 65535.0)
        x_min = min(float(np.nanmin(curve.x)) for curve in visible)
        x_max = max(float(np.nanmax(curve.x)) for curve in visible)
        y_min = min(float(np.nanmin(curve.y)) for curve in visible)
        y_max = max(float(np.nanmax(curve.y)) for curve in visible)
        if x_max <= x_min: x_max = x_min + 1
        if y_max <= y_min: y_max = y_min + 1
        x_pad = (x_max - x_min) * 0.02
        y_pad = (y_max - y_min) * 0.08
        return (x_min - x_pad, x_max + x_pad, y_min - y_pad, y_max + y_pad)

    def _initial_view_range(self):
        bounds = self._calculate_bounds()
        if self.fixed_y_enabled and self.fixed_y_range is not None:
            return (bounds[0], bounds[1], *self.fixed_y_range)
        return bounds

    def _effective_range(self):
        return self._view_range or self._initial_view_range()

    @staticmethod
    def format_axis_value(value: float, prefer_integer: bool = False) -> str:
        value = float(value)
        if not np.isfinite(value):
            return "--"
        if abs(value) < 5e-13:
            value = 0.0
        if prefer_integer:
            rounded = np.floor(value + 0.5) if value >= 0 else np.ceil(value - 0.5)
            return str(int(rounded))
        magnitude = abs(value)
        if 0 < magnitude < 1:
            decimals = min(12, max(6, int(-np.floor(np.log10(magnitude))) + 3))
        else:
            decimals = 6
        text = f"{value:.{decimals}f}".rstrip("0").rstrip(".")
        return "0" if text in ("", "-0") else text

    def _format_x(self, value):
        return self.format_axis_value(value, self.x_label.startswith("像素"))

    def _format_y(self, value):
        return self.format_axis_value(value, self.y_label == "强度（计数）")

    def _axis_layout(self, view=None):
        view = view or self._effective_range()
        metrics = QtGui.QFontMetrics(self.font())
        y_labels = [
            self._format_y(view[2] + index / 6 * (view[3] - view[2]))
            for index in range(7)
        ]
        x_labels = [
            self._format_x(view[0] + index / 6 * (view[1] - view[0]))
            for index in range(7)
        ]
        widest_x = max(_text_width(metrics, label) for label in x_labels) + 12
        left = max(
            68,
            max(_text_width(metrics, label) for label in y_labels) + 35,
            widest_x / 2 + 8,
        )
        right = max(24, widest_x / 2 + 8)
        rect = QtCore.QRectF(self.rect()).adjusted(left, 22, -right, -54)
        x_ticks = max(2, min(7, int(rect.width() // max(1, widest_x)) + 1))
        y_ticks = max(2, min(7, int(rect.height() // max(1, metrics.height() + 8)) + 1))
        return rect, x_ticks, y_ticks

    def _plot_rect(self):
        return self._axis_layout()[0]

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        self._paint(painter)

    def _paint(self, painter):
        antialias = getattr(QtGui.QPainter, "Antialiasing", None)
        if antialias is None:
            antialias = QtGui.QPainter.RenderHint.Antialiasing
        painter.setRenderHint(antialias, True)
        painter.fillRect(self.rect(), QtGui.QColor("#FFFFFF"))
        view = self._effective_range()
        plot_rect, x_ticks, y_ticks = self._axis_layout(view)
        painter.fillRect(plot_rect, QtGui.QColor("#FBFCFE"))
        self._draw_grid(painter, plot_rect, view, x_ticks, y_ticks)
        for curve in self._all_curves():
            if curve.visible:
                self._draw_curve(painter, plot_rect, view, curve)
        self._draw_legend(painter, plot_rect)
        self._draw_crosshair(painter, plot_rect, view)
        self._draw_selection(painter, plot_rect)

    def _draw_grid(self, painter, rect, view, x_ticks=7, y_ticks=7):
        painter.setPen(QtGui.QPen(QtGui.QColor("#E2E8F0"), 1))
        font = painter.font(); font.setPointSize(8); painter.setFont(font)
        for index in range(x_ticks):
            ratio = index / max(1, x_ticks - 1)
            px = rect.left() + ratio * rect.width()
            painter.drawLine(QtCore.QPointF(px, rect.top()), QtCore.QPointF(px, rect.bottom()))
            x_value = view[0] + ratio * (view[1] - view[0])
            painter.setPen(QtGui.QColor("#667085"))
            label = self._format_x(x_value)
            label_width = _text_width(QtGui.QFontMetrics(painter.font()), label) + 10
            painter.drawText(
                QtCore.QRectF(px - label_width / 2, rect.bottom() + 6, label_width, 18),
                QtCore.Qt.AlignCenter,
                label,
            )
            painter.setPen(QtGui.QPen(QtGui.QColor("#E2E8F0"), 1))
        for index in range(y_ticks):
            ratio = index / max(1, y_ticks - 1)
            py = rect.bottom() - ratio * rect.height()
            painter.drawLine(QtCore.QPointF(rect.left(), py), QtCore.QPointF(rect.right(), py))
            y_value = view[2] + ratio * (view[3] - view[2])
            painter.setPen(QtGui.QColor("#667085"))
            painter.drawText(
                QtCore.QRectF(3, py - 9, rect.left() - 10, 18),
                (
                    int(QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter)
                    if QT_API == "PyQt5"
                    else QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                ),
                self._format_y(y_value),
            )
            painter.setPen(QtGui.QPen(QtGui.QColor("#E2E8F0"), 1))
        painter.setPen(QtGui.QPen(QtGui.QColor("#8492A6"), 1))
        painter.drawRect(rect)
        painter.setPen(QtGui.QColor("#344054"))
        painter.drawText(QtCore.QRectF(rect.left(), rect.bottom() + 29, rect.width(), 18), QtCore.Qt.AlignCenter, self.x_label)
        painter.save(); painter.translate(17, rect.center().y()); painter.rotate(-90)
        painter.drawText(QtCore.QRectF(-rect.height() / 2, -10, rect.height(), 20), QtCore.Qt.AlignCenter, self.y_label)
        painter.restore()

    def _draw_curve(self, painter, rect, view, curve):
        if curve.x.size < 2:
            return
        finite = np.isfinite(curve.x) & np.isfinite(curve.y)
        x, y = curve.x[finite], curve.y[finite]
        if x.size < 2:
            return
        max_points = max(400, int(rect.width() * 2))
        if x.size > max_points:
            indices = np.linspace(0, x.size - 1, max_points, dtype=int)
            x, y = x[indices], y[indices]
        px = rect.left() + (x - view[0]) / (view[1] - view[0]) * rect.width()
        py = rect.bottom() - (y - view[2]) / (view[3] - view[2]) * rect.height()
        path = QtGui.QPainterPath(QtCore.QPointF(float(px[0]), float(py[0])))
        for x_point, y_point in zip(px[1:], py[1:]):
            path.lineTo(float(x_point), float(y_point))
        painter.save(); painter.setClipRect(rect)
        painter.setPen(QtGui.QPen(curve.color, self.line_width))
        painter.drawPath(path); painter.restore()

    def _draw_legend(self, painter, rect):
        items = [curve for curve in self._all_curves() if curve.visible][:12]
        if not items:
            return
        box = QtCore.QRectF(rect.right() - 170, rect.top() + 10, 158, 8 + len(items) * 20)
        painter.fillRect(box, QtGui.QColor(255, 255, 255, 225))
        painter.setPen(QtGui.QColor("#D0D5DD")); painter.drawRect(box)
        for index, curve in enumerate(items):
            y = box.top() + 14 + index * 20
            painter.setPen(QtGui.QPen(curve.color, 2))
            painter.drawLine(
                QtCore.QLineF(box.left() + 8, y, box.left() + 30, y)
            )
            painter.setPen(QtGui.QColor("#344054")); painter.drawText(QtCore.QPointF(box.left() + 36, y + 4), curve.label[:18])

    def _draw_crosshair(self, painter, rect, view):
        if not self._show_crosshair or self._cursor_pos is None or not rect.contains(self._cursor_pos):
            return
        pos = self._cursor_pos
        painter.setPen(QtGui.QPen(QtGui.QColor("#98A2B3"), 1, QtCore.Qt.DashLine))
        painter.drawLine(QtCore.QPointF(pos.x(), rect.top()), QtCore.QPointF(pos.x(), rect.bottom()))
        painter.drawLine(QtCore.QPointF(rect.left(), pos.y()), QtCore.QPointF(rect.right(), pos.y()))
        x_value = view[0] + (pos.x() - rect.left()) / rect.width() * (view[1] - view[0])
        y_value = view[3] - (pos.y() - rect.top()) / rect.height() * (view[3] - view[2])
        text = f"X {self._format_x(x_value)}   Y {self._format_y(y_value)}"
        width = _text_width(QtGui.QFontMetrics(painter.font()), text) + 18
        box = QtCore.QRectF(pos.x() + 8, pos.y() - 27, width, 22)
        if box.right() > rect.right():
            box.moveRight(pos.x() - 8)
        painter.fillRect(box, QtGui.QColor(16, 24, 40, 220))
        painter.setPen(QtGui.QColor("white")); painter.drawText(box, QtCore.Qt.AlignCenter, text)

    def _draw_selection(self, painter, rect):
        if self._selection_origin is None or self._selection_current is None:
            return
        selection = QtCore.QRectF(
            self._selection_origin, self._selection_current
        ).normalized().intersected(rect)
        if selection.isEmpty():
            return
        painter.fillRect(selection, QtGui.QColor(19, 103, 209, 45))
        painter.setPen(QtGui.QPen(QtGui.QColor("#1367D1"), 1))
        painter.drawRect(selection)

    def mouseMoveEvent(self, event):
        position = _event_position(event)
        self._cursor_pos = QtCore.QPointF(position)
        if self._selection_origin is not None:
            rect = self._plot_rect()
            self._selection_current = QtCore.QPointF(
                min(rect.right(), max(rect.left(), position.x())),
                min(rect.bottom(), max(rect.top(), position.y())),
            )
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._plot_rect().contains(_event_position(event)):
            self._selection_origin = QtCore.QPointF(_event_position(event))
            self._selection_current = QtCore.QPointF(_event_position(event))

    def mouseReleaseEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._selection_origin is not None:
            current = self._selection_current or QtCore.QPointF(_event_position(event))
            self._apply_selection_zoom(self._selection_origin, current)
        self._selection_origin = None
        self._selection_current = None
        self.update()

    def _apply_selection_zoom(self, origin, current) -> bool:
        rect = self._plot_rect()
        selection = QtCore.QRectF(origin, current).normalized().intersected(rect)
        if selection.width() < 6 or selection.height() < 6:
            return False
        x_min, x_max, y_min, y_max = self._effective_range()
        selected_x_min = x_min + (
            (selection.left() - rect.left()) / rect.width()
        ) * (x_max - x_min)
        selected_x_max = x_min + (
            (selection.right() - rect.left()) / rect.width()
        ) * (x_max - x_min)
        selected_y_max = y_max - (
            (selection.top() - rect.top()) / rect.height()
        ) * (y_max - y_min)
        selected_y_min = y_max - (
            (selection.bottom() - rect.top()) / rect.height()
        ) * (y_max - y_min)
        self._view_range = (
            selected_x_min,
            selected_x_max,
            selected_y_min,
            selected_y_max,
        )
        self.auto_range_enabled = False
        self.user_zoomed.emit()
        return True

    def leaveEvent(self, event):
        self._cursor_pos = None; self.update()

    def mouseDoubleClickEvent(self, event):
        if (
            event.button() == QtCore.Qt.LeftButton
            and self._plot_rect().contains(_event_position(event))
        ):
            self.reset_initial_view()

    def wheelEvent(self, event):
        position = _event_position(event)
        rect = self._plot_rect()
        if not rect.contains(position):
            return
        factor = 0.85 if event.angleDelta().y() > 0 else 1.18
        x_min, x_max, y_min, y_max = self._effective_range()
        x_ratio = (position.x() - rect.left()) / rect.width()
        y_ratio = (rect.bottom() - position.y()) / rect.height()
        x_anchor = x_min + x_ratio * (x_max - x_min)
        y_anchor = y_min + y_ratio * (y_max - y_min)
        self._view_range = (
            x_anchor + (x_min - x_anchor) * factor,
            x_anchor + (x_max - x_anchor) * factor,
            y_anchor + (y_min - y_anchor) * factor,
            y_anchor + (y_max - y_anchor) * factor,
        )
        self.auto_range_enabled = False; self.user_zoomed.emit(); self.update()


class HistoryPlotWidget(SpectrumPlotWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._history_index = 0

    def load_spectrum(self, wavelengths, intensities, label="", color=None):
        if self._history_index >= 64:
            return False
        key = self._history_index
        self.update_device_curve(key, wavelengths, intensities, label or f"光谱 {key + 1}")
        if color is not None:
            self.set_device_color(key, color)
        self._history_index += 1
        return True

    def clear(self):
        self.clear_all(); self._history_index = 0
