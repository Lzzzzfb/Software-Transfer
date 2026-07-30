from dataclasses import FrozenInstanceError

import pytest

from spectrometer.domain.enums import (
    AcquisitionMode,
    AcquisitionOwner,
    ControlState,
    StorageFormat,
    SyncMode,
    control_state_label,
)
from spectrometer.domain.models import AcquisitionRequest


def test_acquisition_request_is_an_immutable_snapshot():
    request = AcquisitionRequest.create(
        AcquisitionOwner.GLOBAL,
        [2, 1],
        mode=AcquisitionMode.CONTINUOUS,
        sync_mode=SyncMode.SOFTWARE,
        auto_store=True,
        storage_format=StorageFormat.CSV_EXCEL,
        batch_size=500,
    )

    assert request.device_ids == (2, 1)
    assert request.owner is AcquisitionOwner.GLOBAL
    assert request.task_id
    assert request.started_at
    with pytest.raises(FrozenInstanceError):
        request.batch_size = 10


@pytest.mark.parametrize("device_ids", [[], [1, 1]])
def test_acquisition_request_rejects_missing_or_duplicate_devices(device_ids):
    with pytest.raises(ValueError):
        AcquisitionRequest.create(AcquisitionOwner.LOCAL, device_ids)


def test_calibration_request_requires_supported_reference_kind():
    with pytest.raises(ValueError):
        AcquisitionRequest.create(
            AcquisitionOwner.CALIBRATION,
            [1],
            reference_kind="invalid",
        )

    request = AcquisitionRequest.create(
        AcquisitionOwner.CALIBRATION,
        [1],
        mode=AcquisitionMode.SINGLE,
        reference_kind="background",
    )
    assert request.reference_kind == "background"


def test_scan_request_is_a_distinct_continuous_owner():
    request = AcquisitionRequest.create(
        AcquisitionOwner.SCAN,
        [1, 2],
        mode=AcquisitionMode.CONTINUOUS,
        auto_store=True,
    )

    assert request.owner is AcquisitionOwner.SCAN
    assert request.mode is AcquisitionMode.CONTINUOUS
    assert request.auto_store is True


def test_control_state_labels_cover_every_state():
    labels = [control_state_label(state) for state in ControlState]
    assert all(labels)
    assert len(labels) == len(set(labels))
    assert control_state_label(ControlState.FINALIZING) == "正在保存"
