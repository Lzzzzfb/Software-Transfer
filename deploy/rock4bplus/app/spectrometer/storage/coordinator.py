"""有界队列、临时缓存和 CSV/Excel 批次导出的协调器。"""

from pathlib import Path
from concurrent.futures import ProcessPoolExecutor
import multiprocessing
import queue
import re
import threading
from typing import Callable, Dict, Mapping, Optional

from ..domain.enums import StorageFormat
from ..domain.models import AcquisitionSession, SpectrumFrame
from ..processing.processor import ProcessingSnapshot
from .naming import unique_path
from .process_worker import export_processed_batch
from .spool import SpoolWriter


class StorageBackpressureError(RuntimeError):
    pass


class BatchStorageCoordinator:
    def __init__(
        self,
        output_directory,
        session: AcquisitionSession,
        *,
        wavelengths_by_device: Mapping[int, tuple] = None,
        device_labels: Mapping[int, str] = None,
        device_metadata: Mapping[int, Mapping] = None,
        processing_snapshots: Mapping[int, ProcessingSnapshot] = None,
        filename_stem: str = "",
        device_filename_stems: Mapping[int, str] = None,
        warning_callback: Optional[Callable[[str], None]] = None,
        controlled_stop_callback: Optional[Callable[[str], None]] = None,
    ):
        self.output_directory = Path(output_directory)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.session = session
        self.wavelengths_by_device = dict(wavelengths_by_device or {})
        self.device_labels = dict(device_labels or {})
        self.device_metadata = dict(device_metadata or {})
        self.processing_snapshots = dict(processing_snapshots or {})
        self.filename_stem = str(filename_stem or "")
        self.device_filename_stems = dict(device_filename_stems or {})
        self.warning_callback = warning_callback
        self.controlled_stop_callback = controlled_stop_callback
        self.capacity = session.batch_size * 4 * max(1, len(session.device_ids))
        self._queue = queue.Queue(maxsize=self.capacity)
        self._stop_token = object()
        self._thread = threading.Thread(
            target=self._run, name="spectrum-storage", daemon=True
        )
        self._buffers: Dict[int, list] = {device_id: [] for device_id in session.device_ids}
        self._batch_index = 0
        self._warning_sent = False
        self._stop_sent = False
        self.errors = []
        self.exported_files = []
        spool_path = self.output_directory / f"{session.session_id}.part"
        self.spool_path = spool_path
        self._spool = SpoolWriter(
            spool_path,
            {
                "session_id": session.session_id,
                "started_at": session.started_at,
                "device_ids": list(session.device_ids),
                "batch_size": session.batch_size,
                "storage_format": session.storage_format.value,
            },
        )
        self._started = False
        self._closed = False
        self._process_pool = None

    @property
    def queue_ratio(self) -> float:
        return self._queue.qsize() / self.capacity

    def start(self) -> None:
        if not self._started:
            self._started = True
            self._thread.start()

    def submit(self, frame: SpectrumFrame) -> None:
        if self._closed:
            raise RuntimeError("存储协调器已关闭")
        if frame.device_id not in self._buffers:
            raise ValueError(f"设备 {frame.device_id} 不属于当前会话")
        if not self._started:
            self.start()

        # 先落临时缓存。即使内存队列已满，帧仍能在恢复流程中找回。
        self._spool.append(frame)
        try:
            self._queue.put_nowait(frame)
        except queue.Full as exc:
            self._request_controlled_stop("存储队列已满，采集已请求受控停止")
            raise StorageBackpressureError("存储队列已满，帧已写入恢复缓存") from exc

        ratio = self.queue_ratio
        if ratio >= 0.9:
            self._request_controlled_stop("存储队列超过 90%，采集已请求受控停止")
        elif ratio >= 0.5 and not self._warning_sent:
            self._warning_sent = True
            if self.warning_callback:
                self.warning_callback("存储队列超过 50%")

    def close(self, timeout: float = 60.0) -> None:
        if self._closed:
            return
        if self._started:
            self._queue.join()
            self._queue.put(self._stop_token)
            self._thread.join(timeout)
            if self._thread.is_alive():
                self.errors.append("存储线程未在超时时间内结束")
        else:
            self._export_remaining()
        self._spool.close()
        if self._process_pool is not None:
            self._process_pool.shutdown(wait=True, cancel_futures=False)
        self._closed = True
        if not self.errors and self.spool_path.exists():
            self.spool_path.unlink()

    def _request_controlled_stop(self, message: str) -> None:
        if not self._stop_sent:
            self._stop_sent = True
            if self.controlled_stop_callback:
                self.controlled_stop_callback(message)

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            if item is self._stop_token:
                try:
                    self._export_remaining()
                except Exception as exc:
                    self.errors.append(str(exc))
                finally:
                    self._queue.task_done()
                return
            try:
                self._buffers[item.device_id].append(item)
                self._export_complete_batches()
            except Exception as exc:
                self.errors.append(str(exc))
            finally:
                self._queue.task_done()

    def _export_complete_batches(self) -> None:
        while all(
            len(self._buffers[device_id]) >= self.session.batch_size
            for device_id in self.session.device_ids
        ):
            batch = {
                device_id: self._pop_frames(device_id, self.session.batch_size)
                for device_id in self.session.device_ids
            }
            self._export_batch(batch)

    def _export_remaining(self) -> None:
        if not any(self._buffers.values()):
            return
        batch = {device_id: list(frames) for device_id, frames in self._buffers.items() if frames}
        for frames in self._buffers.values():
            frames.clear()
        self._export_batch(batch)

    def _pop_frames(self, device_id: int, count: int):
        frames = self._buffers[device_id][:count]
        del self._buffers[device_id][:count]
        return frames

    def _export_batch(self, batch: Dict[int, list]) -> None:
        self._batch_index += 1
        prefix = f"{self.session.session_id}_batch_{self._batch_index:04d}"
        batch_token = f"B{self._batch_index:04d}"
        csv_targets = {}
        if self.session.storage_format in (StorageFormat.CSV, StorageFormat.CSV_EXCEL):
            for device_id, frames in batch.items():
                label = self._filename_token(
                    self.device_labels.get(device_id, f"device_{device_id}")
                )
                if self.filename_stem:
                    stem = self.device_filename_stems.get(
                        device_id, f"{self.filename_stem}_{label}"
                    )
                    path = unique_path(
                        self.output_directory / f"{stem}_{batch_token}.csv"
                    )
                else:
                    path = self.output_directory / f"{prefix}_{label}.csv"
                csv_targets[device_id] = str(path)
        workbook_path = None
        if self.session.storage_format in (StorageFormat.EXCEL, StorageFormat.CSV_EXCEL):
            if self.filename_stem:
                path = unique_path(
                    self.output_directory
                    / f"{self.filename_stem}_{batch_token}.xlsx"
                )
            else:
                path = self.output_directory / f"{prefix}.xlsx"
            workbook_path = str(path)

        snapshots = dict(self.processing_snapshots)
        for device_id in batch:
            snapshots.setdefault(
                device_id,
                ProcessingSnapshot(
                    wavelengths=tuple(self.wavelengths_by_device.get(device_id, ()))
                ),
            )
        if self._process_pool is None:
            self._process_pool = ProcessPoolExecutor(
                max_workers=1,
                mp_context=multiprocessing.get_context("spawn"),
            )
        result = self._process_pool.submit(
            export_processed_batch,
            batch,
            processing_snapshots=snapshots,
            csv_targets=csv_targets,
            xlsx_target=workbook_path,
            wavelengths_by_device=self.wavelengths_by_device,
            device_labels=self.device_labels,
            device_metadata=self.device_metadata,
            session_metadata={
                "Session ID": self.session.session_id,
                "Batch": self._batch_index,
                "Sync Mode": self.session.sync_mode.value,
            },
        ).result()
        self.exported_files.extend(Path(path) for path in result["files"])
        for device_id, rejected in result["rejected"].items():
            for sequence, reason in rejected:
                if self.warning_callback:
                    self.warning_callback(
                        f"设备 {device_id} 丢弃错误帧 Seq {sequence:06X}：{reason}"
                    )

    @staticmethod
    def _filename_token(value: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1F]', "_", str(value)).strip(" .")
        return cleaned or "device"
