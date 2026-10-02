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

    cause = "최신 가격일 예측이 없습니다: horizon 60" if dataset_type is Dataset else "가격 데이터에 close 열이 없습니다."
    message = f"파이프라인 처리 실패: PipelineInputError ({cause})"
    with pytest.raises(pipeline.PipelineRunError) as raised:
        pipeline.run_pipeline(
            "backfill", tmp_path, tmp_path / "model.pt", date(2025, 1, 1), date(2025, 12, 31),
            skip_ingestion=True,
        )

    assert str(raised.value) == message
    assert upserts == []
    assert completed == [("failed", message)]


def test_selected_weather_names_only_stale_model_regions():
    from coffee_service.features import FeatureDataset

    sessions = pd.bdate_range("2026-01-01", periods=70)
    features = pd.DataFrame({"br_cerrado_rain_anomaly": 1.0, "co_huila_rain_anomaly": 1.0,
                             "co_caldas_frost": 1.0, "month_sin": 0.5}, index=sessions)
    prices = pd.DataFrame({"close": 200.0}, index=sessions)
    dataset = FeatureDataset(features, prices, sessions, list(features), {})
    model_columns = ["br_cerrado_rain_anomaly", "co_huila_rain_anomaly", "month_sin"]

    features.iloc[-3:, features.columns.get_loc("co_caldas_frost")] = float("nan")
    pipeline.validate_selected_weather(dataset, model_columns)  # Unused column is ignored.
    features.iloc[-3:, features.columns.get_loc("br_cerrado_rain_anomaly")] = float("nan")
    with pytest.raises(pipeline.PipelineInputError, match="비어 있습니다: br_cerrado$"):
        pipeline.validate_selected_weather(dataset, model_columns)
