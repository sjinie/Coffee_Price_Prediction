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

from .config import HORIZONS, REGIONS, SETTINGS, VOL_HORIZONS

WEATHER_LAG = pd.Timedelta(days=SETTINGS["weather"]["availability_lag_days"])
HEAT_C = SETTINGS["weather"]["heat_threshold_c"]
CLIMATOLOGY_YEARS = SETTINGS["weather"]["climatology_years"]
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
    for h in VOL_HORIZONS:
        frame[f"v_{h}"] = np.log(daily.rolling(h, min_periods=h - 2).std().shift(-h))
    return frame


def build_dataset(sources: dict) -> pd.DataFrame:
    """거래일 하나가 한 행인 표: 종가, 모든 피처, 지평별 타깃과 목표일."""
    prices = sources["prices"].dropna(subset=["close"])
    sessions = trading_sessions(prices["date"].min(), prices["date"].max())
    data = price_features(prices, sessions)
    data = data.join(macro_features(sources, sessions)).join(climate_features(sources, sessions))
    data = data.join(cycle_features(sessions))
    return data.join(targets(data["close"]))
