"""원천별 시점 정렬과 변환."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pandas_market_calendars as mcal


REGIONS = (
    "br_sul_minas",
    "br_cerrado",
    "br_alta_mogiana",
    "co_huila",
    "co_caldas",
    "co_antioquia",
)
WEATHER_DELAY_DAYS = 4


def coffee_sessions(start, end) -> pd.DatetimeIndex:
    """Notebook과 같은 거래일 축을 만든다."""
    days = mcal.get_calendar("NYSE").valid_days(start, end).tz_localize(None).as_unit("ns")
    exceptions = pd.to_datetime(["2018-12-05", "2025-01-09"])
    days = days.union(exceptions).sort_values()
    return days[(days >= pd.Timestamp(start)) & (days <= pd.Timestamp(end))]


def transform_market(sources: dict[str, pd.DataFrame], sessions: pd.DatetimeIndex):
    coffee = sources["coffee"].sort_values("date").drop_duplicates("date", keep="last")
    prices = coffee.set_index("date").reindex(sessions)
    close = prices["close"]
    log_price = np.log(close)
    features = pd.DataFrame(index=sessions)
    for days in (1, 5, 20):
        features[f"return_{days}"] = log_price.diff(days)
    features["volatility_20"] = log_price.diff().rolling(20, min_periods=20).std()
    return prices, features


def align_available(
    frame: pd.DataFrame,
    columns: list[str],
    sessions: pd.DatetimeIndex,
    max_age_days: int | None = None,
) -> pd.DataFrame:
    """각 거래일에 그 시점까지 공개된 최신 관측만 붙인다."""
    left = pd.DataFrame({"date": sessions})
    right = frame.sort_values(["available_at", "date"]).copy()
    right = right.loc[right["date"].eq(right["date"].cummax())]
    right = right.rename(columns={"date": "observed_at"})
    tolerance = None if max_age_days is None else pd.Timedelta(days=max_age_days)
    joined = pd.merge_asof(
        left,
        right[["observed_at", "available_at", *columns]],
        left_on="date",
        right_on="available_at",
        direction="backward",
        tolerance=tolerance,
    )
    valid = joined["available_at"].notna()
    if not (joined.loc[valid, "available_at"] <= joined.loc[valid, "date"]).all():
        raise ValueError("공개 전 거시·기상 관측이 feature에 포함됐습니다.")
    if not (joined.loc[valid, "observed_at"] <= joined.loc[valid, "available_at"]).all():
        raise ValueError("관측일이 공개 시점보다 늦습니다.")
    return joined.set_index("date")


def transform_macro(sources: dict[str, pd.DataFrame], sessions: pd.DatetimeIndex):
    features = pd.DataFrame(index=sessions)
    availability = {}
    specifications = (
        ("alfred_dexbzus", "brl", True),
        ("alfred_dff", "rate", False),
        ("alfred_dcoilwtico", "oil", False),
    )
    for source, output, logarithm in specifications:
        raw = sources[source].dropna(subset=["value"]).copy()
        raw["available_at"] = pd.to_datetime(raw["release_date"]) + pd.Timedelta(days=1)
        joined = align_available(raw, ["value"], sessions)
        values = np.log(joined["value"]) if logarithm else joined["value"]
        features[f"{output}_change_20"] = values.diff(20)
        availability[output] = joined[["observed_at", "available_at"]]
    return features, availability


def transform_weather(
    sources: dict[str, pd.DataFrame],
    sessions: pd.DatetimeIndex,
    fit_end,
):
    features = pd.DataFrame(index=sessions)
    availability = {}
    weather_columns = []
    train_start = pd.Timestamp("2015-01-01")
    for region in REGIONS:
        source = sources[f"weather_{region}"].sort_values("date").drop_duplicates("date", keep="last")
        daily_index = pd.date_range(source["date"].min(), source["date"].max())
        raw = source.set_index("date").reindex(daily_index)
        daily = pd.DataFrame(index=daily_index)
        daily["rain30"] = raw["PRECTOTCORR"].rolling(30, min_periods=30).sum()
        daily["rain90"] = raw["PRECTOTCORR"].rolling(90, min_periods=90).sum()
        daily["temp30"] = raw["T2M"].rolling(30, min_periods=30).mean()
        daily["prior_rain90"] = daily["rain90"].shift(7)
        daily["frost7"] = (-raw["T2M_MIN"]).clip(lower=0).rolling(7, min_periods=7).sum()
        fit = daily.loc[train_start : pd.Timestamp(fit_end) - pd.Timedelta(days=WEATHER_DELAY_DAYS)]
        normal = fit.groupby(fit.index.month)[["rain30", "rain90", "temp30", "prior_rain90"]].mean()
        reference = normal.reindex(daily.index.month).set_axis(daily.index)
        derived = pd.DataFrame(
            {
                f"{region}_rain_anomaly": daily["rain30"] - reference["rain30"],
                f"{region}_temp_anomaly": daily["temp30"] - reference["temp30"],
                f"{region}_drought": (reference["rain90"] - daily["rain90"]).clip(lower=0),
            },
            index=daily.index,
        )
        if region.startswith("br_"):
            derived[f"{region}_frost"] = daily["frost7"]
            derived[f"{region}_drought_frost"] = (
                (reference["prior_rain90"] - daily["prior_rain90"]).clip(lower=0) * daily["frost7"]
            )
        derived = derived.rename_axis("date").reset_index()
        derived["available_at"] = derived["date"] + pd.Timedelta(days=WEATHER_DELAY_DAYS)
        columns = [column for column in derived if column not in ("date", "available_at")]
        joined = align_available(derived, columns, sessions, max_age_days=WEATHER_DELAY_DAYS)
        features[columns] = joined[columns]
        availability[region] = joined[["observed_at", "available_at"]]
        weather_columns.extend(columns)
    features["month_sin"] = np.sin(2 * np.pi * features.index.month / 12)
    features["month_cos"] = np.cos(2 * np.pi * features.index.month / 12)
    weather_columns.extend(["month_sin", "month_cos"])
    return features, weather_columns, availability

