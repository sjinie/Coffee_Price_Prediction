from datetime import date
import json
from pathlib import Path

import pytest

from coffee_service import jev


def _answer(label="bullish"):
    return {"answers": {"price_pressure": {"choice": label, "probabilities": {"bullish": .7, "bearish": .1, "neutral": .1, "uncertain": .1}, "confidence": .7}, "relevance": {"noul": .9}}, "usage": {"input_tokens": 12, "output_tokens": 3}, "provider_metadata": {"gateway": {"cost": 0.01}}}


def test_payload_is_typesafe_choice_and_noul_with_market_specific_instructions():
    payload = jev.request_payload("Coffee frost threatens crop", "A short summary")
    assert payload["model"] == "typesafe-ai/jev"
    assert payload["questions"]["price_pressure"]["type"] == "choice"
    assert set(payload["questions"]["price_pressure"]["criteria"]) == {"bullish", "bearish", "neutral", "uncertain"}
    assert payload["questions"]["relevance"]["type"] == "noul"
    assert "statement" not in payload["questions"]["relevance"]
    assert "KC" in payload["questions"]["price_pressure"]["instructions"]


def test_payload_bounds_metadata_before_the_gateway_request():
    payload = jev.request_payload("t" * 700, "s" * 1400)
    assert len(payload["state"]["title"]) == jev.MAX_TITLE_CHARS
    assert len(payload["state"]["summary"]) == jev.MAX_SUMMARY_CHARS


def test_yahoo_kc_adapter_uses_only_provided_metadata(monkeypatch):
    import yfinance

    calls = []
    class Ticker:
        def get_news(self, **kwargs):
            calls.append(kwargs)
            return [{"id": "provider-id", "content": {
                "title": "Brazil output weighs on coffee prices", "summary": "Provided short summary.",
                "pubDate": "2026-09-25T18:18:14Z", "provider": {"displayName": "Barchart"},
                "canonicalUrl": {"url": "https://www.barchart.com/story/coffee", "lang": "en-US"},
                "clickThroughUrl": {"url": "https://finance.yahoo.com/ignored", "lang": "en-US"},
            }}]
    monkeypatch.setattr(yfinance, "Ticker", lambda symbol: Ticker())
    monkeypatch.setattr(jev, "_now", lambda: "2026-09-26T00:00:00Z")
    records = jev._fetch_yahoo(date(2026, 9, 24), date(2026, 9, 26), 199)
    assert calls == [{"count": 199, "tab": "news"}]
    assert records == [{"url": "https://www.barchart.com/story/coffee", "title": "Brazil output weighs on coffee prices", "summary": "Provided short summary.", "source": "yahoo_kc_news", "publisher": "Barchart", "language": "en-US", "published_at": "2026-09-25T18:18:14Z"}]


class _Response:
    status_code = 200
    headers = {}
    def json(self): return _answer()


class _Session:
    def __init__(self, articles=None, post_status=200): self.articles, self.post_status, self.posts = articles or [], post_status, 0
    def get(self, *_args, **_kwargs):
        class Response:
            status_code = 200
            def json(inner): return {"articles": self.articles}
        return Response()
    def post(self, *_args, **_kwargs):
        self.posts += 1
        response = _Response(); response.status_code = self.post_status.pop(0) if isinstance(self.post_status, list) else self.post_status
        return response


def _article(url="https://example.test/a?utm_source=x", title="Arabica frost threatens harvest"):
    return {"url": url, "title": title, "seendate": "20260926010203", "language": "English"}


def test_collect_caches_success_and_deduplicates_url_and_title(tmp_path):
    path = tmp_path / "jev.json"
    session = _Session([_article(), _article("https://example.test/a", "Other"), _article("https://two.test/a", "Arabica frost threatens harvest")])
    records, status = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=session, api_key="fixture")
    assert status["source_status"] == status["classification_status"] == "success"
    assert status["new_records"] == session.posts == 1
    assert records[0]["time_basis"] == "discovered_at"
    assert records[0]["available_at"] >= records[0]["analyzed_at"]
    assert set(jev.REQUIRED_KEYS).issubset(records[0])
    assert jev._status_path(path).exists()
    saved_status = json.loads(jev._status_path(path).read_text())
    assert saved_status["requested_start"] == "2026-09-01"
    assert saved_status["truncated"] is False
    assert "not complete coverage" in saved_status["sampling"]
    again, second = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=session, api_key="fixture")
    assert len(again) == 1 and second["new_records"] == 0 and session.posts == 1


def test_new_prompt_version_keeps_first_analysis_and_reclassifies(tmp_path, monkeypatch):
    path = tmp_path / "jev.json"
    first = _Session([_article()])
    original, _ = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=first, api_key="fixture")
    monkeypatch.setattr(jev, "PROMPT_VERSION", "arabica-kc-futures-v2")
    second = _Session([_article()])
    records, status = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=second, api_key="fixture")
    assert len(records) == 2 and status["new_records"] == second.posts == 1
    assert records[0]["analysis_id"] == original[0]["analysis_id"]
    assert {row["prompt_version"] for row in records} == {"arabica-kc-futures-v1", "arabica-kc-futures-v2"}


def test_auth_failure_stops_and_never_writes_partial_answer(tmp_path):
    path = tmp_path / "jev.json"
    records, status = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=_Session([_article()], 401), api_key="fixture")
    assert records == []
    assert status["classification_status"] == "failed"
    assert not path.exists()


def test_rate_limit_stops_current_batch_and_preserves_collected_metadata(tmp_path):
    path = tmp_path / "jev.json"
    session = _Session([_article(), _article("https://two.test/a", "Arabica drought cuts supply")], [200, 429])
    records, status = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=session, api_key="fixture")
    assert len(records) == 1 and session.posts == 2
    assert status["classification_status"] == "partial"
    assert status["errors"] == ["Jev Gateway rate limit"]
    collected = json.loads(jev._collected_path(path).read_text())
    assert len(collected["articles"]) == 2
    assert collected["articles"][0]["title"] == "Arabica frost threatens harvest"
    assert "analyzed_at" not in collected["articles"][0]


def test_retry_after_is_bounded_and_exposed_without_waiting():
    class Session:
        def post(self, *_args, **_kwargs):
            response = _Response()
            response.status_code = 429
            response.headers = {"Retry-After": "99999"}
            return response
    with pytest.raises(jev.JevRateLimitError) as raised:
        jev.classify_article({"url": "https://a.test", "title": "Coffee", "discovered_at": "2026-01-01T00:00:00Z"}, session=Session(), api_key="fixture")
    assert raised.value.retry_after_seconds == 3600


def test_invalid_cache_and_invalid_probabilities_are_rejected(tmp_path):
    path = tmp_path / "broken.json"
    path.write_text("{}")
    with pytest.raises(ValueError, match="JSON list"):
        jev.read_records(path)
    with pytest.raises(jev.JevStopError, match="probabilities"):
        jev._response_record(jev._article_fields({"url": "https://a.test", "title": "coffee", "discovered_at": "2026-01-01T00:00:00Z"}), {"answers": {"price_pressure": {"choice": "bullish", "probabilities": {"bullish": 2, "bearish": 0, "neutral": 0, "uncertain": 0}, "confidence": 1}, "relevance": {"noul": 1}}}, "2026-01-01T00:00:01Z")


def test_source_failure_is_distinct_from_empty_result(tmp_path, monkeypatch):
    path = tmp_path / "jev.json"
    monkeypatch.setattr(jev, "_fetch_gdelt", lambda *_args: (_ for _ in ()).throw(RuntimeError("down")))
    _, failed = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=_Session(), api_key="fixture")
    assert failed["source_status"] == "failed" and failed["classification_status"] == "not_started"
    monkeypatch.setattr(jev, "_fetch_gdelt", lambda *_args: [])
    _, empty = jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=_Session(), api_key="fixture")
    assert empty["source_status"] == empty["classification_status"] == "empty"


def test_held_cache_lock_fails_before_any_source_or_gateway_request(tmp_path, monkeypatch):
    path = tmp_path / "jev.json"
    session = _Session([_article()])
    monkeypatch.setattr(jev.fcntl, "flock", lambda *_args: (_ for _ in ()).throw(BlockingIOError()))
    with pytest.raises(jev.JevError, match="already being collected"):
        jev.collect_and_classify(path, date(2026, 9, 1), date(2026, 9, 2), source="gdelt", session=session, api_key="fixture")
    assert session.posts == 0
