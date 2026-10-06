"""뉴스: 선정 기사의 Jev 분석을 거래일별 뉴스 점수로 만든다.

기사 수집(Google News RSS·Yahoo)과 Jev 분류는 coffee/jev.py가 맡는다.
"""
import json
from pathlib import Path

import numpy as np
import pandas as pd

from .config import DATA_DIR, SETTINGS

CUTOFF = pd.Timedelta(hours=23)  # 거래일 마감 23:00 UTC
COLUMNS = ["content_hash", "title", "url", "source", "event_at", "analyzed_at", "available_at",
           "label", "p_bullish", "p_bearish", "p_neutral", "p_uncertain", "relevance", "confidence"]


def load_jev_archive(directory: Path = DATA_DIR / "jev") -> pd.DataFrame:
    """기존 Jev 보관 파일(responses.json)에서 기사마다 처음 분석한 결과 한 건씩 읽는다.

    처음 분석한 결과를 쓰는 이유: 나중에 다시 분석한 결과는 그 시점에 알 수 없었던 값이다.
    """
    analyses = json.loads((Path(directory) / "responses.json").read_text(encoding="utf-8"))["analyses"]
    frame = pd.DataFrame(analyses)
    frame = frame[(frame["model"] == SETTINGS["jev"]["model"])
                  & (frame["prompt_version"] == SETTINGS["jev"]["prompt_version"])]
    for column in ("event_at", "analyzed_at", "available_at"):
        frame[column] = pd.to_datetime(frame[column], utc=True, format="ISO8601")
    frame = frame.sort_values("analyzed_at").drop_duplicates("content_hash", keep="first")
    return frame[COLUMNS].sort_values("event_at").reset_index(drop=True)


def daily_news(articles: pd.DataFrame, sessions: pd.DatetimeIndex, mode: str = "live",
               research_lag: pd.Timedelta = pd.Timedelta(days=1)) -> pd.DataFrame:
    """거래일별 뉴스 점수 = tanh(Σ (P상승 − P하락) × 관련성). 기사가 없는 날은 결측이다.

    - live: 분석이 끝나 실제로 쓸 수 있게 된 시각(available_at) 이후 첫 거래일 마감에 넣는다.
      서비스는 이 방식만 쓴다.
    - research: 과거 기사는 2026년에 소급 분류해 실제 이용 시각이 없다. 발행 + 1일에 알았다고
      가정한 연구용 시각이다. 분류는 별개의 결정 모델(Jev)에 기사만 평가하게 하므로, 이후 가격을
      알고 판단했을 위험은 낮게 본다. 점수는 이후 수익률과 관련이 없었다(노트북 05).
    """
    if mode not in ("live", "research"):
        raise ValueError("mode는 live 또는 research")
    when = articles["available_at"] if mode == "live" else articles["event_at"] + research_lag
    keep = when.notna()
    closes = pd.DatetimeIndex(sessions).tz_localize("UTC") + CUTOFF
    position = closes.searchsorted(pd.DatetimeIndex(when[keep]))  # 그 시각 이후 처음 맞는 마감
    pressure = ((articles["p_bullish"] - articles["p_bearish"]) * articles["relevance"]).to_numpy()[keep.to_numpy()]
    inside = position < len(sessions)
    total = np.bincount(position[inside], weights=pressure[inside], minlength=len(sessions))
    count = np.bincount(position[inside], minlength=len(sessions))
    return pd.DataFrame({"news_score": np.where(count > 0, np.tanh(total), np.nan), "news_count": count},
                        index=sessions)
