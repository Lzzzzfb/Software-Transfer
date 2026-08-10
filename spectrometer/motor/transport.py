"""Queued non-blocking Modbus-RTU transport for LK-MD2202."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace

from ..qt import QtCore, QtSerialPort, Signal, Slot
from .modbus_rtu import (
    ModbusDeviceError,
    ModbusError,
    ModbusFrameError,
    ModbusResponseBuffer,
    parse_response,
)


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
            error = serial.errorString()
            serial.deleteLater()
            self.opened.emit(False, error)
            return
        self._serial = serial
        serial.readyRead.connect(self._on_ready_read)
        serial.errorOccurred.connect(self._on_error)
        self.opened.emit(True, "")

    @Slot(object)
    def write(self, data):
        if self._serial is None or not self._serial.isOpen():
            self.connection_lost.emit("motor serial port is not open")
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
                else "motor serial device removed"
            )


@dataclass(frozen=True)
class ModbusTransaction:
    request: bytes
    tag: object
    timeout_ms: int = 500
    retries_remaining: int = 0
    action: bool = False

    @property
    def address(self):
        return self.request[0]

    @property
    def function(self):
        return self.request[1]


class MotorSerialTransport(QtCore.QObject):
    open_requested = Signal(str, int)
    close_requested = Signal()
    write_requested = Signal(object)

    connection_changed = Signal(bool, str)
    command_completed = Signal(object, object)
    command_failed = Signal(object, str)
    diagnostic_event = Signal(str)

    def __init__(self, parent=None, *, worker=None, own_thread=True):
        super().__init__(parent)
        self._worker = worker or MotorSerialWorker()
        self._own_thread = bool(own_thread)
        self._thread = None
        self._connected = False
        self._port_name = ""
        self._baud_rate = 0
        self._queue: deque[ModbusTransaction] = deque()
        self._active: ModbusTransaction | None = None
        self._buffer = ModbusResponseBuffer()
        self._timer = QtCore.QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._on_timeout)

        self.open_requested.connect(self._worker.open_port)
        self.close_requested.connect(self._worker.close_port)
        self.write_requested.connect(self._worker.write)
        self._worker.opened.connect(self._on_opened)
        self._worker.closed.connect(self._on_closed)
        self._worker.bytes_received.connect(self._on_bytes)
        self._worker.connection_lost.connect(self._on_connection_lost)

    @property
    def connected(self):
        return self._connected

    @property
    def port_name(self):
        return self._port_name

    @property
    def busy(self):
        return self._active is not None or bool(self._queue)

    def _ensure_thread(self):
        if not self._own_thread or self._thread is not None:
            return
        self._thread = QtCore.QThread(self)
        self._worker.moveToThread(self._thread)
        self._thread.start()

    def connect_port(self, port_name: str, baud_rate: int = 9600):
        self.disconnect_port()
        self._ensure_thread()
        self._port_name = str(port_name)
        self._baud_rate = int(baud_rate)
        self.open_requested.emit(self._port_name, self._baud_rate)

    def disconnect_port(self):
        self._timer.stop()
        self._fail_all("disconnected")
        self._buffer.clear()
        was_open = self._connected or bool(self._port_name)
        self._connected = False
        self._port_name = ""
        self._baud_rate = 0
        if was_open:
            self.close_requested.emit()

    def send_request(
        self,
        request: bytes,
        *,
        tag=None,
        timeout_ms: int = 500,
        read_retries: int = 0,
        action: bool = False,
    ) -> bool:
        request = bytes(request)
        if len(request) < 4:
            raise ValueError("invalid Modbus request")
        if not self._connected:
            self.command_failed.emit(tag, "not_connected")
            return False
        retries = 0 if action else max(0, int(read_retries))
        self._queue.append(
            ModbusTransaction(request, tag, max(1, int(timeout_ms)), retries, bool(action))
        )
        self._start_next()
        return True

    def send_emergency(self, requests, *, reason="emergency_stop"):
        requests = tuple(requests) if not isinstance(requests, (bytes, bytearray)) else (bytes(requests),)
        self._timer.stop()
        self._fail_all(reason)
        self._buffer.clear()
        for request in requests:
            self._queue.append(ModbusTransaction(bytes(request), ("stop",), 500, 0, True))
        self._start_next()

    def _start_next(self):
        if self._active is not None or not self._queue or not self._connected:
            return
        self._active = self._queue.popleft()
        self._buffer.clear()
        self.write_requested.emit(self._active.request)
        self._timer.start(self._active.timeout_ms)

    @Slot(bool, str)
    def _on_opened(self, success: bool, detail: str):
        self._connected = bool(success)
        if not success:
            self._port_name = ""
        self.connection_changed.emit(bool(success), str(detail))

    @Slot()
    def _on_closed(self):
        return

    @Slot(object)
    def _on_bytes(self, data):
        active = self._active
        if active is None:
            return
        frames = self._buffer.feed(
            bytes(data),
            expected_address=active.address,
            expected_function=active.function,
        )
        if not frames:
            return
        self._timer.stop()
        try:
            response = parse_response(
                frames[0],
                expected_address=active.address,
                expected_function=active.function,
            )
            self._validate_response(active.request, response)
        except ModbusDeviceError as exc:
            self._finish_failed(f"modbus_exception:0x{exc.exception_code:02X}")
            return
        except ModbusError as exc:
            self._finish_failed(f"protocol_error:{exc}")
            return
        tag = active.tag
        self._active = None
        self.command_completed.emit(tag, response)
        self._start_next()

    @staticmethod
    def _validate_response(request, response):
        function = request[1]
        if function == 0x03:
            requested_count = int.from_bytes(request[4:6], "big")
            if len(response.registers) != requested_count:
                raise ModbusFrameError(
                    "read response register count does not match request"
                )
        elif function == 0x06:
            if response.raw[:6] != request[:6]:
                raise ModbusFrameError("write-single response echo mismatch")
        elif function == 0x10:
            if response.raw[2:6] != request[2:6]:
                raise ModbusFrameError("write-multiple response echo mismatch")

    @Slot()
    def _on_timeout(self):
        active = self._active
        if active is None:
            return
        if not active.action and active.retries_remaining > 0:
            self._active = replace(active, retries_remaining=active.retries_remaining - 1)
            self._buffer.clear()
            self.write_requested.emit(active.request)
            self._timer.start(active.timeout_ms)
            return
        self._finish_failed("result_unknown" if active.action else "timeout")

    def _finish_failed(self, reason: str):
        active = self._active
        self._active = None
        self._timer.stop()
        if active is not None:
            self.command_failed.emit(active.tag, str(reason))
        self._start_next()

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

    def shutdown(self):
        self.disconnect_port()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1000)
            self._thread = None
