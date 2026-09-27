"""External source부터 PostgreSQL prediction까지 실행하는 Local E2E pipeline."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
import json
import os
from pathlib import Path

import pandas as pd
import yaml
from dotenv import load_dotenv
import yfinance as yf

from . import db
from .features import assemble_features, load_sources
from .inference import generate_predictions
from .ingestion import (
    build_session,
    fetch_cot,
    fetch_fred,
    fetch_initial_release,
    fetch_weather,
    fetch_yahoo,
    incremental_start,
    persist_increment,
)
from .modeling import DEFAULT_ARTIFACT, load_bundle


ROOT = Path(__file__).resolve().parents[1]
WEATHER_BUFFER_DAYS = 90
SOURCE_GROUPS = ("yahoo", "fred", "nasa", "cftc")
REQUIRED_SOURCES = {
    "coffee", "alfred_dexbzus", "alfred_dff", "alfred_dcoilwtico",
}
REQUIRED_MACRO_SOURCES = tuple(sorted(REQUIRED_SOURCES - {"coffee"}))
MAX_MACRO_STALENESS_DAYS = 14


def collection_jobs(config, regions, session, selected_groups):
    jobs = []
    if "yahoo" in selected_groups:
        jobs.extend([
            ("coffee", "yahoo", fetch_yahoo, ("KC=F",)),
            ("brl", "yahoo", fetch_yahoo, ("BRL=X",)),
        ])
    if "fred" in selected_groups:
        jobs.extend(
            (series.lower(), "fred", fetch_fred, (session, series))
            for series in ("DFF", "DTWEXBGS", "DCOILWTICO")
        )
        jobs.extend(
            (f"alfred_{series.lower()}", "fred", fetch_initial_release, (session, series))
            for series in config["fred"]["initial_release_series"]
        )
    if "nasa" in selected_groups:
        jobs.extend(
            (f"weather_{region['region_id']}", "nasa", fetch_weather, (session, region))
            for region in regions
        )
    if "cftc" in selected_groups:
        jobs.append(("cot", "cftc", fetch_cot, (session,)))
    return jobs


def collect(
    source_dir: Path,
    mode: str,
    start: date,
    end: date,
    selected_groups=SOURCE_GROUPS,
    selected_regions=None,
) -> tuple[list[dict], list[str]]:
    config = yaml.safe_load((ROOT / "configs" / "sources.yaml").read_text())
    regions = yaml.safe_load((ROOT / "configs" / "regions.yaml").read_text())["regions"]
    if selected_regions:
        unknown = set(selected_regions) - {region["region_id"] for region in regions}
        if unknown:
            raise ValueError("알 수 없는 기상 지점: " + ", ".join(sorted(unknown)))
        regions = [region for region in regions if region["region_id"] in selected_regions]
    statuses, failures = [], []
    with build_session() as session:
        for name, _group, fetch, inputs in collection_jobs(config, regions, session, selected_groups):
            path = source_dir / f"{name}.parquet"
            fetch_start = start
            if mode == "incremental":
                fetch_start = incremental_start(path, start, overlap_days=7)
            elif name.startswith("weather_"):
                fetch_start -= timedelta(days=WEATHER_BUFFER_DAYS)
            if fetch_start > end:
                statuses.append(status_from_path(name, path, "current", cutoff=end))
                continue
            print(f"수집: {name} | {fetch_start} ~ {end}", flush=True)
            try:
                incoming = fetch(*inputs, fetch_start, end)
                persist_increment(path, incoming)
                statuses.append(status_from_path(name, path, "success", cutoff=end))
            except Exception as exc:
                key = os.getenv("FRED_API_KEY") or "<no-key>"
                message = str(exc).replace(key, "<redacted>")
                statuses.append(status_from_path(name, path, "failed", message, cutoff=end))
                failures.append(name)
                print(f"  실패: {type(exc).__name__}: {message}", flush=True)
    return statuses, failures


def status_from_path(name: str, path: Path, status: str, error=None, cutoff=None) -> dict:
    if path.exists():
        frame = pd.read_parquet(path)
        if cutoff is not None:
            frame = frame.loc[pd.to_datetime(frame["date"]).le(pd.Timestamp(cutoff))]
            if "release_date" in frame:
                frame = frame.loc[(pd.to_datetime(frame["release_date"]) + pd.Timedelta(days=1)).le(pd.Timestamp(cutoff))]
        last_date = None if frame.empty else pd.Timestamp(frame["date"].max()).date()
        row_count = len(frame)
    else:
        last_date, row_count = None, 0
    return {
        "source": name, "status": status, "last_data_date": last_date,
        "row_count": row_count, "error": error,
    }


def existing_source_status(source_dir: Path, cutoff: date) -> list[dict]:
    return [status_from_path(path.stem, path, "existing", cutoff=cutoff) for path in sorted(source_dir.glob("*.parquet"))]


def sources_as_of(source_dir: Path, cutoff: date) -> dict[str, pd.DataFrame]:
    """Cut saved source data at the requested as-of date before feature assembly."""
    sources = load_sources(source_dir)
    cutoff_at = pd.Timestamp(cutoff)
    for name, frame in sources.items():
        filtered = frame.loc[pd.to_datetime(frame["date"]).le(cutoff_at)].copy()
        if "release_date" in filtered:
            available_at = pd.to_datetime(filtered["release_date"]) + pd.Timedelta(days=1)
            filtered = filtered.loc[available_at.le(cutoff_at)]
        sources[name] = filtered.reset_index(drop=True)
    return sources


def required_failures(failures: list[str]) -> list[str]:
    return sorted(set(failures).intersection(REQUIRED_SOURCES))


def validate_macro_freshness(sources: dict[str, pd.DataFrame], cutoff: date) -> None:
    """Reject local-serving data when required macro observations lag coffee too far."""
    coffee = sources["coffee"]
    coffee_latest = pd.to_datetime(coffee["date"]).max()
    if pd.isna(coffee_latest):
        raise ValueError("커피 가격 데이터가 없습니다.")
    cutoff_at = pd.Timestamp(cutoff)
    for name in REQUIRED_MACRO_SOURCES:
        macro = sources[name].dropna(subset=["value"]).copy()
        available_at = pd.to_datetime(macro["release_date"]) + pd.Timedelta(days=1)
        macro = macro.loc[available_at.le(cutoff_at)]
        latest = pd.to_datetime(macro["date"]).max()
        if pd.isna(latest) or (coffee_latest - latest).days > MAX_MACRO_STALENESS_DAYS:
            raise ValueError(f"거시 데이터가 오래되었습니다: {name}")


def validate_latest_prediction_coverage(dataset, predictions: pd.DataFrame) -> None:
    """Reject a run that cannot serve every advertised horizon at the latest price."""
    if "close" not in dataset.prices:
        raise ValueError("가격 데이터에 close 열이 없습니다.")
    latest = dataset.prices.loc[dataset.prices["close"].notna()].index.max()
    if pd.isna(latest):
        raise ValueError("최신 커피 가격이 없습니다.")
    latest_date = pd.Timestamp(latest).date()
    origins = pd.to_datetime(predictions["origin_date"], errors="coerce").dt.date
    present = set(predictions.loc[origins.eq(latest_date), "horizon"])
    missing = sorted({5, 20, 60} - present)
    if missing:
        raise ValueError("최신 가격일 예측이 없습니다: " + ", ".join(f"horizon {value}" for value in missing))


def run_pipeline(
    mode: str,
    source_dir: Path,
    artifact: Path,
    start: date,
    end: date,
    *,
    selected_groups=SOURCE_GROUPS,
    selected_regions=None,
    skip_ingestion=False,
    database_url=None,
    news_path=None,
    intelligence_artifact=None,
    ingest_news_source=False,
    news_availability="live",
) -> dict:
    source_dir, artifact = Path(source_dir), Path(artifact)
    if news_availability not in {"live", "historical"}:
        raise ValueError("news_availability는 live 또는 historical이어야 합니다.")
    with db.connect(database_url) as connection:
        db.create_schema(connection)
        run_id = db.start_pipeline_run(connection, mode)
        try:
            if skip_ingestion:
                statuses, failures = existing_source_status(source_dir, end), []
            else:
                statuses, failures = collect(
                    source_dir, mode, start, end, selected_groups, selected_regions
                )
            articles, news_available = None, False
            news_enabled = news_path is not None or intelligence_artifact is not None or ingest_news_source
            if news_enabled:
                from .news import ingest_news, read_news

                path = Path(news_path) if news_path else source_dir / "news" / "articles.parquet"
                try:
                    articles = (ingest_news(path, start, end, mode) if ingest_news_source else read_news(path))
                    news_available = path.exists()
                    if not news_available:
                        raise FileNotFoundError("뉴스 저장 자료가 없습니다.")
                    coverage_start = articles.attrs.get("coverage_start")
                    coverage_end = articles.attrs.get("coverage_end")
                    if (coverage_start is None or coverage_end is None
                            or pd.Timestamp(coverage_start).date() > start
                            or pd.Timestamp(coverage_end).date() < end):
                        raise ValueError("뉴스 수집 범위가 요청 기간을 포함하지 않습니다.")
                    cutoff_at = pd.Timestamp(end, tz="UTC") + pd.Timedelta(hours=23)
                    visible = articles.loc[articles.available_at.le(cutoff_at)]
                    if news_availability == "live":
                        visible = visible.loc[visible.collected_at.le(cutoff_at)]
                    statuses.append({"source": "news", "status": "success" if ingest_news_source else "existing",
                                     "last_data_date": None if visible.empty else visible.published_at.max().date(),
                                     "row_count": len(visible)})
                except Exception as exc:
                    # 뉴스 장애는 필수 numeric source와 독립적으로 기록한다.
                    articles, news_available = None, False
                    failures.append("news")
                    statuses.append({"source": "news", "status": "failed", "row_count": 0,
                                     "error": f"뉴스 처리 실패: {type(exc).__name__}"})
            if statuses:
                db.upsert_source_status(connection, statuses)
            blocking_failures = required_failures(failures)
            if blocking_failures:
                raise RuntimeError("수집 실패: " + ", ".join(blocking_failures))
            sources = sources_as_of(source_dir, end)
            validate_macro_freshness(sources, end)
            dataset = assemble_features(sources, fit_end=min("2023-12-31", end.isoformat()))
            bundle = load_bundle(artifact)
            predictions = generate_predictions(dataset, bundle)
            validate_latest_prediction_coverage(dataset, predictions)
            if intelligence_artifact is None:
                db.upsert_models(connection, bundle)
            else:
                db.upsert_models(connection, bundle, activate=False)
            if news_available:
                from .news import aggregate_news

                collected_bound = (pd.Timestamp.now(tz="UTC") if news_availability == "live" else None)
                news_features = aggregate_news(articles, dataset.sessions, (1, 3, 7, 14, 30, 60),
                                               collected_bound=collected_bound)
                db.upsert_news_articles(connection, articles)
                db.upsert_news_daily_features(connection, news_features, availability_mode=news_availability)
            if intelligence_artifact is not None:
                from .intelligence import load_intelligence, model_records, predict_intelligence

                intelligence = load_intelligence(Path(intelligence_artifact))
                predictions = predict_intelligence(
                    dataset, predictions, articles, intelligence,
                    news_available=news_available, availability_mode=news_availability,
                )
                db.upsert_intelligence_models(connection, model_records(
                    intelligence, availability_mode=news_availability, base_bundle=bundle,
                ))
            price_rows = db.upsert_prices(connection, dataset.prices)
            prediction_rows = db.upsert_predictions(connection, predictions)
            db.fill_prediction_actuals(connection)
            message = f"가격 {price_rows:,}행, 예측 {prediction_rows:,}행 UPSERT"
            if failures:
                message += " | 보조 수집 실패: " + ", ".join(sorted(failures))
            db.finish_pipeline_run(
                connection, run_id, "success", message, price_rows, prediction_rows
            )
            return {
                "run_id": run_id, "status": "success",
                "price_rows": price_rows, "prediction_rows": prediction_rows,
                "source_failures": sorted(failures),
            }
        except Exception as exc:
            connection.rollback()
            message = f"파이프라인 처리 실패: {type(exc).__name__}"
            db.finish_pipeline_run(connection, run_id, "failed", message)
            raise RuntimeError(message) from None


def build_parser() -> argparse.ArgumentParser:
    config = yaml.safe_load((ROOT / "configs" / "sources.yaml").read_text())
    period = config["periods"]["backfill"]
    default_source_dir = ROOT / "data" / "processed" / f"{period['start']}_{period['end']}"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["backfill", "incremental", "news", "refresh"])
    parser.add_argument("--once", action="store_true", help="refresh: run one catch-up cycle and exit")
    parser.add_argument("--start", type=date.fromisoformat)
    parser.add_argument("--end", type=date.fromisoformat)
    parser.add_argument("--source-dir", type=Path, default=default_source_dir)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--sources", nargs="+", choices=SOURCE_GROUPS, default=list(SOURCE_GROUPS))
    parser.add_argument("--regions", nargs="+")
    parser.add_argument("--skip-ingestion", action="store_true")
    parser.add_argument("--database-url")
    parser.add_argument("--news-path", type=Path, help="독립 뉴스 Parquet (기본 source-dir/news/articles.parquet)")
    parser.add_argument("--intelligence-artifact", type=Path, help="검증 결과와 분류 모델을 담은 별도 artifact")
    parser.add_argument("--ingest-news", action="store_true", help="numeric 수집 여부와 독립적으로 뉴스 수집")
    parser.add_argument("--news-availability", choices=("live", "historical"), default="live",
                        help="live는 실제 수집 시각도 제한; historical은 공개/수정 시각 기반 연구 재생")
    parser.add_argument("--jev-cache", type=Path, default=ROOT / "data/jev/responses.json")
    parser.add_argument("--jev-limit", type=int, default=200, help="신규 Jev 요청 상한(재시도 포함, 최대 200)")
    parser.add_argument("--jev-source", choices=("market", "yahoo", "gdelt"), default="market")
    parser.add_argument("--jev-days", type=int, default=200, help="뉴스 수집 기간(달력일, 최대 200)")
    parser.add_argument("--jev-batch-size", type=int, default=20, help="요청당 독립 평가 기사 수(1~20)")
    parser.add_argument("--jev-candidates", type=Path, help="저장된 기사 후보로 일별 선정·분류 재개")
    parser.add_argument("--skip-news-collection", action="store_true", help="저장된 Jev 분석으로만 재실행")
    parser.add_argument("--jev-training-availability", choices=("research", "live"), default="research",
                        help="research는 사후 기사 재분류를 사용한 실험; 현재 추론은 항상 실제 가용 시각 적용")
    return parser


def run_news_pipeline(source_dir, artifact, end, *, cache_path, limit=200,
                      database_url=None, skip_collection=False, training_availability="research", source="market",
                      days=200, candidates_path=None, batch_size=20):
    """Collect decisions and store a separately issued, auditable news forecast."""
    from .jev import MODEL, PROMPT_VERSION, collect_and_classify, read_selected_records
    from .news_residual import fit_residual, predict_residual

    if not 1 <= limit <= 200:
        raise ValueError("jev-limit must be between 1 and 200")
    if not 1 <= days <= 200:
        raise ValueError("jev-days must be between 1 and 200")
    if not 1 <= batch_size <= 20:
        raise ValueError("jev-batch-size must be between 1 and 20")
    with db.connect(database_url) as connection:
        db.create_schema(connection)
        run_id = db.start_pipeline_run(connection, "news")
        try:
            start = end - timedelta(days=days - 1)
            if skip_collection:
                records = read_selected_records(cache_path, start, end)
                status = {"source_status": "cached", "classification_status": "cached",
                          "source_count": len(records), "api_attempts": 0,
                          "coverage_complete": False, "errors": []}
                status_path = Path(cache_path).with_suffix(Path(cache_path).suffix + ".status.json")
                stored_status = None
                if Path(cache_path).name == "responses.json":
                    from coffee_service import jev_store
                    stored_status = jev_store.read_document(Path(cache_path).parent / "news.json").get("service_status")
                elif status_path.exists():
                    stored_status = json.loads(status_path.read_text(encoding="utf-8"))
                if stored_status:
                    status.update(stored_status)
                    status["previous_api_attempts"] = status.get("api_attempts", 0)
                    status["api_attempts"] = 0
                    status["cache_replay"] = True
                    if "selected_days" in status:
                        selected_days = [day for day in status["selected_days"]
                                         if start.isoformat() <= day <= end.isoformat()]
                        status.update(selected_days=selected_days, selected_count=len(selected_days),
                                      selected_pending_count=max(0, len(selected_days) - len(records)))
                    status["selected_analysis_ids"] = [record["analysis_id"] for record in records]
                    status["requested_start"], status["requested_end"] = start.isoformat(), end.isoformat()
                    status["missing_days"] = [day for day in status.get("missing_days", [])
                                              if start.isoformat() <= day <= end.isoformat()]
                if not records and not stored_status:
                    raise ValueError("저장된 Jev 분석 또는 실행 상태가 없습니다.")
            else:
                records, status = collect_and_classify(cache_path, start, end, limit,
                    source=source, candidates_path=candidates_path, batch_size=batch_size)
            records = [record for record in records
                       if record["model"] == MODEL and record["prompt_version"] == PROMPT_VERSION]
            db.upsert_jev_analyses(connection, records)
            # Analysis survives numeric-source/model failure and can be reused without a new API call.
            connection.commit()
            as_of = pd.Timestamp.now(tz="UTC")
            from .refresh import source_lock
            with source_lock(source_dir):
                sources = sources_as_of(Path(source_dir), end)
            validate_macro_freshness(sources, end)
            dataset = assemble_features(sources)
            base_bundle = load_bundle(Path(artifact))
            base_predictions = generate_predictions(dataset, base_bundle)
            validate_latest_prediction_coverage(dataset, base_predictions)
            price_date = dataset.prices.loc[dataset.prices.close.notna()].index.max()
            latest = base_predictions.loc[pd.to_datetime(base_predictions.origin_date).eq(price_date)]
            residual = fit_residual(base_predictions, dataset.prices, records,
                                    as_of=as_of, availability=training_availability)
            forecasts = predict_residual(latest, dataset.prices, records, residual, as_of=as_of)
            failed = status["source_status"] == "failed" or status["classification_status"] == "failed"
            partial = status["classification_status"] == "partial"
            if failed:
                for forecast in forecasts:
                    forecast.update(adjusted_price=None, adjusted_return=None, news_correction=None,
                                    status="unavailable", reason="뉴스 수집 또는 분석에 실패했습니다.")
            document = {"run_id": run_id, "as_of": as_of.isoformat(),
                        "price_date": price_date.date().isoformat(),
                        "selected_analysis_ids": [record["analysis_id"] for record in records],
                        "training_availability": training_availability,
                        "source_status": status, "model": residual, "forecasts": forecasts}
            # The full residual model is already retained in the DB forecast document.
            db.store_news_forecast(connection, run_id, document)
            db.upsert_source_status(connection, [{"source": "jev_news", "status": "failed" if failed else "partial" if partial else "success",
                "last_data_date": max((pd.Timestamp(r["event_at"]).date() for r in records), default=None),
                "row_count": len(records), "error": "; ".join(status.get("errors", [])) or None}])
            result_status = "failed" if failed else "partial" if partial else "success"
            message = f"Jev 분석 {len(records)}건 | 신규 API 요청 {status['api_attempts']}회 | 뉴스 보정 {residual['status']}"
            db.finish_pipeline_run(connection, run_id, result_status, message, prediction_rows=len(forecasts))
            return {"run_id": run_id, "status": result_status, "price_rows": 0,
                    "prediction_rows": len(forecasts), "articles": len(records), "source_status": status}
        except Exception as exc:
            connection.rollback()
            db.finish_pipeline_run(connection, run_id, "failed", f"뉴스 파이프라인 실패: {type(exc).__name__}")
            raise RuntimeError(f"뉴스 파이프라인 실패: {type(exc).__name__}") from None


def main(argv=None) -> int:
    load_dotenv(ROOT / ".env")
    yf.set_tz_cache_location(str(ROOT / ".cache" / "yfinance"))
    parser = build_parser()
    args = parser.parse_args(argv)
    config = yaml.safe_load((ROOT / "configs" / "sources.yaml").read_text())
    period = config["periods"]["backfill"]
    start = args.start or date.fromisoformat(period["start"])
    end = args.end or (date.fromisoformat(period["end"]) if args.mode == "backfill" else date.today())
    if start > end:
        parser.error("start는 end보다 늦을 수 없습니다.")
    from . import refresh
    if args.mode == "refresh":
        if args.end or args.skip_ingestion or args.skip_news_collection:
            parser.error("refresh uses the last completed NY date and collects all sources")
        if args.jev_cache.name != "responses.json":
            parser.error("refresh requires the unified responses.json archive")
        return refresh.serve(args.source_dir, args.artifact, args.jev_cache.parent, start,
                             once=args.once, database_url=args.database_url)
    if args.once:
        parser.error("--once is only valid with refresh")
    try:
        result = _run_command(args, start, end)
    except Exception as exc:
        print(f"실패: {type(exc).__name__}", flush=True)
        return 1
    label = {"success": "완료", "partial": "부분 완료", "failed": "실패"}.get(result["status"], result["status"])
    print(f"{label}: {result['run_id']} | 가격 {result['price_rows']:,}행 | 예측 {result['prediction_rows']:,}행", flush=True)
    return 0 if result["status"] == "success" else 1


def _run_command(args, start, end):
    from .refresh import source_lock
    if args.mode == "news":
        if not args.skip_ingestion:
            with source_lock(args.source_dir):
                run_pipeline("incremental", args.source_dir, args.artifact, start, end,
                             selected_groups=("yahoo", "fred"), database_url=args.database_url)
        result = run_news_pipeline(args.source_dir, args.artifact, end,
            cache_path=args.jev_cache, limit=args.jev_limit, database_url=args.database_url,
            skip_collection=args.skip_news_collection,
            training_availability=args.jev_training_availability, source=args.jev_source,
            days=args.jev_days, candidates_path=args.jev_candidates, batch_size=args.jev_batch_size)
    else:
        with source_lock(args.source_dir):
            result = run_pipeline(
                args.mode, args.source_dir, args.artifact, start, end,
                selected_groups=args.sources, selected_regions=args.regions,
                skip_ingestion=args.skip_ingestion, database_url=args.database_url,
                news_path=args.news_path, intelligence_artifact=args.intelligence_artifact,
                ingest_news_source=args.ingest_news, news_availability=args.news_availability,
            )
    return result


if __name__ == "__main__":
    raise SystemExit(main())
