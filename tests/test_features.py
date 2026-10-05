import numpy as np
import pandas as pd

from coffee.evaluate import fold_rows, usable_rows
from coffee.features import (ALL_FEATURES, EXTRA_FEATURES, add_news, build_dataset, clean_sources, daily_climatology,
                             future_session, replace_spikes, trading_sessions, trailing_climatology, weather_rolling)


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
    columns = ALL_FEATURES + EXTRA_FEATURES
    pd.testing.assert_frame_equal(before.loc[:cutoff, columns], after.loc[:cutoff, columns])


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
    data = build_dataset(sources)
    cold = data["br_cerrado_cold_anom"]
    assert cold.loc["2016-05-05"] > -20 and cold.loc["2016-05-06"] < -30  # 관측 + 4일
    z = data["br_tmin7_z"]  # 브라질 요약(가장 추운 산지)도 같은 날 반영된다
    assert z.loc["2016-05-05"] > -5 and z.loc["2016-05-06"] < -5


def test_climatology_uses_only_previous_years():
    days = pd.date_range("2000-01-01", "2016-12-31")
    daily = pd.DataFrame({"PRECTOTCORR": 1.0, "T2M": 20.0, "T2M_MIN": 10.0, "T2M_MAX": 25.0}, index=days)
    daily.loc["2015-01-01":"2015-12-31", "T2M"] = 30.0  # 2015년만 매우 더움
    normal = trailing_climatology(weather_rolling(daily))
    assert normal.loc["2015-07-15", "temp30"] == 20.0  # 그해 자료는 쓰지 않는다
    assert np.isclose(normal.loc["2016-07-15", "temp30"], 21.0)  # 다음 해부터 10년 평균에 포함


def test_daily_climatology_uses_only_previous_years():
    days = pd.date_range("2000-01-01", "2016-12-31")
    rolled = pd.DataFrame({"temp30": 20.0}, index=days)
    rolled.loc["2015", "temp30"] = 30.0  # 2015년만 매우 더움
    mean, std = daily_climatology(rolled)
    assert mean.loc["2015-07-15", "temp30"] == 20.0 and std.loc["2015-07-15", "temp30"] == 0.0
    assert np.isclose(mean.loc["2016-07-15", "temp30"], 21.0)
    changed = rolled.copy()
    changed.loc["2016", "temp30"] = -50.0  # 그해 값이 바뀌어도 그해 평년값은 그대로
    pd.testing.assert_frame_equal(daily_climatology(changed)[0], mean)


def test_daily_climatology_moves_smoothly_across_month_ends():
    days = pd.date_range("2000-01-01", "2016-12-31")
    rolled = pd.DataFrame({"temp30": 20 + 5 * np.sin(2 * np.pi * days.dayofyear / 365), "rain90": 100.0}, index=days)
    daily_step = daily_climatology(rolled)[0].loc["2015", "temp30"].diff().abs().max()
    monthly_step = trailing_climatology(rolled).loc["2015", "temp30"].diff().abs().max()
    assert daily_step < 0.1 and monthly_step > 1  # 같은 달 평년값은 달이 바뀔 때 한 번에 뛴다


def test_climate_summary_counts_cold_days_and_keeps_frost_feature_in_winter(sources):
    for region in ("br_sul_minas", "br_cerrado", "br_alta_mogiana"):
        weather = sources[f"weather_{region}"]
        weather.loc[weather["date"].between("2016-07-01", "2016-07-05"), "T2M_MIN"] = -30.0  # 서리철 한파
        weather.loc[weather["date"].between("2016-11-01", "2016-11-05"), "T2M_MIN"] = -30.0  # 서리철 밖 한파
    data = build_dataset(sources)
    july, november = data.loc["2016-07-11"], data.loc["2016-11-10"]  # 관측 + 4일 뒤의 거래일
    assert july["br_tmin7_z"] < -5 and july["br_tmin7_z_frost"] == july["br_tmin7_z"]
    assert july["br_cold_days30"] >= 3
    assert november["br_tmin7_z"] < -5 and november["br_tmin7_z_frost"] == 0.0


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


def test_news_score_is_zero_without_articles_and_waits_until_it_is_known(sources):
    data = build_dataset(sources)
    assert (add_news(data, None, "live")["news_score"] == 0).all()  # 수집 전 기간은 중립
    article = pd.DataFrame({"event_at": [pd.Timestamp("2016-03-01 12:00", tz="UTC")],      # 화요일 발행
                            "available_at": [pd.Timestamp("2016-03-03 01:00", tz="UTC")],  # 목요일 새벽 분석 완료
                            "p_bullish": [0.9], "p_bearish": [0.1], "relevance": [1.0]})
    live = add_news(data, article, "live")["news_score"]
    assert live.loc["2016-03-02"] == 0 and live.loc["2016-03-03"] > 0  # 분석 뒤 첫 마감(23:00 UTC)부터
    assert (live.drop(pd.Timestamp("2016-03-03")) == 0).all()          # 기사가 없는 날은 0
    research = add_news(data, article, "research")["news_score"]
    assert research.loc["2016-03-01"] == 0 and research.loc["2016-03-02"] > 0  # 소급 분류: 발행 + 1일


def test_one_day_bad_quote_is_replaced_by_the_previous_value_without_looking_ahead():
    rng = np.random.default_rng(0)
    quotes = pd.Series(2000 * np.exp(np.cumsum(rng.normal(0, 0.005, 400))))
    quotes[300] = 3.67                  # 다른 통화 시세가 섞인 하루(2013-07-12 페소 같은 오류)
    quotes[350] = quotes[349] * 1.04    # 큰 실제 움직임(4%)
    cleaned = replace_spikes(quotes, log=True)
    assert cleaned[300] == quotes[299]  # 직전 값
    assert cleaned.drop(300).equals(quotes.drop(300))  # 되돌아온 날과 실제 움직임은 그대로
    assert replace_spikes(quotes.iloc[:301], log=True).equals(cleaned.iloc[:301])  # 그날까지의 값만 보고 판단한다
    assert replace_spikes(quotes.iloc[:200], log=True).equals(quotes.iloc[:200])  # 변화 250개 전에는 울타리가 없다


def test_clean_sources_leaves_coffee_price_rate_and_rain_alone(sources):
    sources["fx_cop"].loc[400, "close"] = 3.67
    sources["macro_rate"].loc[500, "value"] += 5.0       # 금리: 계단형이라 대상이 아니다
    sources["prices"].loc[300, "close"] *= 1.5           # 커피 가격: 예측 대상이라 고치지 않는다
    sources["weather_br_cerrado"].loc[5000, "PRECTOTCORR"] = -1.0
    cleaned = clean_sources(sources)
    assert cleaned["fx_cop"].loc[400, "close"] == sources["fx_cop"].loc[399, "close"]
    assert cleaned["macro_rate"].equals(sources["macro_rate"]) and cleaned["prices"].equals(sources["prices"])
    rain = cleaned["weather_br_cerrado"]["PRECTOTCORR"]
    assert rain.loc[5000] == rain.loc[4999] and (rain >= 0).all()  # 음수 강수만 직전 값
