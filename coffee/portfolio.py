"""포트폴리오 화면에 쓰는 연구 자료를 만든다.

API로 바로 읽을 수 없는 것(노트북 05의 뉴스 시차 상관, 원자료 요약, 피처 묶음, 평가 구간, 모델 metadata에 없는
노트북 03b·07 결과, 환율 오류 시세)만 담는다. 예측 방법(피처, 평가 구간, 동결 모델, 뉴스 처리)을 바꾸면
노트북을 다시 실행한 뒤 이것도 다시 실행하고 frontend/src/research.js의 문장도 함께 고친다.

    python -m coffee.portfolio   # → frontend/src/research-data.json
"""
import json

import numpy as np
import pandas as pd

from coffee.config import DEV_YEARS, FORWARD_START, HOLDOUT_YEARS, HORIZONS, ROOT, SETTINGS, TRAIN_START
from coffee.features import EXTRA_GROUPS, FEATURE_GROUPS, NEWS_FEATURES, build_dataset, clean_sources
from coffee.news import daily_news, load_jev_archive
from coffee.sources import load_sources

OUTPUT = ROOT / "frontend" / "src" / "research-data.json"
RESULTS = ROOT / "notebooks" / "results"
BRAZIL = ("br_sul_minas", "br_cerrado", "br_alta_mogiana")
FX_WINDOW = ("2013-01-01", "2016-03-31")  # 페소 환율 오류 시세 17개 가운데 11개가 이 기간에 있다


def news_lag(close: pd.Series, score: pd.Series, max_lag: int = 10) -> dict:
    """거래일 t의 뉴스 점수와 t+k일 일간 로그수익률의 상관. k < 0은 점수보다 앞선 수익률이다(노트북 05와 같은 계산)."""
    daily_return = np.log(close).diff()
    scored = score.notna()
    lags = list(range(-max_lag, max_lag + 1))
    corr = [score[scored].corr(daily_return.shift(-k)[scored]) for k in lags]
    return {"k": lags, "r": [round(float(c), 3) for c in corr], "band": round(float(2 / np.sqrt(scored.sum())), 3)}


def _values(series: pd.Series, digits: int) -> list:
    return [None if pd.isna(v) else round(float(v), digits) for v in series]


def horizon_moves(data: pd.DataFrame) -> dict:
    """h거래일 뒤 가격 변화(%)의 10·50·90% 분위와 10% 넘게 움직인 비율. 모델의 목표 y_h와 같은 거래 세션 달력을
    쓰고, 어느 쪽 종가라도 없는 쌍은 계산하지 않는다(결측을 채우거나 건너뛰어 간격을 늘리지 않는다)."""
    out = {}
    for h in HORIZONS:
        move = np.expm1(data[f"y_{h}"].dropna()) * 100
        q10, q50, q90 = np.percentile(move, [10, 50, 90])
        out[str(h)] = {"q10": round(float(q10), 2), "q50": round(float(q50), 2), "q90": round(float(q90), 2),
                       "big": round(float((move.abs() > 10).mean()), 3), "rows": int(len(move))}
    return out


def notebook_results() -> dict:
    """Why 섹션 그림 가운데 모델 metadata에 없는 노트북 결과. 숫자를 손으로 옮기지 않으려고 결과 파일에서 읽는다."""
    dist = json.loads((RESULTS / "distribution_model.json").read_text(encoding="utf-8"))
    redesign = json.loads((RESULTS / "return_redesign.json").read_text(encoding="utf-8"))
    ladder = pd.DataFrame(dist["ladder"])
    news = pd.DataFrame(dist["leave_one_out"]).query("`뺀 묶음` == '뉴스'").set_index("지평")
    stages = pd.DataFrame(redesign["stages"])
    r = lambda v: round(float(v), 2)
    return {
        # 07 단계별 기여: 무조건 분포에서 시작해 같은 규칙으로 한 단계씩 더했을 때의 B1 대비 CRPS(%)
        "ladder": {"steps": list(dict.fromkeys(ladder["단계"])),
                   **{key: {str(h): [r(v) for v in ladder.loc[ladder["지평"] == h, column]] for h in HORIZONS}
                      for key, column in (("dev", "개발 Test B1 대비 %"), ("holdout", "재사용 확인 B1 대비 %"))}},
        # 07 뉴스만 뺐을 때의 CRPS 변화(%). 개발 구간에는 기사가 없어 담지 않는다.
        "news_ablation": {str(h): {"holdout": r(news.loc[h, "재사용 확인 CRPS 변화 %"]),
                                   "forward": r(news.loc[h, "2026년 CRPS 변화 %"])} for h in HORIZONS},
        # 03b: 03의 수익률 모델과 재설계 마지막 단계의 현재가 유지 대비 개발 구간 RMSE(%), 같은 평가 행
        "return_stages": {str(h): {"before": r(group["개발 Test RMSE%"].iloc[0]), "after": r(group["개발 Test RMSE%"].iloc[-1])}
                          for h, group in stages.groupby("지평")},
        # 07 20거래일 구매 시뮬레이션(정기 구매 대비 %): 서비스 분포 모델의 신호, 무작위, 미래를 아는 경우
        "purchase": [{"period": row["구간"], "model": r(row["모델 신호 %"]), "random": r(row["무작위 %"]),
                      "oracle": r(row["오라클 %"])} for row in dist["purchase"] if row["모델"] == "분포 모델(서비스)"],
    }


def fx_spikes(sources: dict) -> dict:
    """페소 환율 원자료와 이상치 규칙(features.clean_sources)이 바꾼 날. 시행착오 그림에 쓴다."""
    raw = sources["fx_cop"].set_index("date")["close"][FX_WINDOW[0]:FX_WINDOW[1]]
    used = clean_sources(sources)["fx_cop"].set_index("date")["close"][raw.index]
    changed = np.flatnonzero(raw.to_numpy() != used.to_numpy())
    return {"dates": [d.strftime("%Y-%m-%d") for d in raw.index], "raw": _values(raw, 2),
            "replaced": [{"i": int(i), "used": round(float(used.iloc[i]), 2)} for i in changed]}


def build() -> dict:
    sources = load_sources()
    data = build_dataset(sources)
    news = daily_news(load_jev_archive(), data.index, mode="research")

    def observed(name: str, column: str = "value") -> pd.Series:
        return sources[name].set_index("date")[column]

    # 스파크라인은 모양만 보여 주므로 4주마다 한 점, 강수는 월 합계로 줄인다
    weekly = lambda s: s.resample("W-FRI").last().dropna()[str(TRAIN_START.year - 1):]
    rain = pd.concat([observed(f"weather_{r}", "PRECTOTCORR") for r in BRAZIL], axis=1).mean(axis=1)
    return {
        "model_version": SETTINGS["model_version"],
        "periods": {
            "train_start": TRAIN_START.strftime("%Y-%m-%d"),
            "development": [DEV_YEARS[0], DEV_YEARS[-1]],
            "holdout": [HOLDOUT_YEARS[0], HOLDOUT_YEARS[-1]],
            "forward_start": FORWARD_START.strftime("%Y-%m-%d"),
        },
        "feature_groups": {**FEATURE_GROUPS, **EXTRA_GROUPS, "news": NEWS_FEATURES},  # 분포 모델(07)이 넣는 67개
        "news_lag": news_lag(data["close"], news["news_score"]),
        "sparks": {
            "price": _values(weekly(observed("prices", "close")).iloc[::4], 1),
            "brl": _values(weekly(observed("macro_brl")).iloc[::4], 3),
            "rate": _values(weekly(observed("macro_rate")).iloc[::4], 2),
            "rain": _values(rain["2005":].resample("MS").sum(min_count=1), 0),
            "news": _values(news["news_score"]["2022":].resample("W-FRI").mean(), 2),
        },
        "moves": horizon_moves(data),
        "results": notebook_results(),
        "fx_cop": fx_spikes(sources),
    }


if __name__ == "__main__":
    OUTPUT.write_text(json.dumps(build(), ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{OUTPUT.relative_to(ROOT)} 저장")
