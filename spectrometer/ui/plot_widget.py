"""无需第三方绘图库的高性能光谱绘图控件。"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np

from ..qt import QtCore, QtGui, QtWidgets, Signal


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


class SpectrumPlotWidget(QtWidgets.QWidget):
    user_zoomed = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("spectrumPlot")
        self.setMinimumSize(500, 360)
        self.setMouseTracking(True)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.device_curves: Dict[int, _Curve] = {}
        self.reference_curves: Dict[str, _Curve] = {}
        self.baseline_curves: Dict[int, _Curve] = {}
        self.line_width = 1.4
        self.x_label = "像素序号"
        self.y_label = "强度 (counts)"
        self.auto_range_enabled = True
        self._view_range: Optional[Tuple[float, float, float, float]] = None
        self._drag_origin = None
        self._drag_range = None
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
            self._view_range = self._calculate_bounds()
        self.update()

    update_spectrum = update_device_curve

    def remove_device_curve(self, device_id: int):
        self.device_curves.pop(device_id, None)
        self.baseline_curves.pop(device_id, None)
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
        self._view_range = None
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
        self.auto_range_enabled = True
        self._view_range = self._calculate_bounds()
        self.update()

    def enable_auto_range(self, enabled: bool):
        self.auto_range_enabled = bool(enabled)
        if enabled:
            self.auto_range()

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

    def _effective_range(self):
        return self._view_range or self._calculate_bounds()

    def _plot_rect(self):
        return QtCore.QRectF(self.rect()).adjusted(68, 22, -24, -52)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        self._paint(painter)

    def _paint(self, painter):
        antialias = getattr(QtGui.QPainter, "Antialiasing", None)
        if antialias is None:
            antialias = QtGui.QPainter.RenderHint.Antialiasing
        painter.setRenderHint(antialias, True)
        painter.fillRect(self.rect(), QtGui.QColor("#FFFFFF"))
        plot_rect = self._plot_rect()
        painter.fillRect(plot_rect, QtGui.QColor("#FBFCFE"))
        view = self._effective_range()
        self._draw_grid(painter, plot_rect, view)
        for curve in self._all_curves():
            if curve.visible:
                self._draw_curve(painter, plot_rect, view, curve)
        self._draw_legend(painter, plot_rect)
        self._draw_crosshair(painter, plot_rect, view)

    def _draw_grid(self, painter, rect, view):
        painter.setPen(QtGui.QPen(QtGui.QColor("#E2E8F0"), 1))
        font = painter.font(); font.setPointSize(8); painter.setFont(font)
        for index in range(7):
            ratio = index / 6
            px = rect.left() + ratio * rect.width()
            py = rect.bottom() - ratio * rect.height()
            painter.drawLine(QtCore.QPointF(px, rect.top()), QtCore.QPointF(px, rect.bottom()))
            painter.drawLine(QtCore.QPointF(rect.left(), py), QtCore.QPointF(rect.right(), py))
            x_value = view[0] + ratio * (view[1] - view[0])
            y_value = view[2] + ratio * (view[3] - view[2])
            painter.setPen(QtGui.QColor("#667085"))
            painter.drawText(QtCore.QRectF(px - 40, rect.bottom() + 6, 80, 18), QtCore.Qt.AlignCenter, f"{x_value:.3g}")
            painter.drawText(QtCore.QRectF(3, py - 9, 58, 18), QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, f"{y_value:.3g}")
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
            painter.setPen(QtGui.QPen(curve.color, 2)); painter.drawLine(box.left() + 8, y, box.left() + 30, y)
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
        text = f"X {x_value:.4f}   Y {y_value:.4f}"
        box = QtCore.QRectF(pos.x() + 8, pos.y() - 27, 155, 22)
        if box.right() > rect.right(): box.moveRight(pos.x() - 8)
        painter.fillRect(box, QtGui.QColor(16, 24, 40, 220))
        painter.setPen(QtGui.QColor("white")); painter.drawText(box, QtCore.Qt.AlignCenter, text)

    def mouseMoveEvent(self, event):
        position = _event_position(event)
        self._cursor_pos = QtCore.QPointF(position)
        if self._drag_origin is not None and self._drag_range is not None:
            dx = position.x() - self._drag_origin.x(); dy = position.y() - self._drag_origin.y()
            rect = self._plot_rect(); view = self._drag_range
            x_shift = -dx / rect.width() * (view[1] - view[0])
            y_shift = dy / rect.height() * (view[3] - view[2])
            self._view_range = (view[0] + x_shift, view[1] + x_shift, view[2] + y_shift, view[3] + y_shift)
            self.auto_range_enabled = False; self.user_zoomed.emit()
        self.update()

    def mousePressEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton and self._plot_rect().contains(_event_position(event)):
            self._drag_origin = QtCore.QPointF(_event_position(event)); self._drag_range = self._effective_range()

    def mouseReleaseEvent(self, event):
        self._drag_origin = None; self._drag_range = None

    def leaveEvent(self, event):
        self._cursor_pos = None; self.update()

    def mouseDoubleClickEvent(self, event):
        self.auto_range()

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
