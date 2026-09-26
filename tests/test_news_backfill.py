from datetime import datetime, timezone
from email.utils import format_datetime
import json

from coffee_service import jev, news_backfill as worker


def article():
    result = jev._article_fields({"url": "https://example.com/coffee", "title": "Brazil coffee crop falls",
                                  "published_at": "2025-10-01T12:00:00Z"})
    result.update(selection_date="2025-10-01", selection_available_at="2025-10-02T04:00:00Z")
    return result


def result(row):
    return jev._response_record(row, {"answers": {
        "price_pressure": {"type": "choice", "choice": "bullish", "confidence": .7,
                           "probabilities": {"bullish": .7, "bearish": .1, "neutral": .1, "uncertain": .1}},
        "relevance": {"type": "noul", "noul": .9}}, "usage": {"input_tokens": 100},
        "provider_metadata": {"gateway": {"cost": ".001"}}}, jev._now())


def setup_jobs(tmp_path):
    for name, _ in worker.JOBS:
        worker.write_json(tmp_path / name / "selected.json", [article()])


def test_retry_after_floor_dates_and_uncapped_delay():
    assert worker.retry_delay(None) == 60
    assert worker.retry_delay("NaN") == 60
    assert worker.retry_delay("-1") == 60
    assert worker.retry_delay("7200") == 7200
    date = format_datetime(datetime.fromtimestamp(10000, timezone.utc), usegmt=True)
    assert worker.retry_delay(date, now=1000) == 9000


def test_rate_limit_checkpoints_and_restart_does_not_send_early(tmp_path, monkeypatch):
    setup_jobs(tmp_path)
    calls = []
    def limited(rows, session):
        calls.append(rows)
        session.status, session.delay = 429, 120
        raise jev.JevRateLimitError(120)
    monkeypatch.setattr(jev, "classify_articles", limited)
    assert worker.run(tmp_path, once=True) == 0
    state = json.loads((tmp_path / "backfill-status.json").read_text())
    assert state["pending"] == 1 and state["consecutive_failures"] == 1
    assert state["next_attempt_epoch"] > worker.time.time() + 110
    assert worker.run(tmp_path, once=True) == 0
    assert len(calls) == 1


def test_restart_honors_saved_429_retry_slot(tmp_path, monkeypatch):
    setup_jobs(tmp_path)
    worker.write_json(tmp_path / "backfill-status.json", {
        "state": "waiting", "next_attempt_epoch": worker.time.time() + 1,
        "last_response": {"at": jev._now(), "status": 429}})
    def unexpected(*args, **kwargs):
        raise AssertionError("Old short interval must not trigger a request")
    monkeypatch.setattr(jev, "classify_articles", unexpected)
    assert worker.run(tmp_path, once=True) == 0
    state = json.loads((tmp_path / "backfill-status.json").read_text())
    assert state["interval_seconds"] == 60
    assert state["next_attempt_epoch"] > worker.time.time() + 50


def test_429_retries_each_minute_then_200_waits_five_minutes(tmp_path, monkeypatch):
    setup_jobs(tmp_path)
    calls = []
    def classify(rows, session):
        calls.append(rows)
        if len(calls) == 1:
            session.status, session.delay = 429, 60
            raise jev.JevRateLimitError(60)
        session.status = 200
        return [result(row) for row in rows]
    monkeypatch.setattr(jev, "classify_articles", classify)
    assert worker.run(tmp_path, once=True) == 0
    state = json.loads((tmp_path / "backfill-status.json").read_text())
    assert state["interval_seconds"] == 60
    assert 50 < state["next_attempt_epoch"] - worker.time.time() <= 60
    state["next_attempt_epoch"] = worker.time.time() - 1
    state["last_response"]["at"] = "2020-01-01T00:00:00Z"
    worker.write_json(tmp_path / "backfill-status.json", state)
    assert worker.run(tmp_path, once=True) == 0
    state = json.loads((tmp_path / "backfill-status.json").read_text())
    assert state["interval_seconds"] == 300
    assert 290 < state["next_attempt_epoch"] - worker.time.time() <= 300
    assert worker.run(tmp_path, once=True) == 0
    assert len(calls) == 2


def test_success_reused_between_jobs_without_second_call(tmp_path, monkeypatch):
    setup_jobs(tmp_path)
    calls = []
    def classify(rows, session):
        calls.append(rows)
        return [result(row) for row in rows]
    monkeypatch.setattr(jev, "classify_articles", classify)
    assert worker.run(tmp_path, once=True) == 0
    assert worker.run(tmp_path, once=True) == 0
    assert len(calls) == 1
    state = json.loads((tmp_path / "backfill-status.json").read_text())
    assert state["state"] == "completed"
    for name, _ in worker.JOBS:
        records = jev.read_records(tmp_path / name / "results.json")
        assert len(records) == 1 and records[0]["available_at"] >= records[0]["analyzed_at"]


def test_budget_error_stops_and_does_not_retry(tmp_path, monkeypatch):
    setup_jobs(tmp_path)
    def stopped(rows, session):
        session.status = 402
        raise jev.JevStopError("HTTP 402")
    monkeypatch.setattr(jev, "classify_articles", stopped)
    assert worker.run(tmp_path, once=True) == 1
    state = json.loads((tmp_path / "backfill-status.json").read_text())
    assert state["state"] == "stopped" and state["last_response"]["status"] == 402


def test_reused_analysis_retains_selected_source_and_later_availability():
    original = article()
    cached = result(original)
    cached.update(analyzed_at="2026-01-01T00:00:00Z", available_at="2026-01-01T00:00:00Z",
                  publisher="Original publisher")
    selected = {**original, "url": "https://other.example.com/reprint", "source": "other_publisher",
                "published_at": "2025-10-02T12:00:00Z", "collected_at": "2026-02-01T00:00:00Z",
                "selection_date": "2025-10-02", "selection_available_at": "2025-10-03T04:00:00Z"}
    row = worker.results_for([selected], {worker.key(cached): cached})[0]
    assert row["url"] == selected["url"] and row["source"] == "other_publisher"
    assert row["article_id"] != cached["article_id"]
    assert row["published_at"] == selected["published_at"]
    assert row["available_at"] == selected["collected_at"]
    assert row["analysis_id"] == cached["analysis_id"]
    assert row["publisher"] is None
    selected["publisher"] = "Selected publisher"
    assert worker.results_for([selected], {worker.key(cached): cached})[0]["publisher"] == "Selected publisher"
