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
    days = pd.date_range(start, end, freq="D")
    for name in ("brl", "rate", "oil"):
        sources[f"macro_{name}"] = pd.DataFrame({
            "date": days, "release_date": days + pd.Timedelta(days=1),
            "value": 5 + np.cumsum(rng.normal(0, 0.01, len(days)))})
    weather_days = pd.date_range("2014-03-01", end, freq="D")  # 90일 창이 첫 거래일 전에 차도록
    for region in REGIONS:
        n = len(weather_days)
        sources[f"weather_{region['id']}"] = pd.DataFrame({
            "date": weather_days, "PRECTOTCORR": rng.gamma(1.0, 3.0, n),
            "T2M": 20 + rng.normal(0, 2, n), "T2M_MIN": 10 + rng.normal(0, 5, n)})
    return sources


def make_normals() -> pd.DataFrame:
    return pd.DataFrame([{"region": region["id"], "month": month, "rain30": 90.0, "temp30": 20.0, "rain90": 270.0}
                         for region in REGIONS for month in range(1, 13)])


@pytest.fixture
def sources():
    return make_sources()


@pytest.fixture
def normals():
    return make_normals()
