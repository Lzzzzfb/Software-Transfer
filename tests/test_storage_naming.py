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


def test_global_and_device_file_stems_use_date_and_serial_only():
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

    assert workbook == "20260722_多设备"
    assert csv_stems == {
        0: "20260722_SN003",
        1: "20260722_SN002",
    }


def test_local_file_stem_excludes_time_of_day_port_and_acquisition_mode():
    acquisition = request(
        AcquisitionOwner.LOCAL,
        [0],
        AcquisitionMode.SINGLE,
        SyncMode.INDEPENDENT,
    )

    workbook, csv_stems = build_session_file_stems(
        acquisition, [device(0, "COM14", "003")]
    )

    expected = "20260722_SN003"
    assert workbook == expected
    assert csv_stems == {0: expected}


def test_missing_serial_uses_device_id_without_port():
    acquisition = request(
        AcquisitionOwner.LOCAL,
        [4],
        AcquisitionMode.CONTINUOUS,
        SyncMode.INDEPENDENT,
    )

    workbook, csv_stems = build_session_file_stems(
        acquisition, [device(4, "COM99", "")]
    )

    assert workbook == "20260722_设备4"
    assert csv_stems == {4: "20260722_设备4"}
    assert "COM99" not in workbook


def test_unique_path_never_overwrites_existing_file(tmp_path):
    first = tmp_path / "光谱_B0001.xlsx"
    first.write_bytes(b"existing")
    second = tmp_path / "光谱_B0001_01.xlsx"
    second.write_bytes(b"existing")

    assert unique_path(first).name == "光谱_B0001_02.xlsx"
