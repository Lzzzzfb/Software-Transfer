from spectrometer.domain.models import SpectrumFrame
from spectrometer.storage.csv_exporter import export_device_csv
from spectrometer.storage.xlsx_exporter import export_workbook
from spectrometer.ui.history_viewer import HistoryViewer


class PlotRecorder:
    def __init__(self):
        self.curves = []

    def load_spectrum(self, x, y, label):
        self.curves.append((x, y, label))
        return True


def _frames():
    return [
        SpectrumFrame.create(0, 0x100, [10, 20, 30]),
        SpectrumFrame.create(0, 0x200, [11, 21, 31]),
    ]


def test_history_loads_new_chinese_csv_export(tmp_path):
    path = export_device_csv(
        tmp_path / "中文.csv",
        0,
        _frames(),
        wavelengths=(400.0, 401.0, 402.0),
    )
    plot = PlotRecorder()

    HistoryViewer._load_csv(path, plot)

    assert len(plot.curves) == 2
    assert plot.curves[0][0].tolist() == [400.0, 401.0, 402.0]
    assert plot.curves[0][1].tolist() == [10.0, 20.0, 30.0]
    assert plot.curves[0][2].startswith("第 0001 帧_序号_")


def test_history_loads_new_chinese_excel_export(tmp_path):
    path = export_workbook(
        tmp_path / "中文.xlsx",
        {0: _frames()},
        wavelengths_by_device={0: (400.0, 401.0, 402.0)},
        device_labels={0: "SN003"},
    )
    plot = PlotRecorder()

    HistoryViewer._load_xlsx(path, plot)

    assert len(plot.curves) == 2
    assert plot.curves[0][0].tolist() == [400.0, 401.0, 402.0]
    assert plot.curves[0][1].tolist() == [10.0, 20.0, 30.0]
    assert plot.curves[0][2].startswith("SN003/第 0001 帧_序号_")


def test_history_keeps_loading_legacy_english_csv(tmp_path):
    path = tmp_path / "legacy.csv"
    path.write_text(
        "Pixel,Wavelength,Frame_0001_Seq_000100\n"
        "0,400,10\n"
        "1,401,20\n",
        encoding="utf-8",
    )
    plot = PlotRecorder()

    HistoryViewer._load_csv(path, plot)

    assert len(plot.curves) == 1
    assert plot.curves[0][0].tolist() == [400.0, 401.0]
    assert plot.curves[0][1].tolist() == [10.0, 20.0]
