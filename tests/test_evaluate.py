import numpy as np

from coffee.evaluate import block_bootstrap_rmse_diff, metrics


def test_metrics_count_zero_prediction_as_no_direction():
    y = np.array([0.02, -0.01, 0.03, -0.02, 0.0])
    pred = np.array([0.01, 0.0, -0.01, -0.03, 0.05])
    result = metrics(y, pred)
    assert np.isclose(result["rmse"], np.sqrt(np.mean((y - pred) ** 2)))
    # 실제 0인 마지막 날은 제외: 맞춘 날은 1번째·4번째 → 2/4. 상승 재현율 1/2, 하락 재현율 1/2
    assert result["direction_acc"] == 0.5 and result["balanced_acc"] == 0.5


def test_naive_has_no_direction_metrics():
    result = metrics([0.01, -0.02], [0.0, 0.0])
    assert np.isnan(result["direction_acc"]) and np.isnan(result["balanced_acc"])


def test_block_bootstrap_detects_better_model_and_is_reproducible():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 0.05, 400)
    better, worse = y + rng.normal(0, 0.01, 400), np.zeros(400)
    first = block_bootstrap_rmse_diff(y, better, worse, block=20)
    assert first["high"] < 0  # better가 확실히 더 정확
    assert first == block_bootstrap_rmse_diff(y, better, worse, block=20)
    same = block_bootstrap_rmse_diff(y, worse, worse, block=20)
    assert same["low"] == same["high"] == same["diff"] == 0
