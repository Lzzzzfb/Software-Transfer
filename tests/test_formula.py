import numpy as np
import pytest

from spectrometer.processing.formula import FormulaError, evaluate_formula, validate_formula


def variables():
    return {
        "I": np.array([10.0, 20.0]),
        "Idark": np.array([1.0, 2.0]),
        "Ib": np.array([1.0, 2.0]),
        "I0": np.array([100.0, 200.0]),
        "x": np.array([400.0, 500.0]),
    }


def test_supported_formula_matches_expected_numpy_result():
    result = evaluate_formula("-log10((I-Idark)/(I0-Idark))", variables())
    expected = -np.log10(np.array([9 / 99, 18 / 198]))
    np.testing.assert_allclose(result, expected)


@pytest.mark.parametrize(
    "formula",
    [
        "__import__('os').system('calc')",
        "I.__class__",
        "I[0]",
        "open('x')",
        "[v for v in I]",
        "I ** I",
        "I ** 99",
    ],
)
def test_dangerous_or_unbounded_expressions_are_rejected(formula):
    with pytest.raises(FormulaError):
        validate_formula(formula)


def test_non_finite_result_is_reported_instead_of_silent_fallback():
    with pytest.raises(FormulaError, match="无效值"):
        evaluate_formula("I / (I-I)", variables())


def test_scalar_result_is_broadcast_to_spectrum_length():
    np.testing.assert_array_equal(evaluate_formula("2", variables()), [2.0, 2.0])
