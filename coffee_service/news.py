"""Daily Coffee News 메타데이터 수집과 시점 안전 뉴스 피처."""

from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta, timezone
from hashlib import sha256
from html.parser import HTMLParser
from pathlib import Path
import re
import time
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import pandas as pd
import requests


DEFAULT_ENDPOINT = "https://dailycoffeenews.com/wp-json/wp/v2/posts"
SOURCE = "daily_coffee_news"
RIGHTS = ("Daily Coffee News Terms of Service: personal, non-commercial copies only; "
          "source copyright retained; no redistribution or full body stored.")
ANALYSIS_VERSION = "title-rules-v2"
TOPICS = ("weather", "production", "supply", "inventory", "export", "currency", "demand", "logistics", "policy", "other")
ARTICLE_COLUMNS = (
    "article_id", "source", "title", "summary", "published_at", "collected_at", "url", "language", "modified_at", "available_at", "rights", "coffee_relevance", "topic", "price_impact", "confidence", "analysis_version",
)
TRACKING_PARAMS = {"fbclid", "gclid", "mc_cid", "mc_eid"}


class _TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []

    def handle_data(self, data):
        self.parts.append(data)


def _title_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(str(value))
    return " ".join("".join(parser.parts).split())


def _utc(value, field: str) -> pd.Timestamp:
    try:
        timestamp = pd.Timestamp(value)
    except (TypeError, ValueError, OverflowError):
        raise ValueError(f"{field} must be a valid timezone-aware timestamp") from None
    if pd.isna(timestamp) or timestamp.tzinfo is None:
        raise ValueError(f"{field} must include a timezone")
    return timestamp.tz_convert("UTC")


def _normalize_url(value: str) -> str:
    parsed = urlsplit(str(value).strip())
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise ValueError("url must be an absolute http(s) URL")
    query = urlencode([(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                       if not key.lower().startswith("utm_") and key.lower() not in TRACKING_PARAMS])
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, query, ""))


def _article_id(url: str) -> str:
    return sha256(url.encode("utf-8")).hexdigest()


def analyze_title(title: str, language: str = "en") -> dict:
    """Apply transparent title-only rules; this is not general sentiment analysis."""
    text = re.sub(r"[^a-z0-9 ]+", " ", title.lower())
    words = set(text.split())
    if language.lower() != "en" or not re.search(r"[a-z]", text):
        return {"coffee_relevance": 0.0, "topic": "other", "price_impact": "neutral", "confidence": 0.1}
    coffee = bool(words & {"coffee", "arabica", "robusta"})
    if not coffee:
        return {"coffee_relevance": 0.0, "topic": "other", "price_impact": "neutral", "confidence": 0.1}
    keyword_topics = (
        ("weather", {"drought", "rain", "rainfall", "frost", "weather", "climate", "storm"}),
        ("production", {"harvest", "crop", "yield", "production", "growers"}),
        ("supply", {"supply", "shortage", "shortfall", "availability"}),
        ("inventory", {"inventory", "inventories", "stockpile", "stocks"}),
        ("export", {"export", "exports", "shipment", "shipments"}),
        ("currency", {"currency", "real", "dollar", "exchange"}),
        ("demand", {"demand", "consumption", "consumers", "roasters"}),
        ("logistics", {"freight", "port", "shipping", "logistics", "container"}),
        ("policy", {"tariff", "policy", "regulation", "law", "ban"}),
    )
    topic = next((name for name, terms in keyword_topics if words & terms), "other")

    def has(terms):
        return any(re.search(rf"(?<!\w)(?:not|no|without)(?:\s+\w+){{0,2}}\s+{term}(?!\w)", text) is None
                   and re.search(rf"(?<!\w){term}(?!\w)", text) for term in terms)

    lower_supply = {"cut", "cuts", "reduced", "reduce", "decrease", "decreases", "fall", "falls", "shortage", "shortfall", "drought", "frost", "disruption", "delay", "delays"}
    higher_supply = {"bumper", "abundant", "surplus", "increase", "increases", "grow", "grows", "growing", "recovery", "relief", "surge", "surges", "surged"}
    demand_up = {"increase", "increases", "grow", "grows", "growing", "rise", "rises", "surge", "surges"}
    demand_down = {"cut", "cuts", "reduced", "reduce", "decrease", "decreases", "fall", "falls", "drop", "drops"}
    production_terms = r"(?:production|crop|harvest|yield|output)"
    record_production = bool(
        re.search(rf"\brecord(?:\s+\w+){{0,3}}\s+{production_terms}\b", text)
        or re.search(rf"\b{production_terms}(?:\s+\w+){{0,3}}\s+record(?:\s+high)?\b", text)
    )
    signals = []
    if topic in {"weather", "production", "supply", "logistics", "export"}:
        signals.extend([1] if has(lower_supply) else [])
        signals.extend([-1] if has(higher_supply) else [])
        # A record crop/output is a supply increase.  Do not treat record prices
        # as supply, and let an explicit disruption such as drought cuts prevail.
        signals.extend([-1] if record_production and not has(lower_supply) else [])
    elif topic == "inventory":
        signals.extend([1] if has(demand_down) else [])
        signals.extend([-1] if has(demand_up) else [])
    elif topic == "demand":
        signals.extend([1] if has(demand_up) else [])
        signals.extend([-1] if has(demand_down) else [])
    elif topic == "currency" and has({"strong", "higher"}) and "real" in words:
        signals.append(1)
    if has(lower_supply) and has(higher_supply) and "relief" not in words:
        signals = []
    elif "relief" in words and ("drought" in words or "frost" in words):
        signals = [-1]
    impact = "bullish" if signals == [1] else "bearish" if signals == [-1] else "neutral"
    confidence = 0.8 if impact != "neutral" else 0.55 if topic != "other" else 0.35
    return {"coffee_relevance": 1.0, "topic": topic, "price_impact": impact, "confidence": confidence}


def _deduplicate_articles(frame: pd.DataFrame) -> pd.DataFrame:
    """Keep the first collected URL version and earliest available title duplicate."""
    if frame.empty:
        return frame
    attrs = dict(frame.attrs)
    frame = frame.sort_values(["collected_at", "article_id"]).drop_duplicates("article_id", keep="first")
    frame["_title_key"] = frame.title.str.casefold().str.replace(r"[^\w]+", "", regex=True)
    frame = frame.sort_values(["_title_key", "available_at", "collected_at", "article_id"])
    keep, last_by_title = [], {}
    for index, row in frame.iterrows():
        previous = last_by_title.get(row["_title_key"])
        if previous is None or row["available_at"] - previous > pd.Timedelta(hours=24):
            keep.append(index)
            last_by_title[row["_title_key"]] = row["available_at"]
    result = frame.loc[keep].drop(columns="_title_key").sort_values(["published_at", "article_id"]).reset_index(drop=True)
    result.attrs.update(attrs)
    return result


def normalize_articles(records, collected_at=None, *, parse_html=True) -> pd.DataFrame:
    """Normalize raw source records; callers must pass HTML titles only once."""
    collected = _utc(collected_at or datetime.now(timezone.utc), "collected_at")
    rows = []
    for record in records:
        url = _normalize_url(record["url"])
        title = _title_text(record["title"]) if parse_html else " ".join(str(record["title"]).split())
        if not title:
            raise ValueError("title must not be empty")
        published = _utc(record["published_at"], "published_at")
        modified = _utc(record.get("modified_at") or record["published_at"], "modified_at")
        article_collected = _utc(record.get("collected_at", collected), "collected_at")
        if published > article_collected:
            raise ValueError("published_at must not be after collected_at")
        language = record.get("language", "en")
        analysis = analyze_title(title, language)
        rows.append({
            "article_id": _article_id(url), "source": record.get("source", SOURCE), "title": title,
            "summary": "", "published_at": published, "collected_at": article_collected,
            "url": url, "language": language, "modified_at": modified, "available_at": max(published, modified),
            "rights": record.get("rights", RIGHTS), **analysis, "analysis_version": ANALYSIS_VERSION,
        })
    frame = pd.DataFrame(rows, columns=ARTICLE_COLUMNS)
    if frame.empty:
        return _empty_articles()
    return _deduplicate_articles(frame)


def _empty_articles() -> pd.DataFrame:
    frame = pd.DataFrame(columns=ARTICLE_COLUMNS)
    for column in ("published_at", "collected_at", "modified_at", "available_at"):
        frame[column] = pd.Series(dtype="datetime64[ns, UTC]")
    for column in ("coffee_relevance", "confidence"):
        frame[column] = pd.Series(dtype=float)
    return frame


def fetch_wordpress(session, start, end, endpoint=DEFAULT_ENDPOINT) -> pd.DataFrame:
    """Fetch all Daily Coffee News post metadata in the inclusive date interval."""
    start, end = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    if start > end:
        raise ValueError("start must not be after end")
    start_at = pd.Timestamp(start, tz="UTC")
    end_at = pd.Timestamp(end + timedelta(days=1), tz="UTC")
    records, seen_ids, page, pages, total, quarantined = [], set(), 1, None, None, 0
    while pages is None or page <= pages:
        params = {"after": f"{start - timedelta(days=1)}T00:00:00", "before": f"{end + timedelta(days=2)}T00:00:00", "per_page": 100, "page": page, "orderby": "date", "order": "asc", "_fields": "id,date_gmt,modified_gmt,link,title"}
        response = None
        for attempt in range(3):
            try:
                response = session.get(endpoint, params=params, timeout=(10, 30))
                response.raise_for_status()
                break
            except requests.RequestException:
                if attempt == 2:
                    raise RuntimeError("Daily Coffee News WordPress request failed") from None
                time.sleep(attempt + 1)
        payload = response.json()
        try:
            current_pages = int(response.headers["X-WP-TotalPages"])
            current_total = int(response.headers["X-WP-Total"])
        except (KeyError, TypeError, ValueError):
            raise ValueError("WordPress pagination header is missing") from None
        if pages is None:
            pages, total = current_pages, current_total
        elif pages != current_pages or total != current_total:
            raise ValueError("WordPress pagination changed during fetch")
        if not isinstance(payload, list) or (total and not payload):
            raise ValueError("WordPress payload is not a post list")
        page_ids = {item.get("id") for item in payload}
        if None in page_ids or len(page_ids) != len(payload) or seen_ids.intersection(page_ids):
            raise ValueError("WordPress pagination contains duplicate posts")
        seen_ids.update(page_ids)
        for item in payload:
            title = _title_text(item["title"]["rendered"])
            if not title:
                quarantined += 1
                continue
            records.append({"source": SOURCE, "url": item["link"], "title": title, "published_at": item["date_gmt"] + "+00:00", "modified_at": item["modified_gmt"] + "+00:00", "language": "en", "rights": RIGHTS})
        if page % 10 == 0 or page == pages:
            print(f"Daily Coffee News: page {page}/{pages} ({len(seen_ids)}/{total} posts)", flush=True)
        if page < pages:
            time.sleep(3)
        page += 1
    if len(seen_ids) != total:
        raise ValueError("WordPress pagination is incomplete")
    if quarantined:
        print(f"Daily Coffee News: quarantined {quarantined} empty-title posts", flush=True)
    # Titles are already decoded above.  Do not interpret literal angle brackets again.
    frame = normalize_articles(records, parse_html=False)
    return frame.loc[(frame.published_at >= start_at) & (frame.published_at < end_at)].reset_index(drop=True)


def read_news(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        return _empty_articles()
    frame = pd.read_parquet(path)
    attrs = dict(frame.attrs)
    # Analysis values are derived and can be migrated without touching raw
    # source metadata saved by an earlier title-rule version.
    if "analysis_version" not in frame.columns:
        frame["analysis_version"] = "legacy"
    missing = set(ARTICLE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"news file misses columns: {sorted(missing)}")
    result = _validate_normalized_articles(frame)
    result.attrs.update(attrs)
    return result


def _coverage_range(attrs: dict) -> tuple[date, date] | None:
    start, end = attrs.get("coverage_start"), attrs.get("coverage_end")
    if start is None or end is None:
        return None
    try:
        start_date, end_date = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    except (TypeError, ValueError, OverflowError):
        return None
    return (start_date, end_date) if start_date <= end_date else None


def _set_coverage_attrs(frame: pd.DataFrame, existing_attrs: dict, start: date, end: date) -> pd.DataFrame:
    """Record only one contiguous successfully fetched coverage interval."""
    previous = _coverage_range(existing_attrs)
    if previous is None:
        coverage_start, coverage_end = start, end
    elif start <= previous[1] + timedelta(days=1) and previous[0] <= end + timedelta(days=1):
        coverage_start, coverage_end = min(previous[0], start), max(previous[1], end)
    else:
        coverage_start, coverage_end = previous
    frame.attrs.update({
        "coverage_start": coverage_start.isoformat(),
        "coverage_end": coverage_end.isoformat(),
        "last_collected_at": _utc(datetime.now(timezone.utc), "last_collected_at").isoformat(),
    })
    return frame


def ingest_news(path, start, end, mode="backfill") -> pd.DataFrame:
    """Atomically persist metadata; incremental runs retain the first collected version."""
    if mode not in {"backfill", "incremental"}:
        raise ValueError("mode must be backfill or incremental")
    path = Path(path)
    requested_start, requested_end = pd.Timestamp(start).date(), pd.Timestamp(end).date()
    if requested_start > requested_end:
        raise ValueError("start must not be after end")
    existing = read_news(path)
    fetch_start = requested_start
    coverage = _coverage_range(existing.attrs)
    if mode == "incremental" and coverage is not None and requested_start >= coverage[0]:
        # Re-fetch a short overlap so a single contiguous coverage interval can
        # be extended only after a complete request succeeds.  A pipeline that
        # always supplies its historical start therefore does not re-fetch it.
        last_published = existing.published_at.max().date() if not existing.empty else coverage[1]
        overlap_start = min(coverage[1], last_published) - timedelta(days=7)
        fetch_start = max(requested_start, overlap_start)
    with requests.Session() as session:
        incoming = fetch_wordpress(session, fetch_start, requested_end)
    merged = _validate_normalized_articles(pd.concat([existing, incoming], ignore_index=True))
    _set_coverage_attrs(merged, existing.attrs, fetch_start, requested_end)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        merged.to_parquet(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return merged


def news_feature_columns(window) -> list[str]:
    suffix = f"_{int(window)}d"
    columns = [f"news_{name}{suffix}" for name in ("count", "relevant_count", "bullish_ratio", "bearish_ratio", "mean_impact", "weighted_impact", "time_decay", "count_confidence")]
    return columns + [f"news_{topic}_{metric}{suffix}" for topic in TOPICS for metric in ("count", "impact")]


def aggregate_news(articles, sessions, windows, collected_bound=None) -> pd.DataFrame:
    sessions = pd.DatetimeIndex(pd.to_datetime(sessions)).tz_localize(None)
    windows = tuple(int(window) for window in windows)
    result = pd.DataFrame(0.0, index=sessions, columns=[name for window in windows for name in news_feature_columns(window)])
    if not len(sessions) or articles is None or len(articles) == 0:
        return result
    frame = read_news_frame(articles)
    bound = None if collected_bound is None else _utc(collected_bound, "collected_bound")
    impact = frame.price_impact.map({"bullish": 1.0, "bearish": -1.0, "neutral": 0.0}).fillna(0.0)
    for session in sessions:
        asof = pd.Timestamp(session, tz="UTC") + pd.Timedelta(hours=23)
        visible = frame.loc[(frame.available_at <= asof) & (frame.published_at <= asof)]
        if bound is not None:
            visible = visible.loc[visible.collected_at <= min(bound, asof)]
        for window in windows:
            subset = visible.loc[visible.published_at > asof - pd.Timedelta(days=window)].copy()
            if subset.empty:
                continue
            values = impact.loc[subset.index]
            relevant = subset.coffee_relevance > 0
            age_days = (asof - subset.published_at).dt.total_seconds() / 86400
            decay = age_days.map(lambda age: 2.718281828459045 ** (-age / window))
            suffix = f"_{window}d"
            result.loc[session, f"news_count{suffix}"] = len(subset)
            result.loc[session, f"news_relevant_count{suffix}"] = relevant.sum()
            relevant_values = values.loc[relevant]
            result.loc[session, f"news_bullish_ratio{suffix}"] = (relevant_values == 1).mean() if len(relevant_values) else 0.0
            result.loc[session, f"news_bearish_ratio{suffix}"] = (relevant_values == -1).mean() if len(relevant_values) else 0.0
            result.loc[session, f"news_mean_impact{suffix}"] = values.mean()
            result.loc[session, f"news_time_decay{suffix}"] = decay.mean()
            result.loc[session, f"news_count_confidence{suffix}"] = subset.confidence.sum()
            weights = subset.confidence * decay
            result.loc[session, f"news_weighted_impact{suffix}"] = 0.0 if decay.sum() == 0 else (values * weights).sum() / decay.sum()
            for topic in TOPICS:
                topic_values = values.loc[subset.topic.eq(topic)]
                result.loc[session, f"news_{topic}_count{suffix}"] = len(topic_values)
                result.loc[session, f"news_{topic}_impact{suffix}"] = topic_values.mean() if len(topic_values) else 0.0
    return result


def read_news_frame(articles) -> pd.DataFrame:
    frame = articles.copy()
    missing = set(ARTICLE_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"articles miss columns: {sorted(missing)}")
    for column in ("published_at", "collected_at", "modified_at", "available_at"):
        frame[column] = frame[column].map(lambda value: _utc(value, column))
    if (frame.published_at > frame.collected_at).any():
        raise ValueError("published_at must not be after collected_at")
    if (frame.available_at < frame[["published_at", "modified_at"]].max(axis=1)).any():
        raise ValueError("available_at must be at least published_at and modified_at")
    for column in ("coffee_relevance", "confidence"):
        values = pd.to_numeric(frame[column], errors="raise")
        if values.isna().any() or (~values.between(0, 1)).any():
            raise ValueError(f"{column} must be between 0 and 1")
        frame[column] = values
    return frame


def _apply_analysis(frame: pd.DataFrame) -> pd.DataFrame:
    """Refresh only deterministic derived title fields; raw source fields stay immutable."""
    if frame.empty:
        frame["analysis_version"] = pd.Series(dtype="object")
        return frame
    analyses = [analyze_title(title, language) for title, language in zip(frame.title, frame.language)]
    analysis_frame = pd.DataFrame(analyses, index=frame.index)
    for column in ("coffee_relevance", "topic", "price_impact", "confidence"):
        frame[column] = analysis_frame[column]
    frame["analysis_version"] = ANALYSIS_VERSION
    return frame


def _validate_normalized_articles(articles) -> pd.DataFrame:
    """Validate raw fields, then refresh title-derived values without HTML parsing."""
    frame = read_news_frame(articles)
    if frame.title.isna().any() or frame.title.map(lambda value: not str(value).strip()).any():
        raise ValueError("title must not be empty")
    if frame.url.map(_normalize_url).tolist() != frame.url.tolist():
        raise ValueError("stored URL must be normalized")
    expected_ids = frame.url.map(_article_id)
    if not frame.article_id.eq(expected_ids).all():
        raise ValueError("article_id does not match URL")
    return _deduplicate_articles(_apply_analysis(frame))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--mode", choices=("backfill", "incremental"), default="backfill")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("start must not be after end")
    print(f"saved {len(ingest_news(args.output, args.start, args.end, args.mode))} news articles")


if __name__ == "__main__":
    main()
