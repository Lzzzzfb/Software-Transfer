import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.domain.enums import ControlState
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
