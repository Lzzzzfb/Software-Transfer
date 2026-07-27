import csv

from openpyxl import load_workbook

from spectrometer.domain.models import SpectrumFrame
from spectrometer.processing.processor import ProcessingSnapshot
from spectrometer.storage.process_worker import export_processed_batch


def test_worker_exports_only_background_subtracted_float_values(tmp_path):
    frame = SpectrumFrame.create(0, 7 << 8, [10, 30], timestamp_ns=123)
    csv_path = tmp_path / "result.csv"
    xlsx_path = tmp_path / "result.xlsx"
    snapshot = ProcessingSnapshot(
        mode="dark_subtract",
        wavelengths=(500.0, 501.0),
        background=(15.0, 25.0),
    )

    result = export_processed_batch(
        {0: [frame]},
        processing_snapshots={0: snapshot},
        csv_targets={0: str(csv_path)},
        xlsx_target=str(xlsx_path),
        wavelengths_by_device={0: snapshot.wavelengths},
        device_labels={0: "003"},
        device_metadata={0: {"Serial": "003"}},
        session_metadata={"Session ID": "test"},
    )

    assert result["files"] == [str(csv_path), str(xlsx_path)]
    with csv_path.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.reader(handle))
    header_row = next(
        index
        for index, row in enumerate(rows)
        if row[:2] == ["Pixel", "Wavelength"]
    )
    assert float(rows[header_row + 1][2]) == -5.0
    assert float(rows[header_row + 2][2]) == 5.0

    workbook = load_workbook(xlsx_path, read_only=True, data_only=True)
    sheet = workbook["003"]
    assert sheet.cell(2, 3).value == -5.0
    assert sheet.cell(3, 3).value == 5.0
    workbook.close()
