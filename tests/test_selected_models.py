"""선택 구성·뉴스 시점·가격 평균 및 저장된 실험 결과와 운영 추론의 일치 검사."""
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from coffee_service.selected_models import DEFAULT_SELECTED, SELECTION, SelectedBundle, daily_news


def test_news_is_available_on_event_session_and_not_reintroduced_by_backfill():
    sessions = pd.to_datetime(["2026-09-21", "2026-09-22", "2026-09-23"])
    record = dict(analysis_id="a", event_at="2026-09-21T10:00:00Z", available_at="2026-09-21T12:00:00Z",
                  p_bullish=.02, p_bearish=.41, p_neutral=.24, p_uncertain=.33, confidence=.22, relevance=.52)
    result = daily_news([record], sessions, as_of="2026-09-23T23:00:00Z")
    assert result.iloc[0].news_sentiment == pytest.approx(np.tanh(-.22*.52))
    assert result.iloc[1:].news_sentiment.isna().all()
    delayed = {**record, "available_at": "2026-09-23T12:00:00Z"}
    delayed_result = daily_news([delayed], sessions, as_of="2026-09-23T23:00:00Z")
    assert delayed_result.iloc[:2].news_sentiment.isna().all()
    assert delayed_result.iloc[2].news_sentiment == pytest.approx(np.tanh(-.22*.52))
    archived = {**delayed, "event_at": "2022-01-01T10:00:00Z"}
    assert daily_news([archived], sessions, as_of="2026-09-23T23:00:00Z").news_sentiment.isna().all()
    assert daily_news([record], sessions, as_of="2026-09-21T11:00:00Z").news_sentiment.isna().all()
    neutral = {**record, "p_bearish": .24, "p_neutral": .41}
    assert daily_news([neutral], sessions).iloc[0].news_sentiment == 0


def test_price_average_and_exact_numeric_fallback():
    bundle = object.__new__(SelectedBundle)
    calls = []
    def component(horizon, name, mode, windows):
        calls.append((name, mode, len(windows)))
        price_ratio = (1.1 if name == "LightGBM" else .9) + (.2 if mode == "news" else 0)
        return np.full(len(windows), np.log(price_ratio))
    bundle.predict_component = component
    windows = np.zeros((2, 60, 28))
    windows[0, -1, -1] = -.1
    actual = bundle.predict(5, windows)
    np.testing.assert_allclose(np.exp(actual), [1.2, 1.0])
    assert [n for _, mode, n in calls if mode == "news"] == [1, 1]
    assert set(SELECTION) == {5, 20, 60}
    assert all(name != "Persistence" for members in SELECTION.values() for name, _ in members)


@pytest.mark.skipif(not DEFAULT_SELECTED.exists(), reason="local research artifacts are not in CI")
def test_exported_models_reproduce_research_predictions():
    from data_code.fixed_ensemble_news import load_market
    from data_code.news_expanding_benchmark import prepare_fold

    root = Path(__file__).resolve().parents[1]
    run = root / "data/processed/news_feature_ensemble/expanding_7f2729fd5ce23b69"
    bundle = SelectedBundle(DEFAULT_SELECTED)
    sources, sessions, _ = load_market(root / "data/processed/fixed_ensemble_news/sources_20260928")
    news = pd.read_parquet(root / "data/processed/news_feature_ensemble/ed374e9c9c0a19c6/daily_news.parquet")
    for h, members in SELECTION.items():
        fold = prepare_fold(sources, sessions, bundle.columns[:-1], news.set_index("date").news_sentiment,
                            h, "2025-12-31", "2026-09-25")
        # Reconstruct unscaled inputs from the exact research windows.
        meta = fold["metadata"]
        windows = fold["eval_x"].astype(float)*meta["input_scale"] + meta["input_mean"]
        expected = []
        for name, setting in members:
            saved = pd.read_parquet(run / f"models/evaluation_2026_{h}/{name}/predictions.parquet")
            saved = saved.loc[saved.setting.eq(setting)].sort_values("origin_date")
            assert list(saved.origin_date) == list(fold["reference"].origin_date)
            expected.append(saved.predicted_return.to_numpy())
        expected = np.logaddexp.reduce(np.stack(expected), axis=0)-np.log(len(members))
        np.testing.assert_allclose(bundle.predict(h, windows), expected, rtol=1e-6, atol=1e-7)
