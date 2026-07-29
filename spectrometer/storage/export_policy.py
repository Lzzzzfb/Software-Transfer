"""单/多设备任务的 CSV 与 Excel 输出决策。"""

from dataclasses import dataclass

from ..domain.enums import StorageFormat


@dataclass(frozen=True)
class ExportPlan:
    csv_device_ids: tuple
    write_excel: bool

    @property
    def write_csv(self):
        return bool(self.csv_device_ids)


def resolve_export_plan(storage_format, device_ids) -> ExportPlan:
    storage_format = StorageFormat(storage_format)
    unique_device_ids = tuple(
        dict.fromkeys(int(device_id) for device_id in device_ids)
    )
    if not unique_device_ids:
        raise ValueError("导出计划至少需要一台设备")

    single_device = len(unique_device_ids) == 1
    if storage_format is StorageFormat.CSV:
        return ExportPlan(unique_device_ids, False)
    if storage_format is StorageFormat.EXCEL:
        return ExportPlan((), True)
    if storage_format is StorageFormat.CSV_EXCEL:
        if single_device:
            return ExportPlan(unique_device_ids, False)
        return ExportPlan((), True)
    raise ValueError(f"不支持的存储格式：{storage_format}")
