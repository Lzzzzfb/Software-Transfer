"""Qt 主进程中的采集子进程代理。"""

import multiprocessing
import queue

import numpy as np

from ..domain.models import SpectrumFrame
from ..qt import QtCore, Signal, Slot
from .process_worker import acquisition_process_main


class AcquisitionProcessProxy(QtCore.QObject):
    frame_received = Signal(object)
    packet_error = Signal(int, str)
    connection_lost = Signal(int)
    response_ready = Signal(int, int, bytes)
    open_finished = Signal(int, bool, str)
    diagnostic_ready = Signal(int, object)
    session_started = Signal(int, str)
    session_sealed = Signal(int, object)
    fatal_error = Signal(int, str)
    controlled_stop_requested = Signal(int, str)
    frame_summary_ready = Signal(int, object)
    recent_frames_ready = Signal(int, object)

    def __init__(self, device_index: int, port_name: str, baud_rate: int):
        super().__init__()
        self.device_index = int(device_index)
        self._port_name = port_name
        self._baud_rate = int(baud_rate)
        self._context = multiprocessing.get_context("spawn")
        self._control = None
        self._process = None
        self._events = None
        self._display = None
        self._closing = False
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(5)
        self._timer.timeout.connect(self._poll)

    @Slot()
    def do_connect(self):
        if self._process is not None:
            return
        parent, child = self._context.Pipe()
        self._control = parent
        self._events = self._context.Queue(maxsize=256)
        self._display = self._context.Queue(maxsize=1)
        self._process = self._context.Process(
            target=acquisition_process_main,
            args=(
                child,
                self._events,
                self._display,
                self.device_index,
                self._port_name,
                self._baud_rate,
            ),
            name=f"spectrometer-{self.device_index}",
            daemon=True,
        )
        self._process.start()
        child.close()
        self._timer.start()

    @Slot()
    def do_close(self):
        self._closing = True
        self._send(("close",))
        self._timer.stop()
        if self._process is not None:
            self._process.join(timeout=2.0)
            if self._process.is_alive():
                self._process.terminate()
                self._process.join(timeout=1.0)
        if self._control is not None:
            self._control.close()
        for item in (self._events, self._display):
            if item is not None:
                item.close()
        self._process = None

    @Slot(int, QtCore.QByteArray)
    def do_send_command(self, cmd: int, params):
        self._send(("command", int(cmd), bytes(params)))

    @Slot(int, int, int)
    def set_pixel_info(self, n_pixel: int, n_start_pixel: int, n_valid_pixel: int):
        self._send(
            ("pixel_info", int(n_pixel), int(n_start_pixel), int(n_valid_pixel))
        )

    def begin_session(self, path, metadata) -> None:
        self._send(("begin_session", str(path), dict(metadata)))

    def end_session(self) -> None:
        self._send(("end_session",))

    def request_recent_frames(self) -> None:
        self._send(("recent_frames",))

    @property
    def process_id(self):
        return self._process.pid if self._process is not None else None

    def _send(self, message) -> None:
        if self._control is None:
            raise RuntimeError("采集进程尚未连接")
        self._control.send(message)

    @Slot()
    def _poll(self):
        if self._events is not None:
            while True:
                try:
                    event = self._events.get_nowait()
                except queue.Empty:
                    break
                self._dispatch(event)
        if self._display is not None:
            latest = None
            while True:
                try:
                    latest = self._display.get_nowait()
                except queue.Empty:
                    break
            if latest is not None:
                self._dispatch_display(latest)
        if (
            not self._closing
            and self._process is not None
            and not self._process.is_alive()
        ):
            self._timer.stop()
            self.connection_lost.emit(self.device_index)

    def _dispatch(self, event) -> None:
        kind = event[0]
        if kind == "open":
            self.open_finished.emit(event[1], event[2], event[3])
        elif kind == "response":
            self.response_ready.emit(event[1], event[2], event[3])
        elif kind == "stream_error":
            values = event[2]
            self.packet_error.emit(
                event[1],
                (
                    f"串口帧重新同步：拒绝 {values['rejected_candidates']} 个候选，"
                    f"恢复 {values['recovered']} 次"
                ),
            )
        elif kind == "diagnostics":
            self.diagnostic_ready.emit(event[1], event[2])
        elif kind == "session_started":
            self.session_started.emit(event[1], event[2])
        elif kind == "session_sealed":
            self.session_sealed.emit(event[1], event[2])
        elif kind == "fatal":
            self.fatal_error.emit(event[1], event[2])
            self.packet_error.emit(event[1], event[2])
        elif kind == "controlled_stop":
            self.controlled_stop_requested.emit(event[1], event[2])
        elif kind == "frame_summary":
            self.frame_summary_ready.emit(event[1], event[2])
        elif kind == "recent_frames":
            self.recent_frames_ready.emit(event[1], event[2])

    def _dispatch_display(self, event) -> None:
        (
            _kind,
            device_id,
            packet_number,
            sequence_bits,
            monotonic_ns,
            timestamp_ns,
            pixel_bytes,
            pixel_count,
        ) = event
        pixels = np.frombuffer(pixel_bytes, dtype="<u2", count=pixel_count)
        frame = SpectrumFrame.create(
            device_id,
            packet_number,
            pixels,
            monotonic_ns=monotonic_ns,
            timestamp_ns=timestamp_ns,
            sequence_bits=sequence_bits,
        )
        self.frame_received.emit(frame)
