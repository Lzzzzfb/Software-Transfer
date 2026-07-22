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


def test_real_firmware_init_ack_with_cmd_01_is_recognized_as_compatibility_response():
    manager = DeviceManager()
    manager.devices[0] = SpectrometerDevice(0, "COM_TEST")
    diagnostics = []
    errors = []
    manager.diagnostic_event.connect(diagnostics.append)
    manager.error_occurred.connect(lambda device_id, message: errors.append((device_id, message)))

    manager._on_response(0, CmdCode.GET_VERSION, b"\x61")

    assert diagnostics and "Cmd=0x01" in diagnostics[-1]
    assert errors == []
    assert not manager.devices[0].initialized


def test_set_command_emits_one_successful_command_result_after_ack():
    manager = DeviceManager()
    manager.devices[0] = SpectrometerDevice(0, "COM_TEST")
    results = []
    manager.command_completed.connect(
        lambda device_id, cmd, success, params: results.append(
            (device_id, cmd, success, bytes(params))
        )
    )

    manager._on_response(0, CmdCode.START_CONTINUOUS, b"\x60")

    assert results == [(0, int(CmdCode.START_CONTINUOUS), True, b"\x60")]
    assert manager.devices[0].acquiring


def test_set_command_emits_failure_for_missing_status_and_nak():
    manager = DeviceManager()
    manager.devices[0] = SpectrometerDevice(0, "COM_TEST")
    results = []
    manager.command_completed.connect(
        lambda device_id, cmd, success, params: results.append(
            (device_id, cmd, success, bytes(params))
        )
    )

    manager._on_response(0, CmdCode.SET_TRIG_MODE, b"")
    manager._on_response(0, CmdCode.STOP_ACQUISITION, b"\x70")

    assert results == [
        (0, int(CmdCode.SET_TRIG_MODE), False, b""),
        (0, int(CmdCode.STOP_ACQUISITION), False, b"\x70"),
    ]


def test_compatible_init_ack_reports_original_init_command():
    manager = DeviceManager()
    manager.devices[0] = SpectrometerDevice(0, "COM_TEST")
    results = []
    manager.command_completed.connect(
        lambda device_id, cmd, success, params: results.append(
            (device_id, cmd, success, bytes(params))
        )
    )

    manager._on_response(0, CmdCode.GET_VERSION, b"\x61")

    assert results == [(0, int(CmdCode.DEVICE_INIT), True, b"\x61")]


def test_simulated_control_commands_emit_the_same_completion_events():
    manager = DeviceManager()
    device_id = manager.add_simulated_device("SIM-1", "SIM001", 16, 400, 1)
    results = []
    manager.command_completed.connect(
        lambda did, cmd, success, params: results.append((did, cmd, success))
    )

    manager.set_trigger_mode(device_id, 0)
    manager.start_acquisition(device_id, continuous=True)
    manager.stop_acquisition(device_id)

    assert results == [
        (device_id, int(CmdCode.SET_TRIG_MODE), True),
        (device_id, int(CmdCode.START_CONTINUOUS), True),
        (device_id, int(CmdCode.STOP_ACQUISITION), True),
    ]
