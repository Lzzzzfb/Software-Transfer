"""实时光谱绘图后端选择与显式回退。"""

from dataclasses import dataclass
import importlib
import os
import sys

from .plot_widget import SpectrumPlotWidget


@dataclass(frozen=True)
class PlotBackendInfo:
    requested: str
    active: str
    fallback_reason: str = ""


def resolve_plot_backend(
    *,
    requested=None,
    platform_name=None,
    import_module=importlib.import_module,
):
    """返回实时绘图控件类型和可写入诊断记录的选择结果。"""

    value = (
        os.environ.get("ZGCAI_PLOT_BACKEND", "")
        if requested is None
        else requested
    )
    requested_name = str(value or "").strip().lower()
    if requested_name not in {"", "legacy", "pyqtgraph"}:
        raise ValueError(
            "ZGCAI_PLOT_BACKEND 只能是 pyqtgraph 或 legacy"
        )
    label = requested_name or "auto"
    if requested_name == "legacy":
        return SpectrumPlotWidget, PlotBackendInfo(label, "legacy")

    platform_name = sys.platform if platform_name is None else str(platform_name)
    should_try_pyqtgraph = requested_name == "pyqtgraph" or platform_name.startswith(
        "linux"
    )
    if not should_try_pyqtgraph:
        return SpectrumPlotWidget, PlotBackendInfo(label, "legacy")

    try:
        module = import_module("spectrometer.ui.pyqtgraph_plot_widget")
        widget_class = module.PyQtGraphSpectrumPlotWidget
    except Exception as exc:
        if requested_name == "pyqtgraph":
            raise RuntimeError(
                f"已强制使用 PyQtGraph，但绘图后端不可用：{exc}"
            ) from exc
        return SpectrumPlotWidget, PlotBackendInfo(
            label,
            "legacy",
            f"PyQtGraph 不可用，已降级到 legacy：{exc}",
        )
    return widget_class, PlotBackendInfo(label, "pyqtgraph")


def create_spectrum_plot_widget(parent=None):
    widget_class, info = resolve_plot_backend()
    return widget_class(parent), info
