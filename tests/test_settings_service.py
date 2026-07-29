from spectrometer.services.settings_service import SettingsService


def test_settings_round_trip_and_invalid_values_are_normalized(tmp_path):
    service = SettingsService(tmp_path / "设置.json")
    service.save({"storage_path": "D:/数据", "batch_size": 1200, "storage_format": "bad"})
    settings = service.load()
    assert settings["storage_path"] == "D:/数据"
    assert settings["batch_size"] == 1000
    assert settings["storage_format"] == "csv_excel"


def test_airpls_defaults_and_valid_values_round_trip(tmp_path):
    service = SettingsService(tmp_path / "设置.json")

    defaults = service.load()
    assert defaults["airpls_enabled"] is False
    assert defaults["airpls_lam"] == 1e5
    assert defaults["airpls_order"] == 2
    assert defaults["airpls_max_iter"] == 15

    service.save(
        {
            "airpls_enabled": True,
            "airpls_lam": 2e6,
            "airpls_order": 3,
            "airpls_max_iter": 25,
        }
    )
    stored = service.load()
    assert stored["airpls_enabled"] is True
    assert stored["airpls_lam"] == 2e6
    assert stored["airpls_order"] == 3
    assert stored["airpls_max_iter"] == 25


def test_invalid_airpls_settings_fall_back_to_defaults(tmp_path):
    service = SettingsService(tmp_path / "设置.json")
    service.save(
        {
            "airpls_enabled": "yes",
            "airpls_lam": 0,
            "airpls_order": 9,
            "airpls_max_iter": -1,
        }
    )

    settings = service.load()

    assert settings["airpls_enabled"] is False
    assert settings["airpls_lam"] == 1e5
    assert settings["airpls_order"] == 2
    assert settings["airpls_max_iter"] == 15
