import numpy as np
import pytest

from coffee.evaluate import usable_rows
from coffee.features import ALL_FEATURES, FEATURE_GROUPS, build_dataset
from coffee.models import (DLinearModel, LightGBMClassifier, LightGBMModel, LogisticModel, Momentum, Naive,
                           RidgeModel, ScaledReturn, ShrunkProbability, _moving_average, buy_signal, load_bundle,
                           price_range, save_bundle)


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


def test_moving_average_keeps_length_and_trend_plus_rest_restores_window():
    windows = np.arange(60, dtype=float).reshape(1, 60, 1)
    trend = _moving_average(windows, 25)
    assert trend.shape == windows.shape
    assert np.allclose(trend[0, 12:48, 0], windows[0, 12:48, 0])  # 직선의 이동평균은 그 직선


def test_saved_bundle_predicts_the_same_and_rejects_changed_files(dataset, tmp_path):
    data, train, test = dataset
    model = RidgeModel(FEATURE_GROUPS["price"]).fit(data, train, data["y_5"].to_numpy()[train])
    save_bundle(tmp_path, {5: model}, {"version": "test"})
    loaded, metadata = load_bundle(tmp_path)
    assert metadata["version"] == "test"
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
    low, high = price_range(100.0, np.log(0.02), 20, 1.0)
    half = 1.2815515655446004 * 0.02 * np.sqrt(20)  # 80% 범위의 로그 반폭
    assert np.isclose(low, 100 * np.exp(-half)) and np.isclose(high, 100 * np.exp(half))
