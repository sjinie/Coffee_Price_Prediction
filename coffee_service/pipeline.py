"""External source부터 PostgreSQL prediction까지 실행하는 Local E2E pipeline."""

from __future__ import annotations

import argparse
from datetime import date, timedelta
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
    parser.add_argument("mode", choices=["backfill", "incremental"])
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
    return parser


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
    try:
        result = run_pipeline(
            args.mode, args.source_dir, args.artifact, start, end,
            selected_groups=args.sources, selected_regions=args.regions,
            skip_ingestion=args.skip_ingestion, database_url=args.database_url,
            news_path=args.news_path, intelligence_artifact=args.intelligence_artifact,
            ingest_news_source=args.ingest_news, news_availability=args.news_availability,
        )
    except Exception as exc:
        print(f"실패: {type(exc).__name__}", flush=True)
        return 1
    print(
        f"완료: {result['run_id']} | 가격 {result['price_rows']:,}행 | "
        f"예측 {result['prediction_rows']:,}행",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
