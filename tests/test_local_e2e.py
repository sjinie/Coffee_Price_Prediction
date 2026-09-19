"""Local E2E 핵심 계약 검사. 외부 API는 호출하지 않는다."""

from contextlib import contextmanager
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from coffee_service import db, pipeline
from coffee_service.features import LOOKBACK, PRODUCTION_FEATURES
from coffee_service.ingestion import incremental_start, persist_increment
from coffee_service.modeling import DLinear, MODEL_ID, ModelBundle, load_bundle, save_bundle
from coffee_service.transform import align_available


def test_merge_and_incremental_range_are_idempotent(tmp_path):
    path = tmp_path / "coffee.parquet"
    existing = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-01", "2026-01-02"]), "close": [100.0, 101.0]}
    )
    incoming = pd.DataFrame(
        {"date": pd.to_datetime(["2026-01-02", "2026-01-03"]), "close": [102.0, 103.0]}
    )
    existing.to_parquet(path, index=False)
    merged = persist_increment(path, incoming)
    repeated = persist_increment(path, incoming)

    assert merged["date"].tolist() == list(pd.date_range("2026-01-01", periods=3))
    assert merged["close"].tolist() == [100.0, 102.0, 103.0]
    pd.testing.assert_frame_equal(merged, repeated)
    assert incremental_start(path, date(2025, 1, 1)) == date(2026, 1, 4)


def test_align_available_never_uses_future_release():
    sessions = pd.date_range("2026-01-01", periods=4)
    source = pd.DataFrame(
        {
            "date": pd.to_datetime(["2025-12-30", "2026-01-01"]),
            "available_at": pd.to_datetime(["2026-01-02", "2026-01-04"]),
            "value": [10.0, 99.0],
        }
    )
    joined = align_available(source, ["value"], sessions)

    assert pd.isna(joined.loc["2026-01-01", "value"])
    assert joined.loc["2026-01-03", "value"] == 10.0
    assert joined.loc["2026-01-04", "value"] == 99.0
    available = joined["available_at"].notna()
    assert (joined.loc[available, "available_at"] <= joined.index[available]).all()


def test_collect_merges_incremental_saved_responses_idempotently(tmp_path, monkeypatch):
    first = pd.DataFrame({"date": pd.to_datetime(["2026-01-01", "2026-01-02"]), "close": [100.0, 101.0]})
    second = pd.DataFrame({"date": pd.to_datetime(["2026-01-02", "2026-01-03"]), "close": [102.0, 103.0]})
    responses = iter([first, second, second])

    @contextmanager
    def session():
        yield object()

    def fetch(*_args):
        return next(responses)

    monkeypatch.setattr(pipeline, "build_session", session)
    monkeypatch.setattr(pipeline, "collection_jobs", lambda *_args: [("coffee", "yahoo", fetch, ())])
    source_dir = tmp_path / "sources"
    for expected_rows, expected_date in ((2, date(2026, 1, 2)), (3, date(2026, 1, 3)), (3, date(2026, 1, 3))):
        statuses, failures = pipeline.collect(source_dir, "incremental", date(2026, 1, 1), date(2026, 1, 3), ())
        assert not failures
        assert statuses[0]["source"] == "coffee"
        assert statuses[0]["status"] == "success"
        assert statuses[0]["last_data_date"] == expected_date
        assert statuses[0]["row_count"] == expected_rows

    merged = pd.read_parquet(source_dir / "coffee.parquet")
    assert merged.date.tolist() == list(pd.date_range("2026-01-01", periods=3))
    assert merged.close.tolist() == [100.0, 102.0, 103.0]


def test_collect_records_required_and_optional_failures(tmp_path, monkeypatch):
    @contextmanager
    def session():
        yield object()

    def fail(*_args):
        raise RuntimeError("fixture failure")

    monkeypatch.setattr(pipeline, "build_session", session)
    monkeypatch.setattr(
        pipeline,
        "collection_jobs",
        lambda *_args: [
            ("coffee", "yahoo", fail, ()),
            ("weather_br_sul_minas", "nasa", fail, ()),
        ],
    )

    statuses, failures = pipeline.collect(tmp_path, "backfill", date(2026, 1, 1), date(2026, 1, 2), ())

    assert failures == ["coffee", "weather_br_sul_minas"]
    assert [status["status"] for status in statuses] == ["failed", "failed"]
    assert pipeline.required_failures(failures) == ["coffee"]
    assert pipeline.required_failures(["weather_br_sul_minas"]) == []


@pytest.mark.parametrize("news_failure", [False, True])
def test_pipeline_records_optional_failure_without_blocking_serving(tmp_path, monkeypatch, news_failure):
    connection = FakeConnection()
    statuses = [{
        "source": "weather_br_sul_minas", "status": "failed", "last_data_date": None,
        "row_count": 0, "error": "fixture failure",
    }]
    completed = []

    @contextmanager
    def connect(_url):
        yield connection

    class Dataset:
        prices = pd.DataFrame()

    monkeypatch.setattr(db, "connect", connect)
    monkeypatch.setattr(db, "create_schema", lambda *_args: None)
    monkeypatch.setattr(db, "start_pipeline_run", lambda *_args: "run-id")
    monkeypatch.setattr(db, "upsert_source_status", lambda _connection, records: completed.append(records))
    monkeypatch.setattr(db, "upsert_models", lambda *_args: None)
    monkeypatch.setattr(db, "upsert_prices", lambda *_args: 3)
    monkeypatch.setattr(db, "upsert_predictions", lambda *_args: 2)
    monkeypatch.setattr(db, "fill_prediction_actuals", lambda *_args: None)
    monkeypatch.setattr(
        db,
        "finish_pipeline_run",
        lambda _connection, _run_id, status, message, *_args: completed.append((status, message)),
    )
    monkeypatch.setattr(pipeline, "collect", lambda *_args, **_kwargs: (statuses, ["weather_br_sul_minas"]))
    monkeypatch.setattr(pipeline, "sources_as_of", lambda *_args: {})
    monkeypatch.setattr(pipeline, "validate_macro_freshness", lambda *_args: None)
    monkeypatch.setattr(pipeline, "assemble_features", lambda *_args, **_kwargs: Dataset())
    monkeypatch.setattr(pipeline, "load_bundle", lambda *_args: object())
    monkeypatch.setattr(pipeline, "generate_predictions", lambda *_args: pd.DataFrame())

    result = pipeline.run_pipeline(
        "backfill", tmp_path, tmp_path / "model.pt", date(2026, 1, 1), date(2026, 1, 2),
        news_path=tmp_path / "missing-news.parquet" if news_failure else None,
    )

    assert result["status"] == "success"
    assert completed[0] == statuses
    failed_sources = "news, weather_br_sul_minas" if news_failure else "weather_br_sul_minas"
    assert completed[1] == ("success", f"가격 3행, 예측 2행 UPSERT | 보조 수집 실패: {failed_sources}")
    if news_failure:
        assert completed[0][-1]["source"] == "news"
        assert completed[0][-1]["status"] == "failed"


def test_sources_as_of_excludes_future_observations_and_unavailable_releases(tmp_path):
    source_dir = tmp_path / "sources"
    source_dir.mkdir()
    required = {
        "coffee", "alfred_dexbzus", "alfred_dff", "alfred_dcoilwtico",
        "weather_br_sul_minas", "weather_br_cerrado", "weather_br_alta_mogiana",
        "weather_co_huila", "weather_co_caldas", "weather_co_antioquia",
    }
    for name in required:
        frame = pd.DataFrame({"date": pd.to_datetime(["2023-12-31", "2024-01-02"]), "value": [1.0, 2.0]})
        if name.startswith("alfred_"):
            frame["release_date"] = pd.to_datetime(["2023-12-30", "2024-01-02"])
        frame.to_parquet(source_dir / f"{name}.parquet", index=False)

    sources = pipeline.sources_as_of(source_dir, date(2024, 1, 1))
    assert all(len(frame) == 1 for frame in sources.values())
    assert all(frame.date.max() == pd.Timestamp("2023-12-31") for frame in sources.values())


def test_validate_macro_freshness_rejects_stale_but_allows_holiday_lag():
    def macro(latest, release_date=None, value=1.0):
        return pd.DataFrame({
            "date": pd.to_datetime([latest]),
            "release_date": pd.to_datetime([release_date or latest]),
            "value": [value],
        })

    fresh = {
        "coffee": pd.DataFrame({"date": pd.to_datetime(["2025-12-29"])}),
        "alfred_dexbzus": macro("2025-12-15"),
        "alfred_dff": macro("2025-12-15"),
        "alfred_dcoilwtico": macro("2025-12-15"),
    }
    pipeline.validate_macro_freshness(fresh, date(2025, 12, 29))

    stale = {**fresh, "alfred_dff": macro("2025-12-14")}
    with pytest.raises(ValueError, match="alfred_dff"):
        pipeline.validate_macro_freshness(stale, date(2025, 12, 29))

    unavailable = {**fresh, "alfred_dff": pd.concat([macro("2025-12-14"), macro("2025-12-29", "2026-01-01")])}
    with pytest.raises(ValueError, match="alfred_dff"):
        pipeline.validate_macro_freshness(unavailable, date(2025, 12, 29))


def test_model_save_load_preserves_feature_contract_and_prediction(tmp_path):
    torch.manual_seed(42)
    model = DLinear(len(PRODUCTION_FEATURES))
    bundle = ModelBundle(
        model=model,
        feature_columns=list(PRODUCTION_FEATURES),
        scaler_mean=np.zeros(len(PRODUCTION_FEATURES)),
        scaler_scale=np.ones(len(PRODUCTION_FEATURES)),
        target_mean=0.01,
        target_std=0.2,
        metadata={"fixture": True},
    )
    values = np.arange(LOOKBACK * len(PRODUCTION_FEATURES), dtype=float).reshape(
        1, LOOKBACK, len(PRODUCTION_FEATURES)
    ) / 100
    expected = bundle.predict(values)
    path = tmp_path / "model.pt"
    save_bundle(bundle, path)
    restored = load_bundle(path)

    assert restored.feature_columns == PRODUCTION_FEATURES
    np.testing.assert_allclose(restored.predict(values), expected)


class FakeConnection:
    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.executed = []
        self.many = []
        self.commits = 0

    def execute(self, query, parameters=(), **_kwargs):
        self.executed.append((" ".join(query.split()), parameters))
        return self

    def fetchall(self):
        return self.responses.pop(0) if self.responses else []

    def fetchone(self):
        return self.responses.pop(0) if self.responses else None

    @contextmanager
    def cursor(self):
        yield FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class FakeCursor:
    def __init__(self, connection):
        self.connection = connection

    def executemany(self, query, rows):
        self.connection.many.append((" ".join(query.split()), list(rows)))
        return self



def test_prediction_upsert_uses_stable_natural_key():
    connection = FakeConnection()
    frame = pd.DataFrame(
        [{
            "model_id": MODEL_ID, "origin_date": date(2026, 1, 2),
            "target_date": date(2026, 3, 30), "horizon": 60,
            "predicted_return": 0.05, "predicted_price": 350.0, "actual_price": None,
        }]
    )
    assert db.upsert_predictions(connection, frame) == 1
    query, rows = connection.many[0]
    assert "ON CONFLICT (model_id, origin_date, horizon) DO UPDATE" in query
    assert "actual_price = COALESCE(EXCLUDED.actual_price, predictions.actual_price)" in query
    assert "WHERE predictions.target_date = EXCLUDED.target_date" in query
    assert "predicted_price = EXCLUDED.predicted_price" not in query
    assert rows[0][:4] == (MODEL_ID, date(2026, 1, 2), date(2026, 3, 30), 60)


def test_fill_prediction_actuals_uses_stored_target_date():
    connection = FakeConnection()
    db.fill_prediction_actuals(connection)
    query, _parameters = connection.executed[0]
    assert "predictions.target_date = prices.date" in query
    assert "predictions.actual_price IS NULL" in query


def test_fastapi_serves_database_rows_only():
    pytest.importorskip("fastapi")
    pytest.importorskip("starlette")
    from fastapi.testclient import TestClient
    from coffee_service.api import create_app

    connection = FakeConnection(responses=[[{"date": date(2026, 1, 2), "close": 350.0}]])

    @contextmanager
    def factory():
        yield connection

    client = TestClient(create_app(factory))
    response = client.get("/api/v1/prices?limit=1")

    assert response.status_code == 200
    assert response.json() == [{"date": "2026-01-02", "close": 350.0}]
    assert "FROM prices" in connection.executed[0][0]

    for path in (
        "/api/v1/prices?limit=0", "/api/v1/prices?limit=5001",
        "/api/v1/predictions?horizon=0", "/api/v1/predictions?horizon=abc",
        "/api/v1/predictions?limit=-1",
    ):
        assert client.get(path).status_code == 422
    assert len(connection.executed) == 1


def test_production_artifact_reproduces_notebook_metrics():
    root = Path(__file__).resolve().parents[1]
    artifact = root / "model_artifacts" / "production_dlinear_60.pt"
    source_dir = root / "data" / "processed" / "2014-07-01_2025-12-31"
    if not artifact.exists() or not source_dir.exists():
        pytest.skip("로컬 production artifact와 분석 Parquet가 필요합니다.")

    from coffee_service.features import assemble_features, load_sources
    from coffee_service.inference import historical_predictions

    dataset = assemble_features(load_sources(source_dir), fit_end="2023-12-31")
    result = historical_predictions(dataset, load_bundle(artifact))
    result = result[result.horizon.eq(60)]
    origin = dataset.prices.loc[pd.to_datetime(result.origin_date), "close"].to_numpy()
    actual_return = np.log(result.actual_price.to_numpy() / origin)
    error = actual_return - result.predicted_return.to_numpy()

    assert len(result) == 443
    assert np.sqrt(np.mean(error**2)) == pytest.approx(0.16794, abs=5e-6)
    assert np.mean(np.abs(error)) == pytest.approx(0.14041, abs=5e-6)
