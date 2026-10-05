import numpy as np
import pandas as pd

from coffee.evaluate import (apply_selection, block_bootstrap_mean_diff, block_bootstrap_rmse_diff, crps_normal,
                             direction_metrics, metrics, select_by_score, select_by_year, shrink_weight)


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


def test_direction_metrics_count_only_confident_signals():
    y = np.array([0.03, -0.02, 0.01, -0.01, 0.02, 0.0])
    prob = np.array([0.8, 0.3, 0.55, 0.65, 0.5, 0.9])
    result = direction_metrics(y, prob, base_rate=0.5, threshold=0.6)
    # 실제 0인 날 제외 5일 중 신호: 0.8(구매, 맞음), 0.3(미루기, 맞음), 0.65(구매, 틀림) → 3/5
    assert result["coverage"] == 0.6 and np.isclose(result["precision"], 2 / 3)
    # 기준선은 같은 신호일에 늘 '구매'라고 했을 때: 세 날 중 오른 날은 첫째 날뿐 → 1/3
    assert np.isclose(result["signal_up_rate"], 1 / 3)
    assert np.isclose(result["base_brier"], 0.25)


def test_block_bootstrap_detects_better_model_and_is_reproducible():
    rng = np.random.default_rng(0)
    y = rng.normal(0, 0.05, 400)
    better, worse = y + rng.normal(0, 0.01, 400), np.zeros(400)
    first = block_bootstrap_rmse_diff(y, better, worse, block=20)
    assert first["high"] < 0  # better가 확실히 더 정확
    assert first == block_bootstrap_rmse_diff(y, better, worse, block=20)
    same = block_bootstrap_rmse_diff(y, worse, worse, block=20)
    assert same["low"] == same["high"] == same["diff"] == 0


def test_shrink_weight_is_least_squares_clipped_to_unit_interval():
    pred = np.array([1.0, -2.0, 3.0])
    assert np.isclose(shrink_weight(0.5 * pred, pred), 0.5)
    assert shrink_weight(-pred, pred) == 0.0 and shrink_weight(2 * pred, pred) == 1.0  # 반대 방향은 0, 1에서 자름
    assert shrink_weight(pred, np.zeros(3)) == 0.0


def test_select_by_year_looks_only_at_earlier_years():
    dates = pd.bdate_range("2009-01-01", "2012-12-31")
    rng = np.random.default_rng(0)
    y = rng.normal(0, 0.02, len(dates))
    data = pd.DataFrame({"y_5": y, "target_date_5": pd.Series(dates).shift(-5).to_numpy()}, index=dates)
    rows = np.arange(len(dates) - 5)
    noise = pd.Series(rng.normal(0, 0.02, len(rows)), index=rows)
    half = pd.Series(0.5 * y[rows], index=rows)  # 정답의 절반 크기를 맞히는 후보
    selection = select_by_year(data, 5, {"noise": noise, "half": half}, [2012])
    assert selection.loc[2012, "candidate"] == "half" and selection.loc[2012, "weight"] == 1.0
    in_2012 = data.index[rows].year == 2012
    target_end = data["target_date_5"].to_numpy()[rows] <= pd.Timestamp("2011-12-31")
    assert selection.loc[2012, "inner_rows"] == int((~in_2012 & target_end).sum())  # 목표일이 2012년인 행은 뺀다

    spoiled = {"noise": noise.copy(), "half": half.copy()}
    spoiled["noise"][in_2012] = y[rows][in_2012]  # 2012년에만 완벽한 후보는 2012년 선택에 영향을 주지 못한다
    pd.testing.assert_frame_equal(select_by_year(data, 5, spoiled, [2012]), selection)

    applied = apply_selection(data, {"noise": noise, "half": half}, selection)
    assert np.allclose(applied[in_2012], half[in_2012]) and applied[~in_2012].isna().all()


def test_crps_is_absolute_error_for_a_point_and_bootstrap_finds_lower_loss():
    assert np.isclose(crps_normal(0.0, 0.0, 1.0), (np.sqrt(2) - 1) / np.sqrt(np.pi))  # 정답이 분포 한가운데
    assert np.isclose(crps_normal(1.3, 0.2, 1e-9), 1.1)  # 폭이 0이면 절대오차
    rng = np.random.default_rng(0)
    loss = rng.random(500)
    result = block_bootstrap_mean_diff(loss, loss + 0.1, block=20)
    assert np.isclose(result["diff"], -0.1) and result["high"] < 0
    assert result == block_bootstrap_mean_diff(loss, loss + 0.1, block=20)  # 같은 seed면 같은 구간


def test_select_by_score_looks_only_at_earlier_years():
    dates = pd.bdate_range("2009-01-01", "2012-12-31")
    rng = np.random.default_rng(0)
    data = pd.DataFrame({"y_5": rng.normal(0, 0.02, len(dates)),
                         "target_date_5": pd.Series(dates).shift(-5).to_numpy()}, index=dates)
    rows = np.arange(len(dates) - 5)
    good = pd.Series(rng.random(len(rows)), index=rows)
    bad = good + 0.1
    selection = select_by_score(data, 5, {"bad": bad, "good": good}, [2012])
    in_2012 = data.index[rows].year == 2012
    target_end = data["target_date_5"].to_numpy()[rows] <= pd.Timestamp("2011-12-31")
    assert selection.loc[2012, "candidate"] == "good"
    assert selection.loc[2012, "inner_rows"] == int((~in_2012 & target_end).sum())  # 목표일이 2012년인 행은 뺀다

    spoiled = {"bad": bad.copy(), "good": good}
    spoiled["bad"][in_2012 | ~target_end] = -1.0  # 2012년(과 목표일이 2012년인 행)에만 좋은 후보는 영향이 없다
    pd.testing.assert_frame_equal(select_by_score(data, 5, spoiled, [2012]), selection)
