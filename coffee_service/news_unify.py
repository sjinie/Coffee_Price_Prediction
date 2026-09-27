"""Combine completed Jev backfill checkpoints into one research dataset."""

from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile

from coffee_service import jev, news_backfill


DATA = news_backfill.DATA
OUTPUT = news_backfill.ROOT / "data/processed/jev_news"
HISTORICAL = "validation-2022-2025"
RECENT = "year-20250926-20260925"


def _key(record):
    return record["article_id"], record["content_hash"], record["selection_date"]


def _split(day):
    year = int(day[:4])
    if 2022 <= year <= 2023:
        return "validation_2022_2023"
    if 2024 <= year <= 2025:
        return "seen_test_2024_2025"
    if year == 2026:
        return "research_2026"
    raise ValueError(f"Selection date outside research window: {day}")


def _job_records(data, name, cap):
    job = data / name
    selected = news_backfill.selected_records(job, cap)
    if selected is None or not (job / "results.json").exists():
        raise ValueError(f"Incomplete checkpoint: {name}")
    results = jev.read_records(job / "results.json")
    selected_keys = [_key(record) for record in selected]
    result_keys = [_key(record) for record in results]
    if len(set(selected_keys)) != len(selected_keys) or len(set(result_keys)) != len(result_keys):
        raise ValueError(f"Duplicate selection/result key: {name}")
    if set(selected_keys) != set(result_keys):
        raise ValueError(f"Selection/result mismatch: {name}")
    for record in results:
        _split(record["selection_date"])
    selected_by_key = {_key(record): record for record in selected}
    return [(selected_by_key[_key(record)], record) for record in results]


def _write_csv(path, records):
    fields = sorted({field for record in records for field in record})
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for record in records:
            writer.writerow({field: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             if isinstance(value, (list, dict)) else value
                             for field, value in record.items()})
        temporary = Path(output.name)
    temporary.replace(path)


def run(data: Path = DATA, output: Path = OUTPUT,
        reanalysis_path: Path | None = None) -> list[dict]:
    data, output = Path(data), Path(output)
    historical = _job_records(data, HISTORICAL, 1)
    recent = _job_records(data, RECENT, 2)
    versions = {(result["model"], result["prompt_version"])
                for _, result in historical + recent}
    if len(versions) != 1:
        raise ValueError("Expected exactly one Jev model/prompt version")

    grouped = {}
    for name, records in ((HISTORICAL, historical), (RECENT, recent)):
        for selected, result in records:
            grouped.setdefault(result["content_hash"], []).append((name, selected, result))
    cross_date = {content_hash for content_hash, sources in grouped.items()
                  if len({selected["selection_date"] for _, selected, _ in sources}) > 1}
    fresh = {}
    if cross_date:
        path = Path(reanalysis_path) if reanalysis_path is not None else data / "unify-reanalysis.json"
        if not path.exists():
            raise ValueError(f"Missing Jev reanalysis: {path}")
        reanalyses = jev.read_records(path)
        fresh = {record["content_hash"]: record for record in reanalyses}
        if len(fresh) != len(reanalyses) or set(fresh) != cross_date:
            raise ValueError("Jev reanalysis content hashes do not match cross-date duplicates")
        if {(record["model"], record["prompt_version"]) for record in fresh.values()} != versions:
            raise ValueError("Jev reanalysis model/prompt version mismatch")
    articles = []
    for content_hash, sources in grouped.items():
        sources.sort(key=lambda source: (source[1]["selection_date"],
                                         source[0] != HISTORICAL, source[1]["article_id"]))
        selected, representative = sources[0][1:]
        article = {**representative,
                   "backfill_jobs": [name for name in (HISTORICAL, RECENT)
                                     if any(source_name == name for source_name, _, _ in sources)],
                   "source_selections": [
                       {"job": name, **{field: source[field] for field in (
                           "selection_date", "article_id", "published_at", "url")},
                        "available_at": source.get("available_at"),
                        "selection_available_at": source.get("selection_available_at")}
                       for name, source, _ in sources],
                   "evaluation_split": _split(representative["selection_date"])}
        if content_hash in cross_date:
            analysis = fresh[content_hash]
            if analysis["analyzed_at"] <= max(result["analyzed_at"] for _, _, result in sources):
                raise ValueError("Jev reanalysis must be newer than checkpoint analyses")
            for field in ("analysis_id", "model", "prompt_version", "label", "p_bullish",
                          "p_bearish", "p_neutral", "p_uncertain", "relevance", "confidence",
                          "analyzed_at", "cost", "usage", "batch_size", "request_format"):
                if field in analysis:
                    article[field] = analysis[field]
            metadata_available = max((value for field in (
                "published_at", "modified_at", "discovered_at", "collected_at",
                "available_at", "selection_available_at")
                if (value := selected.get(field))), key=jev._as_datetime)
            article["available_at"] = max(metadata_available, analysis["analyzed_at"],
                                          key=jev._as_datetime)
        articles.append(article)
    articles.sort(key=lambda row: (
        row["selection_date"], row["event_at"], row["content_hash"], row["article_id"]))
    # ponytail: JSON is authoritative; rerun after a CSV interruption. Version both files if concurrent readers arrive.
    jev._write_records(output / "articles.json", articles)
    _write_csv(output / "articles.csv", articles)
    return articles


def main():
    print(f"Unified {len(run())} Jev news events")


if __name__ == "__main__":
    main()
