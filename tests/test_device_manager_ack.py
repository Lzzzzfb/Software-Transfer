import struct

from spectrometer.communication.protocol import CmdCode
from spectrometer.device.device_manager import DeviceManager
from spectrometer.device.spectrometer import SpectrometerDevice


def test_calibration_keeps_reverse_wire_order_and_applies_only_after_ack():
    manager = DeviceManager()
    manager.devices[0] = SpectrometerDevice(0, "COM_TEST")
    captured = {}

    def send(device_id, cmd, params=b""):
        captured.update(device_id=device_id, cmd=cmd, params=params)
        return True

    manager.send_to_device = send
    manager.set_calib_coeff(0, 1.0, 2.0, 3.0, 4.0)
    assert struct.unpack("<4f", captured["params"]) == (4.0, 3.0, 2.0, 1.0)
    assert manager.devices[0].wavelength_calib.c1 == 0.0

    manager._on_response(0, CmdCode.SET_CALIB_COEFF, b"\x60")
    calibration = manager.devices[0].wavelength_calib
    assert (calibration.c1, calibration.c2, calibration.c3, calibration.c4) == (1.0, 2.0, 3.0, 4.0)
