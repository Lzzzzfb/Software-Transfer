from types import SimpleNamespace

import pytest

from spectrometer.ui.plot_backend import resolve_plot_backend
from spectrometer.ui.plot_widget import SpectrumPlotWidget


class FakePyQtGraphWidget:
    pass


def fake_pyqtgraph_import(name):
    assert name == "spectrometer.ui.pyqtgraph_plot_widget"
    return SimpleNamespace(PyQtGraphSpectrumPlotWidget=FakePyQtGraphWidget)


def missing_pyqtgraph_import(_name):
    raise ImportError("pyqtgraph is not installed")


def test_forced_legacy_backend_never_imports_pyqtgraph():
    def unexpected_import(_name):
        raise AssertionError("legacy 后端不应导入 PyQtGraph")

    widget_class, info = resolve_plot_backend(
        requested="legacy",
        platform_name="linux",
        import_module=unexpected_import,
    )

    assert widget_class is SpectrumPlotWidget
    assert info.requested == "legacy"
    assert info.active == "legacy"
    assert info.fallback_reason == ""


def test_forced_pyqtgraph_backend_uses_new_widget():
    widget_class, info = resolve_plot_backend(
        requested="pyqtgraph",
        platform_name="linux",
        import_module=fake_pyqtgraph_import,
    )

    assert widget_class is FakePyQtGraphWidget
    assert info.active == "pyqtgraph"
    assert info.fallback_reason == ""


def test_forced_pyqtgraph_backend_reports_missing_dependency():
    with pytest.raises(RuntimeError, match="PyQtGraph"):
        resolve_plot_backend(
            requested="pyqtgraph",
            platform_name="linux",
            import_module=missing_pyqtgraph_import,
        )


def test_linux_auto_falls_back_to_legacy_with_reason():
    widget_class, info = resolve_plot_backend(
        requested="",
        platform_name="linux",
        import_module=missing_pyqtgraph_import,
    )

    assert widget_class is SpectrumPlotWidget
    assert info.requested == "auto"
    assert info.active == "legacy"
    assert "pyqtgraph is not installed" in info.fallback_reason


def test_non_linux_auto_keeps_legacy_backend():
    widget_class, info = resolve_plot_backend(
        requested="",
        platform_name="win32",
        import_module=fake_pyqtgraph_import,
    )

    assert widget_class is SpectrumPlotWidget
    assert info.requested == "auto"
    assert info.active == "legacy"
    assert info.fallback_reason == ""


def test_unknown_backend_name_is_rejected():
    with pytest.raises(ValueError, match="ZGCAI_PLOT_BACKEND"):
        resolve_plot_backend(requested="fastest", platform_name="linux")
