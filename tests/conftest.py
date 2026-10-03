"""외부 API 없이 쓰는 합성 데이터."""
import numpy as np
import pandas as pd
import pytest

from coffee.config import REGIONS
from coffee.features import trading_sessions


def make_sources(start="2014-07-01", end="2016-12-31", seed=0) -> dict:
    rng = np.random.default_rng(seed)
    sessions = trading_sessions(start, end)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.02, len(sessions))))
    sources = {"prices": pd.DataFrame({"date": sessions, "open": close, "high": close * 1.01,
                                       "low": close * 0.99, "close": close, "volume": 1000})}
    sources["fx_cop"] = pd.DataFrame({"date": sessions, "close": 3000 * np.exp(np.cumsum(rng.normal(0, 0.01, len(sessions))))})
    days = pd.date_range(start, end, freq="D")
    for name in ("brl", "rate", "oil"):
        sources[f"macro_{name}"] = pd.DataFrame({
            "date": days, "release_date": days + pd.Timedelta(days=1),
            "value": 5 + np.cumsum(rng.normal(0, 0.01, len(days)))})
    months = pd.date_range("2013-01-01", end, freq="MS")
    sources["macro_freight"] = pd.DataFrame({"date": months, "release_date": months + pd.Timedelta(days=45),
                                             "value": 100 + np.cumsum(rng.normal(0, 1, len(months)))})
    sources["enso"] = pd.DataFrame({"date": months, "release_date": months + pd.DateOffset(months=2),
                                    "value": rng.normal(0, 1, len(months))})
    weather_days = pd.date_range("2003-01-01", end, freq="D")  # 직전 10년 평년값이 2014년 전에 차도록
    for region in REGIONS:
        n = len(weather_days)
        sources[f"weather_{region['id']}"] = pd.DataFrame({
            "date": weather_days, "PRECTOTCORR": rng.gamma(1.0, 3.0, n), "T2M": 20 + rng.normal(0, 2, n),
            "T2M_MIN": 10 + rng.normal(0, 3, n), "T2M_MAX": 28 + rng.normal(0, 3, n)})
    return sources


@pytest.fixture
def sources():
    return make_sources()
