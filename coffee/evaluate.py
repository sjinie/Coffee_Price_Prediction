"""시간순 분할, 평가 지표, 블록 bootstrap."""
import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from .config import TRAIN_START

LOOKBACK = 60  # 창 입력 모델(DLinear·LSTM)이 쓰는 과거 거래일 수. 모든 모델을 같은 행으로 비교한다.


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
        "buy_precision": float(up[buy].mean()) if buy.any() else np.nan,
    }


def block_bootstrap_rmse_diff(y_true, pred_a, pred_b, block: int, n_boot: int = 1000, seed: int = 42) -> dict:
    """RMSE(a) − RMSE(b)의 95% 구간. 음수면 a가 더 정확하다.

    h일 수익률은 날짜가 겹쳐 오차가 서로 상관된다. 그래서 하루씩이 아니라 길이 block의
    연속 구간을 뽑는 원형 블록 bootstrap을 쓴다.
    """
    y_true, pred_a, pred_b = (np.asarray(v, float) for v in (y_true, pred_a, pred_b))
    n = len(y_true)
    rng = np.random.default_rng(seed)
    blocks = int(np.ceil(n / block))
    diffs = np.empty(n_boot)
    for i in range(n_boot):
        starts = rng.integers(0, n, blocks)
        index = ((starts[:, None] + np.arange(block)) % n).ravel()[:n]
        err_a = (y_true[index] - pred_a[index]) ** 2
        err_b = (y_true[index] - pred_b[index]) ** 2
        diffs[i] = np.sqrt(err_a.mean()) - np.sqrt(err_b.mean())
    point = float(np.sqrt(np.mean((y_true - pred_a) ** 2)) - np.sqrt(np.mean((y_true - pred_b) ** 2)))
    low, high = np.percentile(diffs, [2.5, 97.5])
    return {"diff": point, "low": float(low), "high": float(high)}
