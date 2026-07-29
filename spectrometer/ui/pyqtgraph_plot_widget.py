"""基于 PyQtGraph 的 ROCK 4B+ 实时光谱绘图控件。"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import numpy as np
import pyqtgraph as pg
from pyqtgraph.exporters import ImageExporter

from ..qt import QtCore, QtGui, Signal
from .plot_widget import COLOR_PALETTE, SpectrumPlotWidget


def _enum(owner, group: str, name: str):
    if hasattr(owner, name):
        return getattr(owner, name)
    return getattr(getattr(owner, group), name)


PAINT_EVENT = _enum(QtCore.QEvent, "Type", "Paint")


class _SpectrumAxisItem(pg.AxisItem):
    def __init__(self, orientation, *, prefer_integer=False):
        super().__init__(orientation=orientation)
        self.prefer_integer = bool(prefer_integer)

    def tickStrings(self, values, scale, spacing):
        return [
            SpectrumPlotWidget.format_axis_value(
                value * scale,
                prefer_integer=self.prefer_integer,
            )
            for value in values
        ]


class _SpectrumViewBox(pg.ViewBox):
    user_zoomed = Signal()
    reset_requested = Signal()

    def __init__(self):
        super().__init__(enableMenu=False)
        self.setMouseMode(self.RectMode)

    def mouseDragEvent(self, event, axis=None):
        if event.button() != QtCore.Qt.LeftButton:
            event.accept()
            return
        super().mouseDragEvent(event, axis=axis)
        if event.isFinish() and event.button() == QtCore.Qt.LeftButton:
            self.user_zoomed.emit()

    def wheelEvent(self, event, axis=None):
        event.accept()

    def mouseClickEvent(self, event):
        if event.button() != QtCore.Qt.LeftButton:
            event.accept()
            return
        super().mouseClickEvent(event)

    def mouseDoubleClickEvent(self, event):
        if event.button() == QtCore.Qt.LeftButton:
            event.accept()
            self.reset_requested.emit()
            return
        super().mouseDoubleClickEvent(event)


@dataclass
class _PlotCurve:
    item: pg.PlotDataItem
    x: np.ndarray
    y: np.ndarray
    color: str
    label: str
    visible: bool = True


class PyQtGraphSpectrumPlotWidget(pg.PlotWidget):
    """与 legacy ``SpectrumPlotWidget`` 兼容的高性能实时后端。"""

    user_zoomed = Signal()
    view_reset = Signal()
    data_frame_painted = Signal()

    def __init__(self, parent=None):
        pg.setConfigOptions(antialias=False, useOpenGL=False)
        self._bottom_axis = _SpectrumAxisItem("bottom", prefer_integer=True)
        self._left_axis = _SpectrumAxisItem("left", prefer_integer=True)
        self._view_box = _SpectrumViewBox()
        super().__init__(
            parent=parent,
            background="w",
            viewBox=self._view_box,
            axisItems={
                "bottom": self._bottom_axis,
                "left": self._left_axis,
            },
            enableMenu=False,
        )
        self.setObjectName("spectrumPlot")
        self.setMinimumSize(500, 360)
        self.setToolTip(
            "左键拖动框选放大；左键双击恢复初始视图"
        )
        self.setMouseTracking(True)

        self.device_curves: Dict[int, _PlotCurve] = {}
        self.reference_curves: Dict[str, _PlotCurve] = {}
        self.baseline_curves: Dict[int, _PlotCurve] = {}
        self.line_width = 1.0
        self.x_label = ""
        self.y_label = ""
        self.auto_range_enabled = True
        self.fixed_y_enabled = False
        self.fixed_y_range: Optional[Tuple[float, float]] = None
        self._view_range: Optional[Tuple[float, float, float, float]] = None
        self._data_generation = 0
        self._painted_generation = 0
        self._reference_index = 0

        self.plotItem.showGrid(x=True, y=True, alpha=0.22)
        self.plotItem.hideButtons()
        self.plotItem.setMenuEnabled(False)
        self.plotItem.enableAutoRange(x=False, y=False)
        self.legend = self.plotItem.addLegend(
            offset=(-12, 12),
            brush=pg.mkBrush(255, 255, 255, 225),
            pen=pg.mkPen("#D0D5DD"),
        )
        self.set_axis_labels("像素序号", "强度（计数）")

        self._crosshair_vertical = pg.InfiniteLine(
            angle=90,
            movable=False,
            pen=pg.mkPen("#98A2B3", width=1, style=QtCore.Qt.DashLine),
        )
        self._crosshair_horizontal = pg.InfiniteLine(
            angle=0,
            movable=False,
            pen=pg.mkPen("#98A2B3", width=1, style=QtCore.Qt.DashLine),
        )
        self._crosshair_label = pg.TextItem(
            color="white",
            fill=pg.mkBrush(16, 24, 40, 220),
            border=pg.mkPen("#344054"),
            anchor=(0, 1),
        )
        for item in (
            self._crosshair_vertical,
            self._crosshair_horizontal,
            self._crosshair_label,
        ):
            item.hide()
            self.plotItem.addItem(item, ignoreBounds=True)
        self._mouse_proxy = pg.SignalProxy(
            self.scene().sigMouseMoved,
            rateLimit=30,
            slot=self._mouse_moved,
        )

        self._view_box.user_zoomed.connect(self._manual_zoomed)
        self._view_box.reset_requested.connect(self.reset_initial_view)
        self.viewport().installEventFilter(self)

    @staticmethod
    def format_axis_value(value: float, prefer_integer: bool = False) -> str:
        return SpectrumPlotWidget.format_axis_value(value, prefer_integer)

    @staticmethod
    def _validated_arrays(x, y):
        x_array = np.ascontiguousarray(x, dtype=np.float64)
        y_array = np.ascontiguousarray(y, dtype=np.float64)
        if x_array.ndim != 1 or x_array.shape != y_array.shape:
            raise ValueError("绘图 x/y 必须是一维且长度相同")
        if not np.isfinite(x_array).all() or not np.isfinite(y_array).all():
            raise ValueError("绘图 x/y 必须全部为有限数值")
        return x_array, y_array

    def _create_item(self, x, y, color: str, label: str) -> pg.PlotDataItem:
        item = self.plotItem.plot(
            x,
            y,
            pen=pg.mkPen(color, width=self.line_width),
            name=label,
            antialias=False,
            connect="all",
        )
        item.setClipToView(True)
        item.setDownsampling(auto=True, method="peak")
        if hasattr(item, "setSkipFiniteCheck"):
            item.setSkipFiniteCheck(True)
        return item

    def _update_item(self, curve: _PlotCurve, x, y):
        curve.x = x
        curve.y = y
        curve.item.setData(x, y, connect="all")

    def update_device_curve(self, device_id: int, x, y, label: str = ""):
        x_array, y_array = self._validated_arrays(x, y)
        device_id = int(device_id)
        curve = self.device_curves.get(device_id)
        is_new = curve is None
        if curve is None:
            color = COLOR_PALETTE[device_id % len(COLOR_PALETTE)]
            curve = _PlotCurve(
                self._create_item(
                    x_array,
                    y_array,
                    color,
                    label or f"设备 {device_id}",
                ),
                x_array,
                y_array,
                color,
                label or f"设备 {device_id}",
            )
            self.device_curves[device_id] = curve
        else:
            self._update_item(curve, x_array, y_array)
        if self.auto_range_enabled:
            self._update_auto_range(force=is_new or self._view_range is None)
        self._data_generation += 1

    update_spectrum = update_device_curve

    def remove_device_curve(self, device_id: int):
        self._remove_curve(self.device_curves.pop(int(device_id), None))
        self._remove_curve(self.baseline_curves.pop(int(device_id), None))
        if self.auto_range_enabled:
            self._update_auto_range(force=True)

    def add_reference_curve(self, x, y, label: str = "对比光谱") -> bool:
        if len(self.reference_curves) >= 64:
            return False
        x_array, y_array = self._validated_arrays(x, y)
        self._reference_index += 1
        key = f"reference_{self._reference_index}"
        color = COLOR_PALETTE[
            (len(self.reference_curves) + 5) % len(COLOR_PALETTE)
        ]
        self.reference_curves[key] = _PlotCurve(
            self._create_item(x_array, y_array, color, label),
            x_array,
            y_array,
            color,
            label,
        )
        if self.auto_range_enabled:
            self._update_auto_range(force=True)
        return True

    def set_baseline_curve(self, device_id: int, x, y, visible: bool = True):
        x_array, y_array = self._validated_arrays(x, y)
        device_id = int(device_id)
        curve = self.baseline_curves.get(device_id)
        if curve is None:
            label = f"设备 {device_id} 基线"
            curve = _PlotCurve(
                self._create_item(x_array, y_array, "#8894A4", label),
                x_array,
                y_array,
                "#8894A4",
                label,
                bool(visible),
            )
            self.baseline_curves[device_id] = curve
        else:
            self._update_item(curve, x_array, y_array)
            curve.visible = bool(visible)
        curve.item.setVisible(bool(visible))
        if self.auto_range_enabled:
            self._update_auto_range(force=True)

    def clear_reference_curves(self):
        for curve in self.reference_curves.values():
            self._remove_curve(curve)
        self.reference_curves.clear()
        if self.auto_range_enabled:
            self._update_auto_range(force=True)

    def clear_all(self):
        for curve in self._all_curves():
            self._remove_curve(curve)
        self.device_curves.clear()
        self.reference_curves.clear()
        self.baseline_curves.clear()
        self._view_range = None
        self._update_auto_range(force=True)

    def _remove_curve(self, curve):
        if curve is None:
            return
        try:
            self.legend.removeItem(curve.item)
        except (KeyError, ValueError, TypeError):
            try:
                self.legend.removeItem(curve.label)
            except (KeyError, ValueError, TypeError):
                pass
        self.plotItem.removeItem(curve.item)

    def set_axis_labels(self, x_label: str, y_label: str):
        x_label = str(x_label)
        y_label = str(y_label)
        if self.x_label == x_label and self.y_label == y_label:
            return
        self.x_label = x_label
        self.y_label = y_label
        self._bottom_axis.prefer_integer = self.x_label.startswith("像素")
        self._left_axis.prefer_integer = self.y_label == "强度（计数）"
        self.plotItem.setLabel("bottom", self.x_label)
        self.plotItem.setLabel("left", self.y_label)

    def set_line_width(self, width: float):
        self.line_width = max(0.5, float(width))
        for curve in self._all_curves():
            curve.item.setPen(pg.mkPen(curve.color, width=self.line_width))

    def set_device_color(self, device_id: int, color):
        curve = self.device_curves.get(int(device_id))
        if curve is None:
            return
        curve.color = QtGui.QColor(color).name()
        curve.item.setPen(pg.mkPen(curve.color, width=self.line_width))

    def auto_range(self):
        self.reset_initial_view()

    def enable_auto_range(self, enabled: bool):
        self.auto_range_enabled = bool(enabled)
        if enabled:
            self.reset_initial_view()

    def set_fixed_y_range(self, minimum: float, maximum: float):
        minimum, maximum = self._validated_range(minimum, maximum, "Y")
        self.fixed_y_enabled = True
        self.fixed_y_range = (minimum, maximum)
        self.reset_initial_view()

    def disable_fixed_y(self):
        self.fixed_y_enabled = False
        self.fixed_y_range = None
        self.reset_initial_view()

    def reset_initial_view(self):
        self.auto_range_enabled = True
        self._update_auto_range(force=True)
        self.view_reset.emit()

    def set_x_range(self, minimum: float, maximum: float):
        minimum, maximum = self._validated_range(minimum, maximum, "X")
        current = self._effective_range()
        self.set_view_range(minimum, maximum, current[2], current[3])

    def set_y_range(self, minimum: float, maximum: float):
        minimum, maximum = self._validated_range(minimum, maximum, "Y")
        current = self._effective_range()
        self.set_view_range(current[0], current[1], minimum, maximum)

    def set_view_range(self, x_min, x_max, y_min, y_max):
        x_min, x_max = self._validated_range(x_min, x_max, "X")
        y_min, y_max = self._validated_range(y_min, y_max, "Y")
        self.auto_range_enabled = False
        self._apply_view_range((x_min, x_max, y_min, y_max))

    @staticmethod
    def _validated_range(minimum, maximum, axis):
        minimum = float(minimum)
        maximum = float(maximum)
        if not np.isfinite(minimum) or not np.isfinite(maximum):
            raise ValueError(f"{axis} 轴范围必须是有限数值")
        if minimum >= maximum:
            raise ValueError(f"{axis} 轴最小值必须小于最大值")
        return minimum, maximum

    def _all_curves(self):
        return (
            list(self.device_curves.values())
            + list(self.reference_curves.values())
            + list(self.baseline_curves.values())
        )

    def _calculate_bounds(self):
        curves = [
            curve
            for curve in self._all_curves()
            if curve.visible and curve.x.size
        ]
        if not curves:
            return 0.0, 4095.0, 0.0, 65535.0
        x_min = min(float(curve.x.min()) for curve in curves)
        x_max = max(float(curve.x.max()) for curve in curves)
        y_min = min(float(curve.y.min()) for curve in curves)
        y_max = max(float(curve.y.max()) for curve in curves)
        if x_max <= x_min:
            x_max = x_min + 1.0
        if y_max <= y_min:
            y_max = y_min + 1.0
        x_pad = (x_max - x_min) * 0.02
        y_pad = (y_max - y_min) * 0.08
        bounds = (
            x_min - x_pad,
            x_max + x_pad,
            y_min - y_pad,
            y_max + y_pad,
        )
        if self.fixed_y_enabled and self.fixed_y_range is not None:
            bounds = (bounds[0], bounds[1], *self.fixed_y_range)
        return bounds

    def _effective_range(self):
        if self._view_range is not None:
            return self._view_range
        return self._calculate_bounds()

    def _update_auto_range(self, *, force=False):
        target = self._calculate_bounds()
        current = self._view_range
        if not force and current is not None:
            outside = (
                target[0] < current[0]
                or target[1] > current[1]
                or (
                    not self.fixed_y_enabled
                    and (target[2] < current[2] or target[3] > current[3])
                )
            )
            if not outside:
                return
            target = (
                min(target[0], current[0]),
                max(target[1], current[1]),
                (
                    target[2]
                    if self.fixed_y_enabled
                    else min(target[2], current[2])
                ),
                (
                    target[3]
                    if self.fixed_y_enabled
                    else max(target[3], current[3])
                ),
            )
        self._apply_view_range(target)

    def _apply_view_range(self, view):
        self._view_range = tuple(float(value) for value in view)
        self.plotItem.setXRange(view[0], view[1], padding=0)
        self.plotItem.setYRange(view[2], view[3], padding=0)

    def _manual_zoomed(self):
        view = self._view_box.viewRange()
        self._view_range = (
            float(view[0][0]),
            float(view[0][1]),
            float(view[1][0]),
            float(view[1][1]),
        )
        self.auto_range_enabled = False
        self.user_zoomed.emit()

    def _mouse_moved(self, event):
        position = event[0] if isinstance(event, (tuple, list)) else event
        if not self.plotItem.sceneBoundingRect().contains(position):
            self._set_crosshair_visible(False)
            return
        point = self._view_box.mapSceneToView(position)
        self._crosshair_vertical.setPos(point.x())
        self._crosshair_horizontal.setPos(point.y())
        self._crosshair_label.setText(
            f"X {self.format_axis_value(point.x(), self._bottom_axis.prefer_integer)}"
            f"   Y {self.format_axis_value(point.y(), self._left_axis.prefer_integer)}"
        )
        self._crosshair_label.setPos(point.x(), point.y())
        self._set_crosshair_visible(True)

    def _set_crosshair_visible(self, visible):
        for item in (
            self._crosshair_vertical,
            self._crosshair_horizontal,
            self._crosshair_label,
        ):
            item.setVisible(bool(visible))

    def leaveEvent(self, event):
        self._set_crosshair_visible(False)
        super().leaveEvent(event)

    def eventFilter(self, watched, event):
        result = super().eventFilter(watched, event)
        if (
            watched is self.viewport()
            and event.type() == PAINT_EVENT
            and self._painted_generation != self._data_generation
        ):
            self._painted_generation = self._data_generation
            self.data_frame_painted.emit()
        return result

    def save_image(self, path: str) -> bool:
        view_before = self._effective_range()
        try:
            exporter = ImageExporter(self.plotItem)
            exporter.parameters()["width"] = max(1, self.width())
            exporter.export(str(path))
            return True
        except (OSError, RuntimeError, ValueError, TypeError):
            return False
        finally:
            if self._view_range != view_before:
                self._apply_view_range(view_before)
