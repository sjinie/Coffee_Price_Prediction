"""가격·거시·기상 원천 데이터를 받아 Parquet로 증분 저장한다.

    python -m coffee.sources update            # 오늘까지 증분 수집
    python -m coffee.sources normals           # 기상 평년값(1991–2013) 1회 계산
"""
from datetime import date, timedelta
from functools import partial
import os
from pathlib import Path
import sys
import time

import numpy as np
import pandas as pd
import requests
from dotenv import load_dotenv

from .config import MACRO_SERIES, NORMALS_FILE, REGIONS, ROOT, SETTINGS, SOURCES_DIR

FRED_URL = "https://api.stlouisfed.org/fred/series/observations"
NASA_URL = "https://power.larc.nasa.gov/api/temporal/daily/point"
WEATHER_VARS = SETTINGS["weather"]["variables"]
OVERLAP_DAYS = 7  # 원천이 최근 값을 고쳐 보내는 경우가 있어 마지막 일주일은 다시 받는다


class SourceError(RuntimeError):
    """수집 실패. 메시지에 API 키나 요청 URL을 넣지 않는다."""


def _get(url: str, params: dict, timeout: int = 60, retries: int = 3) -> requests.Response:
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


def fetch_prices(start: date, end: date) -> pd.DataFrame:
    """Yahoo Finance의 KC=F 일봉. Close는 Yahoo 제공값이며 공식 정산가와 다를 수 있다."""
    import yfinance as yf

    raw = yf.Ticker(SETTINGS["market"]["symbol"]).history(
        start=start.isoformat(), end=(end + timedelta(days=1)).isoformat(),  # Yahoo의 end는 포함되지 않는다
        interval="1d", auto_adjust=False, actions=False, repair=False, timeout=30)
    if raw.empty:
        raise SourceError("가격 응답이 비어 있음")
    frame = raw[["Open", "High", "Low", "Close", "Volume"]].rename(columns=str.lower)
    frame.index = frame.index.tz_localize(None).normalize()
    return frame.rename_axis("date").reset_index()


def fetch_alfred(series_id: str, start: date, end: date) -> pd.DataFrame:
    """ALFRED 최초 공개값(output_type=4)과 그 공개일.

    FRED의 현재값은 나중에 수정된 값이라 당시 시점을 재현하지 못한다. 공개본이 많은 일별
    금리는 한 번에 요청하면 API 제한(2,000개)을 넘으므로 연 단위로 나눠 받는다.
    """
    key = os.environ.get("FRED_API_KEY", "").strip()
    if not key:
        raise SourceError("FRED_API_KEY 환경 변수가 없음")
    parts = []
    for year in range(start.year, end.year + 1):
        first, last = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        payload = _get(FRED_URL, {
            "api_key": key, "series_id": series_id, "file_type": "json", "output_type": 4,
            "observation_start": first.isoformat(), "observation_end": last.isoformat(),
            "realtime_start": first.isoformat(), "realtime_end": min(end, date(year + 1, 6, 30)).isoformat(),
        }).json()
        parts.append(pd.DataFrame(payload["observations"], columns=["date", "realtime_start", "value"]))
    frame = pd.concat(parts, ignore_index=True).rename(columns={"realtime_start": "release_date"})
    frame["date"] = pd.to_datetime(frame["date"])
    frame["release_date"] = pd.to_datetime(frame["release_date"])
    frame["value"] = pd.to_numeric(frame["value"].replace(".", np.nan))  # FRED는 결측을 "."로 보낸다
    return frame


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
    """(파일 이름, 수집 함수, 값 열) 목록. 파일 이름이 곧 소스 이름이다."""
    yield "prices", fetch_prices, ["close"]
    for name, series_id in MACRO_SERIES.items():
        yield f"macro_{name}", partial(fetch_alfred, series_id), ["value"]
    for region in REGIONS:
        yield f"weather_{region['id']}", partial(fetch_weather, region), WEATHER_VARS


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
    start = date.fromisoformat(SETTINGS["collect_start"])
    results = []
    for name, fetch, columns in source_jobs():
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
    for name, _, _ in source_jobs():
        path = Path(directory) / f"{name}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"소스 파일이 없습니다: {path}")
        frame = pd.read_parquet(path)
        frame["date"] = pd.to_datetime(frame["date"])
        if frame["date"].duplicated().any():
            raise ValueError(f"{name}: 날짜가 중복됩니다")
        sources[name] = frame.sort_values("date").reset_index(drop=True)
    return sources


def write_weather_normals(path: Path = NORMALS_FILE) -> pd.DataFrame:
    """표본 시작(2014) 이전 1991–2013년 자료로 지점·월별 평년값을 만든다. 한 번만 실행한다."""
    from .features import weather_rolling

    start, end = (date.fromisoformat(day) for day in SETTINGS["weather"]["normals_period"])
    rows = []
    for region in REGIONS:
        rolled = weather_rolling(fetch_weather(region, start, end).set_index("date"))
        rolled = rolled.loc[str(start.year + 1):]  # 첫해는 90일 창이 덜 차서 뺀다
        monthly = rolled.groupby(rolled.index.month)[["rain30", "temp30", "rain90"]].mean()
        rows.append(monthly.rename_axis("month").reset_index().assign(region=region["id"]))
    normals = pd.concat(rows)[["region", "month", "rain30", "temp30", "rain90"]].round(3)
    normals.to_csv(path, index=False)
    return normals


if __name__ == "__main__":
    load_dotenv(ROOT / ".env")
    command = sys.argv[1] if len(sys.argv) > 1 else "update"
    if command == "normals":
        print(write_weather_normals())
    elif command == "update":
        update_all(date.today() - timedelta(days=1))
    else:
        raise SystemExit("사용법: python -m coffee.sources [update|normals]")
