"""PostgreSQL schema, UPSERT, serving query."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid
from typing import TYPE_CHECKING

from dotenv import load_dotenv

if TYPE_CHECKING:
    import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE_URL = "postgresql://localhost/coffee_price"

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS prices (
    date DATE PRIMARY KEY,
    open DOUBLE PRECISION,
    high DOUBLE PRECISION,
    low DOUBLE PRECISION,
    close DOUBLE PRECISION NOT NULL,
    volume BIGINT,
    symbol TEXT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS models (
    model_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    horizons INTEGER[] NOT NULL,
    feature_columns JSONB NOT NULL,
    metrics JSONB NOT NULL,
    training_start DATE,
    training_end DATE,
    trained_at TIMESTAMPTZ,
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS predictions (
    model_id TEXT NOT NULL REFERENCES models(model_id),
    origin_date DATE NOT NULL,
    target_date DATE NOT NULL,
    horizon INTEGER NOT NULL CHECK (horizon > 0),
    predicted_return DOUBLE PRECISION NOT NULL,
    predicted_price DOUBLE PRECISION NOT NULL,
    actual_price DOUBLE PRECISION,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (model_id, origin_date, horizon)
);
CREATE INDEX IF NOT EXISTS predictions_target_date_idx
    ON predictions (target_date DESC, horizon);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id UUID PRIMARY KEY,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    message TEXT,
    price_rows INTEGER,
    prediction_rows INTEGER
);
CREATE TABLE IF NOT EXISTS source_status (
    source TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    last_data_date DATE,
    row_count INTEGER NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL,
    error TEXT
);
CREATE TABLE IF NOT EXISTS news_articles (
    article_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL,
    published_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    url TEXT NOT NULL,
    language TEXT NOT NULL,
    rights TEXT NOT NULL,
    modified_at TIMESTAMPTZ NOT NULL,
    available_at TIMESTAMPTZ NOT NULL,
    coffee_relevance DOUBLE PRECISION NOT NULL CHECK (coffee_relevance BETWEEN 0 AND 1),
    topic TEXT NOT NULL CHECK (topic IN ('weather', 'production', 'supply', 'inventory', 'export', 'currency', 'demand', 'logistics', 'policy', 'other')),
    price_impact TEXT NOT NULL CHECK (price_impact IN ('bullish', 'bearish', 'neutral')),
    confidence DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    CHECK (available_at >= published_at AND available_at >= modified_at AND published_at <= collected_at)
);
CREATE INDEX IF NOT EXISTS news_articles_available_at_idx
    ON news_articles (available_at DESC);
CREATE TABLE IF NOT EXISTS news_daily_features (
    as_of_date DATE NOT NULL,
    window_days INTEGER NOT NULL CHECK (window_days > 0),
    availability_mode TEXT NOT NULL,
    metrics JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (as_of_date, window_days, availability_mode)
);
"""

PREDICTION_UPGRADE_SQL = (
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS probability_up DOUBLE PRECISION CHECK (probability_up BETWEEN 0 AND 1)",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS final_direction prediction_direction",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS news_impact_score DOUBLE PRECISION",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS news_article_count INTEGER",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS news_updated_at TIMESTAMPTZ",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS signal_status TEXT",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS classifier_version TEXT",
    "ALTER TABLE predictions ADD COLUMN IF NOT EXISTS model_version TEXT",
)


def database_url() -> str:
    load_dotenv(ROOT / ".env")
    # PG* 환경변수는 비밀번호를 URL에 넣거나 인코딩할 필요가 없다.
    return os.getenv("DATABASE_URL") or ("" if os.getenv("PGHOST") else DEFAULT_DATABASE_URL)


@contextmanager
def connect(url: str | None = None):
    import psycopg
    from psycopg.rows import dict_row

    with psycopg.connect(url or database_url(), row_factory=dict_row) as connection:
        yield connection


def create_schema(connection) -> None:
    connection.execute(
        """
        DO $$ BEGIN
            CREATE TYPE prediction_direction AS ENUM ('UP', 'DOWN', 'FLAT');
        EXCEPTION WHEN duplicate_object THEN NULL;
        END $$;
        """
    )
    for statement in SCHEMA_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    for statement in PREDICTION_UPGRADE_SQL:
        connection.execute(statement)
    connection.commit()


def start_pipeline_run(connection, mode: str) -> str:
    run_id = str(uuid.uuid4())
    connection.execute(
        "INSERT INTO pipeline_runs (run_id, mode, status, started_at) VALUES (%s, %s, 'running', %s)",
        (run_id, mode, datetime.now(timezone.utc)),
    )
    connection.commit()
    return run_id


def finish_pipeline_run(
    connection, run_id: str, status: str, message: str, price_rows=0, prediction_rows=0
) -> None:
    connection.execute(
        """
        UPDATE pipeline_runs
        SET status = %s, finished_at = %s, message = %s,
            price_rows = %s, prediction_rows = %s
        WHERE run_id = %s
        """,
        (status, datetime.now(timezone.utc), message, price_rows, prediction_rows, run_id),
    )
    connection.commit()


def upsert_prices(connection, prices: pd.DataFrame) -> int:
    import pandas as pd

    rows = []
    for row in prices.dropna(subset=["close"]).itertuples():
        rows.append(
            (pd.Timestamp(row.Index).date(), _float(row.open), _float(row.high), _float(row.low),
             float(row.close), _int(row.volume), str(row.symbol))
        )
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO prices (date, open, high, low, close, volume, symbol)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (date) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high, low = EXCLUDED.low,
                    close = EXCLUDED.close, volume = EXCLUDED.volume, symbol = EXCLUDED.symbol,
                    updated_at = now()
                """,
                rows,
            )
    return len(rows)


def upsert_models(connection, bundle, activate=True) -> None:
    metadata = bundle.metadata
    records = [
        (
            metadata["model_id"], metadata["model_name"], [metadata["horizon"]],
            json.dumps(bundle.feature_columns), json.dumps(metadata["notebook_metrics"]),
            metadata["training_start"], metadata["training_end"], metadata["trained_at"], activate,
        ),
        (
            "persistence-h5-h20-v1", "Persistence", [5, 20], json.dumps(["close"]),
            json.dumps({"note": "Notebook에서 5·20일 선택 모델과 사실상 같은 가격 유지 기준"}),
            metadata["training_start"], metadata["training_end"], metadata["trained_at"], activate,
        ),
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO models (
                model_id, name, horizons, feature_columns, metrics,
                training_start, training_end, trained_at, is_current
            ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, %s)
            ON CONFLICT (model_id) DO UPDATE SET
                name = EXCLUDED.name, horizons = EXCLUDED.horizons,
                feature_columns = EXCLUDED.feature_columns, metrics = EXCLUDED.metrics,
                training_start = EXCLUDED.training_start, training_end = EXCLUDED.training_end,
                trained_at = EXCLUDED.trained_at, is_current = EXCLUDED.is_current, updated_at = now()
            """,
            records,
        )
    if activate:
        activate_current_models(connection, [record[0] for record in records])


def upsert_predictions(connection, predictions: pd.DataFrame) -> int:
    import pandas as pd

    signal_columns = (
        "probability_up", "final_direction", "news_impact_score", "news_article_count",
        "news_updated_at", "signal_status", "classifier_version", "model_version",
    )

    def value(row, column):
        return _nullable(getattr(row, column, None))

    rows = [
        (
            row.model_id, row.origin_date, row.target_date, int(row.horizon),
            float(row.predicted_return), float(row.predicted_price), _float(row.actual_price),
            *(value(row, column) for column in signal_columns),
        )
        for row in predictions.itertuples(index=False)
    ]
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO predictions (
                    model_id, origin_date, target_date, horizon,
                    predicted_return, predicted_price, actual_price, probability_up,
                    final_direction, news_impact_score, news_article_count, news_updated_at,
                    signal_status, classifier_version, model_version
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (model_id, origin_date, horizon) DO UPDATE SET
                    actual_price = COALESCE(EXCLUDED.actual_price, predictions.actual_price),
                    probability_up = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.probability_up ELSE predictions.probability_up END,
                    final_direction = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.final_direction ELSE predictions.final_direction END,
                    news_impact_score = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.news_impact_score ELSE predictions.news_impact_score END,
                    news_article_count = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.news_article_count ELSE predictions.news_article_count END,
                    news_updated_at = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.news_updated_at ELSE predictions.news_updated_at END,
                    signal_status = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.signal_status ELSE predictions.signal_status END,
                    classifier_version = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.classifier_version ELSE predictions.classifier_version END,
                    model_version = CASE WHEN predictions.probability_up IS NULL
                        AND predictions.final_direction IS NULL AND predictions.news_impact_score IS NULL
                        AND predictions.news_article_count IS NULL AND predictions.news_updated_at IS NULL
                        AND predictions.signal_status IS NULL AND predictions.classifier_version IS NULL
                        AND predictions.model_version IS NULL
                        AND (EXCLUDED.signal_status IS NOT NULL OR EXCLUDED.model_version IS NOT NULL)
                        AND predictions.predicted_return IS NOT DISTINCT FROM EXCLUDED.predicted_return
                        AND predictions.predicted_price IS NOT DISTINCT FROM EXCLUDED.predicted_price
                        THEN EXCLUDED.model_version ELSE predictions.model_version END
                WHERE predictions.target_date = EXCLUDED.target_date
                """,
                rows,
            )
    return len(rows)


def fill_prediction_actuals(connection) -> None:
    """Fill pending outcomes from the immutable stored prediction target date."""
    connection.execute(
        """
        UPDATE predictions
        SET actual_price = prices.close
        FROM prices
        WHERE predictions.target_date = prices.date
          AND predictions.actual_price IS NULL
        """
    )


def upsert_source_status(connection, records: list[dict]) -> None:
    rows = [
        (record["source"], record["status"], record.get("last_data_date"),
         record.get("row_count", 0), datetime.now(timezone.utc), record.get("error"))
        for record in records
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO source_status (source, status, last_data_date, row_count, updated_at, error)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (source) DO UPDATE SET
                status = EXCLUDED.status, last_data_date = EXCLUDED.last_data_date,
                row_count = EXCLUDED.row_count, updated_at = EXCLUDED.updated_at, error = EXCLUDED.error
            """,
            rows,
        )
    connection.commit()


def upsert_news_articles(connection, articles) -> int:
    """Insert first-seen article metadata without replacing the raw collected record."""
    import pandas as pd

    required = {
        "article_id", "source", "title", "summary", "published_at", "collected_at", "url",
        "language", "rights", "modified_at", "available_at", "coffee_relevance", "topic", "price_impact", "confidence",
    }
    missing = required - set(articles.columns)
    if missing:
        raise ValueError(f"articles miss columns: {sorted(missing)}")
    rows = [
        (
            str(row.article_id), str(row.source), str(row.title), str(row.summary),
            pd.Timestamp(row.published_at).to_pydatetime(), pd.Timestamp(row.collected_at).to_pydatetime(),
            str(row.url), str(row.language), str(row.rights), pd.Timestamp(row.modified_at).to_pydatetime(),
            pd.Timestamp(row.available_at).to_pydatetime(), float(row.coffee_relevance), str(row.topic),
            str(row.price_impact), float(row.confidence),
        )
        for row in articles.itertuples(index=False)
    ]
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO news_articles (
                    article_id, source, title, summary, published_at, collected_at, url, language, rights,
                    modified_at, available_at, coffee_relevance, topic, price_impact, confidence
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (article_id) DO NOTHING
                """,
                rows,
            )
    return len(rows)


def upsert_news_daily_features(connection, frame, availability_mode="historical") -> int:
    """Store each news window as a compact per-date metric document."""
    import pandas as pd
    import re

    columns = {}
    for column in frame.columns:
        match = re.fullmatch(r"news_(.+)_(\d+)d", str(column))
        if match:
            columns.setdefault(int(match.group(2)), []).append((column, match.group(1)))
    rows = []
    for as_of, values in frame.iterrows():
        for window_days, metrics in columns.items():
            document = {
                metric: _json_value(values[column])
                for column, metric in metrics if not pd.isna(values[column])
            }
            rows.append((pd.Timestamp(as_of).date(), window_days, availability_mode, json.dumps(document)))
    if rows:
        with connection.cursor() as cursor:
            cursor.executemany(
                """
                INSERT INTO news_daily_features (as_of_date, window_days, availability_mode, metrics)
                VALUES (%s, %s, %s, %s::jsonb)
                ON CONFLICT (as_of_date, window_days, availability_mode) DO UPDATE SET
                    metrics = EXCLUDED.metrics, updated_at = now()
                """,
                rows,
            )
    return len(rows)


def upsert_intelligence_models(connection, records: list[dict]) -> int:
    """Register a complete, non-overlapping current intelligence model set."""
    if not records:
        return 0
    required = {"model_id", "name", "horizons", "feature_columns", "metrics", "training_start", "training_end", "trained_at"}
    for record in records:
        missing = required - set(record)
        if missing:
            raise ValueError(f"model record misses keys: {sorted(missing)}")
    model_ids = [record["model_id"] for record in records]
    if len(set(model_ids)) != len(model_ids):
        raise ValueError("model records must have unique model_id values")
    horizons = [int(horizon) for record in records for horizon in record["horizons"]]
    if not horizons or any(horizon <= 0 for horizon in horizons) or len(set(horizons)) != len(horizons):
        raise ValueError("model records must have non-overlapping positive horizons")
    affected = fetch_all(connection, "SELECT model_id, horizons FROM models WHERE is_current AND horizons && %s::integer[]", (horizons,))
    for existing in affected:
        if not set(existing["horizons"]).issubset(horizons):
            raise ValueError(f"incoming horizons do not fully replace current model: {existing['model_id']}")
    rows = [
        (
            record["model_id"], record["name"], [int(horizon) for horizon in record["horizons"]],
            json.dumps(record["feature_columns"]), json.dumps(record["metrics"]), record["training_start"],
            record["training_end"], record["trained_at"],
        )
        for record in records
    ]
    with connection.cursor() as cursor:
        cursor.executemany(
            """
            INSERT INTO models (
                model_id, name, horizons, feature_columns, metrics,
                training_start, training_end, trained_at, is_current
            ) VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, %s, %s, FALSE)
            ON CONFLICT (model_id) DO UPDATE SET
                name = EXCLUDED.name, horizons = EXCLUDED.horizons,
                feature_columns = EXCLUDED.feature_columns, metrics = EXCLUDED.metrics,
                training_start = EXCLUDED.training_start, training_end = EXCLUDED.training_end,
                trained_at = EXCLUDED.trained_at, is_current = FALSE, updated_at = now()
            """,
            rows,
        )
    activate_current_models(connection, model_ids)
    return len(rows)


def activate_current_models(connection, model_ids: list[str]) -> None:
    """Set the exact current serving-model set within the caller transaction."""
    if not model_ids or len(set(model_ids)) != len(model_ids):
        raise ValueError("model_ids must be a non-empty unique list")
    connection.execute("UPDATE models SET is_current = FALSE, updated_at = now() WHERE is_current")
    updated = connection.execute(
        "UPDATE models SET is_current = TRUE, updated_at = now() WHERE model_id = ANY(%s)", (model_ids,)
    )
    if updated.rowcount != len(model_ids):
        raise ValueError("all current model_ids must already be registered")


def fetch_all(connection, query: str, parameters=()):
    return connection.execute(query, parameters).fetchall()


def fetch_one(connection, query: str, parameters=()):
    return connection.execute(query, parameters).fetchone()


def _float(value):
    import pandas as pd

    return None if pd.isna(value) else float(value)


def _int(value):
    import pandas as pd

    return None if pd.isna(value) else int(value)


def _nullable(value):
    import pandas as pd

    return None if pd.isna(value) else value


def _json_value(value):
    import pandas as pd

    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


if __name__ == "__main__":
    with connect() as connection:
        create_schema(connection)
    print("DB schema 준비 완료")
