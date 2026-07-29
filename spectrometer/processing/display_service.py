"""有界的实时显示处理服务；存储链路不经过这里。"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
import threading

from ..domain.models import SpectrumFrame
from ..qt import QtCore, Signal
from .processor import ProcessingSnapshot, SpectrumProcessor


@dataclass(frozen=True)
class DisplayProcessingJob:
    device_id: int
    generation: int
    frame: SpectrumFrame
    snapshot: ProcessingSnapshot


@dataclass(frozen=True)
class DisplayProcessingResult:
    device_id: int
    generation: int
    frame: SpectrumFrame
    snapshot: ProcessingSnapshot
    spectrum: object


@dataclass(frozen=True)
class DisplayProcessingFailure:
    device_id: int
    generation: int
    frame: SpectrumFrame
    message: str


class DisplayProcessingService(QtCore.QObject):
    """每台设备最多保留一个运行任务和一个最新待处理任务。"""

    result_ready = Signal(object)
    processing_failed = Signal(object)

    def __init__(
        self,
        parent=None,
        *,
        max_workers=2,
        process_callback=None,
        result_callback=None,
        error_callback=None,
    ):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_workers)),
            thread_name_prefix="display-processing",
        )
        self._process_callback = (
            process_callback
            or (
                lambda frame, snapshot: SpectrumProcessor().process_frame(
                    frame, snapshot
                )
            )
        )
        self._result_callback = result_callback
        self._error_callback = error_callback
        self._lock = threading.Lock()
        self._running = {}
        self._pending = {}
        self._device_epochs = {}
        self._futures = set()
        self._closed = False

    @property
    def running_count(self):
        with self._lock:
            return len(self._running)

    @property
    def pending_count(self):
        with self._lock:
            return len(self._pending)

    def submit(self, device_id, generation, frame, snapshot):
        job = DisplayProcessingJob(
            int(device_id), int(generation), frame, snapshot
        )
        with self._lock:
            if self._closed:
                return False
            epoch = self._device_epochs.get(job.device_id, 0)
            scheduled = (job, epoch)
            if job.device_id in self._running:
                self._pending[job.device_id] = scheduled
                return True
            self._running[job.device_id] = scheduled
        self._start(scheduled)
        return True

    def discard_device(self, device_id):
        device_id = int(device_id)
        with self._lock:
            self._device_epochs[device_id] = (
                self._device_epochs.get(device_id, 0) + 1
            )
            self._pending.pop(device_id, None)

    def _start(self, scheduled):
        job, _epoch = scheduled
        try:
            future = self._executor.submit(
                self._process_callback, job.frame, job.snapshot
            )
        except RuntimeError:
            with self._lock:
                self._running.pop(job.device_id, None)
            return
        with self._lock:
            self._futures.add(future)
        future.add_done_callback(
            lambda item, value=scheduled: self._done(value, item)
        )

    def _done(self, scheduled, future):
        job, epoch = scheduled
        result = None
        failure = None
        try:
            spectrum = future.result()
            result = DisplayProcessingResult(
                job.device_id,
                job.generation,
                job.frame,
                job.snapshot,
                spectrum,
            )
        except Exception as exc:
            failure = DisplayProcessingFailure(
                job.device_id,
                job.generation,
                job.frame,
                str(exc),
            )

        next_scheduled = None
        with self._lock:
            self._futures.discard(future)
            current_epoch = self._device_epochs.get(job.device_id, 0)
            valid = not self._closed and epoch == current_epoch
            current = self._running.get(job.device_id)
            if current == scheduled:
                next_scheduled = self._pending.pop(job.device_id, None)
                if next_scheduled is None:
                    self._running.pop(job.device_id, None)
                else:
                    self._running[job.device_id] = next_scheduled
            if (
                next_scheduled is not None
                and next_scheduled[1] != current_epoch
            ):
                self._running.pop(job.device_id, None)
                next_scheduled = None

        if valid:
            if failure is None:
                if self._result_callback is not None:
                    self._result_callback(result)
                self.result_ready.emit(result)
            else:
                if self._error_callback is not None:
                    self._error_callback(failure)
                self.processing_failed.emit(failure)
        if next_scheduled is not None:
            self._start(next_scheduled)

    def shutdown(self, timeout=2.0):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._pending.clear()
            for device_id in tuple(self._device_epochs):
                self._device_epochs[device_id] += 1
            futures = tuple(self._futures)
        if futures:
            wait(futures, timeout=max(0.0, float(timeout)))
        self._executor.shutdown(wait=False, cancel_futures=True)
