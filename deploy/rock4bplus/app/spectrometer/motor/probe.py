"""Read-only LK-MD2202 probe for Windows and ROCK 4B+."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import sys

from ..qt import QtCore, application_exec
from .discovery import MotorPortCandidate, find_motor_candidates
from .lk_md2202 import (
    BAUD_RATE_TO_CODE,
    DriverAxis,
    axis_configuration_request,
    communication_request,
    decode_axis_configuration,
    decode_axis_status,
    decode_communication,
    decode_identity,
    identity_request,
    status_request,
)
from .transport import MotorSerialTransport


class ReadOnlyProbe(QtCore.QObject):
    """Sequentially identify candidates using only Modbus function 0x03."""

    finished = QtCore.pyqtSignal(object) if hasattr(QtCore, "pyqtSignal") else QtCore.Signal(object)

    def __init__(self, candidates, *, address=1, baud_rate=9600, transport=None, parent=None):
        super().__init__(parent)
        self.transport = transport or MotorSerialTransport(self)
        self.candidates = tuple(candidates)
        self.address = int(address)
        self.baud_rate = int(baud_rate)
        self._index = -1
        self._candidate = None
        self._payload = {}
        self._failures = []
        self._done = False
        self.transport.connection_changed.connect(self._on_connection)
        self.transport.command_completed.connect(self._on_completed)
        self.transport.command_failed.connect(self._on_failed)

    def start(self):
        self._try_next()

    def _try_next(self, reason=""):
        if self._candidate is not None and reason:
            self._failures.append({"port": self._candidate.port_name, "reason": str(reason)})
        self.transport.disconnect_port()
        self._index += 1
        if self._index >= len(self.candidates):
            self._finish({"ok": False, "failures": self._failures})
            return
        self._candidate = self.candidates[self._index]
        self._payload = {
            "port": self._candidate.port_name,
            "system_location": self._candidate.system_location,
            "serial_number": self._candidate.serial_number,
            "address": self.address,
            "baud_rate": self.baud_rate,
        }
        self.transport.connect_port(self._candidate.port_name, self.baud_rate)

    def _send(self, request, step):
        self.transport.send_request(
            request,
            tag=("probe", step),
            timeout_ms=700,
            read_retries=1,
        )

    def _on_connection(self, connected, detail):
        if self._done:
            return
        if not connected:
            self._try_next(detail or "open_failed")
            return
        self._send(identity_request(self.address), "identity")

    def _on_completed(self, tag, response):
        if self._done or not isinstance(tag, tuple) or tag[0] != "probe":
            return
        step = tag[1]
        try:
            if step == "identity":
                identity = decode_identity(response.registers)
                if not identity.is_lk_md2202:
                    raise ValueError(f"unexpected device name: {identity.name}")
                self._payload["identity"] = asdict(identity)
                self._send(axis_configuration_request(self.address, DriverAxis.X), "x_config")
            elif step == "x_config":
                self._payload["x"] = asdict(decode_axis_configuration(response.registers))
                self._send(axis_configuration_request(self.address, DriverAxis.Y), "y_config")
            elif step == "y_config":
                self._payload["y"] = asdict(decode_axis_configuration(response.registers))
                self._send(communication_request(self.address), "communication")
            elif step == "communication":
                self._payload["communication"] = asdict(decode_communication(response.registers))
                self._send(status_request(self.address, DriverAxis.X), "x_status")
            elif step == "x_status":
                self._payload["x_status"] = asdict(decode_axis_status(response.registers))
                self._send(status_request(self.address, DriverAxis.Y), "y_status")
            else:
                self._payload["y_status"] = asdict(decode_axis_status(response.registers))
                self._finish({"ok": True, "device": self._payload, "failures": self._failures})
        except (TypeError, ValueError) as exc:
            self._try_next(str(exc))

    def _on_failed(self, tag, reason):
        if self._done or not isinstance(tag, tuple) or tag[0] != "probe":
            return
        self._try_next(f"{tag[1]}: {reason}")

    def abort(self, reason="probe_timeout"):
        if not self._done:
            self._finish({"ok": False, "failures": [*self._failures, {"reason": str(reason)}]})

    def _finish(self, result):
        if self._done:
            return
        self._done = True
        self.transport.disconnect_port()
        self.finished.emit(result)

    def shutdown(self):
        self.transport.shutdown()


def create_parser():
    parser = argparse.ArgumentParser(
        description="只读探测 LK-MD2202（不会写参数、运动、停止或回零）"
    )
    parser.add_argument("--port", help="只探测指定串口，例如 COM7 或 /dev/ttyUSB0")
    parser.add_argument("--address", type=int, default=1, help="Modbus 地址，默认 1")
    parser.add_argument("--baud", type=int, default=9600, choices=tuple(BAUD_RATE_TO_CODE), help="波特率")
    parser.add_argument("--timeout", type=float, default=8.0, help="总超时秒数")
    parser.add_argument("--json", action="store_true", help="输出便于归档的 JSON")
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    if not 1 <= args.address <= 247:
        print("Modbus 地址必须为 1～247", file=sys.stderr)
        return 2
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    candidates = (
        (MotorPortCandidate(args.port, args.port, preferred=True),)
        if args.port
        else find_motor_candidates()
    )
    if not candidates:
        result = {"ok": False, "failures": [{"reason": "no_serial_candidates"}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3
    probe = ReadOnlyProbe(candidates, address=args.address, baud_rate=args.baud)
    result_holder = []

    def completed(result):
        result_holder.append(result)
        app.quit()

    probe.finished.connect(completed)
    timer = QtCore.QTimer()
    timer.setSingleShot(True)
    timer.timeout.connect(lambda: probe.abort("probe_timeout"))
    timer.start(max(1, round(args.timeout * 1000)))
    QtCore.QTimer.singleShot(0, probe.start)
    application_exec(app)
    timer.stop()
    probe.shutdown()
    result = result_holder[0] if result_holder else {"ok": False, "failures": [{"reason": "no_result"}]}
    if args.json or True:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
