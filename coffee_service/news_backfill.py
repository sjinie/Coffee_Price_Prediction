"""Resumable Jev job: retry each minute, then wait five minutes after success."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import time

from dotenv import load_dotenv
import requests

from coffee_service import jev, jev_store

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/jev"
INTERVAL = 300
RETRY_INTERVAL = 60
MAX_FAILURES = 24 * 60 * 60 // RETRY_INTERVAL


def retry_delay(value, now=None):
    """Retry no faster than once a minute and honor longer Retry-After values."""
    now = time.time() if now is None else now
    try:
        seconds = float(value)
        if not math.isfinite(seconds):
            return RETRY_INTERVAL
    except (ValueError, TypeError):
        try:
            parsed = parsedate_to_datetime(str(value))
            if parsed.tzinfo is None:
                return RETRY_INTERVAL
            seconds = parsed.timestamp() - now
        except (ValueError, TypeError, OverflowError):
            return RETRY_INTERVAL
    return max(RETRY_INTERVAL, math.ceil(seconds))


def key(record):
    return record["content_hash"], record["model"], record["prompt_version"]


def chosen_key(record):
    return record["content_hash"], jev.MODEL, jev.PROMPT_VERSION


def cost_total(records):
    total = Decimal(0)
    for record in records:
        value = record.get("cost")
        if value is None:
            continue
        try:
            cost = Decimal(str(value))
        except InvalidOperation:
            raise ValueError("Invalid cached cost") from None
        if not cost.is_finite() or cost < 0:
            raise ValueError("Invalid cached cost")
        total += cost
    return total


def results_for(selected, cache):
    results = []
    for article in selected:
        record = cache.get(chosen_key(article))
        if record is not None:
            metadata = jev._article_fields(article)
            metadata["publisher"] = article.get("publisher") or None
            result = {**record, **metadata, **{name: value for name, value in article.items()
                                  if name.startswith("selection_")}}
            result["available_at"] = max(record["available_at"], metadata["available_at"])
            results.append(result)
    return results


def pack_batch(pending):
    batch = []
    for article in sorted(pending, key=lambda item: item["selection_date"], reverse=True):
        candidate = batch + [article]
        size = len(json.dumps(jev._batch_payload(candidate), ensure_ascii=False).encode("utf-8"))
        if size > jev.MAX_BATCH_BYTES:
            break
        batch = candidate
    return batch


def run(data=DATA, *, once=False, groups=None, limit=None, transport=None, api_key=None, batch_size=None):
    """Serialize each HTTP exchange, releasing the archive lock during rate-limit waits."""
    data = Path(data)
    attempts = 0
    while True:
        with jev._cache_lock(data / "responses.json"):
            before = len(jev_store.read_document(data / "requests.json")["attempts"])
            result = _run_locked(data, groups=groups, transport=transport,
                                 api_key=api_key, batch_size=batch_size)
            document = jev_store.read_document(data / "requests.json")
            attempts += len(document["attempts"]) - before
            state = document["worker_state"]
        if result or state["state"] == "completed" or once or (limit is not None and attempts >= limit):
            return result
        time.sleep(max(0, state["next_attempt_epoch"] - time.time()))


def _run_locked(data, *, groups, transport, api_key, batch_size):
    jev_store.initialize(data)
    news = jev_store.read_document(data / "news.json")
    selections = news.get("selections", {})
    wanted = {}
    for name, rows in selections.items():
        if (groups is None and name == "service") or (groups is not None and name not in groups):
            continue
        for row in sorted(rows, key=lambda r: r["selection_date"]):
            wanted.setdefault(chosen_key(row), row)
    request_doc = jev_store.read_document(data / "requests.json")
    state = request_doc.get("worker_state", {})
    if state.get("state") in {"stopped", "budget_stop", "retry_exhausted", "cost_unknown"}:
        raise RuntimeError("Inspect requests.json worker_state before explicitly resuming")
    due = float(state.get("next_attempt_epoch", 0))
    last = state.get("last_response", {})
    if last.get("at"):
        due = max(due, jev._as_datetime(last["at"]).timestamp() +
                  (INTERVAL if last.get("status") == 200 else RETRY_INTERVAL))
    for response in jev_store.read_document(data / "responses.json")["attempts"]:
        received = jev._as_datetime(response["received_at"]).timestamp()
        delay = INTERVAL if response.get("status") == 200 else retry_delay(response.get("retry_after"), now=received)
        due = max(due, received + delay)
    failures = int(state.get("consecutive_failures", 0))

    def save(status, **values):
        state.update(state=status, updated_at=jev._now(), pid=os.getpid(),
                     consecutive_failures=failures, **values)
        document = jev_store.read_document(data / "requests.json")
        document["worker_state"] = state
        jev_store.write_document(data / "requests.json", document)

    owns_session = transport is None
    session = transport or requests.Session()
    capture = jev_store.CaptureSession(data, session)
    try:
        # A saved HTTP 200 is recoverable without paying for the same batch again.
        try:
            recover_responses(data)
        except jev.JevStopError as exc:
            save("stopped", error=str(exc))
            return 1
        except ValueError as exc:
            save("cost_unknown", error=str(exc))
            return 1
        cache = {key(record): record for record in jev_store.read_analyses(data, latest=True)}
        pending = [row for identity, row in wanted.items() if identity not in cache]
        jev_store.export(data)
        if not pending:
            save("completed", selected=len(wanted), completed=len(wanted), pending=0)
            return 0
        save("waiting" if due > time.time() else "running", selected=len(wanted),
             completed=len(wanted)-len(pending), pending=len(pending), next_attempt_epoch=due)
        if due > time.time():
            return 0
        if cost_total(jev_store.read_analyses(data)) + Decimal("0.01") >= Decimal("1"):
            save("budget_stop")
            return 1
        batch = pack_batch(pending)
        if batch_size is not None:
            batch = batch[:batch_size]
        if not batch:
            save("stopped", error="Article exceeds request size limit")
            return 1
        due = time.time() + INTERVAL
        save("requesting", next_attempt_epoch=due)
        event = {"at": jev._now(), "articles": len(batch)}
        stop = None
        try:
            capture.articles = batch
            classified = jev.classify_articles(batch, session=capture, api_key=api_key)
            for record in classified:
                record["request_id"] = capture.last_request_id
            jev_store.append_analyses(data, classified)
            failures = 0
            event.update(status=200, cost=classified[0]["cost"], usage=classified[0]["usage"])
            due = time.time() + INTERVAL
            if classified[0]["cost"] is None:
                stop = "cost_unknown"
        except (jev.JevRateLimitError, jev.JevTransientError) as exc:
            failures += 1
            due = time.time() + retry_delay(capture.last_retry_after)
            event.update(status=capture.last_status or "connection", error=str(exc))
            if failures >= MAX_FAILURES:
                stop = "retry_exhausted"
        except jev.JevStopError as exc:
            event.update(status=capture.last_status or "stopped", error=str(exc))
            stop = "stopped"
        event["request_id"] = capture.last_request_id
        save(stop or "waiting", next_attempt_epoch=due, last_response=event,
             interval_seconds=INTERVAL if event.get("status") == 200 else RETRY_INTERVAL)
        jev_store.export(data)
        if stop:
            return 1
        return 0
    finally:
        if owns_session:
            session.close()



def recover_responses(data):
    """Finish local parsing after a crash, preserving the original response time."""
    requests_doc = jev_store.read_document(data / "requests.json")
    responses_doc = jev_store.read_document(data / "responses.json")
    responses = {r["request_id"]: r for r in responses_doc["attempts"]}
    completed = {r.get("request_id") for r in responses_doc["analyses"]}
    for request in requests_doc["attempts"]:
        identity = request["request_id"]
        response = responses.get(identity)
        if response is None:
            raise ValueError("Unanswered archived request: verify Gateway cost before resuming")
        status = response.get("status")
        # A crash after saving a denied response must not bypass the stop policy.
        if (request is requests_doc["attempts"][-1] and status is not None
                and 400 <= status < 500 and status != 429
                and requests_doc["worker_state"].get("last_response", {}).get("request_id") != identity):
            raise jev.JevStopError(f"Archived Gateway stop requires inspection: HTTP {status}")
        if identity in completed or status != 200:
            continue
        articles = request.get("articles")
        if not articles or request["body"] != jev._batch_payload(articles):
            raise ValueError("Archived request cannot be parsed with the current prompt")
        rows = jev._parse_batch_response([jev._article_fields(a) for a in articles],
                                       response["body"], response["received_at"])
        for row in rows:
            row["request_id"] = identity
        jev_store.append_analyses(data, rows)
        if rows[0]["cost"] is None:
            raise ValueError("Archived HTTP 200 has no known cost")

def import_selected(data, path):
    """Append a vetted article list; retain its original fields and freeze selected dates."""
    rows = jev._read_candidates(Path(path))
    normalized = []
    for row in rows:
        item = jev._article_fields(row)
        day = jev._as_datetime(item["event_at"]).astimezone(jev.NY_TZ).date()
        if day >= datetime.now(jev.NY_TZ).date():
            raise ValueError("Only completed New York news dates can be selected")
        midnight = datetime.combine(day + timedelta(days=1),
                                    datetime.min.time(), jev.NY_TZ).astimezone(timezone.utc)
        item.update(selection_date=day.isoformat(), selection_policy="user-selected-import-v1",
                    selection_available_at=midnight.isoformat().replace("+00:00", "Z"))
        item["available_at"] = max(item["available_at"], item["selection_available_at"])
        normalized.append(item)
    with jev._cache_lock(Path(data) / "responses.json"):
        doc = jev_store.read_document(Path(data) / "news.json")
        jev_store.initialize(data)
        prior = doc.setdefault("selections", {}).get("incremental", [])
        unique = {(r["content_hash"], r["selection_date"]): r for r in prior}
        for row in normalized:
            unique.setdefault((row["content_hash"], row["selection_date"]), row)
        existing = {(r["content_hash"], r["selection_date"]): r
                    for name, items in doc["selections"].items() if name != "service"
                    for r in items}
        previous_counts = Counter(r["selection_date"] for r in existing.values())
        selected = {(r["content_hash"], r["selection_date"]): r
                    for name, items in doc["selections"].items() if name not in {"service", "incremental"}
                    for r in items}
        selected.update(unique)
        counts = Counter(r["selection_date"] for r in selected.values())
        if any(n > max(previous_counts[day], 1 if day < "2025-09-26" else 2)
               for day, n in counts.items()):
            raise ValueError("Imported selection exceeds the daily cap (historical 1, recent 2; existing selections remain frozen)")
        doc["selections"]["incremental"] = list(unique.values())
        digest = jev.sha256(Path(path).read_bytes()).hexdigest()
        doc.setdefault("sources", {})["import:" + digest] = rows
        jev_store.write_document(Path(data) / "news.json", doc)

def collect(data, start, end, *, limit, source, candidates_path=None, session=None, api_key=None, batch_size=1):
    """Keep the service's daily selection contract while using the four-file archive."""
    data = Path(data)
    owns_session = session is None
    client = session or requests.Session()
    status = {"source_status": "pending", "classification_status": "not_started",
              "api_attempts": 0, "errors": []}
    try:
        with jev._cache_lock(data / "responses.json"):
            jev_store.initialize(data)
            document = jev_store.read_document(data / "news.json")
            try:
                if candidates_path is not None:
                    records = jev._read_candidates(candidates_path)
                elif source == "gdelt":
                    records = jev._fetch_gdelt(client, start, end, 200)
                elif source == "yahoo":
                    records = jev._fetch_yahoo(start, end, 1000)
                else:
                    from coffee_service import news
                    records = jev._fetch_yahoo(start, end, 1000)
                    frame = news.fetch_wordpress(client, start - timedelta(days=1), end + timedelta(days=1))
                    records += json.loads(frame.to_json(orient="records", date_format="iso"))
            except (RuntimeError, requests.RequestException) as exc:
                status.update(source_status="failed", errors=[type(exc).__name__])
                document["service_status"] = status
                jev_store.write_document(data / "news.json", document)
                return jev.read_selected_records(data / "responses.json", start, end), status
            collected = jev._now()
            document.setdefault("sources", {})["collection:" + collected] = records
            jev_store.write_document(data / "news.json", document)
            normalized = []
            for row in records:
                try:
                    normalized.append(jev._article_fields(row, collected))
                except (KeyError, TypeError, ValueError):
                    jev._add_error(status, "Invalid source article skipped")
            selected, summary = jev.select_daily_articles(normalized, start, end)
            prior = document.setdefault("selections", {}).get("service", [])
            locked_days = {r["selection_date"] for r in prior}
            new = [r for r in selected if r["selection_date"] not in locked_days
                   and not any(jev._near_duplicate(r, old) for old in prior)]
            document["selections"]["service"] = prior + new
            wanted = [r for r in prior + new if start.isoformat() <= r["selection_date"] <= end.isoformat()]
            selected_days = {r["selection_date"] for r in wanted}
            status.update(missing_days=sorted(set(summary["missing_days"]) - selected_days),
                          selection_timezone="America/New_York", selection_duplicates=summary["duplicates"],
                          sampling="one heuristic-selected article per completed New York day; incomplete coverage",
                          retrieved_min_event_at=min((r["event_at"] for r in normalized), default=None),
                          retrieved_max_event_at=max((r["event_at"] for r in normalized), default=None),
                          truncated=len(records) >= 200,
                          source_status="success" if records else "empty", source_count=len(records),
                          selected_count=len(wanted), selected_days=sorted({r["selection_date"] for r in wanted}),
                          selection_policy=jev.SELECTION_POLICY, requested_start=start.isoformat(),
                          requested_end=end.isoformat())
            document["service_status"] = status
            jev_store.write_document(data / "news.json", document)
        before = len(jev_store.read_document(data / "requests.json").get("attempts", []))
        run(data, groups={"service"}, limit=limit, transport=client, api_key=api_key, batch_size=batch_size)
        after = len(jev_store.read_document(data / "requests.json").get("attempts", []))
        rows = jev.read_selected_records(data / "responses.json", start, end)
        state = jev_store.read_document(data / "requests.json").get("worker_state", {})
        failed = state.get("state") in {"stopped", "budget_stop", "cost_unknown", "retry_exhausted"}
        if state.get("error") or state.get("last_response", {}).get("error"):
            jev._add_error(status, state.get("error") or state["last_response"]["error"])
        status.update(completed_at=jev._now(), api_attempts=after-before, selected_pending_count=len(wanted)-len(rows),
                      selected_analysis_ids=[r["analysis_id"] for r in rows],
                      classification_status="failed" if failed else "partial" if len(rows)<len(wanted) else "success")
        with jev._cache_lock(data / "responses.json"):
            document = jev_store.read_document(data / "news.json")
            document["service_status"] = status
            jev_store.write_document(data / "news.json", document)
        return rows, status
    finally:
        if owns_session:
            client.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="At most one due request, without waiting")
    parser.add_argument("--input", type=Path, help="JSON list of vetted articles to append")
    args = parser.parse_args()
    if args.input:
        import_selected(DATA, args.input)
    load_dotenv(ROOT / ".env")
    return run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
