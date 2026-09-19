"""PostgreSQL의 가격·예측·상태만 제공하는 FastAPI 애플리케이션."""

from __future__ import annotations

import os
from datetime import date

from . import db


def create_app(connection_factory=db.connect):
    from fastapi import FastAPI, Query
    from fastapi.middleware.cors import CORSMiddleware

    app = FastAPI(title="Coffee Price Prediction API", version="1.0.0")
    origins = os.getenv("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173").split(",")
    app.add_middleware(
        CORSMiddleware, allow_origins=origins, allow_credentials=False,
        allow_methods=["GET"], allow_headers=["*"],
    )

    @app.get("/health")
    def health():
        with connection_factory() as connection:
            connection.execute("SELECT 1")
        return {"status": "ok"}

    @app.get("/api/v1/prices")
    def prices(limit: int = Query(365, ge=1, le=5000)):
        with connection_factory() as connection:
            rows = db.fetch_all(
                connection,
                "SELECT date, open, high, low, close, volume, symbol FROM prices ORDER BY date DESC LIMIT %s",
                (limit,),
            )
        return list(reversed(rows))

    @app.get("/api/v1/prices/latest")
    def latest_price():
        with connection_factory() as connection:
            return db.fetch_one(
                connection,
                "SELECT date, open, high, low, close, volume, symbol FROM prices ORDER BY date DESC LIMIT 1",
            )

    @app.get("/api/v1/predictions")
    def predictions(
        horizon: int | None = Query(None, gt=0), model_id: str | None = None,
        limit: int = Query(1000, ge=1, le=5000),
    ):
        where, parameters = (["p.horizon = %s"], [horizon]) if horizon else ([], [])
        if model_id:
            where.append("p.model_id = %s")
            parameters.append(model_id)
        else:
            where.append("m.is_current")
        parameters.append(limit)
        with connection_factory() as connection:
            rows = db.fetch_all(
                connection,
                f"""
                SELECT p.model_id, p.origin_date, p.target_date, p.horizon, p.predicted_return,
                       p.predicted_price, p.actual_price, p.probability_up, p.final_direction,
                       p.news_impact_score, p.news_article_count, p.news_updated_at, p.signal_status,
                       p.classifier_version, p.model_version,
                       m.metrics->>'news_availability' AS news_availability
                FROM predictions p JOIN models m ON m.model_id = p.model_id
                {'WHERE ' + ' AND '.join(where) if where else ''}
                ORDER BY p.target_date DESC, p.horizon LIMIT %s
                """,
                tuple(parameters),
            )
        return list(reversed(rows))

    @app.get("/api/v1/news/summary")
    def news_summary(
        window_days: int = Query(7, gt=0), as_of: date | None = None,
        availability_mode: str = Query("historical", min_length=1),
    ):
        parameters = [window_days, availability_mode]
        where = "window_days = %s AND availability_mode = %s"
        if as_of:
            where += " AND as_of_date = %s"
            parameters.append(as_of)
        with connection_factory() as connection:
            row = db.fetch_one(
                connection,
                f"""
                SELECT as_of_date AS date, window_days, availability_mode, metrics, updated_at
                FROM news_daily_features WHERE {where}
                ORDER BY as_of_date DESC LIMIT 1
                """,
                tuple(parameters),
            )
        return row

    @app.get("/api/v1/models/current")
    def current_models():
        with connection_factory() as connection:
            return db.fetch_all(
                connection,
                """
                SELECT model_id, name, horizons, feature_columns, metrics,
                       training_start, training_end, trained_at
                FROM models WHERE is_current ORDER BY horizons
                """,
            )

    @app.get("/api/v1/pipeline/status")
    def pipeline_status():
        with connection_factory() as connection:
            return db.fetch_one(
                connection,
                """
                SELECT run_id, mode, status, started_at, finished_at, message,
                       price_rows, prediction_rows
                FROM pipeline_runs ORDER BY started_at DESC LIMIT 1
                """,
            )

    @app.get("/api/v1/data-sources/status")
    def source_status():
        with connection_factory() as connection:
            return db.fetch_all(
                connection,
                """
                SELECT source, status, last_data_date, row_count, updated_at, error
                FROM source_status ORDER BY source
                """,
            )

    return app


app = create_app()
