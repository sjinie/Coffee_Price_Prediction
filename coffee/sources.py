"""가격·거시·기후 원천 데이터를 받아 Parquet로 증분 저장한다.

    python -m coffee.sources                   # 어제까지 증분 수집
"""
from datetime import date, timedelta
from functools import partial
import os
from pathlib import Path
import time

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from .config import MACRO_SERIES, REGIONS, ROOT, SETTINGS, SOURCES_DIR

FRED_URL = "https://api.stlouisfed.org/fred"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
WEATHER_VARS = SETTINGS["weather"]["variables"]
OVERLAP_DAYS = 7  # 원천이 최근 값을 고쳐 보내는 경우가 있어 마지막 일주일은 다시 받는다
ENSO_SEASONS = ["DJF", "JFM", "FMA", "MAM", "AMJ", "MJJ", "JJA", "JAS", "ASO", "SON", "OND", "NDJ"]


class SourceError(RuntimeError):
    """수집 실패. 메시지에 API 키나 요청 URL을 넣지 않는다."""


def _get(url: str, params: dict | None = None, timeout: int = 60, retries: int = 3) -> requests.Response:
    for attempt in range(retries):
        try:
            response = requests.get(url, params=params, timeout=(10, timeout))
            response.raise_for_status()
            return response
        except requests.RequestException as exc:
            if attempt == retries - 1:
                status = getattr(exc.response, "status_code", None)
                raise SourceError(f"HTTP {status}" if status else "연결 실패") from None
            time.sleep(2 ** attempt)


def fetch_yahoo(symbol: str, start: date, end: date) -> pd.DataFrame:
    """Yahoo Finance 일봉. KC=F의 Close는 Yahoo 제공값이며 공식 정산가와 다를 수 있다."""
    import yfinance as yf

    raw = yf.Ticker(symbol).history(
        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),  # Yahoo의 end는 포함되지 않는다
        interval="1d", auto_adjust=False, actions=False, repair=False, timeout=30)
    if raw.empty:
        raise SourceError(f"{symbol} 응답이 비어 있음")
    frame = raw[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
    frame.index = frame.index.tz_localize(None).normalize()
    return frame.rename_axis("date").reset_index()


def _fred_key() -> str:
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise SourceError("FRED_API_KEY 환경 변수가 없음")
    return key


def _observations(params: dict) -> pd.DataFrame:
    payload = _get(f"{FRED_URL}/series/observations", {"api_key": _fred_key(), "file_type": "json", **params}).json()
    return pd.DataFrame(payload["observations"], columns=["date", "realtime_start", "value"])


def fetch_macro(series_id: str, assumed_lag_days: int, start: date, end: date) -> pd.DataFrame:
    """FRED/ALFRED 거시 지표와 공개일.

    FRED의 현재값은 나중에 수정된 값일 수 있어, 가능한 구간은 ALFRED 최초 공개값
    (output_type=4)과 그 공개일을 쓴다. ALFRED 기록은 지표마다 시작 시점이 달라
    (예: 환율 2014년, 운임 2015년) 그 이전 관측은 FRED 현재값에 공개 지연을 가정하고
    release_assumed=True로 표시한다.
    """
    vintages = _get(f"{FRED_URL}/series/vintagedates",
                    {"api_key": _fred_key(), "file_type": "json", "series_id": series_id, "limit": 1}).json()
    first_vintage = date.fromisoformat(vintages["vintage_dates"][0])
    parts = []
    if start < first_vintage:
        early = _observations({"series_id": series_id, "observation_start": start.isoformat(),
                               "observation_end": min(end, first_vintage - timedelta(days=1)).isoformat()})
        early["realtime_start"] = (pd.to_datetime(early["date"]) + pd.Timedelta(days=assumed_lag_days)).dt.strftime("%Y-%m-%d")
        parts.append(early.assign(release_assumed=True))
    # 공개본이 많은 일별 금리는 한 번에 요청하면 API 제한(2,000개)을 넘으므로 연 단위로 나눈다
    for year in range(max(start, first_vintage).year, end.year + 1):
        first, last = max(start, first_vintage, date(year, 1, 1)), min(end, date(year, 12, 31))
        if first > last:
            continue
        late = _observations({"series_id": series_id, "output_type": 4,
                              "observation_start": first.isoformat(), "observation_end": last.isoformat(),
                              "realtime_start": first.isoformat(),
                              "realtime_end": min(end, date(year + 1, 6, 30)).isoformat()})
        parts.append(late.assign(release_assumed=False))
    frame = pd.concat(parts, ignore_index=True).rename(columns={"realtime_start": "release_date"})
    frame["date"] = pd.to_datetime(frame["date"])
    frame["release_date"] = pd.to_datetime(frame["release_date"])
    frame["value"] = pd.to_numeric(frame["value"].replace(".", np.nan))  # FRED는 결측을 "."로 보낸다
    return frame.drop_duplicates("date", keep="last")


def fetch_enso(start: date, end: date) -> pd.DataFrame:
    """NOAA CPC의 ONI(엘니뇨 3.4 해역 수온 편차의 3개월 이동평균).

    날짜는 3개월의 가운데 달 1일이다. 마지막 달이 끝나고 약 열흘 뒤 공개된다고 가정한다.
    ONI는 원자료 갱신으로 과거 값이 조금씩 바뀔 수 있다(최초 공개값 기록은 없다).
    """
    lines = _get(SETTINGS["enso"]["url"]).text.split("\n")[1:]
    rows = []
    for line in lines:
        parts = line.split()
        if len(parts) != 4:
            continue
        season, year, _, anomaly = parts
        center = pd.Timestamp(year=int(year), month=ENSO_SEASONS.index(season) + 1, day=1)
        released = center + pd.DateOffset(months=2) + pd.Timedelta(days=SETTINGS["enso"]["availability_lag_days"])
        rows.append({"date": center, "release_date": released, "value": float(anomaly)})
    frame = pd.DataFrame(rows)
    return frame[(frame["date"] >= pd.Timestamp(start)) & (frame["date"] <= pd.Timestamp(end))]


def fetch_weather(region: dict, start: date, end: date) -> pd.DataFrame:
    """NASA POWER 일별 기상(UTC). 응답의 fill value(-999)는 결측으로 바꾼다."""
    payload = _get(NASA_URL, {
        "parameters": ",".join(WEATHER_VARS), "community": "AG", "format": "JSON", "time-standard": "UTC",
        "latitude": region["lat"], "longitude": region["lon"],
        "start": start.strftime("%Y%m%d"), "end": end.strftime("%Y%m%d"),
    }, timeout=180).json()
    frame = pd.DataFrame(payload["properties"]["parameter"])[WEATHER_VARS]
    frame = frame.replace(payload["header"]["fill_value"], np.nan)
    frame.index = pd.to_datetime(frame.index, format="%Y%m%d")
    return frame.rename_axis("date").reset_index()


def source_jobs():
    """(파일 이름, 수집 함수, 값 열, 수집 시작일) 목록. 파일 이름이 곧 소스 이름이다."""
    start = date.fromisoformat(SETTINGS["collect_start"])
    weather_start = date.fromisoformat(SETTINGS["weather"]["start"])  # 직전 10년 평년값용
    yield "prices", partial(fetch_yahoo, SETTINGS["market"]["symbol"]), ["close"], start
    yield "fx_cop", partial(fetch_yahoo, SETTINGS["market"]["cop_symbol"]), ["close"], start
    for name, spec in MACRO_SERIES.items():
        yield f"macro_{name}", partial(fetch_macro, spec["series"], spec["assumed_lag_days"]), ["value"], start
    yield "enso", fetch_enso, ["value"], start
    for region in REGIONS:
        yield f"weather_{region['id']}", partial(fetch_weather, region), WEATHER_VARS, weather_start


def update_parquet(path: Path, fetch, start: date, end: date, value_columns: list[str]) -> pd.DataFrame:
    """값이 있는 마지막 날짜부터 다시 받아 기존 파일과 합친다. 같은 날짜는 새 값으로 바꾼다."""
    old = pd.read_parquet(path) if path.exists() else None
    fetch_start = start
    if old is not None:
        valid = old.dropna(subset=value_columns, how="all")
        if not valid.empty:
            fetch_start = max(start, valid["date"].max().date() - timedelta(days=OVERLAP_DAYS))
    if fetch_start > end:
        return old
    merged = pd.concat([old, fetch(fetch_start, end)]) if old is not None else fetch(fetch_start, end)
    merged["date"] = pd.to_datetime(merged["date"])
    merged = merged.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    merged.to_parquet(temporary, index=False)
    temporary.replace(path)  # 쓰는 도중 실패해도 기존 파일이 깨지지 않게
    return merged


def update_all(end: date, directory: Path = SOURCES_DIR) -> list[dict]:
    """모든 소스를 갱신하고 소스별 결과를 돌려준다. 한 소스가 실패해도 나머지는 계속한다."""
    results = []
    for name, fetch, columns, start in source_jobs():
        path = Path(directory) / f"{name}.parquet"
        try:
            frame = update_parquet(path, fetch, start, end, columns)
            last = frame.dropna(subset=columns, how="all")["date"].max().date()
            results.append({"source": name, "status": "ok", "last_date": last.isoformat()})
        except Exception as exc:  # 수집 실패는 기록만 하고, 예측 가능 여부는 피처 단계가 판단한다
            results.append({"source": name, "status": "failed", "error": f"{type(exc).__name__}: {exc}"})
        print(f"  {name}: {results[-1]['status']}", flush=True)
    return results


def load_sources(directory: Path = SOURCES_DIR) -> dict[str, pd.DataFrame]:
    sources = {}
    for name, *_ in source_jobs():
        path = Path(directory) / f"{name}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"소스 파일이 없습니다: {path}")
        frame = pd.read_parquet(path)
        for column in ("date", "release_date"):
            if column in frame:
                frame[column] = pd.to_datetime(frame[column]).astype("datetime64[ns]")
        if frame["date"].duplicated().any():
            raise ValueError(f"{name}: 날짜가 중복됩니다")
        sources[name] = frame.sort_values("date").reset_index(drop=True)
    return sources


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    update_all(date.today() - timedelta(days=1))
