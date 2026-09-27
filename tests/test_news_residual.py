import math
import copy

import numpy as np
import pandas as pd
import pytest

from coffee_service.news_residual import (
    VERSION, SIGNAL_DEFINITION, LEGACY_SIGNAL_DEFINITION, _bootstrap, _candidate_rows, _non_overlapping, _prices, _walk_evaluation, fit_residual, latest_snapshot_features,
    load_residual, predict_residual, save_residual, signal_features,
)


def _record(article_id, event, bullish=1.0, available=None):
    return {"article_id": article_id, "title": article_id, "source": "fixture", "event_at": event,
            "available_at": available or event, "p_bullish": bullish, "p_bearish": 1 - bullish,
            "p_neutral": 0., "p_uncertain": 0., "relevance": 1., "confidence": 1.}


def _synthetic():
    sessions = pd.bdate_range("2024-01-01", periods=280)
    records = [_record(f"n{i}", f"{day.date()}T08:00:00Z", bullish=1. if i % 4 in (1, 2) else 0.) for i, day in enumerate(sessions)]
    signals = signal_features(records, sessions, availability="research")
    # Daily prices have a real path. Forward returns therefore agree with close,
    # rather than being fabricated only in base.actual_price.
    rng = np.random.default_rng(7)
    daily = .0002 + .006 * signals.news_signal.to_numpy(float) + rng.normal(0, .001, len(sessions))
    close = pd.Series(100 * np.exp(np.cumsum(daily)), index=sessions)
    rows = []
    for horizon in (5, 20, 60):
        for position, origin in enumerate(sessions):
            target_position = position + horizon
            if target_position >= len(sessions):
                continue
            target = sessions[target_position]
            rows.append({"model_id": f"base-{horizon}", "origin_date": origin, "target_date": target,
                         "horizon": horizon, "predicted_return": 0., "predicted_price": float(close[origin]),
                         "actual_price": float(close[target])})
    return pd.DataFrame(rows), close, records, sessions[-1]


def _latest_base(close):
    origin = close.index[-1]
    return pd.DataFrame([{"model_id": f"base-{h}", "origin_date": origin,
                          "target_date": origin + pd.offsets.BDay(h), "horizon": h,
                          "predicted_return": 0., "predicted_price": float(close.iloc[-1]), "actual_price": None}
                         for h in (5, 20, 60)])


def test_signal_direction_and_live_future_boundary():
    sessions = pd.bdate_range("2024-01-01", periods=4)
    bullish = _record("bull", "2024-01-02T08:00:00Z", 1.)
    bearish = _record("bear", "2024-01-03T08:00:00Z", 0.)
    future = _record("future", "2024-01-04T08:00:00Z", 1., "2024-01-05T08:00:00Z")
    values = signal_features([bullish, bearish, future], sessions, availability="live")
    assert values.news_signal.iloc[1] > 0
    assert values.news_signal.iloc[2] < values.news_signal.iloc[1]
    assert "future" not in values.news_article_ids.iloc[-1]


def test_signal_decays_and_is_bounded():
    sessions = pd.bdate_range("2024-01-01", periods=4)
    values = signal_features([_record("a", "2024-01-01T00:00:00Z")], sessions, half_life=1)
    assert 0 < values.news_signal.iloc[-1] < values.news_signal.iloc[0] < 1
    assert values.news_lag_1.iloc[1] == values.news_signal.iloc[0]
    assert values.news_lag_3.iloc[3] == values.news_signal.iloc[0]


def test_live_cutoff_preserves_event_age_and_daily_selection_availability():
    sessions = pd.bdate_range("2024-01-01", periods=4)
    late = _record("late", "2024-01-01T00:00:00Z", available="2024-01-04T00:00:00Z")
    live = signal_features([late], sessions, availability="live")
    research = signal_features([late], sessions, availability="research")
    assert live.news_signal.iloc[:3].eq(0).all()
    assert live.news_signal.iloc[-1] == pytest.approx(research.news_signal.iloc[-1])
    first = {**_record("first", "2024-01-01T18:00:00Z"), "selection_available_at": "2024-01-02T05:00:00Z"}
    first["modified_at"] = "2024-01-03T12:00:00Z"
    revised = signal_features([first], pd.bdate_range("2024-01-01", periods=3), availability="research")
    assert revised.news_article_ids.iloc[1] == []
    assert revised.news_article_ids.iloc[2] == ["first"]


def test_news_boundaries_and_target_maturity_reject_unavailable_labels():
    with pytest.raises(ValueError, match="sessions after"):
        signal_features([], pd.bdate_range("2024-01-01", periods=2), as_of="2024-01-01T23:00:00Z")
    bad = _record("bad", "2024-01-01T01:00:00Z", available="2024-01-01T02:00:00Z")
    bad["analyzed_at"] = "2024-01-01T03:00:00Z"
    with pytest.raises(ValueError, match="available_at"):
        signal_features([bad], pd.bdate_range("2024-01-01", periods=1))
    base, prices, records, _ = _synthetic()
    row = base[base.horizon.eq(5)].iloc[[0]].copy()
    prices.loc[pd.Timestamp(row.target_date.iloc[0])] = np.nan
    signals = signal_features(records, prices.index, availability="research")
    rows = _candidate_rows(row, prices, signals, pd.Timestamp("2025-12-31T23:00:00Z"), pd.Timestamp(records[0]["event_at"]), "research")
    assert rows.empty
    with pytest.raises(ValueError, match="observed prices"):
        _prices(pd.Series([np.nan], index=[pd.Timestamp("2024-01-01")]))


def test_argmax_direction_uses_confidence_and_zeros_other_winners():
    session = pd.bdate_range("2024-01-02", periods=1)
    def article(name, probabilities, confidence):
        row = _record(name, "2024-01-02T08:00:00Z", probabilities[0])
        row.update(dict(zip(("p_bullish", "p_bearish", "p_neutral", "p_uncertain"), probabilities)))
        row["confidence"] = confidence
        return row
    strong_bull = article("strong-bull", [.90, .05, .03, .02], .9)
    weak_bull = article("weak-bull", [.45, .40, .10, .05], .5)
    strong_bear = article("strong-bear", [.05, .90, .03, .02], .9)
    neutral = article("neutral", [.25, .25, .45, .05], .95)
    uncertain = article("uncertain", [.25, .25, .05, .45], .95)
    same_probs_low_confidence = article("same-low", [.90, .05, .03, .02], .1)
    same_probs_high_confidence = article("same-high", [.90, .05, .03, .02], .9)
    signal = lambda row: signal_features([row], session, availability="research").news_signal.iloc[0]
    assert signal(strong_bull) > signal(weak_bull) > 0
    assert signal(strong_bear) < 0
    assert signal(neutral) == signal(uncertain) == 0
    assert math.atanh(signal(same_probs_high_confidence)) == pytest.approx(
        9 * math.atanh(signal(same_probs_low_confidence)))


def test_signal_definition_argmax_ties_and_legacy_invariance():
    session = pd.DatetimeIndex([pd.Timestamp("2024-01-02")])
    event = "2024-01-02T08:00:00Z"
    row = {**_record("bear", event), "p_bullish": .02, "p_bearish": .41,
           "p_neutral": .24, "p_uncertain": .33, "confidence": .22, "relevance": .52}
    snapshot = lambda article, definition=SIGNAL_DEFINITION: latest_snapshot_features(
        [article], session, 3, "research", event, signal_definition=definition)
    assert math.atanh(snapshot(row).news_signal.iloc[0]) == pytest.approx(-.1144)
    assert math.atanh(snapshot(row, LEGACY_SIGNAL_DEFINITION).news_signal.iloc[0]) == pytest.approx(
        (.02 - .41) * .52)
    historical = signal_features([row], session, availability="research",
                                 signal_definition=LEGACY_SIGNAL_DEFINITION)
    assert math.atanh(historical.news_signal.iloc[0]) == pytest.approx(
        (.02 - .41) * .52 * 2 ** (-(15 / 24) / 3))
    assert snapshot({**row, "confidence": .9}, LEGACY_SIGNAL_DEFINITION).news_signal.iloc[0] == pytest.approx(
        snapshot(row, LEGACY_SIGNAL_DEFINITION).news_signal.iloc[0])
    for probabilities in ((.40, .10, .45, .05), (.40, .10, .05, .45),
                          (.40, .40, .10, .10), (.40, .400000005, .10, .099999995)):
        candidate = dict(zip(("p_bullish", "p_bearish", "p_neutral", "p_uncertain"), probabilities))
        assert snapshot({**row, **candidate}).news_signal.iloc[0] == 0
    with pytest.raises(ValueError, match="signal definition"):
        signal_features([row], session, signal_definition="unknown")
    for invalid in ({"confidence": 1.1}, {"p_bullish": 1.01}, {"p_bullish": float("nan")}):
        with pytest.raises(ValueError, match="probabilities"):
            signal_features([{**row, **invalid}], session)
    for key in ("relevance", "confidence"):
        missing = {k: v for k, v in row.items() if k != key}
        with pytest.raises(ValueError, match="relevance or confidence"):
            signal_features([missing], session)
        assert np.isfinite(snapshot(missing, LEGACY_SIGNAL_DEFINITION).news_signal).all()


def test_latest_snapshot_allows_known_after_close_without_historical_rewrite():
    friday = pd.DatetimeIndex([pd.Timestamp("2024-01-05")])
    article = _record("after", "2024-01-06T06:00:00Z", 1., "2024-01-06T07:00:00Z")
    historical = signal_features([article], friday, availability="live")
    snapshot = latest_snapshot_features([article], friday, 3, "live", "2024-01-06T12:00:00Z")
    assert historical.news_signal.iloc[0] == 0
    assert snapshot.news_lag_0.iloc[0] > 0


def test_non_overlapping_rows_purge_shared_forward_labels():
    days = pd.bdate_range("2024-01-01", periods=10)
    rows = pd.DataFrame({"origin_date": days[:5], "target_date": days[[3, 4, 5, 6, 7]], "horizon": 5})
    selected = _non_overlapping(rows)
    assert selected.origin_date.tolist() == [days[0], days[3]]
    assert all(selected.origin_date.iloc[index] >= selected.target_date.iloc[index - 1] for index in range(1, len(selected)))


def test_walk_forward_evaluates_daily_origins_with_purged_training_only():
    base, close, records, as_of = _synthetic()
    signals = signal_features(records, close.index, availability="research")
    rows = _candidate_rows(base, close, signals, pd.Timestamp(f"{as_of.date()}T23:00:00Z"), pd.Timestamp(records[0]["event_at"]), "research")
    horizon_rows = rows[rows.horizon.eq(5)]
    evaluation, predictions = _walk_evaluation(horizon_rows, 5)
    assert evaluation["n"] == len(horizon_rows) - int(len(horizon_rows) * .6)
    assert evaluation["baseline"]["n"] == evaluation["adjusted"]["n"] == evaluation["n"]
    assert all(training_max_target < row.origin_date for row, _correction, training_max_target in predictions)


def test_bounded_direction_preserving_fit_roundtrip_and_prediction(tmp_path):
    base, close, records, as_of = _synthetic()
    bundle = fit_residual(base, close, records, as_of=f"{as_of.date()}T23:00:00Z")
    assert bundle["version"] == VERSION
    assert bundle["signal_definition"] == SIGNAL_DEFINITION
    assert bundle["status"] == "experimental"
    model = bundle["horizon_models"]["5"]
    assert model["status"] == "experimental"
    assert 0 <= model["weight"] <= 1
    assert 0 <= model["null_probability"] <= 1
    assert model["prior"] == {"null_mass": .5, "slab": "uniform_0_1"}
    assert model["evaluation"]["n"] >= 5
    path = tmp_path / "residual.json"
    save_residual(bundle, path)
    restored = load_residual(path)
    bullish = [_record("now-bull", f"{as_of.date()}T08:00:00Z", 1.)]
    bearish = [_record("now-bear", f"{as_of.date()}T08:00:00Z", 0.)]
    forecast = _latest_base(close).query("horizon == 5")
    up = predict_residual(forecast, close, bullish, restored, as_of=f"{as_of.date()}T23:00:00Z", availability="research")[0]
    down = predict_residual(forecast, close, bearish, restored, as_of=f"{as_of.date()}T23:00:00Z", availability="research")[0]
    assert up["news_correction"] >= 0
    assert down["news_correction"] <= 0
    assert up["news_effect_interval"] is not None
    assert up["availability_mode"] == "research_known_at_issue"

    # A saved v2 bundle must take the historical snapshot path, including confidence handling.
    old = copy.deepcopy(bundle)
    old["version"] = "news-residual-v2"
    old["model_version"] = "news-residual-v2-fixture"
    old["signal_definition"] = LEGACY_SIGNAL_DEFINITION
    old_path = tmp_path / "residual-v2.json"
    save_residual(old, old_path)
    old = load_residual(old_path)
    mixed = {**_record("mixed", f"{as_of.date()}T08:00:00Z"),
             "p_bullish": .02, "p_bearish": .41, "p_neutral": .24,
             "p_uncertain": .33, "confidence": .22, "relevance": .52}
    issued = f"{as_of.date()}T23:00:00Z"
    old_result = predict_residual(forecast, close, [mixed], old, as_of=issued, availability="research")[0]
    new_result = predict_residual(forecast, close, [mixed], restored, as_of=issued, availability="research")[0]
    old_signal = latest_snapshot_features([mixed], close.index, 3, "research", issued,
                                          signal_definition=LEGACY_SIGNAL_DEFINITION)
    new_signal = latest_snapshot_features([mixed], close.index, 3, "research", issued)
    assert old_result["news_signal"] == pytest.approx(
        float(old_signal.iloc[-1][["news_lag_0", "news_lag_1", "news_lag_3", "news_lag_5"]] @ np.asarray(old["lag_weights"])))
    assert new_result["news_signal"] == pytest.approx(
        float(new_signal.iloc[-1][["news_lag_0", "news_lag_1", "news_lag_3", "news_lag_5"]] @ np.asarray(bundle["lag_weights"])))
    assert old_result["news_signal"] != pytest.approx(new_result["news_signal"])
    for version, definition in ((VERSION, LEGACY_SIGNAL_DEFINITION),
                                ("news-residual-v2", SIGNAL_DEFINITION), (VERSION, "unknown")):
        mismatched = {**old, "version": version, "signal_definition": definition}
        with pytest.raises(ValueError, match="unsupported"):
            predict_residual(forecast, close, [mixed], mismatched, as_of=issued, availability="research")


def test_research_snapshot_blocks_article_not_known_at_issuance():
    base, close, _records, as_of = _synthetic()
    bundle = fit_residual(base, close, _records, as_of=f"{as_of.date()}T23:00:00Z")
    late = _record("late", f"{as_of.date()}T08:00:00Z", 1., f"{(as_of + pd.Timedelta(days=1)).date()}T08:00:00Z")
    result = predict_residual(_latest_base(close).query("horizon == 5"), close, [late], bundle,
                              as_of=f"{as_of.date()}T23:00:00Z", availability="research")[0]
    assert result["status"] == "no_news"
    assert result["news_correction"] == 0


def test_flat_or_short_price_history_does_not_impute_volatility():
    sessions = pd.bdate_range("2024-01-01", periods=25)
    base = pd.DataFrame([{"model_id": "base", "origin_date": sessions[-1], "target_date": sessions[-1] + pd.offsets.BDay(5),
                          "horizon": 5, "predicted_return": 0., "predicted_price": 100., "actual_price": None}])
    raw_lag_weights = np.asarray([1, 2 ** (-1 / 3), .5, 2 ** (-5 / 3)])
    artifact = {"version": VERSION, "model_version": "fixture", "availability": "research", "status": "experimental",
                "training_cutoff": "2024-02-01T00:00:00Z", "half_life": 3, "lags": [0, 1, 3, 5],
                "lag_weights": (raw_lag_weights / raw_lag_weights.sum()).tolist(),
                "signal_definition": SIGNAL_DEFINITION,
                "horizon_decay": {"tau": 10}, "eligibility": {},
                "horizon_models": {str(h): {"status": "experimental", "weight": .2, "weight_interval": [.1, .3], "null_probability": .5, "prior": {"null_mass": .5, "slab": "uniform_0_1"}, "evidence_status": "not_demonstrated"} for h in (5, 20, 60)}, "metrics": {}}
    result = predict_residual(base, pd.Series(100., index=sessions), [_record("a", "2024-01-01T08:00:00Z")], artifact,
                              as_of=f"{sessions[-1].date()}T23:00:00Z", availability="research")[0]
    assert result["status"] == "unavailable"
    assert result["volatility_scale"] is None
    assert result["as_of"] == f"{sessions[-1].date()}T23:00:00+00:00"


def test_bootstrap_is_blocked_and_reproducible():
    small = _bootstrap(np.ones(10), 5)
    assert small["ci95"] is None
    large_one = _bootstrap(np.linspace(-1, 1, 30), 5)
    large_two = _bootstrap(np.linspace(-1, 1, 30), 5)
    assert large_one == large_two
    assert large_one["resamples"] == 1000


def test_invalid_or_v1_artifact_is_rejected(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('{"version":"news-residual-v1"}', encoding="utf-8")
    with pytest.raises(ValueError, match="unsupported"):
        load_residual(path)


def test_artifact_rejects_boolean_weight_and_invalid_interval(tmp_path):
    base, close, records, as_of = _synthetic()
    artifact = fit_residual(base, close, records, as_of=f"{as_of.date()}T23:00:00Z")
    bad_weight = copy.deepcopy(artifact)
    bad_weight["horizon_models"]["5"]["weight"] = True
    with pytest.raises(ValueError, match="incomplete"):
        save_residual(bad_weight, tmp_path / "bad-weight.json")
    bad_interval = copy.deepcopy(artifact)
    bad_interval["horizon_models"]["5"]["weight_interval"] = [.8, .2]
    with pytest.raises(ValueError, match="incomplete"):
        save_residual(bad_interval, tmp_path / "bad-interval.json")
