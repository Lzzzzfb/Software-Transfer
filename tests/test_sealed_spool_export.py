import pytest

from spectrometer.domain.enums import (
    AcquisitionOwner,
    StorageFormat,
)
from spectrometer.domain.models import AcquisitionRequest, SpectrumFrame
from spectrometer.processing.processor import ProcessingSnapshot
from spectrometer.storage import sealed_export
from spectrometer.storage.sealed_export import SealedSpoolExporter
from spectrometer.storage.spool import SpoolWriter
from spectrometer.storage.spool_lifecycle import SpoolCleanupResult


def test_sealed_spool_exports_every_persisted_frame(tmp_path):
    spool = tmp_path / "session.zgs"
    with SpoolWriter(spool, {"session_id": "session"}) as writer:
        for sequence in range(3):
            writer.append(
                SpectrumFrame.create(
                    0,
                    sequence << 8,
                    (10 + sequence, 20 + sequence),
                )
            )
    request = AcquisitionRequest.create(
        AcquisitionOwner.LOCAL,
        [0],
        auto_store=True,
        storage_format=StorageFormat.CSV,
        batch_size=2,
    )
    exporter = SealedSpoolExporter(
        tmp_path,
        request,
        {0: spool},
        expected_counts={0: 3},
        wavelengths_by_device={0: (400.0, 401.0)},
        device_labels={0: "SN003"},
        device_metadata={0: {}},
        processing_snapshots={
            0: ProcessingSnapshot(wavelengths=(400.0, 401.0))
        },
        filename_stem="session",
        device_filename_stems={0: "session_SN003"},
    )

    exporter.close()

    assert len(exporter.exported_files) == 2
    assert all(path.exists() for path in exporter.exported_files)
    assert not spool.exists()
    assert exporter.recovery_files == ()
    assert exporter.cleanup_warnings == ()


def test_sealed_spool_is_preserved_when_exported_count_mismatches(tmp_path):
    spool = tmp_path / "session.zgs"
    with SpoolWriter(spool, {"session_id": "session"}) as writer:
        writer.append(SpectrumFrame.create(0, 0, (10, 20)))
    request = AcquisitionRequest.create(
        AcquisitionOwner.LOCAL,
        [0],
        auto_store=True,
        storage_format=StorageFormat.CSV,
        batch_size=2,
    )
    exporter = SealedSpoolExporter(
        tmp_path,
        request,
        {0: spool},
        expected_counts={0: 2},
        wavelengths_by_device={0: (400.0, 401.0)},
        device_labels={0: "SN003"},
        device_metadata={0: {}},
        processing_snapshots={
            0: ProcessingSnapshot(wavelengths=(400.0, 401.0))
        },
        filename_stem="session",
        device_filename_stems={0: "session_SN003"},
    )

    with pytest.raises(RuntimeError, match="封存帧数与导出帧数不一致"):
        exporter.close()

    assert spool.exists()
    assert exporter.recovery_files == (spool,)


def test_sealed_spool_can_be_exported_without_cleanup(tmp_path):
    spool = tmp_path / "session.zgs"
    with SpoolWriter(spool, {"session_id": "session"}) as writer:
        writer.append(SpectrumFrame.create(0, 0, (10, 20)))
    request = AcquisitionRequest.create(
        AcquisitionOwner.LOCAL,
        [0],
        auto_store=True,
        storage_format=StorageFormat.CSV,
        batch_size=2,
    )
    exporter = SealedSpoolExporter(
        tmp_path,
        request,
        {0: spool},
        expected_counts={0: 1},
        wavelengths_by_device={0: (400.0, 401.0)},
        device_labels={0: "SN003"},
        device_metadata={0: {}},
        processing_snapshots={
            0: ProcessingSnapshot(wavelengths=(400.0, 401.0))
        },
        filename_stem="session",
        device_filename_stems={0: "session_SN003"},
        cleanup_sources=False,
    )

    exporter.close()

    assert spool.exists()
    assert exporter.recovery_files == (spool,)
    assert exporter.cleanup_warnings == ()


def test_sealed_spool_cleanup_failure_is_nonfatal_and_reported(
    tmp_path, monkeypatch
):
    spool = tmp_path / "session.zgs"
    with SpoolWriter(spool, {"session_id": "session"}) as writer:
        writer.append(SpectrumFrame.create(0, 0, (10, 20)))
    request = AcquisitionRequest.create(
        AcquisitionOwner.LOCAL,
        [0],
        auto_store=True,
        storage_format=StorageFormat.CSV,
        batch_size=2,
    )
    warning = f"临时采集缓存清理失败，已保留 {spool}：injected"
    monkeypatch.setattr(
        sealed_export,
        "cleanup_spool_files",
        lambda paths: SpoolCleanupResult((), (spool,), (warning,)),
    )
    exporter = SealedSpoolExporter(
        tmp_path,
        request,
        {0: spool},
        expected_counts={0: 1},
        wavelengths_by_device={0: (400.0, 401.0)},
        device_labels={0: "SN003"},
        device_metadata={0: {}},
        processing_snapshots={
            0: ProcessingSnapshot(wavelengths=(400.0, 401.0))
        },
        filename_stem="session",
        device_filename_stems={0: "session_SN003"},
    )

    exporter.close()

    assert spool.exists()
    assert exporter.recovery_files == (spool,)
    assert exporter.cleanup_warnings == (warning,)
