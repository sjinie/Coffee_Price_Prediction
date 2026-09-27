"""Checkpointed metadata candidates for a separate historical Jev research run."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
import json
from pathlib import Path
import re
import time
import tempfile
import xml.etree.ElementTree as ET

import pandas as pd
import requests

from coffee_service import jev


START = date(2022, 1, 1)
END = date(2025, 12, 31)
NEWS_FILE = Path(__file__).resolve().parents[1] / "data/processed/2014-07-01_2025-12-31/news/articles.parquet"
RSS_URL = "https://news.google.com/rss/search"
QUERIES = (
    "coffee (price OR harvest OR crop OR supply OR drought OR frost OR export)",
    "(Brazil OR Colombia) (agriculture OR farming OR drought OR rainfall OR fertilizer OR coffee)",
)
AGRICULTURE = {"agriculture", "agricultural", "farming", "farmer", "farmers", "crop", "crops", "harvest", "production", "drought", "rainfall", "frost", "fertilizer", "exports", "export", "supply"}
COFFEE = {"coffee", "arabica", "robusta"}
COUNTRIES = {"brazil", "brazilian", "colombia", "colombian"}
EXCLUDE = {"cafe", "cafes", "café", "cafés", "espresso", "grinder", "equipment", "machine", "machines", "health", "diet", "starbucks", "shares", "earnings", "nasdaq", "nyse", "otcpk", "stock", "stocks"}


def _rss_items(xml: bytes, collected_at: str) -> list[dict]:
    root = ET.fromstring(xml)
    channel = root.find("channel")
    if root.tag != "rss" or channel is None:
        raise ValueError("Google News RSS response is not an RSS channel")
    records = []
    for item in channel.findall("item"):
        title, link, pub_date = (item.findtext(name) for name in ("title", "link", "pubDate"))
        if not title or not link or not pub_date:
            raise ValueError("Google News RSS item misses required metadata")
        published = parsedate_to_datetime(pub_date)
        if published.tzinfo is None:
            raise ValueError("Google News RSS pubDate must have a timezone")
        publisher = item.findtext("source") or ""
        title = re.sub(r"\s+-\s+" + re.escape(publisher) + r"$", "", title).strip() if publisher else title.strip()
        records.append({"title": title, "url": link, "source": "google_news_rss", "publisher": publisher,
                        "summary": "", "language": "en", "published_at": published.astimezone(timezone.utc).isoformat(),
                        "collected_at": collected_at})
    return records


def _fetch_rss(session: requests.Session, month: date, query: str, collected_at: str) -> list[dict]:
    next_month = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
    params = {"q": f"{query} after:{month - timedelta(days=1)} before:{next_month + timedelta(days=1)}",
              "hl": "en-US", "gl": "US", "ceid": "US:en"}
    for attempt in range(3):
        try:
            response = session.get(RSS_URL, params=params, timeout=(10, 30))
            response.raise_for_status()
            break
        except requests.RequestException:
            if attempt == 2:
                raise RuntimeError("Historical RSS source request failed") from None
            time.sleep(60)
    return _rss_items(response.content, collected_at)


def _read_checkpoint(path: Path) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or any(not isinstance(item, dict) for item in records):
        raise ValueError(f"invalid source checkpoint: {path.name}")
    return records


def _relevance(item: dict) -> tuple[int, str] | None:
    words = jev._tokens(item["title"] + " " + item.get("summary", ""))
    if words & EXCLUDE:
        return None
    score, reason = jev._market_rank(item)
    if score >= 0:
        return score, reason
    if words & COFFEE and words & AGRICULTURE:
        return 2, "coffee_agriculture"
    if words & COUNTRIES and words & AGRICULTURE:
        return 1, "producer_agriculture"
    return None


def select_historical(records: list[dict], now: datetime | None = None) -> tuple[list[dict], dict]:
    """Choose one article per completed NY date, deduplicating in publication order."""
    today = (now or datetime.now(timezone.utc)).astimezone(jev.NY_TZ).date()
    candidates = []
    rejected = 0
    for raw in records:
        try:
            item = jev._article_fields(raw)
            day = jev._as_datetime(item["event_at"]).astimezone(jev.NY_TZ).date()
            if not START <= day <= END or day >= today:
                continue
            midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), jev.NY_TZ).astimezone(timezone.utc)
            # An edited current version cannot stand in for its earlier publication-day text.
            if item["modified_at"] and jev._as_datetime(item["modified_at"]) > midnight:
                rejected += 1
                continue
            rank = _relevance(item)
            if rank is not None:
                candidates.append((day, item, rank))
        except (KeyError, TypeError, ValueError):
            rejected += 1
    seen_urls: dict[str, set[str]] = {}
    seen_content: set[str] = set()
    prior: list[dict] = []
    by_day: dict[date, list[dict]] = {}
    duplicates = {"url": 0, "content": 0, "title": 0, "near": 0}
    for day, item, (score, reason) in sorted(candidates, key=lambda row: (row[1]["event_at"], row[1]["article_id"])):
        title_key = jev._title_key(item["title"])
        if item["content_hash"] in seen_urls.get(item["url"], set()):
            duplicates["url"] += 1
            continue
        if item["content_hash"] in seen_content:
            duplicates["content"] += 1
            continue
        match = next((earlier for earlier in prior if jev._near_duplicate(item, earlier)), None)
        if match is not None:
            duplicates["title" if jev._title_key(match["title"]) == title_key else "near"] += 1
            continue
        seen_urls.setdefault(item["url"], set()).add(item["content_hash"])
        seen_content.add(item["content_hash"])
        prior.append(item)
        item.update(selection_score=score, selection_reason=reason, selection_policy="historical-ny-daily-v1",
                    selection_date=day.isoformat())
        by_day.setdefault(day, []).append(item)
    selected = []
    for day in sorted(by_day):
        item = max(by_day[day], key=lambda value: (value["selection_score"], value["event_at"], value["article_id"]))
        midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), jev.NY_TZ).astimezone(timezone.utc)
        item["selection_available_at"] = midnight.isoformat().replace("+00:00", "Z")
        item["available_at"] = max(value for value in (item["published_at"], item["modified_at"], item["discovered_at"], item["collected_at"], item["selection_available_at"]) if value)
        selected.append(item)
    days = {item["selection_date"] for item in selected}
    missing = [(START + timedelta(days=offset)).isoformat() for offset in range((min(END, today - timedelta(days=1)) - START).days + 1)
               if (START + timedelta(days=offset)).isoformat() not in days] if today > START else []
    return selected, {"selected_count": len(selected), "missing_days": missing, "duplicates": duplicates,
                      "rejected_invalid_or_late_modified": rejected,
                      "coverage_limit": "Google News RSS is capped at 100 results per query; missing days are not proof of no news"}


def prepare_historical(output_dir: Path) -> list[dict]:
    """Resume source retrieval and freeze selection after every source succeeds.

    The caller owns the collection lock. No Jev requests or serving writes occur here.
    """
    output_dir = Path(output_dir)
    selected_path = output_dir / "selected.json"
    if selected_path.exists():
        return _read_checkpoint(selected_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    source_dir = output_dir / "sources"
    source_dir.mkdir(exist_ok=True)
    status_path = output_dir / "selected.status.json"
    source_records = []
    try:
        wordpress_path = source_dir / "wordpress.json"
        if wordpress_path.exists():
            source_records.extend(_read_checkpoint(wordpress_path))
        elif NEWS_FILE.exists():
            frame = pd.read_parquet(NEWS_FILE, columns=["url", "title", "summary", "source", "language", "published_at", "modified_at", "collected_at"])
            frame = frame.loc[(frame.published_at.dt.date >= START) & (frame.published_at.dt.date <= END + timedelta(days=1))]
            articles = [{key: value.isoformat() if isinstance(value, pd.Timestamp) else value for key, value in row.items()}
                        for row in frame.to_dict("records")]
            jev._write_records(wordpress_path, articles)
            source_records.extend(articles)
        else:
            from coffee_service import news
            with requests.Session() as session:
                frame = news.fetch_wordpress(session, START - timedelta(days=1), END + timedelta(days=1))
            articles = [{key: value.isoformat() if isinstance(value, pd.Timestamp) else value for key, value in row.items()}
                        for row in frame.to_dict("records")]
            jev._write_records(wordpress_path, articles)
            source_records.extend(articles)
        capped = []
        with requests.Session() as session:
            for year in range(START.year, END.year + 1):
                for month_number in range(1, 13):
                    month = date(year, month_number, 1)
                    for index, query in enumerate(QUERIES):
                        path = source_dir / f"rss-{year}-{month_number:02d}-{index}.json"
                        if path.exists():
                            articles = _read_checkpoint(path)
                        else:
                            articles = _fetch_rss(session, month, query, jev._now())
                            jev._write_records(path, articles)
                            print(f"Historical RSS {year}-{month_number:02d}/{index}: {len(articles)}", flush=True)
                            time.sleep(1)
                        if len(articles) >= 100:
                            capped.append(path.name)
                        source_records.extend(articles)
        selected, summary = select_historical(source_records)
        summary.update(source_status="complete", wordpress_count=len(_read_checkpoint(wordpress_path)),
                       rss_capped_queries=capped, candidate_count=len(source_records))
        jev._write_records(status_path, [summary])
        jev._write_records(selected_path, selected)
        return selected
    except Exception as exc:
        jev._write_records(status_path, [{"source_status": "failed", "error": str(exc),
                                          "candidate_count_before_failure": len(source_records)}])
        raise


if __name__ == "__main__":
    from coffee_service import jev_store
    name = "validation-2022-2025"
    with jev._cache_lock(jev_store.DATA / "responses.json"):
        document = jev_store.read_document(jev_store.DATA / "news.json")
        selected = document.get("selections", {}).get(name)
        if selected is None:
            with tempfile.TemporaryDirectory(prefix="coffee-sources-") as temporary:
                output_dir = Path(temporary)
                for source, value in document.get("sources", {}).items():
                    if source.startswith(name + "/sources/"):
                        jev._write_records(output_dir / "sources" / Path(source).name, value)
                try:
                    selected = prepare_historical(output_dir)
                    document.setdefault("selections", {})[name] = selected
                finally:
                    for path in (output_dir / "sources").glob("*.json"):
                        document.setdefault("sources", {})[name + "/sources/" + path.name] = json.loads(path.read_text())
                    jev_store.write_document(jev_store.DATA / "news.json", document)
    print(f"Historical selection ready: {len(selected)} articles", flush=True)
