import numpy as np
import pandas as pd
import pytest

from data_code.fixed_ensemble_news import adjust, align_models, average_prices, daily_news_feature, fit_strength, forecast, scores


def test_strength_starts_at_half_recovers_effect_and_can_learn_zero():
    origins = pd.bdate_range("2022-02-01", periods=80)
    x = np.linspace(-.05, .05, len(origins))
    prediction = np.linspace(-.1, .08, len(origins))
    frame = pd.DataFrame({"target_date": origins+pd.offsets.BDay(5), "news_x": x,
                          "current_price": np.linspace(120, 220, len(origins)),
                          "actual_return": prediction+.73*x}, index=origins)
    fit = fit_strength(frame, prediction, "2022-12-31")
    assert fit["initial_beta"] == fit["path"][0] == .5
    assert fit["beta"] == pytest.approx(.73, abs=1e-6)
    assert fit["loss_at_fit"] < fit["loss_at_initial"]
    assert fit["gradient_at_zero"] < 0  # A zero initial guess would not freeze this regression.
    corrected = adjust(prediction, x, fit["beta"])
    assert scores(frame, corrected)["가격 RMSE"] < 1e-5
    frame["actual_return"] = prediction-.73*x
    negative = fit_strength(frame, prediction, "2022-12-31")
    assert negative["beta"] == 0
    assert negative["loss_at_fit"] < negative["loss_at_initial"]
    frame["news_x"] = 0
    assert fit_strength(frame, prediction, "2022-12-31")["status"] == "no_identifiable_signal"
    frame.iloc[0, frame.columns.get_loc("target_date")] = pd.Timestamp("2024-01-01")
    with pytest.raises(ValueError, match="mature"):
        fit_strength(frame, prediction, "2023-12-31")
    with pytest.raises(ValueError, match="mature"):
        fit_strength(frame, prediction, "2024-12-31")


def test_equal_price_average_alignment_and_direction_preservation():
    np.testing.assert_allclose(np.exp(average_prices(np.log([[1., 3.], [2., 4.]]))), [2., 3.])
    prediction, x = np.array([.2, -.2, .1]), np.array([-.04, .03, 0.])
    adjusted = adjust(prediction, x, .5)
    assert adjusted[0] < prediction[0] and adjusted[1] > prediction[1] and adjusted[2] == prediction[2]
    with pytest.raises(ValueError):
        adjust(prediction, x, -.1)
    frame = pd.DataFrame({"model": ["NLinear", "DLinear"],
                          "origin_date": pd.to_datetime(["2022-01-03", "2022-01-04"]),
                          "target_date": pd.to_datetime(["2022-01-10", "2022-01-11"]),
                          "actual_return": [.1, .1], "predicted_return": [.05, .05],
                          "current_price": [100., 100.]})
    with pytest.raises(AssertionError):
        align_models(frame, ("NLinear", "DLinear"))


def test_daily_news_assigns_first_visible_close_without_carry_forward():
    sessions = pd.bdate_range("2024-01-05", periods=5)

    def article(name, event, direction, available=None):
        return {"article_id": name, "title": name, "event_at": event, "available_at": available or event,
                "p_bullish": float(direction == 1), "p_bearish": float(direction == -1),
                "p_neutral": float(direction == 0), "p_uncertain": 0., "relevance": 1., "confidence": 1.}

    early = article("early", "2024-01-05T20:00:00Z", 1)
    late = article("late", "2024-01-05T23:30:00Z", -1)
    delayed = article("delayed", "2024-01-05T19:00:00Z", 1, "2024-01-09T00:00:00Z")
    neutral = article("neutral", "2024-01-10T16:00:00Z", 0)
    records = [early, late, delayed, neutral, early.copy()]
    research = daily_news_feature(records, sessions)
    live = daily_news_feature(records, sessions, availability="live")
    assert research.news_article_count.tolist() == [2, 1, 0, 1, 0]
    assert live.news_article_count.tolist() == [1, 1, 1, 1, 0]
    assert research.news_sentiment.iloc[0] == pytest.approx(np.tanh(2))
    assert live.news_sentiment.iloc[0] == pytest.approx(np.tanh(1))
    assert research.news_sentiment.iloc[1] == pytest.approx(-np.tanh(1))
    assert live.news_sentiment.iloc[2] == pytest.approx(np.tanh(1))
    assert research.news_sentiment.iloc[3] == live.news_sentiment.iloc[3] == 0
    assert research.news_present.tolist() == [True, True, False, True, False]
    assert pd.isna(research.news_sentiment.iloc[2]) and pd.isna(live.news_sentiment.iloc[4])
    with pytest.raises(ValueError, match="sessions"):
        daily_news_feature([], sessions[::-1])
    with pytest.raises(ValueError, match="availability"):
        daily_news_feature([], sessions, availability="unknown")


def test_news_feature_uses_common_windows_and_training_only_scalers(monkeypatch, tmp_path):
    import xgboost
    from data_code import fixed_ensemble_news as module

    sessions = pd.bdate_range("2021-09-01", "2024-06-30")
    n = np.arange(len(sessions))
    columns = [f"price_{i}" for i in range(27)]
    features = pd.DataFrame({c: np.sin(n / (11 + i)) + i / 10 for i, c in enumerate(columns)}, index=sessions)
    close = pd.Series(100 * np.exp(n * .0002 + .02 * np.sin(n / 17)), index=sessions, name="close")
    cutoffs = []
    monkeypatch.setattr(module, "build_features", lambda sources, dates, cutoff: (cutoffs.append(cutoff) or features, []))
    monkeypatch.setattr(module, "transform_market", lambda sources, dates: (pd.DataFrame({"close": close}), None))

    fitted = []

    class SmallXGB:
        def __init__(self, **kwargs):
            pass

        def fit(self, x, y):
            fitted.append((x.copy(), y.copy()))

        def predict(self, x):
            return np.zeros(len(x), dtype=float)

        def save_model(self, path):
            path.write_text("model")

        def load_model(self, path):
            assert path.read_text() == "model"

    monkeypatch.setattr(xgboost, "XGBRegressor", SmallXGB)
    news = pd.Series(.2 * np.sin(n / 8), index=sessions)
    news.loc[:"2022-01-31"] = np.nan
    news.loc["2024-01-01"] = 0.
    news.loc["2024-02-01"] = np.nan

    def run(label, signal, use_news):
        result = forecast({}, sessions, columns, 5, "XGBoost", 2, tmp_path / label,
                          news_feature=signal, use_news=use_news, evaluation_end=pd.Timestamp("2024-05-31"))
        with np.load(tmp_path / f"{label}.npz") as saved:
            metadata = {key: saved[key].copy() for key in saved.files}
        return result, metadata, fitted[-1]

    price, price_meta, (price_x, price_y) = run("price", news.sample(frac=1, random_state=42), False)
    price.to_parquet(tmp_path / "predictions.parquet", index=False)
    pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "predictions.parquet"), price, check_exact=True)
    mixed, mixed_meta, (mixed_x, mixed_y) = run("mixed", news, True)
    pd.testing.assert_frame_equal(price[["origin_date", "target_date", "actual_return"]],
                                  mixed[["origin_date", "target_date", "actual_return"]])
    assert price.origin_date.min() == pd.Timestamp("2024-01-01")
    assert price.origin_date.eq("2024-02-01").any()
    assert mixed.loc[mixed.origin_date.eq("2024-01-01"), "news_present"].all()
    assert not mixed.loc[mixed.origin_date.eq("2024-02-01"), "news_present"].any()
    assert np.isfinite(mixed.predicted_return).all()
    assert price.target_date.max() <= pd.Timestamp("2024-05-31")
    assert price.feature_count.eq(27).all() and mixed.feature_count.eq(28).all()
    assert len(price_x.columns) == 27 * 60 and len(mixed_x.columns) == 28 * 60
    assert not any("news_sentiment" in c for c in price_x.columns)
    assert any("news_sentiment" in c for c in mixed_x.columns)
    assert len(price_x) == len(mixed_x) == int(price_meta["train_rows"]) == int(mixed_meta["train_rows"])
    assert price_meta["features"].tolist() == columns
    assert mixed_meta["features"].tolist() == [*columns, "news_sentiment"]
    np.testing.assert_array_equal(price_meta["input_mean"], mixed_meta["input_mean"][:-1])
    np.testing.assert_array_equal(price_meta["input_scale"], mixed_meta["input_scale"][:-1])
    assert mixed_meta["input_mean"][-1] == 0 and mixed_meta["input_scale"][-1] == 1
    assert not bool(price_meta["use_news"]) and bool(mixed_meta["use_news"])
    assert min(mixed_meta["train_origin_dates"]) >= "2022-01-01"
    assert max(mixed_meta["train_target_dates"]) <= "2023-12-31"
    np.testing.assert_array_equal(price_y, mixed_y)
    assert (mixed_x.filter(like="news_sentiment") == 0).any().any()
    assert all(c == pd.Timestamp("2023-12-31") for c in cutoffs)

    changed = news.copy()
    changed.loc["2024-01-01":] = -.8
    _, later_meta, (later_x, later_y) = run("later", changed, True)
    np.testing.assert_array_equal(mixed_meta["input_mean"], later_meta["input_mean"])
    np.testing.assert_array_equal(mixed_meta["input_scale"], later_meta["input_scale"])
    np.testing.assert_array_equal(mixed_x, later_x)
    np.testing.assert_array_equal(mixed_y, later_y)

    empty = pd.Series(np.nan, index=sessions)
    absent, absent_meta, (absent_x, absent_y) = run("absent", empty, True)
    pd.testing.assert_series_equal(price.origin_date, absent.origin_date)
    assert not absent.news_present.any() and np.isfinite(absent.predicted_return).all()
    assert (absent_x.filter(like="news_sentiment") == 0).all().all()
    np.testing.assert_array_equal(price_y, absent_y)
    assert int(absent_meta["train_rows"]) == int(price_meta["train_rows"])


@pytest.mark.parametrize("news", [
    pd.Series([.1, .2], index=pd.to_datetime(["2022-02-01", "2022-02-01"])),
    pd.Series([.1], index=["2022-02-01"]),
    pd.Series([np.inf], index=pd.to_datetime(["2022-02-01"])),
    pd.Series([1.01], index=pd.to_datetime(["2022-02-01"])),
    pd.Series([.1], index=pd.to_datetime(["2022-02-01 12:00"])),
    pd.Series(["0.1"], index=pd.to_datetime(["2022-02-01"])),
])
def test_news_feature_rejects_invalid_index_or_values(news, tmp_path):
    with pytest.raises(ValueError, match="news_feature"):
        forecast({}, pd.DatetimeIndex([]), [], 5, "XGBoost", 2, tmp_path / "invalid", news_feature=news)


def test_news_forecast_rejects_missing_signal_and_unsafe_dates(tmp_path):
    args = ({}, pd.DatetimeIndex([]), [], 5, "XGBoost", 2, tmp_path / "invalid")
    with pytest.raises(ValueError, match="requires news_feature"):
        forecast(*args, use_news=True)
    with pytest.raises(ValueError, match="Cutoff"):
        forecast(*args, cutoff=pd.Timestamp("2024-01-01"))
    with pytest.raises(ValueError, match="evaluation end"):
        forecast(*args, evaluation_end=pd.Timestamp("2023-12-31"))
