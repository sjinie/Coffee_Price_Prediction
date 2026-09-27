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


def test_inventory_deduplicates_content_and_exposes_unanalyzed_collection(isolated_database):
    from fastapi.testclient import TestClient

    old = "2026-01-01T10:00:00Z"
    newer = "2026-01-02T10:00:00Z"
    future = "2099-01-01T10:00:00Z"
    article = dict(content_hash="same", article_id="article", title="Coffee supply",
                   url="https://example.test/coffee", event_at=old, collected_at=old, available_at=old)
    analysis = {**article, "analysis_id": "analysis", "analyzed_at": old,
                "label": "bullish", "p_bullish": .8, "raw_response": "not public",
                "url": "https://example.test/analysis-variant", "collected_at": newer}
    with db.connect(isolated_database) as connection:
        db.create_schema(connection)
        db.upsert_jev_selections(connection, {"research": [article], "service": [article,
            {**article, "content_hash": "pending", "event_at": newer, "available_at": None},
            {**article, "content_hash": "future", "available_at": future}]})
        db.upsert_jev_analyses(connection, [analysis,
            {**analysis, "analysis_id": "new-analysis", "analyzed_at": newer, "p_bullish": .7},
            {**analysis, "analysis_id": "future-analysis", "content_hash": "pending", "available_at": future}])
        connection.commit()
        db.upsert_jev_selections(connection, {"service": [{**article, "title": "changed"}]})
        assert db.fetch_one(connection, "SELECT document FROM jev_selections WHERE content_hash='same'")["document"]["title"] == article["title"]

    @contextmanager
    def factory():
        with db.connect(isolated_database) as connection:
            yield connection

    client = TestClient(create_app(factory))
    response = client.get("/api/v1/news/jev?limit=1").json()
    view = response["inventory"]
    assert (view["total"], view["analyzed"], view["pending"], view["matched"]) == (2, 1, 1, 2)
    assert view["items"][0]["content_hash"] == "pending"
    assert view["items"][0]["analysis_status"] == "pending"
    assert view["items"][0]["p_bullish"] is None
    second = client.get("/api/v1/news/jev?limit=1&offset=1").json()["inventory"]
    assert second["items"][0]["analysis_id"] == "new-analysis"
    assert second["items"][0]["p_bullish"] == .7
    assert second["items"][0]["url"] == article["url"]
    assert second["items"][0]["collected_at"] == article["collected_at"]
    assert "raw_response" not in second["items"][0]
    filtered = client.get("/api/v1/news/jev?analysis_status=analyzed").json()["inventory"]
    assert filtered["matched"] == 1 and filtered["total"] == 2
    assert client.get("/api/v1/news/jev?offset=999").json()["inventory"]["items"] == []
    assert client.get("/api/v1/news/jev?analysis_status=invalid").status_code == 422
    assert client.get("/api/v1/news/jev?offset=-1").status_code == 422


def test_selected_pipeline_keeps_news_inventory_when_numeric_sources_fail(isolated_database, monkeypatch, tmp_path):
    from coffee_service import jev_store

    article = dict(content_hash="pending", article_id="article", title="Coffee harvest",
                   event_at="2026-01-01T10:00:00Z", collected_at="2026-01-02T10:00:00Z")
    monkeypatch.setattr(jev_store, "read_news", lambda *_: {"selections": {"service": [article]}})
    monkeypatch.setattr(pipeline, "existing_source_status", lambda *_: [])
    def failed_source(*_):
        raise ValueError("missing numeric source")
    monkeypatch.setattr(pipeline, "sources_as_of", failed_source)
    with pytest.raises(RuntimeError, match="ValueError"):
        pipeline.run_pipeline("incremental", tmp_path, tmp_path / "manifest.json", date(2022, 1, 1),
                              date(2026, 1, 2), skip_ingestion=True, database_url=isolated_database,
                              jev_cache=tmp_path / "responses.json")
    with db.connect(isolated_database) as connection:
        assert db.fetch_jev_inventory(connection, 10, 0, "all")["pending"] == 1
        assert db.fetch_one(connection, "SELECT status FROM pipeline_runs")["status"] == "failed"
        assert db.fetch_one(connection, "SELECT count(*) AS n FROM predictions")["n"] == 0
