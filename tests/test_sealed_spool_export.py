from spectrometer.domain.enums import (
    AcquisitionOwner,
    StorageFormat,
)
from spectrometer.domain.models import AcquisitionRequest, SpectrumFrame
from spectrometer.processing.processor import ProcessingSnapshot
from spectrometer.storage.sealed_export import SealedSpoolExporter
from spectrometer.storage.spool import SpoolWriter


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
