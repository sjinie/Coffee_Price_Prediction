"""PostgreSQL 읽기·쓰기. SQL은 이 파일과 schema.sql에만 둔다.

API 이미지에는 pandas·numpy가 없으므로 이 파일은 둘을 import하지 않는다.
"""
import os
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

SCHEMA = Path(__file__).with_name("schema.sql")
FORECAST_COLUMNS = ["model_version", "origin_date", "horizon", "target_date", "origin_close", "prob_up", "signal",
                    "price_low", "price_high", "predicted_vol", "vol_percentile", "kind"]
NEWS_COLUMNS = ["content_hash", "url", "title", "source", "event_at", "analyzed_at", "available_at", "label",
                "p_bullish", "p_bearish", "p_neutral", "p_uncertain", "relevance", "confidence", "model",
                "prompt_version", "cost_usd"]
PRICE_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


def connect(url: str | None = None) -> psycopg.Connection:
    url = url or os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL이 없습니다")
    return psycopg.connect(url, row_factory=dict_row)


def migrate(conn) -> None:
    conn.execute(SCHEMA.read_text(encoding="utf-8"))
    conn.commit()


def _clean(value):
    """NaN·NaT는 NULL로, numpy 값은 Python 값으로 바꾼다(NaN이 그대로 저장되지 않게)."""
    if type(value).__module__ == "numpy":
        value = value.item()
    if value is None or value != value:  # NaN·NaT만 자기 자신과 같지 않다
        return None
    return value


def _insert(conn, table: str, columns: list[str], rows: list[dict], on_conflict: str = "DO NOTHING") -> int:
    """행을 넣고 실제로 들어간 행 수를 돌려준다. table·columns는 이 파일의 상수만 받는다."""
    sql = (f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join(['%s'] * len(columns))}) "
           f"ON CONFLICT {on_conflict}")
    inserted = 0
    with conn.cursor() as cursor:
        for row in rows:
            cursor.execute(sql, [_clean(row.get(column)) for column in columns])
            inserted += cursor.rowcount
    return inserted


def upsert_prices(conn, prices) -> int:
    """가격은 Yahoo가 최근 값을 고칠 수 있어 같은 날짜면 새 값으로 바꾼다."""
    rows = prices.dropna(subset=["close"]).assign(date=lambda frame: frame["date"].dt.date).to_dict("records")
    update = ", ".join(f"{column} = EXCLUDED.{column}" for column in PRICE_COLUMNS[1:])
    return _insert(conn, "prices", PRICE_COLUMNS, rows, f"(date) DO UPDATE SET {update}")


def activate_model(conn, version: str, train_end: str, metadata: dict) -> None:
    conn.execute("INSERT INTO models (model_version, train_end, metadata) VALUES (%s, %s, %s) "
                 "ON CONFLICT (model_version) DO NOTHING", (version, train_end, Jsonb(metadata)))
    conn.execute("UPDATE models SET is_active = false WHERE is_active AND model_version <> %s", (version,))
    conn.execute("UPDATE models SET is_active = true WHERE model_version = %s", (version,))


def insert_forecasts(conn, rows: list[dict]) -> int:
    return _insert(conn, "forecasts", FORECAST_COLUMNS, rows)


def insert_news(conn, rows: list[dict]) -> int:
    return _insert(conn, "news_articles", NEWS_COLUMNS, rows)


def news_state(conn, since: date, per_day: int) -> tuple[set, set, float]:
    """(분석한 기사 해시, since 이후 기사를 다 채운 뉴욕 날짜, 누적 비용)."""
    hashes = {row["content_hash"] for row in conn.execute("SELECT content_hash FROM news_articles")}
    covered = {row["day"] for row in conn.execute(
        "SELECT (event_at AT TIME ZONE 'America/New_York')::date AS day FROM news_articles "
        "WHERE event_at >= %s GROUP BY day HAVING count(*) >= %s", (since, per_day))}
    spent = conn.execute("SELECT coalesce(sum(cost_usd), 0) AS spent FROM news_articles").fetchone()["spent"]
    return hashes, covered, float(spent)


def start_run(conn, command: str) -> int:
    run_id = conn.execute("INSERT INTO pipeline_runs (command, status) VALUES (%s, 'running') RETURNING run_id",
                          (command,)).fetchone()["run_id"]
    conn.commit()
    return run_id


def finish_run(conn, run_id: int, status: str, steps: list, message: str | None = None) -> None:
    conn.execute("UPDATE pipeline_runs SET finished_at = now(), status = %s, steps = %s, message = %s "
                 "WHERE run_id = %s", (status, Jsonb(steps), message, run_id))
    conn.commit()


# ---- API 조회 ----

def read_prices(conn, start: date) -> list[dict]:
    return conn.execute("SELECT date, close FROM prices WHERE date >= %s ORDER BY date", (start,)).fetchall()


def read_latest_forecasts(conn) -> list[dict]:
    return conn.execute(
        "SELECT f.* FROM forecasts f JOIN models m ON m.model_version = f.model_version AND m.is_active "
        "WHERE f.origin_date = (SELECT max(origin_date) FROM forecasts WHERE model_version = m.model_version) "
        "ORDER BY f.horizon").fetchall()


def read_forecast_history(conn, horizon: int, start: date) -> list[dict]:
    """과거 예측과, 목표일이 지났으면 그날의 실제 종가."""
    return conn.execute(
        "SELECT f.*, p.close AS actual_close FROM forecasts f "
        "JOIN models m ON m.model_version = f.model_version AND m.is_active "
        "LEFT JOIN prices p ON p.date = f.target_date "
        "WHERE f.horizon = %s AND f.origin_date >= %s ORDER BY f.origin_date", (horizon, start)).fetchall()


def read_news(conn, since: date) -> dict:
    # 화면은 기사가 나온 뉴욕 날짜로 묶는다(참고 정보). 모델 입력 시점 규칙(available_at)은 news.daily_news가 따른다.
    articles = conn.execute(
        "SELECT title, url, source, event_at, (event_at AT TIME ZONE 'America/New_York')::date AS day, label, "
        "p_bullish, p_bearish, relevance FROM news_articles WHERE event_at >= %s ORDER BY event_at DESC",
        (since,)).fetchall()
    daily = conn.execute(
        "SELECT (event_at AT TIME ZONE 'America/New_York')::date AS day, "
        "tanh(sum((p_bullish - p_bearish) * relevance)) AS score, count(*) AS articles "
        "FROM news_articles WHERE event_at >= %s GROUP BY day ORDER BY day", (since,)).fetchall()
    return {"articles": articles, "daily": daily}


def read_active_model(conn) -> dict | None:
    return conn.execute("SELECT model_version, train_end, metadata, created_at FROM models WHERE is_active").fetchone()


def read_status(conn, limit: int = 10) -> dict:
    runs = conn.execute("SELECT run_id, command, started_at, finished_at, status, message FROM pipeline_runs "
                        "ORDER BY run_id DESC LIMIT %s", (limit,)).fetchall()
    latest = conn.execute("SELECT max(date) AS price_date FROM prices").fetchone()["price_date"]
    return {"latest_price_date": latest, "runs": runs}
