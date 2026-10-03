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


class ScaledReturn:
    """수익률을 최근 변동성으로 나눠 학습하고, 예측은 다시 곱해 돌려준다.

    변동성이 두 배로 커진 국면(예: 2024–2025)에서도 '평소 변동 폭의 몇 배'라는 같은 척도로
    배우게 하려는 장치다. 척도는 그날 알려진 60일 변동성 × √h다.
    """

    def __init__(self, model, horizon: int):
        self.model, self.horizon = model, horizon
        self.name, self.features = model.name, model.features

    def _scale(self, data, rows):
        return data["vol_60"].to_numpy(float)[rows] * np.sqrt(self.horizon)

    def fit(self, data, rows, y):
        self.model.fit(data, rows, y / self._scale(data, rows))
        return self

    def predict(self, data, rows):
        return self.model.predict(data, rows) * self._scale(data, rows)


class LogisticModel:
    """상승 확률을 내는 로지스틱 회귀. predict()는 P(상승)을 돌려준다."""
    name = "Logistic"

    def __init__(self, features, C: float = 0.1):
        self.features, self.C = list(features), C

    def _x(self, data, rows):
        return data[self.features].to_numpy(float)[rows]

    def fit(self, data, rows, y):
        from sklearn.linear_model import LogisticRegression

        self.pipeline_ = make_pipeline(StandardScaler(), LogisticRegression(C=self.C, max_iter=1000))
        self.pipeline_.fit(self._x(data, rows), (np.asarray(y) > 0).astype(int))
        return self

    def predict(self, data, rows):
        return self.pipeline_.predict_proba(self._x(data, rows))[:, 1]


class LightGBMClassifier:
    """상승 확률을 내는 LightGBM. predict()는 P(상승)을 돌려준다."""
    name = "LightGBM분류"

    def __init__(self, features, n_estimators: int = 200, learning_rate: float = 0.03,
                 num_leaves: int = 7, min_child_samples: int = 80, seed: int = 42):
        self.features = list(features)
        self.params = dict(n_estimators=n_estimators, learning_rate=learning_rate, num_leaves=num_leaves,
                           min_child_samples=min_child_samples, subsample=0.8, subsample_freq=1,
                           colsample_bytree=0.8, random_state=seed, n_jobs=1, verbose=-1)

    def _x(self, data, rows):
        return data[self.features].to_numpy(float)[rows]

    def fit(self, data, rows, y):
        from lightgbm import LGBMClassifier

        self.model_ = LGBMClassifier(**self.params).fit(self._x(data, rows), (np.asarray(y) > 0).astype(int))
        return self

    def predict(self, data, rows):
        return self.model_.predict_proba(self._x(data, rows))[:, 1]


class ShrunkProbability:
    """분류기의 상승 확률을 학습 구간 상승 비율 쪽으로 당긴다.

    03에서 원래 확률은 너무 극단적이어서 Brier가 기본 비율보다 나빴다. weight=1이면 원래 확률,
    0이면 늘 기본 비율이다.
    """

    def __init__(self, model, weight: float):
        self.model, self.weight = model, weight
        self.name, self.features = model.name, model.features

    def fit(self, data, rows, y):
        self.base_ = float((np.asarray(y) > 0).mean())
        self.model.fit(data, rows, y)
        return self

    def predict(self, data, rows):
        return self.base_ + self.weight * (self.model.predict(data, rows) - self.base_)


def buy_signal(prob_up, threshold: float) -> np.ndarray:
    """상승 확률 ≥ threshold면 'buy'(지금 구매), ≤ 1−threshold면 'wait'(미루기), 그 사이는 'hold'(보류)."""
    prob_up = np.asarray(prob_up, float)
    return np.where(prob_up >= threshold, "buy", np.where(prob_up <= 1 - threshold, "wait", "hold"))


Z80 = 1.2815515655446004  # 표준정규 90% 분위수. 양쪽 10%씩 뺀 80% 범위


def price_range(close, log_vol, horizon: int, multiplier: float):
    """h거래일 뒤 가격의 80% 범위: close · exp(±1.28 · k · σ · √h). σ는 예측한 일간 변동성(exp(log_vol))."""
    half = Z80 * multiplier * np.exp(np.asarray(log_vol, float)) * np.sqrt(horizon)
    close = np.asarray(close, float)
    return close * np.exp(-half), close * np.exp(half)


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
