import pytest

from spectrometer.motor.models import Axis, Direction, Position
from spectrometer.motor.scan import ScanParameters, build_scan_plan


def test_ten_by_ten_scan_has_n_plus_one_x_strokes_and_n_y_advances():
    plan = build_scan_plan(
        ScanParameters(
            x_mm=10,
            y_mm=1,
            line_count=10,
            scan_count=2,
        ),
        start=Position(0, 0),
    )

    assert len(plan.rounds) == 2
    first = plan.rounds[0]
    x_moves = [move for move in first.scan_moves if move.axis is Axis.X]
    y_moves = [move for move in first.scan_moves if move.axis is Axis.Y]
    assert len(x_moves) == 11
    assert len(y_moves) == 10
    assert [move.direction for move in x_moves[:3]] == [
        Direction.POSITIVE,
        Direction.NEGATIVE,
        Direction.POSITIVE,
    ]
    assert first.scan_end == Position(10, 10)
    assert first.end == Position(0, 0)
    assert all(move.acquiring for move in first.scan_moves)
    assert all(not move.acquiring for move in first.return_moves)


def test_substeps_preserve_geometry_and_apply_one_wait_per_substep():
    plan = build_scan_plan(
        ScanParameters(
            x_mm=10,
            y_mm=1,
            line_count=2,
            scan_count=1,
            x_steps=10,
            y_steps=2,
            dwell_seconds=1,
        ),
        start=Position(0, 0),
    )
    scan_moves = plan.rounds[0].scan_moves
    x_moves = [move for move in scan_moves if move.axis is Axis.X]
    y_moves = [move for move in scan_moves if move.axis is Axis.Y]

    assert len(x_moves) == 30
    assert {move.distance_mm for move in x_moves} == {1.0}
    assert len(y_moves) == 4
    assert {move.distance_mm for move in y_moves} == {0.5}
    assert all(move.dwell_after_seconds == 1.0 for move in scan_moves[:-1])
    assert scan_moves[-1].dwell_after_seconds == 0.0
    assert plan.rounds[0].scan_end == Position(10, 2)


def test_odd_line_count_finishes_x_at_start_side_before_return():
    plan = build_scan_plan(
        ScanParameters(x_mm=4, y_mm=1, line_count=3, scan_count=1),
        start=Position(2, 2),
    )
    assert plan.rounds[0].scan_end == Position(2, 5)
    assert [move.axis for move in plan.rounds[0].return_moves] == [Axis.Y]


@pytest.mark.parametrize(
    "parameters,start,error",
    [
        (ScanParameters(10, 1, 10, 1), Position(6, 0), "X"),
        (ScanParameters(10, 1, 10, 1), Position(0, 6), "Y"),
        (ScanParameters(0, 1, 1, 1), Position(0, 0), "x_mm"),
        (
            ScanParameters(0.001, 1, 1, 1),
            Position(0, 0),
            "pulse",
        ),
    ],
)
def test_scan_preflight_rejects_invalid_or_out_of_bounds_path(
    parameters, start, error
):
    with pytest.raises(ValueError, match=error):
        build_scan_plan(parameters, start=start)


def test_non_divisible_substeps_preserve_exact_integer_pulses():
    plan = build_scan_plan(
        ScanParameters(1.0, 0.1, 1, 1, x_steps=3, y_steps=3)
    )
    x_pulses = [
        move.pulses
        for move in plan.rounds[0].scan_moves
        if move.axis is Axis.X
    ]
    assert x_pulses[:3] == [107, 107, 106]
    assert sum(x_pulses[:3]) == 320


def test_scan_plan_rejects_accidentally_excessive_move_count():
    with pytest.raises(ValueError, match="maximum"):
        build_scan_plan(
            ScanParameters(
                1,
                0.003125,
                1000,
                1000,
                x_steps=100,
                y_steps=100,
            )
        )
