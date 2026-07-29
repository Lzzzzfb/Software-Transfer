import json

import pytest

from spectrometer.processing.profile_repository import (
    ProcessingProfileRepository,
    ProfileRepositoryError,
)
from spectrometer.processing.profiles import (
    AirplsProfile,
    DeviceAirplsOverride,
    ProfileValidationError,
    resolve_effective_profile,
)


def test_device_follows_global_by_default():
    global_profile = AirplsProfile(
        enabled=True, lam=2e5, order=3, max_iter=20
    )

    effective = resolve_effective_profile(global_profile, None)

    assert effective.enabled
    assert effective.lam == 2e5
    assert effective.order == 3
    assert effective.max_iter == 20
    assert effective.source == "global"


def test_device_override_wins_when_follow_global_is_false():
    global_profile = AirplsProfile(enabled=False)
    override = DeviceAirplsOverride(
        follow_global=False,
        enabled=True,
        lam=8e5,
        order=4,
        max_iter=30,
    )

    effective = resolve_effective_profile(global_profile, override)

    assert effective == AirplsProfile(
        enabled=True,
        lam=8e5,
        order=4,
        max_iter=30,
        source="device",
    )


def test_following_override_does_not_shadow_global():
    global_profile = AirplsProfile(enabled=True, lam=4e5)
    override = DeviceAirplsOverride(
        follow_global=True,
        enabled=False,
        lam=9e5,
    )

    assert resolve_effective_profile(global_profile, override).lam == 4e5


@pytest.mark.parametrize(
    "kwargs",
    [
        {"lam": 0},
        {"lam": 1e16},
        {"order": 0},
        {"order": 5},
        {"max_iter": 0},
        {"max_iter": 101},
    ],
)
def test_profile_rejects_out_of_range_parameters(kwargs):
    with pytest.raises(ProfileValidationError):
        AirplsProfile(**kwargs)


def test_profile_repository_round_trip_and_clear(tmp_path):
    repository = ProcessingProfileRepository(tmp_path / "profiles.json")
    override = DeviceAirplsOverride(
        follow_global=False,
        enabled=True,
        lam=7e5,
        order=3,
        max_iter=24,
    )

    repository.save("003", override)

    assert repository.load("003") == override
    assert repository.load("004") is None
    assert repository.clear("003")
    assert repository.load("003") is None
    assert not repository.clear("003")


def test_profile_repository_rejects_missing_serial(tmp_path):
    repository = ProcessingProfileRepository(tmp_path / "profiles.json")

    with pytest.raises(ProfileRepositoryError, match="生产序列号"):
        repository.save("", DeviceAirplsOverride())


def test_profile_repository_rejects_corrupt_record(tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(
        json.dumps(
            {
                "format_version": 1,
                "profiles": {
                    "003": {
                        "follow_global": False,
                        "enabled": True,
                        "lam": 0,
                        "order": 2,
                        "max_iter": 15,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    repository = ProcessingProfileRepository(path)

    with pytest.raises(ProfileRepositoryError, match="003"):
        repository.load("003")
