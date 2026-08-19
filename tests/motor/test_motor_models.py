import pytest

from spectrometer.motor.models import (
    MOTOR_PULSES_PER_MM,
    MOTOR_SPEED_MAX_HZ,
    MOTOR_SPEED_MIN_HZ,
    MOTOR_TRAVEL_MM,
    MOTOR_TRAVEL_PULSES,
    Axis,
    Direction,
    MotorAxisStatus,
    MotorConfiguration,
    MotorStatus,
    Position,
    home_timeout_seconds,
    stall_release_timeout_seconds,
)


def test_motor_truth_constants_match_confirmed_mechanics():
    assert MOTOR_PULSES_PER_MM == 320
    assert MOTOR_TRAVEL_MM == 15.0
    assert 15 * MOTOR_PULSES_PER_MM == MOTOR_TRAVEL_PULSES == 4800
    assert tuple(Axis) == (Axis.X, Axis.Y)
    assert Direction.POSITIVE.sign == 1
    assert Direction.NEGATIVE.sign == -1


@pytest.mark.parametrize("speed", [100, 10_000, 14_000])
def test_configuration_accepts_speed_boundaries(speed):
    assert MotorConfiguration(speed_hz=speed).speed_hz == speed


@pytest.mark.parametrize("speed", [99, 14_001])
def test_configuration_rejects_speed_outside_validated_range(speed):
    with pytest.raises(ValueError, match="speed"):
        MotorConfiguration(speed_hz=speed)


def test_software_coordinate_can_be_negative_without_claiming_calibration():
    x = MotorAxisStatus(Axis.X, 320, -2.5, False, False, False)
    y = MotorAxisStatus(Axis.Y, 0, 0.0, False, False, False)
    status = MotorStatus(x, y)
    assert status.x.position_mm == -2.5
    assert status.position is None


def test_mechanical_position_stays_within_confirmed_travel():
    assert Position(0, 15) == Position(0, 15)
    with pytest.raises(ValueError, match="between 0 and 15"):
        Position(-0.001, 0)


@pytest.mark.parametrize(
    ("speed", "expected"),
    [(100, 1.0), (5000, 0.96), (10_000, 0.48)],
)
def test_stall_release_timeout_is_bounded_by_time_and_travel(speed, expected):
    assert stall_release_timeout_seconds(speed) == pytest.approx(expected)
    assert stall_release_timeout_seconds(-speed) == pytest.approx(expected)


@pytest.mark.parametrize(
    ("speed", "expected"),
    [(100, 5.0), (5000, 3.38), (10_000, 2.0)],
)
def test_home_timeout_is_clamped_between_two_and_five_seconds(speed, expected):
    assert home_timeout_seconds(speed) == pytest.approx(expected)


@pytest.mark.parametrize("function", [stall_release_timeout_seconds, home_timeout_seconds])
def test_motion_timeout_helpers_reject_zero(function):
    with pytest.raises(ValueError, match="non-zero"):
        function(0)


def test_limit_state_can_be_unknown_and_stop_unknown_has_a_reason():
    x = MotorAxisStatus(Axis.X, 0, 0.0, False, False, None)
    y = MotorAxisStatus(Axis.Y, 0, 0.0, False, False, False)
    status = MotorStatus(x, y, fault_latched=True, fault_reason="stop_unconfirmed")
    assert status.x.zero_limit_active is None
    assert status.fault_latched
    assert status.fault_reason == "stop_unconfirmed"
