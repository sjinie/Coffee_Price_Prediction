from datetime import datetime, timezone
import json

import pytest

from coffee_service import news_backfill_sources as historical


NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def article(title, day, suffix, **extra):
    return {"title": title, "url": f"https://example.org/{suffix}",
            "published_at": f"{day}T15:00:00Z", "collected_at": "2026-09-26T00:00:00Z", **extra}


def test_selection_caps_ny_day_and_keeps_later_numerical_update():
    records = [
        article("Coffee exports fall 10 percent", "2022-01-01", "first"),
        article("Coffee exports fall 10 percent", "2022-01-02", "reprint"),
        article("Coffee exports fall 12 percent", "2022-01-03", "update"),
        article("Brazil agriculture drought cuts harvest", "2022-01-03", "brazil"),
        article("Coffee cafe opens", "2022-01-04", "cafe"),
        article("Coffee crop reaches 4 tons", "2022-01-05", "same-url"),
        article("Coffee crop reaches 4 tons", "2022-01-06", "same-url"),
        article("Coffee crop reaches 5 tons", "2022-01-07", "same-url"),
        article("Coffee supply falls", "2025-12-31", "late", modified_at="2026-01-02T00:00:00Z"),
    ]
    selected, status = historical.select_historical(records, NOW)
    assert [item["selection_date"] for item in selected] == ["2022-01-01", "2022-01-03", "2022-01-05", "2022-01-07"]
    assert selected[1]["title"] == "Coffee exports fall 12 percent"
    assert selected[0]["selection_available_at"] == "2022-01-02T05:00:00Z"
    assert selected[0]["available_at"] == "2026-09-26T00:00:00Z"
    assert status["duplicates"]["content"] == 1
    assert status["duplicates"]["url"] == 1
    assert "2022-01-02" in status["missing_days"]
    assert status["rejected_invalid_or_late_modified"] == 1


def test_rss_metadata_and_timezone_validation():
    xml = b'''<rss><channel><item><title>Coffee crop falls - Publisher</title>
    <link>https://news.google.com/rss/articles/abc</link><source>Publisher</source>
    <pubDate>Sat, 01 Jan 2022 15:00:00 GMT</pubDate></item></channel></rss>'''
    records = historical._rss_items(xml, "2026-09-26T00:00:00Z")
    assert records[0]["title"] == "Coffee crop falls"
    assert records[0]["publisher"] == "Publisher"
    assert records[0]["published_at"] == "2022-01-01T15:00:00+00:00"
    assert records[0]["url"].startswith("https://news.google.com/")
    with pytest.raises(ValueError, match="timezone"):
        historical._rss_items(xml.replace(b" GMT", b""), "2026-09-26T00:00:00Z")


def test_failed_month_resumes_without_freezing_or_refetching(tmp_path, monkeypatch):
    monkeypatch.setattr(historical.time, "sleep", lambda _: None)
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    (source_dir / "wordpress.json").write_text("[]", encoding="utf-8")
    calls = []
    fail_once = True

    def fetch(_session, month, query, _collected):
        nonlocal fail_once
        calls.append((month.isoformat(), query))
        if fail_once and len(calls) == 2:
            fail_once = False
            raise RuntimeError("fixture source failure")
        return [article("Brazil coffee harvest falls", "2022-01-01", "one")] if len(calls) == 1 else []

    monkeypatch.setattr(historical, "_fetch_rss", fetch)
    with pytest.raises(RuntimeError, match="fixture source failure"):
        historical.prepare_historical(tmp_path)
    assert not (tmp_path / "selected.json").exists()
    assert json.loads((tmp_path / "selected.status.json").read_text())[0]["source_status"] == "failed"
    selected = historical.prepare_historical(tmp_path)
    assert len(selected) == 1
    assert len(calls) == 97  # First query checkpoint reused; 96 total RSS queries.
    assert historical.prepare_historical(tmp_path) == selected
    assert len(calls) == 97
    status = json.loads((tmp_path / "selected.status.json").read_text())[0]
    assert status["source_status"] == "complete" and status["missing_days"]
