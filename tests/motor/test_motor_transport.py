import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.protocol import build_id_command, build_stop_command
from spectrometer.motor.transport import MotorSerialTransport
from spectrometer.qt import QtCore, QtWidgets, Signal, Slot


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


class FakeSerialWorker(QtCore.QObject):
    opened = Signal(bool, str)
    closed = Signal()
    line_received = Signal(str)
    connection_lost = Signal(str)

    def __init__(self):
        super().__init__()
        self.opened_ports = []
        self.writes = []
        self.close_count = 0

    @Slot(str, int)
    def open_port(self, port_name, baud_rate):
        self.opened_ports.append((port_name, baud_rate))
        self.opened.emit(True, "")

    @Slot(QtCore.QByteArray)
    def write(self, data):
        self.writes.append(bytes(data))

    @Slot()
    def close_port(self):
        self.close_count += 1
        self.closed.emit()


def test_transport_serializes_commands_and_parses_responses():
    app = application()
    worker = FakeSerialWorker()
    transport = MotorSerialTransport(worker=worker, own_thread=False)
    completed = []
    transport.command_completed.connect(
        lambda tag, response: completed.append((tag, response))
    )

    transport.connect_port("COM8")
    assert transport.connected
    transport.send_command(build_id_command(), tag="identify")
    transport.send_command(build_stop_command(), tag="stop")
    app.processEvents()

    assert worker.writes == [b"ID?\r\n"]
    worker.line_received.emit("OK ID=TMC2209 MOTOR_PROTOCOL=2")
    app.processEvents()
    assert worker.writes == [b"ID?\r\n", b"STOP\r\n"]
    assert completed[0][0] == "identify"
    assert completed[0][1].fields["ID"] == "TMC2209"

    worker.line_received.emit("OK STOP")
    app.processEvents()
    assert [item[0] for item in completed] == ["identify", "stop"]


def test_timed_out_movement_is_not_retried():
    app = application()
    worker = FakeSerialWorker()
    transport = MotorSerialTransport(worker=worker, own_thread=False)
    failed = []
    transport.command_failed.connect(
        lambda tag, reason: failed.append((tag, reason))
    )

    transport.connect_port("COM8")
    transport.send_command(b"MOVE1=1.000:1\r\n", tag="move", timeout_ms=10)
    app.processEvents()
    transport._on_timeout()
    app.processEvents()

    assert worker.writes == [b"MOVE1=1.000:1\r\n"]
    assert failed == [("move", "timeout")]


def test_connection_loss_clears_active_and_queued_transactions():
    app = application()
    worker = FakeSerialWorker()
    transport = MotorSerialTransport(worker=worker, own_thread=False)
    failed = []
    transport.command_failed.connect(
        lambda tag, reason: failed.append((tag, reason))
    )

    transport.connect_port("COM8")
    transport.send_command(build_id_command(), tag="one")
    transport.send_command(build_stop_command(), tag="two")
    worker.connection_lost.emit("device removed")
    app.processEvents()

    assert transport.connected is False
    assert failed == [
        ("one", "device removed"),
        ("two", "device removed"),
    ]

