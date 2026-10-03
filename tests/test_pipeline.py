from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from coffee import db, jev, pipeline
from coffee.config import HORIZONS, VOL_HORIZONS
from coffee.evaluate import usable_rows
from coffee.features import FEATURE_GROUPS, build_dataset
from coffee.models import LightGBMClassifier, RidgeModel, ShrunkProbability
from coffee.pipeline import (EXIT_OK, EXIT_WARNING, RISK_WINDOW, _rolling_percentile, closed_prices, make_forecasts,
                             update_news)

HAR = ["log_vol_5", "log_vol_20", "log_vol_60"]


@pytest.fixture
def small_models(sources):
    data = build_dataset(sources)
    price = FEATURE_GROUPS["price"]
    direction, volatility = {}, {}
    for h in HORIZONS:
        fit = usable_rows(data, price, h, "2015-01-01", "2015-12-31", fit_end="2015-12-31")
        direction[h] = ShrunkProbability(LightGBMClassifier(price, n_estimators=10), 0.25).fit(
            data, fit, data[f"y_{h}"].to_numpy()[fit])
    for h in VOL_HORIZONS:
        fit = usable_rows(data, price, h, "2015-01-01", "2015-12-31", fit_end="2015-12-31", target=f"v_{h}")
        volatility[h] = RidgeModel(HAR, alpha=1).fit(data, fit, data[f"v_{h}"].to_numpy()[fit])
    models = {"version": "test", "direction": direction, "volatility": volatility,
              "direction_meta": {"train_end": "2015-12-31",
                                 "horizons": {str(h): {"features": price, "threshold": 0.52} for h in HORIZONS}},
              "volatility_meta": {"horizons": {str(h): {"features": HAR, "interval_multiplier": 1.0}
                                               for h in VOL_HORIZONS}}}
    return data, models


def test_forecast_rows_follow_the_serving_contract(small_models):
    data, models = small_models
    origins = np.arange(len(data) - 3, len(data))  # 마지막 기준일의 목표일은 자료 밖의 미래 거래일
    rows = pd.DataFrame(make_forecasts(data, origins, models, "live"))
    assert len(rows) == len(origins) * len(HORIZONS)
    assert rows["prob_up"].between(0, 1).all() and rows["signal"].isin(["buy", "wait", "hold"]).all()
    assert (rows["target_date"] > rows["origin_date"]).all()
    five, ranged = rows[rows["horizon"] == 5], rows[rows["horizon"] != 5]
    assert five[["price_low", "price_high", "predicted_vol", "vol_percentile"]].isna().all().all()
    assert ((ranged["price_low"] < ranged["origin_close"]) & (ranged["origin_close"] < ranged["price_high"])).all()
    assert ranged["vol_percentile"].between(0, 1).all()
    last = rows[(rows["horizon"] == 60) & (rows["origin_date"] == data.index[-1].date())]
    assert last["target_date"].iloc[0] > data.index[-1].date()


def test_rolling_percentile_ranks_against_recent_three_years():
    rising = _rolling_percentile(pd.Series(np.arange(1000, dtype=float)))
    falling = _rolling_percentile(pd.Series(np.arange(1000, 0, -1, dtype=float)))
    assert np.isnan(rising.iloc[RISK_WINDOW // 3 - 2]) and rising.iloc[-1] == 1.0
    assert falling.iloc[-1] == pytest.approx(1 / RISK_WINDOW)


def test_target_dates_match_the_dataset_targets(small_models):
    data, models = small_models
    origins = np.arange(len(data) - 150, len(data) - 70)  # 60거래일 뒤 목표일까지 자료 안에 있는 기준일
    for row in make_forecasts(data, origins, models, "backfill"):
        expected = data.loc[pd.Timestamp(row["origin_date"]), f"target_date_{row['horizon']}"]
        assert row["target_date"] == expected.date()


def test_percentile_is_missing_when_todays_value_is_missing():
    values = pd.Series(np.r_[np.arange(400, dtype=float), np.nan])
    assert np.isnan(_rolling_percentile(values).iloc[-1])


def test_closed_prices_drops_todays_unfinished_bar():
    prices = pd.DataFrame({"date": pd.to_datetime(["2026-10-01", "2026-10-02"]), "close": [1.0, 2.0]})
    intraday = datetime(2026, 10, 2, 15, 0, tzinfo=timezone.utc)
    after_close = datetime(2026, 10, 2, 23, 30, tzinfo=timezone.utc)
    assert closed_prices(prices, intraday)["close"].tolist() == [1.0]
    assert closed_prices(prices, after_close)["close"].tolist() == [1.0, 2.0]


class FakeConn:
    def __init__(self):
        self.commits = self.rollbacks = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def news_stubs(monkeypatch):
    now = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
    articles = [{"title": f"Coffee prices rise on frost {i}", "summary": "", "url": f"https://x/{i}",
                 "source": "google_news_rss", "event_at": pd.Timestamp("2026-09-28 15:00", tz="UTC") + pd.Timedelta(days=i // 2),
                 "content_hash": jev.content_hash(f"Coffee prices rise on frost {i}")} for i in range(10)]
    calls = {"classified": []}
    monkeypatch.setattr(jev, "fetch_google_news", lambda query, start, end: articles)
    monkeypatch.setattr(jev, "classify", lambda chosen: calls["classified"].append(chosen) or
                        [{**item, "cost": 0.001} for item in chosen])
    monkeypatch.setattr(db, "insert_news", lambda conn, rows: len(rows))
    monkeypatch.setitem(pipeline.SETTINGS["jev"], "max_articles_per_run", 3)
    return now, calls


def test_update_news_caps_articles_per_run(monkeypatch, news_stubs):
    now, calls = news_stubs
    monkeypatch.setattr(db, "news_state", lambda conn, since, per_day: (set(), set(), 0.0))
    step = update_news(FakeConn(), now)
    assert step["selected"] == 3 and step["classified"] == 3 and len(calls["classified"][0]) == 3


def test_update_news_stops_at_the_budget(monkeypatch, news_stubs):
    now, calls = news_stubs
    budget = pipeline.SETTINGS["jev"]["budget_usd"]
    monkeypatch.setattr(db, "news_state", lambda conn, since, per_day: (set(), set(), budget))
    step = update_news(FakeConn(), now)
    assert "warning" in step and calls["classified"] == []


@pytest.mark.parametrize("news_fails, expected", [(False, EXIT_OK), (True, EXIT_WARNING)])
def test_daily_stores_the_forecast_even_when_news_fails(monkeypatch, sources, small_models, news_fails, expected):
    _, models = small_models
    saved = []
    monkeypatch.setattr(pipeline, "update_all", lambda end: [{"source": "prices", "status": "ok"}])
    monkeypatch.setattr(pipeline, "load_sources", lambda: {name: frame.copy() for name, frame in sources.items()})
    monkeypatch.setattr(pipeline, "load_models", lambda: models)
    monkeypatch.setattr(db, "upsert_prices", lambda conn, prices: len(prices))
    monkeypatch.setattr(db, "activate_model", lambda *args: None)
    monkeypatch.setattr(db, "insert_forecasts", lambda conn, rows: saved.extend(rows) or len(rows))

    def news(conn, now):
        if news_fails:
            raise RuntimeError("뉴스 서버 장애")
        return {"step": "news", "selected": 0}
    monkeypatch.setattr(pipeline, "update_news", news)
    conn = FakeConn()
    code, steps = pipeline.daily(conn, datetime(2016, 12, 31, 1, 0, tzinfo=timezone.utc))  # 합성 자료 마지막 마감 뒤
    assert code == expected
    assert [row["horizon"] for row in saved] == list(HORIZONS) and {row["kind"] for row in saved} == {"live"}
    assert conn.rollbacks == int(news_fails)


def test_main_records_a_short_failure_message_and_exits_1(monkeypatch):
    finished = {}
    monkeypatch.setattr(pipeline, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(db, "connect", lambda: FakeConn())
    monkeypatch.setattr(db, "start_run", lambda conn, command: 1)
    monkeypatch.setattr(db, "finish_run", lambda conn, run_id, status, steps, message=None:
                        finished.update(status=status, message=message))

    def broken(conn):
        raise RuntimeError("첫 줄\n자세한 내부 정보")
    monkeypatch.setattr(pipeline, "daily", broken)
    assert pipeline.main(["daily"]) == 1
    assert finished == {"status": "failed", "message": "RuntimeError: 첫 줄"}
