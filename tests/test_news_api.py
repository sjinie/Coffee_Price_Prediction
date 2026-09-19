"""뉴스 DB/API 계약. 전용 PostgreSQL DB가 있을 때만 실행한다."""

from contextlib import contextmanager
from datetime import date, datetime, timezone
import os
from uuid import uuid4

import pandas as pd
import pytest

from coffee_service import db
from coffee_service.api import create_app


DATABASE_URL = os.getenv("COFFEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="COFFEE_TEST_DATABASE_URL이 필요합니다.")


@pytest.fixture
def test_database_url():
    schema = f"coffee_news_{uuid4().hex}"
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


def test_news_schema_upgrades_old_predictions_and_keeps_article_truth(test_database_url):
    with db.connect(test_database_url) as connection:
        connection.execute("CREATE TABLE models (model_id TEXT PRIMARY KEY, name TEXT NOT NULL, horizons INTEGER[] NOT NULL, feature_columns JSONB NOT NULL, metrics JSONB NOT NULL, training_start DATE, training_end DATE, trained_at TIMESTAMPTZ, is_current BOOLEAN NOT NULL DEFAULT TRUE, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())")
        connection.execute("CREATE TABLE predictions (model_id TEXT NOT NULL REFERENCES models(model_id), origin_date DATE NOT NULL, target_date DATE NOT NULL, horizon INTEGER NOT NULL, predicted_return DOUBLE PRECISION NOT NULL, predicted_price DOUBLE PRECISION NOT NULL, actual_price DOUBLE PRECISION, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), PRIMARY KEY (model_id, origin_date, horizon))")
        connection.commit()
        db.create_schema(connection)
        columns = db.fetch_all(
            connection,
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'predictions'",
        )
        assert {"probability_up", "final_direction", "news_impact_score", "news_article_count", "news_updated_at", "signal_status", "classifier_version", "model_version"} <= {row["column_name"] for row in columns}

        first = articles("original", datetime(2024, 1, 3, tzinfo=timezone.utc))
        second = articles("changed", datetime(2024, 1, 4, tzinfo=timezone.utc))
        assert db.upsert_news_articles(connection, first) == 1
        assert db.upsert_news_articles(connection, second) == 1
        stored = db.fetch_one(connection, "SELECT title, collected_at FROM news_articles")
        assert stored == {"title": "original", "collected_at": first.iloc[0].collected_at.to_pydatetime()}


def test_news_features_prediction_signals_and_api_model_filter(test_database_url):
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from fastapi.testclient import TestClient

    with db.connect(test_database_url) as connection:
        db.create_schema(connection)
        records = [model("archived", [5]), model("current", [5], "historical")]
        db.upsert_intelligence_models(connection, records[:1])
        db.upsert_intelligence_models(connection, records[1:])
        assert db.fetch_one(connection, "SELECT is_current FROM models WHERE model_id = 'archived'")["is_current"] is False

        frame = pd.DataFrame({"news_count_7d": [2.0], "news_weighted_impact_7d": [0.4]}, index=pd.to_datetime(["2024-01-05"]))
        assert db.upsert_news_daily_features(connection, frame) == 1
        assert db.upsert_news_daily_features(connection, frame) == 1

        base = prediction("current")
        db.upsert_predictions(connection, pd.DataFrame([base]))
        signal = {**base, "probability_up": 0.7, "final_direction": "UP", "classifier_version": "c1", "model_version": "m1", "news_article_count": 2}
        db.upsert_predictions(connection, pd.DataFrame([signal]))
        db.upsert_predictions(connection, pd.DataFrame([{**signal, "probability_up": 0.2, "classifier_version": "changed", "model_version": "changed"}]))
        db.upsert_predictions(connection, pd.DataFrame([prediction("archived")]))
        connection.commit()
        stored = db.fetch_one(connection, "SELECT probability_up, classifier_version, model_version FROM predictions WHERE model_id = 'current'")
        assert stored == {"probability_up": 0.7, "classifier_version": "c1", "model_version": "m1"}

    @contextmanager
    def connection_factory():
        with db.connect(test_database_url) as connection:
            yield connection

    client = TestClient(create_app(connection_factory))
    summary = client.get("/api/v1/news/summary?window_days=7&as_of=2024-01-05")
    assert summary.status_code == 200
    assert summary.json()["metrics"] == {"count": 2.0, "weighted_impact": 0.4}
    assert client.get("/api/v1/news/summary?window_days=3").json() is None
    current = client.get("/api/v1/predictions").json()
    archived = client.get("/api/v1/predictions?model_id=archived").json()
    assert [row["model_id"] for row in current] == ["current"]
    assert [row["model_id"] for row in archived] == ["archived"]
    assert current[0]["news_availability"] == "historical"
    assert archived[0]["news_availability"] is None
    assert "title" not in summary.text


def test_legacy_activation_replaces_current_intelligence_models(test_database_url):
    with db.connect(test_database_url) as connection:
        db.create_schema(connection)
        db.upsert_intelligence_models(connection, [model("intelligence-h5", [5])])
        db.upsert_models(connection, legacy_bundle())
        assert db.fetch_all(connection, "SELECT model_id, horizons FROM models WHERE is_current ORDER BY model_id") == [
            {"model_id": "legacy-h60", "horizons": [60]},
            {"model_id": "persistence-h5-h20-v1", "horizons": [5, 20]},
        ]


def articles(title, collected_at):
    return pd.DataFrame([{
        "article_id": "article-1", "source": "fixture", "title": title, "summary": "summary",
        "published_at": pd.Timestamp("2024-01-01", tz="UTC"), "collected_at": pd.Timestamp(collected_at),
        "url": "https://example.test/article", "language": "en", "rights": "fixture attribution",
        "modified_at": pd.Timestamp("2024-01-01", tz="UTC"), "available_at": pd.Timestamp("2024-01-01", tz="UTC"),
        "coffee_relevance": 1.0, "topic": "supply", "price_impact": "bullish", "confidence": 0.8,
    }])


def model(model_id, horizons, news_availability=None):
    return {
        "model_id": model_id, "name": model_id, "horizons": horizons, "feature_columns": [],
        "metrics": ({"news_availability": news_availability} if news_availability else {}),
        "training_start": date(2023, 1, 1), "training_end": date(2023, 12, 31),
        "trained_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
    }


def prediction(model_id):
    return {
        "model_id": model_id, "origin_date": date(2024, 1, 1), "target_date": date(2024, 1, 8),
        "horizon": 5, "predicted_return": 0.01, "predicted_price": 101.0, "actual_price": None,
    }


def legacy_bundle():
    class Bundle:
        feature_columns = ["close"]
        metadata = {
            "model_id": "legacy-h60", "model_name": "Legacy", "horizon": 60,
            "notebook_metrics": {}, "training_start": date(2023, 1, 1),
            "training_end": date(2023, 12, 31), "trained_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
        }

    return Bundle()
