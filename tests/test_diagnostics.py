import numpy as np
import pytest

from coffee_service.diagnostics import diagnose_returns


def test_exact_zero_and_tiny_prediction_are_not_a_binary_up_baseline():
    result = diagnose_returns([-0.1, 0, 0.1], [0, 0, 1e-12])
    assert result["direction_accuracy"] == 2 / 3
    assert result["threshold_direction_accuracy"] == 1 / 3
    assert result["predicted_near_zero_ratio"] == 1
    assert result["actual_up_ratio"] == result["actual_down_ratio"] == 1 / 3
    assert result["predicted_std"] > 0
    with pytest.raises(ValueError):
        diagnose_returns([0], [np.nan])
