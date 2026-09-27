"""Jev snapshot persistence/API checks on an explicitly selected test database."""

from contextlib import contextmanager
from datetime import date
import json
import os
from types import SimpleNamespace
from uuid import uuid4

import pandas as pd
import pytest

from coffee_service import db, jev, pipeline
from coffee_service.api import create_app


DATABASE_URL = os.getenv("COFFEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="COFFEE_TEST_DATABASE_URL is required")


@pytest.fixture
def isolated_database():
    schema = f"jev_{uuid4().hex}"
    with db.connect(DATABASE_URL) as connection:
        connection.execute(f'CREATE SCHEMA "{schema}"')
        connection.commit()
    url = f"{DATABASE_URL}?options=-csearch_path%3D{schema}"
    try:
        yield url
    finally:
        with db.connect(DATABASE_URL) as connection:
            connection.execute(f'DROP SCHEMA "{schema}" CASCADE')
            connection.commit()


def test_news_snapshot_does_not_replace_price_run_and_keeps_first_analysis(isolated_database, monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    now = pd.Timestamp.now(tz="UTC")
    origin = now.tz_localize(None).normalize() - pd.Timedelta(days=1)
    price_frame = pd.DataFrame({"close": [300.]}, index=pd.DatetimeIndex([origin]))
    predictions = pd.DataFrame([{"model_id": "fixture", "origin_date": origin.date(),
        "target_date": (origin + pd.Timedelta(days=h * 2)).date(), "horizon": h,
        "predicted_return": 0., "predicted_price": 300., "actual_price": None} for h in (5, 20, 60)])
    record = {"analysis_id": "analysis", "article_id": "article", "title": "Coffee supply falls",
        "url": "https://example.test/coffee", "event_at": (now - pd.Timedelta(hours=1)).isoformat(),
        "available_at": now.isoformat(), "collected_at": now.isoformat(), "analyzed_at": now.isoformat(),
        "model": jev.MODEL, "prompt_version": jev.PROMPT_VERSION,
        "p_bullish": .8, "p_bearish": .05, "p_neutral": .1, "p_uncertain": .05,
        "relevance": 1., "confidence": .7}
    status = {"source_status": "success", "classification_status": "success", "api_attempts": 1, "errors": [],
              "selected_count": 2, "selection_policy": "fixture"}
    monkeypatch.setattr(jev, "collect_and_classify", lambda *_args, **_kwargs: ([record], status))
    monkeypatch.setattr(pipeline, "sources_as_of", lambda *_args: {})
    monkeypatch.setattr(pipeline, "validate_macro_freshness", lambda *_args: None)
    monkeypatch.setattr(pipeline, "assemble_features", lambda *_args: SimpleNamespace(prices=price_frame))
    monkeypatch.setattr(pipeline, "load_bundle", lambda *_args: object())
    monkeypatch.setattr(pipeline, "generate_predictions", lambda *_args: predictions)

    with db.connect(isolated_database) as connection:
        db.create_schema(connection)
        price_run = db.start_pipeline_run(connection, "incremental")
        db.finish_pipeline_run(connection, price_run, "success", "price fixture")
        db.upsert_jev_analyses(connection, [record])

    @contextmanager
    def factory():
        with db.connect(isolated_database) as connection:
            yield connection

    client = TestClient(create_app(factory))
    assert client.get("/api/v1/news/jev").json()["articles"] == []
    first = pipeline.run_news_pipeline(tmp_path, tmp_path / "model", date.today(),
        cache_path=tmp_path / "news.json", database_url=isolated_database)
    assert first["status"] == "success"
    second = pipeline.run_news_pipeline(tmp_path, tmp_path / "model", date.today(),
        cache_path=tmp_path / "news.json", database_url=isolated_database)
    with db.connect(isolated_database) as connection:
        assert db.fetch_one(connection, "SELECT count(*) AS n FROM jev_analyses")["n"] == 1
        assert db.fetch_one(connection, "SELECT count(*) AS n FROM news_forecast_runs")["n"] == 2
        assert db.fetch_one(connection, "SELECT count(*) AS n FROM predictions")["n"] == 0

    assert client.get("/api/v1/pipeline/status").json()["run_id"] == price_run
    response = client.get("/api/v1/news/jev").json()
    assert response["latest_run"]["run_id"] == second["run_id"]
    assert response["articles"][0]["analysis_id"] == "analysis"
    assert response["selection"]["selected_count"] == 2
    assert response["selection"]["selected_analysis_ids"] == ["analysis"]
    assert len(response["forecast"]["forecasts"]) == 3
    assert all(row["adjusted_price"] is None for row in response["forecast"]["forecasts"])

    with db.connect(isolated_database) as connection:
        db.upsert_jev_analyses(connection, [{**record, "analysis_id": "future",
            "available_at": (now + pd.Timedelta(days=1)).isoformat()}])
    assert len(client.get("/api/v1/news/jev").json()["articles"]) == 1

    monkeypatch.setattr(jev, "read_selected_records", lambda *_args: [record])
    (tmp_path / "news.json.status.json").write_text(json.dumps({
        "source_status": "success", "classification_status": "partial", "api_attempts": 2,
        "source_count": 10, "errors": ["HTTP 429"]}))
    replay = pipeline.run_news_pipeline(tmp_path, tmp_path / "model", date.today(),
        cache_path=tmp_path / "news.json", database_url=isolated_database, skip_collection=True)
    assert replay["status"] == "partial"
    assert replay["source_status"]["api_attempts"] == 0
    assert replay["source_status"]["previous_api_attempts"] == 2
    assert replay["source_status"]["source_count"] == 10

    status.update(source_status="failed", classification_status="not_started", api_attempts=0, errors=["source failed"])
    failure = pipeline.run_news_pipeline(tmp_path, tmp_path / "model", date.today(),
        cache_path=tmp_path / "news.json", database_url=isolated_database)
    assert failure["status"] == "failed"
    assert client.get("/api/v1/news/jev").json()["latest_run"]["status"] == "failed"
    assert client.get("/api/v1/news/jev").json()["forecast"]["run_id"] == replay["run_id"]
