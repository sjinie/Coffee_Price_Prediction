"""설정 파일과 프로젝트 경로."""
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = yaml.safe_load((ROOT / "configs" / "settings.yaml").read_text(encoding="utf-8"))

DATA_DIR = ROOT / "data"
SOURCES_DIR = DATA_DIR / "sources"   # 수집한 가격·거시·기상 Parquet
NEWS_DIR = DATA_DIR / "news"         # 선정 기사와 Jev 분석 결과
ARTIFACTS_DIR = ROOT / "model_artifacts"
NORMALS_FILE = ROOT / "configs" / "weather_normals.csv"

HORIZONS = tuple(SETTINGS["horizons"])
REGIONS = SETTINGS["weather"]["regions"]
REGION_IDS = tuple(region["id"] for region in REGIONS)
MACRO_SERIES = SETTINGS["macro"]


def period(name: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    start, end = SETTINGS["periods"][name]
    return pd.Timestamp(start), pd.Timestamp(end)
