from datetime import date, datetime, timezone

import pandas as pd
import pytest

from coffee_service import news


def record(url="https://example.test/a?utm_source=x", title="Coffee drought cuts supply", published="2024-01-02T12:00:00+00:00", modified="2024-01-02T12:00:00+00:00"):
    return {"url": url, "title": title, "published_at": published, "modified_at": modified, "source": "fixture"}


def test_normalization_deduplicates_tracking_and_cross_source_title():
    frame = news.normalize_articles([record(), record("https://other.test/story", "Coffee drought cuts supply")], datetime(2024, 1, 3, tzinfo=timezone.utc))
    assert len(frame) == 1
    assert frame.url.iloc[0] == "https://example.test/a"
    assert frame.title.iloc[0] == "Coffee drought cuts supply"
    assert str(frame.published_at.dtype) == "datetime64[ns, UTC]"


def test_encoded_literal_angle_brackets_survive_read_and_incremental_merge(tmp_path, monkeypatch):
    path = tmp_path / "news.parquet"
    raw = [record(title="Coffee &amp; &lt;title&gt; drought")]
    original = news.normalize_articles(raw, datetime(2024, 1, 3, tzinfo=timezone.utc))
    original.to_parquet(path, index=False)
    assert news.read_news(path).title.tolist() == ["Coffee & <title> drought"]
    monkeypatch.setattr(news, "fetch_wordpress", lambda *_args, **_kwargs: original)
    merged = news.ingest_news(path, "2024-01-01", "2024-01-03", "incremental")
    assert merged.title.tolist() == ["Coffee & <title> drought"]


def test_normalization_rejects_naive_timestamp():
    with pytest.raises(ValueError, match="timezone"):
        news.normalize_articles([record(published="2024-01-02T12:00:00")])


def test_analyzer_handles_supply_demand_and_negation():
    assert news.analyze_title("Coffee drought cuts supply")["price_impact"] == "bullish"
    assert news.analyze_title("Coffee bumper crop increases supply")["price_impact"] == "bearish"
    assert news.analyze_title("Coffee supply is not reduced")["price_impact"] == "neutral"
    assert news.analyze_title("Coffee supply is not materially reduced")["price_impact"] == "neutral"
    assert news.analyze_title("Coffee demand increases")["price_impact"] == "bullish"
    assert news.analyze_title("Brazil coffee production reaches record high")["price_impact"] == "bearish"
    assert news.analyze_title("Record coffee crop in Brazil")["price_impact"] == "bearish"
    assert news.analyze_title("Record coffee prices as drought cuts production")["price_impact"] == "bullish"
    assert news.analyze_title("No coffee shortage")["price_impact"] == "neutral"
    assert news.analyze_title("Coffee demand falls while stocks rise")["price_impact"] == "neutral"
    assert news.analyze_title("Coffee exports surge")["price_impact"] == "bearish"
    assert news.analyze_title("Coffee drought relief improves production")["price_impact"] == "bearish"


def test_wordpress_fetch_uses_metadata_fields_and_requires_all_pages(monkeypatch):
    calls = []

    class Response:
        headers = {"X-WP-TotalPages": "2", "X-WP-Total": "2"}

        def __init__(self, page):
            self.page = page

        def raise_for_status(self):
            return None

        def json(self):
            return [{"id": self.page, "link": f"https://example.test/{self.page}", "title": {"rendered": f"Coffee drought cuts supply {self.page}"}, "date_gmt": "2024-01-02T12:00:00", "modified_gmt": "2024-01-02T12:00:00"}]

    class Session:
        def get(self, _endpoint, params, timeout):
            calls.append((params, timeout))
            return Response(params["page"])

    monkeypatch.setattr(news.time, "sleep", lambda *_: None)
    frame = news.fetch_wordpress(Session(), "2024-01-01", "2024-01-03")
    assert len(frame) == 2
    assert all(call[0]["_fields"] == "id,date_gmt,modified_gmt,link,title" for call in calls)
    assert all(call[0]["orderby"] == "date" and call[0]["order"] == "asc" for call in calls)
    assert [call[0]["page"] for call in calls] == [1, 2]


def test_aggregation_excludes_future_and_later_modified_article():
    articles = news.normalize_articles([
        record(modified="2024-01-04T00:00:00+00:00"),
        record("https://example.test/future", "Coffee demand grows", "2024-01-03T12:00:00+00:00", "2024-01-03T12:00:00+00:00"),
    ])
    values = news.aggregate_news(articles, pd.to_datetime(["2024-01-02", "2024-01-04"]), [3])
    assert values.loc[pd.Timestamp("2024-01-02"), "news_count_3d"] == 0
    assert values.loc[pd.Timestamp("2024-01-04"), "news_count_3d"] == 2


def test_old_article_revised_today_is_visible_but_not_a_fresh_event():
    articles = news.normalize_articles([
        record("https://example.test/old", "Coffee drought cuts supply", "2015-01-01T12:00:00+00:00", "2024-01-04T00:00:00+00:00"),
    ])
    values = news.aggregate_news(articles, pd.to_datetime(["2024-01-04"]), [3])
    assert values.loc[pd.Timestamp("2024-01-04"), "news_count_3d"] == 0


def test_daily_aggregation_decay_and_empty_windows_are_deterministic():
    articles = news.normalize_articles([record()])
    values = news.aggregate_news(articles, pd.to_datetime(["2024-01-02", "2024-02-01"]), [1, 3])
    assert values.loc[pd.Timestamp("2024-01-02"), "news_bullish_ratio_1d"] == 1
    assert 0 < values.loc[pd.Timestamp("2024-01-02"), "news_time_decay_1d"] < 1
    assert values.loc[pd.Timestamp("2024-02-01")].eq(0).all()
    assert "news_weather_impact_3d" in news.news_feature_columns(3)


def test_aggregation_uses_origin_collection_bound_and_confidence_weighting():
    articles = news.normalize_articles([
        {**record("https://example.test/a"), "collected_at": "2024-01-02T14:00:00+00:00"},
        {**record("https://example.test/b", "Coffee demand grows"), "published_at": "2024-01-02T13:00:00+00:00", "modified_at": "2024-01-02T13:00:00+00:00", "collected_at": "2024-01-03T00:00:00+00:00"},
    ])
    values = news.aggregate_news(articles, pd.to_datetime(["2024-01-02"]), [3], collected_bound="2024-01-03T00:00:00+00:00")
    assert values.loc[pd.Timestamp("2024-01-02"), "news_count_3d"] == 1
    assert values.loc[pd.Timestamp("2024-01-02"), "news_weighted_impact_3d"] == pytest.approx(0.8)


def test_validation_and_title_deduplication_are_strict():
    with pytest.raises(ValueError, match="http"):
        news.normalize_articles([record(url="ftp://example.test/a")])
    with pytest.raises(ValueError, match="empty"):
        news.normalize_articles([record(title="<b> </b>")])
    with pytest.raises(ValueError, match="collected"):
        news.normalize_articles([{**record(), "collected_at": "2024-01-01T00:00:00+00:00"}])
    frame = news.normalize_articles([record("https://example.test/a", "Coffee: drought!"), record("https://example.test/b", "coffee drought", "2024-01-03T11:00:00+00:00")])
    assert frame.url.tolist() == ["https://example.test/a"]


def test_ingest_is_atomic_when_fetch_fails(tmp_path, monkeypatch):
    path = tmp_path / "news.parquet"
    existing = news.normalize_articles([record()])
    existing.to_parquet(path, index=False)
    monkeypatch.setattr(news, "fetch_wordpress", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("fixture failure")))
    with pytest.raises(RuntimeError, match="fixture failure"):
        news.ingest_news(path, "2024-01-01", "2024-01-03", "incremental")
    assert news.read_news(path).article_id.tolist() == existing.article_id.tolist()


def test_successful_empty_ingest_round_trips_schema_and_utc_dtypes(tmp_path, monkeypatch):
    path = tmp_path / "empty-news.parquet"
    monkeypatch.setattr(news, "fetch_wordpress", lambda *_args, **_kwargs: news._empty_articles())

    saved = news.ingest_news(path, "2014-07-01", "2014-07-01")
    loaded = news.read_news(path)

    assert path.exists()
    assert list(saved.columns) == list(news.ARTICLE_COLUMNS)
    assert list(loaded.columns) == list(news.ARTICLE_COLUMNS)
    assert saved.empty and loaded.empty
    assert loaded.attrs["coverage_start"] == "2014-07-01"
    assert loaded.attrs["coverage_end"] == "2014-07-01"
    assert pd.Timestamp(loaded.attrs["last_collected_at"]).tzinfo is not None
    for column in ("published_at", "collected_at", "modified_at", "available_at"):
        assert str(saved[column].dtype) == "datetime64[ns, UTC]"
        assert str(loaded[column].dtype) == "datetime64[ns, UTC]"


def test_incremental_extends_overlapping_coverage_and_preserves_attrs(tmp_path, monkeypatch):
    path = tmp_path / "news.parquet"
    initial = news.normalize_articles([record(published="2024-01-10T12:00:00+00:00")])
    initial.attrs.update({"coverage_start": "2024-01-01", "coverage_end": "2024-01-10", "last_collected_at": "2024-01-11T00:00:00+00:00"})
    initial.to_parquet(path, index=False)
    calls = []

    def fetch(_session, start, end):
        calls.append((start, end))
        return news.normalize_articles([record("https://example.test/new", published="2024-01-12T12:00:00+00:00")])

    monkeypatch.setattr(news, "fetch_wordpress", fetch)
    saved = news.ingest_news(path, "2024-01-01", "2024-01-12", "incremental")
    loaded = news.read_news(path)

    assert calls == [(date(2024, 1, 3), date(2024, 1, 12))]
    assert saved.attrs["coverage_start"] == "2024-01-01"
    assert saved.attrs["coverage_end"] == "2024-01-12"
    assert loaded.attrs == saved.attrs


def test_empty_incremental_joins_adjacent_dates_but_not_a_gap(tmp_path, monkeypatch):
    path = tmp_path / "empty-news.parquet"
    monkeypatch.setattr(news, "fetch_wordpress", lambda *_args, **_kwargs: news._empty_articles())
    news.ingest_news(path, "2024-01-01", "2024-01-01")
    adjacent = news.ingest_news(path, "2024-01-02", "2024-01-02", "incremental")
    assert adjacent.attrs["coverage_end"] == "2024-01-02"
    separated = news.ingest_news(path, "2024-01-04", "2024-01-04", "incremental")
    assert separated.attrs["coverage_end"] == "2024-01-02"
    assert news.read_news(path).attrs == separated.attrs


def test_incremental_preserves_first_collected_historical_article(tmp_path, monkeypatch):
    path = tmp_path / "news.parquet"
    original = news.normalize_articles([record(title="Coffee drought cuts supply")], datetime(2024, 1, 3, tzinfo=timezone.utc))
    original.to_parquet(path, index=False)
    revised = news.normalize_articles([record(title="Coffee bumper crop increases supply", modified="2024-01-04T00:00:00+00:00")])
    monkeypatch.setattr(news, "fetch_wordpress", lambda *_args, **_kwargs: revised)
    saved = news.ingest_news(path, "2024-01-01", "2024-01-05", "incremental")
    assert saved.title.tolist() == ["Coffee drought cuts supply"]
    assert saved.modified_at.tolist() == original.modified_at.tolist()


def test_read_news_refreshes_only_derived_title_analysis(tmp_path):
    path = tmp_path / "news.parquet"
    original = news.normalize_articles([record("https://example.test/record", "Record coffee crop in Brazil")])
    raw_columns = ["article_id", "source", "title", "summary", "published_at", "collected_at", "url", "language", "modified_at", "available_at", "rights"]
    expected_raw = original.loc[:, raw_columns].copy()
    original.loc[:, ["coffee_relevance", "topic", "price_impact", "confidence", "analysis_version"]] = [0.0, "other", "neutral", 0.1, "old-rules"]
    original.to_parquet(path, index=False)

    refreshed = news.read_news(path)

    pd.testing.assert_frame_equal(refreshed.loc[:, raw_columns], expected_raw)
    assert refreshed.price_impact.tolist() == ["bearish"]
    assert refreshed.analysis_version.tolist() == [news.ANALYSIS_VERSION]
