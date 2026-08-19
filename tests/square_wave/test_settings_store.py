import json

from spectrometer.square_wave.models import SquareWaveParameters
from spectrometer.square_wave.settings_store import (
    HostSquareWaveSettings,
    SquareWaveSettingsStore,
)


def test_settings_defaults_and_atomic_round_trip(tmp_path):
    path = tmp_path / "square-wave-settings.json"
    store = SquareWaveSettingsStore(path)
    assert store.load() == HostSquareWaveSettings()
    assert store.last_error == ""

    settings = HostSquareWaveSettings(
        automatic_port=False,
        port_name="/dev/serial/by-id/usb-square-wave",
        system_location="/dev/ttyACM1",
        usb_serial="ABC123",
        baud_rate=9600,
        parameters=SquareWaveParameters(4, 125),
    )
    store.save(settings)
    assert store.load() == settings
    assert not path.with_suffix(path.suffix + ".tmp").exists()
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["frequency_hz"] == 4
    assert payload["pulse_width_us"] == 125
    assert "scan_link_enabled" not in payload


def test_corrupt_settings_fall_back_without_overwriting_source(tmp_path):
    path = tmp_path / "square-wave-settings.json"
    path.write_text("not json", encoding="utf-8")
    store = SquareWaveSettingsStore(path)
    assert store.load() == HostSquareWaveSettings()
    assert store.last_error
    assert path.read_text(encoding="utf-8") == "not json"


def test_invalid_values_fall_back_safely(tmp_path):
    path = tmp_path / "square-wave-settings.json"
    path.write_text(
        json.dumps({"frequency_hz": 11, "pulse_width_us": 5}),
        encoding="utf-8",
    )
    store = SquareWaveSettingsStore(path)
    assert store.load() == HostSquareWaveSettings()
    assert "frequency" in store.last_error.lower()


def test_unknown_keys_do_not_restore_scan_link_state(tmp_path):
    path = tmp_path / "square-wave-settings.json"
    path.write_text(
        json.dumps(
            {
                "automatic_port": True,
                "frequency_hz": 3,
                "pulse_width_us": 7,
                "scan_link_enabled": True,
            }
        ),
        encoding="utf-8",
    )
    settings = SquareWaveSettingsStore(path).load()
    assert settings.parameters == SquareWaveParameters(3, 7)
    assert not hasattr(settings, "scan_link_enabled")
