import json

from spectrometer.motor.settings_store import (
    HostMotorSettings,
    MotorSettingsStore,
)


def test_settings_defaults_and_round_trip(tmp_path):
    store = MotorSettingsStore(tmp_path / "motor-settings.json")
    assert store.load() == HostMotorSettings()
    settings = HostMotorSettings(
        automatic_port=False,
        port_name="/dev/serial/by-id/usb-rs485",
        address=7,
        baud_rate=19200,
        reverse_x=True,
    )
    store.save(settings)
    assert store.load() == settings
    assert not (tmp_path / "motor-settings.json.tmp").exists()


def test_corrupt_or_invalid_settings_fall_back_safely(tmp_path):
    path = tmp_path / "motor-settings.json"
    path.write_text("not json", encoding="utf-8")
    assert MotorSettingsStore(path).load() == HostMotorSettings()
    path.write_text(json.dumps({"address": 0}), encoding="utf-8")
    assert MotorSettingsStore(path).load() == HostMotorSettings()
