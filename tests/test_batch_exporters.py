import csv

from openpyxl import load_workbook

from spectrometer.domain.models import SpectrumFrame
from spectrometer.storage.csv_exporter import export_device_csv
from spectrometer.storage.xlsx_exporter import export_workbook


def frames(device_id, offset=0):
    return [
        SpectrumFrame.create(device_id, (index + 1) << 8, [offset + index, offset + index + 10])
        for index in range(2)
    ]


def test_csv_is_one_frame_per_column_and_one_pixel_per_row(tmp_path):
    path = export_device_csv(
        tmp_path / "设备一.csv",
        1,
        frames(1),
        wavelengths=(400.0, 401.5),
        metadata={"Serial": "SN001"},
    )
    with path.open(encoding="utf-8-sig", newline="") as file:
        rows = list(csv.reader(file))
    header_index = next(index for index, row in enumerate(rows) if row and row[0] == "Pixel")
    assert rows[header_index][:2] == ["Pixel", "Wavelength"]
    assert len(rows[header_index]) == 4
    assert rows[header_index + 1][:2] == ["0", "400.000000"]
    assert rows[header_index + 2][0] == "1"


def test_excel_uses_summary_and_separate_device_sheets(tmp_path):
    path = export_workbook(
        tmp_path / "批量.xlsx",
        {1: frames(1), 2: frames(2, 100)},
        wavelengths_by_device={1: (400.0, 401.0), 2: (900.0, 901.0)},
        device_labels={1: "SN001_可见光", 2: "SN002_近红外"},
    )
    workbook = load_workbook(path, read_only=True, data_only=True)
    assert workbook.sheetnames == ["采集概要", "SN001_可见光", "SN002_近红外"]
    visible = workbook["SN001_可见光"]
    infrared = workbook["SN002_近红外"]
    assert visible.cell(1, 1).value == "Pixel"
    assert visible.cell(1, 3).value.startswith("Frame_0001")
    assert visible.cell(2, 2).value == 400
    assert infrared.cell(2, 2).value == 900
    assert infrared.cell(2, 3).value == 100
