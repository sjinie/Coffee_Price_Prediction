from datetime import date
import json
from pathlib import Path

import pytest

from coffee_service import jev


def _answer(label="bullish"):
    pressure = {"type": "choice", "choice": label, "probabilities": {"bullish": .7, "bearish": .1, "neutral": .1, "uncertain": .1}, "confidence": .7}
    return {"answers": {"article_0_price_pressure": pressure, "article_0_relevance": {"type": "noul", "noul": .9}}, "usage": {"input_tokens": 12, "output_tokens": 3}, "provider_metadata": {"gateway": {"cost": 0.01}}}


def test_payload_is_typesafe_choice_and_noul_with_market_specific_instructions():
    payload = jev.request_payload("Coffee frost threatens crop", "A short summary")
    assert payload["model"] == "typesafe-ai/jev"
    assert payload["questions"]["price_pressure"]["type"] == "choice"
    assert set(payload["questions"]["price_pressure"]["criteria"]) == {"bullish", "bearish", "neutral", "uncertain"}
    assert payload["questions"]["relevance"]["type"] == "noul"
    assert "statement" not in payload["questions"]["relevance"]
    assert "KC" in payload["questions"]["price_pressure"]["instructions"]["question"]


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


def test_yahoo_date_filter_uses_new_york_not_utc(monkeypatch):
    import yfinance
    class Ticker:
        def get_news(self, **_kwargs):
            return [{"content": {"title": "Arabica frost cuts harvest", "summary": "", "pubDate": "2026-01-02T02:00:00Z", "canonicalUrl": {"url": "https://a.test/story", "lang": "en"}}}]
    monkeypatch.setattr(yfinance, "Ticker", lambda _symbol: Ticker())
    monkeypatch.setattr(jev, "_now", lambda: "2026-01-03T00:00:00Z")
    assert jev._fetch_yahoo(date(2026, 1, 1), date(2026, 1, 1), 200)
    assert not jev._fetch_yahoo(date(2026, 1, 2), date(2026, 1, 2), 200)


def test_near_duplicate_preserves_opposite_direction_word_forms():
    left = jev._article_fields({"url": "https://a.test/one", "title": "Coffee prices rise as exports tighten", "discovered_at": "2026-01-01T00:00:00Z"})
    right = jev._article_fields({"url": "https://a.test/two", "title": "Coffee prices drop as exports recover", "discovered_at": "2026-01-02T00:00:00Z"})
    assert not jev._near_duplicate(left, right)
    assert jev._near_duplicate(left, {**left, "title": left["title"].replace("rise", "rises")})
    assert jev._near_duplicate(right, {**right, "title": right["title"].replace("drop", "fall")})
    assert not jev._near_duplicate({**left, "title": "Coffee supply rises and demand falls"},
                                  {**left, "title": "Coffee supply falls and demand rises"})


def test_batch_questions_are_independent_and_cost_is_not_duplicated():
    sent = []
    pressure = {"type": "choice", "choice": "bullish", "probabilities": {"bullish": .7, "bearish": .1, "neutral": .1, "uncertain": .1}, "confidence": .7}
    class Session:
        def post(self, _url, **kwargs):
            sent.append(kwargs["json"])
            class Response:
                status_code, headers = 200, {}
                def json(self): return {"answers": {"article_0_price_pressure": pressure, "article_0_relevance": {"type": "noul", "noul": .9}, "article_1_price_pressure": pressure, "article_1_relevance": {"type": "noul", "noul": .8}}, "usage": {"input_tokens": 30}, "provider_metadata": {"gateway": {"cost": .02}}}
            return Response()
    records = [{"url": "https://a.test/one", "title": "Arabica frost cuts harvest", "discovered_at": "2026-01-01T00:00:00Z"}, {"url": "https://a.test/two", "title": "Coffee exports fall", "discovered_at": "2026-01-02T00:00:00Z"}]
    result = jev.classify_articles(records, session=Session(), api_key="fixture")
    assert len(result) == 2 and result[0]["cost"] == .02 and result[1]["cost"] is None and result[1]["usage"] == {}
    assert sent[0]["state"] == {"market": "Arabica Coffee Futures (KC)"}
    assert sent[0]["questions"]["article_0_price_pressure"]["instructions"]["article"]["title"] != sent[0]["questions"]["article_1_price_pressure"]["instructions"]["article"]["title"]
    with pytest.raises(ValueError, match="20"):
        jev.classify_articles(records * 11, session=Session(), api_key="fixture")
    large = [{**records[0], "title": "가" * 512, "summary": "가" * 1200}] * 20
    with pytest.raises(jev.JevStopError, match="reduce batch_size"):
        jev.classify_articles(large, session=Session(), api_key="fixture")
    assert len(sent) == 1
    pressure["type"] = "score"
    with pytest.raises(jev.JevStopError, match="answer types"):
        jev.classify_articles(records, session=Session(), api_key="fixture")


def test_daily_selection_uses_new_york_completed_days_rank_and_selection_availability():
    records = [
        {"url": "https://a.test/cafe", "title": "Coffee cafe opens", "published_at": "2026-01-02T04:30:00Z"},
        {"url": "https://a.test/frost", "title": "Arabica frost cuts harvest", "summary": "Brazil supply risk", "published_at": "2026-01-02T03:00:00Z"},
        {"url": "https://a.test/today", "title": "Coffee export falls", "published_at": "2026-01-03T16:00:00Z"},
    ]
    selected, summary = jev.select_daily_articles(records, date(2026, 1, 1), date(2026, 1, 3), now=jev._as_datetime("2026-01-03T18:00:00Z"))
    assert [item["selection_date"] for item in selected] == ["2026-01-01"]
    assert selected[0]["title"] == "Arabica frost cuts harvest"
    assert selected[0]["selection_available_at"] == "2026-01-02T05:00:00Z"
    assert summary["missing_days"] == ["2026-01-02"]


def test_daily_selection_deduplicates_republication_but_keeps_numeric_and_direction_updates():
    records = [
        {"url": "https://a.test/one", "title": "Coffee market update", "summary": "Exports fall 10 percent", "published_at": "2026-01-01T15:00:00Z"},
        {"url": "https://b.test/reprint", "title": "Coffee market update", "summary": "Exports fall 10 percent", "published_at": "2026-01-02T15:00:00Z"},
        {"url": "https://c.test/update", "title": "Coffee market update", "summary": "Exports rise 12 percent", "published_at": "2026-01-03T15:00:00Z"},
    ]
    selected, summary = jev.select_daily_articles(records, date(2026, 1, 1), date(2026, 1, 3), now=jev._as_datetime("2026-01-05T00:00:00Z"))
    assert [item["url"] for item in selected] == ["https://a.test/one", "https://c.test/update"]
    assert summary["duplicates"]["content"] == 1


def test_selection_excludes_retail_equipment_equity_titles_and_keeps_url_updates():
    records = [
        {"url": "https://a.test/cafe", "title": "Necessity Coffee Meets Demand with Two San Diego-Area Cafes", "published_at": "2026-01-01T15:00:00Z"},
        {"url": "https://a.test/grinder", "title": "April Coffee Springs a Manual Grinder Into Production", "published_at": "2026-01-02T15:00:00Z"},
        {"url": "https://a.test/equity", "title": "Is Luckin Coffee (OTCPK:LKNC.Y) Attractive After Earnings", "published_at": "2026-01-03T15:00:00Z"},
        {"url": "https://a.test/update", "title": "Coffee market update", "summary": "Exports fall 10 percent", "published_at": "2026-01-01T16:00:00Z"},
        {"url": "https://a.test/update", "title": "Coffee market update", "summary": "Exports rise 12 percent", "published_at": "2026-01-02T16:00:00Z"},
    ]
    selected, _ = jev.select_daily_articles(records, date(2026, 1, 1), date(2026, 1, 3), now=jev._as_datetime("2026-01-05T00:00:00Z"))
    assert [item["url"] for item in selected] == ["https://a.test/update", "https://a.test/update"]
    assert [item["summary"] for item in selected] == ["Exports fall 10 percent", "Exports rise 12 percent"]


def test_candidates_file_needs_no_source_fetch_and_selected_reader_overlays_cutoff(tmp_path, monkeypatch):
    path = tmp_path / "jev.json"
    candidates = tmp_path / "candidates.json"
    candidates.write_text(json.dumps([_article()]))
    monkeypatch.setattr(jev, "_fetch_yahoo", lambda *_args: (_ for _ in ()).throw(AssertionError("source fetch")))
    records, status = jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 4), candidates_path=candidates, session=_Session(), api_key="fixture")
    assert status["source_count"] == 1 and len(records) == 1
    selected = jev.read_selected_records(path, date(2026, 1, 1), date(2026, 1, 4))
    assert selected[0]["selection_date"] == "2026-01-02"
    assert selected[0]["available_at"] >= selected[0]["selection_available_at"]


def test_rolling_window_does_not_select_reprint_of_frozen_previous_day(tmp_path):
    path = tmp_path / "jev.json"
    first_path, second_path = tmp_path / "first.json", tmp_path / "second.json"
    first_path.write_text(json.dumps([{**_article("https://a.test/first", "Arabica frost cuts harvest"), "summary": "Exports fall 10 percent"}]))
    second_path.write_text(json.dumps([{**_article("https://b.test/reprint", "Arabica frost cuts harvest"), "summary": "Exports fall 10 percent", "seendate": "20260103120203"}]))
    first = _Session()
    jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 2), candidates_path=first_path, session=first, api_key="fixture")
    second = _Session()
    active, status = jev.collect_and_classify(path, date(2026, 1, 3), date(2026, 1, 4), candidates_path=second_path, session=second, api_key="fixture")
    assert active == [] and second.posts == 0
    assert status["selected_count"] == status["selected_pending_count"] == 0
    assert "2026-01-03" in status["missing_days"]


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
    return {"url": url, "title": title, "seendate": "20260102120203", "language": "English"}


def test_collect_caches_success_and_deduplicates_url_and_title(tmp_path):
    path = tmp_path / "jev.json"
    session = _Session([_article(), _article("https://example.test/a", "Other"), _article("https://two.test/a", "Arabica frost threatens harvest")])
    records, status = jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 4), source="gdelt", session=session, api_key="fixture")
    assert status["source_status"] == status["classification_status"] == "success"
    assert status["new_records"] == session.posts == 1
    assert records[0]["time_basis"] == "discovered_at"
    assert records[0]["available_at"] >= records[0]["analyzed_at"]
    assert set(jev.REQUIRED_KEYS).issubset(records[0])
    assert jev._status_path(path).exists()
    saved_status = json.loads(jev._status_path(path).read_text())
    assert saved_status["requested_start"] == "2026-01-01"
    assert saved_status["truncated"] is False
    assert "not complete coverage" in saved_status["sampling"]
    again, second = jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 4), source="gdelt", session=session, api_key="fixture")
    assert len(again) == 1 and second["new_records"] == 0 and session.posts == 1


def test_new_prompt_version_keeps_first_analysis_and_reclassifies(tmp_path, monkeypatch):
    path = tmp_path / "jev.json"
    first = _Session([_article()])
    original, _ = jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 4), source="gdelt", session=first, api_key="fixture")
    monkeypatch.setattr(jev, "PROMPT_VERSION", "arabica-kc-futures-v3")
    second = _Session([_article()])
    records, status = jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 4), source="gdelt", session=second, api_key="fixture")
    assert len(records) == 1 and status["new_records"] == second.posts == 1
    assert records[0]["analysis_id"] != original[0]["analysis_id"]
    assert {row["prompt_version"] for row in jev.read_records(path)} == {"arabica-kc-futures-v2", "arabica-kc-futures-v3"}


def test_auth_failure_stops_and_never_writes_partial_answer(tmp_path):
    path = tmp_path / "jev.json"
    records, status = jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 4), source="gdelt", session=_Session([_article()], 401), api_key="fixture")
    assert records == []
    assert status["classification_status"] == "failed"
    assert not path.exists()


def test_rate_limit_stops_current_batch_and_preserves_collected_metadata(tmp_path, monkeypatch):
    path = tmp_path / "jev.json"
    second = {**_article("https://two.test/a", "Arabica drought cuts supply"), "seendate": "20260103120203"}
    session = _Session([_article(), second], [200, 429])
    monkeypatch.setattr(jev.time, "sleep", lambda *_args: None)
    records, status = jev.collect_and_classify(path, date(2026, 1, 1), date(2026, 1, 4), source="gdelt", session=session, api_key="fixture")
    assert len(records) == 1 and session.posts == 2
    assert records[0]["selection_date"] == "2026-01-03"
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
        jev._response_record(jev._article_fields({"url": "https://a.test", "title": "coffee", "discovered_at": "2026-01-01T00:00:00Z"}), {"answers": {"price_pressure": {"type": "choice", "choice": "bullish", "probabilities": {"bullish": 2, "bearish": 0, "neutral": 0, "uncertain": 0}, "confidence": 1}, "relevance": {"type": "noul", "noul": 1}}}, "2026-01-01T00:00:01Z")


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
