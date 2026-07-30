import os
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.device.spectrometer import SpectrometerDevice
from spectrometer.domain.enums import (
    AcquisitionOwner,
    StorageFormat,
)
from spectrometer.domain.models import AcquisitionRequest, SpectrumFrame
from spectrometer.processing.processor import ProcessingSnapshot
from spectrometer.qt import QtCore
from spectrometer.storage.session_manager import StorageSessionManager
from spectrometer.storage.spool import SpoolWriter


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtCore.QCoreApplication.instance() or QtCore.QCoreApplication([])
    )
    return _APPLICATION


def wait_until(predicate, timeout=5.0):
    app = application()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return predicate()


def device():
    item = SpectrometerDevice(0, "ttyACM0")
    item.connected = True
    item.initialized = True
    item.info.prod_serial = "003"
    item.info.valid_pixel = 2
    item.info.pixel_count = 2
    return item


def test_manual_export_uses_frozen_single_device_policy_and_all_frames(
    tmp_path,
):
    application()
    spool = tmp_path / "capture.zgs"
    with SpoolWriter(spool, {"session_id": "manual"}) as writer:
        for sequence in range(3):
            writer.append(
                SpectrumFrame.create(
                    0,
                    sequence << 8,
                    (10 + sequence, 20 + sequence),
                )
            )
    request = AcquisitionRequest(
        task_id="manual-task",
        started_at="2026-07-30T10:20:30+08:00",
        owner=AcquisitionOwner.LOCAL,
        device_ids=(0,),
        auto_store=False,
        storage_format=StorageFormat.CSV_EXCEL,
        batch_size=2,
    )
    snapshot = ProcessingSnapshot(wavelengths=(400.0, 401.0))
    manager = StorageSessionManager(
        tmp_path,
        processing_snapshot_provider=lambda devices: {0: snapshot},
    )
    context = manager.prepare_sealed_export(
        request,
        [device()],
        {
            0: {
                "spool_path": str(spool),
                "persisted_frames": 3,
            }
        },
    )
    closed = []
    manager.session_closed.connect(
        lambda task_id, files, errors:
            closed.append((task_id, list(files), list(errors)))
    )

    manager.start_prepared_export(context)

    assert wait_until(lambda: bool(closed))
    assert closed[0][0] == request.task_id
    assert closed[0][2] == []
    assert {os.path.basename(path) for path in closed[0][1]} == {
        "20260730_SN003_B0001.csv",
        "20260730_SN003_B0002.csv",
    }
    assert not spool.exists()
