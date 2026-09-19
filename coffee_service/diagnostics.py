"""기존 serving artifact를 변경하지 않고 예측 분포와 방향을 진단한다."""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd

from .features import assemble_features, load_sources
from .inference import historical_predictions
from .modeling import DEFAULT_ARTIFACT, load_bundle


def diagnose_returns(actual, predicted, *, threshold=0.001) -> dict:
    """로그수익률 10bp 미만은 진단용 보합, exact sign 지표도 별도 보존한다."""
    actual, predicted = np.asarray(actual, dtype=float), np.asarray(predicted, dtype=float)
    if actual.ndim != 1 or actual.shape != predicted.shape or not len(actual):
        raise ValueError("실제·예측은 같은 길이의 비어 있지 않은 1차원 배열이어야 합니다.")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all() or threshold <= 0:
        raise ValueError("수익률은 유한하며 threshold는 양수여야 합니다.")
    result = {
        "n": len(actual), "flat_threshold": threshold,
        "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))),
        "mae": float(np.mean(np.abs(actual - predicted))),
        "direction_accuracy": float(np.mean(np.sign(actual) == np.sign(predicted))),
        "threshold_direction_accuracy": float(np.mean(
            np.where(np.abs(actual) < threshold, 0, np.sign(actual))
            == np.where(np.abs(predicted) < threshold, 0, np.sign(predicted))
        )),
    }
    for name, values in (("actual", actual), ("predicted", predicted)):
        result.update({f"{name}_{key}": float(value) for key, value in {
            "mean": values.mean(), "std": values.std(),
            "up_ratio": np.mean(values > 0), "down_ratio": np.mean(values < 0),
            "flat_ratio": np.mean(values == 0), "near_zero_ratio": np.mean(np.abs(values) < threshold),
            "below_1bp_ratio": np.mean(np.abs(values) < 0.0001),
            **dict(zip(("min", "p05", "p25", "median", "p75", "p95", "max"),
                       np.quantile(values, [0, .05, .25, .5, .75, .95, 1]))),
        }.items()})
    return result


def diagnose(source_dir: Path, artifact=DEFAULT_ARTIFACT, start="2024-01-01"):
    dataset = assemble_features(load_sources(source_dir))
    predictions = historical_predictions(dataset, load_bundle(artifact), start=start)
    rows = []
    for horizon, group in predictions.groupby("horizon"):
        origin = dataset.prices.loc[pd.to_datetime(group.origin_date), "close"].to_numpy()
        actual = np.log(group.actual_price.to_numpy() / origin)
        rows.append({"horizon": int(horizon), "model_id": group.model_id.iloc[0],
                     "first_origin": group.origin_date.min(), "last_origin": group.origin_date.max(),
                     **diagnose_returns(actual, group.predicted_return)})
    return pd.DataFrame(rows), predictions


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    metrics, predictions = diagnose(args.source_dir, args.artifact)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    # 기존 실험 결과를 실수로 교체하지 않는다.
    for name in ("baseline_diagnostics", "baseline_predictions"):
        if (args.output_dir / f"{name}.parquet").exists():
            raise FileExistsError("새 output-dir을 지정하세요.")
    metrics.to_parquet(args.output_dir / "baseline_diagnostics.parquet", index=False)
    predictions.to_parquet(args.output_dir / "baseline_predictions.parquet", index=False)
    print(metrics.to_string(index=False))


if __name__ == "__main__":
    main()
