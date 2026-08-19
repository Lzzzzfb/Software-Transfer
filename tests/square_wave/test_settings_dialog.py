import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from spectrometer.qt import QtWidgets
from spectrometer.square_wave.models import DeviceIdentity, PortCandidate
from spectrometer.square_wave.settings_store import HostSquareWaveSettings
from spectrometer.ui.square_wave_settings_dialog import SquareWaveSettingsDialog


_APPLICATION = None


def application():
    global _APPLICATION
    _APPLICATION = (
        QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    )
    return _APPLICATION


def test_settings_dialog_exposes_connection_identity_and_fixed_protocol():
    application()
    dialog = SquareWaveSettingsDialog(
        HostSquareWaveSettings(),
        identity=DeviceIdentity("ZGCAI_SQUARE_WAVE", 1),
        serial_number="STM32-123",
    )

    assert dialog.automatic_port.isChecked()
    assert dialog.baud_rate.text() == "9600"
    assert dialog.serial_format.text() == "8 数据位，1 停止位，无校验（8N1）"
    assert dialog.identity_name.text() == "ZGCAI_SQUARE_WAVE"
    assert dialog.protocol_version.text() == "1"
    assert dialog.usb_serial.text() == "STM32-123"
    assert not dialog.findChildren(QtWidgets.QCheckBox)[-1].text().startswith(
        "扫描联动"
    )


def test_candidates_populate_editable_manual_port_and_apply_host_only_settings():
    application()
    dialog = SquareWaveSettingsDialog(HostSquareWaveSettings())
    dialog.set_candidates(
        (
            PortCandidate("COM7", "COM7", serial_number="A"),
            PortCandidate("ttyACM0", "/dev/ttyACM0", serial_number="B"),
        )
    )
    dialog.automatic_port.setChecked(False)
    dialog.port_name.setCurrentText("/dev/ttyACM0")
    emitted = []
    dialog.apply_requested.connect(emitted.append)
    dialog.apply_button.click()

    assert emitted
    assert emitted[-1].automatic_port is False
    assert emitted[-1].port_name == "/dev/ttyACM0"
    assert emitted[-1].baud_rate == 9600
    assert not hasattr(emitted[-1], "scan_link_enabled")
