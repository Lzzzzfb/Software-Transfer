"""Read-only square-wave generator probe for Windows and ROCK 4B+."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json

from ..qt import QtCore, application_exec
from .discovery import find_square_wave_candidates
from .models import (
    DEFAULT_BAUD_RATE,
    DEVICE_NAME,
    PROTOCOL_VERSION,
    DeviceIdentity,
    DeviceStatus,
    PortCandidate,
)
from .protocol import id_command, status_command
from .transport import SquareWaveSerialTransport


class ReadOnlySquareWaveProbe(QtCore.QObject):
    """Identify candidates using only ``ID?`` and ``STATUS?``."""

    finished = (
        QtCore.pyqtSignal(object)
        if hasattr(QtCore, "pyqtSignal")
        else QtCore.Signal(object)
    )

    def __init__(
        self,
        candidates,
        *,
        baud_rate=DEFAULT_BAUD_RATE,
        transport=None,
        parent=None,
    ):
        super().__init__(parent)
        self.transport = transport or SquareWaveSerialTransport(self)
        self.candidates = tuple(candidates)
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
            self._failures.append({
                "port": self._candidate.port_name,
                "reason": str(reason),
            })
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
            "baud_rate": self.baud_rate,
        }
        self.transport.connect_port(self._candidate.port_name, self.baud_rate)

    def _send(self, request, expected, step):
        self.transport.send_request(
            request,
            expected=expected,
            tag=("square_wave_probe", step),
            timeout_ms=700,
            read_retries=1,
            action=False,
        )

    def _on_connection(self, connected, detail):
        if self._done:
            return
        if not connected:
            self._try_next(detail or "open_failed")
            return
        self._send(id_command(), "identity", "identity")

    def _on_completed(self, tag, response):
        if (
            self._done
            or not isinstance(tag, tuple)
            or tag[0] != "square_wave_probe"
        ):
            return
        step = tag[1]
        if step == "identity":
            if not isinstance(response, DeviceIdentity):
                self._try_next("invalid identity response")
                return
            if (
                response.name != DEVICE_NAME
                or response.protocol_version != PROTOCOL_VERSION
            ):
                self._try_next(
                    f"unsupported identity: {response.name} "
                    f"protocol={response.protocol_version}"
                )
                return
            self._payload["identity"] = asdict(response)
            self._send(status_command(), "status", "status")
            return
        if not isinstance(response, DeviceStatus):
            self._try_next("invalid status response")
            return
        self._payload["status"] = {
            "running": response.running,
            "frequency_hz": response.parameters.frequency_hz,
            "pulse_width_us": response.parameters.pulse_width_us,
        }
        self._finish({
            "ok": True,
            "device": self._payload,
            "failures": self._failures,
        })

    def _on_failed(self, tag, reason):
        if (
            self._done
            or not isinstance(tag, tuple)
            or tag[0] != "square_wave_probe"
        ):
            return
        self._try_next(f"{tag[1]}: {reason}")

    def abort(self, reason="probe_timeout"):
        if not self._done:
            failures = list(self._failures)
            if self._candidate is not None:
                failures.append({
                    "port": self._candidate.port_name,
                    "reason": str(reason),
                })
            else:
                failures.append({"reason": str(reason)})
            self._finish({"ok": False, "failures": failures})

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
        description="只读探测方波发生器（只发送 ID? 和 STATUS?，不会启停或写参数）"
    )
    parser.add_argument("--port", help="只探测指定串口，例如 COM7 或 /dev/ttyACM0")
    parser.add_argument(
        "--baud",
        type=int,
        default=DEFAULT_BAUD_RATE,
        choices=(DEFAULT_BAUD_RATE,),
    )
    parser.add_argument("--timeout", type=float, default=8.0, help="总超时秒数")
    parser.add_argument("--json", action="store_true", help="输出便于归档的 JSON")
    return parser


def main(argv=None):
    args = create_parser().parse_args(argv)
    app = QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    candidates = (
        (PortCandidate(args.port, args.port),)
        if args.port
        else find_square_wave_candidates()
    )
    if not candidates:
        result = {"ok": False, "failures": [{"reason": "no_serial_candidates"}]}
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 3
    probe = ReadOnlySquareWaveProbe(candidates, baud_rate=args.baud)
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
    result = (
        result_holder[0]
        if result_holder
        else {"ok": False, "failures": [{"reason": "no_result"}]}
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 4


if __name__ == "__main__":
    raise SystemExit(main())
