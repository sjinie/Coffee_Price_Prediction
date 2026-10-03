import numpy as np
import pandas as pd

from coffee.news import daily_news


def article(available, event, bull=0.8, bear=0.1, relevance=1.0):
    return {"available_at": pd.Timestamp(available, tz="UTC"), "event_at": pd.Timestamp(event, tz="UTC"),
            "p_bullish": bull, "p_bearish": bear, "relevance": relevance}


def test_live_news_goes_to_first_close_after_it_became_available():
    sessions = pd.DatetimeIndex(["2026-09-28", "2026-09-29", "2026-09-30"])
    articles = pd.DataFrame([
        article("2026-09-28 22:59", "2026-09-28 10:00"),               # 마감 전 → 9/28
        article("2026-09-28 23:01", "2026-09-28 10:00", 0.1, 0.7),     # 마감 후 → 9/29
        article("2026-10-05 01:00", "2026-09-28 10:00"),               # 아직 오지 않은 마감 → 제외
    ])
    news = daily_news(articles, sessions)
    assert news["news_count"].tolist() == [1, 1, 0]
    assert np.isclose(news.loc["2026-09-28", "news_score"], np.tanh(0.7))
    assert np.isclose(news.loc["2026-09-29", "news_score"], np.tanh(-0.6))
    assert np.isnan(news.loc["2026-09-30", "news_score"])


def test_research_mode_uses_publication_plus_one_day():
    sessions = pd.DatetimeIndex(["2024-03-04", "2024-03-05"])
    articles = pd.DataFrame([article("2026-09-26 12:00", "2024-03-04 01:00")])  # 2026년에 소급 분류
    assert daily_news(articles, sessions)["news_count"].sum() == 0  # live로는 과거 날짜에 쓸 수 없다
    research = daily_news(articles, sessions, mode="research")
    assert research["news_count"].tolist() == [0, 1]  # 발행 + 1일 = 3/5 01:00 → 3/5 마감
