"""수집 → 예측 → DB 적재 파이프라인.

    python -m coffee.pipeline migrate     스키마 적용
    python -m coffee.pipeline backfill    가격 전체, 보관 뉴스, 2026년부터 최신까지 예측(kind=backfill)
    python -m coffee.pipeline daily       자료 갱신 → 최신 기준일 예측(kind=live) → 뉴스 분류

종료 코드: 0 성공, 3 경고(예측은 저장했지만 일부 소스 실패·자료 지연·피처 결측·뉴스 실패), 1 실패.
같은 기준일의 예측이 이미 있으면(backfill 포함) 새로 쓰지 않는다.
"""
import argparse
import sys
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from . import db, jev
from .config import ARTIFACTS_DIR, FORWARD_START, HORIZONS, ROOT, SETTINGS
from .features import build_dataset, trading_sessions
from .models import buy_signal, load_bundle, price_range
from .news import load_jev_archive
from .sources import load_sources, update_all

RISK_WINDOW = 756            # 위험 수준을 매기는 최근 3년(거래일)
NEWS_LOOKBACK_DAYS = 7       # 매일 다시 훑는 뉴스 기간. 하루 이틀 실패해도 메운다
EXIT_OK, EXIT_WARNING, EXIT_FAILED = 0, 3, 1


def load_models(version: str = SETTINGS["model_version"]) -> dict:
    direction, direction_meta = load_bundle(ARTIFACTS_DIR / version / "direction")
    volatility, volatility_meta = load_bundle(ARTIFACTS_DIR / version / "volatility")
    return {"version": version, "direction": direction, "volatility": volatility,
            "direction_meta": direction_meta, "volatility_meta": volatility_meta}


def _rolling_percentile(values: pd.Series) -> pd.Series:
    """각 날짜의 값이 직전 RISK_WINDOW개(그날 포함) 가운데 몇 번째인지(0~1)."""
    def rank(window):
        window = window[~np.isnan(window)]
        return np.mean(window <= window[-1])
    return values.rolling(RISK_WINDOW, min_periods=RISK_WINDOW // 3).apply(rank, raw=True)


def make_forecasts(data: pd.DataFrame, origins, models: dict, kind: str) -> list[dict]:
    """기준일(행 번호)마다 지평별 예측 행을 만든다. 계산은 노트북 06과 같다."""
    origins = np.asarray(origins)
    axis = data.index.append(trading_sessions(data.index[-1] + pd.Timedelta(days=1),
                                              data.index[-1] + pd.Timedelta(days=120)))
    close = data["close"].to_numpy(float)
    rows = []
    for h in HORIZONS:
        prob = models["direction"][h].predict(data, origins)
        signal = buy_signal(prob, models["direction_meta"]["horizons"][str(h)]["threshold"])
        low = high = vol = percentile = np.full(len(origins), np.nan)
        if h in models["volatility"]:
            model = models["volatility"][h]
            valid = np.flatnonzero(data[model.features].notna().all(axis=1).to_numpy())
            log_vol = pd.Series(model.predict(data, valid), index=valid).reindex(range(len(data)))
            multiplier = models["volatility_meta"]["horizons"][str(h)]["interval_multiplier"]
            low, high = price_range(close[origins], log_vol.to_numpy()[origins], h, multiplier)
            vol = np.exp(log_vol.to_numpy()[origins]) * np.sqrt(252)
            percentile = _rolling_percentile(log_vol).to_numpy()[origins]
        for i, origin in enumerate(origins):
            rows.append({"model_version": models["version"], "origin_date": data.index[origin].date(), "horizon": h,
                         "target_date": axis[origin + h].date(), "origin_close": close[origin],
                         "prob_up": float(prob[i]), "signal": str(signal[i]), "price_low": low[i],
                         "price_high": high[i], "predicted_vol": vol[i], "vol_percentile": percentile[i],
                         "kind": kind})
    return rows


def _model_features(models: dict) -> list[str]:
    names = {name for item in models["direction_meta"]["horizons"].values() for name in item["features"]}
    return sorted(names | {name for item in models["volatility_meta"]["horizons"].values() for name in item["features"]})


def _activate(conn, models: dict) -> None:
    metadata = {"direction": models["direction_meta"], "volatility": models["volatility_meta"]}
    db.activate_model(conn, models["version"], models["direction_meta"]["train_end"], metadata)


def update_news(conn, now: datetime) -> dict:
    """최근 며칠의 기사를 모아 새 기사만 Jev로 분류한다. 누적 비용이 상한을 넘으면 분류하지 않는다."""
    settings = SETTINGS["jev"]
    today = now.astimezone(jev.NY).date()
    since = today - timedelta(days=NEWS_LOOKBACK_DAYS)
    known, covered, spent = db.news_state(conn, since, SETTINGS["news"]["articles_per_day"])
    articles = [item for query in SETTINGS["news"]["queries"]
                for item in jev.fetch_google_news(query, since, today + timedelta(days=1))]
    chosen = jev.select_daily(articles, today, known, covered)[: settings["max_articles_per_run"]]
    step = {"step": "news", "collected": len(articles), "selected": len(chosen), "spent_usd": round(spent, 6)}
    if not chosen:
        return step
    if spent >= settings["budget_usd"]:
        return {**step, "warning": f"누적 비용 {spent:.4f} USD가 상한 {settings['budget_usd']} USD 이상"}
    results = jev.classify(chosen)
    inserted = db.insert_news(conn, [{**item, "cost_usd": item["cost"]} for item in results])
    conn.commit()
    return {**step, "classified": inserted, "cost_usd": sum(item["cost"] for item in results)}


def daily(conn, now: datetime | None = None) -> tuple[int, list]:
    now = now or datetime.now(timezone.utc)
    steps, warnings = [], []
    results = update_all(now.date())
    failed = [item["source"] for item in results if item["status"] != "ok"]
    steps.append({"step": "sources", "failed": failed, "results": results})
    if failed:
        warnings.append(f"수집 실패: {', '.join(failed)}")

    sources = load_sources()
    data = build_dataset(sources)
    db.upsert_prices(conn, sources["prices"].tail(30))
    models = load_models()
    _activate(conn, models)
    origin = len(data) - 1
    expected = trading_sessions(now.date() - timedelta(days=10), now.date() - timedelta(days=1))[-1]
    if data.index[origin] < expected:
        warnings.append(f"최신 가격 {data.index[origin]:%Y-%m-%d}이 기대한 {expected:%Y-%m-%d}보다 늦음")
    missing = [name for name in _model_features(models) if pd.isna(data[name].iloc[origin])]
    if missing:
        warnings.append(f"기준일 피처 결측 {len(missing)}개")
    inserted = db.insert_forecasts(conn, make_forecasts(data, [origin], models, "live"))
    conn.commit()
    steps.append({"step": "forecast", "origin": f"{data.index[origin]:%Y-%m-%d}", "inserted": inserted,
                  "missing_features": missing})

    try:
        news = update_news(conn, now)
        if "warning" in news:
            warnings.append(news["warning"])
    except Exception as exc:  # 뉴스는 참고 정보라 실패해도 예측은 유지한다
        conn.rollback()
        news = {"step": "news", "error": f"{type(exc).__name__}: {exc}"}
        warnings.append("뉴스 수집·분류 실패")
    steps.append(news)
    if warnings:
        steps.append({"step": "warnings", "messages": warnings})
    return (EXIT_WARNING if warnings else EXIT_OK), steps


def backfill(conn) -> tuple[int, list]:
    sources = load_sources()
    data = build_dataset(sources)
    models = load_models()
    _activate(conn, models)
    steps = [{"step": "prices", "upserted": db.upsert_prices(conn, sources["prices"])}]
    origins = np.flatnonzero((data.index >= FORWARD_START) & data["close"].notna().to_numpy())
    inserted = db.insert_forecasts(conn, make_forecasts(data, origins, models, "backfill"))
    steps.append({"step": "forecast", "origins": len(origins), "inserted": inserted})
    warnings = []
    try:
        archive = load_jev_archive()
    except FileNotFoundError:
        archive, warnings = None, ["보관 뉴스(data/jev/responses.json)가 없어 건너뜀"]
    if archive is not None:
        rows = archive.assign(model=SETTINGS["jev"]["model"], prompt_version=SETTINGS["jev"]["prompt_version"],
                              cost_usd=0.0).to_dict("records")
        steps.append({"step": "news_archive", "articles": len(rows), "inserted": db.insert_news(conn, rows)})
    conn.commit()
    if warnings:
        steps.append({"step": "warnings", "messages": warnings})
    return (EXIT_WARNING if warnings else EXIT_OK), steps


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="커피 예측 파이프라인")
    parser.add_argument("command", choices=["migrate", "daily", "backfill"])
    command = parser.parse_args(argv).command
    load_dotenv(ROOT / ".env")
    try:
        conn = db.connect()
    except Exception as exc:
        print(f"DB 연결 실패: {type(exc).__name__}", file=sys.stderr)  # 접속 문자열은 출력하지 않는다
        return EXIT_FAILED
    with conn:
        if command == "migrate":
            db.migrate(conn)
            print("스키마 적용 완료")
            return EXIT_OK
        run_id = db.start_run(conn, command)
        try:
            code, steps = (daily if command == "daily" else backfill)(conn)
        except Exception as exc:
            conn.rollback()
            message = f"{type(exc).__name__}: {exc}"
            db.finish_run(conn, run_id, "failed", [], message)
            print(f"실패: {message}", file=sys.stderr)
            return EXIT_FAILED
        status = "warning" if code == EXIT_WARNING else "success"
        db.finish_run(conn, run_id, status, steps)
        for step in steps:
            print({key: value for key, value in step.items() if key != "results"})
        print(f"{command}: {status}")
        return code


if __name__ == "__main__":
    sys.exit(main())
