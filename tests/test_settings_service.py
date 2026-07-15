from spectrometer.services.settings_service import SettingsService


def test_settings_round_trip_and_invalid_values_are_normalized(tmp_path):
    service = SettingsService(tmp_path / "设置.json")
    service.save({"storage_path": "D:/数据", "batch_size": 1200, "storage_format": "bad"})
    settings = service.load()
    assert settings["storage_path"] == "D:/数据"
    assert settings["batch_size"] == 1000
    assert settings["storage_format"] == "csv_excel"
