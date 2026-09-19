"""저장 artifact와 가격+거시 서빙 입력 계약."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import torch

from coffee_service.features import PRODUCTION_FEATURES, assemble_features, load_sources
from coffee_service.modeling import DLinear, ModelBundle, load_bundle, save_bundle
from coffee_service.ingestion import merge_table


def test_incoming_revision_wins_before_date_sorting():
    dates = pd.date_range("2025-01-01", periods=100)
    old = pd.DataFrame({"date": dates, "value": 1.0})
    revised = pd.DataFrame({"date": dates[::-1], "value": 2.0})
    result = merge_table(old, revised)
    assert result.date.is_monotonic_increasing and result.date.is_unique
    assert result.value.eq(2.0).all()


def test_artifact_rejects_invalid_normalization(tmp_path):
    path = tmp_path / "model.pt"
    size = len(PRODUCTION_FEATURES)
    save_bundle(ModelBundle(DLinear(size), list(PRODUCTION_FEATURES), np.zeros(size),
                            np.ones(size), 0.0, 1.0, {}), path)
    original = torch.load(path, weights_only=True)
    for field, value in [("scaler_scale", [1.0]), ("scaler_mean", [float("nan")] * size),
                         ("target_std", 0.0), ("target_mean", float("inf"))]:
        torch.save({**original, field: value}, path)
        with pytest.raises(ValueError, match="통계"):
            load_bundle(path)


def test_production_features_need_only_price_and_initial_macro_releases(tmp_path):
    dates = pd.date_range("2024-01-01", periods=100, freq="B")
    pd.DataFrame({"date": dates, "close": np.arange(100) + 100.0}).to_parquet(
        tmp_path / "coffee.parquet")
    for name in ("alfred_dexbzus", "alfred_dff", "alfred_dcoilwtico"):
        pd.DataFrame({"date": dates, "release_date": dates,
                      "value": np.arange(100) + 1.0}).to_parquet(tmp_path / f"{name}.parquet")
    dataset = assemble_features(load_sources(Path(tmp_path)))
    assert dataset.features.columns.tolist() == PRODUCTION_FEATURES
    assert dataset.features.iloc[-1].notna().all()
