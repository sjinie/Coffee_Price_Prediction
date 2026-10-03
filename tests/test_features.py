import numpy as np
import pandas as pd

from coffee.evaluate import usable_rows
from coffee.features import ALL_FEATURES, build_dataset, future_session, trading_sessions


def test_trading_sessions_add_coffee_only_day_and_skip_weekends():
    sessions = trading_sessions("2018-12-01", "2018-12-10")
    assert pd.Timestamp("2018-12-05") in sessions  # NYSE 휴장, 커피 선물은 거래
    assert not any(day.weekday() >= 5 for day in sessions)
    assert future_session("2018-12-04", 1) == pd.Timestamp("2018-12-05")


def test_future_data_never_changes_past_features(sources, normals):
    """기준일 이후 원천 데이터를 모두 바꿔도 기준일까지의 피처는 그대로여야 한다."""
    cutoff = pd.Timestamp("2016-06-30")
    before = build_dataset(sources, normals)
    changed = {name: frame.copy() for name, frame in sources.items()}
    for frame in changed.values():
        later = frame["date"] > cutoff
        numeric = frame.select_dtypes("number").columns
        frame.loc[later, numeric] = frame.loc[later, numeric] * 1.7 + 3
    after = build_dataset(changed, normals)
    pd.testing.assert_frame_equal(before.loc[:cutoff, ALL_FEATURES], after.loc[:cutoff, ALL_FEATURES])


def test_macro_value_is_used_only_after_release(sources, normals):
    macro = sources["macro_rate"]
    macro["release_date"] = macro["date"] + pd.Timedelta(days=10)  # 열흘 뒤에 공개되는 지표
    macro.loc[macro["date"] >= "2016-03-01", "value"] = 999.0
    data = build_dataset(sources, normals)
    jump = data["rate_chg_20"].abs() > 100
    # 3/1 관측은 3/11 공개 → 3/12(토)부터 이용 가능 → 첫 거래일은 3/14(월)
    assert data.index[jump].min() == pd.Timestamp("2016-03-14")


def test_weather_change_appears_four_days_later(sources, normals):
    weather = sources["weather_br_cerrado"]
    weather.loc[weather["date"] >= "2016-05-02", "T2M_MIN"] = -10.0  # 갑작스러운 한파
    data = build_dataset(sources, normals)
    frost = data["br_cerrado_frost"]
    assert frost.loc["2016-05-05"] < 50 and frost.loc["2016-05-06"] >= 10  # 관측 + 4일


def test_targets_use_actual_prices_and_skip_missing(sources, normals):
    sources["prices"].loc[sources["prices"]["date"] == "2016-03-10", "close"] = np.nan
    data = build_dataset(sources, normals)
    assert np.isnan(data.loc["2016-03-10", "y_5"])
    origin = data.index.get_loc(pd.Timestamp("2016-03-01"))
    expected = np.log(data["close"].iloc[origin + 5] / data["close"].iloc[origin])
    assert np.isclose(data["y_5"].iloc[origin], expected)
    assert data["target_date_5"].iloc[origin] == data.index[origin + 5]
    assert not np.isnan(data.loc["2016-03-11", "ret_1"])  # 피처는 마지막으로 알려진 종가로 계산


def test_training_rows_exclude_targets_beyond_fit_end(sources, normals):
    data = build_dataset(sources, normals)
    rows = usable_rows(data, ALL_FEATURES, 20, "2015-01-01", "2015-12-31", fit_end="2015-12-31")
    assert (data["target_date_20"].iloc[rows] <= pd.Timestamp("2015-12-31")).all()
    assert data.index[rows].max() < pd.Timestamp("2015-12-31") - pd.Timedelta(days=20)
