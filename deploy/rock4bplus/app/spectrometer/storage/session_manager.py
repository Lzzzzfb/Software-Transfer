"""Routes frames to independent storage sessions and closes them off the GUI thread."""

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from ..domain.models import AcquisitionSession, SpectrumFrame
from ..qt import QtCore, Signal, Slot
from .coordinator import BatchStorageCoordinator
from .naming import build_session_file_stems
from .sealed_export import SealedSpoolExporter


@dataclass
class _ManagedSession:
    coordinator: BatchStorageCoordinator
    device_ids: tuple
    closing: bool = False
    future: object = None


class StorageSessionManager(QtCore.QObject):
    session_closed = Signal(str, object, object)
    warning_event = Signal(str, str)
    controlled_stop_requested = Signal(str, str)
    diagnostic_event = Signal(str)
    _worker_closed = Signal(str, object, object, object)

    def __init__(
        self,
        output_directory,
        *,
        coordinator_factory=BatchStorageCoordinator,
        max_close_workers: int = 4,
        processing_snapshot_provider=None,
        parent=None,
    ):
        super().__init__(parent)
        self.output_directory = Path(output_directory)
        self.output_directory.mkdir(parents=True, exist_ok=True)
        self.coordinator_factory = coordinator_factory
        self.processing_snapshot_provider = processing_snapshot_provider
        self._sessions: Dict[str, _ManagedSession] = {}
        self._device_routes: Dict[int, str] = {}
        self._executor = ThreadPoolExecutor(
            max_workers=max(1, int(max_close_workers)),
            thread_name_prefix="spectrum-finalize",
        )
        self._worker_closed.connect(self._complete_close)

    @property
    def queue_ratio(self) -> float:
        ratios = [
            managed.coordinator.queue_ratio
            for managed in self._sessions.values()
            if not managed.closing
        ]
        return max(ratios, default=0.0)

    @property
    def active_device_ids(self):
        return set(self._device_routes)

    def has_session(self, task_id: str) -> bool:
        return task_id in self._sessions

    def set_output_directory(self, output_directory) -> None:
        if self._sessions:
            raise RuntimeError("存储任务未结束，暂时不能更改数据目录")
        directory = Path(output_directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.output_directory = directory

    def start_session(self, request, devices) -> None:
        if request.task_id in self._sessions:
            raise ValueError("存储任务已经存在")
        devices = list(devices)
        if any(device is None for device in devices):
            raise ValueError("存储设备信息不完整")
        device_ids = tuple(device.device_id for device in devices)
        if set(device_ids) != set(request.device_ids):
            raise ValueError("存储设备集合与采集请求不一致")
        conflicts = sorted(set(device_ids) & set(self._device_routes))
        if conflicts:
            raise RuntimeError(f"设备已属于其他存储会话：{conflicts}")

        session = AcquisitionSession(
            session_id=request.task_id,
            started_at=request.started_at,
            device_ids=request.device_ids,
            mode=request.mode,
            sync_mode=request.sync_mode,
            storage_format=request.storage_format,
            batch_size=request.batch_size,
        )
        workbook_stem, device_stems = build_session_file_stems(request, devices)
        wavelengths = {
            device.device_id: tuple(
                device.get_wavelength_array(
                    device.info.valid_pixel or device.info.pixel_count or 4096
                )
            )
            for device in devices
        }
        labels = {
            device.device_id: (
                device.info.prod_serial
                or device.port_name
                or f"设备_{device.device_id}"
            )
            for device in devices
        }
        metadata = {
            device.device_id: {
                "Port": device.port_name,
                "Serial": device.info.prod_serial,
                "Integration Time (us)": device.integration_time_us,
                "Trigger Mode": device.trigger_mode,
            }
            for device in devices
        }
        processing_snapshots = (
            dict(self.processing_snapshot_provider(devices))
            if self.processing_snapshot_provider is not None
            else {}
        )
        task_id = request.task_id
        coordinator = self.coordinator_factory(
            self.output_directory,
            session,
            wavelengths_by_device=wavelengths,
            device_labels=labels,
            device_metadata=metadata,
            processing_snapshots=processing_snapshots,
            filename_stem=workbook_stem,
            device_filename_stems=device_stems,
            warning_callback=lambda message, tid=task_id:
                self.warning_event.emit(tid, message),
            controlled_stop_callback=lambda message, tid=task_id:
                self.controlled_stop_requested.emit(tid, message),
        )
        managed = _ManagedSession(coordinator, device_ids)
        self._sessions[task_id] = managed
        for device_id in device_ids:
            self._device_routes[device_id] = task_id
        try:
            coordinator.start()
        except Exception:
            for device_id in device_ids:
                self._device_routes.pop(device_id, None)
            self._sessions.pop(task_id, None)
            raise

    def start_sealed_export(
        self,
        request,
        devices,
        sealed_results,
        *,
        cleanup_sources=True,
    ) -> None:
        if request.task_id in self._sessions:
            raise ValueError("存储任务已经存在")
        devices = list(devices)
        device_ids = tuple(device.device_id for device in devices)
        if set(device_ids) != set(sealed_results):
            raise ValueError("封存文件与采集设备集合不一致")
        spool_paths = {
            device_id: values["spool_path"]
            for device_id, values in sealed_results.items()
        }
        expected_counts = {
            device_id: values["persisted_frames"]
            for device_id, values in sealed_results.items()
        }
        workbook_stem, device_stems = build_session_file_stems(request, devices)
        wavelengths = {
            device.device_id: tuple(
                device.get_wavelength_array(
                    device.info.valid_pixel
                    or device.info.pixel_count
                    or 4096
                )
            )
            for device in devices
        }
        labels = {
            device.device_id: (
                device.info.prod_serial
                or device.port_name
                or f"设备_{device.device_id}"
            )
            for device in devices
        }
        metadata = {
            device.device_id: {
                "Port": device.port_name,
                "Serial": device.info.prod_serial,
                "Integration Time (us)": device.integration_time_us,
                "Trigger Mode": device.trigger_mode,
            }
            for device in devices
        }
        snapshots = (
            dict(self.processing_snapshot_provider(devices))
            if self.processing_snapshot_provider is not None
            else {}
        )
        exporter = SealedSpoolExporter(
            self.output_directory,
            request,
            spool_paths,
            expected_counts=expected_counts,
            wavelengths_by_device=wavelengths,
            device_labels=labels,
            device_metadata=metadata,
            processing_snapshots=snapshots,
            filename_stem=workbook_stem,
            device_filename_stems=device_stems,
            cleanup_sources=cleanup_sources,
        )
        managed = _ManagedSession(exporter, device_ids, closing=True)
        self._sessions[request.task_id] = managed
        managed.future = self._executor.submit(
            self._close_worker, request.task_id, exporter
        )

    def submit(self, frame: SpectrumFrame) -> bool:
        task_id = self._device_routes.get(frame.device_id)
        managed = self._sessions.get(task_id or "")
        if managed is None or managed.closing:
            return False
        managed.coordinator.submit(frame)
        return True

    def close_session(self, task_id: str) -> bool:
        managed = self._sessions.get(task_id)
        if managed is None:
            return False
        if managed.closing:
            return False
        managed.closing = True
        for device_id in managed.device_ids:
            if self._device_routes.get(device_id) == task_id:
                self._device_routes.pop(device_id, None)
        managed.future = self._executor.submit(
            self._close_worker, task_id, managed.coordinator
        )
        return True

    def close_all(self) -> None:
        for task_id in list(self._sessions):
            self.close_session(task_id)

    def shutdown(self, timeout: float = 60.0) -> bool:
        self.close_all()
        futures = [
            managed.future
            for managed in self._sessions.values()
            if managed.future is not None
        ]
        if not futures:
            self._executor.shutdown(wait=False)
            return True
        _, pending = wait(futures, timeout=max(0.0, float(timeout)))
        self._executor.shutdown(wait=False)
        return not pending

    def _close_worker(self, task_id, coordinator):
        errors = []
        try:
            coordinator.close()
        except Exception as exc:
            errors.append(str(exc))
        errors.extend(str(error) for error in coordinator.errors)
        paths = list(coordinator.exported_files)
        paths.extend(getattr(coordinator, "recovery_files", ()))
        files = [str(path) for path in dict.fromkeys(paths)]
        warnings = [
            str(message)
            for message in getattr(coordinator, "cleanup_warnings", ())
        ]
        self._worker_closed.emit(task_id, files, errors, warnings)

    @Slot(str, object, object, object)
    def _complete_close(self, task_id: str, files, errors, warnings) -> None:
        self._sessions.pop(task_id, None)
        for message in warnings:
            self.warning_event.emit(task_id, str(message))
        self.session_closed.emit(task_id, list(files), list(errors))
