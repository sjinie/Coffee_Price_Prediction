import math

import numpy as np
import pandas as pd
import pytest

from coffee_service.news_residual import (VERSION, fit_residual, load_residual,
                                          latest_snapshot_features, predict_residual, save_residual,
                                          signal_features)
from coffee_service.news_residual import _candidate_rows, _prices, _segments


def _record(article_id, event, bullish=1.0, available=None):
    return {"article_id": article_id, "title": article_id, "source": "fixture", "event_at": event,
            "available_at": available or event, "p_bullish": bullish, "p_bearish": 1 - bullish,
            "p_neutral": 0.0, "p_uncertain": 0.0, "relevance": 1.0, "confidence": 1.0}


def test_signal_decays_and_is_bounded():
    sessions = pd.bdate_range("2024-01-01", periods=4)
    values = signal_features([_record("a", "2024-01-01T00:00:00Z")], sessions, half_life=1)
    assert 0 < values.news_signal.iloc[-1] < values.news_signal.iloc[0] < 1
    assert values.news_lag_1.iloc[1] == values.news_signal.iloc[0]
    assert values.news_lag_3.iloc[3] == values.news_signal.iloc[0]


def test_live_cutoff_excludes_future_and_preserves_old_event_age():
    sessions = pd.bdate_range("2024-01-01", periods=4)
    late = _record("late", "2024-01-01T00:00:00Z", available="2024-01-04T00:00:00Z")
    future = _record("future", "2024-01-05T00:00:00Z")
    live = signal_features([late, future], sessions, availability="live", half_life=3)
    research = signal_features([late], sessions, availability="research", half_life=3)
    assert live.news_signal.iloc[:3].eq(0).all()
    # Delayed classification does not reset the article's event age on arrival.
    assert live.news_signal.iloc[-1] == pytest.approx(research.news_signal.iloc[-1])
    assert live.news_article_ids.iloc[-1] == ["late"]


def test_snapshot_can_use_after_close_news_without_rewriting_historical_origin():
    friday = pd.DatetimeIndex([pd.Timestamp("2024-01-05")])
    post_close = _record("after-close", "2024-01-06T06:00:00Z", available="2024-01-06T07:00:00Z")
    historical = signal_features([post_close], friday, availability="live")
    snapshot = latest_snapshot_features([post_close], friday, 3, "live", "2024-01-06T12:00:00Z")
    assert historical.news_signal.iloc[0] == 0
    assert snapshot.news_signal.iloc[0] > 0
    assert snapshot.news_lag_0_article_ids.iloc[0] == ["after-close"]


def test_daily_winner_is_unavailable_before_day_closes_and_updates_are_distinct():
    first = {**_record("first", "2024-01-01T18:00:00Z"), "title": "Brazil coffee output",
             "summary": "Output estimate is 50 million bags", "selection_available_at": "2024-01-02T05:00:00Z"}
    second = {**_record("second", "2024-01-02T18:00:00Z"), "title": "Brazil coffee output",
              "summary": "Output estimate is 55 million bags", "selection_available_at": "2024-01-03T05:00:00Z"}
    values = signal_features([first, second], pd.bdate_range("2024-01-01", periods=3), availability="research")
    assert values.news_signal.iloc[0] == 0
    assert values.news_article_ids.iloc[1] == ["first"]
    assert values.news_article_ids.iloc[2] == ["first", "second"]
    first["modified_at"] = "2024-01-03T12:00:00Z"
    revised = signal_features([first], pd.bdate_range("2024-01-01", periods=3), availability="research")
    assert revised.news_article_ids.iloc[1] == []
    assert revised.news_article_ids.iloc[2] == ["first"]


def test_news_input_boundaries_reject_future_sessions_and_impossible_availability():
    with pytest.raises(ValueError, match="sessions after"):
        signal_features([], pd.bdate_range("2024-01-01", periods=2), as_of="2024-01-01T23:00:00Z")
    bad = _record("bad", "2024-01-01T01:00:00Z", available="2024-01-01T02:00:00Z")
    bad["analyzed_at"] = "2024-01-01T03:00:00Z"
    with pytest.raises(ValueError, match="available_at"):
        signal_features([bad], pd.bdate_range("2024-01-01", periods=1))


def _synthetic():
    sessions = pd.bdate_range("2024-01-01", periods=180)
    close = pd.Series(100.0, index=sessions)
    records = [_record(f"a{i}", f"{day.date()}T08:00:00Z", bullish=float(i % 3 != 0)) for i, day in enumerate(sessions)]
    signals = signal_features(records, sessions, half_life=3, availability="research")
    rows = []
    for horizon in (5, 20, 60):
        for origin in sessions:
            target = origin + pd.offsets.BDay(horizon)
            if target > sessions[-1]:
                continue
            residual = .025 * signals.loc[origin, "news_lag_0"] * math.exp(-(horizon - 5) / 20)
            rows.append({"model_id": f"base-{horizon}", "origin_date": origin, "target_date": target,
                         "horizon": horizon, "predicted_return": 0.0, "predicted_price": 100.0,
                         "actual_price": 100.0 * math.exp(residual)})
    return pd.DataFrame(rows), close, records, sessions[-1]


def test_fit_save_load_and_predict_with_shared_horizon_decay(tmp_path):
    base, prices, records, as_of = _synthetic()
    bundle = fit_residual(base, prices, records, as_of=f"{as_of.date()}T23:00:00Z")
    assert bundle["status"] == "experimental"
    assert bundle["eligibility"]["5"]["status"] == "eligible"
    path = tmp_path / "residual.json"
    save_residual(bundle, path)
    restored = load_residual(path)
    latest = pd.DataFrame([{"model_id": f"base-{horizon}", "origin_date": as_of,
                            "target_date": as_of + pd.offsets.BDay(horizon), "horizon": horizon,
                            "predicted_return": 0.0, "predicted_price": 100.0, "actual_price": None}
                           for horizon in (5, 20, 60)])
    result = predict_residual(latest, prices, records, restored, as_of=f"{as_of.date()}T23:00:00Z")
    corrections = {row["horizon"]: row["news_correction"] for row in result}
    assert corrections[5] >= corrections[20] >= 0
    assert {row["horizon"] for row in result if row["status"] == "experimental"} == {5, 20}
    assert next(row for row in result if row["horizon"] == 60)["status"] == "insufficient_data"


def test_fit_purges_unmatured_and_returns_insufficient_data():
    sessions = pd.bdate_range("2024-01-01", periods=12)
    base = pd.DataFrame([{"model_id": "base", "origin_date": sessions[0], "target_date": sessions[-1] + pd.offsets.BDay(1),
                          "horizon": 5, "predicted_return": 0.0, "predicted_price": 100.0, "actual_price": 101.0}])
    bundle = fit_residual(base, pd.Series(100.0, index=sessions), [_record("a", "2024-01-01T08:00:00Z")],
                          as_of=f"{sessions[-1].date()}T23:00:00Z")
    assert bundle["status"] == "insufficient_data"


def test_missing_close_keeps_session_calendar_but_excludes_its_origin():
    base, prices, records, as_of = _synthetic()
    missing_day = prices.index[90]
    prices.loc[missing_day] = np.nan
    bundle = fit_residual(base, prices, records, as_of=f"{as_of.date()}T23:00:00Z")
    assert bundle["status"] == "experimental"
    assert bundle["eligibility"]["5"]["train_rows"] > 0


def test_missing_target_close_excludes_row_even_when_base_actual_exists():
    base, prices, records, as_of = _synthetic()
    row = base[base.horizon.eq(5)].iloc[[0]].copy()
    prices.loc[pd.Timestamp(row.target_date.iloc[0])] = np.nan
    signals = signal_features(records, prices.index, half_life=3, availability="research")
    rows = _candidate_rows(row, prices, signals, pd.Timestamp(f"{as_of.date()}T23:00:00Z"),
                           pd.Timestamp(records[0]["event_at"]), "research")
    assert rows.empty
    with pytest.raises(ValueError, match="observed prices"):
        _prices(pd.Series([np.nan], index=[pd.Timestamp("2024-01-01")]))


def test_maturity_requires_target_close_and_tune_labels_do_not_cross_holdout():
    base, prices, records, as_of = _synthetic()
    target_day = prices.index[40]
    one = pd.DataFrame([{"model_id": "base", "origin_date": prices.index[30], "target_date": target_day,
                         "horizon": 5, "predicted_return": 0.0, "predicted_price": 100.0, "actual_price": 101.0}])
    signals = signal_features(records, prices.index, half_life=3, availability="research")
    rows = _candidate_rows(one, prices, signals, pd.Timestamp(f"{target_day.date()}T22:59:00Z"),
                           pd.Timestamp(records[0]["event_at"]), "research")
    assert rows.empty
    full_rows = _candidate_rows(base, prices, signals, pd.Timestamp(f"{as_of.date()}T23:00:00Z"),
                                pd.Timestamp(records[0]["event_at"]), "research")
    _train, tune, _final_train, _holdout, _tune_start, holdout_start = _segments(full_rows)
    assert (tune.target_date < holdout_start).all()


def test_insufficient_bundle_returns_insufficient_before_usability_checks():
    sessions = pd.bdate_range("2024-01-01", periods=10)
    base = pd.DataFrame([{"model_id": "base", "origin_date": sessions[-1], "target_date": sessions[-1] + pd.offsets.BDay(5),
                          "horizon": 5, "predicted_return": 0.0, "predicted_price": 100.0, "actual_price": None}])
    bundle = {"version": VERSION, "model_version": "fixture", "availability": "live", "status": "insufficient_data",
              "training_cutoff": "2024-01-01T00:00:00+00:00", "half_life": None, "horizon_decay": None,
              "coefficients": None, "eligibility": {}, "metrics": {}}
    result = predict_residual(base, pd.Series(100.0, index=sessions), [], bundle, as_of=f"{sessions[-1].date()}T23:00:00Z")
    assert result[0]["status"] == "insufficient_data"


def test_invalid_future_origin_and_bundle_are_rejected():
    sessions = pd.bdate_range("2024-01-01", periods=10)
    base = pd.DataFrame([{"model_id": "base", "origin_date": sessions[-1], "target_date": sessions[-1] + pd.offsets.BDay(5),
                          "horizon": 5, "predicted_return": 0.0, "predicted_price": 100.0, "actual_price": None}])
    bundle = {"version": VERSION, "model_version": VERSION, "availability": "live", "status": "insufficient_data",
              "training_cutoff": "2024-01-01T00:00:00+00:00", "half_life": None, "horizon_decay": None,
              "coefficients": None, "eligibility": {}, "metrics": {}}
    with pytest.raises(ValueError, match="after as_of"):
        predict_residual(base, pd.Series(100.0, index=sessions), [], bundle, as_of="2024-01-01T23:00:00Z")
