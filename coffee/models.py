"""예측 모델과 artifact 저장.

모든 모델은 같은 방식으로 쓴다.

    model.fit(data, rows, y)     # data: build_dataset() 결과, rows: 학습 행 번호, y: 타깃
    model.predict(data, rows)    # → 로그수익률 예측

창 입력 모델(DLinear)은 각 행 직전 LOOKBACK 거래일을 함께 본다. LSTM은 torch가 필요해
서비스 패키지에 넣지 않고 노트북(03)에서만 정의한다.
"""
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .evaluate import LOOKBACK


class Naive:
    """가격이 그대로라고 예측한다(수익률 0). 모든 모델이 넘어야 하는 기준선."""
    name = "Naive"

    def __init__(self, features=()):
        self.features = list(features)

    def fit(self, data, rows, y):
        return self

    def predict(self, data, rows):
        return np.zeros(len(rows))


class Momentum:
    """지난 h일 수익률이 그대로 이어진다고 예측한다. 방향 지표의 기준선."""
    name = "Momentum"

    def __init__(self, horizon: int):
        self.horizon = horizon
        self.features = [f"ret_{horizon}"]

    def fit(self, data, rows, y):
        return self

    def predict(self, data, rows):
        return data[f"ret_{self.horizon}"].to_numpy(float)[rows]


class RidgeModel:
    """현재 행의 피처로 하는 선형 회귀. 피처는 학습 구간에서 표준화한다."""
    name = "Ridge"

    def __init__(self, features, alpha: float = 10.0):
        self.features, self.alpha = list(features), alpha

    def _x(self, data, rows):
        return data[self.features].to_numpy(float)[rows]

    def fit(self, data, rows, y):
        self.pipeline_ = make_pipeline(StandardScaler(), Ridge(alpha=self.alpha)).fit(self._x(data, rows), y)
        return self

    def predict(self, data, rows):
        return self.pipeline_.predict(self._x(data, rows))


class LightGBMModel:
    """그래디언트 부스팅 트리. 표본이 작아 얕은 트리·작은 학습률을 쓴다. early stopping은 쓰지 않는다."""
    name = "LightGBM"

    def __init__(self, features, n_estimators: int = 300, learning_rate: float = 0.03,
                 num_leaves: int = 15, min_child_samples: int = 50, seed: int = 42):
        self.features = list(features)
        self.params = dict(n_estimators=n_estimators, learning_rate=learning_rate, num_leaves=num_leaves,
                           min_child_samples=min_child_samples, subsample=0.8, subsample_freq=1,
                           colsample_bytree=0.8, random_state=seed, n_jobs=1, verbose=-1)

    def _x(self, data, rows):
        return data[self.features].to_numpy(float)[rows]

    def fit(self, data, rows, y):
        from lightgbm import LGBMRegressor

        self.model_ = LGBMRegressor(**self.params).fit(self._x(data, rows), y)
        return self

    def predict(self, data, rows):
        return self.model_.predict(self._x(data, rows))


def _moving_average(windows: np.ndarray, kernel: int) -> np.ndarray:
    """(n, 시간, 피처) 창의 시간 축 이동평균. 양 끝은 첫·마지막 값을 반복해 길이를 유지한다."""
    left = kernel // 2
    padded = np.concatenate([np.repeat(windows[:, :1], left, axis=1), windows,
                             np.repeat(windows[:, -1:], kernel - 1 - left, axis=1)], axis=1)
    cumsum = np.concatenate([np.zeros_like(padded[:, :1]), np.cumsum(padded, axis=1)], axis=1)
    return (cumsum[:, kernel:] - cumsum[:, :-kernel]) / kernel


class DLinearModel:
    """DLinear(Zeng et al., 2023) 방식.

    과거 60거래일의 피처 창을 이동평균 추세와 나머지로 나누고, 두 부분을 이어 붙여 Ridge로
    회귀한다. 분해는 선형 변환이므로 결국 '입력 창 전체에 대한 선형 모델'이다. 원 논문의
    요점도 단순한 선형 모델이 복잡한 Transformer와 견줄 만하다는 것이었다.
    """
    name = "DLinear"

    def __init__(self, features, alpha: float = 100.0, kernel: int = 25):
        self.features, self.alpha, self.kernel = list(features), alpha, kernel

    def _x(self, data, rows):
        values = data[self.features].to_numpy(float)
        windows = np.stack([values[row - LOOKBACK + 1: row + 1] for row in rows])
        trend = _moving_average(windows, self.kernel)
        return np.concatenate([trend.reshape(len(rows), -1), (windows - trend).reshape(len(rows), -1)], axis=1)

    def fit(self, data, rows, y):
        self.pipeline_ = make_pipeline(StandardScaler(), Ridge(alpha=self.alpha)).fit(self._x(data, rows), y)
        return self

    def predict(self, data, rows):
        return self.pipeline_.predict(self._x(data, rows))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def save_bundle(directory: Path, models: dict[int, object], metadata: dict) -> None:
    """지평별 모델(h5.joblib …)과 metadata.json을 저장한다. 파일 해시도 함께 적는다."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for horizon, model in models.items():
        path = directory / f"h{horizon}.joblib"
        joblib.dump(model, path)
        files[path.name] = _sha256(path)
    document = {**metadata, "files": files}
    (directory / "metadata.json").write_text(json.dumps(document, indent=2, ensure_ascii=False, default=str) + "\n",
                                             encoding="utf-8")


def load_bundle(directory: Path) -> tuple[dict[int, object], dict]:
    """저장한 모델을 읽는다. 해시가 다르면(파일이 바뀌었으면) 읽지 않는다."""
    directory = Path(directory)
    metadata = json.loads((directory / "metadata.json").read_text(encoding="utf-8"))
    models = {}
    for name, digest in metadata["files"].items():
        path = directory / name
        if _sha256(path) != digest:
            raise ValueError(f"모델 파일 해시가 metadata와 다릅니다: {name}")
        models[int(name.removeprefix("h").removesuffix(".joblib"))] = joblib.load(path)
    return models, metadata
