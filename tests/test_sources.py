from datetime import date

import numpy as np
import pandas as pd
import pytest
import requests

from coffee import sources


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


def test_alfred_parses_missing_dot_and_error_hides_key(monkeypatch):
    monkeypatch.setenv("FRED_API_KEY", "secret-key")
    real_get = sources._get

    class Response:
        def json(self):
            return {"observations": [{"date": "2026-01-02", "realtime_start": "2026-01-05", "value": "5.1"},
                                     {"date": "2026-01-05", "realtime_start": "2026-01-06", "value": "."}]}

    monkeypatch.setattr(sources, "_get", lambda *args, **kwargs: Response())
    frame = sources.fetch_alfred("DEXBZUS", date(2026, 1, 1), date(2026, 1, 10))
    assert frame["value"].iloc[0] == 5.1 and np.isnan(frame["value"].iloc[1])
    assert frame["release_date"].iloc[0] == pd.Timestamp("2026-01-05")

    class Forbidden:
        status_code = 403

    def fail(*args, **kwargs):
        raise requests.HTTPError("https://api.example/?api_key=secret-key", response=Forbidden())

    monkeypatch.setattr(sources.requests, "get", fail)
    monkeypatch.setattr(sources.time, "sleep", lambda seconds: None)
    with pytest.raises(sources.SourceError) as error:
        real_get("https://api.example", {"api_key": "secret-key"})
    assert str(error.value) == "HTTP 403" and "secret" not in str(error.value)


def test_weather_fill_value_becomes_missing(monkeypatch):
    class Response:
        def json(self):
            return {"header": {"fill_value": -999.0},
                    "properties": {"parameter": {"PRECTOTCORR": {"20260101": 1.5, "20260102": -999.0},
                                                 "T2M": {"20260101": 20.0, "20260102": -999.0},
                                                 "T2M_MIN": {"20260101": 15.0, "20260102": 14.0}}}}

    monkeypatch.setattr(sources, "_get", lambda *args, **kwargs: Response())
    frame = sources.fetch_weather({"lat": 0, "lon": 0}, date(2026, 1, 1), date(2026, 1, 2))
    assert frame["PRECTOTCORR"].isna().tolist() == [False, True]
    assert frame["T2M_MIN"].tolist() == [15.0, 14.0]
