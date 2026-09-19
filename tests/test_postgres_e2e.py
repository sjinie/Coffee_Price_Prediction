"""PostgreSQL E2E. COFFEE_TEST_DATABASE_URL의 전용 DB에서만 실행한다."""

from contextlib import contextmanager
from datetime import date, timedelta
import os
from pathlib import Path
from uuid import uuid4

import pandas as pd
import pytest

from coffee_service import db
from coffee_service.api import create_app
from coffee_service.pipeline import run_pipeline


DATABASE_URL = os.getenv("COFFEE_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="COFFEE_TEST_DATABASE_URL이 필요합니다.")
ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def test_database_url():
    schema = f"coffee_e2e_{uuid4().hex}"
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


def test_pipeline_is_idempotent_serves_api_and_records_rollback(test_database_url, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from fastapi.testclient import TestClient

    source_dir = ROOT / "data" / "processed" / "2014-07-01_2025-12-31"
    artifact = ROOT / "model_artifacts" / "production_dlinear_60.pt"
    first_end, final_end = date(2025, 12, 15), date(2025, 12, 31)
    result = run_pipeline("backfill", source_dir, artifact, date(2014, 7, 1), first_end, skip_ingestion=True, database_url=test_database_url)
    assert result["status"] == "success"

    with db.connect(test_database_url) as connection:
        price_count = db.fetch_one(connection, "SELECT count(*) AS count FROM prices")["count"]
        prediction_count = db.fetch_one(connection, "SELECT count(*) AS count FROM predictions")["count"]
        assert price_count == result["price_rows"]
        assert prediction_count == result["prediction_rows"]
        original_rows = db.fetch_all(
            connection,
            "SELECT model_id, origin_date, horizon, target_date, predicted_return, predicted_price, created_at FROM predictions",
        )
        pending = db.fetch_all(
            connection,
            "SELECT model_id, origin_date, horizon FROM predictions WHERE horizon = 5 AND actual_price IS NULL AND target_date <= %s",
            (final_end,),
        )
        assert pending

    incremental = run_pipeline("incremental", source_dir, artifact, date(2014, 7, 1), final_end, skip_ingestion=True, database_url=test_database_url)
    repeated = run_pipeline("incremental", source_dir, artifact, date(2014, 7, 1), final_end, skip_ingestion=True, database_url=test_database_url)
    assert incremental["status"] == repeated["status"] == "success"

    with db.connect(test_database_url) as connection:
        assert db.fetch_one(connection, "SELECT count(*) AS count FROM prices")["count"] > price_count
        assert db.fetch_one(connection, "SELECT count(*) AS count FROM predictions")["count"] > prediction_count
        assert db.fetch_one(connection, "SELECT count(*) AS count FROM prices GROUP BY date HAVING count(*) > 1") is None
        assert db.fetch_one(connection, "SELECT count(*) AS count FROM predictions GROUP BY model_id, origin_date, horizon HAVING count(*) > 1") is None
        for original_row in original_rows:
            stored = db.fetch_one(
                connection,
                "SELECT target_date, predicted_return, predicted_price, created_at FROM predictions WHERE model_id = %s AND origin_date = %s AND horizon = %s",
                (original_row["model_id"], original_row["origin_date"], original_row["horizon"]),
            )
            assert stored == {
                "target_date": original_row["target_date"],
                "predicted_return": original_row["predicted_return"],
                "predicted_price": original_row["predicted_price"],
                "created_at": original_row["created_at"],
            }
        for pending_row in pending:
            actual = db.fetch_one(
                connection,
                """
                SELECT predictions.actual_price, prices.close
                FROM predictions JOIN prices ON predictions.target_date = prices.date
                WHERE predictions.model_id = %s AND predictions.origin_date = %s AND predictions.horizon = %s
                """,
                (pending_row["model_id"], pending_row["origin_date"], pending_row["horizon"]),
            )
            assert actual["actual_price"] == actual["close"]
        original = db.fetch_one(
            connection,
            "SELECT model_id, origin_date, target_date, horizon, predicted_return, predicted_price, actual_price, created_at FROM predictions ORDER BY origin_date, horizon LIMIT 1",
        )
        revised = {
            **original,
            "target_date": original["target_date"] + timedelta(days=1),
            "predicted_return": original["predicted_return"] + 1.0,
            "predicted_price": original["predicted_price"] + 1.0,
            "actual_price": 123.45,
        }
        db.upsert_predictions(connection, pd.DataFrame([revised]))
        connection.commit()
        mismatch = db.fetch_one(
            connection,
            "SELECT target_date, predicted_return, predicted_price, actual_price, created_at FROM predictions WHERE model_id = %s AND origin_date = %s AND horizon = %s",
            (original["model_id"], original["origin_date"], original["horizon"]),
        )
        assert mismatch == {
            "target_date": original["target_date"], "predicted_return": original["predicted_return"],
            "predicted_price": original["predicted_price"], "actual_price": original["actual_price"],
            "created_at": original["created_at"],
        }
        matching = {**revised, "target_date": original["target_date"]}
        db.upsert_predictions(connection, pd.DataFrame([matching]))
        connection.commit()
        matched = db.fetch_one(
            connection,
            "SELECT target_date, predicted_return, predicted_price, actual_price, created_at FROM predictions WHERE model_id = %s AND origin_date = %s AND horizon = %s",
            (original["model_id"], original["origin_date"], original["horizon"]),
        )
        assert matched == {
            "target_date": original["target_date"], "predicted_return": original["predicted_return"],
            "predicted_price": original["predicted_price"], "actual_price": 123.45,
            "created_at": original["created_at"],
        }

    @contextmanager
    def connection_factory():
        with db.connect(test_database_url) as connection:
            yield connection

    client = TestClient(create_app(connection_factory))
    for path in ("/health", "/api/v1/prices", "/api/v1/prices/latest", "/api/v1/predictions", "/api/v1/models/current", "/api/v1/pipeline/status", "/api/v1/data-sources/status"):
        assert client.get(path).status_code == 200

    with db.connect(test_database_url) as connection:
        price_snapshot = db.fetch_all(
            connection,
            "SELECT date, open, high, low, close, volume, symbol, updated_at FROM prices ORDER BY date",
        )

    def fail_with_sql_error(connection, *_args, **_kwargs):
        connection.execute("SELECT 1 / 0")

    monkeypatch.setattr(db, "upsert_predictions", fail_with_sql_error)
    with pytest.raises(RuntimeError, match="파이프라인 처리 실패: DivisionByZero"):
        run_pipeline("backfill", source_dir, artifact, date(2014, 7, 1), date(2025, 12, 31), skip_ingestion=True, database_url=test_database_url)

    with db.connect(test_database_url) as connection:
        assert db.fetch_all(
            connection,
            "SELECT date, open, high, low, close, volume, symbol, updated_at FROM prices ORDER BY date",
        ) == price_snapshot
        failed = db.fetch_one(connection, "SELECT status, message FROM pipeline_runs ORDER BY started_at DESC LIMIT 1")
        assert failed["status"] == "failed"
        assert "secret" not in failed["message"]
