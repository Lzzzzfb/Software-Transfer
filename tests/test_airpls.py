from importlib import import_module

import numpy as np
import pytest


module = import_module("spectrometer.extensions.airpls")


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"lam": 0}, "lambda"),
        ({"lam": float("inf")}, "lambda"),
        ({"order": 0}, "差分阶数"),
        ({"order": 3, "size": 3}, "差分阶数"),
        ({"max_iter": 0}, "迭代次数"),
    ],
)
def test_validate_airpls_parameters(kwargs, message):
    size = kwargs.pop("size", 10)
    with pytest.raises(ValueError, match=message):
        module.validate_airpls_parameters(size=size, **kwargs)


def test_penalty_matrix_is_cached_by_size_and_order():
    first = module.penalty_matrix(64, 2)
    second = module.penalty_matrix(64, 2)
    other = module.penalty_matrix(64, 3)

    assert first is second
    assert first is not other


def test_airpls_accepts_cached_penalty_matrix():
    values = np.linspace(1, 10, 64)
    penalty = module.penalty_matrix(len(values), 2)

    baseline = module.airpls(
        values,
        lam=1e4,
        order=2,
        max_iter=3,
        penalty=penalty,
    )

    assert baseline.shape == values.shape
    assert np.all(np.isfinite(baseline))
