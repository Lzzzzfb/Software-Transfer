import pytest

from spectrometer.motor.models import (
    MOTOR_PULSES_PER_MM,
    MOTOR_SPEED_MAX_HZ,
    MOTOR_SPEED_MIN_HZ,
    MOTOR_TRAVEL_MM,
    Axis,
    Direction,
    MotorAxisStatus,
    MotorConfiguration,
    MotorStatus,
    Position,
)


def test_motor_truth_constants_match_confirmed_mechanics():
    assert MOTOR_PULSES_PER_MM == 320
    assert MOTOR_TRAVEL_MM == 15.0
    assert 15 * MOTOR_PULSES_PER_MM == 4800
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
