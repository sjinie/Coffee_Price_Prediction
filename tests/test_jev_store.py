import csv
import json

import pytest

from coffee_service import jev, jev_store


def _analysis(url="https://example.test/coffee", *, analyzed_at="2026-09-27T02:00:00Z",
              prompt=jev.PROMPT_VERSION, summary="Arabica supply falls"):
    article = jev._article_fields({
        "url": url, "title": "Arabica harvest news", "summary": summary,
        "published_at": "2025-07-18T15:00:00Z", "collected_at": "2026-09-26T00:00:00Z"})
    answer = {"answers": {
        "price_pressure": {"type": "choice", "choice": "bullish", "confidence": .7,
                           "probabilities": {"bullish": .7, "bearish": .1,
                                             "neutral": .1, "uncertain": .1}},
        "relevance": {"type": "noul", "noul": .9}}}
    record = jev._response_record(article, answer, analyzed_at)
    record["prompt_version"] = prompt
    record["analysis_id"] = jev.sha256((record["content_hash"] + "\n" + record["model"] +
                                         "\n" + prompt).encode()).hexdigest()
    return record


def _selection(record, day, available):
    return {key: record[key] for key in ("article_id", "content_hash", "url", "title",
                                          "summary", "source", "language", "published_at",
                                          "modified_at", "discovered_at", "collected_at",
                                          "event_at", "time_basis")} | {
        "selection_date": day, "selection_available_at": available,
        "available_at": available, "selection_policy": "fixture"}


def _csv(data):
    with (data / "sentiment.csv").open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def test_documents_reject_partial_or_unreadable_and_initialize_only_four(tmp_path):
    assert jev_store.read_document(tmp_path / "news.json") == {
        "schema_version": 1, "sources": {}, "selections": {},
        "selection_metadata": {}, "service_status": {}}
    jev_store.initialize(tmp_path)
    assert {path.name for path in tmp_path.iterdir()} == {
        "news.json", "requests.json", "responses.json", "sentiment.csv"}
    news = jev_store.read_document(tmp_path / "news.json")
    news["sources"]["legacy/source.json"] = {"articles": [{"raw": "untouched"}]}
    jev_store.write_document(tmp_path / "news.json", news)
    jev_store.initialize(tmp_path)
    assert jev_store.read_news(tmp_path) == news
    with pytest.raises(ValueError):
        jev_store.write_document(tmp_path / "news.json", {"schema_version": 1})
    assert jev_store.read_news(tmp_path) == news
    (tmp_path / "requests.json").write_text("{", encoding="utf-8")
    with pytest.raises(ValueError, match="unreadable"):
        jev_store.read_document(tmp_path / "requests.json")
    (tmp_path / "requests.json").write_text('{"schema_version":1}', encoding="utf-8")
    with pytest.raises(ValueError, match="attempts"):
        jev_store.read_document(tmp_path / "requests.json")
    responses = jev_store.read_responses(tmp_path)
    responses["legacy_files"]["legacy/results.json"] = [0]
    with pytest.raises(ValueError, match="legacy_files"):
        jev_store.write_responses(tmp_path, responses)


def test_historical_variants_survive_store_and_collapse_only_on_read(tmp_path):
    first = _analysis()
    first["request_id"] = "recorded-request"
    first["publisher"] = "original publisher"
    variant = {**first, "source": "rss", "available_at": "2026-09-27T03:00:00Z"}
    del variant["publisher"]
    later = _analysis(analyzed_at="2026-09-27T04:00:00Z")
    jev_store.append_analyses(tmp_path, [first, variant, later, first])
    assert len(jev_store.read_responses(tmp_path)["analyses"]) == 3
    assert len(jev_store.read_analyses(tmp_path)) == 2
    assert [row["analyzed_at"] for row in jev_store.read_analyses(tmp_path, latest=True)] == [
        "2026-09-27T04:00:00Z"]
    assert jev_store.read_analyses(tmp_path)[0]["publisher"] == "original publisher"
    assert jev_store.read_analyses(tmp_path)[0]["available_at"] == "2026-09-27T03:00:00Z"
    assert jev_store.read_responses(tmp_path)["analyses"][0]["request_id"] == "recorded-request"


def test_export_preserves_attempts_provenance_and_as_of_flags(tmp_path):
    current = _analysis()
    old_attempt = _analysis(analyzed_at="2026-09-26T02:00:00Z")
    v1 = _analysis(analyzed_at="2026-09-25T02:00:00Z", prompt="arabica-kc-futures-v1")
    probe = _analysis("https://example.test/probe", summary="")
    service = _analysis("https://example.test/service", summary="Service only")
    mismatched = {**service, "label": "bearish"}
    jev_store.append_analyses(tmp_path, [current, old_attempt, v1, probe, mismatched])
    news = jev_store.read_news(tmp_path)
    historical = _selection(current, "2025-07-18", "2025-07-19T04:00:00Z")
    historical["source"] = "historic"
    recent = _selection(current, "2026-08-19", "2026-08-20T04:00:00Z")
    recent["available_at"] = "2026-09-28T00:00:00Z"
    news["selections"] = {
        "service": [_selection(service, "2026-09-26", "2026-09-27T04:00:00Z"),
                    _selection(current, "2025-07-18", "2025-07-19T04:00:00Z")],
        "year-20250926-20260925": [recent],
        "validation-2022-2025": [historical],
    }
    jev_store.write_news(tmp_path, news)
    rows = jev_store.export(tmp_path)
    assert len(rows) == 5
    by_time = {row["analyzed_at"]: row for row in rows if row["content_hash"] == current["content_hash"]}
    selected = by_time[current["analyzed_at"]]
    assert selected["selection_date"] == "2025-07-18"
    assert selected["source"] == "historic"
    assert selected["evaluation_split"] == "seen_test_2024_2025"
    assert len(selected["source_selections"]) == 3
    assert selected["backfill_jobs"] == sorted(news["selections"])
    assert selected["available_at"] == "2026-09-28T00:00:00Z"
    assert selected["research_available_at"] == "2026-08-20T04:00:00Z"
    assert selected["selected_for_research"] and selected["is_latest_analysis"]
    assert not by_time[old_attempt["analyzed_at"]]["selected_for_research"]
    assert not by_time[v1["analyzed_at"]]["selected_for_research"]
    assert not next(row for row in rows if row["content_hash"] == probe["content_hash"])["selected_for_research"]
    service_row = next(row for row in rows if row["content_hash"] == service["content_hash"])
    assert not service_row["selected_for_research"]
    assert not service_row["label_matches_probabilities"]
    assert next(row for row in rows if row["content_hash"] == probe["content_hash"])["title_only"]
    encoded = _csv(tmp_path)
    assert len(encoded) == len(rows)
    assert json.loads(encoded[0]["source_selections"]) == rows[0]["source_selections"]
    original = (tmp_path / "sentiment.csv").read_bytes()
    assert jev_store.export(tmp_path) == rows
    assert (tmp_path / "sentiment.csv").read_bytes() == original


def test_export_validation_and_future_split(tmp_path):
    first = _analysis("https://example.test/validation")
    future = _analysis("https://example.test/future", summary="Different content")
    jev_store.append_analyses(tmp_path, [first, future])
    news = jev_store.read_news(tmp_path)
    news["selections"] = {
        "validation-2022-2025": [_selection(first, "2022-06-01", "2022-06-02T04:00:00Z")],
        "future": [_selection(future, "2027-01-01", "2027-01-02T05:00:00Z")],
    }
    jev_store.write_news(tmp_path, news)
    assert {row["evaluation_split"] for row in jev_store.export(tmp_path)} == {
        "validation_2022_2023", "research_2027"}


def test_probability_label_tolerance_keeps_original_values(tmp_path):
    close = _analysis()
    close.update(label="bearish", p_bullish=.500000005, p_bearish=.5,
                 p_neutral=0, p_uncertain=0)
    jev_store.append_analyses(tmp_path, [close])
    row = jev_store.export(tmp_path)[0]
    assert row["label_matches_probabilities"] is True
    assert row["label"] == "bearish" and row["p_bullish"] == .500000005


def test_capture_persists_before_send_and_redacts_response(tmp_path, monkeypatch):
    key = "gateway-secret-for-test"
    monkeypatch.delenv("AI_GATEWAY_API_KEY", raising=False)
    body = {"model": "typesafe-ai/jev", "questions": {"a": {"article": "coffee"}}}

    class Response:
        status_code = 429
        headers = {"Retry-After": "120", "Authorization": "never-persist"}

        def json(self):
            return {"error": {"message": f"echo {key}"}}

    class Session:
        def post(self, *args, **kwargs):
            assert jev_store.read_requests(tmp_path)["attempts"][0]["body"] == body
            assert jev_store.read_responses(tmp_path)["attempts"] == []
            return Response()

        def close(self):
            raise AssertionError("caller owns session")

    capture = jev_store.CaptureSession(tmp_path, Session())
    capture.articles = [{"content_hash": "selected-hash", "selection_date": "2026-09-26"}]
    response = capture.post("https://example.test", headers={"Authorization": f"Bearer {key}"}, json=body)
    capture.close()
    assert response.status_code == capture.last_status == 429
    assert capture.last_retry_after == "120"
    sent = jev_store.read_requests(tmp_path)["attempts"][0]
    received = jev_store.read_responses(tmp_path)["attempts"][0]
    assert sent["request_id"] == received["request_id"] == capture.last_request_id
    assert sent["body"] == body and sent["sent_at"]
    assert sent["articles"] == [{"content_hash": "selected-hash", "selection_date": "2026-09-26"}]
    assert "articles" not in sent["body"] and capture.articles is None
    assert received["body"] == {"error": {"message": "echo [REDACTED]"}}
    assert received["redacted"] is True and received["received_at"]
    assert key not in (tmp_path / "responses.json").read_text()
    assert "Authorization" not in (tmp_path / "requests.json").read_text()
    assert "Authorization" not in (tmp_path / "responses.json").read_text()


def test_capture_network_failure_records_only_safe_type(tmp_path):
    class Session:
        def post(self, *args, **kwargs):
            raise OSError("sensitive-host-and-key")

    capture = jev_store.CaptureSession(tmp_path, Session())
    with pytest.raises(OSError):
        capture.post("https://example.test", json={"a": 1})
    attempts = jev_store.read_responses(tmp_path)["attempts"]
    assert attempts == [{"request_id": capture.last_request_id,
                         "received_at": attempts[0]["received_at"], "error_type": "OSError"}]
    assert "sensitive-host-and-key" not in (tmp_path / "responses.json").read_text()


def test_capture_invalid_json_records_parse_error(tmp_path, monkeypatch):
    monkeypatch.setenv("AI_GATEWAY_API_KEY", "secret-test-key")
    class Response:
        status_code = 502
        headers = {}
        text = "Gateway error: secret-test-key"

        def json(self):
            raise ValueError("sensitive raw response")

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    jev_store.CaptureSession(tmp_path, Session()).post("https://example.test", json={})
    captured = jev_store.read_responses(tmp_path)["attempts"][0]
    assert captured["body"] == "Gateway error: [REDACTED]"
    assert captured["parse_error"] == "invalid_json" and captured["redacted"]
    assert "sensitive raw response" not in (tmp_path / "responses.json").read_text()


def test_capture_keeps_http_200_before_classification_rejects_answer(tmp_path):
    class Response:
        status_code = 200
        headers = {}

        def json(self):
            return {"answers": {"article_0_price_pressure": {"type": "choice"}}}

    class Session:
        def post(self, *args, **kwargs):
            return Response()

    article = {"url": "https://example.test/coffee", "title": "Arabica news",
               "published_at": "2026-09-26T12:00:00Z"}
    capture = jev_store.CaptureSession(tmp_path, Session())
    capture.articles = [article]
    with pytest.raises(jev.JevStopError):
        jev.classify_articles([article], session=capture, api_key="fixture-key")
    assert jev_store.read_requests(tmp_path)["attempts"][0]["articles"] == [article]
    assert jev_store.read_responses(tmp_path)["attempts"][0]["body"] == Response().json()
    assert jev_store.read_responses(tmp_path)["analyses"] == []
