"""PostgreSQL 계약 테스트. COFFEE_TEST_DATABASE_URL이 있을 때만 실행한다(테이블을 지우고 다시 만든다)."""
import os
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from coffee import db

URL = os.getenv("COFFEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not URL, reason="COFFEE_TEST_DATABASE_URL이 없음")


@pytest.fixture
def conn():
    with db.connect(URL) as conn:
        conn.execute("DROP TABLE IF EXISTS forecasts, models, prices, news_articles, pipeline_runs CASCADE")
        conn.commit()
        db.migrate(conn)
        db.migrate(conn)  # 두 번 적용해도 같다
        yield conn


def forecast(origin, horizon, kind="backfill", version="v1", prob=0.6):
    ranged = horizon != 5
    return {"model_version": version, "origin_date": origin, "horizon": horizon,
            "target_date": origin + timedelta(days=1), "origin_close": 300.0,
            "prob_up": np.float64(prob), "signal": "buy", "price_low": 280.0 if ranged else np.nan,
            "price_high": 320.0 if ranged else np.nan, "predicted_vol": 0.3 if ranged else np.nan,
            "vol_percentile": 0.5 if ranged else np.nan, "kind": kind}


def news(title, when, cost=0.0):
    at = pd.Timestamp(when, tz="UTC")
    return {"content_hash": title, "url": f"https://x/{title}", "title": title, "source": "google_news_rss",
            "event_at": at, "analyzed_at": at, "available_at": at, "label": "bullish", "p_bullish": 0.7,
            "p_bearish": 0.1, "p_neutral": 0.1, "p_uncertain": 0.1, "relevance": 0.8, "confidence": 0.9,
            "model": "typesafe-ai/jev", "prompt_version": "arabica-kc-futures-v2", "cost_usd": cost}


def test_prices_upsert_replaces_values_and_stores_nan_as_null(conn):
    prices = pd.DataFrame({"date": pd.to_datetime(["2026-09-30", "2026-10-01"]), "open": [1.0, 2.0],
                           "high": [1.0, 2.0], "low": [1.0, 2.0], "close": [300.0, 301.0], "volume": [10, 20]})
    assert db.upsert_prices(conn, prices) == 2
    db.upsert_prices(conn, prices.assign(open=[np.nan, 2.0], close=[299.0, 301.0]))
    row = conn.execute("SELECT open, close FROM prices WHERE date = '2026-09-30'").fetchone()
    assert row == {"open": None, "close": 299.0}


def test_only_one_model_is_active_and_forecasts_are_never_overwritten(conn):
    db.activate_model(conn, "v1", "2025-12-31", {"note": "first"})
    db.activate_model(conn, "v2", "2025-12-31", {"note": "second"})
    assert [r["model_version"] for r in conn.execute("SELECT model_version FROM models WHERE is_active")] == ["v2"]
    db.activate_model(conn, "v1", "2025-12-31", {})
    assert db.read_active_model(conn)["model_version"] == "v1"

    rows = [forecast(date(2026, 9, 30), h) for h in (5, 20, 60)]
    assert db.insert_forecasts(conn, rows) == 3
    assert db.insert_forecasts(conn, [forecast(date(2026, 9, 30), 20, kind="live", prob=0.1)]) == 0  # backfill이 이미 있음
    stored = conn.execute("SELECT * FROM forecasts WHERE horizon = 5").fetchone()
    assert stored["price_low"] is None and stored["kind"] == "backfill"
    assert conn.execute("SELECT prob_up FROM forecasts WHERE horizon = 20").fetchone()["prob_up"] == 0.6


def test_news_state_reports_known_articles_covered_days_and_spending(conn):
    db.insert_news(conn, [news("a", "2026-09-29 15:00", 0.0003), news("b", "2026-09-29 16:00"),
                          news("c", "2026-09-30 15:00")])
    assert db.insert_news(conn, [news("a", "2026-09-29 15:00")]) == 0
    hashes, covered, spent = db.news_state(conn, date(2026, 9, 28), per_day=2)
    assert hashes == {"a", "b", "c"} and covered == {date(2026, 9, 29)} and spent == pytest.approx(0.0003)


def test_api_reads_active_model_rows(conn, monkeypatch):
    from fastapi.testclient import TestClient

    from coffee.api import app

    db.activate_model(conn, "v1", "2025-12-31", {"direction": {}})
    today = date.today()
    db.insert_forecasts(conn, [forecast(date(2026, 9, 29), h) for h in (5, 20, 60)]
                        + [forecast(date(2026, 9, 30), h) for h in (5, 20, 60)])
    prices = pd.DataFrame({"date": pd.to_datetime([today]), "open": [1.0], "high": [1.0], "low": [1.0],
                           "close": [300.0], "volume": [1]})
    db.upsert_prices(conn, prices)
    db.upsert_prices(conn, prices.assign(date=pd.to_datetime(["2026-09-30"]), close=[310.0]))
    db.insert_news(conn, [news("a", datetime.now(timezone.utc).replace(tzinfo=None))])
    db.finish_run(conn, db.start_run(conn, "daily"), "success", [{"step": "forecast"}])
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", URL)
    client = TestClient(app)

    assert client.get("/health").json() == {"status": "ok"}
    latest = client.get("/api/forecasts/latest").json()
    assert [row["horizon"] for row in latest] == [5, 20, 60] and {row["origin_date"] for row in latest} == {"2026-09-30"}
    history = client.get("/api/forecasts/history", params={"horizon": 20, "days": 3650}).json()
    assert [row["actual_close"] for row in history] == [310.0, None]  # 9/29 예측의 목표일 9/30은 실제 가격이 있다
    assert client.get("/api/forecasts/history", params={"horizon": 7}).status_code == 422
    assert client.get("/api/prices", params={"days": 5}).json()[-1]["close"] == 300.0
    assert client.get("/api/news").json()["daily"][0]["articles"] == 1
    assert client.get("/api/models").json()["model_version"] == "v1"
    assert client.get("/api/status").json()["runs"][0]["status"] == "success"
