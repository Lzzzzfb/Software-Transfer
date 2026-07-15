"""
实时光谱可视化组件 — 基于pyqtgraph实现高性能绘图。
支持缩放、平移、对比光谱叠加、多设备分色显示。
"""

from typing import Dict, List, Optional, Tuple
import numpy as np

import pyqtgraph as pg
from PyQt5.QtCore import Qt, QPointF, pyqtSignal
from PyQt5.QtGui import QColor, QPen


# 预定义的颜色循环 (最多支持64种)
COLOR_PALETTE = [
    (0, 114, 189), (217, 83, 25), (237, 177, 32), (126, 47, 142),
    (119, 172, 48), (77, 190, 238), (162, 20, 47), (76, 67, 27),
    (0, 163, 136), (189, 126, 190), (255, 127, 14), (174, 199, 232),
    (255, 187, 120), (44, 160, 44), (152, 223, 138), (196, 156, 148),
    (140, 86, 75), (227, 119, 194), (127, 127, 127), (199, 199, 199),
    (188, 189, 34), (23, 190, 207), (31, 119, 180), (255, 20, 147),
    (0, 128, 128), (128, 0, 0), (128, 128, 0), (0, 0, 128),
    (70, 130, 180), (255, 69, 0), (50, 205, 50), (138, 43, 226),
    (0, 255, 127), (220, 20, 60), (0, 191, 255), (255, 215, 0),
    (102, 205, 170), (233, 150, 122), (65, 105, 225), (218, 112, 214),
    (135, 206, 235), (238, 130, 238), (144, 238, 144), (255, 182, 193),
    (176, 196, 222), (240, 128, 128), (147, 112, 219), (0, 250, 154),
    (255, 228, 181), (154, 205, 50), (186, 85, 211), (100, 149, 237),
    (205, 92, 92), (222, 184, 135), (95, 158, 160), (210, 105, 30),
    (139, 69, 19), (85, 107, 47), (189, 183, 107), (128, 128, 128),
    (72, 61, 139), (143, 188, 143), (160, 82, 45), (123, 104, 238),
]


class SpectrumPlotWidget(pg.GraphicsLayoutWidget):
    """光谱实时绘图组件"""

    curve_selected = pyqtSignal(int)  # curve_index

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground('#fafbfc')

        # 主绘图区
        self.plot = self.addPlot(row=0, col=0)

        # 坐标轴标签
        self.plot.setLabel('bottom', '像素序号', color='#555')
        self.plot.setLabel('left', '强度', units='counts', color='#555')
        self.plot.getAxis('left').enableAutoSIPrefix(False)

        # 网格
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.getAxis('bottom').setPen(pg.mkPen(color='#c8d6e5', width=1))
        self.plot.getAxis('left').setPen(pg.mkPen(color='#c8d6e5', width=1))

        # 图例 (勾选控制曲线显隐)
        self.legend = self.plot.addLegend(offset=(5, 5))

        # 缩放交互
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.enableAutoRange()
        self.set_rect_zoom_mode(False)

        # 曲线管理
        self.device_curves: Dict[int, pg.PlotDataItem] = {}
        self.reference_curves: List[pg.PlotDataItem] = []
        self.line_width = 1.0

        # 图例项勾选框追踪
        self._legend_checkable = False

        # 右键菜单
        self.plot.scene().contextMenu = None
        self.plot.setMenuEnabled(False)

        # 悬停提示 (X, Y)
        self._hover_marker = pg.ScatterPlotItem(
            size=8, pen=pg.mkPen(color='#222', width=1),
            brush=pg.mkBrush(255, 230, 0, 200))
        self._hover_marker.setZValue(10)
        self._hover_marker.hide()
        self.plot.addItem(self._hover_marker)
        self._hover_label = pg.TextItem(color='#111', anchor=(0, 1),
                                        fill=pg.mkBrush(255, 255, 255, 220))
        self._hover_label.setZValue(10)
        self._hover_label.hide()
        self.plot.addItem(self._hover_label)
        self._mouse_move_proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved)

    def set_rect_zoom_mode(self, enabled: bool):
        """切换框选缩放模式"""
        vb = self.plot.getViewBox()
        if enabled:
            vb.setMouseMode(vb.RectMode)
        else:
            vb.setMouseMode(vb.PanMode)

    def _hide_hover(self):
        self._hover_marker.hide()
        self._hover_label.hide()

    def _on_mouse_moved(self, evt):
        pos = evt[0] if isinstance(evt, (tuple, list)) else evt
        if not self.plot.sceneBoundingRect().contains(pos):
            self._hide_hover()
            return

        curves = list(self.device_curves.values()) + list(self.reference_curves)
        if not curves:
            self._hide_hover()
            return

        vb = self.plot.getViewBox()
        view_pos = vb.mapSceneToView(pos)
        x = view_pos.x()

        best = None
        best_dist2 = None
        for curve in curves:
            xdata, ydata = curve.getData()
            if xdata is None or ydata is None or len(xdata) == 0:
                continue

            if len(xdata) >= 2 and xdata[0] <= xdata[-1]:
                idx = int(np.searchsorted(xdata, x))
                candidates = []
                if 0 <= idx < len(xdata):
                    candidates.append(idx)
                if idx - 1 >= 0:
                    candidates.append(idx - 1)
            else:
                idx = int(np.argmin(np.abs(xdata - x)))
                candidates = [idx]

            for i in candidates:
                px = float(xdata[i])
                py = float(ydata[i])
                pt_scene = vb.mapViewToScene(QPointF(px, py))
                dx = pt_scene.x() - pos.x()
                dy = pt_scene.y() - pos.y()
                dist2 = dx * dx + dy * dy
                if best_dist2 is None or dist2 < best_dist2:
                    best_dist2 = dist2
                    best = (px, py)

        if best is None or best_dist2 is None or best_dist2 > 120:  # 约11px半径
            self._hide_hover()
            return

        bx, by = best
        self._hover_marker.setData([bx], [by])
        self._hover_marker.show()
        self._hover_label.setText(f'({bx:.4f}, {by:.4f})')
        self._hover_label.setPos(bx, by)
        self._hover_label.show()
    def update_device_curve(self, device_id: int, wavelengths: np.ndarray,
                            intensities: np.ndarray, label: str = ''):
        """更新设备实时曲线 — label 变化时自动重建曲线和图例"""
        existing = self.device_curves.get(device_id)
        old_name = existing.opts.get('name', '') if existing else ''

        if label and old_name and label != old_name:
            # 标签变化(例如序列号到达) → 重建曲线
            self.legend.removeItem(old_name)
            self.plot.removeItem(existing)
            del self.device_curves[device_id]
            existing = None

        if existing is None:
            color = COLOR_PALETTE[device_id % len(COLOR_PALETTE)]
            pen = pg.mkPen(color=color, width=self.line_width)
            curve = self.plot.plot(pen=pen, name=label or f'Ch{device_id}')
            self.device_curves[device_id] = curve
            self._make_legend_checkable()
        else:
            curve = existing
        curve.setData(wavelengths, intensities)

    def _make_legend_checkable(self):
        """确保所有图例项可勾选 — 显示复选框控制曲线显隐"""
        if self._legend_checkable:
            return
        try:
            for item in self.legend.items:
                # pyqtgraph 0.14+: items 是 ItemSample 或 (curve, label) 元组
                sample = item[0] if isinstance(item, (tuple, list)) else item
                if hasattr(sample, 'setCheckable'):
                    sample.setCheckable(True)
                    sample.setCheckState(True)
                elif hasattr(item, 'setCheckable'):
                    item.setCheckable(True)
                    item.setCheckState(True)
            self._legend_checkable = True
        except Exception:
            self._legend_checkable = True  # 避免反复重试

    def add_reference_curve(self, wavelengths: np.ndarray, intensities: np.ndarray,
                            label: str, color: Optional[Tuple[int, int, int]] = None):
        """添加对比光谱曲线"""
        idx = len(self.reference_curves)
        if idx >= 64:
            return
        if color is None:
            color = COLOR_PALETTE[(idx + len(self.device_curves)) % len(COLOR_PALETTE)]
        pen = pg.mkPen(color=color, width=self.line_width, style=Qt.DashLine)
        curve = self.plot.plot(pen=pen, name=label)
        curve.setData(wavelengths, intensities)
        self.reference_curves.append(curve)
        self._make_legend_checkable()
        return idx

    def remove_reference_curve(self, index: int):
        """移除指定对比曲线"""
        if 0 <= index < len(self.reference_curves):
            curve = self.reference_curves[index]
            self.legend.removeItem(curve.opts.get('name', ''))
            self.plot.removeItem(curve)
            self.reference_curves.pop(index)

    def clear_reference_curves(self):
        """清除所有对比曲线"""
        for curve in self.reference_curves:
            self.legend.removeItem(curve.opts.get('name', ''))
            self.plot.removeItem(curve)
        self.reference_curves.clear()

    def clear_all(self):
        """清除所有曲线"""
        self.legend.clear()
        for curve in self.device_curves.values():
            self.plot.removeItem(curve)
        self.device_curves.clear()
        self.clear_reference_curves()
        self._legend_checkable = False

    def set_line_width(self, width: float):
        """设置曲线粗细"""
        self.line_width = width
        for curve in self.device_curves.values():
            curve.setPen(pg.mkPen(color=curve.opts['pen'].color(), width=width))

    def set_device_color(self, device_id: int, color: QColor):
        """设置设备曲线颜色"""
        if device_id in self.device_curves:
            self.device_curves[device_id].setPen(
                pg.mkPen(color=color, width=self.line_width))

    def auto_range(self):
        """自动缩放"""
        self.plot.autoRange()

    def set_x_range(self, x_min: float, x_max: float):
        self.plot.setXRange(x_min, x_max)

    def set_y_range(self, y_min: float, y_max: float):
        self.plot.setYRange(y_min, y_max)

    def set_view_range(self, x_min: float, x_max: float, y_min: float, y_max: float):
        self.plot.setRange(xRange=(x_min, x_max), yRange=(y_min, y_max))


class HistoryPlotWidget(pg.GraphicsLayoutWidget):
    """历史数据查看组件 — 支持多标签页"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackground('#fafbfc')

        self.plot = self.addPlot(row=0, col=0)
        self.plot.setLabel('bottom', '像素序号', color='#555')
        self.plot.setLabel('left', '强度', units='counts', color='#555')
        self.plot.getAxis('left').enableAutoSIPrefix(False)
        self.plot.showGrid(x=True, y=True, alpha=0.25)
        self.plot.getAxis('bottom').setPen(pg.mkPen(color='#c8d6e5', width=1))
        self.plot.getAxis('left').setPen(pg.mkPen(color='#c8d6e5', width=1))
        self.plot.setMouseEnabled(x=True, y=True)
        self.plot.scene().contextMenu = None

        self.curves: List[pg.PlotDataItem] = []
        self._curve_count = 0

        # 悬停提示 (X, Y)
        self._hover_marker = pg.ScatterPlotItem(
            size=8, pen=pg.mkPen(color='#222', width=1),
            brush=pg.mkBrush(255, 230, 0, 200))
        self._hover_marker.setZValue(10)
        self._hover_marker.hide()
        self.plot.addItem(self._hover_marker)
        self._hover_label = pg.TextItem(color='#111', anchor=(0, 1),
                                        fill=pg.mkBrush(255, 255, 255, 220))
        self._hover_label.setZValue(10)
        self._hover_label.hide()
        self.plot.addItem(self._hover_label)
        self._mouse_move_proxy = pg.SignalProxy(
            self.plot.scene().sigMouseMoved, rateLimit=60, slot=self._on_mouse_moved)

    def _hide_hover(self):
        self._hover_marker.hide()
        self._hover_label.hide()

    def _on_mouse_moved(self, evt):
        pos = evt[0] if isinstance(evt, (tuple, list)) else evt
        if not self.plot.sceneBoundingRect().contains(pos):
            self._hide_hover()
            return

        if not self.curves:
            self._hide_hover()
            return

        vb = self.plot.getViewBox()
        view_pos = vb.mapSceneToView(pos)
        x = view_pos.x()

        best = None
        best_dist2 = None
        for curve in self.curves:
            xdata, ydata = curve.getData()
            if xdata is None or ydata is None or len(xdata) == 0:
                continue

            if len(xdata) >= 2 and xdata[0] <= xdata[-1]:
                idx = int(np.searchsorted(xdata, x))
                candidates = []
                if 0 <= idx < len(xdata):
                    candidates.append(idx)
                if idx - 1 >= 0:
                    candidates.append(idx - 1)
            else:
                idx = int(np.argmin(np.abs(xdata - x)))
                candidates = [idx]

            for i in candidates:
                px = float(xdata[i])
                py = float(ydata[i])
                pt_scene = vb.mapViewToScene(QPointF(px, py))
                dx = pt_scene.x() - pos.x()
                dy = pt_scene.y() - pos.y()
                dist2 = dx * dx + dy * dy
                if best_dist2 is None or dist2 < best_dist2:
                    best_dist2 = dist2
                    best = (px, py)

        if best is None or best_dist2 is None or best_dist2 > 120:  # 约11px半径
            self._hide_hover()
            return

        bx, by = best
        self._hover_marker.setData([bx], [by])
        self._hover_marker.show()
        self._hover_label.setText(f'({bx:.4f}, {by:.4f})')
        self._hover_label.setPos(bx, by)
        self._hover_label.show()

    def load_spectrum(self, wavelengths: np.ndarray, intensities: np.ndarray,
                      label: str = '', color: Optional[Tuple[int, int, int]] = None):
        """加载一条光谱数据"""
        idx = self._curve_count
        if idx >= 64:
            return
        if color is None:
            color = COLOR_PALETTE[idx % len(COLOR_PALETTE)]
        pen = pg.mkPen(color=color, width=1.5)
        curve = self.plot.plot(pen=pen, name=label)
        curve.setData(wavelengths, intensities)
        self.curves.append(curve)
        self._curve_count += 1

    def clear(self):
        for curve in self.curves:
            self.plot.removeItem(curve)
        self.curves.clear()
        self._curve_count = 0
