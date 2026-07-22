from pathlib import Path

import pytest

from spectrometer.domain.models import SpectrumReference
from spectrometer.processing.references import ReferenceRepository


def test_references_never_cross_device_or_pixel_configuration(tmp_path):
    repository = ReferenceRepository(tmp_path)
    compatible = SpectrumReference.create("SN-A", "background", [1, 2])
    wrong_device = SpectrumReference.create("SN-B", "background", [3, 4])
    wrong_pixels = SpectrumReference.create("SN-A", "background", [1, 2, 3])
    for item in (compatible, wrong_device, wrong_pixels):
        repository.save(item)

    matches = repository.list_compatible("SN-A", 2, "background")
    assert [item.reference_id for item in matches] == [compatible.reference_id]
    assert repository.latest_compatible("SN-B", 2, "reference") is None


def test_reference_batch_is_committed_together(tmp_path):
    repository = ReferenceRepository(tmp_path)
    references = [
        SpectrumReference.create("SN-A", "background", [1, 2]),
        SpectrumReference.create("SN-B", "background", [3, 4]),
    ]

    paths = repository.save_batch(references)

    assert len(paths) == 2
    assert all(path.exists() for path in paths)


def test_reference_batch_rolls_back_new_files_if_commit_fails(tmp_path, monkeypatch):
    repository = ReferenceRepository(tmp_path)
    references = [
        SpectrumReference.create("SN-A", "reference", [1, 2]),
        SpectrumReference.create("SN-B", "reference", [3, 4]),
    ]
    original_replace = Path.replace
    calls = 0

    def fail_second_replace(path, target):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected commit failure")
        return original_replace(path, target)

    monkeypatch.setattr(Path, "replace", fail_second_replace)

    with pytest.raises(OSError, match="injected"):
        repository.save_batch(references)

    assert list(tmp_path.glob("*.json")) == []
    assert list(tmp_path.glob("*.tmp")) == []
