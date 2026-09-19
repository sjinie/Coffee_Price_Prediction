"""Feature Dataset 조립과 시간 경계 검사."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .transform import coffee_sessions, transform_macro, transform_market


PRICE_FEATURES = ["return_1", "return_5", "return_20", "volatility_20"]
MACRO_FEATURES = ["brl_change_20", "rate_change_20", "oil_change_20"]
PRODUCTION_FEATURES = PRICE_FEATURES + MACRO_FEATURES
LOOKBACK = 60


@dataclass
class FeatureDataset:
    features: pd.DataFrame
    prices: pd.DataFrame
    sessions: pd.DatetimeIndex
    weather_columns: list[str]
    availability: dict[str, pd.DataFrame]


def load_sources(source_dir: Path) -> dict[str, pd.DataFrame]:
    required = {"coffee", "alfred_dexbzus", "alfred_dff", "alfred_dcoilwtico"}
    missing = sorted(name for name in required if not (Path(source_dir) / f"{name}.parquet").exists())
    if missing:
        raise FileNotFoundError("필수 source Parquet가 없습니다: " + ", ".join(missing))
    sources = {}
    for name in sorted(required):
        path = Path(source_dir) / f"{name}.parquet"
        frame = pd.read_parquet(path).sort_values("date").reset_index(drop=True)
        frame["date"] = pd.to_datetime(frame["date"]).astype("datetime64[ns]")
        if frame["date"].isna().any() or frame["date"].duplicated().any():
            raise ValueError(f"{path.name}: 날짜가 비었거나 중복됩니다.")
        sources[path.stem] = frame
    return sources


def assemble_features(sources: dict[str, pd.DataFrame], fit_end="2023-12-31") -> FeatureDataset:
    start = sources["coffee"]["date"].min()
    end = sources["coffee"]["date"].max()
    sessions = coffee_sessions(start, end)
    prices, market = transform_market(sources, sessions)
    macro, macro_availability = transform_macro(sources, sessions)
    # 현재 artifact는 가격+거시 7개만 사용하므로 추론 때 기상 통계를 재학습하지 않는다.
    features = market.join(macro)
    if features.index.has_duplicates or not features.index.is_monotonic_increasing:
        raise ValueError("Feature Dataset 날짜는 정렬된 고유 값이어야 합니다.")
    if list(features.columns[: len(PRODUCTION_FEATURES)]) != PRODUCTION_FEATURES:
        raise ValueError("production feature 열 순서가 Notebook 계약과 다릅니다.")
    return FeatureDataset(
        features=features,
        prices=prices,
        sessions=sessions,
        weather_columns=[],
        availability=macro_availability,
    )


def make_targets(close: pd.Series, horizons=(5, 20, 60)) -> pd.DataFrame:
    targets = pd.DataFrame(index=close.index)
    dates = pd.Series(close.index, index=close.index)
    for horizon in horizons:
        targets[f"y_{horizon}"] = np.log(close.shift(-horizon) / close)
        targets[f"target_date_{horizon}"] = dates.shift(-horizon)
    return targets


def eligible_rows(
    features: pd.DataFrame,
    targets: pd.DataFrame,
    horizon: int,
    start,
    end,
) -> np.ndarray:
    valid = (
        features[PRODUCTION_FEATURES]
        .notna()
        .all(axis=1)
        .rolling(LOOKBACK, min_periods=LOOKBACK)
        .sum()
        .eq(LOOKBACK)
    )
    target_date = targets[f"target_date_{horizon}"]
    valid &= targets[f"y_{horizon}"].notna() & target_date.le(pd.Timestamp(end))
    valid &= (features.index >= pd.Timestamp(start)) & (features.index <= pd.Timestamp(end))
    return np.flatnonzero(valid)
