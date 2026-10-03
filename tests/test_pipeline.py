import numpy as np
import pandas as pd
import pytest

from coffee.config import HORIZONS, VOL_HORIZONS
from coffee.evaluate import usable_rows
from coffee.features import FEATURE_GROUPS, build_dataset
from coffee.models import LightGBMClassifier, RidgeModel, ShrunkProbability
from coffee.pipeline import RISK_WINDOW, _rolling_percentile, make_forecasts

HAR = ["log_vol_5", "log_vol_20", "log_vol_60"]


@pytest.fixture
def small_models(sources):
    data = build_dataset(sources)
    price = FEATURE_GROUPS["price"]
    direction, volatility = {}, {}
    for h in HORIZONS:
        fit = usable_rows(data, price, h, "2015-01-01", "2015-12-31", fit_end="2015-12-31")
        direction[h] = ShrunkProbability(LightGBMClassifier(price, n_estimators=10), 0.25).fit(
            data, fit, data[f"y_{h}"].to_numpy()[fit])
    for h in VOL_HORIZONS:
        fit = usable_rows(data, price, h, "2015-01-01", "2015-12-31", fit_end="2015-12-31", target=f"v_{h}")
        volatility[h] = RidgeModel(HAR, alpha=1).fit(data, fit, data[f"v_{h}"].to_numpy()[fit])
    models = {"version": "test", "direction": direction, "volatility": volatility,
              "direction_meta": {"train_end": "2015-12-31",
                                 "horizons": {str(h): {"features": price, "threshold": 0.52} for h in HORIZONS}},
              "volatility_meta": {"horizons": {str(h): {"features": HAR, "interval_multiplier": 1.0}
                                               for h in VOL_HORIZONS}}}
    return data, models


def test_forecast_rows_follow_the_serving_contract(small_models):
    data, models = small_models
    origins = np.arange(len(data) - 3, len(data))  # 마지막 기준일의 목표일은 자료 밖의 미래 거래일
    rows = pd.DataFrame(make_forecasts(data, origins, models, "live"))
    assert len(rows) == len(origins) * len(HORIZONS)
    assert rows["prob_up"].between(0, 1).all() and rows["signal"].isin(["buy", "wait", "hold"]).all()
    assert (rows["target_date"] > rows["origin_date"]).all()
    five, ranged = rows[rows["horizon"] == 5], rows[rows["horizon"] != 5]
    assert five[["price_low", "price_high", "predicted_vol", "vol_percentile"]].isna().all().all()
    assert ((ranged["price_low"] < ranged["origin_close"]) & (ranged["origin_close"] < ranged["price_high"])).all()
    assert ranged["vol_percentile"].between(0, 1).all()
    last = rows[(rows["horizon"] == 60) & (rows["origin_date"] == data.index[-1].date())]
    assert last["target_date"].iloc[0] > data.index[-1].date()


def test_rolling_percentile_ranks_against_recent_three_years():
    rising = _rolling_percentile(pd.Series(np.arange(1000, dtype=float)))
    falling = _rolling_percentile(pd.Series(np.arange(1000, 0, -1, dtype=float)))
    assert np.isnan(rising.iloc[RISK_WINDOW // 3 - 2]) and rising.iloc[-1] == 1.0
    assert falling.iloc[-1] == pytest.approx(1 / RISK_WINDOW)
