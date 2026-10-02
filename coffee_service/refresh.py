"""Refresh on process start, then once a day while the server stays running."""
from __future__ import annotations

from datetime import datetime, timedelta
import os
from pathlib import Path
import signal
from threading import Event
import time

from . import jev, jev_store, news_backfill
from .ingestion import source_lock

# Daily, like the production Actions schedule: the news feature was trained on daily
# arrivals, and a weekly batch would push a week of articles onto one forecast date.
REFRESH_INTERVAL = 24 * 60 * 60
SOURCE_RETRY = 60 * 60
# Forecasts were issued, but news or auxiliary sources were incomplete. Distinct from
# 1 (forecast failed) and argparse's 2 so callers can report a warning instead of a failure.
PARTIAL_EXIT = 3


def save_state(data, state):
    with jev._cache_lock(data / "responses.json"):
        jev_store.initialize(data)
        document = jev_store.read_document(data / "requests.json")
        document["refresh_state"] = state
        jev_store.write_document(data / "requests.json", document)


def run_cycle(source_dir, artifact, data, start, *, stop=None, end=None, database_url=None):
    """Catch up each source from its own checkpoint; never replace the price model."""
    from . import news_incremental, pipeline

    stop = stop if stop is not None else Event()
    data = Path(data)
    end = end or (datetime.now(jev.NY_TZ).date() - timedelta(days=1))
    state = {"started_at": jev._now(), "pid": os.getpid(), "requested_end": end.isoformat(),
             "numeric": "pending", "collection": "pending", "classification": "pending", "errors": []}
    save_state(data, state)
    def run_numeric():
        try:
            with source_lock(source_dir):
                result = pipeline.run_pipeline("incremental", source_dir, artifact, start, end,
                                               database_url=database_url, jev_cache=data / "responses.json")
            state["numeric"] = result["status"]
            state["numeric_source_failures"] = result.get("source_failures", [])
            if state["numeric_source_failures"]:
                state["numeric"] = "partial"
        except Exception as exc:
            state["numeric"] = "failed"
            # Only the pipeline's own run error carries a message built from safe parts.
            detail = str(exc) if isinstance(exc, pipeline.PipelineRunError) else type(exc).__name__
            state["errors"].append("numeric: " + detail)
        save_state(data, state)

    selected = Path(artifact).suffix == ".json"
    if not selected:
        run_numeric()
    if not stop.is_set():
        try:
            collected = news_incremental.collect_pending(data, end)
            state["collection"] = collected["source_status"]
            state["collection_errors"] = collected.get("errors", [])
        except Exception as exc:
            state["collection"] = "failed"
            state["errors"].append("collection: " + type(exc).__name__)
        save_state(data, state)
    classification_retry_epoch = None
    while not stop.is_set():
        try:
            if not os.getenv("AI_GATEWAY_API_KEY"):
                state["classification"] = "not_configured"
                break
            # One due request at a time, so SIGTERM can interrupt the wait safely.
            result = news_backfill.run(data, once=True)
            with jev._cache_lock(data / "responses.json"):
                worker = jev_store.read_document(data / "requests.json")["worker_state"]
            state["classification"] = worker["state"]
            if result or worker["state"] in {"completed", "stopped", "budget_stop", "cost_unknown", "retry_exhausted"}:
                break
            delay = max(0, worker.get("next_attempt_epoch", time.time()) - time.time())
            # A provider retry wait must not postpone the daily price forecast.
            if selected and delay > 0 and worker.get("consecutive_failures", 0) > 0:
                classification_retry_epoch = worker["next_attempt_epoch"]
                break
            if stop.wait(delay):
                break
        except Exception as exc:
            state["classification"] = "failed"
            state["errors"].append("classification: " + type(exc).__name__)
            break
    # Forecasts are immutable: complete the available news update before issuing
    # the selected model forecasts, including the no-news path after API failure.
    if selected and not stop.is_set():
        run_numeric()
    state["finished_at"] = jev._now()
    state["status"] = ("interrupted" if stop.is_set() else "success" if
                       state["numeric"] == "success" and state["collection"] == "success" and
                       state["classification"] == "completed" else "partial")
    retry_source = state["numeric"] in {"failed", "partial"} or state["collection"] == "failed"
    state["next_run_epoch"] = time.time() + (SOURCE_RETRY if retry_source else REFRESH_INTERVAL)
    if classification_retry_epoch is not None:
        state["next_run_epoch"] = min(state["next_run_epoch"], classification_retry_epoch)
    save_state(data, state)
    print("Refresh: " + str({k: state.get(k) for k in ("status", "numeric", "collection", "classification",
                                                        "errors", "collection_errors")}), flush=True)
    return state


# Classification states that need an operator: the worker will not resume on its own.
CLASSIFICATION_HALTED = {"failed", "stopped", "budget_stop", "cost_unknown", "retry_exhausted"}


def exit_code(state):
    """0 = all done; PARTIAL_EXIT = forecasts issued, news only degraded (supplement gaps,
    capped days, provider retry wait); 1 = forecasts failed or news pipeline halted."""
    if state["status"] == "success":
        return 0
    if (state["status"] == "partial" and state["numeric"] in {"success", "partial"}
            and state.get("collection") != "failed"
            and state.get("classification") not in CLASSIFICATION_HALTED):
        return PARTIAL_EXIT
    return 1


def serve(source_dir, artifact, data, start, *, once=False, database_url=None, stop=None):
    """Restart means one catch-up; missed daily ticks are coalesced into that run."""
    stop = stop if stop is not None else Event()
    previous = {}
    if not once:
        for name in (signal.SIGINT, signal.SIGTERM):
            previous[name] = signal.signal(name, lambda *_: stop.set())
    try:
        while not stop.is_set():
            state = run_cycle(source_dir, artifact, data, start, stop=stop, database_url=database_url)
            if once:
                return exit_code(state)
            if stop.wait(max(0, state["next_run_epoch"] - time.time())):
                break
        return 0
    finally:
        for name, handler in previous.items():
            signal.signal(name, handler)
