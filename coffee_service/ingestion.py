"""커피 가격·거시·기상·COT 데이터를 받아 Parquet로 저장한다."""

import argparse
from datetime import date, timedelta
from io import BytesIO
import os
from pathlib import Path
from zipfile import ZipFile

import numpy as np
import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
import yaml
import yfinance as yf
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[1]
WEATHER_COLUMNS = ["PRECTOTCORR", "T2M", "T2M_MIN", "T2M_MAX", "RH2M"]
WEATHER_BUFFER_DAYS = 30
COT_COLUMNS = {
    "CFTC_Contract_Market_Code": "market_code",
    "Market_and_Exchange_Names": "market_name",
    "Open_Interest_All": "open_interest",
    "M_Money_Positions_Long_All": "managed_money_long",
    "M_Money_Positions_Short_All": "managed_money_short",
    "M_Money_Positions_Spread_All": "managed_money_spreading",
    "Prod_Merc_Positions_Long_All": "producer_long",
    "Prod_Merc_Positions_Short_All": "producer_short",
}


def get_response(session, url, **params):
    # requests 예외에는 FRED 키가 든 URL이 포함될 수 있어 그대로 출력하지 않는다.
    try:
        response = session.get(url, params=params, timeout=(10, 60))
        response.raise_for_status()
    except requests.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else "연결 실패"
        raise RuntimeError(f"요청 실패: {status}") from None
    return response


def build_session():
    session = requests.Session()
    retries = Retry(
        total=2, backoff_factor=1, status_forcelist=[502, 503, 504],
        allowed_methods=["GET"], respect_retry_after_header=False,
    )
    session.mount("https://", HTTPAdapter(max_retries=retries))
    return session


def fetch_yahoo(symbol, start, end):
    frame = yf.Ticker(symbol).history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),  # Yahoo의 end는 미포함이다.
        interval="1d", auto_adjust=False, back_adjust=False,
        repair=False, actions=False, keepna=True, timeout=30,
    )
    if frame.empty:
        raise ValueError(f"{symbol}: 받은 데이터가 없습니다.")
    frame = frame[["Open", "High", "Low", "Close", "Volume"]].copy()
    frame.columns = frame.columns.str.lower()
    frame.insert(0, "date", frame.index.tz_localize(None).normalize())
    frame = frame.reset_index(drop=True)
    frame["symbol"] = symbol
    outside = (frame.close < frame.low) | (frame.close > frame.high)
    print(f"  {symbol}: Close 범위 이탈 {outside.sum()}행, 원값 유지", flush=True)
    return frame


def fetch_fred(session, series_id, start, end):
    key = os.getenv("FRED_API_KEY")
    if not key:
        raise ValueError(".env에 FRED_API_KEY를 설정하세요.")
    payload = get_response(
        session, "https://api.stlouisfed.org/fred/series/observations",
        api_key=key, series_id=series_id, file_type="json",
        observation_start=start.isoformat(), observation_end=end.isoformat(),
        sort_order="asc", limit=100000,
    ).json()
    frame = pd.DataFrame(payload["observations"])
    if len(frame) != int(payload["count"]):
        raise ValueError(f"{series_id}: 응답이 잘렸습니다.")
    frame = frame[["date", "value"]].copy()
    frame["value"] = pd.to_numeric(frame.value.replace(".", np.nan))
    frame["series_id"] = series_id
    return frame


def fetch_weather(session, region, start, end):
    payload = get_response(
        session, "https://power.larc.nasa.gov/api/temporal/daily/point",
        parameters=",".join(WEATHER_COLUMNS), community="AG",
        longitude=region["longitude"], latitude=region["latitude"],
        start=start.strftime("%Y%m%d"), end=end.strftime("%Y%m%d"),
        format="JSON", **{"time-standard": "UTC"},
    ).json()
    frame = pd.DataFrame(payload["properties"]["parameter"])[WEATHER_COLUMNS]
    for column in WEATHER_COLUMNS:
        fill = payload["parameters"][column].get("fill_value", payload["header"]["fill_value"])
        frame[column] = pd.to_numeric(frame[column]).replace(fill, np.nan)
    frame.insert(0, "date", pd.to_datetime(frame.index, format="%Y%m%d"))
    frame["region_id"] = region["region_id"]
    return frame.reset_index(drop=True)


def fetch_initial_release(session, series_id, start, end):
    """수정된 최신값 대신 ALFRED 최초 공개값과 공개 날짜를 받는다."""
    key = os.getenv("FRED_API_KEY")
    if not key:
        raise ValueError(".env에 FRED_API_KEY를 설정하세요.")
    parts = []
    # 일별 금리는 공개본이 많아 한 번에 요청하면 API의 2,000개 제한을 넘는다.
    for year in range(start.year, end.year + 1):
        year_start, year_end = max(start, date(year, 1, 1)), min(end, date(year, 12, 31))
        payload = get_response(
            session, "https://api.stlouisfed.org/fred/series/observations",
            api_key=key, series_id=series_id, file_type="json", output_type=4,
            realtime_start=year_start.isoformat(),
            realtime_end=min(end, date(year + 1, 6, 30)).isoformat(),
            observation_start=year_start.isoformat(), observation_end=year_end.isoformat(),
            sort_order="asc", limit=100000,
        ).json()
        part = pd.DataFrame(payload["observations"])
        if part.empty or len(part) != int(payload["count"]):
            raise ValueError(f"{series_id} {year}: 최초 공개값 응답이 비었거나 잘렸습니다.")
        parts.append(part)
    frame = pd.concat(parts, ignore_index=True)
    frame = frame[["date", "realtime_start", "value"]].rename(
        columns={"realtime_start": "release_date"})
    frame["value"] = pd.to_numeric(frame.value.mask(frame.value.eq(".")))
    frame["release_date"] = pd.to_datetime(frame.release_date)
    frame["series_id"] = series_id
    return frame


def fetch_cot(session, start, end):
    parts = []
    for year in range(start.year, end.year + 1):
        print(f"  CFTC {year}", flush=True)
        response = get_response(
            session, f"https://www.cftc.gov/files/dea/history/fut_disagg_txt_{year}.zip",
        )
        with ZipFile(BytesIO(response.content)) as archive:
            members = [n for n in archive.namelist() if n.lower().endswith((".txt", ".csv"))]
            if len(members) != 1:
                raise ValueError(f"CFTC {year}: CSV 파일을 하나로 식별할 수 없습니다.")
            frame = pd.read_csv(BytesIO(archive.read(members[0])), dtype=str, encoding="cp1252")
        frame.columns = frame.columns.str.strip()
        frame = frame.loc[frame.CFTC_Contract_Market_Code.str.strip().eq("083731")].copy()
        date_column = next(c for c in ("Report_Date_as_YYYY-MM-DD", "Report_Date_as_MM_DD_YYYY") if c in frame)
        date_format = "%Y-%m-%d" if date_column.endswith("YYYY-MM-DD") else "%m/%d/%Y"
        mapping = {date_column: "date", **COT_COLUMNS}
        frame = frame[list(mapping)].rename(columns=mapping)
        frame["date"] = pd.to_datetime(frame.date, format=date_format)
        frame["market_code"] = frame.market_code.str.strip()
        for column in list(COT_COLUMNS.values())[2:]:
            frame[column] = pd.to_numeric(frame[column].str.replace(",", "", regex=False))
        if frame.empty:
            raise ValueError(f"CFTC {year}: 커피 데이터가 없습니다.")
        parts.append(frame)
    # date는 포지션 기준일이다. 모델에 붙일 때 발표일을 따로 고려한다.
    return pd.concat(parts, ignore_index=True)


def save_table(frame, path, start, end):
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame.date)
    if frame.date.isna().any():
        raise ValueError("날짜가 비어 있습니다.")
    frame = frame.loc[frame.date.between(pd.Timestamp(start), pd.Timestamp(end))]
    frame = frame.sort_values("date").reset_index(drop=True)
    if frame.empty or frame.date.duplicated().any():
        raise ValueError("받은 구간이 비어 있거나 날짜가 중복됩니다.")
    if np.isinf(frame.select_dtypes(include="number").to_numpy(dtype=float)).any():
        raise ValueError("숫자 열에 무한대가 있습니다.")
    # 가격 결측은 남겨 둔다. 앞뒤 가격으로 채우면 수익률과 평가값이 달라진다.
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".parquet.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    missing = int(frame.isna().sum().sum())
    print(
        f"  저장: {path.name} | {len(frame):,}행 | "
        f"{frame.date.min().date()} ~ {frame.date.max().date()} | 결측 {missing}개",
        flush=True,
    )


def normalize_table(frame):
    """날짜 키로 정렬·중복 제거하고 수치 무한대를 거부한다."""
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame.date).dt.tz_localize(None)
    if frame.date.isna().any():
        raise ValueError("날짜가 비어 있습니다.")
    frame = frame.drop_duplicates("date", keep="last").sort_values("date").reset_index(drop=True)
    if np.isinf(frame.select_dtypes(include="number").to_numpy(dtype=float)).any():
        raise ValueError("숫자 열에 무한대가 있습니다.")
    return frame


def merge_table(existing, incoming):
    """incoming 값이 같은 날짜의 기존 값을 대체한다."""
    if existing is None or existing.empty:
        return normalize_table(incoming)
    return normalize_table(pd.concat([existing, incoming], ignore_index=True))


def last_saved_date(path):
    if not path.exists():
        return None
    dates = pd.read_parquet(path, columns=["date"])["date"]
    return None if dates.empty else pd.Timestamp(dates.max()).date()


def incremental_start(path, fallback_start, overlap_days=0):
    last_date = last_saved_date(path)
    if last_date is None:
        return fallback_start
    return max(fallback_start, last_date + timedelta(days=1 - overlap_days))


def persist_increment(path, incoming):
    existing = pd.read_parquet(path) if path.exists() else None
    merged = merge_table(existing, incoming)
    save_table(merged, path, merged.date.min().date(), merged.date.max().date())
    return merged


def main():
    config = yaml.safe_load((ROOT / "configs" / "sources.yaml").read_text())
    period = config["periods"]["backfill"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", type=date.fromisoformat, default=date.fromisoformat(period["start"]))
    parser.add_argument("--end", type=date.fromisoformat, default=date.fromisoformat(period["end"]), help="마지막 날짜 포함")
    parser.add_argument("--sources", nargs="+", choices=["yahoo", "fred", "nasa", "cftc"],
                        default=["yahoo", "fred", "nasa", "cftc"])
    parser.add_argument("--regions", nargs="+", help="수집할 기상 region_id. 생략하면 6곳 모두 수집")
    parser.add_argument("--weather-buffer-days", type=int, default=WEATHER_BUFFER_DAYS,
                        help="기상 집계용 앞뒤 여유 일수 (기본 30)")
    parser.add_argument("--output", type=Path, help="기본값: data/processed/<시작일>_<종료일>")
    args = parser.parse_args()
    if args.start > args.end:
        parser.error("start는 end보다 늦을 수 없습니다.")
    if args.weather_buffer_days < 0:
        parser.error("weather-buffer-days는 0 이상이어야 합니다.")
    regions = yaml.safe_load((ROOT / "configs" / "regions.yaml").read_text())["regions"]
    if args.regions:
        unknown = set(args.regions) - {region["region_id"] for region in regions}
        if unknown:
            parser.error("알 수 없는 기상 지점: " + ", ".join(sorted(unknown)))
        regions = [region for region in regions if region["region_id"] in args.regions]
    output = args.output or ROOT / "data" / "processed" / f"{args.start}_{args.end}"
    load_dotenv(ROOT / ".env")
    # yfinance 캐시도 프로젝트 안에 둔다.
    yf.set_tz_cache_location(str(ROOT / ".cache" / "yfinance"))
    failures = []
    with build_session() as session:
        jobs = []
        if "yahoo" in args.sources:
            jobs.extend((name, fetch_yahoo, (symbol,)) for name, symbol in
                        [("coffee", "KC=F"), ("brl", "BRL=X")])
        if "fred" in args.sources:
            jobs.extend((series.lower(), fetch_fred, (session, series))
                        for series in ["DFF", "DTWEXBGS", "DCOILWTICO"])
            jobs.extend((f"alfred_{series.lower()}", fetch_initial_release, (session, series))
                        for series in config["fred"]["initial_release_series"])
        if "nasa" in args.sources:
            jobs.extend((f"weather_{r['region_id']}", fetch_weather, (session, r)) for r in regions)
        if "cftc" in args.sources:
            jobs.append(("cot", fetch_cot, (session,)))
        for name, fetch, inputs in jobs:
            print(f"수집: {name}", flush=True)
            try:
                start, end = args.start, args.end
                if name.startswith("weather_"):
                    # 긴 집계도 실제 과거 관측값으로 채운다. 모델 기간은 그대로다.
                    start -= timedelta(days=args.weather_buffer_days)
                    end += timedelta(days=args.weather_buffer_days)
                frame = fetch(*inputs, start, end)
                save_table(frame, output / f"{name}.parquet", start, end)
            except Exception as exc:
                # 실패한 소스 이름과 오류만 남기고 나머지 수집은 계속한다.
                message = str(exc).replace(os.getenv("FRED_API_KEY") or "<no-key>", "<redacted>")
                print(f"  실패: {type(exc).__name__}: {message}", flush=True)
                failures.append(name)
    if failures:
        print("수집 실패: " + ", ".join(failures), flush=True)
        return 1
    print(f"완료: {output}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
