import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.models import Axis, Direction, MotorAxisStatus, MotorStatus
from spectrometer.motor.process_proxy import MotorProcessProxy
from spectrometer.motor.settings_store import HostMotorSettings
from spectrometer.qt import QtCore


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    return _APPLICATION


class Pipe:
    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)


def unknown_status():
    return MotorStatus(
        MotorAxisStatus(Axis.X, None, None, False, False, None),
        MotorAxisStatus(Axis.Y, None, None, False, False, None),
    )


def test_proxy_queues_commands_without_blocking_gui(tmp_path):
    application()
    proxy = MotorProcessProxy(
        settings_path=tmp_path / "motor.json", autostart=False
    )
    pipe = Pipe()
    proxy._control = pipe

    assert proxy.set_scan_active(True)
    assert proxy.prepare_scan_round()
    assert proxy.start_scan_path(
        "path-1", (), returning=False
    )
    operation_id = proxy.move_relative(Axis.X, 1.0, Direction.POSITIVE)

    names = [message[2] for message in pipe.messages]
    assert names == [
        "set_scan_active",
        "prepare_scan_round",
        "start_scan_path",
        "move_relative",
    ]
    assert operation_id


def test_proxy_mirrors_child_snapshot_and_signals(tmp_path):
    application()
    proxy = MotorProcessProxy(
        settings_path=tmp_path / "motor.json", autostart=False
    )
    status = unknown_status()
    host = HostMotorSettings(port_name="/dev/ttyUSB0")
    changes = []
    proxy.connection_changed.connect(
        lambda connected, detail: changes.append((connected, detail))
    )

    proxy._dispatch(
        (
            "snapshot",
            {
                "connected": True,
                "device_id": "LK-MD2202:test",
                "status": status,
                "configuration": None,
                "host_settings": host,
                "motion_active": False,
                "scan_active": True,
                "safety_locked": False,
                "mechanics_valid": True,
            },
        )
    )
    proxy._dispatch(("connection_changed", True, "ttyUSB0"))

    assert proxy.connected is True
    assert proxy.device_id == "LK-MD2202:test"
    assert proxy.status == status
    assert proxy.host_settings == host
    assert proxy.scan_active is True
    assert changes == [(True, "ttyUSB0")]


def test_proxy_reports_unexpected_process_exit_once(tmp_path):
    application()
    proxy = MotorProcessProxy(
        settings_path=tmp_path / "motor.json", autostart=False
    )
    failures = []
    disconnected = []
    proxy.operation_failed.connect(failures.append)
    proxy.connection_changed.connect(
        lambda connected, detail: disconnected.append((connected, detail))
    )

    proxy._handle_process_exit("motor_process_exited:3")
    proxy._handle_process_exit("motor_process_exited:3")

    assert failures == ["电机控制进程异常退出：motor_process_exited:3"]
    assert disconnected == [(False, "motor_process_exited:3")]


def test_spawned_motor_process_starts_and_shuts_down_cleanly(tmp_path):
    application()
    proxy = MotorProcessProxy(settings_path=tmp_path / "motor.json")
    deadline = time.monotonic() + 3.0
    while time.monotonic() < deadline:
        application().processEvents()
        if proxy.process_id and proxy._process.is_alive():
            break
        time.sleep(0.01)

    assert proxy.process_id
    assert proxy._process.is_alive()
    proxy.shutdown()
    assert proxy._process is None
