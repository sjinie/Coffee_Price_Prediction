"""저장된 모델과 persistence 기준으로 가격 예측을 생성한다."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from .features import LOOKBACK, PRODUCTION_FEATURES, FeatureDataset, make_targets
from .modeling import HORIZON, MODEL_ID, ModelBundle, load_bundle
from .transform import coffee_sessions


PERSISTENCE_MODEL_ID = "persistence-h5-h20-v1"
PREDICTION_START = pd.Timestamp("2024-01-01")
PREDICTION_COLUMNS = [
    "model_id", "origin_date", "target_date", "horizon", "predicted_return",
    "predicted_price", "actual_price",
]


def _windows(features: pd.DataFrame, indices: np.ndarray) -> np.ndarray:
    values = features[PRODUCTION_FEATURES].to_numpy(dtype=float)
    return np.stack([values[index - LOOKBACK + 1 : index + 1] for index in indices])


def _future_target_date(origin_date, horizon: int) -> pd.Timestamp:
    start = pd.Timestamp(origin_date) + pd.Timedelta(days=1)
    sessions = coffee_sessions(start, start + pd.Timedelta(days=max(120, horizon * 3)))
    if len(sessions) < horizon:
        raise ValueError(f"{horizon}거래일 뒤 날짜를 계산할 수 없습니다.")
    return sessions[horizon - 1]


def _empty_predictions() -> pd.DataFrame:
    return pd.DataFrame(columns=PREDICTION_COLUMNS)


def historical_predictions(
    dataset: FeatureDataset,
    bundle: ModelBundle,
    start="2024-01-01",
) -> pd.DataFrame:
    targets = make_targets(dataset.prices["close"])
    rows = []
    for horizon in (5, 20):
        valid = (
            dataset.prices["close"].notna()
            & targets[f"y_{horizon}"].notna()
            & (dataset.sessions >= pd.Timestamp(start))
        )
        for index in np.flatnonzero(valid):
            origin_price = float(dataset.prices.iloc[index]["close"])
            target_date = targets.iloc[index][f"target_date_{horizon}"]
            rows.append(
                {
                    "model_id": PERSISTENCE_MODEL_ID,
                    "origin_date": dataset.sessions[index].date(),
                    "target_date": target_date.date(),
                    "horizon": horizon,
                    "predicted_return": 0.0,
                    "predicted_price": origin_price,
                    "actual_price": float(dataset.prices.loc[target_date, "close"]),
                }
            )
    valid = (
        dataset.features[PRODUCTION_FEATURES].notna().all(axis=1)
        .rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
        & targets[f"y_{HORIZON}"].notna()
        & (dataset.sessions >= pd.Timestamp(start))
    )
    indices = np.flatnonzero(valid)
    returns = bundle.predict(_windows(dataset.features, indices)) if len(indices) else []
    for index, predicted_return in zip(indices, returns):
        origin_price = float(dataset.prices.iloc[index]["close"])
        target_date = targets.iloc[index][f"target_date_{HORIZON}"]
        rows.append(
            {
                "model_id": MODEL_ID,
                "origin_date": dataset.sessions[index].date(),
                "target_date": target_date.date(),
                "horizon": HORIZON,
                "predicted_return": float(predicted_return),
                "predicted_price": float(origin_price * np.exp(predicted_return)),
                "actual_price": float(dataset.prices.loc[target_date, "close"]),
            }
        )
    if not rows:
        return _empty_predictions()
    return pd.DataFrame(rows).sort_values(["target_date", "horizon"]).reset_index(drop=True)


def incremental_predictions(dataset: FeatureDataset, bundle: ModelBundle) -> pd.DataFrame:
    """학습 종료 뒤 origin의 예측을 성숙 여부와 관계없이 만든다."""
    targets = make_targets(dataset.prices["close"])
    rows = []
    origin_valid = dataset.prices["close"].notna() & (dataset.sessions >= PREDICTION_START)

    for horizon in (5, 20):
        for index in np.flatnonzero(origin_valid):
            origin_date = dataset.sessions[index]
            target_date = targets.iloc[index][f"target_date_{horizon}"]
            target_date = _future_target_date(origin_date, horizon) if pd.isna(target_date) else target_date
            actual = dataset.prices.loc[target_date, "close"] if target_date in dataset.prices.index else np.nan
            rows.append(
                {
                    "model_id": PERSISTENCE_MODEL_ID,
                    "origin_date": origin_date.date(),
                    "target_date": target_date.date(),
                    "horizon": horizon,
                    "predicted_return": 0.0,
                    "predicted_price": float(dataset.prices.iloc[index]["close"]),
                    "actual_price": None if pd.isna(actual) else float(actual),
                }
            )

    model_valid = (
        dataset.features[PRODUCTION_FEATURES].notna().all(axis=1)
        .rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
        & origin_valid
    )
    indices = np.flatnonzero(model_valid)
    returns = bundle.predict(_windows(dataset.features, indices)) if len(indices) else []
    for index, predicted_return in zip(indices, returns):
        origin_date = dataset.sessions[index]
        target_date = targets.iloc[index][f"target_date_{HORIZON}"]
        target_date = _future_target_date(origin_date, HORIZON) if pd.isna(target_date) else target_date
        actual = dataset.prices.loc[target_date, "close"] if target_date in dataset.prices.index else np.nan
        origin_price = float(dataset.prices.iloc[index]["close"])
        rows.append(
            {
                "model_id": MODEL_ID,
                "origin_date": origin_date.date(),
                "target_date": target_date.date(),
                "horizon": HORIZON,
                "predicted_return": float(predicted_return),
                "predicted_price": float(origin_price * np.exp(predicted_return)),
                "actual_price": None if pd.isna(actual) else float(actual),
            }
        )
    if not rows:
        return _empty_predictions()
    return pd.DataFrame(rows).sort_values(["origin_date", "horizon"]).reset_index(drop=True)


def latest_predictions(dataset: FeatureDataset, bundle: ModelBundle) -> pd.DataFrame:
    price_rows = np.flatnonzero(dataset.prices["close"].notna())
    if not len(price_rows):
        raise ValueError("예측할 커피 가격이 없습니다.")
    index = int(price_rows[-1])
    window = dataset.features.iloc[index - LOOKBACK + 1 : index + 1][PRODUCTION_FEATURES]
    if len(window) != LOOKBACK or window.isna().any().any():
        raise ValueError("최신일의 60거래일 feature window가 완전하지 않습니다.")
    origin_date = dataset.sessions[index]
    origin_price = float(dataset.prices.iloc[index]["close"])
    dlinear_return = float(bundle.predict(window.to_numpy(dtype=float)[None, :, :])[0])
    rows = []
    for horizon in (5, 20, 60):
        predicted_return = dlinear_return if horizon == 60 else 0.0
        rows.append(
            {
                "model_id": MODEL_ID if horizon == 60 else PERSISTENCE_MODEL_ID,
                "origin_date": origin_date.date(),
                "target_date": _future_target_date(origin_date, horizon).date(),
                "horizon": horizon,
                "predicted_return": predicted_return,
                "predicted_price": float(origin_price * np.exp(predicted_return)),
                "actual_price": None,
            }
        )
    return pd.DataFrame(rows)


def generate_predictions(dataset: FeatureDataset, artifact: Path | ModelBundle) -> pd.DataFrame:
    bundle = artifact if isinstance(artifact, ModelBundle) else load_bundle(artifact)
    return incremental_predictions(dataset, bundle)
