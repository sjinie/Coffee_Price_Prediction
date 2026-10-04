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
        conn.execute("DROP TABLE IF EXISTS forecasts, models, prices, weather, news_articles, pipeline_runs CASCADE")
        conn.commit()
        db.migrate(conn)
        db.migrate(conn)  # 두 번 적용해도 같다
        yield conn


def forecast(origin, horizon, kind="backfill", version="v1", prob=0.6):
    ranged = horizon != 5  # 5일 행은 피처가 빠져 예측값이 결측인 경우(NaN → NULL)로 쓴다
    return {"model_version": version, "origin_date": origin, "horizon": horizon,
            "target_date": origin + timedelta(days=1), "origin_close": 300.0,
            "predicted_return": np.float64(0.01) if ranged else np.nan,
            "predicted_price": 303.0 if ranged else np.nan,
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


WEATHER_FIELDS = ("precip", "t_mean", "t_min", "t_max")


def weather_frame(dates, precip=1.0, mean=20.0, minimum=10.0, maximum=30.0):
    return pd.DataFrame({"date": pd.to_datetime(dates), "PRECTOTCORR": precip,
                         "T2M": mean, "T2M_MIN": minimum, "T2M_MAX": maximum})


def test_weather_upsert_replaces_all_values_and_preserves_region_and_nulls(conn):
    frame = weather_frame(["2026-01-01", "2026-01-02"])
    assert db.upsert_weather(conn, "br_sul_minas", frame) == 2
    assert db.upsert_weather(conn, "co_huila", frame.iloc[:1]) == 1
    revised = weather_frame(["2026-01-01", "2026-01-02"], precip=[2.5, np.nan],
                            mean=[np.nan, 22.0], minimum=[-1.0, np.nan], maximum=[31.0, np.nan])
    assert db.upsert_weather(conn, "br_sul_minas", revised) == 2  # 수정한 행도 합계에 포함
    rows = conn.execute("SELECT region, precip, t_mean, t_min, t_max FROM weather ORDER BY region, date").fetchall()
    assert rows == [
        {"region": "br_sul_minas", "precip": 2.5, "t_mean": None, "t_min": -1.0, "t_max": 31.0},
        {"region": "br_sul_minas", "precip": None, "t_mean": 22.0, "t_min": None, "t_max": None},
        {"region": "co_huila", "precip": 1.0, "t_mean": 20.0, "t_min": 10.0, "t_max": 30.0},
    ]
    assert db.upsert_weather(conn, "br_sul_minas", frame.iloc[:0]) == 0


@pytest.mark.parametrize("year, last_week_days", [(2023, 8), (2024, 9)])
def test_weather_weeks_start_january_first_and_keep_year_end_days(conn, year, last_week_days):
    db.upsert_weather(conn, "br_sul_minas", weather_frame(pd.date_range(f"{year}-01-01", f"{year}-12-31")))
    data = db.read_weather(conn, "br_sul_minas", date(2005, 1, 1))
    assert data["years"] == [year]
    assert data["days"] == [[7] * 51 + [last_week_days]]
    assert data["precip"] == [[7.0] * 51 + [float(last_week_days)]]
    # 1/7은 0주, 1/8은 1주, 12/31은 51주다(ISO 주가 아님).
    db.upsert_weather(conn, "br_sul_minas", weather_frame([f"{year}-01-07", f"{year}-01-08", f"{year}-12-31"],
                                                          precip=[2.0, 3.0, 4.0]))
    revised = db.read_weather(conn, "br_sul_minas", date(2005, 1, 1))
    assert [revised["precip"][0][w] for w in (0, 1, 51)] == [8.0, 9.0, last_week_days + 3.0]


def test_weather_aggregates_each_metric_skips_nulls_and_fills_missing_weeks(conn):
    # 연도가 섞여 들어와도 오름차순, 시작일 이전/다른 산지 값은 집계에서 제외한다.
    frame = weather_frame(["2026-01-01", "2004-12-31", "2005-01-01", "2005-01-07", "2005-01-08"],
                          precip=[np.nan, 999, 1.24, 2.24, np.nan], mean=[np.nan, 99, 20.24, 22.44, np.nan],
                          minimum=[np.nan, -99, 10.24, 8.26, np.nan], maximum=[np.nan, 99, 28.24, 31.26, np.nan])
    db.upsert_weather(conn, "br_sul_minas", frame)
    db.upsert_weather(conn, "co_huila", weather_frame(["2005-01-01"], precip=99))
    data = db.read_weather(conn, "br_sul_minas", date(2005, 1, 1))
    assert data["years"] == [2005, 2026]
    assert data["days"][0][:3] == [2, 1, 0]  # 전부 NULL인 관측 행도 days에는 포함
    assert data["days"][1][:2] == [1, 0]
    assert [data[key][0][0] for key in WEATHER_FIELDS] == [3.5, 21.3, 8.3, 31.3]
    for key in WEATHER_FIELDS:
        assert data[key][0][1:] == [None] * 51
        assert data[key][1] == [None] * 52
    assert data["days"][0][2:] == [0] * 50


def test_weather_dates_use_utc_before_grouping_years(conn):
    frame = weather_frame(pd.DatetimeIndex(["2026-01-01 00:30:00+09:00"]))
    db.upsert_weather(conn, "br_sul_minas", frame)
    conn.execute("SET TIME ZONE 'Pacific/Auckland'")
    data = db.read_weather(conn, "br_sul_minas", date(2005, 1, 1))
    assert data["years"] == [2025]
    assert data["days"][0][51] == 1


def test_weather_api_default_compact_shape_validation_start_and_empty_region(conn, monkeypatch):
    from fastapi.testclient import TestClient
    from coffee.api import app

    db.upsert_weather(conn, "br_sul_minas", weather_frame(["2004-12-31", "2005-01-01", "2026-01-08"]))
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", URL)
    client = TestClient(app)
    response = client.get("/api/weather")
    assert response.status_code == 200
    data = response.json()
    assert set(data) == {"region", "years", "days", *WEATHER_FIELDS}
    assert data["region"] == "br_sul_minas" and data["years"] == [2005, 2026]
    for key in ("days", *WEATHER_FIELDS):
        assert len(data[key]) == 2 and all(len(row) == 52 for row in data[key])
    assert data["days"][1][:3] == [0, 1, 0]
    assert data["precip"][1][:3] == [None, 1.0, None]  # 진행 중인 주도 원래 합계를 보낸다
    for region in ("br_cerrado", "br_alta_mogiana", "co_huila", "co_caldas", "co_antioquia"):
        response = client.get("/api/weather", params={"region": region})
        assert response.status_code == 200
        assert response.json() == {"region": region, "years": [], "days": [], **{key: [] for key in WEATHER_FIELDS}}
    for invalid in ("unknown", "", "BR_SUL_MINAS", "br_sul_minas' OR 1=1 --"):
        assert client.get("/api/weather", params={"region": invalid}).status_code == 422


def test_weather_command_only_updates_weather_and_records_count(conn, monkeypatch, tmp_path, sources):
    from coffee import pipeline
    from coffee.sources import load_sources

    for name, frame in sources.items():
        if name.startswith("weather_"):
            frame = weather_frame(["2004-12-31", "2005-01-01", "2026-10-02"])
        frame.to_parquet(tmp_path / f"{name}.parquet", index=False)
    db.upsert_prices(conn, sources["prices"].tail(1))
    conn.commit()
    prices_before = conn.execute("SELECT * FROM prices").fetchall()
    conn.commit()
    monkeypatch.setenv("DATABASE_URL", URL)
    monkeypatch.setattr(pipeline, "load_dotenv", lambda *args: None)
    monkeypatch.setattr(pipeline, "load_sources", lambda: load_sources(tmp_path))

    def forbidden(*args, **kwargs):
        pytest.fail("기상 단독 명령이 수집·모델·가격·뉴스 작업을 실행함")

    for name in ("update_all", "load_models", "build_dataset", "update_news", "load_jev_archive"):
        monkeypatch.setattr(pipeline, name, forbidden)
    monkeypatch.setattr(db, "upsert_prices", forbidden)
    assert pipeline.main(["weather"]) == 0
    assert conn.execute("SELECT * FROM prices").fetchall() == prices_before
    rows = conn.execute("SELECT region, min(date) AS first, count(*) AS n FROM weather GROUP BY region").fetchall()
    assert len(rows) == 6 and all(row["first"] == date(2005, 1, 1) and row["n"] == 2 for row in rows)
    run = conn.execute("SELECT command, status, steps FROM pipeline_runs").fetchone()
    assert run == {"command": "weather", "status": "success", "steps": [{"step": "weather", "upserted": 12}]}
    conn.commit()
    (tmp_path / "weather_co_huila.parquet").unlink()
    assert pipeline.main(["weather"]) == 1
    assert conn.execute("SELECT status FROM pipeline_runs ORDER BY run_id DESC LIMIT 1").fetchone()["status"] == "failed"


@pytest.mark.parametrize("command, expected_count, first", [
    ("daily", 3, date(2026, 9, 5)), ("backfill", 5, date(2005, 1, 1)),
])
def test_pipeline_weather_window_and_collection_warning(conn, monkeypatch, sources, command, expected_count, first):
    from coffee import pipeline

    frame = weather_frame(["2004-12-31", "2005-01-01", "2026-09-04", "2026-09-05", "2026-10-02", "2026-10-04"])
    for name in sources:
        if name.startswith("weather_"):
            sources[name] = frame
    # 외부 수집은 fixture로, 예측 계산은 기존 테스트에서 별도로 검증한다.
    monkeypatch.setattr(pipeline, "load_sources", lambda: sources)
    monkeypatch.setattr(pipeline, "update_all", lambda end: [{"source": "weather_co_huila", "status": "failed"}])
    monkeypatch.setattr(pipeline, "build_dataset", lambda src: pd.DataFrame({"close": [300.]}, index=pd.to_datetime(["2026-10-02"])))
    monkeypatch.setattr(pipeline, "load_models", lambda: {})
    monkeypatch.setattr(pipeline, "_activate", lambda *args: None)
    monkeypatch.setattr(pipeline, "_model_features", lambda models: [])
    monkeypatch.setattr(pipeline, "make_forecasts", lambda *args: [])
    monkeypatch.setattr(pipeline, "update_news", lambda *args: {"step": "news", "selected": 0})
    monkeypatch.setattr(pipeline, "load_jev_archive", lambda: pd.DataFrame())
    now = datetime(2026, 10, 4, 1, 0, tzinfo=timezone.utc)
    code, steps = getattr(pipeline, command)(conn, now)
    assert code == (pipeline.EXIT_WARNING if command == "daily" else pipeline.EXIT_OK)
    rows = conn.execute("SELECT region, min(date) AS first, count(*) AS n FROM weather GROUP BY region").fetchall()
    assert len(rows) == 6 and all(row["first"] == first and row["n"] == expected_count for row in rows)
    assert {"step": "weather", "upserted": expected_count * 6} in steps
    if command == "daily":
        assert steps[0]["failed"] == ["weather_co_huila"]  # 수집 실패해도 보관한 값을 적재
