import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.communication.protocol import TriggerMode
from spectrometer.device.device_manager import DeviceManager
from spectrometer.qt import QtCore, QtWidgets


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def run_timers(milliseconds=500):
    loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(milliseconds, loop.quit)
    loop.exec() if hasattr(loop, "exec") else loop.exec_()


def manager_with_three_devices():
    manager = DeviceManager()
    for index in range(3):
        manager.add_simulated_device(f"SIM{index}", f"S{index}", 8, 300 + index * 100, 1)
    return manager


def test_internal_hard_sync_configures_slaves_and_starts_master_last():
    application()
    manager = manager_with_three_devices()
    manager.global_sync_enabled = True
    manager.sync_mode = "hard"
    manager.master_device_id = 1
    order = []
    manager.start_acquisition = lambda device_id, continuous=True: order.append(device_id) or True

    assert manager.prepare_sync_acquisition(True)
    run_timers()

    assert [manager.devices[index].trigger_mode for index in range(3)] == [
        TriggerMode.EXTERNAL,
        TriggerMode.SOFT_MASTER,
        TriggerMode.EXTERNAL,
    ]
    assert order == [0, 2, 1]


def test_external_hard_sync_configures_every_device_for_external_trigger():
    application()
    manager = manager_with_three_devices()
    manager.global_sync_enabled = True
    manager.sync_mode = "hard"
    manager.master_device_id = None
    order = []
    manager.start_acquisition = lambda device_id, continuous=True: order.append(device_id) or True

    assert manager.prepare_sync_acquisition(True)
    run_timers()

    assert [manager.devices[index].trigger_mode for index in range(3)] == [
        TriggerMode.EXTERNAL,
        TriggerMode.EXTERNAL,
        TriggerMode.EXTERNAL,
    ]
    assert order == [0, 1, 2]


def test_parameter_commands_are_rejected_while_streaming():
    application()
    manager = manager_with_three_devices()
    errors = []
    manager.error_occurred.connect(lambda device_id, message: errors.append((device_id, message)))

    assert manager.start_acquisition(0, True)
    assert not manager.set_integration_time(0, 20_000)
    assert errors and "采集中" in errors[-1][1]

    manager.stop_acquisition(0)
    assert manager.set_integration_time(0, 20_000)
