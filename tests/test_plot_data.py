import numpy as np
import pytest

from spectrometer.ui.plot_data import (
    finite_curve_bounds,
    validated_plot_arrays,
    visible_finite_segments,
)


@pytest.mark.parametrize("length", [0, 1, 17, 3648, 4096, 6144])
def test_validated_plot_arrays_preserve_dynamic_lengths(length):
    source = np.arange(length * 2, dtype=np.float64)[::2]

    x, y = validated_plot_arrays(source, source + 1)

    assert x.shape == (length,)
    assert y.shape == (length,)
    assert x.dtype == np.float64
    assert y.dtype == np.float64
    assert x.flags.c_contiguous
    assert y.flags.c_contiguous


def test_validated_plot_arrays_reject_bad_shapes_and_nonfinite_x():
    with pytest.raises(ValueError, match="一维且长度相同"):
        validated_plot_arrays((0, 1), (1,))
    with pytest.raises(ValueError, match="一维且长度相同"):
        validated_plot_arrays(((0, 1),), ((1, 2),))
    with pytest.raises(ValueError, match="X.*有限"):
        validated_plot_arrays((0, np.nan), (1, 2))


def test_validated_plot_arrays_keep_nonfinite_y_as_gaps():
    x, y = validated_plot_arrays((0, 1, 2), (1, np.nan, np.inf))

    assert np.array_equal(x, (0, 1, 2))
    assert y[0] == 1
    assert np.isnan(y[1])
    assert np.isinf(y[2])


def test_finite_curve_bounds_ignore_nonfinite_y_and_empty_curves():
    assert finite_curve_bounds(np.array([]), np.array([])) is None
    assert finite_curve_bounds(
        np.array([0.0, 1.0]), np.array([np.nan, np.inf])
    ) is None
    assert finite_curve_bounds(
        np.array([0.0, 1.0, 2.0]),
        np.array([np.nan, 10.0, np.inf]),
    ) == (1.0, 1.0, 10.0, 10.0)


def test_visible_segments_keep_every_visible_point_and_boundary_neighbors():
    x = np.arange(10, dtype=np.float64)
    y = x * 10

    segments = visible_finite_segments(x, y, 3.2, 6.2)

    assert len(segments) == 1
    assert np.array_equal(segments[0][0], (3, 4, 5, 6, 7))
    assert np.array_equal(segments[0][1], (30, 40, 50, 60, 70))


def test_visible_segments_include_crossing_pair_when_no_sample_is_inside():
    x = np.arange(6, dtype=np.float64)
    y = x.copy()

    segments = visible_finite_segments(x, y, 2.2, 2.8)

    assert len(segments) == 1
    assert np.array_equal(segments[0][0], (2, 3))


def test_visible_segments_split_nonfinite_y_without_bridge():
    x = np.arange(10, dtype=np.float64)
    y = x.copy()
    y[5] = np.nan

    segments = visible_finite_segments(x, y, 3.2, 6.2)

    assert [segment[0].tolist() for segment in segments] == [[3.0, 4.0], [6.0, 7.0]]


def test_visible_segments_preserve_nonmonotonic_acquisition_order():
    x = np.array([0.0, 5.0, 2.0, 8.0, 10.0])
    y = np.arange(x.size, dtype=np.float64)

    segments = visible_finite_segments(x, y, 3.0, 6.0)

    assert len(segments) == 1
    assert np.array_equal(segments[0][0], (0.0, 5.0, 2.0, 8.0))
    assert np.array_equal(segments[0][1], (0.0, 1.0, 2.0, 3.0))
