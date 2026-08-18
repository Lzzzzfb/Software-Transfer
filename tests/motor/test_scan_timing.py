import pytest

from spectrometer.motor.scan_timing import (
    HandoffTimingConfig,
    MotionHandoffEstimator,
)


def make_estimator(**overrides):
    values = {
        "start_pulses": 0,
        "target_pulses": 1000,
        "commanded_at": 10.0,
        "configured_speed_pps": 8000,
    }
    values.update(overrides)
    return MotionHandoffEstimator(**values)


def test_requires_a_trusted_motion_sample_before_predicting():
    estimator = make_estimator()

    assert not estimator.has_trusted_motion_sample
    assert estimator.handoff_deadline(10.1) is None

    estimator.add_sample(100, 10.1)

    assert estimator.has_trusted_motion_sample
    assert estimator.handoff_deadline(10.1) == pytest.approx(10.2925)


def test_near_target_and_timeout_ranges_use_absolute_remaining_distance():
    forward = make_estimator()
    forward.add_sample(680, 10.1)
    assert not forward.is_near_target
    assert forward.timeout_is_end_phase
    forward.add_sample(840, 10.2)
    assert forward.is_near_target

    reverse = make_estimator(start_pulses=1000, target_pulses=0)
    reverse.add_sample(320, 10.1)
    assert not reverse.is_near_target
    assert reverse.timeout_is_end_phase
    reverse.add_sample(160, 10.2)
    assert reverse.is_near_target


def test_observed_speed_is_capped_by_configured_speed():
    estimator = make_estimator()
    estimator.add_sample(100, 10.1)
    estimator.add_sample(900, 10.15)

    assert estimator.effective_speed_pps == 8000
    assert estimator.handoff_deadline(10.15) == pytest.approx(10.2425)


def test_slower_observed_speed_drives_the_prediction():
    estimator = make_estimator()
    estimator.add_sample(400, 10.1)
    estimator.add_sample(700, 10.2)

    assert estimator.effective_speed_pps == pytest.approx(3000)
    assert estimator.handoff_deadline(10.2) == pytest.approx(10.38)


def test_minimum_segment_deadline_applies_to_short_moves():
    estimator = make_estimator(target_pulses=100)
    estimator.add_sample(90, 10.05)

    assert estimator.handoff_deadline(10.05) == pytest.approx(10.2)


def test_deadline_never_precedes_the_calculation_time():
    estimator = make_estimator()
    estimator.add_sample(900, 10.1)

    assert estimator.handoff_deadline(11.0) == 11.0
    assert estimator.prediction_due(11.0)


@pytest.mark.parametrize(
    ("position", "captured_at", "message"),
    [
        (-1, 10.1, "away from target"),
        (1001, 10.1, "past target"),
    ],
)
def test_rejects_untrusted_motion_samples(position, captured_at, message):
    estimator = make_estimator()

    with pytest.raises(ValueError, match=message):
        estimator.add_sample(position, captured_at)


def test_rejects_non_increasing_sample_time():
    estimator = make_estimator()
    estimator.add_sample(100, 10.1)

    with pytest.raises(ValueError, match="strictly increasing"):
        estimator.add_sample(200, 10.1)


@pytest.mark.parametrize("speed", [0, -1])
def test_rejects_invalid_configured_speed(speed):
    with pytest.raises(ValueError, match="positive"):
        make_estimator(configured_speed_pps=speed)


def test_rejects_invalid_timing_configuration():
    with pytest.raises(ValueError, match="near-target"):
        HandoffTimingConfig(near_target_pulses=0)

    with pytest.raises(ValueError, match="end-phase"):
        HandoffTimingConfig(near_target_pulses=160, timeout_target_pulses=159)
