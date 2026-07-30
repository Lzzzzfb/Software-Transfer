import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.controller import MotorController
from spectrometer.motor.models import Axis, Direction, Position
from spectrometer.motor.protocol import parse_response
from spectrometer.motor.state_store import MotorStateStore
from spectrometer.qt import QtCore, QtWidgets, Signal


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


class FakeTransport(QtCore.QObject):
    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)
    diagnostic_event = Signal(str)

    def __init__(self):
        super().__init__()
        self.connected = False
        self.port_name = ""
        self.sent = []
        self.emergency = []

    @property
    def busy(self):
        return False

    def connect_port(self, port_name, baud_rate=115_200):
        self.connected = True
        self.port_name = port_name
        self.connection_changed.emit(True, "")

    def disconnect_port(self):
        self.connected = False
        self.port_name = ""

    def send_command(self, command, *, tag=None, timeout_ms=1000):
        self.sent.append((bytes(command), tag, timeout_ms))
        return self.connected

    def send_emergency(self, command, *, reason="cancelled"):
        self.emergency.append(bytes(command))

    def shutdown(self, timeout_ms=1000):
        self.disconnect_port()

    def complete(self, index, line):
        _command, tag, _timeout = self.sent[index]
        self.command_completed.emit(tag, parse_response(line))


PORTS = [
    {
        "port_name": "COM8",
        "system_location": "COM8",
        "serial_number": "MOTOR-A",
        "vendor_id": 0x0483,
        "product_id": 0x5740,
    },
    {
        "port_name": "COM9",
        "system_location": "COM9",
        "serial_number": "UNKNOWN",
        "vendor_id": 0x9999,
        "product_id": 0x0001,
    },
]


def _status_line(x=0.0, y=0.0, z=0.0, *, moving=0, stop="DONE"):
    return (
        "OK STATUS "
        f"X_POS={x:.3f} X_VALID=1 X_MOVING={moving} X_LIMIT=0 "
        f"X_STOP={stop} X_HOME=IDLE "
        f"Y_POS={y:.3f} Y_VALID=1 Y_MOVING=0 Y_LIMIT=0 "
        "Y_STOP=DONE Y_HOME=IDLE "
        f"Z_POS={z:.3f} Z_VALID=1 Z_MOVING=0 Z_LIMIT=0 "
        "Z_STOP=DONE Z_HOME=IDLE FAULT=0"
    )


def test_auto_connect_requires_protocol_identity(tmp_path):
    application()
    transport = FakeTransport()
    controller = MotorController(
        transport=transport,
        state_store=MotorStateStore(tmp_path / "motor.json"),
        port_provider=lambda: PORTS,
    )
    confirmed = []
    controller.connection_changed.connect(
        lambda connected, detail: confirmed.append((connected, detail))
    )

    controller.connect_auto(excluded_ports={"COM7"})
    assert transport.sent[0][0] == b"ID?\r\n"
    transport.complete(0, "OK ID=NOT_MOTOR MOTOR_PROTOCOL=2")

    assert transport.port_name == "COM9"
    transport.complete(1, "OK ID=TMC2209 MOTOR_PROTOCOL=2")

    assert controller.connected is True
    assert controller.device_id == "UNKNOWN"
    assert confirmed[-1] == (True, "COM9")


def test_trusted_saved_coordinates_are_restored_to_same_device(tmp_path):
    app = application()
    store = MotorStateStore(tmp_path / "motor.json")
    store.confirm_position("MOTOR-A", Position(1.0, 2.0, 0.0))
    transport = FakeTransport()
    controller = MotorController(
        transport=transport,
        state_store=store,
        port_provider=lambda: PORTS[:1],
    )

    controller.connect_auto()
    transport.complete(0, "OK ID=TMC2209 MOTOR_PROTOCOL=2")
    app.processEvents()

    assert [item[0] for item in transport.sent[1:4]] == [
        b"POSSET=X:1.000\r\n",
        b"POSSET=Y:2.000\r\n",
        b"POSSET=Z:0.000\r\n",
    ]


def test_motion_marks_state_dirty_then_confirms_reported_position(tmp_path):
    app = application()
    store = MotorStateStore(tmp_path / "motor.json")
    store.confirm_position("MOTOR-A", Position(1.0, 2.0, 0.0))
    transport = FakeTransport()
    controller = MotorController(
        transport=transport,
        state_store=store,
        port_provider=lambda: PORTS[:1],
    )
    controller.connect_auto()
    transport.complete(0, "OK ID=TMC2209 MOTOR_PROTOCOL=2")
    for index in range(1, 4):
        transport.complete(index, "OK POSSET AXIS=X POS=0.000")
    transport.complete(4, _status_line(1, 2, 0))

    operation_id = controller.move_relative(
        Axis.X, 1.0, Direction.POSITIVE
    )
    assert operation_id
    assert store.load("MOTOR-A").dirty is True
    move_index = len(transport.sent) - 1
    assert transport.sent[move_index][0] == b"MOVE1=1.000:1\r\n"

    transport.complete(move_index, "OK MOVE MOTOR=1 MM=1.000 DIR=1")
    controller._poll_motion()
    status_index = len(transport.sent) - 1
    transport.complete(status_index, _status_line(2, 2, 0))
    app.processEvents()

    restored = store.load("MOTOR-A")
    assert restored.trusted is True
    assert restored.position == Position(2, 2, 0)


def test_emergency_stop_invalidates_an_active_motion(tmp_path):
    application()
    store = MotorStateStore(tmp_path / "motor.json")
    store.confirm_position("MOTOR-A", Position(1, 2, 0))
    transport = FakeTransport()
    controller = MotorController(
        transport=transport,
        state_store=store,
        port_provider=lambda: PORTS[:1],
    )
    controller._confirmed_candidate = controller.discover()[0]
    controller._connected = True
    controller._status = controller._parse_status(
        parse_response(_status_line(1, 2, 0))
    )
    controller._active_motion = object()

    controller.stop()

    assert transport.emergency == [b"STOP\r\n"]
    assert store.load("MOTOR-A").trusted is False
