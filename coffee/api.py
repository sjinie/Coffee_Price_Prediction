"""읽기 전용 API. DB 계정은 SELECT 권한만 있으면 된다.

    uvicorn coffee.api:app
"""
import os
from datetime import date, timedelta

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import db

HORIZONS = (5, 20, 60)  # schema.sql의 CHECK와 같다 (API 이미지에는 config가 쓰는 pandas가 없다)

REGIONS = ("br_sul_minas", "br_cerrado", "br_alta_mogiana", "co_huila", "co_caldas", "co_antioquia")

app = FastAPI(title="커피 선물 예측 API")
cors_origins = [origin for origin in os.getenv("CORS_ORIGINS", "").split(",") if origin]
if cors_origins:
    app.add_middleware(CORSMiddleware, allow_origins=cors_origins, allow_methods=["GET"])


def _read(function, *args):
    # ponytail: 요청마다 연결한다. 트래픽이 늘면 psycopg_pool로 바꾼다.
    with db.connect() as conn:
        return function(conn, *args)


def _since(days: int) -> date:
    return date.today() - timedelta(days=days)


@app.get("/health")
def health():
    try:
        _read(lambda conn: conn.execute("SELECT 1"))
    except Exception:
        return JSONResponse({"status": "db_unavailable"}, status_code=503)
    return {"status": "ok"}


@app.get("/api/prices")
def prices(days: int = Query(730, ge=1, le=8000)):
    return _read(db.read_prices, _since(days))


@app.get("/api/weather")
def weather(region: str = "br_sul_minas"):
    if region not in REGIONS:
        raise HTTPException(422, f"region은 {REGIONS} 중 하나")
    return _read(db.read_weather, region, date(2005, 1, 1))


@app.get("/api/forecasts/latest")
def latest_forecasts():
    return _read(db.read_latest_forecasts)


@app.get("/api/forecasts/history")
def forecast_history(horizon: int = 20, days: int = Query(365, ge=1, le=3650)):
    if horizon not in HORIZONS:
        raise HTTPException(422, f"horizon은 {HORIZONS} 중 하나")
    return _read(db.read_forecast_history, horizon, _since(days))


@app.get("/api/news")
def news(days: int = Query(30, ge=1, le=365)):
    return _read(db.read_news, _since(days))


@app.get("/api/models")
def active_model():
    return _read(db.read_active_model)


@app.get("/api/status")
def status():
    return _read(db.read_status)
