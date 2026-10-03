"""거래일 축, 시점 결합, 피처와 타깃.

노트북의 학습과 일일 파이프라인의 예측이 모두 build_dataset()을 거친다. 학습 때와 서빙 때
피처 계산이 달라지는 일을 막기 위해서다.

시점 규칙: 각 거래일 t에는 t일 마감(23:00 UTC)까지 알려진 값만 붙인다.
- ALFRED 거시: 공개일 다음 날부터
- NASA 기상: 관측일 + 4일부터
"""
import numpy as np
import pandas as pd
import pandas_market_calendars as mcal

from .config import HORIZONS, NORMALS_FILE, REGIONS, SETTINGS

WEATHER_LAG = pd.Timedelta(days=SETTINGS["weather"]["availability_lag_days"])
WEATHER_MAX_AGE = pd.Timedelta(days=4)   # 이보다 오래된 기상 값은 결측으로 둔다 (관측일 기준 최대 8일 전)
MACRO_MAX_AGE = pd.Timedelta(days=14)    # 거시 값이 2주 넘게 갱신되지 않으면 결측으로 둔다


def _weather_names(region: dict) -> list[str]:
    names = ["rain_anom", "temp_anom", "drought"]
    if region["country"] == "Brazil":  # 서리 피해는 브라질 고원 산지에서만 의미가 있다
        names.append("frost")
    return [f"{region['id']}_{name}" for name in names]


FEATURE_GROUPS = {
    "price": ["ret_1", "ret_5", "ret_20", "ret_60", "vol_20", "vol_60", "ma_gap_60"],
    "macro": ["brl_chg_20", "rate_chg_20", "oil_chg_20"],
    "weather": [name for region in REGIONS for name in _weather_names(region)],
    "season": ["month_sin", "month_cos"],
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
    features = pd.DataFrame({"close": close}, index=sessions)
    for days in (1, 5, 20, 60):
        features[f"ret_{days}"] = log_price - log_price.shift(days)
    features["vol_20"] = log_price.diff().rolling(20, min_periods=20).std()
    features["vol_60"] = log_price.diff().rolling(60, min_periods=60).std()
    features["ma_gap_60"] = log_price - np.log(known.rolling(60, min_periods=60).mean())
    return features


def macro_features(sources: dict, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    features = pd.DataFrame(index=sessions)
    for name in ("brl", "rate", "oil"):
        raw = sources[f"macro_{name}"].dropna(subset=["value"])
        values = pd.DataFrame({"observed_at": raw["date"], "available_at": raw["release_date"] + pd.Timedelta(days=1),
                               "value": raw["value"]})
        series = asof(values, sessions, MACRO_MAX_AGE)["value"]
        # 환율은 비율 변화(로그 차분), 금리·유가는 수준 차이. WTI는 음수인 날이 있어 로그를 쓰지 않는다.
        features[f"{name}_chg_20"] = np.log(series).diff(20) if name == "brl" else series.diff(20)
    return features


def weather_rolling(daily: pd.DataFrame) -> pd.DataFrame:
    """일별 기상 → 30일 강수 합, 30일 평균기온, 90일 강수 합, 7일 서리 지수.

    서리 지수는 최저기온이 0℃ 아래로 내려간 정도의 7일 합이다(실제 서리 관측은 아니다).
    창 안에 결측이 있으면 결과도 결측이다. 평균이나 이전 값으로 채우지 않는다.
    """
    daily = daily.asfreq("D")
    return pd.DataFrame({
        "rain30": daily["PRECTOTCORR"].rolling(30, min_periods=30).sum(),
        "temp30": daily["T2M"].rolling(30, min_periods=30).mean(),
        "rain90": daily["PRECTOTCORR"].rolling(90, min_periods=90).sum(),
        "frost7": (-daily["T2M_MIN"]).clip(lower=0).rolling(7, min_periods=7).sum(),
    })


def weather_features(sources: dict, normals: pd.DataFrame, sessions: pd.DatetimeIndex) -> pd.DataFrame:
    """평년(1991–2013) 같은 달 대비 편차. 평년값은 표본 이전 자료라 학습 구간에 따라 다시 fit하지 않는다."""
    features = pd.DataFrame(index=sessions)
    for region in REGIONS:
        rid = region["id"]
        rolled = weather_rolling(sources[f"weather_{rid}"].set_index("date"))
        normal = normals[normals["region"] == rid].set_index("month").reindex(rolled.index.month)
        derived = pd.DataFrame({
            f"{rid}_rain_anom": rolled["rain30"].to_numpy() - normal["rain30"].to_numpy(),
            f"{rid}_temp_anom": rolled["temp30"].to_numpy() - normal["temp30"].to_numpy(),
            f"{rid}_drought": np.clip(normal["rain90"].to_numpy() - rolled["rain90"].to_numpy(), 0, None),
            f"{rid}_frost": rolled["frost7"].to_numpy(),
        }, index=rolled.index)[_weather_names(region)]
        derived["observed_at"] = derived.index
        derived["available_at"] = derived.index + WEATHER_LAG
        aligned = asof(derived, sessions, WEATHER_MAX_AGE)
        features[_weather_names(region)] = aligned[_weather_names(region)]
    return features


def targets(close: pd.Series) -> pd.DataFrame:
    """y_h = ln(P[t+h] / P[t]). 둘 중 하나라도 가격이 없으면 결측이다."""
    sessions = pd.Series(close.index, index=close.index)
    frame = pd.DataFrame(index=close.index)
    for h in HORIZONS:
        frame[f"y_{h}"] = np.log(close.shift(-h) / close)
        frame[f"target_date_{h}"] = sessions.shift(-h)
    return frame


def build_dataset(sources: dict, normals: pd.DataFrame | None = None) -> pd.DataFrame:
    """거래일 하나가 한 행인 표: 종가, 모든 피처, 지평별 타깃과 목표일."""
    if normals is None:
        normals = pd.read_csv(NORMALS_FILE)
    prices = sources["prices"].dropna(subset=["close"])
    sessions = trading_sessions(prices["date"].min(), prices["date"].max())
    data = price_features(prices, sessions)
    data = data.join(macro_features(sources, sessions)).join(weather_features(sources, normals, sessions))
    data["month_sin"] = np.sin(2 * np.pi * sessions.month / 12)
    data["month_cos"] = np.cos(2 * np.pi * sessions.month / 12)
    return data.join(targets(data["close"]))
