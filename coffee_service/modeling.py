"""Production DLinear 정의와 안전한 artifact 저장·로드."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch
from torch import nn

from .features import LOOKBACK, PRODUCTION_FEATURES


MODEL_ID = "dlinear-price-macro-h60-v1"
MODEL_NAME = "DLinear"
HORIZON = 60
SEED = 42
EPOCHS = 50
DEFAULT_ARTIFACT = Path(__file__).resolve().parents[1] / "model_artifacts" / "production_dlinear_60.pt"
NOTEBOOK_METRICS = {
    "validation_rmse": 0.14719,
    "test_rmse": 0.16794,
    "test_mae": 0.14041,
    "test_direction_accuracy": 60.05,
    "test_naive_rmse_improvement_pct": 9.59,
}


class DLinear(nn.Module):
    def __init__(self, n_features: int):
        super().__init__()
        self.trend = nn.Linear(LOOKBACK, 1)
        self.residual = nn.Linear(LOOKBACK, 1)
        self.head = nn.Linear(n_features, 1)

    def forward(self, values):
        channels = values.transpose(1, 2)
        smooth = nn.functional.avg_pool1d(
            nn.functional.pad(channels, (2, 2), mode="replicate"),
            5,
            stride=1,
        )
        combined = (self.trend(smooth) + self.residual(channels - smooth)).squeeze(-1)
        return self.head(combined).squeeze(-1)


@dataclass
class ModelBundle:
    model: DLinear
    feature_columns: list[str]
    scaler_mean: np.ndarray
    scaler_scale: np.ndarray
    target_mean: float
    target_std: float
    metadata: dict

    def predict(self, windows: np.ndarray) -> np.ndarray:
        if windows.ndim != 3 or windows.shape[1:] != (LOOKBACK, len(self.feature_columns)):
            raise ValueError(
                f"입력 shape은 (n, {LOOKBACK}, {len(self.feature_columns)})이어야 합니다."
            )
        scaled = (windows - self.scaler_mean) / self.scaler_scale
        if not np.isfinite(scaled).all():
            raise ValueError("모델 입력에 결측 또는 무한대가 있습니다.")
        self.model.eval()
        with torch.no_grad():
            prediction = self.model(torch.from_numpy(scaled.astype("float32"))).numpy()
        return prediction * self.target_std + self.target_mean


def save_bundle(bundle: ModelBundle, path: Path) -> None:
    payload = {
        "format_version": 1,
        "model_id": MODEL_ID,
        "feature_columns": bundle.feature_columns,
        "state_dict": bundle.model.state_dict(),
        "scaler_mean": bundle.scaler_mean.tolist(),
        "scaler_scale": bundle.scaler_scale.tolist(),
        "target_mean": bundle.target_mean,
        "target_std": bundle.target_std,
        "metadata": bundle.metadata,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        torch.save(payload, temporary)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_bundle(path: Path) -> ModelBundle:
    payload = torch.load(Path(path), map_location="cpu", weights_only=True)
    if payload.get("format_version") != 1 or payload.get("model_id") != MODEL_ID:
        raise ValueError("지원하지 않는 model artifact입니다.")
    columns = list(payload["feature_columns"])
    if columns != PRODUCTION_FEATURES:
        raise ValueError("artifact feature 열이 production 계약과 다릅니다.")
    model = DLinear(len(columns))
    model.load_state_dict(payload["state_dict"])
    model.eval()
    scale = np.asarray(payload["scaler_scale"], dtype=float)
    mean = np.asarray(payload["scaler_mean"], dtype=float)
    target_mean = float(payload["target_mean"])
    target_std = float(payload["target_std"])
    if (
        scale.shape != (len(columns),) or mean.shape != scale.shape
        or not np.isfinite(scale).all() or not np.isfinite(mean).all()
        or np.any(scale <= 0)
        or not np.isfinite([target_mean, target_std]).all() or target_std <= 0
        or any(not torch.isfinite(value).all() for value in model.state_dict().values())
    ):
        raise ValueError("artifact의 가중치·표준화 통계가 유효하지 않습니다.")
    return ModelBundle(
        model=model,
        feature_columns=columns,
        scaler_mean=mean,
        scaler_scale=scale,
        target_mean=target_mean,
        target_std=target_std,
        metadata=dict(payload["metadata"]),
    )
