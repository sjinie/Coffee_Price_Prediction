"""Resumable Jev job: retry each minute, then wait five minutes after success."""
from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
import json
import math
import os
from pathlib import Path
import time

from dotenv import load_dotenv
import pandas as pd
import requests

from coffee_service import jev

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/raw/jev"
JOBS = (("year-20250926-20260925", 2), ("validation-2022-2025", 1))
INTERVAL = 300
RETRY_INTERVAL = 60
MAX_FAILURES = 24 * 60 * 60 // RETRY_INTERVAL


def write_json(path, value):
    # Existing writer uses a unique temporary file and an atomic rename.
    jev._write_records(Path(path), value)


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


class GatewaySession(requests.Session):
    """Retain only safe response diagnostics, never headers or request secrets."""
    status = None
    delay = RETRY_INTERVAL
    detail = None

    def post(self, *args, **kwargs):
        self.status, self.delay, self.detail = None, RETRY_INTERVAL, None
        response = super().post(*args, **kwargs)
        self.status = response.status_code
        self.delay = retry_delay(response.headers.get("Retry-After"))
        if response.status_code >= 400:
            try:
                payload = response.json()
                error = payload.get("error", payload)
                if isinstance(error, dict):
                    self.detail = {key: str(error[key])[:500] for key in
                                   ("code", "type", "error_type", "message") if key in error}
                    key = os.getenv("AI_GATEWAY_API_KEY", "")
                    if key:
                        self.detail = {name: value.replace(key, "[REDACTED]")
                                       for name, value in self.detail.items()}
            except (ValueError, AttributeError):
                pass
        return response


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


def selected_records(job, daily_max):
    path = job / "selected.json"
    if not path.exists():
        return None
    records = json.loads(path.read_text())
    if not isinstance(records, list):
        raise ValueError("Selection must be a list")
    counts = Counter(record["selection_date"] for record in records)
    if max(counts.values(), default=0) > daily_max:
        raise ValueError("Selection exceeds daily cap")
    if len({record["content_hash"] for record in records}) != len(records):
        raise ValueError("Selection contains duplicate contents")
    return records


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


def export(job, results):
    write_json(job / "results.json", results)
    temporary = job / "results.csv.tmp"
    pd.DataFrame(results).to_csv(temporary, index=False, encoding="utf-8-sig")
    temporary.replace(job / "results.csv")


def run(data=DATA, *, once=False):
    data = Path(data)
    cache_path = data / "backfill-analyses.json"
    status_path = data / "backfill-status.json"
    with jev._cache_lock(cache_path):
        cache = {}
        for path in (data / "articles.json", data / JOBS[0][0] / "analyses.json",
                     data / JOBS[0][0] / "probe-analysis.json", cache_path):
            cache.update((key(record), record) for record in jev.read_records(path))
        write_json(cache_path, list(cache.values()))
        state = json.loads(status_path.read_text()) if status_path.exists() else {}
        due = float(state.get("next_attempt_epoch", 0))
        last_attempt = state.get("last_response", {}).get("at")
        cadence = INTERVAL if state.get("last_response", {}).get("status") == 200 else RETRY_INTERVAL
        if last_attempt:
            due = max(due, jev._as_datetime(last_attempt).timestamp() + cadence)
        state["next_attempt_epoch"] = due
        failures = int(state.get("consecutive_failures", 0))
        # After a paid/authentication/budget stop, a human must explicitly resume.
        if state.get("state") in {"stopped", "budget_stop", "retry_exhausted", "cost_unknown"}:
            raise RuntimeError("Backfill is stopped; inspect backfill-status.json before resuming")
        state.update(pid=os.getpid(), model=jev.MODEL, prompt_version=jev.PROMPT_VERSION,
                     interval_seconds=cadence, jobs=[name for name, _ in JOBS])

        def save(status, **values):
            state.update(state=status, updated_at=jev._now(), consecutive_failures=failures,
                         reported_cache_cost_usd=str(cost_total(cache.values())), **values)
            write_json(status_path, state)
            print(json.dumps(state, ensure_ascii=False), flush=True)

        with GatewaySession() as session:
            while True:
                active = None
                for name, daily_max in JOBS:
                    job = data / name
                    selected = selected_records(job, daily_max)
                    if selected is None:
                        active = (job, None, None)
                        break
                    results = results_for(selected, cache)
                    pending = [record for record in selected if chosen_key(record) not in cache]
                    if pending:
                        active = (job, selected, pending)
                        break
                    export(job, results)
                if active is None:
                    save("completed", pending=0)
                    return 0
                job, selected, pending = active
                if selected is None:
                    save("waiting_selection", job=job.name)
                    if once:
                        return 0
                    time.sleep(INTERVAL)
                    continue
                export(job, results_for(selected, cache))
                save("waiting" if due > time.time() else "running", job=job.name,
                     selected=len(selected), completed=len(selected)-len(pending), pending=len(pending))
                if due > time.time():
                    if once:
                        return 0
                    time.sleep(due - time.time())
                # The Gateway's user-set $1 budget is the hard server-side cap.
                if cost_total(cache.values()) + Decimal("0.01") >= Decimal("1"):
                    save("budget_stop")
                    return 1
                batch = sorted(pending, key=lambda item: item["selection_date"], reverse=True)[:20]
                while len(json.dumps(jev._batch_payload(batch), ensure_ascii=False).encode()) > 60000:
                    batch.pop()
                if not batch:
                    save("stopped", error="Article exceeds request size limit")
                    return 1
                # Persist a conservative crash/restart slot before sending.
                due = time.time() + INTERVAL
                save("requesting", next_attempt_epoch=due)
                event = {"at": jev._now(), "job": job.name, "articles": len(batch)}
                stop = None
                try:
                    classified = jev.classify_articles(batch, session=session)
                    cache.update((key(record), record) for record in classified)
                    write_json(cache_path, list(cache.values()))
                    export(job, results_for(selected, cache))
                    failures = 0
                    event.update(status=200, cost=classified[0]["cost"], usage=classified[0]["usage"])
                    due = time.time() + INTERVAL
                    if classified[0]["cost"] is None:
                        stop = "cost_unknown"
                except (jev.JevRateLimitError, jev.JevTransientError) as exc:
                    failures += 1
                    due = time.time() + session.delay
                    event.update(status=session.status or "connection", error=str(exc), detail=session.detail)
                    if failures >= MAX_FAILURES:
                        stop = "retry_exhausted"
                except jev.JevStopError as exc:
                    event.update(status=session.status or "stopped", error=str(exc), detail=session.detail)
                    stop = "stopped"
                with (data / "backfill-requests.jsonl").open("a", encoding="utf-8") as output:
                    output.write(json.dumps(event, ensure_ascii=False) + "\n")
                    output.flush()
                save(stop or "waiting", next_attempt_epoch=due, last_response=event,
                     interval_seconds=INTERVAL if event.get("status") == 200 else RETRY_INTERVAL,
                     completed=len(results_for(selected, cache)),
                     pending=len(selected)-len(results_for(selected, cache)))
                if stop:
                    return 1
                if once:
                    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="At most one due request, without waiting")
    args = parser.parse_args()
    load_dotenv(ROOT / ".env")
    return run(once=args.once)


if __name__ == "__main__":
    raise SystemExit(main())
