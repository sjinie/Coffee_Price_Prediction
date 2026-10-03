"""설정 파일과 프로젝트 경로."""
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = yaml.safe_load((ROOT / "configs" / "settings.yaml").read_text(encoding="utf-8"))

DATA_DIR = ROOT / "data"
SOURCES_DIR = DATA_DIR / "sources"   # 수집한 가격·거시·기상 Parquet
ARTIFACTS_DIR = ROOT / "model_artifacts"

HORIZONS = tuple(SETTINGS["horizons"])
VOL_HORIZONS = (20, 60)              # 변동성은 한 달·석 달 지평만 예측한다
REGIONS = SETTINGS["weather"]["regions"]
REGION_IDS = tuple(region["id"] for region in REGIONS)
MACRO_SERIES = SETTINGS["macro"]

_periods = SETTINGS["periods"]
TRAIN_START = pd.Timestamp(_periods["train_start"])
DEV_YEARS = tuple(range(_periods["development"][0], _periods["development"][1] + 1))
HOLDOUT_YEARS = tuple(range(_periods["holdout"][0], _periods["holdout"][1] + 1))
FORWARD_START = pd.Timestamp(_periods["forward_start"])
