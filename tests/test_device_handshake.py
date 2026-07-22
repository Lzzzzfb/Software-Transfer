import struct
import time

from spectrometer.communication.protocol import CmdCode
from spectrometer.device.device_manager import DeviceManager
from spectrometer.device.spectrometer import SpectrometerDevice


def version_payload(
    pixel_count=4096, start_pixel=8, valid_pixel=4080
):
    return struct.pack(
        "<IIffIIIIII",
        0x5A474341,
        2,
        1.1,
        2.3,
        12345,
        pixel_count,
        start_pixel,
        valid_pixel,
        10,
        10_000_000,
    )


def test_valid_version_response_announces_and_connects_device():
    manager = DeviceManager()
    device = SpectrometerDevice(0, "COM14")
    manager.devices[0] = device
    manager._probing.add(0)
    manager._probe_tokens[0] = 1
    added = []
    connected = []
    manager.device_added.connect(added.append)
    manager.device_connected.connect(connected.append)

    manager._on_response(0, CmdCode.GET_VERSION, version_payload())

    assert added == [0]
    assert connected == [0]
    assert device.connected
    assert device.initialized
    assert device.info.pixel_count == 4096
    assert device.info.start_pixel == 8
    assert device.info.valid_pixel == 4080
    assert manager.get_connected_devices() == [device]


def test_invalid_version_response_does_not_recognize_device():
    manager = DeviceManager()
    device = SpectrometerDevice(0, "COM10")
    manager.devices[0] = device
    manager._probing.add(0)
    manager._probe_tokens[0] = 1
    added = []
    manager.device_added.connect(added.append)

    manager._on_response(
        0,
        CmdCode.GET_VERSION,
        version_payload(pixel_count=1024, start_pixel=1000, valid_pixel=100),
    )

    assert added == []
    assert not device.connected
    assert not device.initialized
    assert manager.get_connected_devices() == []


def test_probe_timeout_removes_candidate_without_exposing_it_in_ui():
    manager = DeviceManager()
    manager.devices[0] = SpectrometerDevice(0, "COM10")
    manager._probing.add(0)
    manager._probe_tokens[0] = 7
    removed = []
    diagnostics = []
    manager.device_removed.connect(removed.append)
    manager.diagnostic_event.connect(diagnostics.append)

    manager._on_probe_timeout(0, 7)

    assert 0 not in manager.devices
    assert removed == []
    assert diagnostics and "未通过光谱仪协议握手" in diagnostics[-1]
    assert manager._probe_retry_after["COM10"] > time.monotonic()


def test_stale_probe_timeout_cannot_remove_recognized_device():
    manager = DeviceManager()
    device = SpectrometerDevice(0, "COM14")
    device.connected = True
    device.initialized = True
    manager.devices[0] = device
    manager._announced_devices.add(0)

    manager._on_probe_timeout(0, 99)

    assert manager.devices[0] is device

