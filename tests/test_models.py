import numpy as np
import pandas as pd
import pytest

from coffee.evaluate import usable_rows
from coffee.features import ALL_FEATURES, FEATURE_GROUPS, build_dataset
from coffee.models import (HAR_FEATURES, DistributionModel, DLinearModel, LightGBMClassifier, LightGBMModel,
                           LogisticModel, Momentum, Naive, RidgeModel, ScaledReturn, ShrunkProbability, ShrunkReturn,
                           _moving_average, buy_signal, load_bundle, price_range, return_model, save_bundle)


@pytest.fixture
def dataset(sources):
    data = build_dataset(sources)
    train = usable_rows(data, ALL_FEATURES, 5, "2015-01-01", "2015-12-31", fit_end="2015-12-31")
    test = usable_rows(data, ALL_FEATURES, 5, "2016-01-01", "2016-06-30")
    return data, train, test


def test_every_model_fits_and_predicts_one_value_per_row(dataset):
    data, train, test = dataset
    y = data["y_5"].to_numpy()[train]
    features = FEATURE_GROUPS["price"] + FEATURE_GROUPS["macro"]
    for model in (Naive(), Momentum(5), RidgeModel(features), LightGBMModel(features, n_estimators=20),
                  DLinearModel(FEATURE_GROUPS["price"]), ScaledReturn(RidgeModel(features), 5)):
        predictions = model.fit(data, train, y).predict(data, test)
        assert predictions.shape == (len(test),) and np.isfinite(predictions).all()


def test_classifiers_return_probabilities(dataset):
    data, train, test = dataset
    y = data["y_5"].to_numpy()[train]
    for model in (LogisticModel(FEATURE_GROUPS["price"]), LightGBMClassifier(FEATURE_GROUPS["price"], n_estimators=20)):
        prob = model.fit(data, train, y).predict(data, test)
        assert ((prob >= 0) & (prob <= 1)).all()


def test_scaled_return_restores_original_units(dataset):
    data, train, test = dataset
    y = data["y_5"].to_numpy()[train]
    scale = data["vol_60"].to_numpy()[test] * np.sqrt(5)
    inner = RidgeModel(FEATURE_GROUPS["price"]).fit(data, train, y / (data["vol_60"].to_numpy()[train] * np.sqrt(5)))
    wrapped = ScaledReturn(RidgeModel(FEATURE_GROUPS["price"]), 5).fit(data, train, y)
    assert np.allclose(wrapped.predict(data, test), inner.predict(data, test) * scale)


def test_har_scale_is_fitted_on_training_rows(dataset):
    data, train, test = dataset
    y = data["y_5"].to_numpy()[train]
    model = return_model("Ridge", FEATURE_GROUPS["price"], 5, scale="har").fit(data, train, y)
    target = data["v_5"].to_numpy()
    known = train[~np.isnan(target[train])]
    har = RidgeModel(HAR_FEATURES, alpha=1).fit(data, known, target[known])  # 학습 행의 앞으로 5일 변동성
    expected = model.model.predict(data, test) * np.exp(har.predict(data, test)) * np.sqrt(5)
    assert np.allclose(model.predict(data, test), expected)
    assert set(HAR_FEATURES) <= set(model.features)  # 파이프라인이 HAR 입력이 있는 행만 예측하도록


def test_shrunk_return_and_tuned_settings(dataset):
    data, train, test = dataset
    y = data["y_5"].to_numpy()[train]
    base = return_model("Ridge", FEATURE_GROUPS["price"], 5).fit(data, train, y)
    assert base.model.alpha == 100  # 인자가 없으면 03의 설정 그대로
    shrunk = ShrunkReturn(return_model("Ridge", FEATURE_GROUPS["price"], 5), 0.3).fit(data, train, y)
    assert np.allclose(shrunk.predict(data, test), 0.3 * base.predict(data, test))
    tuned = return_model("LightGBM", FEATURE_GROUPS["price"], 5, params={"num_leaves": 3})
    assert tuned.model.params["num_leaves"] == 3 and tuned.model.params["n_estimators"] == 150


def test_moving_average_keeps_length_and_trend_plus_rest_restores_window():
    windows = np.arange(60, dtype=float).reshape(1, 60, 1)
    trend = _moving_average(windows, 25)
    assert trend.shape == windows.shape
    assert np.allclose(trend[0, 12:48, 0], windows[0, 12:48, 0])  # 직선의 이동평균은 그 직선


def test_saved_bundle_predicts_the_same_and_rejects_changed_files(dataset, tmp_path):
    data, train, test = dataset
    model = RidgeModel(FEATURE_GROUPS["price"]).fit(data, train, data["y_5"].to_numpy()[train])
    save_bundle(tmp_path, {5: model}, {"version": "test", "horizons": {"5": {"signal_precision": float("nan")}}})
    loaded, metadata = load_bundle(tmp_path)
    assert metadata["version"] == "test"
    assert metadata["horizons"]["5"]["signal_precision"] is None  # NaN은 null로(DB jsonb·브라우저가 받는 JSON)
    assert "NaN" not in (tmp_path / "metadata.json").read_text(encoding="utf-8")
    assert np.array_equal(loaded[5].predict(data, test), model.predict(data, test))
    (tmp_path / "h5.joblib").write_bytes(b"changed")
    with pytest.raises(ValueError, match="해시"):
        load_bundle(tmp_path)


def test_shrunk_probability_moves_toward_training_up_rate(dataset):
    data, train, test = dataset
    y = data["y_5"].to_numpy()[train]
    inner = LogisticModel(FEATURE_GROUPS["price"]).fit(data, train, y).predict(data, test)
    for weight in (0.0, 0.25, 1.0):
        model = ShrunkProbability(LogisticModel(FEATURE_GROUPS["price"]), weight).fit(data, train, y)
        assert model.base_ == (y > 0).mean()
        assert np.allclose(model.predict(data, test), model.base_ + weight * (inner - model.base_))


def test_buy_signal_and_price_range():
    assert buy_signal([0.61, 0.5, 0.39, 0.6], 0.6).tolist() == ["buy", "hold", "wait", "buy"]
    assert buy_signal([0.9, 0.1], None).tolist() == ["hold", "hold"]  # 근거 있는 기준이 없으면 신호를 내지 않는다
    low, high = price_range(100.0, np.log(0.02), 20, 1.0)
    half = 1.2815515655446004 * 0.02 * np.sqrt(20)  # 80% 범위의 로그 반폭
    assert np.isclose(low, 100 * np.exp(-half)) and np.isclose(high, 100 * np.exp(half))
    low, high = price_range(100.0, np.log(0.02), 20, 1.0, center=0.05)  # 예측 가격 100·e^0.05를 가운데에 둔다
    assert np.isclose(np.sqrt(low * high), 100 * np.exp(0.05)) and np.isclose(high / low, np.exp(2 * half))


def test_distribution_model_learns_center_and_width_and_keeps_outputs_consistent():
    rng = np.random.default_rng(0)
    n = 3000
    data = pd.DataFrame({"signal": rng.normal(size=n), "noise": rng.normal(size=n),
                         **{name: rng.normal(size=n) for name in HAR_FEATURES}})
    sigma = 0.05 * np.exp(0.5 * data["log_vol_20"].to_numpy())  # 폭은 HAR 입력 하나가 정한다
    y = 0.02 * data["signal"].to_numpy() + sigma * rng.normal(size=n)
    fit, test = np.arange(2000), np.arange(2000, n)
    model = DistributionModel(["signal", "noise"], ["noise"], alpha_mean=1, alpha_scale=1).fit(data, fit, y[fit])

    mu, s = model.distribution(data, test)
    assert np.corrcoef(mu, data["signal"].to_numpy()[test])[0, 1] > 0.95  # 방향(중심)
    assert np.corrcoef(np.log(s), np.log(sigma[test]))[0, 1] > 0.9         # 변동폭
    low, mid, high = model.quantiles(data, test).T
    assert np.all(low <= mid) and np.all(mid <= high)
    assert 0.75 < np.mean((y[test] >= low) & (y[test] <= high)) < 0.85      # 80% 범위
    prob = model.prob_up(data, test)
    assert np.array_equal((prob > 0.5)[prob != 0.5], (mid > 0)[prob != 0.5])  # 확률과 가운데가 같은 분포에서 나온다
    assert np.allclose(model.predict(data, test), mid) and (model.crps(data, test, y[test]) > 0).all()

    flat = DistributionModel(["signal", "noise"], ["noise"], alpha_mean=1e9, alpha_scale=1e9).fit(data, fit, y[fit])
    flat_mu, flat_sigma = flat.distribution(data, test)
    assert np.ptp(flat_mu) < 1e-3 * np.ptp(mu) and np.corrcoef(np.log(flat_sigma), np.log(sigma[test]))[0, 1] > 0.9
    assert set(flat.coefficients()["scale"]) == {*HAR_FEATURES, "noise"}  # 벌점이 커도 HAR 폭은 남는다


def test_distribution_model_starts_from_current_price_survives_bad_inputs_and_recalibrates():
    rng = np.random.default_rng(1)
    n = 2000
    data = pd.DataFrame({"noise": rng.normal(size=n), **{name: rng.normal(size=n) for name in HAR_FEATURES}})
    y = 0.03 + 0.05 * rng.standard_t(4, size=n)  # 학습 구간에 평균 추세가 있어도
    fit = np.arange(1500)
    model = DistributionModel(["noise"], ["noise"], alpha_mean=1e9).fit(data, fit, y[fit])
    test = np.arange(1500, n)
    assert np.allclose(model.predict(data, test), 0, atol=1e-6)  # 가운데는 현재가에서 출발한다
    assert np.allclose(model.prob_up(data, test), 0.5, atol=0.01)  # 모양의 중앙값이 0이라 방향을 말하지 않는다

    spoiled = data.copy()
    spoiled.loc[test[0], "noise"] = 1e6  # 오류 시세 같은 극단 입력
    sigma = model.distribution(spoiled, test)[1]
    assert np.isfinite(sigma).all() and sigma[0] < 10 * np.median(sigma)  # 입력을 잘라 폭이 터지지 않는다

    narrow = model.quantiles(data, test)
    model.calibrate(2 * model.residuals(data, test, y[test]))  # 표본 밖 잔차가 두 배 넓으면
    assert np.isclose(np.median(model.shape_), 0)
    assert np.all(np.diff(model.quantiles(data, test)[:, [0, 2]]) > np.diff(narrow[:, [0, 2]]))  # 범위도 넓어진다
