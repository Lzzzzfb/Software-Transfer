import pytest

from spectrometer.domain.enums import StorageFormat
from spectrometer.storage.export_policy import resolve_export_plan


@pytest.mark.parametrize(
    ("storage_format", "device_ids", "csv_ids", "excel"),
    [
        (StorageFormat.CSV, [3], (3,), False),
        (StorageFormat.EXCEL, [3], (), True),
        (StorageFormat.CSV_EXCEL, [3], (3,), False),
        (StorageFormat.CSV, [3, 7], (3, 7), False),
        (StorageFormat.EXCEL, [3, 7], (), True),
        (StorageFormat.CSV_EXCEL, [3, 7], (), True),
    ],
)
def test_export_policy_matches_single_and_multi_device_rules(
    storage_format, device_ids, csv_ids, excel
):
    plan = resolve_export_plan(storage_format, device_ids)
    assert plan.csv_device_ids == csv_ids
    assert plan.write_excel is excel


def test_export_policy_deduplicates_device_ids_in_stable_order():
    plan = resolve_export_plan(StorageFormat.CSV, [7, 3, 7, 3])
    assert plan.csv_device_ids == (7, 3)


def test_export_policy_rejects_empty_device_set():
    with pytest.raises(ValueError, match="设备"):
        resolve_export_plan(StorageFormat.CSV_EXCEL, [])
