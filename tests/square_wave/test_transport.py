import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.qt import QtCore, QtSerialPort, QtWidgets, Signal, Slot
from spectrometer.square_wave.models import DeviceIdentity, DeviceStatus
from spectrometer.square_wave.protocol import id_command, start_command, status_command
from spectrometer.square_wave.transport import (
    SquareWaveSerialTransport,
    SquareWaveSerialWorker,
)


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    return _APPLICATION


def wait_until(predicate, *, timeout=1.0):
    app = application()
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)
    app.processEvents()
    return predicate()


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


def test_qserialport_error_signal_accepts_square_wave_worker_callback():
    app = application()
    worker = SquareWaveSerialWorker()
    serial = QtSerialPort.QSerialPort()
    serial.errorOccurred.connect(worker._on_error)
    serial.errorOccurred.disconnect(worker._on_error)
    serial.deleteLater()
    app.processEvents()


def test_transport_serializes_requests_and_parses_fragmented_lines():
    app = application()
    worker = FakeSerialWorker()
    transport = SquareWaveSerialTransport(worker=worker, own_thread=False)
    completed = []
    transport.command_completed.connect(
        lambda tag, response: completed.append((tag, response))
    )
    transport.connect_port("COM8", 9600)
    transport.send_request(id_command(), expected="identity", tag="id")
    transport.send_request(status_command(), expected="status", tag="status")
    app.processEvents()
    assert worker.writes == [id_command()]

    worker.bytes_received.emit(b"ID ZGCAI_SQUARE_")
    worker.bytes_received.emit(b"WAVE protocol=1\r\n")
    app.processEvents()
    assert worker.writes == [id_command(), status_command()]
    assert completed == [("id", DeviceIdentity("ZGCAI_SQUARE_WAVE", 1))]

    worker.bytes_received.emit(b"STATUS running=0 freq=10 width=5\r\n")
    app.processEvents()
    assert isinstance(completed[-1][1], DeviceStatus)
    assert not completed[-1][1].running


def test_read_only_timeout_retries_but_action_timeout_never_retries():
    app = application()
    worker = FakeSerialWorker()
    transport = SquareWaveSerialTransport(worker=worker, own_thread=False)
    failures = []
    transport.command_failed.connect(
        lambda tag, reason: failures.append((tag, reason))
    )
    transport.connect_port("COM8")
    transport.send_request(
        status_command(), expected="status", tag="read", read_retries=1
    )
    app.processEvents()
    transport._engine._on_timeout()
    transport._engine._on_timeout()
    assert worker.writes == [status_command(), status_command()]
    assert failures == [("read", "timeout")]

    transport.send_request(
        start_command(), expected="ok", tag="start", read_retries=5, action=True
    )
    app.processEvents()
    transport._engine._on_timeout()
    assert worker.writes.count(start_command()) == 1
    assert failures[-1] == ("start", "result_unknown")


def test_wrong_response_type_and_device_error_fail_transaction():
    app = application()
    worker = FakeSerialWorker()
    transport = SquareWaveSerialTransport(worker=worker, own_thread=False)
    failures = []
    transport.command_failed.connect(
        lambda tag, reason: failures.append((tag, reason))
    )
    transport.connect_port("COM8")
    transport.send_request(id_command(), expected="identity", tag="wrong")
    worker.bytes_received.emit(b"OK\r\n")
    app.processEvents()
    assert failures[-1] == ("wrong", "unexpected_response:ok")

    transport.send_request(start_command(), expected="ok", tag="rejected", action=True)
    worker.bytes_received.emit(b"INVALID PARAM\r\n")
    app.processEvents()
    assert failures[-1] == ("rejected", "device_error:INVALID PARAM")


def test_connection_loss_fails_active_and_queued_transactions():
    app = application()
    worker = FakeSerialWorker()
    transport = SquareWaveSerialTransport(worker=worker, own_thread=False)
    failures = []
    transport.command_failed.connect(
        lambda tag, reason: failures.append((tag, reason))
    )
    transport.connect_port("COM8")
    transport.send_request(id_command(), expected="identity", tag="one")
    transport.send_request(status_command(), expected="status", tag="two")
    worker.connection_lost.emit("device removed")
    app.processEvents()
    assert failures == [("one", "device removed"), ("two", "device removed")]
    assert not transport.connected


def test_transaction_engine_and_serial_owner_run_in_worker_thread():
    application()
    worker = FakeSerialWorker()
    transport = SquareWaveSerialTransport(worker=worker, own_thread=True)
    try:
        transport.connect_port("COM8", 9600)
        assert wait_until(lambda: transport.connected)
        assert transport._thread is not None
        assert transport._engine.thread() is transport._thread
        assert transport._engine._timer.thread() is transport._thread
        assert transport._worker.thread() is transport._thread
    finally:
        transport.shutdown()
    assert transport._thread is None
