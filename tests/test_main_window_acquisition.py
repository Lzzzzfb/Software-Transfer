import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.domain.enums import AcquisitionOwner, ControlState
from spectrometer.domain.models import AcquisitionRequest, SpectrumFrame
from spectrometer.processing.references import ReferenceRepository
from spectrometer.qt import QtWidgets
from spectrometer.ui.main_window import MainWindow


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def wait_until(predicate, timeout=2.0):
    app = application()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def window(tmp_path):
    application()
    return MainWindow(
        simulation=True,
        auto_start_simulation=False,
        settings_path=tmp_path / "settings.json",
    )


def test_global_button_and_device_cards_follow_shared_state(tmp_path):
    item = window(tmp_path)
    assert item.start_acquisition()
    assert item.control.global_state is ControlState.ACQUIRING
    assert "停止" in item.ribbon.acquisition_button.text()
    assert all(
        card.acquisition_button.text() == "停止"
        for card in item.sidebar.cards.values()
    )

    assert item.stop_acquisition()
    assert wait_until(lambda: item.control.global_state is ControlState.IDLE)
    assert "开始" in item.ribbon.acquisition_button.text()
    assert all(
        card.acquisition_button.text() == "开始"
        for card in item.sidebar.cards.values()
    )
    item.close()
    application().processEvents()


def test_multiple_local_cards_run_independently_and_block_global_start(tmp_path):
    item = window(tmp_path)
    item._toggle_device_acquisition(0)
    item._toggle_device_acquisition(1)
    item._simulation_tick()
    received_before_rejection = item.acquisition.diagnostics(0).received
    assert item.control.device_state(0) is ControlState.ACQUIRING
    assert item.control.device_state(1) is ControlState.ACQUIRING
    assert item.control.global_state is ControlState.IDLE

    assert not item.start_acquisition()
    assert item.acquisition.diagnostics(0).received == received_before_rejection
    assert item.control.device_state(0) is ControlState.ACQUIRING
    assert item.control.device_state(1) is ControlState.ACQUIRING

    item._toggle_device_acquisition(0)
    assert wait_until(lambda: item.control.device_state(0) is ControlState.IDLE)
    assert item.control.device_state(1) is ControlState.ACQUIRING
    item._toggle_device_acquisition(1)
    assert wait_until(lambda: item.control.device_state(1) is ControlState.IDLE)
    item.close()
    application().processEvents()


def test_global_background_uses_fresh_frames_and_commits_all_devices(tmp_path):
    item = window(tmp_path)
    references = tmp_path / "references"
    item.reference_repository = ReferenceRepository(references)
    assert item.capture_background()
    assert item.control.global_state is ControlState.ACQUIRING

    item._simulation_tick()
    assert wait_until(lambda: item.control.global_state is ControlState.IDLE)
    assert len(list(references.glob("*.json"))) == 4
    assert all(
        device.background_spectrum is not None
        for device in item.device_manager.get_connected_devices()
    )
    item.close()
    application().processEvents()


def test_background_is_rejected_while_any_local_device_is_active(tmp_path):
    item = window(tmp_path)
    item._toggle_device_acquisition(2)
    assert not item.capture_reference()
    assert item.control.device_state(2) is ControlState.ACQUIRING
    item._toggle_device_acquisition(2)
    assert wait_until(lambda: item.control.device_state(2) is ControlState.IDLE)
    item.close()
    application().processEvents()


def test_display_thinning_does_not_create_missing_frame_warning(tmp_path):
    item = window(tmp_path)
    item._frame_arrived(
        SpectrumFrame.create(
            0, 0 << 8, [1, 2], sequence_bits=16, intentionally_skipped=0
        )
    )
    item._frame_arrived(
        SpectrumFrame.create(
            0, 5 << 8, [1, 2], sequence_bits=16, intentionally_skipped=4
        )
    )

    assert item.acquisition.diagnostics(0).missing == 0
    assert item._pending_missing_logs == {}
    assert "检测到缺少" not in item.diagnostics.log.toPlainText()
    item.close()
    application().processEvents()


def test_hidden_live_page_defers_processing_and_keeps_only_latest_frame(tmp_path):
    item = window(tmp_path)
    device = item.device_manager.get_device(0)
    pixel_count = device.info.valid_pixel
    item.tabs.setCurrentWidget(item.diagnostics)
    item.acquisition.ingest(
        SpectrumFrame.create(0, 1 << 8, [100] * pixel_count)
    )
    item._plot_tick()

    assert 0 not in item.plot_widget.device_curves
    assert item._pending_plot_frames[0].sequence == 1

    item.acquisition.ingest(
        SpectrumFrame.create(0, 2 << 8, [200] * pixel_count)
    )
    item._plot_tick()
    assert item._pending_plot_frames[0].sequence == 2

    item.tabs.setCurrentWidget(item.plot_widget)
    item._plot_tick()

    assert 0 in item.plot_widget.device_curves
    assert set(item.plot_widget.device_curves[0].y) == {200.0}
    assert item._pending_plot_frames == {}
    item.close()
    application().processEvents()


def test_finished_task_reports_outputs_and_recovery_files_separately(
    tmp_path,
):
    item = window(tmp_path)
    task_id = "task-with-recovery"
    item._task_requests[task_id] = AcquisitionRequest.create(
        AcquisitionOwner.LOCAL,
        [0],
        auto_store=True,
    )
    output = tmp_path / "batch.csv"
    recovery = tmp_path / "capture.zgs"

    item._control_task_finished(
        task_id,
        [str(output), str(recovery)],
        False,
    )

    log = item.diagnostics.log.toPlainText()
    assert "已生成 1 个 CSV/Excel 文件" in log
    assert "已保留 1 个恢复文件" in log
    assert str(recovery) in log
    assert "已生成 2 个文件" not in log
    item.close()
    application().processEvents()


def test_failed_storage_does_not_report_partial_outputs_as_complete(tmp_path):
    item = window(tmp_path)
    task_id = "failed-task-with-recovery"
    item._task_requests[task_id] = AcquisitionRequest.create(
        AcquisitionOwner.LOCAL,
        [0],
        auto_store=True,
    )

    item._control_task_finished(
        task_id,
        [str(tmp_path / "partial.csv"), str(tmp_path / "capture.zgs")],
        True,
    )

    log = item.diagnostics.log.toPlainText()
    assert "已生成 1 个 CSV/Excel 文件，但存储任务未全部完成" in log
    assert "采集存储完成" not in log
    item.close()
    application().processEvents()
