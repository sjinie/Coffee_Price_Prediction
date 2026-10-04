from datetime import date

import numpy as np
import pandas as pd
import pytest
import requests

from coffee import sources


class Fake:
    def __init__(self, payload=None, text=""):
        self.payload, self.text = payload, text

    def json(self):
        return self.payload


def test_update_refetches_from_last_value_not_last_row(tmp_path):
    """끝에 값이 빈 행(NASA의 최근 결측)이 있어도 마지막 '값'이 있는 날부터 다시 받는다."""
    path = tmp_path / "weather.parquet"
    pd.DataFrame({"date": pd.date_range("2026-09-01", "2026-09-20"),
                  "T2M": [20.0] * 10 + [np.nan] * 10}).to_parquet(path)
    calls = []

    def fetch(start, end):
        calls.append(start)
        return pd.DataFrame({"date": pd.date_range(start, end), "T2M": 21.0})

    merged = sources.update_parquet(path, fetch, date(2014, 7, 1), date(2026, 9, 22), ["T2M"])
    assert calls == [date(2026, 9, 3)]  # 마지막 값 9/10 - 7일
    assert merged["T2M"].notna().all() and merged["date"].is_unique


def test_macro_uses_alfred_release_and_assumes_lag_before_first_vintage(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "secret-key")

    def fake_get(url, params=None, **kwargs):
        if url.endswith("vintagedates"):
            return Fake({"vintage_dates": ["2014-03-18"]})
        if params.get("output_type") == 4:  # ALFRED 최초 공개값
            return Fake({"observations": [{"date": "2014-03-20", "realtime_start": "2014-03-24", "value": "2.4"}]})
        return Fake({"observations": [{"date": "2014-03-10", "realtime_start": "2026-01-01", "value": "2.3"},
                                      {"date": "2014-03-11", "realtime_start": "2026-01-01", "value": "."}]})

    monkeypatch.setattr(sources, "_get", fake_get)
    frame = sources.fetch_macro("DEXBZUS", 7, date(2014, 3, 10), date(2014, 3, 25)).set_index("date")
    assert frame.loc["2014-03-10", "release_date"] == pd.Timestamp("2014-03-17")  # 관측 + 가정 7일
    assert frame.loc["2014-03-10", "release_assumed"] and np.isnan(frame.loc["2014-03-11", "value"])
    assert frame.loc["2014-03-20", "release_date"] == pd.Timestamp("2014-03-24")  # 실제 공개일
    assert not frame.loc["2014-03-20", "release_assumed"]


def test_request_error_keeps_only_status(monkeypatch):
    class Forbidden:
        status_code = 403

    def fail(*args, **kwargs):
        raise requests.HTTPError("https://api.example/?api_key=secret-key", response=Forbidden())

    monkeypatch.setattr(sources.requests, "get", fail)
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
    with pytest.raises(sources.SourceError) as error:
        sources._get("https://api.example", {"api_key": "secret-key"})
    assert str(error.value) == "HTTP 403"


def test_enso_center_month_and_release(monkeypatch):
    text = " SEAS  YR   TOTAL   ANOM\n  JJA 2026  29.09   1.80\n  NDJ 2025  26.10  -0.50\n"
    monkeypatch.setattr(sources, "_get", lambda *args, **kwargs: Fake(text=text))
    frame = sources.fetch_enso(date(2025, 1, 1), date(2026, 12, 31)).set_index("date")
    assert frame.loc["2026-07-01", "value"] == 1.80  # 6·7·8월의 가운데 달
    assert frame.loc["2026-07-01", "release_date"] == pd.Timestamp("2026-09-11")  # 8월 말 + 10일
    assert frame.loc["2025-12-01", "value"] == -0.50


def test_weather_fill_value_becomes_missing(monkeypatch):
    payload = {"header": {"fill_value": -999.0},
               "properties": {"parameter": {name: {"20260101": 1.5, "20260102": -999.0}
                                            for name in sources.WEATHER_VARS}}}
    monkeypatch.setattr(sources, "_get", lambda *args, **kwargs: Fake(payload))
    frame = sources.fetch_weather({"lat": 0, "lon": 0}, date(2026, 1, 1), date(2026, 1, 2))
    assert frame["PRECTOTCORR"].isna().tolist() == [False, True]
