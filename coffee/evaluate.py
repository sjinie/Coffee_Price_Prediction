"""시간순 분할, 평가 지표, 블록 bootstrap."""
import numpy as np
import pandas as pd

LOOKBACK = 60  # 창 입력 모델(DLinear·LSTM)이 쓰는 과거 거래일 수. 모든 모델을 같은 행으로 비교한다.


def usable_rows(data: pd.DataFrame, features: list[str], horizon: int, start, end, fit_end=None) -> np.ndarray:
    """[start, end] 구간에서 학습·평가에 쓸 행 번호.

    - 직전 LOOKBACK 거래일의 피처가 모두 있어야 한다.
    - 타깃이 있어야 한다(P[t], P[t+h] 모두 존재).
    - fit_end를 주면 목표일이 fit_end를 넘는 행은 뺀다. 학습 구간 끝에서 미래 가격이
      타깃으로 새어 들어오는 것을 막는다(embargo).
    """
    complete = data[features].notna().all(axis=1).rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
    mask = complete & data[f"y_{horizon}"].notna()
    mask &= (data.index >= pd.Timestamp(start)) & (data.index <= pd.Timestamp(end))
    if fit_end is not None:
        mask &= data[f"target_date_{horizon}"] <= pd.Timestamp(fit_end)
    return np.flatnonzero(mask.to_numpy())


def metrics(y_true, y_pred) -> dict:
    """RMSE·MAE(로그수익률)와 방향 지표.

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
