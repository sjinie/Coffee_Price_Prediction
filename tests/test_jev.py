from datetime import date

import pandas as pd
import pytest

from coffee import jev

RSS = b"""<?xml version="1.0"?><rss><channel>
<item><title>Coffee Prices Rise on Brazil Weather Risks - TradingView</title><link>https://news.google.com/a</link>
<pubDate>Wed, 30 Sep 2026 18:21:47 GMT</pubDate><source url="https://tradingview.com">TradingView</source></item>
</channel></rss>"""


def article(title, when, url=None):
    return {"title": title, "summary": "", "url": url or f"https://x/{title}", "source": "google_news_rss",
            "event_at": pd.Timestamp(when, tz="UTC"), "content_hash": jev.content_hash(title)}


def test_google_news_strips_publisher_and_keeps_archive_hash(monkeypatch):
    monkeypatch.setattr(jev, "_get", lambda *args, **kwargs: type("R", (), {"content": RSS})())
    [item] = jev.fetch_google_news("coffee", date(2026, 9, 28), date(2026, 10, 2))
    assert item["title"] == "Coffee Prices Rise on Brazil Weather Risks"
    assert item["event_at"] == pd.Timestamp("2026-09-30 18:21:47", tz="UTC")
    assert item["content_hash"] == jev.content_hash("Coffee Prices Rise on Brazil Weather Risks", "")


def test_select_daily_keeps_best_new_articles_of_finished_new_york_days():
    articles = [
        article("Coffee futures jump as Brazil frost hits harvest", "2026-09-29 15:00"),
        article("Coffee prices rise", "2026-09-29 16:00"),
        article("Arabica supply tight", "2026-09-29 17:00"),
        article("Starbucks shares fall on coffee prices", "2026-09-29 18:00"),  # 주식 기사
        article("Coffee exports climb", "2026-09-30 03:00"),  # 뉴욕 9/29 23:00 → 9/29
        article("Coffee crop outlook", "2026-09-30 15:00"),  # 이미 채운 날짜
        article("Coffee harvest delayed", "2026-10-02 15:00"),  # 오늘(뉴욕) → 아직 끝나지 않음
        article("Already analyzed coffee price", "2026-09-28 15:00"),
    ]
    chosen = jev.select_daily(articles, today=date(2026, 10, 2), per_day=2,
                              known_hashes={jev.content_hash("Already analyzed coffee price")},
                              covered_dates={date(2026, 9, 30)})
    assert [item["title"] for item in chosen] == ["Coffee futures jump as Brazil frost hits harvest",
                                                  "Coffee prices rise"]


class FakeSession:
    def __init__(self, status, payload):
        self.status, self.payload, self.sent = status, payload, None

    def post(self, url, headers, json, timeout):
        self.sent = json
        return type("R", (), {"status_code": self.status, "json": lambda _: self.payload})()


def answer(choice="bullish", bull=0.7, relevance=0.8):
    return {"choice": choice, "confidence": 0.9,
            "probabilities": {"bullish": bull, "bearish": 0.1, "neutral": 0.1, "uncertain": 0.9 - bull - 0.1}}, {"noul": relevance}


def test_classify_parses_batch_and_puts_cost_on_first_row():
    (p0, r0), (p1, r1) = answer(), answer("uncertain", bull=0.1)
    payload = {"answers": {"article_0_price_pressure": p0, "article_0_relevance": r0,
                           "article_1_price_pressure": p1, "article_1_relevance": r1},
               "provider_metadata": {"gateway": {"cost": "0.0003"}}}
    session = FakeSession(200, payload)
    items = [article("Coffee prices rise", "2026-09-29"), article("Coffee crop", "2026-09-29")]
    results = jev.classify(items, api_key="test-key", session=session)
    assert len(session.sent["questions"]) == 4 and session.sent["model"] == "typesafe-ai/jev"
    assert [r["label"] for r in results] == ["bullish", "uncertain"]
    assert [r["cost"] for r in results] == [0.0003, 0.0]
    assert results[0]["prompt_version"] == "arabica-kc-futures-v2" and results[0]["available_at"] == results[0]["analyzed_at"]


@pytest.mark.parametrize("status, payload, message", [
    (429, {}, "HTTP 429"),
    (200, {"answers": {}}, "형식"),
    (200, {"answers": {"article_0_price_pressure": answer(bull=0.95)[0], "article_0_relevance": {"noul": 0.5}}}, "형식"),
])
def test_classify_rejects_failures_without_leaking_the_key(status, payload, message):
    with pytest.raises(jev.JevError, match=message) as error:
        jev.classify([article("Coffee prices rise", "2026-09-29")], api_key="secret-key", session=FakeSession(status, payload))
    assert "secret-key" not in str(error.value)
