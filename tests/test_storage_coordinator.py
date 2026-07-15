from openpyxl import load_workbook

from spectrometer.domain.enums import StorageFormat
from spectrometer.domain.models import AcquisitionSession, SpectrumFrame
from spectrometer.storage.coordinator import BatchStorageCoordinator


def test_coordinator_exports_csv_and_one_workbook_with_device_sheets(tmp_path):
    session = AcquisitionSession.create(
        [0, 1], batch_size=2, storage_format=StorageFormat.CSV_EXCEL
    )
    coordinator = BatchStorageCoordinator(
        tmp_path,
        session,
        wavelengths_by_device={0: (400.0, 401.0), 1: (800.0, 801.0)},
        device_labels={0: "设备A", 1: "设备B"},
    )
    for sequence in range(2):
        for device_id in (0, 1):
            coordinator.submit(
                SpectrumFrame.create(device_id, sequence << 8, [sequence, sequence + 1])
            )
    coordinator.close()

    csv_files = list(tmp_path.glob("*.csv"))
    xlsx_files = list(tmp_path.glob("*.xlsx"))
    assert len(csv_files) == 2
    assert len(xlsx_files) == 1
    assert not list(tmp_path.glob("*.part"))
    workbook = load_workbook(xlsx_files[0], read_only=True)
    assert workbook.sheetnames == ["采集概要", "设备A", "设备B"]
