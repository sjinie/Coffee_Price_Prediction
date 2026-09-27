"""Four-file Jev research store. The caller serializes writes with its own lock."""

from __future__ import annotations

import csv
from datetime import date
import json
import os
from pathlib import Path
import tempfile
from uuid import uuid4

from . import jev


DATA = Path(__file__).resolve().parents[1] / "data/jev"
_FIELDS = {
    "news": {"sources": dict, "selections": dict, "selection_metadata": dict,
             "service_status": dict},
    "requests": {"attempts": list, "legacy_events": list, "legacy_payloads": dict,
                 "worker_state": dict},
    "responses": {"analyses": list, "legacy_files": dict, "attempts": list},
}


def _empty(name):
    return {"schema_version": 1, **{field: kind() for field, kind in _FIELDS[name].items()}}


def _validate(name, value):
    if not isinstance(value, dict) or type(value.get("schema_version")) is not int or value["schema_version"] != 1:
        raise ValueError(f"Invalid Jev {name} schema_version")
    for field, kind in _FIELDS[name].items():
        if not isinstance(value.get(field), kind):
            raise ValueError(f"Invalid Jev {name}.{field}")
    if name == "news":
        if any(not isinstance(key, str) for key in value["sources"]):
            raise ValueError("Invalid Jev news.sources")
        if any(not isinstance(key, str) or not isinstance(rows, list) or
               any(not isinstance(row, dict) for row in rows)
               for key, rows in value["selections"].items()):
            raise ValueError("Invalid Jev news.selections")
    if name == "responses":
        if any(not isinstance(row, dict) for row in value["analyses"] + value["attempts"]):
            raise ValueError("Invalid Jev responses records")
        if any(not isinstance(key, str) or not isinstance(indices, list) or
               any(type(index) is not int or index < 0 or index >= len(value["analyses"])
                   for index in indices)
               for key, indices in value["legacy_files"].items()):
            raise ValueError("Invalid Jev responses.legacy_files")
    if name == "requests":
        if any(not isinstance(row, dict) for row in value["attempts"] + value["legacy_events"]):
            raise ValueError("Invalid Jev requests records")
        if any(not isinstance(key, str) for key in value["legacy_payloads"]):
            raise ValueError("Invalid Jev requests.legacy_payloads")
    return value


def _read(data, name):
    path = Path(data) / f"{name}.json"
    if not path.exists():
        return _empty(name)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Jev {name}.json is unreadable") from exc
    return _validate(name, value)


def _write(data, name, value):
    jev._write_records(Path(data) / f"{name}.json", _validate(name, value))


def read_document(path, default=None):
    """Read a versioned store document; reject malformed existing files."""
    path = Path(path)
    name = path.stem
    if name not in _FIELDS or path.suffix != ".json":
        raise ValueError("Unknown Jev document")
    if not path.exists() and default is not None:
        return _validate(name, default)
    return _read(path.parent, name)


def write_document(path, value):
    path = Path(path)
    name = path.stem
    if name not in _FIELDS or path.suffix != ".json":
        raise ValueError("Unknown Jev document")
    _write(path.parent, name, value)


def read_news(data=DATA):
    return _read(data, "news")


def write_news(data, value):
    _write(data, "news", value)


def read_requests(data=DATA):
    return _read(data, "requests")


def write_requests(data, value):
    _write(data, "requests", value)


def read_responses(data=DATA):
    return _read(data, "responses")


def write_responses(data, value):
    _write(data, "responses", value)


def _groups(data):
    groups = {}
    for raw in read_responses(data)["analyses"]:
        record = jev._valid_record(raw)
        groups.setdefault((record["analysis_id"], record["analyzed_at"]), []).append(record)
    return groups


def _representative(variants):
    chosen = max(variants, key=lambda row: (
        len([value for value in row.values() if value is not None]),
        jev._as_datetime(row["available_at"]),
        json.dumps(row, ensure_ascii=False, sort_keys=True)))
    return {**chosen, "available_at": _max_time(row["available_at"] for row in variants)}


def read_analyses(data=DATA, latest=False):
    """Collapse source metadata variants, then optionally keep each ID's latest run."""
    rows = [_representative(variants) for variants in _groups(data).values()]
    if latest:
        by_id = {}
        for row in rows:
            key = row["analysis_id"]
            if key not in by_id or row["analyzed_at"] > by_id[key]["analyzed_at"]:
                by_id[key] = row
        rows = list(by_id.values())
    return sorted(rows, key=lambda row: (row["analyzed_at"], row["analysis_id"]))


def append_analyses(data, records):
    store = read_responses(data)
    seen = {json.dumps(row, ensure_ascii=False, sort_keys=True) for row in store["analyses"]}
    for raw in records:
        row = jev._valid_record(raw)
        encoded = json.dumps(row, ensure_ascii=False, sort_keys=True)
        if encoded not in seen:
            store["analyses"].append(row)
            seen.add(encoded)
    write_responses(data, store)


def _max_time(values):
    return max((value for value in values if value), key=jev._as_datetime)


def _split(day):
    year = date.fromisoformat(day).year
    if year < 2022:
        raise ValueError("Selection date precedes research window")
    if year in (2022, 2023):
        return "validation_2022_2023"
    if year in (2024, 2025):
        return "seen_test_2024_2025"
    return f"research_{year}"


def export(data=DATA):
    """Write one deterministic CSV row per distinct Jev analysis attempt."""
    data = Path(data)
    news = read_news(data)
    groups = _groups(data)
    selections = {}
    for job, rows in news["selections"].items():
        for selected in rows:
            content_hash = selected.get("content_hash")
            if not content_hash:
                raise ValueError("Jev selection lacks content_hash")
            selections.setdefault(content_hash, []).append({"job": job, **selected})
    for sources in selections.values():
        sources.sort(key=lambda row: (row.get("selection_date") or "9999-12-31",
                                      row["job"] != "validation-2022-2025", row["job"] == "service", row["job"],
                                      row.get("article_id") or "", json.dumps(row, sort_keys=True)))

    latest = {}
    for analysis_id, analyzed_at in groups:
        record = groups[(analysis_id, analyzed_at)][0]
        content_hash = record["content_hash"]
        candidate = (analyzed_at, record["model"] == jev.MODEL and
                     record["prompt_version"] == jev.PROMPT_VERSION, analysis_id)
        latest[content_hash] = max(latest.get(content_hash, ("", False, "")), candidate)
    rows = []
    for (analysis_id, analyzed_at), variants in groups.items():
        row = dict(_representative(variants))
        source_selections = selections.get(row["content_hash"], [])
        if source_selections:
            selected = source_selections[0]
            for field in ("article_id", "url", "title", "summary", "source", "language",
                          "published_at", "modified_at", "discovered_at", "collected_at",
                          "event_at", "time_basis", "publisher", "selection_policy",
                          "selection_reason", "selection_score", "selection_available_at"):
                if field in selected:
                    row[field] = selected[field]
        selected_dates = [item["selection_date"] for item in source_selections
                          if item.get("selection_date")]
        if selected_dates:
            selection_date = min(date.fromisoformat(day) for day in selected_dates).isoformat()
        elif row.get("selection_date"):
            selection_date = date.fromisoformat(row["selection_date"]).isoformat()
        else:
            selection_date = jev._as_datetime(row["event_at"]).astimezone(jev.NY_TZ).date().isoformat()
        row["selection_date"] = selection_date
        row["source_selections"] = source_selections
        row["backfill_jobs"] = sorted({item["job"] for item in source_selections})
        row["evaluation_split"] = _split(selection_date)
        row["available_at"] = _max_time(
            [item.get("available_at") for item in variants] + [analyzed_at] +
            [item.get(field) for item in source_selections for field in (
                "available_at", "published_at", "modified_at", "discovered_at",
                "collected_at", "selection_available_at")])
        row["research_available_at"] = _max_time(
            [item.get(field) for item in variants for field in
             ("event_at", "modified_at", "selection_available_at")] +
            [item.get(field) for item in source_selections for field in
             ("event_at", "modified_at", "selection_available_at")])
        row["title_only"] = not bool(row["summary"].strip())
        row["label_matches_probabilities"] = (
            row[f"p_{row['label']}"] >= max(row[f"p_{label}"] for label in
            ("bullish", "bearish", "neutral", "uncertain")) - 1e-8)
        row["is_latest_analysis"] = (analyzed_at, row["model"] == jev.MODEL and
                                     row["prompt_version"] == jev.PROMPT_VERSION,
                                     analysis_id) == latest[row["content_hash"]]
        row["selected_for_research"] = (
            row["is_latest_analysis"] and row["model"] == jev.MODEL and
            row["prompt_version"] == jev.PROMPT_VERSION and
            any(item["job"] != "service" for item in source_selections))
        rows.append(row)
    rows.sort(key=lambda row: (row["selection_date"], row["content_hash"],
                               row["analyzed_at"], row["analysis_id"]))
    path = data / "sentiment.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = sorted({field for row in rows for field in row})
    with tempfile.NamedTemporaryFile("w", encoding="utf-8-sig", newline="", dir=path.parent,
                                     prefix=path.name + ".", suffix=".tmp", delete=False) as output:
        writer = csv.DictWriter(output, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: json.dumps(value, ensure_ascii=False, sort_keys=True)
                             if isinstance(value, (list, dict)) else value
                             for field, value in row.items()})
        temporary = Path(output.name)
    temporary.replace(path)
    return rows


def initialize(data=DATA):
    """Create missing store files without replacing existing content."""
    data = Path(data)
    for name in _FIELDS:
        if (data / f"{name}.json").exists():
            _read(data, name)
        else:
            _write(data, name, _empty(name))
    if not (data / "sentiment.csv").exists():
        export(data)


def _redact(value, key):
    if not key:
        return value, False
    if isinstance(value, str):
        changed = key in value
        return value.replace(key, "[REDACTED]"), changed
    if isinstance(value, list):
        parts = [_redact(item, key) for item in value]
        return [item for item, _ in parts], any(changed for _, changed in parts)
    if isinstance(value, dict):
        parts = [(_redact(name, key), _redact(item, key)) for name, item in value.items()]
        return {name[0]: item[0] for name, item in parts}, any(
            name[1] or item[1] for name, item in parts)
    return value, False


class CaptureSession:
    """Persist request and response evidence around a caller-owned HTTP session."""

    def __init__(self, data, session, api_key=None):
        self.data = Path(data)
        self.session = session
        self.api_key = api_key
        self.articles = None
        self.last_request_id = None
        self.last_status = None
        self.last_retry_after = None

    def post(self, *args, **kwargs):
        if "json" not in kwargs:
            raise ValueError("CaptureSession.post requires a JSON body")
        request_id = str(uuid4())
        self.last_request_id, self.last_status, self.last_retry_after = request_id, None, None
        request = {"request_id": request_id, "sent_at": jev._now(), "body": kwargs["json"]}
        if self.articles is not None:
            if not isinstance(self.articles, list) or any(not isinstance(row, dict) for row in self.articles):
                raise ValueError("CaptureSession.articles must be a list of objects")
            request["articles"] = self.articles
        store = read_requests(self.data)
        store["attempts"].append(request)
        write_requests(self.data, store)
        self.articles = None
        try:
            response = self.session.post(*args, **kwargs)
        except Exception as exc:
            store = read_responses(self.data)
            store["attempts"].append({"request_id": request_id, "received_at": jev._now(),
                                      "error_type": type(exc).__name__})
            write_responses(self.data, store)
            raise
        self.last_status = response.status_code
        self.last_retry_after = getattr(response, "headers", {}).get("Retry-After")
        try:
            body = response.json()
        except ValueError:
            body = getattr(response, "text", None)
            parse_error = "invalid_json"
        else:
            parse_error = None
        header_key = next((value.removeprefix("Bearer ") for name, value in
                           (kwargs.get("headers") or {}).items()
                           if name.lower() == "authorization" and
                           isinstance(value, str) and value.startswith("Bearer ")), None)
        redacted = False
        for key in (self.api_key, os.getenv("AI_GATEWAY_API_KEY"), header_key):
            body, changed = _redact(body, key)
            redacted |= changed
        captured = {"request_id": request_id, "received_at": jev._now(),
                    "status": response.status_code, "body": body,
                    "retry_after": self.last_retry_after}
        if parse_error:
            captured["parse_error"] = parse_error
        if redacted:
            captured["redacted"] = True
        store = read_responses(self.data)
        store["attempts"].append(captured)
        write_responses(self.data, store)
        return response

    def close(self):
        """The underlying session belongs to the caller."""
