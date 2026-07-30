"""Queued, non-blocking Qt serial transactions for the motor controller."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from ..qt import QtCore, QtSerialPort, Signal, Slot
from .protocol import MotorLineBuffer, MotorProtocolError, parse_response


QSerialPort = QtSerialPort.QSerialPort


def _serial_enum(group_name: str, value_name: str):
    if hasattr(QSerialPort, value_name):
        return getattr(QSerialPort, value_name)
    return getattr(getattr(QSerialPort, group_name), value_name)


def _read_write_mode():
    if hasattr(QSerialPort, "ReadWrite"):
        return QSerialPort.ReadWrite
    if hasattr(QtCore.QIODevice, "ReadWrite"):
        return QtCore.QIODevice.ReadWrite
    return QtCore.QIODevice.OpenModeFlag.ReadWrite


class MotorSerialWorker(QtCore.QObject):
    opened = Signal(bool, str)
    closed = Signal()
    line_received = Signal(str)
    connection_lost = Signal(str)

    def __init__(self):
        super().__init__()
        self._serial = None
        self._line_buffer = MotorLineBuffer()

    @Slot(str, int)
    def open_port(self, port_name: str, baud_rate: int):
        self.close_port()
        self._line_buffer.reset()
        serial = QSerialPort()
        serial.setPortName(port_name)
        serial.setBaudRate(baud_rate)
        serial.setDataBits(_serial_enum("DataBits", "Data8"))
        serial.setParity(_serial_enum("Parity", "NoParity"))
        serial.setStopBits(_serial_enum("StopBits", "OneStop"))
        serial.setFlowControl(_serial_enum("FlowControl", "NoFlowControl"))
        if not serial.open(_read_write_mode()):
            error = serial.errorString()
            serial.deleteLater()
            self.opened.emit(False, error)
            return
        self._serial = serial
        serial.readyRead.connect(self._on_ready_read)
        serial.errorOccurred.connect(self._on_error)
        self.opened.emit(True, "")

    @Slot(QtCore.QByteArray)
    def write(self, data):
        if self._serial is None or not self._serial.isOpen():
            self.connection_lost.emit("motor serial port is not open")
            return
        if self._serial.write(data) < 0:
            self.connection_lost.emit(self._serial.errorString())

    @Slot()
    def close_port(self):
        serial = self._serial
        self._serial = None
        if serial is not None:
            if serial.isOpen():
                serial.close()
            serial.deleteLater()
        self._line_buffer.reset()
        self.closed.emit()

    @Slot()
    def _on_ready_read(self):
        if self._serial is None:
            return
        data = bytes(self._serial.readAll())
        try:
            lines = self._line_buffer.feed(data)
        except MotorProtocolError as exc:
            self.connection_lost.emit(str(exc))
            return
        for line in lines:
            self.line_received.emit(line)

    @Slot(object)
    def _on_error(self, error):
        if error == _serial_enum("SerialPortError", "ResourceError"):
            message = (
                self._serial.errorString()
                if self._serial is not None
                else "motor serial device removed"
            )
            self.connection_lost.emit(message)


@dataclass(frozen=True)
class _Transaction:
    command: bytes
    tag: object
    timeout_ms: int


class MotorSerialTransport(QtCore.QObject):
    """One-command-at-a-time transaction layer with no automatic retries."""

    open_requested = Signal(str, int)
    close_requested = Signal()
    write_requested = Signal(QtCore.QByteArray)

    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)
    unsolicited_response = Signal(object)
    diagnostic_event = Signal(str)

    def __init__(self, parent=None, *, worker=None, own_thread=True):
        super().__init__(parent)
        self._worker = worker or MotorSerialWorker()
        self._own_thread = bool(own_thread)
        self._thread = None
        self._connected = False
        self._port_name = ""
        self._queue: deque[_Transaction] = deque()
        self._active: _Transaction | None = None
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

        if self._own_thread:
            self._thread = QtCore.QThread(self)
            self._worker.moveToThread(self._thread)
            self._thread.start()

        self.open_requested.connect(self._worker.open_port)
        self.close_requested.connect(self._worker.close_port)
        self.write_requested.connect(self._worker.write)
        self._worker.opened.connect(self._on_opened)
        self._worker.closed.connect(self._on_closed)
        self._worker.line_received.connect(self._on_line)
        self._worker.connection_lost.connect(self._on_connection_lost)

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def port_name(self) -> str:
        return self._port_name

    @property
    def busy(self) -> bool:
        return self._active is not None or bool(self._queue)

    def connect_port(self, port_name: str, baud_rate: int = 115_200):
        self.disconnect_port()
        self._port_name = str(port_name)
        self.open_requested.emit(self._port_name, int(baud_rate))

    def disconnect_port(self):
        if self._connected or self._port_name:
            self.close_requested.emit()
        self._connected = False
        self._port_name = ""
        self._fail_all("disconnected")

    def send_command(
        self,
        command: bytes,
        *,
        tag=None,
        timeout_ms: int = 1000,
    ) -> bool:
        command = bytes(command)
        if not self._connected:
            self.command_failed.emit(tag, "not connected")
            return False
        if not command.endswith((b"\r", b"\n")):
            raise ValueError("motor command must be line terminated")
        transaction = _Transaction(
            command=command,
            tag=tag,
            timeout_ms=max(1, int(timeout_ms)),
        )
        self._queue.append(transaction)
        self._dispatch_next()
        return True

    def send_emergency(self, command: bytes, *, reason: str = "cancelled"):
        """Write a safety command immediately and abandon queued transactions."""
        command = bytes(command)
        if not command.endswith((b"\r", b"\n")):
            raise ValueError("motor command must be line terminated")
        self._fail_all(reason)
        if self._connected:
            self.write_requested.emit(QtCore.QByteArray(command))

    @Slot(bool, str)
    def _on_opened(self, success: bool, error: str):
        self._connected = bool(success)
        if not success:
            self._port_name = ""
        self.connection_changed.emit(self._connected, str(error))

    @Slot()
    def _on_closed(self):
        was_connected = self._connected
        self._connected = False
        self._fail_all("disconnected")
        if was_connected:
            self.connection_changed.emit(False, "")

    def _dispatch_next(self):
        if (
            not self._connected
            or self._active is not None
            or not self._queue
        ):
            return
        self._active = self._queue.popleft()
        self.write_requested.emit(QtCore.QByteArray(self._active.command))
        self._timer.start(self._active.timeout_ms)

    @Slot(str)
    def _on_line(self, line: str):
        try:
            response = parse_response(line)
        except MotorProtocolError as exc:
            self.diagnostic_event.emit(str(exc))
            return
        if self._active is None:
            self.unsolicited_response.emit(response)
            return
        transaction = self._active
        self._active = None
        self._timer.stop()
        self.command_completed.emit(transaction.tag, response)
        self._dispatch_next()

    @Slot()
    def _on_timeout(self):
        if self._active is None:
            return
        transaction = self._active
        self._active = None
        self.command_failed.emit(transaction.tag, "timeout")
        self._dispatch_next()

    @Slot(str)
    def _on_connection_lost(self, reason: str):
        self._connected = False
        self._fail_all(str(reason) or "connection lost")
        self.connection_changed.emit(False, str(reason))

    def _fail_all(self, reason: str):
        self._timer.stop()
        transactions = []
        if self._active is not None:
            transactions.append(self._active)
            self._active = None
        transactions.extend(self._queue)
        self._queue.clear()
        for transaction in transactions:
            self.command_failed.emit(transaction.tag, reason)

    def shutdown(self, timeout_ms: int = 1000):
        self.disconnect_port()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(max(1, int(timeout_ms)))
            self._thread = None
