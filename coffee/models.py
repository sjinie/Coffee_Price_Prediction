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
from scipy.special import ndtr
from sklearn.linear_model import Ridge
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .evaluate import LOOKBACK, crps_normal


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


HAR_FEATURES = ["log_vol_5", "log_vol_20", "log_vol_60"]  # 04의 HAR 입력


class ScaledReturn:
    """수익률을 변동성 척도로 나눠 학습하고, 예측은 다시 곱해 돌려준다.

    변동성이 두 배로 커진 국면(예: 2024–2025)에서도 '평소 변동 폭의 몇 배'라는 같은 척도로
    배우게 하려는 장치다. 척도 × √h에서 척도는 둘 중 하나다.
    - 'vol_60': 그날 알려진 60일 변동성
    - 'har': 학습 구간에서 다시 맞춘 HAR(5·20·60일 로그 변동성 → 앞으로 h일 로그 변동성)의 예측.
      04에서 HAR이 직전 변동성보다 앞으로의 변동성을 잘 맞혔다(03b 후보).
    """

    def __init__(self, model, horizon: int, scale: str = "vol_60"):
        self.model, self.horizon, self.scale = model, horizon, scale
        self.name = model.name
        self.features = list(dict.fromkeys(model.features + (HAR_FEATURES if scale == "har" else ["vol_60"])))

    def _scale(self, data, rows):
        if getattr(self, "scale", "vol_60") == "har":  # 03b 이전 artifact에는 scale 속성이 없다
            return np.exp(self.har_.predict(data, rows)) * np.sqrt(self.horizon)
        return data["vol_60"].to_numpy(float)[rows] * np.sqrt(self.horizon)

    def fit(self, data, rows, y):
        if self.scale == "har":  # 학습 행 가운데 앞으로의 변동성이 확인된 행으로만 맞춘다
            target = data[f"v_{self.horizon}"].to_numpy(float)
            known = np.asarray(rows)[~np.isnan(target[rows])]
            self.har_ = RidgeModel(HAR_FEATURES, alpha=1).fit(data, known, target[known])
        self.model.fit(data, rows, y / self._scale(data, rows))
        return self

    def predict(self, data, rows):
        return self.model.predict(data, rows) * self._scale(data, rows)


class ShrunkReturn:
    """예측 로그수익률에 weight(0–1)를 곱해 현재가 유지(0) 쪽으로 줄인다.

    신호가 약할 때 예측을 크게 내면 RMSE가 Naive보다 커진다(MSE(p) − MSE(0) = E[p²] − 2E[p·y]).
    weight는 03b의 안쪽 검증 예측으로 정한다(evaluate.shrink_weight). 0이면 Naive와 같다.
    """

    def __init__(self, model, weight: float):
        self.model, self.weight = model, weight
        self.name, self.features = model.name, model.features

    def fit(self, data, rows, y):
        self.model.fit(data, rows, y)
        return self

    def predict(self, data, rows):
        return self.weight * self.model.predict(data, rows)


RETURN_ALGORITHMS = {  # 대시보드에 보여 줄 이름
    "Ridge": "Ridge 회귀 (변동성 정규화)",
    "LightGBM": "LightGBM 회귀 (변동성 정규화)",
    "DLinear": "DLinear (변동성 정규화)",
}
RETURN_DEFAULTS = {  # 03에서 정한 설정. 03b는 후보마다 params로 바꿔 넘긴다
    "Ridge": {"alpha": 100},
    "LightGBM": {"n_estimators": 150, "num_leaves": 7, "min_child_samples": 100},
    "DLinear": {"alpha": 1000},
}


def return_model(name: str, features, horizon: int, params: dict | None = None,
                 scale: str = "vol_60") -> ScaledReturn:
    """수익률 회귀 모델. 03·03b·04·06·파이프라인이 같은 설정을 쓰도록 한곳에 둔다."""
    make = {"Ridge": RidgeModel, "LightGBM": LightGBMModel, "DLinear": DLinearModel}[name]
    return ScaledReturn(make(features, **{**RETURN_DEFAULTS[name], **(params or {})}), horizon, scale)


def describe_return(name: str, params: dict, scale: str, weight: float) -> str:
    """대시보드에 보여 줄 수익률 모델 이름. 예: 'Ridge 회귀 (alpha=1000, HAR 정규화, 0.31배로 축소)'."""
    settings = ", ".join(f"{key}={value}" for key, value in params.items())
    model = {"Ridge": "Ridge 회귀", "LightGBM": "LightGBM 회귀", "DLinear": "DLinear"}[name]
    return f"{model} ({settings}, {'HAR' if scale == 'har' else '60일 변동성'} 정규화, {weight:.2f}배로 축소)"


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


def buy_signal(prob_up, threshold: float | None) -> np.ndarray:
    """상승 확률 ≥ threshold면 'buy'(지금 구매), ≤ 1−threshold면 'wait'(미루기), 그 사이는 'hold'(보류).

    threshold가 None이면 근거 있는 기준을 찾지 못했다는 뜻이라 늘 'hold'다(노트북 07).
    """
    prob_up = np.asarray(prob_up, float)
    if threshold is None:
        return np.full(prob_up.shape, "hold")
    return np.where(prob_up >= threshold, "buy", np.where(prob_up <= 1 - threshold, "wait", "hold"))


Z80 = 1.2815515655446004  # 표준정규 90% 분위수. 양쪽 10%씩 뺀 80% 범위


def price_range(close, log_vol, horizon: int, multiplier: float, center=0.0):
    """h거래일 뒤 가격의 80% 범위: close · exp(r̂ ± 1.28 · k · σ · √h).

    σ는 예측한 일간 변동성(exp(log_vol)), r̂(center)은 예측 로그수익률이다. 범위는 예측 가격을 가운데에 둔다.
    """
    half = Z80 * multiplier * np.exp(np.asarray(log_vol, float)) * np.sqrt(horizon)
    center = np.asarray(center, float)
    close = np.asarray(close, float)
    return close * np.exp(center - half), close * np.exp(center + half)


LEVELS = (0.1, 0.5, 0.9)  # 80% 범위의 아래 끝, 가운데, 위 끝
CLIP = 5.0  # 표준화한 입력의 한계. 오류 시세나 처음 보는 값 하나가 지수식 폭을 터뜨리지 않게 한다


class DistributionModel:
    """h거래일 뒤 로그수익률의 분포 y = μ(x) + σ(x)·Z(노트북 07). 범위·상승 확률·신호가 모두 이 분포에서 나온다.

    - 중심 μ(x) = w·x: 분포가 현재가 위에 놓이는지 아래에 놓이는지(방향). 절편이 없어 피처가 학습 구간
      평균이면 현재가 유지다. 07 v1에서 절편(과거 평균 추세)이 오차를 키웠다.
    - 폭 log σ(x) = b + u·HAR + v·x: 변동폭. HAR(log_vol_5/20/60)은 04에서 기준선을 넘은 변동성 입력이다.
    - 모양 Z: 표준화 잔차 (y − μ)/σ의 실제 분포에서 중앙값을 0으로 맞춘 것. 서리 급등 같은 꼬리와 비대칭만
      담고 방향은 담지 않는다. fit은 학습 행 잔차로 두고, calibrate로 표본 밖 잔차로 바꾼다.

    μ와 σ는 정규분포 CRPS의 합 하나를 최소화해 함께 맞춘다. 폭의 절편과 HAR 계수에는 벌점이 없고 피처
    계수 w·v에는 L2 벌점(alpha_mean, alpha_scale)을 준다. 벌점이 아주 크면 μ는 0, σ는 HAR만 남는다.
    피처는 학습 행으로 표준화해 ±CLIP에서 자르고, 타깃은 학습 행의 표준편차로 나눠 벌점의 크기가 지평과
    무관하게 한다. 학습·평가 행은 피처가 모두 있다. 서빙 때 공개가 늦어 빠진 피처는 학습 구간 평균
    (표준화 값 0)으로 보고, 파이프라인이 결측을 경고한다.
    """
    name = "분포"

    def __init__(self, mean_features=(), scale_features=(), har: bool = True,
                 alpha_mean: float = 1000.0, alpha_scale: float = 1000.0):
        self.mean_features, self.scale_features = list(mean_features), list(scale_features)
        self.har, self.alpha_mean, self.alpha_scale = har, alpha_mean, alpha_scale
        self.har_features = HAR_FEATURES if har else []
        self.features = list(dict.fromkeys(self.mean_features + self.scale_features + self.har_features))

    def _x(self, data, rows):
        x = np.clip((data[self.features].to_numpy(float)[rows] - self.center_) / self.spread_, -CLIP, CLIP)
        return np.nan_to_num(x, nan=0.0)

    def _split(self, theta):
        m, k = len(self.mean_features), len(self.har_features)
        return theta[:m], theta[m], theta[m + 1:m + 1 + k], theta[m + 1 + k:]

    def _heads(self, x, theta):
        """표준화한 단위의 μ와 log σ."""
        w, b, u, v = self._split(theta)
        return x[:, self.mean_] @ w, b + x[:, self.har_] @ u + x[:, self.scale_] @ v

    def _objective(self, theta, x, y):
        mu, eta = self._heads(x, theta)
        sigma = np.exp(eta)
        z = (y - mu) / sigma
        cdf, pdf = ndtr(z), np.exp(-z ** 2 / 2) / np.sqrt(2 * np.pi)
        d_mu, d_eta = 1 - 2 * cdf, sigma * (2 * pdf - 1 / np.sqrt(np.pi))  # CRPS의 μ, log σ 미분
        w, _, _, v = self._split(theta)
        loss = crps_normal(y, mu, sigma).sum() + self.alpha_mean * w @ w + self.alpha_scale * v @ v
        grad = np.concatenate([x[:, self.mean_].T @ d_mu + 2 * self.alpha_mean * w, [d_eta.sum()],
                               x[:, self.har_].T @ d_eta, x[:, self.scale_].T @ d_eta + 2 * self.alpha_scale * v])
        return loss, grad

    def fit(self, data, rows, y):
        from scipy.optimize import minimize

        raw = data[self.features].to_numpy(float)[rows]
        self.center_, spread = raw.mean(axis=0), raw.std(axis=0)
        self.spread_ = np.where(spread > 0, spread, 1.0)  # 학습 행에서 늘 같은 값(수집 전 뉴스 0)은 영향이 없다
        column = {name: i for i, name in enumerate(self.features)}
        self.mean_, self.har_, self.scale_ = (np.array([column[name] for name in names], dtype=int)
                                              for names in (self.mean_features, self.har_features, self.scale_features))
        y = np.asarray(y, float)
        self.y_scale_ = float(np.std(y))
        x, target = self._x(data, rows), y / self.y_scale_
        start = np.zeros(1 + len(self.mean_) + len(self.har_) + len(self.scale_))
        result = minimize(self._objective, start, args=(x, target), jac=True, method="L-BFGS-B",
                          options={"maxiter": 5000})
        if not result.success:
            raise RuntimeError(f"분포 모델 학습이 수렴하지 않음: {result.message}")
        self.theta_ = result.x
        return self.calibrate(self.residuals(data, rows, y))

    def residuals(self, data, rows, y) -> np.ndarray:
        """표준화 잔차 (y − μ)/σ. 학습에 쓰지 않은 행으로 계산하면 calibrate에 넣을 표본 밖 잔차다."""
        mu, sigma = self.distribution(data, rows)
        return (np.asarray(y, float) - mu) / sigma

    def calibrate(self, residuals):
        """모양 Z를 주어진 표준화 잔차로 바꾼다. 중앙값을 0으로 맞춰 꼬리·비대칭만 남긴다."""
        residuals = np.asarray(residuals, float)
        self.shape_ = np.sort(residuals - np.median(residuals))
        m = len(self.shape_)
        self.half_spread_ = float(np.sum((2 * np.arange(1, m + 1) - m - 1) * self.shape_) / m ** 2)  # E|Z − Z'| / 2
        return self

    def distribution(self, data, rows) -> tuple[np.ndarray, np.ndarray]:
        """기준일마다 로그수익률 단위의 (μ, σ)."""
        mu, eta = self._heads(self._x(data, rows), self.theta_)
        return self.y_scale_ * mu, self.y_scale_ * np.exp(eta)

    def quantiles(self, data, rows, levels=LEVELS) -> np.ndarray:
        """(행, 분위) 로그수익률 분위수. 기본은 80% 범위의 아래 끝·가운데·위 끝."""
        mu, sigma = self.distribution(data, rows)
        return mu[:, None] + sigma[:, None] * np.quantile(self.shape_, levels)[None, :]

    def spread(self, data, rows) -> np.ndarray:
        """예측 분포의 표준편차(로그수익률 단위) = σ × 모양의 표준편차."""
        return self.distribution(data, rows)[1] * float(np.std(self.shape_))

    def prob_up(self, data, rows) -> np.ndarray:
        """분포 가운데 현재가보다 위(로그수익률 > 0)에 있는 비율."""
        mu, sigma = self.distribution(data, rows)
        return 1 - np.searchsorted(self.shape_, -mu / sigma, side="right") / len(self.shape_)

    def predict(self, data, rows):
        """분포의 가운데(중앙값) 로그수익률. 맞히려는 값이 아니라 범위의 중심이다."""
        return self.quantiles(data, rows, [0.5])[:, 0]

    def crps(self, data, rows, y) -> np.ndarray:
        """행마다 CRPS(예측 분포, 실제) = E|X − y| − E|X − X'|/2. 낮을수록 좋다."""
        mu, sigma = self.distribution(data, rows)
        u = (np.asarray(y, float) - mu) / sigma
        m, cum = len(self.shape_), np.concatenate([[0.0], np.cumsum(self.shape_)])
        k = np.searchsorted(self.shape_, u, side="right")  # u 이하인 잔차 수
        mean_abs = (k * u - cum[k] + (cum[-1] - cum[k]) - (m - k) * u) / m
        return sigma * (mean_abs - self.half_spread_)

    def coefficients(self) -> dict:
        """표준화한 피처 1단위가 μ(타깃 표준편차 단위)와 log σ를 얼마나 움직이는지."""
        w, _, u, v = self._split(self.theta_)
        return {"mean": dict(zip(self.mean_features, w.tolist())),
                "scale": dict(zip(self.har_features + self.scale_features, np.concatenate([u, v]).tolist()))}

    def describe(self) -> str:
        return (f"분포 모델 (중심: 피처 {len(self.mean_features)}개, L2 α={self.alpha_mean:g} / "
                f"폭: {'HAR + ' if self.har else ''}피처 {len(self.scale_features)}개, L2 α={self.alpha_scale:g} / CRPS 학습)")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_safe(value):
    """NaN·무한대를 null로 바꾼다. PostgreSQL jsonb와 브라우저의 JSON.parse는 NaN을 받지 않는다."""
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return value


def save_bundle(directory: Path, models: dict[int, object], metadata: dict) -> None:
    """지평별 모델(h5.joblib …)과 metadata.json을 저장한다. 파일 해시도 함께 적는다.

    신호를 내지 않는 지평의 신호 적중률처럼 정의되지 않는 값(NaN)은 null로 쓴다.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    files = {}
    for horizon, model in models.items():
        path = directory / f"h{horizon}.joblib"
        joblib.dump(model, path)
        files[path.name] = _sha256(path)
    document = _json_safe({**metadata, "files": files})
    text = json.dumps(document, indent=2, ensure_ascii=False, default=str, allow_nan=False)
    (directory / "metadata.json").write_text(text + "\n", encoding="utf-8")


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
