from spectrometer.storage.localization import (
    localize_metadata_label,
    localize_metadata_value,
)


def test_known_metadata_labels_are_translated_and_unknown_is_preserved():
    assert localize_metadata_label("Session ID") == "会话编号"
    assert localize_metadata_label("Integration Time (us)") == "积分时间 (μs)"
    assert localize_metadata_label("Intensity Calibration ID") == "强度校准 ID"
    assert localize_metadata_label("Third Party Field") == "Third Party Field"


def test_boolean_processing_trigger_and_sync_values_are_translated():
    assert localize_metadata_value("Background Applied", True) == "是"
    assert localize_metadata_value("Reference Applied", False) == "否"
    assert localize_metadata_value("Processing Mode", "raw") == "原始强度"
    assert (
        localize_metadata_value(
            "Processing Modes", "dark_subtract, absorbance"
        )
        == "扣背景、吸光度"
    )
    assert localize_metadata_value("Trigger Mode", 0) == "软件触发"
    assert localize_metadata_value("Trigger Mode", 1) == "软件主机触发"
    assert localize_metadata_value("Trigger Mode", 2) == "外部触发"
    assert localize_metadata_value("Sync Mode", "independent") == "独立采集"
    assert localize_metadata_value("Sync Mode", "software") == "软件同步"
    assert (
        localize_metadata_value("Sync Mode", "hard_internal")
        == "内部硬同步"
    )
    assert (
        localize_metadata_value("Sync Mode", "hard_external")
        == "外部硬同步"
    )


def test_unknown_metadata_value_keeps_original_type_and_value():
    marker = {"vendor": 3}
    assert localize_metadata_value("Third Party Field", marker) is marker
