"""Resume metadata collection into the four-file Jev archive; never call Jev."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
from pathlib import Path
from uuid import uuid4

import requests

from coffee_service import jev, jev_store, news, news_backfill_sources as sources


def _initial_coverage(document: dict, end: date, start: date | None) -> date:
    metadata = document["selection_metadata"]
    legacy, requested = [], []
    for key, value in metadata.items():
        if not isinstance(value, dict):
            continue
        if key.startswith("year-") and key.endswith("/selection-summary.json") and key.split("/")[0] in document["selections"]:
            legacy.append(date.fromisoformat(value["end"]))
        elif key != "incremental" and value.get("requested_end") and value.get("written_at"):
            written_day = jev._as_datetime(value["written_at"]).astimezone(jev.NY_TZ).date()
            requested.append(min(date.fromisoformat(value["requested_end"]), written_day - timedelta(days=1)))
    baseline = max(legacy) if legacy else max(requested) if requested else (start or end - timedelta(days=364)) - timedelta(days=1)
    return max(baseline, start - timedelta(days=1)) if start else baseline


def _select(records: list[dict], document: dict, start: date, end: date, collected_at: str,
            capped_days: set[date]) -> list[dict]:
    frozen = [row for group in document["selections"].values() for row in group]
    selected = []
    candidates = []
    today = datetime.now(timezone.utc).astimezone(jev.NY_TZ).date()
    for raw in records:
        try:
            item = jev._article_fields(raw, collected_at)
            day = jev._as_datetime(item["event_at"]).astimezone(jev.NY_TZ).date()
            if not start <= day <= end or day >= today:
                continue
            midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), jev.NY_TZ).astimezone(timezone.utc)
            if item["modified_at"] and jev._as_datetime(item["modified_at"]) > midnight:
                continue
            rank = sources._relevance(item)
            if rank is not None:
                candidates.append((day, item, rank))
        except (KeyError, TypeError, ValueError):
            continue
    counts = {}
    for item in frozen:
        day = item.get("selection_date")
        if day:
            counts[day] = counts.get(day, 0) + 1
    seen = {item["content_hash"] for item in frozen}
    for day, item, (score, reason) in sorted(candidates, key=lambda row: (row[0], -row[2][0], row[1]["event_at"], row[1]["article_id"])):
        key = day.isoformat()
        if counts.get(key, 0) >= (2 if day >= date(2025, 9, 26) else 1):
            continue
        if item["content_hash"] in seen or any(jev._near_duplicate(item, old) for old in frozen + selected):
            continue
        midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), jev.NY_TZ).astimezone(timezone.utc)
        available = midnight.isoformat().replace("+00:00", "Z")
        item.update(selection_score=score, selection_reason=reason, selection_policy="incremental-ny-daily-v1",
                    selection_date=key, selection_available_at=available)
        if day in capped_days:
            item["selection_limited"] = True
        item["available_at"] = max(value for value in (item["published_at"], item["modified_at"],
                                     item["discovered_at"], item["collected_at"], available) if value)
        selected.append(item)
        seen.add(item["content_hash"])
        counts[key] = counts.get(key, 0) + 1
    return selected


def _checkpoint(document: dict, status: dict) -> None:
    """Merge under the archive lock so a slower collector cannot rewind progress."""
    previous = document["selection_metadata"].get("incremental", {})
    capped = sorted(set(previous.get("capped_days", [])) | set(status["capped_days"]))
    requested = max(previous.get("requested_end", ""), status["requested_end"])
    if previous.get("coverage_end", "") > status["coverage_end"]:
        status.update({key: previous[key] for key in ("coverage_end", "source_status", "errors")})
    if previous.get("source_status") == "failed" and previous["requested_end"] > status["coverage_end"]:
        status.update(source_status="failed", errors=previous["errors"])
    gaps = list(previous.get("source_gaps", []))
    gaps += [gap for gap in status["source_gaps"] if gap not in gaps]
    status.update(requested_end=requested, capped_days=capped, limited=bool(capped), source_gaps=gaps)
    if capped and status["source_status"] == "success":
        status["source_status"] = "partial"
    document["selection_metadata"]["incremental"] = status.copy()


# Source-side failures: exhausted requests and malformed responses (JSON, pagination,
# Yahoo payload checks). Other exceptions are code bugs and must fail the collection.
SOURCE_ERRORS = (requests.RequestException, RuntimeError, ValueError)


def _gap(source: str, first: date, last: date, exc: Exception) -> dict:
    """Describe a failed source query without its URL, query string, or response body."""
    if isinstance(exc, news.SourceRequestError):
        error = f"HTTP {exc.status}" if exc.status else "connection failed"
    else:
        error = type(exc).__name__
    return {"source": source, "start": first.isoformat(), "end": last.isoformat(), "error": error}


def _message(gap: dict) -> str:
    return f"{gap['source']} {gap['start']}~{gap['end']}: {gap['error']}"


def collect_pending(data: Path, end: date, *, start: date | None = None, session=None) -> dict:
    """Checkpoint successful source queries, including explicitly limited RSS days."""
    if type(end) is not date or (start is not None and type(start) is not date) or (start is not None and start > end):
        raise ValueError("start and end must be ordered dates")
    data = Path(data)
    owns_session = session is None
    client = session if session is not None else requests.Session()
    try:
        with jev._cache_lock(data / "responses.json"):
            jev_store.initialize(data)
            document = jev_store.read_news(data)
            previous = document["selection_metadata"].get("incremental", {})
        today = datetime.now(timezone.utc).astimezone(jev.NY_TZ).date()
        coverage = (date.fromisoformat(previous["coverage_end"]) if previous.get("coverage_end")
                    else min(_initial_coverage(document, end, start), today - timedelta(days=1)))
        completed_end = min(end, today - timedelta(days=1))
        capped_days = set(previous.get("capped_days", []))
        status = {"source_status": "partial" if capped_days else "success", "requested_end": end.isoformat(),
                  "coverage_end": coverage.isoformat(), "selected_count": 0, "limited": bool(capped_days),
                  "capped_days": sorted(capped_days), "source_gaps": [], "errors": []}
        run_gaps = []
        window_start = coverage + timedelta(days=1)
        while window_start <= completed_end:
            window_end = min(window_start + timedelta(days=6), completed_end)
            snapshots = {}
            collected_at = jev._now()

            def capture(name: str, first: date, last: date, rows: list[dict]) -> None:
                key = f"incremental/{window_start}_{window_end}/{name}/{first}_{last}/{uuid4().hex}"
                snapshots[key] = rows

            def rss(first: date, last: date, query: str, index: int) -> tuple[list[dict], set[date]]:
                rows = sources._fetch_rss_range(client, first, last, query, collected_at)
                capture(f"rss-{index}", first, last, rows)
                if len(rows) < 100:
                    return rows, set()
                if first == last:
                    return rows, {first}
                middle = first + timedelta(days=(last - first).days // 2)
                left, left_capped = rss(first, middle, query, index)
                right, right_capped = rss(middle + timedelta(days=1), last, query, index)
                return left + right, left_capped | right_capped

            records, window_capped, window_gaps = [], set(), []
            # Google News RSS is the date-queryable core: its failure retries the window later.
            # Daily Coffee News and Yahoo are supplements; record their outage as a gap instead
            # of letting it stop coverage (Daily Coffee News returned HTTP 403 from 2026-09-27).
            try:
                frame = news.fetch_wordpress(client, window_start - timedelta(days=1), window_end + timedelta(days=1))
                wordpress = json.loads(frame.to_json(orient="records", date_format="iso"))
                capture("wordpress", window_start, window_end, wordpress)
                records.extend(wordpress)
            except SOURCE_ERRORS as exc:
                window_gaps.append(_gap("wordpress", window_start, window_end, exc))
            try:
                for index, query in enumerate(sources.QUERIES):
                    rows, query_capped = rss(window_start, window_end, query, index)
                    records.extend(rows)
                    window_capped.update(query_capped)
            except Exception as exc:
                failure = _gap("rss", window_start, window_end, exc)
                # This window is retried later, so its supplement gaps are not recorded as final.
                status.update(source_status="failed", source_gaps=list(run_gaps),
                              errors=[_message(gap) for gap in (*run_gaps, *window_gaps, failure)])
                with jev._cache_lock(data / "responses.json"):
                    document = jev_store.read_news(data)
                    document["sources"].update(snapshots)
                    _checkpoint(document, status)
                    jev_store.write_news(data, document)
                break
            if window_end >= today - timedelta(days=7):
                try:
                    yahoo = jev._fetch_yahoo(window_start, window_end, 1000)
                    capture("yahoo", window_start, window_end, yahoo)
                    records.extend(yahoo)
                except SOURCE_ERRORS as exc:
                    window_gaps.append(_gap("yahoo", window_start, window_end, exc))
            run_gaps.extend(window_gaps)
            with jev._cache_lock(data / "responses.json"):
                document = jev_store.read_news(data)
                document["sources"].update(snapshots)
                jev_store.write_news(data, document)
                added = _select(records, document, window_start, window_end, collected_at, window_capped)
                document["selections"].setdefault("incremental", []).extend(added)
                coverage = window_end
                capped_days.update(day.isoformat() for day in window_capped)
                status.update(coverage_end=coverage.isoformat(), selected_count=status["selected_count"] + len(added),
                              limited=bool(capped_days), capped_days=sorted(capped_days),
                              source_gaps=list(run_gaps), errors=[_message(gap) for gap in run_gaps],
                              source_status="partial" if capped_days or run_gaps else "success")
                _checkpoint(document, status)
                jev_store.write_news(data, document)
                coverage = date.fromisoformat(status["coverage_end"])
            window_start = coverage + timedelta(days=1)
        if status["source_status"] == "success" and date.fromisoformat(status["coverage_end"]) < end:
            status["source_status"] = "partial"
        with jev._cache_lock(data / "responses.json"):
            document = jev_store.read_news(data)
            _checkpoint(document, status)
            jev_store.write_news(data, document)
        return status
    finally:
        if owns_session:
            client.close()
