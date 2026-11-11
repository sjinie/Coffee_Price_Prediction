"""수집 → 예측 → DB 적재 파이프라인.

    python -m coffee.pipeline migrate     스키마 적용
    python -m coffee.pipeline backfill    가격·기상 전체, 보관 뉴스, 2026년부터 최신까지 예측(kind=backfill)
    python -m coffee.pipeline weather     보관 기상 전체 적재(가격·모델·뉴스는 건드리지 않음)
    python -m coffee.pipeline daily       자료 갱신 → 최신 기준일 예측(kind=live) → 뉴스 분류
    python -m coffee.pipeline daily --scheduled   예약 배치(GitHub Actions schedule)가 쓰는 형태

종료 코드: 0 성공, 3 경고(일부 소스 실패·피처 결측·뉴스 실패면 예측은 저장, 가격이 늦으면 예측을 저장하지 않음,
기준일의 예약 시각 전 수동 실행이면 아무것도 하지 않음), 1 실패.
같은 기준일의 예측이 이미 있으면(backfill 포함) 새로 쓰지 않는다. 예약 실행만 그 기준일의 live 예측을 새 값으로 바꾼다.
"""
import argparse
import sys
from datetime import datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
from dotenv import load_dotenv

from . import db, jev
from .config import ARTIFACTS_DIR, FORWARD_START, HORIZONS, REGION_IDS, ROOT, SETTINGS, SOURCES_DIR
from .features import add_news, build_dataset, trading_sessions
from .models import buy_signal, load_bundle
from .news import load_jev_archive
from .sources import load_sources, update_all

RISK_WINDOW = 756            # 위험 수준을 매기는 최근 3년(거래일)
NEWS_LOOKBACK_DAYS = 7       # 매일 다시 훑는 뉴스 기간. 하루 이틀 실패해도 메운다
EXIT_OK, EXIT_WARNING, EXIT_FAILED = 0, 3, 1
SCHEDULE_UTC = time.fromisoformat(SETTINGS["daily_schedule_utc"])  # 예약 배치 시각(daily.yml cron과 같다)
MODEL_KINDS = ("distribution",)  # 분포 모델 하나가 범위·가운데 가격·상승 확률·신호·위험 수준을 낸다(노트북 07)


def load_models(version: str = SETTINGS["model_version"]) -> dict:
    models = {"version": version}
    for kind in MODEL_KINDS:
        models[kind], models[f"{kind}_meta"] = load_bundle(ARTIFACTS_DIR / version / kind)
    return models


def _rolling_percentile(values: pd.Series) -> pd.Series:
    """각 날짜의 값이 직전 RISK_WINDOW개(그날 포함) 가운데 몇 번째인지(0~1). 그날 값이 없으면 결측."""
    def rank(window):
        if np.isnan(window[-1]):
            return np.nan
        window = window[~np.isnan(window)]
        return np.mean(window <= window[-1])
    return values.rolling(RISK_WINDOW, min_periods=RISK_WINDOW // 3).apply(rank, raw=True)


def closed_prices(prices: pd.DataFrame, now: datetime) -> pd.DataFrame:
    """23:00 UTC 마감이 지난 거래일의 가격만 남긴다.

    장중에 실행하면 Yahoo가 아직 끝나지 않은 오늘 봉을 준다. 그 값으로 낸 예측은 끝나지 않은 가격을
    기준일 종가로 기록에 남긴다.
    """
    return prices[prices["date"].dt.date <= _last_closed(now)]


def _last_closed(now: datetime):
    """23:00 UTC 마감이 지난 마지막 달력 날짜."""
    return (pd.Timestamp(now).tz_convert("UTC") - pd.Timedelta(hours=23)).date()


def scheduled_time(now: datetime) -> tuple[pd.Timestamp, datetime]:
    """(마감이 지난 최신 거래일, 예약 배치가 그 거래일을 처리하는 시각 = 다음 날 SCHEDULE_UTC)."""
    last_closed = _last_closed(now)
    session = trading_sessions(last_closed - timedelta(days=10), last_closed)[-1]
    return session, datetime.combine(session.date() + timedelta(days=1), SCHEDULE_UTC, tzinfo=timezone.utc)


def make_forecasts(data: pd.DataFrame, origins, models: dict, kind: str) -> list[dict]:
    """기준일(행 번호)마다 지평별 예측 행을 만든다. 모든 값이 같은 분포에서 나온다(노트북 06과 같은 계산).

    가운데 가격 = 50% 분위, 범위 = 10%·90% 분위, 상승 확률 = 현재가 위의 비율, 신호 = 상승 확률과 기준,
    예측 변동성 = 분포의 표준편차를 연율로 바꾼 값, 위험 수준 = 폭 σ의 최근 3년 백분위.
    """
    origins = np.asarray(origins)
    axis = data.index.append(trading_sessions(data.index[-1] + pd.Timedelta(days=1),
                                              data.index[-1] + pd.Timedelta(days=120)))
    close = data["close"].to_numpy(float)
    rows = []
    for h in HORIZONS:
        model = models["distribution"][h]
        low, mid, high = model.quantiles(data, origins).T
        prob = model.prob_up(data, origins)
        signal = buy_signal(prob, models["distribution_meta"]["horizons"][str(h)]["threshold"])
        vol = model.spread(data, origins) * np.sqrt(252 / h)
        sigma = model.distribution(data, np.arange(len(data)))[1]
        percentile = _rolling_percentile(pd.Series(np.log(sigma))).to_numpy()[origins]
        for i, origin in enumerate(origins):
            rows.append({"model_version": models["version"], "origin_date": data.index[origin].date(), "horizon": h,
                         "target_date": axis[origin + h].date(), "origin_close": close[origin],
                         "predicted_return": mid[i], "predicted_price": close[origin] * np.exp(mid[i]),
                         "prob_up": float(prob[i]), "signal": str(signal[i]),
                         "price_low": close[origin] * np.exp(low[i]), "price_high": close[origin] * np.exp(high[i]),
                         "predicted_vol": vol[i], "vol_percentile": percentile[i], "kind": kind})
    return rows


def _articles(rows: list[dict]) -> pd.DataFrame:
    """DB에서 읽은 기사 점수를 news.daily_news가 받는 표로 바꾼다(시각은 UTC)."""
    frame = pd.DataFrame(rows, columns=["event_at", "available_at", "p_bullish", "p_bearish", "relevance"])
    for column in ("event_at", "available_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True)
    return frame


def _model_features(models: dict) -> list[str]:
    return sorted({name for kind in MODEL_KINDS
                   for item in models[f"{kind}_meta"]["horizons"].values() for name in item["features"]})


EVALUATION_KEYS = ("dev", "holdout", "forward", "b2")  # 지평별 검증 성적


def _behavior(metadata: dict) -> dict:
    """metadata에서 검증 성적과 생성 커밋을 뺀 부분. 모델 파일·매수 기준·피처처럼 예측을 정하는 것이 남는다."""
    kept = {}
    for kind, meta in metadata.items():
        horizons = {h: {k: v for k, v in entry.items() if k not in EVALUATION_KEYS}
                    for h, entry in meta.get("horizons", {}).items()}
        kept[kind] = {**{k: v for k, v in meta.items() if k != "code_commit"}, "horizons": horizons}
    return kept


def _activate(conn, models: dict) -> None:
    # 같은 버전은 검증 성적만 정정할 수 있다. 예측을 정하는 부분이 다르면 그 버전으로 쌓인 예측이
    # 서로 다른 방법으로 만들어지므로 멈춘다(새 버전으로 동결해야 한다).
    metadata = {kind: models[f"{kind}_meta"] for kind in MODEL_KINDS}
    stored = db.read_model_metadata(conn, models["version"])
    if stored is not None and _behavior(stored) != _behavior(metadata):
        raise RuntimeError(f"모델 {models['version']}의 예측 설정이 등록된 것과 다르다. 새 버전으로 동결해야 한다")
    db.activate_model(conn, models["version"], models["distribution_meta"]["train_end"], metadata)


def update_news(conn, now: datetime) -> dict:
    """최근 며칠의 기사를 모아 새 기사만 Jev로 분류한다.

    요청 비용은 응답을 받아야 알 수 있으므로, 실행마다 reserve_usd를 미리 잡아 두고 남은 예산이 그보다
    작으면 분류하지 않는다. 실제 비용이 예약을 넘으면 경고한다(예약값을 늘려야 상한이 지켜진다).
    """
    settings = SETTINGS["jev"]
    today = now.astimezone(jev.NY).date()
    since = today - timedelta(days=NEWS_LOOKBACK_DAYS)
    known, per_day_counts, spent = db.news_state(conn, since)
    articles = [item for query in SETTINGS["news"]["queries"]
                for item in jev.fetch_google_news(query, since, today + timedelta(days=1))]
    chosen = jev.select_daily(articles, today, known, per_day_counts)[: settings["max_articles_per_run"]]
    step = {"step": "news", "collected": len(articles), "selected": len(chosen), "spent_usd": round(spent, 6)}
    if not chosen:
        return step
    remaining, reserve = settings["budget_usd"] - spent, settings["reserve_usd"]
    if remaining < reserve:
        return {**step, "warning": f"남은 예산 {remaining:.4f} USD가 실행당 예약 {reserve} USD보다 작아 분류하지 않음"}
    results = jev.classify(chosen)
    inserted = db.insert_news(conn, [{**item, "cost_usd": item["cost"]} for item in results])
    conn.commit()
    cost = sum(item["cost"] for item in results)
    step = {**step, "classified": inserted, "cost_usd": cost}
    if cost > reserve:
        step["warning"] = f"이번 분류 비용 {cost:.4f} USD가 예약 {reserve} USD를 넘음(reserve_usd를 늘려야 함)"
    return step


def store_weather(conn, sources: dict, recent_days: int | None = None, now: datetime | None = None) -> dict:
    """collect_start 이후를 적재한다. daily는 실행일(UTC) 포함 최근 30일만 다시 쓴다."""
    start = pd.Timestamp(SETTINGS["collect_start"], tz="UTC")
    today = pd.Timestamp(now or datetime.now(timezone.utc)).tz_convert("UTC").normalize()
    if recent_days is not None:
        start = max(start, today - pd.Timedelta(days=recent_days - 1))
    upserted = 0
    for region in REGION_IDS:
        frame = sources[f"weather_{region}"]
        dates = pd.to_datetime(frame["date"], utc=True).dt.normalize()
        selected = dates >= start
        if recent_days is not None:
            selected &= dates <= today
        upserted += db.upsert_weather(conn, region, frame.loc[selected])
    return {"step": "weather", "upserted": upserted}


def weather(conn) -> tuple[int, list]:
    """기상 파일 6개만 읽는다. 가격·거시 자료 없이도 단독 적재할 수 있다."""
    sources = {}
    for region in REGION_IDS:
        name = f"weather_{region}"
        frame = pd.read_parquet(SOURCES_DIR / f"{name}.parquet")
        frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
        if frame["date"].duplicated().any():
            raise ValueError(f"{name}: 날짜가 중복됩니다")
        sources[name] = frame.sort_values("date").reset_index(drop=True)
    step = store_weather(conn, sources)
    conn.commit()
    return EXIT_OK, [step]


def daily(conn, now: datetime | None = None, scheduled: bool = False) -> tuple[int, list]:
    """scheduled는 예약 배치일 때만 True다(run-daily.sh가 GitHub의 schedule 이벤트에서만 넘긴다)."""
    now = now or datetime.now(timezone.utc)
    if not scheduled:
        # 수동 실행이 예약 배치보다 먼저 그 기준일의 예측을 남기지 않게 한다. 2026-11-11 05:38 UTC의 수동 실행은
        # FRED 4개 소스가 실패한 채로 기준일 11-10의 예측을 저장했다(개발 기록 31).
        session, due = scheduled_time(now)
        if now < due:
            return EXIT_WARNING, [{"step": "warnings", "messages": [
                f"기준일 {session:%Y-%m-%d}의 예약 시각({due:%m-%d %H:%M} UTC) 전 수동 실행이라 수집·예측을 하지 않음"]}]
    steps, warnings = [], []
    results = update_all(now.date())
    failed = [item["source"] for item in results if item["status"] != "ok"]
    steps.append({"step": "sources", "failed": failed, "results": results})
    if failed:
        warnings.append(f"수집 실패: {', '.join(failed)}")

    sources = load_sources()
    sources["prices"] = closed_prices(sources["prices"], now)
    # 뉴스 점수: 기준일 마감까지 분석이 끝난 기사(live 시점). 오늘 분류할 기사는 다음 기준일부터 쓴다.
    since = (now - timedelta(days=2 * NEWS_LOOKBACK_DAYS)).date()
    data = add_news(build_dataset(sources), _articles(db.read_news_inputs(conn, since)), "live")
    db.upsert_prices(conn, sources["prices"].tail(30))
    steps.append(store_weather(conn, sources, recent_days=30, now=now))
    models = load_models()
    _activate(conn, models)
    origin = len(data) - 1
    expected = trading_sessions(now.date() - timedelta(days=10), now.date() - timedelta(days=1))[-1]
    # 가격이 늦으면 예측을 저장하지 않는다. 지난 기준일의 예측을 나중에 live로 남기면 실시간 성적이
    # '다음 종가를 보기 전에 낸 예측'이라는 뜻을 잃는다. backfill을 다시 돌리면 빠진 날도 채워지지만 kind='backfill'이라 실시간 성적에 섞이지 않는다.
    stale = data.index[origin] < expected
    if stale:
        warnings.append(f"최신 가격 {data.index[origin]:%Y-%m-%d}이 기대한 {expected:%Y-%m-%d}보다 늦어 예측을 저장하지 않음")
    missing = [name for name in _model_features(models) if pd.isna(data[name].iloc[origin])]
    if missing:
        warnings.append(f"기준일 피처 결측 {len(missing)}개")
    # 예약 실행은 같은 기준일에 이미 있는 live 예측을 새 값으로 바꾼다. 앞선 수동 실행이 일부 소스 없이 남긴 값을
    # 바로잡는다. 기준일은 늘 최신 종가라서 다음 종가를 본 뒤에 고쳐지는 예측은 없다.
    written = 0 if stale else db.insert_forecasts(conn, make_forecasts(data, [origin], models, "live"), replace=scheduled)
    conn.commit()
    steps.append({"step": "forecast", "origin": f"{data.index[origin]:%Y-%m-%d}", "written": written,
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


def backfill(conn, now: datetime | None = None) -> tuple[int, list]:
    # 예측 행은 덮어쓰지 않으므로(ON CONFLICT DO NOTHING) 보관 뉴스 없이 계산한 소급 예측은 나중에 고칠 수 없다.
    # 그래서 아무것도 쓰기 전에 멈추고 실행을 실패로 남긴다. 실행 기록은 공개되므로 경로는 상대 경로만 적는다.
    try:
        archive = load_jev_archive()
    except FileNotFoundError:
        raise RuntimeError("보관 뉴스(data/jev/responses.json)가 없어 소급 예측을 멈춤. 보관 뉴스가 있는 곳에서 다시 실행") from None
    sources = load_sources()
    sources["prices"] = closed_prices(sources["prices"], now or datetime.now(timezone.utc))
    # 소급 예측은 학습·평가와 같은 연구용 시점(발행 + 1일)으로 보관 기사의 점수를 붙인다
    data = add_news(build_dataset(sources), archive, "research")
    models = load_models()
    _activate(conn, models)
    steps = [{"step": "prices", "upserted": db.upsert_prices(conn, sources["prices"])},
             store_weather(conn, sources)]
    origins = np.flatnonzero((data.index >= FORWARD_START) & data["close"].notna().to_numpy())
    inserted = db.insert_forecasts(conn, make_forecasts(data, origins, models, "backfill"))
    steps.append({"step": "forecast", "origins": len(origins), "inserted": inserted})
    rows = archive.assign(model=SETTINGS["jev"]["model"], prompt_version=SETTINGS["jev"]["prompt_version"],
                          cost_usd=0.0).to_dict("records")
    steps.append({"step": "news_archive", "articles": len(rows), "inserted": db.insert_news(conn, rows)})
    conn.commit()
    return EXIT_OK, steps


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="커피 예측 파이프라인")
    parser.add_argument("command", choices=["migrate", "daily", "backfill", "weather"])
    parser.add_argument("--scheduled", action="store_true", help="daily를 예약 배치로 실행한다(기준일의 live 예측을 새 값으로 바꾼다)")
    args = parser.parse_args(argv)
    command = args.command
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
            if command == "daily":
                code, steps = daily(conn, scheduled=args.scheduled)
            else:
                code, steps = {"backfill": backfill, "weather": weather}[command](conn)
        except Exception as exc:
            conn.rollback()
            # 실행 기록은 공개 API로 보이므로 첫 줄만 짧게 남기고, 전체 메시지는 실행 로그에만 출력한다.
            detail = (str(exc).splitlines() or [""])[0][:200]
            db.finish_run(conn, run_id, "failed", [], f"{type(exc).__name__}: {detail}")
            print(f"실패: {type(exc).__name__}: {exc}", file=sys.stderr)
            return EXIT_FAILED
        status = "warning" if code == EXIT_WARNING else "success"
        # 경고 이유는 공개 상태 API가 보여 주는 message에도 짧게 남긴다(steps는 API로 나가지 않는다)
        warned = [text for step in steps if step.get("step") == "warnings" for text in step["messages"]]
        db.finish_run(conn, run_id, status, steps, "; ".join(warned)[:200] or None)
        for step in steps:
            print({key: value for key, value in step.items() if key != "results"})
        print(f"{command}: {status}")
        return code


if __name__ == "__main__":
    sys.exit(main())
