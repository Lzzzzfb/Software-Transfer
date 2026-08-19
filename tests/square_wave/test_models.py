import pytest

from spectrometer.square_wave.models import (
    DEFAULT_FREQUENCY_HZ,
    DEFAULT_PULSE_WIDTH_US,
    OutputOwner,
    OutputState,
    SquareWaveParameters,
)


def test_confirmed_defaults_and_boundaries():
    assert SquareWaveParameters() == SquareWaveParameters(10, 5)
    assert DEFAULT_FREQUENCY_HZ == 10
    assert DEFAULT_PULSE_WIDTH_US == 5
    assert SquareWaveParameters(1, 1).frequency_hz == 1
    assert SquareWaveParameters(10, 9999).pulse_width_us == 9999


@pytest.mark.parametrize("value", [0, 11, 1.5, True, None, "10"])
def test_frequency_rejects_out_of_range_or_non_integer_values(value):
    with pytest.raises(ValueError):
        SquareWaveParameters(value, 5)


@pytest.mark.parametrize("value", [0, 10000, 1.5, True, None, "5"])
def test_pulse_width_rejects_out_of_range_or_non_integer_values(value):
    with pytest.raises(ValueError):
        SquareWaveParameters(10, value)


def test_output_owner_and_state_have_explicit_unknown_values():
    assert OutputOwner.NONE.value == "none"
    assert OutputOwner.MANUAL.value == "manual"
    assert OutputOwner.SCAN.value == "scan"
    assert OutputState.STOPPED.value == "stopped"
    assert OutputState.RUNNING.value == "running"
    assert OutputState.UNKNOWN.value == "unknown"
