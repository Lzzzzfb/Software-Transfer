import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.acquisition.controller import AcquisitionController
from spectrometer.communication.protocol import CmdCode, TriggerMode
from spectrometer.device.spectrometer import SpectrometerDevice
from spectrometer.domain.enums import (
    AcquisitionMode,
    ControlState,
    SyncMode,
)
from spectrometer.domain.models import SpectrumFrame
from spectrometer.qt import QtCore, Signal


class FakeDeviceManager(QtCore.QObject):
    command_completed = Signal(int, int, bool, object)

    def __init__(self, count=3):
        super().__init__()
        self.devices = {}
        self.calls = []
        for device_id in range(count):
            device = SpectrometerDevice(device_id, f"COM{device_id + 1}")
            device.connected = True
            device.initialized = True
            device.info.valid_pixel = 4
            device.info.pixel_count = 4
            device.info.prod_serial = f"00{device_id + 1}"
            self.devices[device_id] = device

    def get_device(self, device_id):
        return self.devices.get(device_id)

    def get_connected_devices(self):
        return [
            device
            for device in self.devices.values()
            if device.connected and device.initialized
        ]

    def set_trigger_mode(self, device_id, mode):
        self.calls.append(("trigger", device_id, int(mode)))
        return True

    def start_acquisition(self, device_id, continuous=True):
        self.calls.append(("start", device_id, bool(continuous)))
        return True

    def stop_acquisition(self, device_id):
        self.calls.append(("stop", device_id))
        return True

    def ack(self, device_id, cmd, success=True):
        self.command_completed.emit(
            device_id,
            int(cmd),
            success,
            b"\x60" if success else b"\x70",
        )


class FakeStorageManager(QtCore.QObject):
    session_closed = Signal(str, object, object)

    def __init__(self):
        super().__init__()
        self.started = []
        self.frames = []
        self.closed = []

    def start_session(self, request, devices):
        self.started.append((request, tuple(device.device_id for device in devices)))

    def submit(self, frame):
        self.frames.append(frame)

    def close_session(self, task_id):
        self.closed.append(task_id)
        self.session_closed.emit(task_id, [], [])


def frame(device_id, sequence=1):
    return SpectrumFrame.create(
        device_id,
        sequence << 8,
        [1, 2, 3, 4],
        monotonic_ns=sequence,
        timestamp_ns=sequence,
    )


def acknowledge_local_start(manager, device_id=0, continuous=True):
    manager.ack(device_id, CmdCode.SET_TRIG_MODE)
    manager.ack(
        device_id,
        CmdCode.START_CONTINUOUS if continuous else CmdCode.START_SINGLE,
    )


def test_local_start_always_configures_software_trigger_before_start():
    manager = FakeDeviceManager()
    controller = AcquisitionController(manager)

    assert controller.start_local(0, AcquisitionMode.CONTINUOUS)
    assert manager.calls == [("trigger", 0, int(TriggerMode.SOFTWARE))]
    assert controller.device_state(0) is ControlState.CONFIGURING

    manager.ack(0, CmdCode.SET_TRIG_MODE)
    assert manager.calls[-1] == ("start", 0, True)
    assert controller.device_state(0) is ControlState.STARTING

    manager.ack(0, CmdCode.START_CONTINUOUS)
    assert controller.device_state(0) is ControlState.ACQUIRING


def test_multiple_local_devices_can_run_and_stop_independently():
    manager = FakeDeviceManager()
    controller = AcquisitionController(manager, tail_quiet_ms=0)
    assert controller.start_local(0, AcquisitionMode.CONTINUOUS)
    assert controller.start_local(1, AcquisitionMode.CONTINUOUS)
    acknowledge_local_start(manager, 0)
    acknowledge_local_start(manager, 1)

    assert controller.stop_local(0)
    manager.ack(0, CmdCode.STOP_ACQUISITION)
    controller.finish_pending_stops()

    assert controller.device_state(0) is ControlState.IDLE
    assert controller.device_state(1) is ControlState.ACQUIRING


def test_global_start_is_rejected_while_any_local_device_is_busy():
    manager = FakeDeviceManager()
    controller = AcquisitionController(manager)
    rejected = []
    controller.operation_rejected.connect(rejected.append)
    controller.start_local(2, AcquisitionMode.CONTINUOUS)

    assert not controller.start_global(
        [0, 1], AcquisitionMode.CONTINUOUS, SyncMode.SOFTWARE
    )
    assert rejected and "COM3" in rejected[-1]
    assert not any(call[0] == "start" for call in manager.calls)


def test_card_start_is_rejected_during_global_configuration():
    manager = FakeDeviceManager()
    controller = AcquisitionController(manager)
    assert controller.start_global(
        [0, 1], AcquisitionMode.CONTINUOUS, SyncMode.SOFTWARE
    )
    calls_before = list(manager.calls)

    assert not controller.start_local(2, AcquisitionMode.CONTINUOUS)
    assert manager.calls == calls_before


def test_internal_hard_sync_starts_master_last_after_all_trigger_acks():
    manager = FakeDeviceManager()
    controller = AcquisitionController(manager)
    assert controller.start_global(
        [0, 1, 2],
        AcquisitionMode.CONTINUOUS,
        SyncMode.HARD_INTERNAL,
        master_device_id=1,
    )
    assert manager.calls == [
        ("trigger", 0, int(TriggerMode.EXTERNAL)),
        ("trigger", 1, int(TriggerMode.SOFT_MASTER)),
        ("trigger", 2, int(TriggerMode.EXTERNAL)),
    ]

    manager.ack(2, CmdCode.SET_TRIG_MODE)
    manager.ack(0, CmdCode.SET_TRIG_MODE)
    assert not any(call[0] == "start" for call in manager.calls)
    manager.ack(1, CmdCode.SET_TRIG_MODE)

    starts = [call for call in manager.calls if call[0] == "start"]
    assert [call[1] for call in starts] == [0, 2, 1]


def test_partial_global_start_failure_stops_devices_already_started():
    manager = FakeDeviceManager(count=2)
    controller = AcquisitionController(manager)
    controller.start_global(
        [0, 1], AcquisitionMode.CONTINUOUS, SyncMode.SOFTWARE
    )
    manager.ack(0, CmdCode.SET_TRIG_MODE)
    manager.ack(1, CmdCode.SET_TRIG_MODE)
    manager.ack(0, CmdCode.START_CONTINUOUS)
    manager.ack(1, CmdCode.START_CONTINUOUS, success=False)

    assert ("stop", 0) in manager.calls
    assert controller.device_state(0) is ControlState.STOPPING


def test_single_local_stops_after_first_current_task_frame():
    manager = FakeDeviceManager()
    controller = AcquisitionController(manager)
    controller.start_local(0, AcquisitionMode.SINGLE)
    acknowledge_local_start(manager, 0, continuous=False)

    controller.on_frame(frame(0))

    assert manager.calls[-1] == ("stop", 0)
    assert controller.device_state(0) is ControlState.STOPPING


def test_reference_capture_requires_global_idle_and_commits_fresh_frame():
    manager = FakeDeviceManager()
    committed = []
    controller = AcquisitionController(
        manager,
        reference_commit=lambda kind, frames: committed.append((kind, frames)),
    )
    controller.start_local(1, AcquisitionMode.CONTINUOUS)

    assert not controller.capture_local_reference(0, "background")

    acknowledge_local_start(manager, 1)
    controller.stop_local(1)
    manager.ack(1, CmdCode.STOP_ACQUISITION)
    controller.finish_pending_stops()
    assert controller.capture_local_reference(0, "background")
    manager.ack(0, CmdCode.SET_TRIG_MODE)
    manager.ack(0, CmdCode.START_SINGLE)
    controller.on_frame(frame(0, 9))
    manager.ack(0, CmdCode.STOP_ACQUISITION)
    controller.finish_pending_stops()

    assert committed and committed[0][0] == "background"
    assert committed[0][1][0].sequence == 9


def test_global_reference_is_committed_only_after_every_fresh_frame():
    manager = FakeDeviceManager(count=2)
    committed = []
    controller = AcquisitionController(
        manager,
        reference_commit=lambda kind, frames: committed.append((kind, frames)),
    )
    assert controller.capture_global_reference(
        [0, 1], "reference", SyncMode.SOFTWARE
    )
    manager.ack(0, CmdCode.SET_TRIG_MODE)
    manager.ack(1, CmdCode.SET_TRIG_MODE)
    manager.ack(0, CmdCode.START_SINGLE)
    manager.ack(1, CmdCode.START_SINGLE)

    controller.on_frame(frame(0, 3))
    assert committed == []
    assert not any(call[0] == "stop" for call in manager.calls)
    controller.on_frame(frame(1, 4))
    manager.ack(0, CmdCode.STOP_ACQUISITION)
    manager.ack(1, CmdCode.STOP_ACQUISITION)
    controller.finish_pending_stops()

    assert len(committed) == 1
    assert set(committed[0][1]) == {0, 1}


def test_global_reference_timeout_keeps_previous_references_unchanged():
    manager = FakeDeviceManager(count=2)
    committed = []
    controller = AcquisitionController(
        manager,
        reference_commit=lambda kind, frames: committed.append((kind, frames)),
    )
    controller.capture_global_reference([0, 1], "background")
    task_id = controller._global_task_id
    manager.ack(0, CmdCode.SET_TRIG_MODE)
    manager.ack(1, CmdCode.SET_TRIG_MODE)
    manager.ack(0, CmdCode.START_SINGLE)
    manager.ack(1, CmdCode.START_SINGLE)
    controller.on_frame(frame(0, 2))

    controller._on_frame_timeout(task_id)
    manager.ack(0, CmdCode.STOP_ACQUISITION)
    manager.ack(1, CmdCode.STOP_ACQUISITION)
    controller.finish_pending_stops()

    assert committed == []


def test_device_disconnect_finishes_local_task_without_waiting_for_stop_ack():
    manager = FakeDeviceManager(count=1)
    controller = AcquisitionController(manager)
    controller.start_local(0)
    acknowledge_local_start(manager, 0)
    manager.devices[0].connected = False

    controller._on_device_changed(0)
    controller.finish_pending_stops()

    assert controller.device_state(0) is ControlState.IDLE
