from spectrometer.device.device_manager import DeviceManager
from spectrometer.device.spectrometer import SpectrometerDevice


def recognized(device_id, port, serial, connected=True):
    device = SpectrometerDevice(device_id, port)
    device.connected = connected
    device.initialized = connected
    device.info.prod_serial = serial
    return device


def test_production_serial_indexes_device_identity():
    manager = DeviceManager()
    device = recognized(0, "ttyACM0", "001")
    manager.devices[0] = device

    manager._reconcile_production_serial(0, "001")

    assert manager._identity_index == {"001": 0}
    assert not device.identity_conflict


def test_new_tty_retires_disconnected_device_with_same_serial():
    manager = DeviceManager()
    old = recognized(0, "ttyACM0", "001", connected=False)
    new = recognized(1, "ttyACM1", "001")
    manager.devices.update({0: old, 1: new})
    manager._announced_devices.update({0, 1})
    manager._identity_index["001"] = 0
    diagnostics = []
    removed = []
    manager.diagnostic_event.connect(diagnostics.append)
    manager.device_removed.connect(removed.append)

    manager._reconcile_production_serial(1, "001")

    assert 0 not in manager.devices
    assert manager._identity_index["001"] == 1
    assert new.previous_port_name == "ttyACM0"
    assert removed == [0]
    assert "ttyACM1" in diagnostics[-1]


def test_duplicate_live_serial_is_reported_without_merging():
    manager = DeviceManager()
    first = recognized(0, "ttyACM0", "001")
    second = recognized(1, "ttyACM1", "001")
    manager.devices.update({0: first, 1: second})
    manager._identity_index["001"] = 0
    diagnostics = []
    manager.diagnostic_event.connect(diagnostics.append)

    manager._reconcile_production_serial(1, "001")

    assert set(manager.devices) == {0, 1}
    assert first.identity_conflict
    assert second.identity_conflict
    assert "重复" in diagnostics[-1]
