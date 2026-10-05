"""거래일 축, 시점 결합, 피처와 타깃.

노트북의 학습과 일일 파이프라인의 예측이 모두 build_dataset()을 거친다. 학습 때와 서빙 때
피처 계산이 달라지는 일을 막기 위해서다.

시점 규칙: 각 거래일 t에는 t일 마감(23:00 UTC)까지 알려진 값만 붙인다.
- ALFRED 거시: 공개일 다음 날부터 (공개 기록이 없는 과거는 공개 지연을 가정)
- 콜롬비아 페소(Yahoo): 다음 날부터
- ENSO: 3개월 평균의 마지막 달 + 10일부터
- NASA 기상: 관측일 + 4일부터 (편차의 기준은 직전 10년 같은 달 평균)
"""
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

from .config import HORIZONS, REGIONS, SETTINGS

WEATHER_LAG = pd.Timedelta(days=SETTINGS["weather"]["availability_lag_days"])
HEAT_C = SETTINGS["weather"]["heat_threshold_c"]
CLIMATOLOGY_YEARS = SETTINGS["weather"]["climatology_years"]
CLIMATE_HALF_WINDOW = 15  # 날짜 단위 평년값: 같은 날짜 앞뒤 15일
# z를 만들 때 표준편차의 하한. 7월처럼 고온일이 한 번도 없던 시기에 0으로 나누지 않게 한다.
Z_FLOOR = {"rain30": 1.0, "rain90": 1.0, "temp30": 0.1, "heat30": 1.0, "tmin7": 0.1}
# 브라질 서리철(6–8월)과 아라비카 개화기(9–10월). 출처는 노트북 03b 사전 등록.
SEASON_MONTHS = {"frost": (6, 7, 8), "flowering": (9, 10)}
# 이보다 오래된 값은 결측으로 둔다. 월별 지표는 공개 주기가 길어 여유를 더 둔다.
MAX_AGE = {"daily": pd.Timedelta(days=14), "monthly": pd.Timedelta(days=80), "weather": pd.Timedelta(days=4)}


def _weather_names(region: dict) -> list[str]:
    names = ["rain_anom", "spi90", "temp_anom", "heat_anom"]
    if region["country"] == "Brazil":  # 한파·서리 피해는 브라질 고원 산지에서만 의미가 있다
        names.append("cold_anom")
    return [f"{region['id']}_{name}" for name in names]


FEATURE_GROUPS = {
    "price": ["ret_1", "ret_5", "ret_20", "ret_60", "vol_5", "vol_20", "vol_60", "ma_gap_60"],
    "macro": ["brl_chg_20", "cop_chg_20", "rate_chg_20", "oil_chg_20", "freight_chg_60"],
    "climate": [name for region in REGIONS for name in _weather_names(region)] + ["enso", "enso_chg_3m"],
    "cycle": ["month_sin", "month_cos", "biennial_sin", "biennial_cos"],
}
ALL_FEATURES = [name for group in FEATURE_GROUPS.values() for name in group]

# 수익률 모델 재설계(노트북 03b)에서 더한 묶음. 위의 묶음과 ALL_FEATURES는 바꾸지 않는다(방향 모델 입력 유지).
EXTRA_GROUPS = {
    "short": ["vol_ratio_5_60", "vol_ratio_20_60"],
    "climate_summary": ["br_rain90_z", "br_temp30_z", "br_heat30_z", "br_tmin7_z", "co_rain90_z", "co_temp30_z",
                        "br_drought_days90", "co_wet_days90", "br_cold_days30", "enso_state",
                        "br_tmin7_z_frost", "br_rain30_z_flowering"],
    "macro_long": ["brl_chg_60", "brl_chg_120", "cop_chg_60", "rate_chg_60", "oil_chg_60", "freight_chg_120"],
}
EXTRA_FEATURES = [name for group in EXTRA_GROUPS.values() for name in group]


def trading_sessions(start, end) -> pd.DatetimeIndex:
    """NYSE 영업일에 커피 선물만 거래한 날을 더한 거래일 축."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    days = mcal.get_calendar("NYSE").valid_days(start, end).tz_localize(None).normalize()
    extra = pd.to_datetime(SETTINGS["market"]["extra_sessions"])
    days = days.union(extra[(extra >= start) & (extra <= end)])
    return pd.DatetimeIndex(days, name="date").as_unit("ns")


def future_session(origin, horizon: int) -> pd.Timestamp:
    """origin 다음 horizon번째 거래일 (아직 오지 않은 목표일 계산용)."""
    origin = pd.Timestamp(origin)
    sessions = trading_sessions(origin + pd.Timedelta(days=1), origin + pd.Timedelta(days=horizon * 2 + 20))
    return sessions[horizon - 1]


def asof(values: pd.DataFrame, sessions: pd.DatetimeIndex, max_age: pd.Timedelta) -> pd.DataFrame:
    """각 거래일에, 그날까지 이용 가능해진 가장 최근 관측을 붙인다.

    values는 observed_at(관측일), available_at(이용 가능 시점)과 값 열을 가진다.
    같은 날 여러 관측이 공개되면 가장 최근 관측일을 쓴다. 미래 값은 붙지 않는다.
    """
    right = values.sort_values(["available_at", "observed_at"])
    right = right[right["observed_at"] == right["observed_at"].cummax()]  # 오래된 관측으로 되돌아가지 않게
    joined = pd.merge_asof(pd.DataFrame({"date": sessions}), right, left_on="date", right_on="available_at",
                           direction="backward", tolerance=max_age)
    return joined.set_index("date")


def price_features(prices: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """가격 피처.

    가격이 빠진 거래일도 축에 남겨 둔다(행을 지우면 'h거래일 뒤'의 간격이 틀어진다).
    피처는 그 시점까지 알려진 마지막 종가로 계산한다. 거시 지표를 as-of로 붙이는 것과 같은
    원칙이다. close 열과 타깃은 실제 종가만 쓰므로, 가격이 없는 날은 학습·평가에서 빠진다.
    """
    close = prices.set_index("date")["close"].reindex(sessions)
    known = close.ffill()
    log_price = np.log(known)
    daily = log_price.diff()
    features = pd.DataFrame({"close": close}, index=sessions)
    for days in (1, 5, 20, 60):
        features[f"ret_{days}"] = log_price - log_price.shift(days)
    for days in (5, 20, 60):
        features[f"vol_{days}"] = daily.rolling(days, min_periods=days).std()
        features[f"log_vol_{days}"] = np.log(features[f"vol_{days}"])  # 변동성 모델(HAR)용
    features["ma_gap_60"] = log_price - np.log(known.rolling(60, min_periods=60).mean())
    # 단기 위험: 최근 변동성이 평소(60일)보다 얼마나 큰가
    features["vol_ratio_5_60"] = features["vol_5"] / features["vol_60"]
    features["vol_ratio_20_60"] = features["vol_20"] / features["vol_60"]
    return features


def _series(sources: dict, name: str, sessions, lag_days: int, max_age: str, column: str = "value",
            release: str | None = "release_date") -> pd.Series:
    raw = sources[name].dropna(subset=[column])
    available = (raw[release] if release else raw["date"]) + pd.Timedelta(days=lag_days)
    values = pd.DataFrame({"observed_at": raw["date"], "available_at": available, "value": raw[column]})
    return asof(values, sessions, MAX_AGE[max_age])["value"]


def macro_features(sources: dict, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """환율·유가·운임은 비율 변화(로그 차분), 금리는 수준 차이. WTI는 음수인 날이 있어 차이로 본다."""
    brl = _series(sources, "macro_brl", sessions, 1, "daily")
    cop = _series(sources, "fx_cop", sessions, 1, "daily", column="close", release=None)
    rate = _series(sources, "macro_rate", sessions, 1, "daily")
    oil = _series(sources, "macro_oil", sessions, 1, "daily")
    freight = _series(sources, "macro_freight", sessions, 1, "monthly")
    return pd.DataFrame({
        "brl_chg_20": np.log(brl).diff(20),
        "cop_chg_20": np.log(cop).diff(20),
        "rate_chg_20": rate.diff(20),
        "oil_chg_20": oil.diff(20),
        "freight_chg_60": np.log(freight).diff(60),
        # 거시 장기(03b): 긴 지평은 느린 변화에 반응한다는 가설
        "brl_chg_60": np.log(brl).diff(60),
        "brl_chg_120": np.log(brl).diff(120),
        "cop_chg_60": np.log(cop).diff(60),
        "rate_chg_60": rate.diff(60),
        "oil_chg_60": oil.diff(60),
        "freight_chg_120": np.log(freight).diff(120),
    }, index=sessions)


def weather_rolling(daily: pd.DataFrame) -> pd.DataFrame:
    """일별 기상 → 30일 강수 합, 30일 평균기온, 90일 강수 합, 7일 최저기온의 최솟값, 30일 고온일 수.

    NASA POWER는 약 50km 격자의 평균이라, 2021년 7월 서리 때도 최저기온이 3℃ 아래로
    내려가지 않았다. 그래서 '0℃ 미만' 같은 서리 기준 대신 평년보다 얼마나 추운지를 본다.
    창 안에 결측이 있으면 결과도 결측이다. 평균이나 이전 값으로 채우지 않는다.
    """
    daily = daily.asfreq("D")
    hot = (daily["T2M_MAX"] > HEAT_C).astype(float).where(daily["T2M_MAX"].notna())
    return pd.DataFrame({
        "rain30": daily["PRECTOTCORR"].rolling(30, min_periods=30).sum(),
        "temp30": daily["T2M"].rolling(30, min_periods=30).mean(),
        "rain90": daily["PRECTOTCORR"].rolling(90, min_periods=90).sum(),
        "tmin7": daily["T2M_MIN"].rolling(7, min_periods=7).min(),
        "heat30": hot.rolling(30, min_periods=30).sum(),
    })


def trailing_climatology(rolled: pd.DataFrame, years: int = CLIMATOLOGY_YEARS) -> pd.DataFrame:
    """각 날짜에 '직전 10년 같은 달'의 평균(과 90일 강수의 표준편차)을 붙인다.

    1981–2004 같은 고정 평년값을 쓰면 2006년 이후 거의 모든 해가 가뭄으로 나왔다(기후 변화
    또는 위성 자료의 수준 변화). 그래서 기준을 10년씩 옮긴다. 그해 자료는 쓰지 않으므로
    미래 정보가 섞이지 않는다.
    """
    by = [rolled.index.year.rename("year"), rolled.index.month.rename("month")]
    monthly = rolled.groupby(by).mean()
    monthly["rain90_sq"] = (rolled["rain90"] ** 2).groupby(by).mean()
    normal = (monthly.unstack("month")                       # 행: 연도, 열: (지표, 월)
                     .rolling(years, min_periods=years - 2).mean()
                     .shift(1)                                # 그해 자료는 쓰지 않는다
                     .stack("month", future_stack=True))
    normal["rain90_std"] = np.sqrt(normal["rain90_sq"] - normal["rain90"] ** 2)
    key = pd.MultiIndex.from_arrays(by)
    return normal.reindex(key).set_axis(rolled.index)


def climate_features(sources: dict, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """직전 10년 같은 달 대비 편차와 ENSO."""
    features = pd.DataFrame(index=sessions)
    for region in REGIONS:
        rid = region["id"]
        rolled = weather_rolling(sources[f"weather_{rid}"].set_index("date"))
        normal = trailing_climatology(rolled)
        n = {column: normal[column].to_numpy() for column in normal.columns}
        derived = pd.DataFrame({
            f"{rid}_rain_anom": rolled["rain30"].to_numpy() - n["rain30"],
            f"{rid}_spi90": (rolled["rain90"].to_numpy() - n["rain90"]) / n["rain90_std"],  # 음수면 가뭄
            f"{rid}_temp_anom": rolled["temp30"].to_numpy() - n["temp30"],
            f"{rid}_heat_anom": rolled["heat30"].to_numpy() - n["heat30"],
            f"{rid}_cold_anom": rolled["tmin7"].to_numpy() - n["tmin7"],  # 음수일수록 평년보다 추움
        }, index=rolled.index)[_weather_names(region)]
        derived["observed_at"] = derived.index
        derived["available_at"] = derived.index + WEATHER_LAG
        aligned = asof(derived, sessions, MAX_AGE["weather"])
        features[_weather_names(region)] = aligned[_weather_names(region)]
    enso = _series(sources, "enso", sessions, 0, "monthly")
    features["enso"] = enso
    features["enso_chg_3m"] = enso.diff(63)
    return features


def daily_climatology(rolled: pd.DataFrame, years: int = CLIMATOLOGY_YEARS,
                      half_window: int = CLIMATE_HALF_WINDOW) -> tuple[pd.DataFrame, pd.DataFrame]:
    """각 날짜에 '직전 10년의 같은 날짜 ±15일' 평균과 표준편차를 붙인다. 그해 자료는 쓰지 않는다.

    같은 달 평균(trailing_climatology)은 달이 바뀌는 날 기준이 한 번에 뛰어서, 날씨가 그대로여도 편차가
    바뀐다(2021-08-01 남미나스 한파 편차 −5.94 → −6.66). 날짜 단위로 앞뒤 15일을 묶으면 기준이 매일
    조금씩만 움직인다. 날짜 축은 원형으로 잇고(12월 말과 1월 초가 이웃), 2월 29일은 2월 28일로 본다.
    """
    index = rolled.index
    doy = np.asarray(index.dayofyear - (index.is_leap_year & (index.dayofyear >= 60)))  # 1–365
    year = np.asarray(index.year)
    all_years = np.arange(year.min(), year.max() + 1)
    width = 2 * half_window + 1

    def window_sum(a):  # 날짜 축의 원형 이동합(앞뒤 half_window일)
        padded = np.concatenate([a[:, -half_window:], a, a[:, :half_window]], axis=1)
        cumsum = np.concatenate([np.zeros((len(a), 1)), np.cumsum(padded, axis=1)], axis=1)
        return cumsum[:, width:] - cumsum[:, :-width]

    def previous_years(a):  # 직전 years년의 합. 그해는 뺀다
        return pd.DataFrame(a).rolling(years, min_periods=1).sum().shift(1).to_numpy()

    means, stds = {}, {}
    for column in rolled.columns:
        table = (rolled[column].groupby([year, doy]).mean().unstack()
                 .reindex(index=all_years, columns=range(1, 366)).to_numpy())
        present = ~np.isnan(table)
        filled = np.where(present, table, 0.0)
        days = window_sum(present.astype(float))
        count = previous_years(days)
        with np.errstate(invalid="ignore", divide="ignore"):  # 자료가 없는 칸은 아래에서 결측으로 둔다
            mean = previous_years(window_sum(filled)) / count
            variance = previous_years(window_sum(filled ** 2)) / count - mean ** 2
        enough = previous_years((days > 0).astype(float)) >= years - 2  # 자료 있는 해가 8년 이상
        mean, std = np.where(enough, mean, np.nan), np.where(enough, np.sqrt(np.clip(variance, 0, None)), np.nan)
        position = (year - all_years[0], doy - 1)
        means[column], stds[column] = mean[position], std[position]
    return pd.DataFrame(means, index=index), pd.DataFrame(stds, index=index)


def _region_anomalies(sources: dict, region_id: str, normal: str, standardize: bool) -> pd.DataFrame:
    rolled = weather_rolling(sources[f"weather_{region_id}"].set_index("date"))
    if normal == "monthly":  # 기존 피처와 같은 '같은 달' 평년값(03b 비교용)
        return rolled - trailing_climatology(rolled)[rolled.columns]
    mean, std = daily_climatology(rolled)
    anomaly = rolled - mean
    return anomaly / std.clip(lower=pd.Series(Z_FLOOR), axis=1) if standardize else anomaly


def _days(condition: pd.Series, known: pd.Series, window: int) -> pd.Series:
    """최근 window일 가운데 condition이 참인 날 수. 그 사이 값이 하나라도 없으면 결측이다."""
    return condition.astype(float).where(known.notna()).rolling(window, min_periods=window).sum()


def climate_summary(sources: dict, sessions: pd.DatetimeIndex, normal: str = "daily", standardize: bool = True,
                    events: bool = True, season: bool = True) -> pd.DataFrame:
    """수익률 모델용 기후 요약(노트북 03b). 산지별 이상 정도를 나라별로 묶는다.

    - normal: 'daily'는 직전 10년 같은 날짜 ±15일, 'monthly'는 기존 피처와 같은 직전 10년 같은 달
    - standardize: 그 시기의 표준편차로 나눈 z. 산지·계절이 달라도 '얼마나 드문가'를 같은 눈금으로 본다
    - events: 표준 기준을 넘은 날 수(SPI ±1.5, 기온 z −2)와 ENSO 상태(ONI ±0.5)
    - season: 브라질 서리철에만 켜지는 한파 z, 개화기에만 켜지는 30일 강수 z
    브라질 3곳은 평균(한파는 가장 추운 곳), 콜롬비아 3곳은 평균이다. 한 산지라도 없으면 결측이다.
    관측일 + 4일에 쓸 수 있다고 보는 것은 기존 기후 피처와 같다.
    """
    if (events or season) and not standardize:
        raise ValueError("사건·생육기 피처는 z(standardize=True)로만 만든다")
    anomalies = {r["id"]: _region_anomalies(sources, r["id"], normal, standardize) for r in REGIONS}

    def country(name, column, how="mean"):
        frame = pd.concat([anomalies[r["id"]][column] for r in REGIONS if r["country"] == name], axis=1)
        return frame.min(axis=1, skipna=False) if how == "min" else frame.mean(axis=1, skipna=False)

    daily = pd.DataFrame({
        "br_rain90_z": country("Brazil", "rain90"), "br_temp30_z": country("Brazil", "temp30"),
        "br_heat30_z": country("Brazil", "heat30"), "br_tmin7_z": country("Brazil", "tmin7", "min"),
        "co_rain90_z": country("Colombia", "rain90"), "co_temp30_z": country("Colombia", "temp30"),
    })
    if events:
        daily["br_drought_days90"] = _days(daily["br_rain90_z"] <= -1.5, daily["br_rain90_z"], 90)
        daily["co_wet_days90"] = _days(daily["co_rain90_z"] >= 1.5, daily["co_rain90_z"], 90)
        daily["br_cold_days30"] = _days(daily["br_tmin7_z"] <= -2, daily["br_tmin7_z"], 30)
    if season:
        month = daily.index.month
        daily["br_tmin7_z_frost"] = daily["br_tmin7_z"].where(month.isin(SEASON_MONTHS["frost"]), 0.0)
        daily["br_rain30_z_flowering"] = country("Brazil", "rain30").where(month.isin(SEASON_MONTHS["flowering"]), 0.0)
    columns = list(daily.columns)
    daily["observed_at"], daily["available_at"] = daily.index, daily.index + WEATHER_LAG
    features = asof(daily, sessions, MAX_AGE["weather"])[columns]
    if events:
        enso = _series(sources, "enso", sessions, 0, "monthly")
        features["enso_state"] = np.sign(enso).where(enso.abs() >= 0.5, 0.0).where(enso.notna())
    return features


def cycle_features(sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """계절(12개월)과 해거리(24개월) 주기.

    아라비카는 많이 열린 해 다음 해에 덜 여는 격년 주기가 있다. 브라질 작물년도가 7월에
    시작하므로 2000년 7월을 기준으로 24개월 주기의 위치를 sin·cos로 나타낸다.
    주기의 방향(어느 해가 풍작인지)은 모델이 학습한다.
    """
    months = (sessions.year - 2000) * 12 + (sessions.month - 7)
    return pd.DataFrame({
        "month_sin": np.sin(2 * np.pi * sessions.month / 12),
        "month_cos": np.cos(2 * np.pi * sessions.month / 12),
        "biennial_sin": np.sin(2 * np.pi * months / 24),
        "biennial_cos": np.cos(2 * np.pi * months / 24),
    }, index=sessions)


def targets(close: pd.Series) -> pd.DataFrame:
    """y_h = ln(P[t+h] / P[t]), v_h = ln(t+1~t+h 일간 수익률의 표준편차). 가격이 없으면 결측이다."""
    sessions = pd.Series(close.index, index=close.index)
    daily = np.log(close).diff()
    frame = pd.DataFrame(index=close.index)
    for h in HORIZONS:
        frame[f"y_{h}"] = np.log(close.shift(-h) / close)
        frame[f"target_date_{h}"] = sessions.shift(-h)
        frame[f"v_{h}"] = np.log(daily.rolling(h, min_periods=h - 2).std().shift(-h))
    return frame


def build_dataset(sources: dict) -> pd.DataFrame:
    """거래일 하나가 한 행인 표: 종가, 모든 피처, 지평별 타깃과 목표일."""
    prices = sources["prices"].dropna(subset=["close"])
    sessions = trading_sessions(prices["date"].min(), prices["date"].max())
    data = price_features(prices, sessions)
    data = data.join(macro_features(sources, sessions)).join(climate_features(sources, sessions))
    data = data.join(climate_summary(sources, sessions)).join(cycle_features(sessions))
    return data.join(targets(data["close"]))
