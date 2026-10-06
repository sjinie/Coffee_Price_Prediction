"""시간순 분할, 평가 지표, 블록 bootstrap, 해마다 후보를 고르는 중첩 검증."""
import numpy as np
import pandas as pd
from scipy.special import ndtr
from sklearn.metrics import roc_auc_score

from .config import TRAIN_START

LOOKBACK = 60  # 창 입력 모델(DLinear·LSTM)이 쓰는 과거 거래일 수. 모든 모델을 같은 행으로 비교한다.
FIRST_INNER_YEAR = 2009  # 안쪽 검증의 첫해. 2006–2008년(3년)으로 학습한 예측부터 쓴다.


def usable_rows(data: pd.DataFrame, features: list[str], horizon: int, start, end, fit_end=None,
                target: str | None = None) -> np.ndarray:
    """[start, end] 구간에서 학습·평가에 쓸 행 번호.

    - 직전 LOOKBACK 거래일의 피처가 모두 있어야 한다.
    - 타깃이 있어야 한다(기본은 y_h, 변동성은 v_h).
    - fit_end를 주면 목표일이 fit_end를 넘는 행은 뺀다. 학습 구간 끝에서 미래 가격이
      타깃으로 새어 들어오는 것을 막는다(embargo).
    """
    target = target or f"y_{horizon}"
    complete = data[features].notna().all(axis=1).rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
    mask = complete & data[target].notna()
    mask &= (data.index >= pd.Timestamp(start)) & (data.index <= pd.Timestamp(end))
    if fit_end is not None:
        mask &= data[f"target_date_{horizon}"] <= pd.Timestamp(fit_end)
    return np.flatnonzero(mask.to_numpy())


def fold_rows(data, features, horizon, year, last_year, target=None):
    """walk-forward 한 해의 (학습 행, 평가 행).

    - 학습 행: TRAIN_START부터 전년 말까지. 목표일도 전년 말을 넘지 않는다(embargo).
    - 평가 행: 기준일이 그해인 행. 목표일은 평가 구간의 마지막 해(last_year) 끝까지 허용한다.
      그해 끝에서 자르면 h일 예측의 연말 기준일(60일이면 10–12월)이 해마다 빠져 계절이 치우친다.
      구간 끝은 넘지 않으므로 개발 구간과 보류 구간의 정답이 섞이지 않는다.
    """
    fit = usable_rows(data, features, horizon, TRAIN_START, f"{year - 1}-12-31", fit_end=f"{year - 1}-12-31",
                      target=target)
    test = usable_rows(data, features, horizon, f"{year}-01-01", f"{year}-12-31", fit_end=f"{last_year}-12-31",
                       target=target)
    return fit, test


def walk_forward_predictions(data, make, row_features, horizon, years, last_year) -> pd.Series:
    """years마다 그 전까지의 자료로 학습해 그해를 예측한다(행 번호 → 예측, fold_rows와 같은 embargo).

    행은 row_features로 정하므로 후보마다 피처가 달라도 같은 행에서 비교한다.
    """
    y = data[f"y_{horizon}"].to_numpy()
    parts = []
    for year in years:
        fit, test = fold_rows(data, row_features, horizon, year, last_year)
        parts.append(pd.Series(make().fit(data, fit, y[fit]).predict(data, test), index=test))
    return pd.concat(parts)


def walk_forward_residuals(data, make, row_features, horizon, years, last_year) -> pd.Series:
    """분포 모델의 표본 밖 표준화 잔차(행 번호 → (y − μ)/σ). 학습·평가 행은 walk_forward_predictions와 같다.

    DistributionModel.calibrate에 넣어 모양을 학습에 쓰지 않은 해의 잔차로 맞춘다(노트북 07·06).
    """
    y = data[f"y_{horizon}"].to_numpy()
    parts = []
    for year in years:
        fit, test = fold_rows(data, row_features, horizon, year, last_year)
        parts.append(pd.Series(make().fit(data, fit, y[fit]).residuals(data, test, y[test]), index=test))
    return pd.concat(parts)


def shrink_weight(y_true, pred) -> float:
    """예측을 0(현재가 유지) 쪽으로 줄이는 비율 λ = Σpy / Σp²(최소제곱)를 [0, 1]로 자른 값."""
    y_true, pred = np.asarray(y_true, float), np.asarray(pred, float)
    denominator = float(np.sum(pred ** 2))
    return 0.0 if denominator == 0 else float(np.clip(np.sum(pred * y_true) / denominator, 0.0, 1.0))


def inner_mask(data, horizon, rows, year, first_inner_year=FIRST_INNER_YEAR) -> np.ndarray:
    """평가 연도 year의 안쪽 검증 행(rows 중): 기준일이 first_inner_year … year−1년이고 목표일이 year−1년 말 이전."""
    origin = data.index[rows]
    target_date = data[f"target_date_{horizon}"].to_numpy()[rows]
    return np.asarray((origin.year >= first_inner_year) & (origin.year < year)
                      & (target_date <= pd.Timestamp(f"{year - 1}-12-31")))


def _same_rows(candidates: dict) -> pd.Index:
    rows = next(iter(candidates.values())).index
    if not all(values.index.equals(rows) for values in candidates.values()):
        raise ValueError("후보마다 평가 행이 다르다")
    return rows


def select_by_year(data, horizon, predictions: dict, years, first_inner_year=FIRST_INNER_YEAR) -> pd.DataFrame:
    """평가 연도 Y마다 first_inner_year … Y−1년의 walk-forward 예측(안쪽 검증)으로 후보와 λ를 고른다.

    predictions는 후보 이름 → walk_forward_predictions 결과이며 모든 후보의 행이 같아야 한다. 안쪽 검증 행은
    목표일이 Y−1년 말을 넘지 않는다. 각 후보는 자기 λ를 적용한 RMSE로 겨루고, 어떤 후보도 Naive(예측 0)보다
    작지 않으면 'Naive'를 고른다. 같으면 먼저 넣은 후보가 이긴다.
    """
    rows = _same_rows(predictions)
    y = data[f"y_{horizon}"].to_numpy()[rows]
    records = []
    for year in years:
        inner = inner_mask(data, horizon, rows, year, first_inner_year)
        best = {"year": year, "candidate": "Naive", "weight": 0.0, "inner_rmse": float(np.sqrt(np.mean(y[inner] ** 2)))}
        for name, pred in predictions.items():
            p = pred.to_numpy()[inner]
            weight = shrink_weight(y[inner], p)
            score = float(np.sqrt(np.mean((y[inner] - weight * p) ** 2)))
            if score < best["inner_rmse"]:
                best = {"year": year, "candidate": name, "weight": weight, "inner_rmse": score}
        records.append({**best, "inner_rows": int(inner.sum())})
    return pd.DataFrame(records).set_index("year")


def select_by_score(data, horizon, scores: dict, years, first_inner_year=FIRST_INNER_YEAR) -> pd.DataFrame:
    """평가 연도 Y마다 안쪽 검증 행(inner_mask)의 행별 점수 평균이 가장 낮은 후보를 고른다.

    scores는 후보 이름 → walk-forward 행별 점수(예: CRPS, 낮을수록 좋음)이며 모든 후보의 행이 같아야 한다.
    같으면 먼저 넣은 후보가 이긴다. select_by_year와 달리 기본값(Naive)이나 축소 비율은 없다.
    """
    rows = _same_rows(scores)
    records = []
    for year in years:
        inner = inner_mask(data, horizon, rows, year, first_inner_year)
        means = {name: float(score.to_numpy()[inner].mean()) for name, score in scores.items()}
        best = min(means, key=means.get)
        records.append({"year": year, "candidate": best, "inner_score": means[best], "inner_rows": int(inner.sum())})
    return pd.DataFrame(records).set_index("year")


def apply_selection(data, predictions: dict, selection: pd.DataFrame) -> pd.Series:
    """select_by_year의 연도별 선택을 적용한 예측(행 번호 → λ × 고른 후보의 예측, Naive면 0)."""
    rows = next(iter(predictions.values())).index
    year = data.index[rows].year
    out = pd.Series(np.nan, index=rows)
    for y, choice in selection.iterrows():
        mask = np.asarray(year == y)
        out[mask] = 0.0 if choice["candidate"] == "Naive" else choice["weight"] * predictions[choice["candidate"]].to_numpy()[mask]
    return out


def metrics(y_true, y_pred) -> dict:
    """RMSE·MAE와 방향 지표.

    방향 지표는 실제 수익률이 0인 날을 뺀다. 예측이 정확히 0이면 방향을 말하지 않은 것이므로
    오답으로 센다. 모든 예측이 0인 Naive는 방향 지표를 계산하지 않는다(NaN).
    """
    y_true, y_pred = np.asarray(y_true, float), np.asarray(y_pred, float)
    result = {"n": len(y_true), "rmse": float(np.sqrt(np.mean((y_true - y_pred) ** 2))),
              "mae": float(np.mean(np.abs(y_true - y_pred)))}
    moved = y_true != 0
    if np.all(y_pred == 0) or not moved.any():
        return {**result, "direction_acc": np.nan, "balanced_acc": np.nan}
    hit = np.sign(y_pred[moved]) == np.sign(y_true[moved])
    up, down = y_true[moved] > 0, y_true[moved] < 0
    recalls = [hit[side].mean() for side in (up, down) if side.any()]
    return {**result, "direction_acc": float(hit.mean()), "balanced_acc": float(np.mean(recalls))}


def direction_metrics(y_true, prob_up, base_rate: float, threshold: float = 0.6) -> dict:
    """상승 확률 예측의 평가.

    - brier: 확률 오차의 제곱 평균. base_brier는 학습 구간의 상승 비율을 늘 말했을 때의 값이다.
    - auc: 상승한 날에 더 높은 확률을 줬는지(0.5면 구분 못 함).
    - 신호: 확률이 threshold 이상이면 '지금 구매', 1-threshold 이하면 '미루기', 사이는 보류.
      precision은 신호를 낸 날 중 맞힌 비율, coverage는 신호를 낸 날의 비율이다.
    - signal_up_rate: 신호를 낸 그날들의 실제 상승 비율. 같은 날 늘 '구매'라고 했을 때의 적중률이라
      precision과 비교하는 기준선이다(전체 기간 상승 비율과 비교하면 모집단이 달라진다).
    """
    y_true, prob_up = np.asarray(y_true, float), np.asarray(prob_up, float)
    moved = y_true != 0
    up, prob = (y_true[moved] > 0).astype(float), prob_up[moved]
    buy, wait = prob >= threshold, prob <= 1 - threshold
    signals = buy | wait
    correct = (buy & (up == 1)) | (wait & (up == 0))
    return {
        "n": int(moved.sum()), "up_rate": float(up.mean()),
        "brier": float(np.mean((prob - up) ** 2)), "base_brier": float(np.mean((base_rate - up) ** 2)),
        "auc": float(roc_auc_score(up, prob)) if 0 < up.mean() < 1 else np.nan,
        "coverage": float(signals.mean()),
        "precision": float(correct[signals].mean()) if signals.any() else np.nan,
        "signal_up_rate": float(up[signals].mean()) if signals.any() else np.nan,
        "buy_precision": float(up[buy].mean()) if buy.any() else np.nan,
    }


def crps_normal(y, mu, sigma) -> np.ndarray:
    """정규분포 N(μ, σ²) 예측의 행별 CRPS(Gneiting·Raftery 2007의 닫힌 식). 낮을수록 좋다.

    CRPS는 예측 분포 전체와 실제 값의 거리다. σ → 0이면 |y − μ|(절대오차)가 된다.
    """
    z = (np.asarray(y, float) - mu) / sigma
    return sigma * (z * (2 * ndtr(z) - 1) + 2 * np.exp(-z ** 2 / 2) / np.sqrt(2 * np.pi) - 1 / np.sqrt(np.pi))


def _block_samples(n: int, block: int, n_boot: int, seed: int):
    """원형 블록 bootstrap의 행 번호 표본을 n_boot번 낸다.

    h일 수익률은 날짜가 겹쳐 오차가 서로 상관된다. 그래서 하루씩이 아니라 길이 block의
    연속 구간을 뽑는다.
    """
    rng = np.random.default_rng(seed)
    blocks = int(np.ceil(n / block))
    for _ in range(n_boot):
        starts = rng.integers(0, n, blocks)
        yield ((starts[:, None] + np.arange(block)) % n).ravel()[:n]


def block_bootstrap_rmse_diff(y_true, pred_a, pred_b, block: int, n_boot: int = 1000, seed: int = 42) -> dict:
    """RMSE(a) − RMSE(b)의 95% 구간(원형 블록 bootstrap). 음수면 a가 더 정확하다."""
    y_true, pred_a, pred_b = (np.asarray(v, float) for v in (y_true, pred_a, pred_b))
    diffs = [np.sqrt(np.mean((y_true[i] - pred_a[i]) ** 2)) - np.sqrt(np.mean((y_true[i] - pred_b[i]) ** 2))
             for i in _block_samples(len(y_true), block, n_boot, seed)]
    point = float(np.sqrt(np.mean((y_true - pred_a) ** 2)) - np.sqrt(np.mean((y_true - pred_b) ** 2)))
    low, high = np.percentile(diffs, [2.5, 97.5])
    return {"diff": point, "low": float(low), "high": float(high)}


def block_bootstrap_mean_diff(loss_a, loss_b, block: int, n_boot: int = 1000, seed: int = 42) -> dict:
    """행별 손실(예: CRPS) 평균의 차이 mean(a) − mean(b)와 95% 구간. 음수면 a가 더 좋다."""
    diff = np.asarray(loss_a, float) - np.asarray(loss_b, float)
    low, high = np.percentile([diff[i].mean() for i in _block_samples(len(diff), block, n_boot, seed)], [2.5, 97.5])
    return {"diff": float(diff.mean()), "low": float(low), "high": float(high)}
