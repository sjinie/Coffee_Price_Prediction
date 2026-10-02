from datetime import date, datetime, timedelta
import json

import pytest
import requests

from coffee_service import jev_store, news_incremental as incremental


class Frame:
    def __init__(self, rows=()):
        self.rows = rows

    def to_json(self, **_kwargs):
        return json.dumps(self.rows)


def article(title, day, suffix, **extra):
    return {"title": title, "url": f"https://example.test/{suffix}",
            "published_at": f"{day}T15:00:00Z", "source": "google_news_rss", **extra}


def sources(monkeypatch, rss):
    monkeypatch.setattr(incremental.news, "fetch_wordpress", lambda *_args: Frame())
    monkeypatch.setattr(incremental.sources, "_fetch_rss_range", rss)


def test_long_gap_uses_all_windows_and_empty_days_advance(tmp_path, monkeypatch):
    calls = []

    def rss(_client, first, last, query, _collected):
        calls.append((first, last, query))
        return []

    sources(monkeypatch, rss)
    result = incremental.collect_pending(tmp_path, date(2024, 10, 1), start=date(2024, 1, 1), session=object())
    assert result == {"source_status": "success", "requested_end": "2024-10-01",
                      "coverage_end": "2024-10-01", "selected_count": 0, "limited": False,
                      "capped_days": [], "source_gaps": [], "errors": []}
    assert len(calls) == 80  # 275 days in 40 windows, two RSS queries each.
    assert all((last - first).days <= 6 for first, last, _ in calls)
    assert {path.name for path in tmp_path.iterdir()} == {"news.json", "requests.json", "responses.json", "sentiment.csv"}
    assert len(jev_store.read_news(tmp_path)["sources"]) == 120
    incremental.collect_pending(tmp_path, date(2024, 10, 1), session=object())
    assert len(calls) == 80


def test_failed_window_resumes_from_checkpoint_without_duplicate_selection(tmp_path, monkeypatch):
    start = date(2024, 1, 1)
    end = start + timedelta(days=14)
    calls = []
    fail = True

    def rss(_client, first, _last, query, _collected):
        nonlocal fail
        calls.append((first, query))
        if first == start + timedelta(days=7) and fail:
            fail = False
            raise RuntimeError("fixture failure with hidden URL")
        return [article("Coffee crop falls 10 percent", "2024-01-01", "one")] if first == start and query == incremental.sources.QUERIES[0] else []

    sources(monkeypatch, rss)
    first = incremental.collect_pending(tmp_path, end, start=start, session=object())
    assert first["source_status"] == "failed" and first["coverage_end"] == "2024-01-07"
    assert first["errors"] == ["rss 2024-01-08~2024-01-14: RuntimeError"]  # No hidden URL text.
    archive = jev_store.read_news(tmp_path)
    assert len(archive["selections"]["incremental"]) == 1
    assert any("2024-01-08_2024-01-14" in key for key in archive["sources"])
    second = incremental.collect_pending(tmp_path, end, session=object())
    assert second["source_status"] == "success" and second["coverage_end"] == "2024-01-15"
    assert len(jev_store.read_news(tmp_path)["selections"]["incremental"]) == 1
    assert sum(first == start for first, _ in calls) == 2  # First window not fetched again.


def test_wordpress_403_is_recorded_gap_while_rss_advances_coverage(tmp_path, monkeypatch):
    class Forbidden:
        status_code = 403

        def raise_for_status(self):
            raise requests.HTTPError(response=self)

    class Session:
        def get(self, *_args, **_kwargs):
            return Forbidden()

    start = date(2024, 1, 1)

    def rss(_client, first, _last, query, _collected):
        return ([article("Coffee crop falls 10 percent", "2024-01-01", "one")]
                if first == start and query == incremental.sources.QUERIES[0] else [])

    monkeypatch.setattr(incremental.news.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(incremental.sources, "_fetch_rss_range", rss)
    result = incremental.collect_pending(tmp_path, start + timedelta(days=8), start=start, session=Session())
    assert result["source_status"] == "partial" and result["coverage_end"] == "2024-01-09"
    assert result["selected_count"] == 1
    assert result["errors"] == ["wordpress 2024-01-01~2024-01-07: HTTP 403",
                                "wordpress 2024-01-08~2024-01-09: HTTP 403"]
    assert result["source_gaps"][0] == {"source": "wordpress", "start": "2024-01-01",
                                        "end": "2024-01-07", "error": "HTTP 403"}

    sources(monkeypatch, lambda *_args: [])
    recovered = incremental.collect_pending(tmp_path, start + timedelta(days=10), session=object())
    assert recovered["source_status"] == "success" and recovered["errors"] == []
    assert recovered["coverage_end"] == "2024-01-11"
    assert len(recovered["source_gaps"]) == 2  # Kept for a later WordPress backfill.


def test_retried_rss_window_does_not_keep_its_wordpress_gap(tmp_path, monkeypatch):
    start = date(2024, 1, 1)

    def wordpress(*_args):
        raise incremental.news.SourceRequestError("x", 403)

    def rss(*_args):
        raise RuntimeError("rss down")

    monkeypatch.setattr(incremental.news, "fetch_wordpress", wordpress)
    monkeypatch.setattr(incremental.sources, "_fetch_rss_range", rss)
    result = incremental.collect_pending(tmp_path, start + timedelta(days=6), start=start, session=object())
    assert result["source_status"] == "failed" and result["coverage_end"] == "2023-12-31"
    assert result["errors"] == ["wordpress 2024-01-01~2024-01-07: HTTP 403", "rss 2024-01-01~2024-01-07: RuntimeError"]
    assert result["source_gaps"] == []  # The window is retried, so nothing is final yet.


def test_supplement_code_bug_fails_collection_instead_of_becoming_gap(tmp_path, monkeypatch):
    def wordpress(*_args):
        raise TypeError("bug")

    monkeypatch.setattr(incremental.news, "fetch_wordpress", wordpress)
    monkeypatch.setattr(incremental.sources, "_fetch_rss_range", lambda *_args: [])
    with pytest.raises(TypeError):
        incremental.collect_pending(tmp_path, date(2024, 1, 7), start=date(2024, 1, 1), session=object())


def test_yahoo_failure_is_gap_and_source_errors_keep_only_status():
    day = date(2024, 1, 1)
    assert incremental._gap("rss", day, day, incremental.news.SourceRequestError("x", 503))["error"] == "HTTP 503"
    assert incremental._gap("rss", day, day, incremental.news.SourceRequestError("x", None))["error"] == "connection failed"
    gap = incremental._gap("yahoo", day, day, RuntimeError("https://hidden.example/?token=1"))
    assert incremental._message(gap) == "yahoo 2024-01-01~2024-01-01: RuntimeError"


def test_recent_yahoo_failure_does_not_block_rss_selection(tmp_path, monkeypatch):
    day = datetime.now(incremental.jev.NY_TZ).date() - timedelta(days=1)
    sources(monkeypatch, lambda _c, _f, _l, query, _col: (
        [article("Coffee harvest falls 12 percent", day, "rss")] if query == incremental.sources.QUERIES[0] else []))

    def yahoo(*_args):
        raise RuntimeError("Yahoo KC news request failed")

    monkeypatch.setattr(incremental.jev, "_fetch_yahoo", yahoo)
    result = incremental.collect_pending(tmp_path, day, start=day, session=object())
    assert result["source_status"] == "partial" and result["coverage_end"] == day.isoformat()
    assert result["selected_count"] == 1
    assert result["errors"] == [f"yahoo {day}~{day}: RuntimeError"]


def test_one_day_rss_cap_marks_limit_and_continues_later_windows(tmp_path, monkeypatch):
    start = date(2024, 1, 1)
    calls = []

    def rss(_client, first, last, query, _collected):
        calls.append((first, last, query))
        return ([article(f"Coffee crop falls {i} percent", "2024-01-01", str(i)) for i in range(100)]
                if query == incremental.sources.QUERIES[0] and first <= start <= last else [])

    sources(monkeypatch, rss)
    end = start + timedelta(days=8)
    result = incremental.collect_pending(tmp_path, end, start=start, session=object())
    assert result["source_status"] == "partial" and result["coverage_end"] == "2024-01-09"
    assert result["limited"] and result["capped_days"] == ["2024-01-01"]
    assert result["selected_count"] == 1
    assert any(first == last == start for first, last, _ in calls)
    assert any(first == date(2024, 1, 8) for first, _, _ in calls)
    archive = jev_store.read_news(tmp_path)
    assert any(len(rows) == 100 for rows in archive["sources"].values())
    assert archive["selections"]["incremental"][0]["selection_limited"] is True
    assert incremental.collect_pending(tmp_path, end, session=object())["source_status"] == "partial"


def test_frozen_groups_near_duplicates_and_numeric_updates(tmp_path, monkeypatch):
    start = date(2024, 1, 1)
    with incremental.jev._cache_lock(tmp_path / "responses.json"):
        jev_store.initialize(tmp_path)
        archive = jev_store.read_news(tmp_path)
        frozen = incremental.jev._article_fields(article("Coffee exports fall 10 percent", "2023-12-31", "frozen"))
        frozen["selection_date"] = "2023-12-31"
        archive["selections"]["validation-2022-2025"] = [frozen]
        jev_store.write_news(tmp_path, archive)

    def rss(_client, _first, _last, query, _collected):
        if query != incremental.sources.QUERIES[0]:
            return []
        return [article("Coffee exports fall 10 percent", "2024-01-01", "reprint"),
                article("Coffee exports fall 12 percent", "2024-01-01", "update"),
                article("Coffee exports fall 12 percent", "2024-01-02", "duplicate"),
                article("Coffee harvest rises 4 percent", "2024-01-03", "late", modified_at="2024-01-05T00:00:00Z")]

    sources(monkeypatch, rss)
    result = incremental.collect_pending(tmp_path, start + timedelta(days=6), start=start, session=object())
    assert result["source_status"] == "success" and result["selected_count"] == 1
    archive = jev_store.read_news(tmp_path)
    assert len(archive["selections"]["validation-2022-2025"]) == 1
    assert [(row["title"], row["selection_date"]) for row in archive["selections"]["incremental"]] == [
        ("Coffee exports fall 12 percent", "2024-01-01")]


def test_initial_cursor_uses_legacy_end_not_latest_article(tmp_path, monkeypatch):
    with incremental.jev._cache_lock(tmp_path / "responses.json"):
        jev_store.initialize(tmp_path)
        archive = jev_store.read_news(tmp_path)
        archive["selections"]["year-20250926-20260925"] = []
        archive["selection_metadata"]["year-20250926-20260925/selection-summary.json"] = {"end": "2025-12-31"}
        archive["selection_metadata"]["service"] = {"requested_end": "2026-01-02", "written_at": "2026-01-03T12:00:00Z"}
        jev_store.write_news(tmp_path, archive)
    calls = []

    def rss(_client, first, last, _query, _collected):
        calls.append((first, last))
        return []

    sources(monkeypatch, rss)
    result = incremental.collect_pending(tmp_path, date(2026, 1, 2), session=object())
    assert result["coverage_end"] == "2026-01-02"
    assert calls[0] == (date(2026, 1, 1), date(2026, 1, 2))


def test_service_requested_end_is_bounded_by_written_day_and_explicit_start(tmp_path, monkeypatch):
    with incremental.jev._cache_lock(tmp_path / "responses.json"):
        jev_store.initialize(tmp_path)
        archive = jev_store.read_news(tmp_path)
        archive["selection_metadata"]["service"] = {
            "requested_end": "2024-01-06", "written_at": "2024-01-03T15:00:00Z"}
        assert incremental._initial_coverage(archive, date(2024, 1, 5), None) == date(2024, 1, 2)
        assert incremental._initial_coverage(archive, date(2024, 1, 5), date(2024, 1, 4)) == date(2024, 1, 3)
        jev_store.write_news(tmp_path, archive)
    calls = []

    def rss(_client, first, last, _query, _collected):
        calls.append((first, last))
        return []

    sources(monkeypatch, rss)
    result = incremental.collect_pending(tmp_path, date(2024, 1, 5), start=date(2024, 1, 4), session=object())
    assert result["coverage_end"] == "2024-01-05"
    assert calls[0] == (date(2024, 1, 4), date(2024, 1, 5))


def test_recent_day_uses_two_slots_without_near_duplicate(tmp_path, monkeypatch):
    day = date(2025, 10, 1)

    def rss(_client, _first, _last, query, _collected):
        if query != incremental.sources.QUERIES[0]:
            return []
        return [article("Coffee exports fall 10 percent", day, "one"),
                article("Coffee exports fall 10 percent", day, "reprint"),
                article("Coffee harvest rises 12 percent", day, "two"),
                article("Coffee crop drought cuts supply 15 percent", day, "three")]

    sources(monkeypatch, rss)
    result = incremental.collect_pending(tmp_path, day, start=day, session=object())
    assert result["selected_count"] == 2
    rows = jev_store.read_news(tmp_path)["selections"]["incremental"]
    assert len(rows) == 2 and len({row["content_hash"] for row in rows}) == 2
    assert all(row["selection_date"] == "2025-10-01" for row in rows)


def test_rss_range_has_ny_boundary_pad_and_monthly_api_stays_available():
    class Response:
        content = b"<rss><channel/></rss>"

        def raise_for_status(self):
            pass

    class Session:
        def __init__(self):
            self.queries = []

        def get(self, _url, *, params, timeout):
            self.queries.append(params["q"])
            assert timeout == (10, 30)
            return Response()

    client = Session()
    incremental.sources._fetch_rss_range(client, date(2024, 1, 2), date(2024, 1, 8), "coffee", "2026-09-27T00:00:00Z")
    incremental.sources._fetch_rss(client, date(2024, 1, 1), "coffee", "2026-09-27T00:00:00Z")
    assert client.queries == ["coffee after:2024-01-01 before:2024-01-10",
                              "coffee after:2023-12-31 before:2024-02-02"]


def test_recent_yahoo_enrichment_uses_same_archive(tmp_path, monkeypatch):
    day = datetime.now(incremental.jev.NY_TZ).date() - timedelta(days=1)
    sources(monkeypatch, lambda *_args: [])
    calls = []

    def yahoo(first, last, limit):
        calls.append((first, last, limit))
        return [article("Coffee harvest falls 12 percent", day, "yahoo", source="yahoo_kc_news")]

    monkeypatch.setattr(incremental.jev, "_fetch_yahoo", yahoo)
    result = incremental.collect_pending(tmp_path, day, start=day, session=object())
    assert calls == [(day, day, 1000)]
    assert result["source_status"] == "success" and result["selected_count"] == 1
    archive = jev_store.read_news(tmp_path)
    assert any("/yahoo/" in key for key in archive["sources"])
    assert archive["selections"]["incremental"][0]["source"] == "yahoo_kc_news"


@pytest.mark.parametrize('fail', [False, True])
def test_slower_collector_does_not_rewind_another_collectors_checkpoint(tmp_path, monkeypatch, fail):
    newer_completed = False

    def rss(*_args):
        nonlocal newer_completed
        if not newer_completed:
            newer_completed = True
            incremental.collect_pending(tmp_path, date(2024, 1, 20),
                                        start=date(2024, 1, 1), session=object())
            if fail:
                raise RuntimeError('older query failed after newer collector finished')
        return []

    sources(monkeypatch, rss)
    result = incremental.collect_pending(tmp_path, date(2024, 1, 7),
                                         start=date(2024, 1, 1), session=object())
    assert result['coverage_end'] == '2024-01-20'
    assert result['source_status'] == 'success'
    assert jev_store.read_news(tmp_path)['selection_metadata']['incremental']['coverage_end'] == '2024-01-20'


def test_shorter_concurrent_run_preserves_failure_beyond_same_coverage(tmp_path, monkeypatch):
    nested = False
    def rss(_client, first, *_args):
        nonlocal nested
        if not nested:
            nested = True
            result = incremental.collect_pending(tmp_path, date(2024, 1, 14),
                                                  start=date(2024, 1, 1), session=object())
            assert result['source_status'] == 'failed'
        if first >= date(2024, 1, 8):
            raise RuntimeError('failed second window')
        return []
    sources(monkeypatch, rss)
    result = incremental.collect_pending(tmp_path, date(2024, 1, 7),
                                         start=date(2024, 1, 1), session=object())
    assert result['coverage_end'] == '2024-01-07'
    assert result['requested_end'] == '2024-01-14'
    assert result['source_status'] == 'failed'
    assert jev_store.read_news(tmp_path)['selection_metadata']['incremental']['source_status'] == 'failed'
    sources(monkeypatch, lambda *_args: [])
    recovered = incremental.collect_pending(tmp_path, date(2024, 1, 14), session=object())
    assert recovered['coverage_end'] == '2024-01-14' and recovered['source_status'] == 'success'
