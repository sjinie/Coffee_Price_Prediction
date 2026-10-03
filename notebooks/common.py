"""노트북 공용 설정: 경로, 한글 그래프, 기간 표시, 결과 저장."""
import json
import sys
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))  # 노트북에서 coffee 패키지를 import하기 위해

from coffee.config import DEV_YEARS, FORWARD_START, HOLDOUT_YEARS, TRAIN_START  # noqa: E402

IMAGES = ROOT / "docs" / "images"
RESULTS = Path(__file__).resolve().parent / "results"

plt.rcParams.update({
    "font.family": "AppleGothic", "axes.unicode_minus": False, "figure.dpi": 110,
    "axes.spines.top": False, "axes.spines.right": False, "axes.grid": True, "grid.alpha": 0.3,
})

PERIODS = {
    "개발 구간": (TRAIN_START, pd.Timestamp(f"{DEV_YEARS[-1]}-12-31")),
    "보류 구간": (pd.Timestamp(f"{HOLDOUT_YEARS[0]}-01-01"), pd.Timestamp(f"{HOLDOUT_YEARS[-1]}-12-31")),
    "사후 확인": (FORWARD_START, pd.Timestamp.today()),
}
PERIOD_COLORS = {"개발 구간": "#e8eef7", "보류 구간": "#f6e0e0", "사후 확인": "#e3f1e3"}


def shade_periods(ax):
    for name, (start, end) in PERIODS.items():
        ax.axvspan(start, end, color=PERIOD_COLORS[name], alpha=0.8, lw=0, label=name, zorder=0)


def save_fig(fig, name: str):
    IMAGES.mkdir(parents=True, exist_ok=True)
    fig.savefig(IMAGES / name, bbox_inches="tight", dpi=150)


def save_result(name: str, value):
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / f"{name}.json").write_text(json.dumps(value, indent=2, ensure_ascii=False, default=str) + "\n",
                                          encoding="utf-8")


def load_result(name: str):
    return json.loads((RESULTS / f"{name}.json").read_text(encoding="utf-8"))
