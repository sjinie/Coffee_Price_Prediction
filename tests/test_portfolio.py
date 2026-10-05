"""화면의 연구 자료가 지금의 예측 방법과 맞는지 검사한다.

동결 모델, 피처, 평가 구간을 바꾸면 여기서 실패한다. AGENTS.md '예측 방법을 바꿀 때'에 따라
python -m coffee.portfolio를 다시 실행하고 frontend/src/research.js의 문장과 숫자를 고친다.
"""
import json
import re

import numpy as np
import pandas as pd

from coffee.config import DEV_YEARS, HOLDOUT_YEARS, ROOT, SETTINGS
from coffee.features import EXTRA_GROUPS, FEATURE_GROUPS, NEWS_FEATURES
from coffee.portfolio import OUTPUT, horizon_moves, news_lag


def test_research_data_matches_current_method():
    data = json.loads(OUTPUT.read_text(encoding="utf-8"))
    assert data["model_version"] == SETTINGS["model_version"]
    assert data["feature_groups"] == {**FEATURE_GROUPS, **EXTRA_GROUPS, "news": NEWS_FEATURES}
    assert data["periods"]["development"] == [DEV_YEARS[0], DEV_YEARS[-1]]
    assert data["periods"]["holdout"] == [HOLDOUT_YEARS[0], HOLDOUT_YEARS[-1]]


def test_research_text_describes_current_model():
    text = (ROOT / "frontend" / "src" / "research.js").read_text(encoding="utf-8")
    assert re.search(r"MODEL_VERSION = '([^']+)'", text).group(1) == SETTINGS["model_version"]


def test_news_lag_finds_score_that_explains_yesterday():
    # 점수가 전날 수익률을 그대로 옮긴 것이면 k = -1에서 상관이 1이고 이후 수익률과는 거의 0이다
    days = pd.bdate_range("2024-01-01", periods=400)
    returns = pd.Series(np.random.default_rng(0).normal(0, 0.02, len(days)), index=days)
    close = 100 * np.exp(returns.cumsum())
    score = np.log(close).diff().shift(1)
    lag = news_lag(close, score)
    best = lag["k"][int(np.argmax(lag["r"]))]
    assert best == -1 and lag["r"][lag["k"].index(-1)] > 0.99
    assert max(abs(r) for k, r in zip(lag["k"], lag["r"]) if k > 0) < 0.2


def test_horizon_moves_use_model_targets_and_skip_missing_pairs():
    # y_h는 거래 세션 달력 위의 목표다. 종가가 없는 쌍(NaN)은 세지 않는다.
    target = np.log([1.2, 0.8, np.nan, 1.05])
    data = pd.DataFrame({f"y_{h}": target for h in (5, 20, 60)})
    moves = horizon_moves(data)["20"]
    assert moves["rows"] == 3 and moves["q50"] == 5.0
    assert moves["big"] == round(2 / 3, 3)   # +20%, −20%가 10%를 넘는다
