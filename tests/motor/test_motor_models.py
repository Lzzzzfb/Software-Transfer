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
)


def test_motor_truth_constants_match_firmware():
    assert MOTOR_PULSES_PER_MM == 640
    assert MOTOR_TRAVEL_MM == 15.0
    assert MOTOR_SPEED_MIN_HZ == 100
    assert MOTOR_SPEED_MAX_HZ == 14_000


@pytest.mark.parametrize("speed", [100, 10_000, 14_000])
def test_configuration_accepts_speed_boundaries(speed):
    config = MotorConfiguration(speed_hz=speed)
    assert config.speed_hz == speed


@pytest.mark.parametrize("speed", [99, 14_001])
def test_configuration_rejects_speed_outside_validated_range(speed):
    with pytest.raises(ValueError, match="speed"):
        MotorConfiguration(speed_hz=speed)


def test_axis_status_requires_coordinate_inside_software_travel():
    status = MotorAxisStatus(
        axis=Axis.X,
        position_mm=15.0,
        position_valid=True,
        moving=False,
        zero_limit_active=False,
    )
    assert status.position_mm == 15.0
    assert Direction.POSITIVE.protocol_value == 1
    assert Direction.NEGATIVE.protocol_value == 0

    with pytest.raises(ValueError, match="position"):
        MotorAxisStatus(
            axis=Axis.X,
            position_mm=-0.001,
            position_valid=True,
            moving=False,
            zero_limit_active=False,
        )

