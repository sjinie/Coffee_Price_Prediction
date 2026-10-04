"""포트폴리오 화면에 쓰는 연구 자료를 만든다.

API로 바로 읽을 수 없는 것(노트북 05의 뉴스 시차 상관, 원자료 요약, 피처 묶음, 평가 구간)만 담는다.
예측 방법(피처, 평가 구간, 동결 모델, 뉴스 처리)을 바꾸면 다시 실행하고 frontend/src/research.js의 문장도 함께 고친다.

    python -m coffee.portfolio   # → frontend/src/research-data.json
"""
import json

import numpy as np
import pandas as pd

from coffee.config import DEV_YEARS, FORWARD_START, HOLDOUT_YEARS, ROOT, SETTINGS, TRAIN_START
from coffee.features import FEATURE_GROUPS, build_dataset
from coffee.news import daily_news, load_jev_archive
from coffee.sources import load_sources

OUTPUT = ROOT / "frontend" / "src" / "research-data.json"
BRAZIL = ("br_sul_minas", "br_cerrado", "br_alta_mogiana")


def news_lag(close: pd.Series, score: pd.Series, max_lag: int = 10) -> dict:
    """거래일 t의 뉴스 점수와 t+k일 일간 로그수익률의 상관. k < 0은 점수보다 앞선 수익률이다(노트북 05와 같은 계산)."""
    daily_return = np.log(close).diff()
    scored = score.notna()
    lags = list(range(-max_lag, max_lag + 1))
    corr = [score[scored].corr(daily_return.shift(-k)[scored]) for k in lags]
    return {"k": lags, "r": [round(float(c), 3) for c in corr], "band": round(float(2 / np.sqrt(scored.sum())), 3)}


def _values(series: pd.Series, digits: int) -> list:
    return [None if pd.isna(v) else round(float(v), digits) for v in series]


def build() -> dict:
    sources = load_sources()
    data = build_dataset(sources)
    news = daily_news(load_jev_archive(), data.index, mode="research")

    def observed(name: str, column: str = "value") -> pd.Series:
        return sources[name].set_index("date")[column]

    # 스파크라인은 모양만 보여 주므로 4주마다 한 점, 강수는 월 합계로 줄인다
    weekly = lambda s: s.resample("W-FRI").last().dropna()[str(TRAIN_START.year - 1):]
    rain = pd.concat([observed(f"weather_{r}", "PRECTOTCORR") for r in BRAZIL], axis=1).mean(axis=1)
    return {
        "model_version": SETTINGS["model_version"],
        "periods": {
            "train_start": TRAIN_START.strftime("%Y-%m-%d"),
            "development": [DEV_YEARS[0], DEV_YEARS[-1]],
            "holdout": [HOLDOUT_YEARS[0], HOLDOUT_YEARS[-1]],
            "forward_start": FORWARD_START.strftime("%Y-%m-%d"),
        },
        "feature_groups": FEATURE_GROUPS,
        "news_lag": news_lag(data["close"], news["news_score"]),
        "sparks": {
            "price": _values(weekly(observed("prices", "close")).iloc[::4], 1),
            "brl": _values(weekly(observed("macro_brl")).iloc[::4], 3),
            "rate": _values(weekly(observed("macro_rate")).iloc[::4], 2),
            "rain": _values(rain["2005":].resample("MS").sum(min_count=1), 0),
            "news": _values(news["news_score"]["2022":].resample("W-FRI").mean(), 2),
        },
    }


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{OUTPUT.relative_to(ROOT)} 저장")
