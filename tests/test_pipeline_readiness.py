"""Pipeline must not publish partial-horizon results as a successful run."""

from contextlib import contextmanager
from datetime import date

import pandas as pd
import pytest

from coffee_service import db, pipeline


class Dataset:
    prices = pd.DataFrame(
        {"close": [200.0]}, index=pd.to_datetime(["2025-12-31"])
    )


class MissingCloseDataset:
    prices = pd.DataFrame(index=pd.to_datetime(["2025-12-31"]))


class Connection:
    def rollback(self):
        pass


def predictions(horizons):
    return pd.DataFrame(
        {
            "origin_date": [date(2025, 12, 31)] * len(horizons),
            "horizon": horizons,
        }
    )


def test_full_latest_horizons_are_accepted():
    pipeline.validate_latest_prediction_coverage(Dataset(), predictions([5, 20, 60]))


@pytest.mark.parametrize("dataset_type", [Dataset, MissingCloseDataset])
def test_partial_latest_horizons_or_missing_close_fail_before_result_upserts(monkeypatch, tmp_path, dataset_type):
    completed, upserts = [], []

    @contextmanager
    def connect(_url):
        yield Connection()

    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(db, "create_schema", lambda *_args: None)
    monkeypatch.setattr(db, "start_pipeline_run", lambda *_args: "run-id")
    monkeypatch.setattr(
        db, "finish_pipeline_run",
        lambda _connection, _run_id, status, message, *_args: completed.append((status, message)),
    )
    monkeypatch.setattr(pipeline, "sources_as_of", lambda *_args: {})
    monkeypatch.setattr(pipeline, "validate_macro_freshness", lambda *_args: None)
    monkeypatch.setattr(pipeline, "assemble_features", lambda *_args, **_kwargs: dataset_type())
    monkeypatch.setattr(pipeline, "load_bundle", lambda *_args: object())
    monkeypatch.setattr(pipeline, "generate_predictions", lambda *_args: predictions([5, 20]))
    for name in ("upsert_models", "upsert_prices", "upsert_predictions"):
        monkeypatch.setattr(db, name, lambda *_args, name=name: upserts.append(name))

    with pytest.raises(RuntimeError, match="파이프라인 처리 실패: ValueError"):
        pipeline.run_pipeline(
            "backfill", tmp_path, tmp_path / "model.pt", date(2025, 1, 1), date(2025, 12, 31),
            skip_ingestion=True,
        )

    assert upserts == []
    assert completed == [("failed", "파이프라인 처리 실패: ValueError")]
