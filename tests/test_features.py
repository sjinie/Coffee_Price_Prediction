import numpy as np
import pandas as pd

from coffee.evaluate import fold_rows, usable_rows
from coffee.features import (ALL_FEATURES, build_dataset, future_session, trading_sessions,
                             trailing_climatology, weather_rolling)


def test_trading_sessions_add_coffee_only_day_and_skip_weekends():
    sessions = trading_sessions("2018-12-01", "2018-12-10")
    assert pd.Timestamp("2018-12-05") in sessions  # NYSE 휴장, 커피 선물은 거래
    assert not any(day.weekday() >= 5 for day in sessions)
    assert future_session("2018-12-04", 1) == pd.Timestamp("2018-12-05")


def test_future_data_never_changes_past_features(sources):
    """기준일 이후 원천 데이터를 모두 바꿔도 기준일까지의 피처는 그대로여야 한다."""
    cutoff = pd.Timestamp("2016-06-30")
    before = build_dataset(sources)
    changed = {name: frame.copy() for name, frame in sources.items()}
    for frame in changed.values():
        later = frame["date"] > cutoff
        numeric = frame.select_dtypes("number").columns
        frame.loc[later, numeric] = frame.loc[later, numeric] * 1.7 + 3
    after = build_dataset(changed)
    pd.testing.assert_frame_equal(before.loc[:cutoff, ALL_FEATURES], after.loc[:cutoff, ALL_FEATURES])


def test_macro_value_is_used_only_after_release(sources):
    macro = sources["macro_rate"]
    macro["release_date"] = macro["date"] + pd.Timedelta(days=10)  # 열흘 뒤에 공개되는 지표
    macro.loc[macro["date"] >= "2016-03-01", "value"] = 999.0
    data = build_dataset(sources)
    jump = data["rate_chg_20"].abs() > 100
    # 3/1 관측은 3/11 공개 → 3/12(토)부터 이용 가능 → 첫 거래일은 3/14(월)
    assert data.index[jump].min() == pd.Timestamp("2016-03-14")


def test_weather_change_appears_four_days_later(sources):
    weather = sources["weather_br_cerrado"]
    weather.loc[weather["date"] >= "2016-05-02", "T2M_MIN"] = -30.0  # 갑작스러운 한파
    cold = build_dataset(sources)["br_cerrado_cold_anom"]
    assert cold.loc["2016-05-05"] > -20 and cold.loc["2016-05-06"] < -30  # 관측 + 4일


def test_climatology_uses_only_previous_years():
    days = pd.date_range("2000-01-01", "2016-12-31")
    daily = pd.DataFrame({"PRECTOTCORR": 1.0, "T2M": 20.0, "T2M_MIN": 10.0, "T2M_MAX": 25.0}, index=days)
    daily.loc["2015-01-01":"2015-12-31", "T2M"] = 30.0  # 2015년만 매우 더움
    normal = trailing_climatology(weather_rolling(daily))
    assert normal.loc["2015-07-15", "temp30"] == 20.0  # 그해 자료는 쓰지 않는다
    assert np.isclose(normal.loc["2016-07-15", "temp30"], 21.0)  # 다음 해부터 10년 평균에 포함


def test_targets_use_actual_prices_and_skip_missing(sources):
    sources["prices"].loc[sources["prices"]["date"] == "2016-03-10", "close"] = np.nan
    data = build_dataset(sources)
    assert np.isnan(data.loc["2016-03-10", "y_5"])
    origin = data.index.get_loc(pd.Timestamp("2016-03-01"))
    expected = np.log(data["close"].iloc[origin + 5] / data["close"].iloc[origin])
    assert np.isclose(data["y_5"].iloc[origin], expected)
    assert data["target_date_5"].iloc[origin] == data.index[origin + 5]
    assert not np.isnan(data.loc["2016-03-11", "ret_1"])  # 피처는 마지막으로 알려진 종가로 계산
    daily = np.log(data["close"]).diff()
    assert np.isclose(data["v_20"].iloc[origin], np.log(daily.iloc[origin + 1: origin + 21].std()))


def test_training_rows_exclude_targets_beyond_fit_end(sources):
    data = build_dataset(sources)
    rows = usable_rows(data, ALL_FEATURES, 20, "2015-01-01", "2015-12-31", fit_end="2015-12-31")
    assert (data["target_date_20"].iloc[rows] <= pd.Timestamp("2015-12-31")).all()
    fit, test = fold_rows(data, ALL_FEATURES, 20, 2016, 2016)
    assert data["target_date_20"].iloc[fit].max() <= pd.Timestamp("2015-12-31")
    assert data.index[test].min().year == 2016 and data["target_date_20"].iloc[test].max().year == 2016


def test_evaluation_rows_keep_year_end_origins_until_the_segment_ends(sources):
    data = build_dataset(sources)
    _, cut_at_year = fold_rows(data, ALL_FEATURES, 20, 2015, 2015)
    _, segment = fold_rows(data, ALL_FEATURES, 20, 2015, 2016)
    targets = data["target_date_20"].iloc[segment]
    assert data["target_date_20"].iloc[cut_at_year].max().year == 2015
    assert data.index[segment].max().year == 2015  # 기준일은 그해 안
    late = data.index[segment][(targets.dt.year == 2016).to_numpy()]
    assert len(late) > 0 and late.min().month == 12  # 목표일이 다음 해인 연말 기준일도 채점한다
    assert len(segment) == len(cut_at_year) + len(late)
