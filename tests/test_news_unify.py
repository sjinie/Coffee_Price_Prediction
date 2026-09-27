import csv
from datetime import datetime, timedelta
import json

import pytest

from coffee_service import jev, news_backfill, news_unify


def article(day, title, suffix):
    row = jev._article_fields({"url": f"https://example.com/{suffix}", "title": title,
                               "published_at": f"{day}T12:00:00Z"})
    row.update(selection_date=day, selection_available_at=f"{day}T13:00:00Z",
               available_at=max(row["collected_at"], f"{day}T13:00:00Z"),
               selection_policy="test", selection_reason="test", selection_score=1)
    return row


def result(row):
    classified = jev._response_record(row, {"answers": {
        "price_pressure": {"type": "choice", "choice": "bullish", "confidence": .7,
                           "probabilities": {"bullish": .7, "bearish": .1, "neutral": .1, "uncertain": .1}},
        "relevance": {"type": "noul", "noul": .9}}, "usage": {"input_tokens": 100}}, jev._now())
    return news_backfill.results_for([row], {news_backfill.key(classified): classified})[0]


def checkpoint(root, name, rows, results=None):
    job = root / name
    job.mkdir(parents=True, exist_ok=True)
    (job / "selected.json").write_text(json.dumps(rows))
    if results is not None:
        (job / "results.json").write_text(json.dumps(results))


def test_unify_overlap_reused_title_and_idempotent(tmp_path):
    validation = article("2022-06-01", "Coffee harvest", "validation")
    historical_only = article("2025-07-18", "Coffee crop", "old")
    overlap = article("2025-10-01", "Coffee prices rise", "overlap")
    recent_only = article("2026-08-19", "Coffee crop", "new")
    old_result, historical_overlap = result(historical_only), result(overlap)
    recent_overlap = {**historical_overlap, "selection_reason": "recent_policy"}
    checkpoint(tmp_path, news_unify.HISTORICAL, [validation, historical_only, overlap],
               [result(validation), old_result, historical_overlap])
    checkpoint(tmp_path, news_unify.RECENT, [overlap, recent_only],
               [recent_overlap, result(recent_only)])

    output = tmp_path / "out"
    with pytest.raises(ValueError, match="Missing Jev reanalysis"):
        news_unify.run(tmp_path, output)
    assert not output.exists()
    fresh = {key: value for key, value in result(historical_only).items()
             if not key.startswith("selection_")}
    analyzed = max(old_result["analyzed_at"], result(recent_only)["analyzed_at"])
    fresh["analyzed_at"] = (datetime.fromisoformat(analyzed.replace("Z", "+00:00")) +
                            timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    fresh.update(available_at=fresh["analyzed_at"], label="bearish", p_bullish=.1,
                 p_bearish=.7, relevance=.4, cost="0.123", usage={"input_tokens": 17})
    (tmp_path / "unify-reanalysis.json").write_text(json.dumps([fresh]))
    records = news_unify.run(tmp_path, output)
    assert len(records) == 3
    assert [row["evaluation_split"] for row in records] == [
        "validation_2022_2023", "seen_test_2024_2025", "seen_test_2024_2025"]
    assert records[2]["selection_reason"] == historical_overlap["selection_reason"]
    assert records[2]["backfill_jobs"] == [news_unify.HISTORICAL, news_unify.RECENT]
    assert records[1]["selection_date"] == "2025-07-18"
    assert [row["selection_date"] for row in records[1]["source_selections"]] == [
        "2025-07-18", "2026-08-19"]
    assert records[1]["backfill_jobs"] == [news_unify.HISTORICAL, news_unify.RECENT]
    assert records[1]["label"] == "bearish" and records[1]["p_bearish"] == .7
    assert records[1]["relevance"] == .4 and records[1]["cost"] == "0.123"
    assert records[1]["usage"] == {"input_tokens": 17}
    assert records[1]["analyzed_at"] == fresh["analyzed_at"]
    assert records[1]["available_at"] == max(historical_only["available_at"], fresh["analyzed_at"])
    with (output / "articles.csv").open(encoding="utf-8-sig", newline="") as stream:
        csv_rows = list(csv.DictReader(stream))
    assert json.loads(csv_rows[2]["backfill_jobs"]) == records[2]["backfill_jobs"]
    assert json.loads(csv_rows[1]["source_selections"]) == records[1]["source_selections"]
    assert json.loads(csv_rows[0]["usage"]) == records[0]["usage"]
    original = [(output / name).read_bytes() for name in ("articles.json", "articles.csv")]
    assert news_unify.run(tmp_path, output) == records
    assert [(output / name).read_bytes() for name in ("articles.json", "articles.csv")] == original


def test_same_day_overlap_needs_no_reanalysis(tmp_path):
    same = article("2025-10-01", "Coffee crop", "same")
    checkpoint(tmp_path, news_unify.HISTORICAL, [same], [result(same)])
    checkpoint(tmp_path, news_unify.RECENT, [same], [result(same)])
    rows = news_unify.run(tmp_path, tmp_path / "out")
    assert len(rows) == 1 and len(rows[0]["source_selections"]) == 2


@pytest.mark.parametrize("problem", ["missing", "incomplete", "mismatch", "duplicate", "version"])
def test_rejects_bad_checkpoints_without_writing(tmp_path, problem):
    old = article("2025-10-01", "Coffee prices rise", "old")
    new = article("2026-08-19", "Coffee crop", "new")
    historical = [result(old)]
    recent = [result(new)]
    checkpoint(tmp_path, news_unify.HISTORICAL, [old], historical)
    checkpoint(tmp_path, news_unify.RECENT, [new], recent)
    path = tmp_path / news_unify.RECENT / "results.json"
    if problem == "missing":
        path.unlink()
    elif problem == "incomplete":
        path.write_text("[]")
    elif problem == "mismatch":
        path.write_text(json.dumps(historical))
    elif problem == "duplicate":
        path.write_text(json.dumps(recent * 2))
    else:
        changed = {**recent[0], "model": "another-model"}
        changed["analysis_id"] = jev.sha256((changed["content_hash"] + "\n" + changed["model"] +
                                              "\n" + changed["prompt_version"]).encode()).hexdigest()
        path.write_text(json.dumps([changed]))
    with pytest.raises(ValueError):
        news_unify.run(tmp_path, tmp_path / "out")
    assert not (tmp_path / "out").exists()
