from pathlib import Path

from spectrometer.services.platform_paths import (
    config_directory,
    data_directory,
    log_directory,
)
from spectrometer.services.settings_service import SettingsService


def test_xdg_directories_and_default_data_path(monkeypatch, tmp_path):
    home = tmp_path / "home"
    config = tmp_path / "config"
    state = tmp_path / "state"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))

    assert config_directory() == config / "ZGCAI" / "Spectrometer"
    assert data_directory() == home / "ZGCAI-Spectrometer-Data"
    assert log_directory() == state / "ZGCAI" / "Spectrometer"
    assert SettingsService().path == config / "ZGCAI" / "Spectrometer" / "settings.json"
    assert SettingsService().load()["storage_path"] == str(
        home / "ZGCAI-Spectrometer-Data"
    )


def test_xdg_directories_fall_back_to_home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)

    assert config_directory() == tmp_path / ".config" / "ZGCAI" / "Spectrometer"
    assert log_directory() == tmp_path / ".local" / "state" / "ZGCAI" / "Spectrometer"
