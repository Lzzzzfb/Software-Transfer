"""Queued non-blocking line transport for the square-wave generator."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

from ..qt import QtCore, QtSerialPort, Signal, Slot
from .models import DeviceIdentity, DeviceStatus, ErrorResponse, OkResponse
from .protocol import ProtocolError, ResponseLineBuffer, parse_response_line


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


class SquareWaveSerialWorker(QtCore.QObject):
    opened = Signal(bool, str)
    closed = Signal()
    bytes_received = Signal(object)
    connection_lost = Signal(str)

    def __init__(self):
        super().__init__()
        self._serial = None

    @Slot(str, int)
    def open_port(self, port_name: str, baud_rate: int):
        self.close_port()
        serial = QSerialPort()
        serial.setPortName(str(port_name))
        serial.setBaudRate(int(baud_rate))
        serial.setDataBits(_serial_enum("DataBits", "Data8"))
        serial.setParity(_serial_enum("Parity", "NoParity"))
        serial.setStopBits(_serial_enum("StopBits", "OneStop"))
        serial.setFlowControl(_serial_enum("FlowControl", "NoFlowControl"))
        if not serial.open(_read_write_mode()):
            detail = serial.errorString()
            serial.deleteLater()
            self.opened.emit(False, detail)
            return
        self._serial = serial
        serial.readyRead.connect(self._on_ready_read)
        serial.errorOccurred.connect(self._on_error)
        self.opened.emit(True, "")

    @Slot(object)
    def write(self, data):
        if self._serial is None or not self._serial.isOpen():
            self.connection_lost.emit("square-wave serial port is not open")
            return
        if self._serial.write(bytes(data)) < 0:
            self.connection_lost.emit(self._serial.errorString())

    @Slot()
    def close_port(self):
        serial = self._serial
        self._serial = None
        if serial is not None:
            if serial.isOpen():
                serial.close()
            serial.deleteLater()
        self.closed.emit()

    @Slot()
    def _on_ready_read(self):
        if self._serial is not None:
            self.bytes_received.emit(bytes(self._serial.readAll()))

    def _on_error(self, error):
        if error == _serial_enum("SerialPortError", "ResourceError"):
            self.connection_lost.emit(
                self._serial.errorString()
                if self._serial is not None
                else "square-wave serial device removed"
            )


@dataclass(frozen=True)
class LineTransaction:
    request: bytes
    expected: str
    tag: object
    timeout_ms: int = 500
    retries_remaining: int = 0
    action: bool = False

    def __post_init__(self):
        if self.expected not in {"identity", "status", "ok"}:
            raise ValueError("unsupported expected response")
        if not self.request.endswith((b"\r", b"\n")):
            raise ValueError("line request must end with CR or LF")


class _SquareWaveTransactionWorker(QtCore.QObject):
    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)
    diagnostic_event = Signal(str)
    busy_changed = Signal(bool)
    write_issued = Signal(object)

    def __init__(self, serial_worker):
        super().__init__()
        self._serial_worker = serial_worker
        if self._serial_worker.parent() is None:
            self._serial_worker.setParent(self)
        self._connected = False
        self._queue: deque[LineTransaction] = deque()
        self._active: LineTransaction | None = None
        self._buffer = ResponseLineBuffer()
        self._last_busy = False
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)
        self._serial_worker.opened.connect(self._on_opened)
        self._serial_worker.closed.connect(self._on_closed)
        self._serial_worker.bytes_received.connect(self._on_bytes)
        self._serial_worker.connection_lost.connect(self._on_connection_lost)

    @property
    def busy(self):
        return self._active is not None or bool(self._queue)

    def _emit_busy_if_changed(self):
        busy = self.busy
        if busy != self._last_busy:
            self._last_busy = busy
            self.busy_changed.emit(busy)

    @Slot(str, int)
    def open_port(self, port_name: str, baud_rate: int):
        self.close_port()
        self._serial_worker.open_port(str(port_name), int(baud_rate))

    @Slot()
    def close_port(self):
        self._timer.stop()
        self._connected = False
        self._fail_all("disconnected")
        self._buffer.clear()
        self._serial_worker.close_port()

    @Slot(object)
    def enqueue_transaction(self, transaction):
        if not self._connected:
            self.command_failed.emit(transaction.tag, "not_connected")
            self._emit_busy_if_changed()
            return
        self._queue.append(transaction)
        self._start_next()
        self._emit_busy_if_changed()

    def _start_next(self):
        if self._active is not None or not self._queue or not self._connected:
            return
        self._active = self._queue.popleft()
        self._buffer.clear()
        self._write(self._active.request)
        self._timer.start(self._active.timeout_ms)

    def _write(self, request: bytes):
        request = bytes(request)
        self.write_issued.emit(request)
        self._serial_worker.write(request)

    @Slot(bool, str)
    def _on_opened(self, success: bool, detail: str):
        self._connected = bool(success)
        self.connection_changed.emit(bool(success), str(detail))

    @Slot()
    def _on_closed(self):
        return

    @staticmethod
    def _response_kind(response) -> str:
        if isinstance(response, DeviceIdentity):
            return "identity"
        if isinstance(response, DeviceStatus):
            return "status"
        if isinstance(response, OkResponse):
            return "ok"
        if isinstance(response, ErrorResponse):
            return "error"
        return "unknown"

    @Slot(object)
    def _on_bytes(self, data):
        try:
            lines = self._buffer.feed(bytes(data))
        except ProtocolError as exc:
            if self._active is not None:
                self._finish_failed(f"protocol_error:{exc}")
            else:
                self.diagnostic_event.emit(f"unsolicited_protocol_error:{exc}")
            return
        for line in lines:
            active = self._active
            if active is None:
                self.diagnostic_event.emit(f"unsolicited_response:{line}")
                continue
            try:
                response = parse_response_line(line)
            except ProtocolError as exc:
                self._finish_failed(f"protocol_error:{exc}")
                continue
            kind = self._response_kind(response)
            if kind == "error":
                self._finish_failed(f"device_error:{response.message}")
                continue
            if kind != active.expected:
                self._finish_failed(f"unexpected_response:{kind}")
                continue
            self._timer.stop()
            tag = active.tag
            self._active = None
            self.command_completed.emit(tag, response)
            self._start_next()
            self._emit_busy_if_changed()

    @Slot()
    def _on_timeout(self):
        active = self._active
        if active is None:
            return
        if not active.action and active.retries_remaining > 0:
            self._active = replace(
                active,
                retries_remaining=active.retries_remaining - 1,
            )
            self._buffer.clear()
            self._write(active.request)
            self._timer.start(active.timeout_ms)
            return
        self._finish_failed("result_unknown" if active.action else "timeout")

    def _finish_failed(self, reason: str):
        active = self._active
        self._active = None
        self._timer.stop()
        self._buffer.clear()
        if active is not None:
            self.command_failed.emit(active.tag, str(reason))
        self._start_next()
        self._emit_busy_if_changed()

    @Slot(str)
    def _on_connection_lost(self, reason: str):
        self._connected = False
        self._timer.stop()
        self._fail_all(str(reason))
        self._buffer.clear()
        self.connection_changed.emit(False, str(reason))

    def _fail_all(self, reason: str):
        active, self._active = self._active, None
        if active is not None:
            self.command_failed.emit(active.tag, str(reason))
        while self._queue:
            self.command_failed.emit(self._queue.popleft().tag, str(reason))
        self._emit_busy_if_changed()


class SquareWaveSerialTransport(QtCore.QObject):
    """GUI-thread facade for the complete serial-thread transaction engine."""

    open_requested = Signal(str, int)
    close_requested = Signal()
    transaction_requested = Signal(object)

    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)
    diagnostic_event = Signal(str)
    write_requested = Signal(object)

    def __init__(self, parent=None, *, worker=None, own_thread=True):
        super().__init__(parent)
        self._worker = worker or SquareWaveSerialWorker()
        self._engine = _SquareWaveTransactionWorker(self._worker)
        self._own_thread = bool(own_thread)
        self._thread = None
        self._connected = False
        self._busy = False
        self._port_name = ""
        self._baud_rate = 0

        self.open_requested.connect(self._engine.open_port)
        self.close_requested.connect(self._engine.close_port)
        self.transaction_requested.connect(self._engine.enqueue_transaction)
        self._engine.connection_changed.connect(self._on_connection_changed)
        self._engine.command_completed.connect(self.command_completed)
        self._engine.command_failed.connect(self.command_failed)
        self._engine.diagnostic_event.connect(self.diagnostic_event)
        self._engine.busy_changed.connect(self._on_busy_changed)
        self._engine.write_issued.connect(self.write_requested)

    @property
    def connected(self):
        return self._connected

    @property
    def port_name(self):
        return self._port_name

    @property
    def busy(self):
        return self._busy

    def _ensure_thread(self):
        if not self._own_thread or self._thread is not None:
            return
        self._thread = QtCore.QThread(self)
        self._engine.moveToThread(self._thread)
        self._thread.start()

    def connect_port(self, port_name: str, baud_rate: int = 9600):
        self.disconnect_port()
        self._ensure_thread()
        self._port_name = str(port_name)
        self._baud_rate = int(baud_rate)
        self.open_requested.emit(self._port_name, self._baud_rate)

    def disconnect_port(self):
        was_open = self._connected or bool(self._port_name)
        self._connected = False
        self._busy = False
        self._port_name = ""
        self._baud_rate = 0
        if was_open:
            self.close_requested.emit()

    def send_request(
        self,
        request: bytes,
        *,
        expected: str,
        tag=None,
        timeout_ms: int = 500,
        read_retries: int = 0,
        action: bool = False,
    ) -> bool:
        if not self._connected:
            self.command_failed.emit(tag, "not_connected")
            return False
        transaction = LineTransaction(
            bytes(request),
            str(expected),
            tag,
            max(1, int(timeout_ms)),
            0 if action else max(0, int(read_retries)),
            bool(action),
        )
        self._busy = True
        self.transaction_requested.emit(transaction)
        return True

    @Slot(bool, str)
    def _on_connection_changed(self, connected: bool, detail: str):
        self._connected = bool(connected)
        if not connected:
            self._busy = False
            if detail and detail != "disconnected":
                self._port_name = ""
        self.connection_changed.emit(bool(connected), str(detail))

    @Slot(bool)
    def _on_busy_changed(self, busy: bool):
        self._busy = bool(busy)

    def shutdown(self):
        self._connected = False
        self._busy = False
        self._port_name = ""
        self._baud_rate = 0
        if self._thread is not None and self._thread.isRunning():
            blocking = getattr(QtCore.Qt, "BlockingQueuedConnection", None)
            if blocking is None:
                blocking = QtCore.Qt.ConnectionType.BlockingQueuedConnection
            QtCore.QMetaObject.invokeMethod(
                self._engine,
                "close_port",
                blocking,
            )
            self._thread.quit()
            self._thread.wait(1000)
            self._thread = None
        else:
            self._engine.close_port()
