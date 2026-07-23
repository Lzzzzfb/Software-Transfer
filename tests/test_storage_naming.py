from datetime import datetime, timedelta, timezone

from spectrometer.device.spectrometer import SpectrometerDevice
from spectrometer.domain.enums import (
    AcquisitionMode,
    AcquisitionOwner,
    SyncMode,
)
from spectrometer.domain.models import AcquisitionRequest
from spectrometer.storage.naming import build_session_file_stems, unique_path


def device(device_id, port, serial):
    item = SpectrometerDevice(device_id, port)
    item.info.prod_serial = serial
    return item


def request(owner, devices, mode, sync):
    return AcquisitionRequest(
        task_id="task",
        started_at=datetime(
            2026, 7, 22, 23, 30, 45,
            tzinfo=timezone(timedelta(hours=8)),
        ).isoformat(),
        owner=owner,
        device_ids=tuple(devices),
        mode=mode,
        sync_mode=sync,
    )


def test_global_and_device_file_stems_are_human_readable():
    acquisition = request(
        AcquisitionOwner.GLOBAL,
        [0, 1],
        AcquisitionMode.CONTINUOUS,
        SyncMode.SOFTWARE,
    )

    workbook, csv_stems = build_session_file_stems(
        acquisition,
        [device(0, "COM14", "003"), device(1, "COM17", "SN002")],
    )

    assert workbook == "20260722_233045_多设备_软件同步"
    assert csv_stems == {
        0: "20260722_233045_SN003_COM14_软件同步",
        1: "20260722_233045_SN002_COM17_软件同步",
    }


def test_local_file_stem_includes_device_and_acquisition_mode():
    acquisition = request(
        AcquisitionOwner.LOCAL,
        [0],
        AcquisitionMode.SINGLE,
        SyncMode.INDEPENDENT,
    )

    workbook, csv_stems = build_session_file_stems(
        acquisition, [device(0, "COM14", "003")]
    )

    expected = "20260722_233045_SN003_COM14_单机单次"
    assert workbook == expected
    assert csv_stems == {0: expected}


def test_unique_path_never_overwrites_existing_file(tmp_path):
    first = tmp_path / "光谱_B0001.xlsx"
    first.write_bytes(b"existing")
    second = tmp_path / "光谱_B0001_01.xlsx"
    second.write_bytes(b"existing")

    assert unique_path(first).name == "光谱_B0001_02.xlsx"
