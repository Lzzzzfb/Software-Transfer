"""从独立采集进程封存的全量 spool 生成批次 CSV/Excel。"""

from concurrent.futures import ProcessPoolExecutor
from itertools import islice
import multiprocessing
from pathlib import Path
import re

from ..processing.processor import ProcessingSnapshot
from .export_policy import resolve_export_plan
from .naming import unique_path
from .process_worker import export_processed_batch
from .spool import iter_spool_frames
from .spool_lifecycle import cleanup_spool_files


class SealedSpoolExporter:
    def __init__(
        self,
        output_directory,
        request,
        spool_paths,
        *,
        expected_counts,
        wavelengths_by_device,
        device_labels,
        device_metadata,
        processing_snapshots,
        filename_stem,
        device_filename_stems,
        cleanup_sources=True,
    ):
        self.output_directory = Path(output_directory)
        self.request = request
        self.spool_paths = {
            int(device_id): Path(path)
            for device_id, path in spool_paths.items()
        }
        self.expected_counts = {
            int(device_id): int(count)
            for device_id, count in expected_counts.items()
        }
        self.wavelengths_by_device = dict(wavelengths_by_device)
        self.device_labels = dict(device_labels)
        self.device_metadata = dict(device_metadata)
        self.processing_snapshots = dict(processing_snapshots)
        self.filename_stem = str(filename_stem)
        self.device_filename_stems = dict(device_filename_stems)
        self.cleanup_sources = bool(cleanup_sources)
        self.errors = []
        self.exported_files = []
        self.cleanup_warnings = ()

    @property
    def queue_ratio(self) -> float:
        return 0.0

    @property
    def recovery_files(self):
        return tuple(
            path for path in self.spool_paths.values() if path.exists()
        )

    def close(self) -> None:
        iterators = {
            device_id: iter(iter_spool_frames(path))
            for device_id, path in self.spool_paths.items()
        }
        batch_index = 0
        exported_counts = {device_id: 0 for device_id in iterators}
        context = multiprocessing.get_context("spawn")
        with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
            while True:
                batch = {
                    device_id: list(
                        islice(iterator, self.request.batch_size)
                    )
                    for device_id, iterator in iterators.items()
                }
                batch = {
                    device_id: frames
                    for device_id, frames in batch.items()
                    if frames
                }
                if not batch:
                    break
                for device_id, frames in batch.items():
                    exported_counts[device_id] += len(frames)
                batch_index += 1
                csv_targets, xlsx_target = self._targets(batch, batch_index)
                snapshots = dict(self.processing_snapshots)
                for device_id in batch:
                    snapshots.setdefault(
                        device_id,
                        ProcessingSnapshot(
                            wavelengths=tuple(
                                self.wavelengths_by_device.get(device_id, ())
                            )
                        ),
                    )
                result = pool.submit(
                    export_processed_batch,
                    batch,
                    processing_snapshots=snapshots,
                    csv_targets=csv_targets,
                    xlsx_target=xlsx_target,
                    wavelengths_by_device=self.wavelengths_by_device,
                    device_labels=self.device_labels,
                    device_metadata=self.device_metadata,
                    session_metadata={
                        "Session ID": self.request.task_id,
                        "Batch": batch_index,
                        "Sync Mode": self.request.sync_mode.value,
                        "Source": "sealed acquisition spool",
                    },
                ).result()
                self.exported_files.extend(Path(path) for path in result["files"])
        if exported_counts != self.expected_counts:
            raise RuntimeError(
                f"封存帧数与导出帧数不一致："
                f"expected={self.expected_counts}, actual={exported_counts}"
            )
        if self.cleanup_sources:
            cleanup = cleanup_spool_files(self.spool_paths.values())
            self.cleanup_warnings = cleanup.warnings

    def _targets(self, batch, batch_index):
        token = f"B{batch_index:04d}"
        export_plan = resolve_export_plan(
            self.request.storage_format, self.request.device_ids
        )
        csv_targets = {}
        if export_plan.write_csv:
            for device_id in batch:
                if device_id not in export_plan.csv_device_ids:
                    continue
                label = self._filename_token(
                    self.device_labels.get(device_id, f"device_{device_id}")
                )
                stem = self.device_filename_stems.get(
                    device_id, f"{self.filename_stem}_{label}"
                )
                csv_targets[device_id] = str(
                    unique_path(self.output_directory / f"{stem}_{token}.csv")
                )
        xlsx_target = None
        if export_plan.write_excel:
            xlsx_target = str(
                unique_path(
                    self.output_directory
                    / f"{self.filename_stem}_{token}.xlsx"
                )
            )
        return csv_targets, xlsx_target

    @staticmethod
    def _filename_token(value: str) -> str:
        cleaned = re.sub(
            r'[<>:"/\\|?*\x00-\x1F]', "_", str(value)
        ).strip(" .")
        return cleaned or "device"
