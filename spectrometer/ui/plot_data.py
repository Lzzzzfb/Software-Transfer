"""绘图后端共享的完整数据校验和可见片段选择。"""

from __future__ import annotations

from typing import Optional, Tuple

import numpy as np


CurveBounds = Tuple[float, float, float, float]
CurveSegment = Tuple[np.ndarray, np.ndarray]


def validated_plot_arrays(x, y) -> CurveSegment:
    """返回连续的一维数组；Y 的非有限值保留为曲线断点。"""

    x_array = np.ascontiguousarray(x, dtype=np.float64)
    y_array = np.ascontiguousarray(y, dtype=np.float64)
    if x_array.ndim != 1 or x_array.shape != y_array.shape:
        raise ValueError("绘图 x/y 必须是一维且长度相同")
    if not np.isfinite(x_array).all():
        raise ValueError("绘图 X 必须全部为有限数值")
    return x_array, y_array


def finite_curve_bounds(x: np.ndarray, y: np.ndarray) -> Optional[CurveBounds]:
    """计算真正可绘制点的边界；没有有限 Y 时返回 ``None``。"""

    finite = np.isfinite(x) & np.isfinite(y)
    if not np.any(finite):
        return None
    finite_x = x[finite]
    finite_y = y[finite]
    return (
        float(finite_x.min()),
        float(finite_x.max()),
        float(finite_y.min()),
        float(finite_y.max()),
    )


def visible_finite_segments(
    x: np.ndarray,
    y: np.ndarray,
    x_min: float,
    x_max: float,
) -> list[CurveSegment]:
    """选择与当前 X 视图相交的完整有限折线段。

    只要相邻原始点构成的线段与视图相交，就同时保留两个端点。这既保留
    当前范围内的全部原始点，也提供正确裁剪所需的边界相邻点。非有限 Y
    会将结果拆成独立片段，绝不会跨断点连接。
    """

    if x.ndim != 1 or x.shape != y.shape or x.size < 2:
        return []
    lower = min(float(x_min), float(x_max))
    upper = max(float(x_min), float(x_max))
    segment_minimum = np.minimum(x[:-1], x[1:])
    segment_maximum = np.maximum(x[:-1], x[1:])
    intersects = (segment_minimum <= upper) & (segment_maximum >= lower)

    visible = (x >= lower) & (x <= upper)
    visible[:-1] |= intersects
    visible[1:] |= intersects
    drawable = visible & np.isfinite(y)

    changes = np.diff(np.concatenate(([False], drawable, [False])).astype(np.int8))
    starts = np.flatnonzero(changes == 1)
    stops = np.flatnonzero(changes == -1)
    return [
        (x[start:stop], y[start:stop])
        for start, stop in zip(starts, stops)
        if stop - start >= 2
    ]
