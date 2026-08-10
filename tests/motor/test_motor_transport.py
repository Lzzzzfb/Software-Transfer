import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.motor.modbus_rtu import (
    append_crc,
    build_read_holding,
    build_write_single,
)
from spectrometer.motor.transport import MotorSerialTransport
from spectrometer.qt import QtCore, QtWidgets, Signal, Slot


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def read_response(address, *registers):
    payload = bytearray((address, 0x03, len(registers) * 2))
    for value in registers:
        payload.extend(int(value).to_bytes(2, "big"))
    return append_crc(payload)


class FakeSerialWorker(QtCore.QObject):
    opened = Signal(bool, str)
    closed = Signal()
    bytes_received = Signal(object)
    connection_lost = Signal(str)

    def __init__(self):
        super().__init__()
        self.opened_ports = []
        self.writes = []

    @Slot(str, int)
    def open_port(self, port_name, baud_rate):
        self.opened_ports.append((port_name, baud_rate))
        self.opened.emit(True, "")

    @Slot(object)
    def write(self, data):
        self.writes.append(bytes(data))

    @Slot()
    def close_port(self):
        self.closed.emit()


def test_transport_serializes_binary_requests_and_parses_split_frames():
    app = application()
    worker = FakeSerialWorker()
    transport = MotorSerialTransport(worker=worker, own_thread=False)
    completed = []
    transport.command_completed.connect(lambda tag, response: completed.append((tag, response)))

    first = build_read_holding(1, 0, 1)
    second = build_read_holding(1, 0x20, 1)
    transport.connect_port("COM8", 9600)
    transport.send_request(first, tag="identity")
    transport.send_request(second, tag="status")
    app.processEvents()
    assert worker.writes == [first]

    response = read_response(1, 0x1234)
    worker.bytes_received.emit(response[:3])
    worker.bytes_received.emit(response[3:])
    app.processEvents()
    assert worker.writes == [first, second]
    assert completed[0][0] == "identity"
    assert completed[0][1].registers == (0x1234,)


def test_read_timeout_retries_but_action_timeout_does_not():
    app = application()
    worker = FakeSerialWorker()
    transport = MotorSerialTransport(worker=worker, own_thread=False)
    failures = []
    transport.command_failed.connect(lambda tag, reason: failures.append((tag, reason)))
    transport.connect_port("COM8")

    read = build_read_holding(1, 0, 1)
    transport.send_request(read, tag="read", read_retries=1)
    app.processEvents()
    transport._on_timeout()
    transport._on_timeout()
    assert worker.writes == [read, read]
    assert failures == [("read", "timeout")]

    action = build_write_single(1, 0x24, 1)
    transport.send_request(action, tag="move", read_retries=5, action=True)
    app.processEvents()
    transport._on_timeout()
    assert worker.writes[-1] == action
    assert worker.writes.count(action) == 1
    assert failures[-1] == ("move", "result_unknown")


def test_connection_loss_fails_active_and_queued_transactions():
    app = application()
    worker = FakeSerialWorker()
    transport = MotorSerialTransport(worker=worker, own_thread=False)
    failures = []
    transport.command_failed.connect(lambda tag, reason: failures.append((tag, reason)))
    transport.connect_port("COM8")
    transport.send_request(build_read_holding(1, 0, 1), tag="one")
    transport.send_request(build_read_holding(1, 1, 1), tag="two")
    worker.connection_lost.emit("device removed")
    app.processEvents()
    assert failures == [("one", "device removed"), ("two", "device removed")]


def test_read_response_register_count_must_match_request():
    app = application()
    worker = FakeSerialWorker()
    transport = MotorSerialTransport(worker=worker, own_thread=False)
    failures = []
    transport.command_failed.connect(lambda tag, reason: failures.append((tag, reason)))
    transport.connect_port("COM8")
    transport.send_request(build_read_holding(1, 0, 2), tag="read-two")
    worker.bytes_received.emit(read_response(1, 0x1234))
    app.processEvents()
    assert failures
    assert failures[-1][0] == "read-two"
    assert "register count" in failures[-1][1]
