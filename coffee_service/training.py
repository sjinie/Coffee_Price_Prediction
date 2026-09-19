"""Notebook에서 정한 production candidate를 artifact로 학습한다."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import torch
from sklearn.preprocessing import StandardScaler
from torch import nn
from torch.utils.data import DataLoader, TensorDataset

from .features import (
    LOOKBACK,
    PRODUCTION_FEATURES,
    assemble_features,
    eligible_rows,
    load_sources,
    make_targets,
)
from .modeling import DEFAULT_ARTIFACT, EPOCHS, HORIZON, MODEL_ID, NOTEBOOK_METRICS, SEED, DLinear, ModelBundle, save_bundle


ROOT = Path(__file__).resolve().parents[1]


def make_windows(values: np.ndarray, indices: np.ndarray) -> np.ndarray:
    indices = np.asarray(indices)
    if not len(indices) or indices.min() < LOOKBACK - 1:
        raise ValueError("60거래일 입력 창을 만들 수 없습니다.")
    windows = np.stack([values[index - LOOKBACK + 1 : index + 1] for index in indices])
    if not np.isfinite(windows).all():
        raise ValueError("학습 입력 창에 결측 또는 무한대가 있습니다.")
    return windows


def train_bundle(
    source_dir: Path,
    *,
    train_start="2015-01-01",
    train_end="2023-12-31",
    epochs: int = EPOCHS,
) -> ModelBundle:
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    torch.manual_seed(SEED)
    dataset = assemble_features(load_sources(source_dir), fit_end=train_end)
    targets = make_targets(dataset.prices["close"])
    indices = eligible_rows(dataset.features, targets, HORIZON, train_start, train_end)
    scaler = StandardScaler().fit(dataset.features.iloc[indices][PRODUCTION_FEATURES])
    values = scaler.transform(dataset.features[PRODUCTION_FEATURES]).astype("float32")
    windows = make_windows(values, indices)
    target = targets.iloc[indices][f"y_{HORIZON}"].to_numpy()
    target_mean, target_std = float(target.mean()), float(target.std())
    if target_std <= 0 or not np.isfinite(target).all():
        raise ValueError("학습 target을 표준화할 수 없습니다.")
    normalized = ((target - target_mean) / target_std).astype("float32")
    generator = torch.Generator().manual_seed(SEED)
    loader = DataLoader(
        TensorDataset(torch.from_numpy(windows), torch.from_numpy(normalized)),
        batch_size=64,
        shuffle=True,
        generator=generator,
    )
    model = DLinear(len(PRODUCTION_FEATURES))
    optimizer = torch.optim.AdamW(model.parameters(), lr=0.001, weight_decay=0.01)
    model.train()
    for _ in range(epochs):
        for batch_x, batch_y in loader:
            optimizer.zero_grad()
            loss = nn.functional.mse_loss(model(batch_x), batch_y)
            if not torch.isfinite(loss):
                raise ValueError("모델 손실이 유한하지 않습니다.")
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            optimizer.step()
    metadata = {
        "model_id": MODEL_ID,
        "model_name": "DLinear",
        "horizon": HORIZON,
        "epochs": epochs,
        "seed": SEED,
        "training_start": str(dataset.sessions[indices[0]].date()),
        "training_end": str(dataset.sessions[indices[-1]].date()),
        "training_cutoff": str(train_end),
        "training_rows": len(indices),
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "notebook_metrics": NOTEBOOK_METRICS,
    }
    return ModelBundle(
        model=model,
        feature_columns=list(PRODUCTION_FEATURES),
        scaler_mean=scaler.mean_,
        scaler_scale=scaler.scale_,
        target_mean=target_mean,
        target_std=target_std,
        metadata=metadata,
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    args = parser.parse_args()
    bundle = train_bundle(args.source_dir)
    save_bundle(bundle, args.artifact)
    print(f"모델 저장: {args.artifact} | 학습 {bundle.metadata['training_rows']:,}행")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
