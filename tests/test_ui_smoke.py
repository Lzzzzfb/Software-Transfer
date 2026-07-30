import os
import json

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np

from spectrometer.qt import QtWidgets
from spectrometer.ui.main_window import MainWindow
from spectrometer.ui.plot_widget import SpectrumPlotWidget


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def test_main_window_follows_ribbon_sidebar_plot_status_layout(tmp_path):
    app = application()
    window = MainWindow(simulation=True, auto_start_simulation=False, settings_path=tmp_path / "settings.json")
    assert window.ribbon.objectName() == "mainRibbon"
    assert window.sidebar.objectName() == "deviceSidebar"
    assert window.plot_widget.objectName() == "spectrumPlot"
    expected_backend = (
        "pyqtgraph"
        if os.environ.get("ZGCAI_PLOT_BACKEND") == "pyqtgraph"
        else "legacy"
    )
    assert window._plot_backend_info.active == expected_backend
    assert (
        f"实时绘图后端：{expected_backend}"
        in window.diagnostics.log.toPlainText()
    )
    assert window.sidebar.findChild(QtWidgets.QLabel, "sectionTitle").text() == "设备"
    assert not hasattr(window.sidebar, "acquisition_mode")
    assert not hasattr(window.sidebar, "batch_size")
    assert not hasattr(window.sidebar, "storage_format")
    assert not hasattr(window.sidebar, "auto_store")
    assert window.acquisition_mode.currentText() == "连续采集"
    assert window.settings["batch_size"] == 500
    assert window.settings["storage_format"] == "csv_excel"
    assert window.settings["auto_store"] is False
    assert len(window.sidebar.cards) == 4
    assert not hasattr(window.sidebar, "port_combo")
    assert not hasattr(window.sidebar, "baud_combo")
    assert window.display_fps == 25
    assert window.plot_timer.interval() == 40
    assert window.diagnostics.export_button.text() == "导出诊断包"
    assert not window.diagnostics.include_recent_frames.isChecked()
    assert window.diagnostics.export_button.isEnabled()
    assert "history" not in window.ribbon.buttons
    assert "acquisition" in window.ribbon.buttons
    context_labels = {
        button.text()
        for button in window.context_bar.findChildren(QtWidgets.QPushButton)
    }
    history_labels = {
        button.text()
        for button in window.history_viewer.findChildren(QtWidgets.QPushButton)
    }
    assert "打开文件" in context_labels
    assert "保存光谱" in context_labels
    assert not window.save_spectrum_button.isEnabled()
    assert "保存图片" in context_labels
    assert "Y 轴设置…" in context_labels
    assert "应用 Y 轴" not in context_labels
    assert not hasattr(window, "fixed_y")
    assert not hasattr(window, "y_minimum")
    assert not hasattr(window, "y_maximum")
    assert "airPLS 参数" in context_labels
    assert hasattr(window, "airpls_enabled")
    assert not window.airpls_enabled.isChecked()
    assert "打开历史" not in context_labels
    assert "打开 CSV / Excel" in history_labels
    assert all(card.enabled.text() == "参与总控" for card in window.sidebar.cards.values())
    assert all(card.acquisition_button.text() == "开始" for card in window.sidebar.cards.values())
    window.close(); app.processEvents()


def test_global_airpls_settings_are_loaded_and_saved(tmp_path):
    app = application()
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(
        json.dumps(
            {
                "airpls_enabled": True,
                "airpls_lam": 250000.0,
                "airpls_order": 3,
                "airpls_max_iter": 21,
            }
        ),
        encoding="utf-8",
    )
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=settings_path,
    )
    assert window.airpls_enabled.isChecked()
    profile = window._global_airpls_profile()
    assert profile.enabled
    assert profile.lam == 250000.0
    assert profile.order == 3
    assert profile.max_iter == 21

    window.airpls_enabled.setChecked(False)
    window.close()
    app.processEvents()

    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["airpls_enabled"] is False
    assert stored["airpls_lam"] == 250000.0
    assert stored["airpls_order"] == 3
    assert stored["airpls_max_iter"] == 21


def test_auto_store_is_unchecked_even_if_previous_settings_enabled_it(tmp_path):
    app = application()
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"auto_store": True}), encoding="utf-8")
    window = MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=settings_path,
    )
    assert window.settings["auto_store"] is False
    window.close(); app.processEvents()
    stored = json.loads(settings_path.read_text(encoding="utf-8"))
    assert stored["auto_store"] is False


def test_settings_dialog_contains_moved_storage_controls(tmp_path):
    app = application()
    from spectrometer.ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog(
        {
            "storage_path": str(tmp_path),
            "line_width": 1.6,
            "batch_size": 321,
            "storage_format": "csv",
            "auto_store": True,
        }
    )

    assert dialog.batch_size.value() == 321
    assert dialog.storage_format.currentData() == "csv"
    assert dialog.auto_store.isChecked()
    assert dialog.values() == {
        "storage_path": str(tmp_path),
        "line_width": 1.6,
        "batch_size": 321,
        "storage_format": "csv",
        "auto_store": True,
    }
    dialog.close()
    app.processEvents()


def test_custom_plot_runs_without_pyqtgraph_and_preserves_manual_range():
    application()
    plot = SpectrumPlotWidget(); plot.resize(800, 500)
    x = np.arange(100); plot.update_device_curve(0, x, np.sin(x / 10), "模拟设备")
    plot.set_view_range(10, 30, -1, 1)
    assert plot._effective_range() == (10, 30, -1, 1)
    assert plot.add_reference_curve(x, np.cos(x / 10), "参考")


def test_plot_reports_completed_new_data_paint():
    app = application()
    plot = SpectrumPlotWidget()
    plot.resize(800, 500)
    painted = []
    plot.data_frame_painted.connect(lambda: painted.append(True))
    plot.show()
    plot.update_device_curve(0, np.arange(100), np.arange(100), "device")
    app.processEvents()
    assert painted == [True]
    plot.close()
