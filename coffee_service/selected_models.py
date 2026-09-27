"""03_8에서 선택한 모델의 고정 가중치·조건부 뉴스 feature 추론."""
from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path

import numpy as np
import pandas as pd
import torch

from .features import LOOKBACK, FeatureDataset
from .inference import _future_target_date
from .modeling import DLinear
from .news_residual import SIGNAL_DEFINITION, _cutoff, _prepared
from .transform import REGIONS, coffee_sessions, transform_market, transform_macro, transform_weather

DEFAULT_SELECTED = Path(__file__).resolve().parents[1] / "model_artifacts/selected_news/manifest.json"
SELECTION = {5: (("LightGBM", 300), ("DLinear", 10)),
             20: (("LightGBM", 100), ("XGBoost", 100)), 60: (("DLinear", 30),)}
EXTENSIONS = {"DLinear": ".pt", "LightGBM": ".txt", "XGBoost": ".json"}
MAX_NEWS_DELAY = pd.Timedelta(days=7)


def daily_news(records, sessions, *, availability="live", as_of=None):
    """실제 가용 첫 거래일에 배정하되 7일 넘은 소급 분석은 새 뉴스로 쓰지 않는다."""
    if availability not in {"live", "research"}:
        raise ValueError("Invalid news availability")
    closes = pd.DatetimeIndex([_cutoff(day, None) for day in sessions])
    totals, counts = np.zeros(len(sessions)), np.zeros(len(sessions), dtype=int)
    as_of = pd.Timestamp.now(tz="UTC") if as_of is None else pd.Timestamp(as_of)
    if as_of.tzinfo is None:
        raise ValueError("as_of must include timezone")
    for article in _prepared(records, SIGNAL_DEFINITION):
        visible = article["research_at"]
        if availability == "live":
            visible = article["available"]
            if visible > as_of or visible - article["event"] > MAX_NEWS_DELAY:
                continue
        position = closes.searchsorted(visible)
        if position >= len(closes):
            continue
        totals[position] += article["pressure"] * article["weight"]
        counts[position] += 1
    return pd.DataFrame({"news_sentiment": pd.Series(np.tanh(totals), index=sessions).where(counts > 0),
                         "news_article_count": counts}, index=sessions)


def assemble_selected(sources, cutoff):
    sessions = coffee_sessions(sources["coffee"].date.min(), sources["coffee"].date.max())
    prices, market = transform_market(sources, sessions)
    macro, availability = transform_macro(sources, sessions)
    weather, columns, weather_availability = transform_weather(sources, sessions, cutoff)
    return FeatureDataset(market.join(macro).join(weather), prices, sessions, columns,
                          {**availability, **weather_availability})


def load_weather(sources, source_dir, end):
    for region in REGIONS:
        name = f"weather_{region}"
        frame = pd.read_parquet(Path(source_dir) / f"{name}.parquet")
        frame["date"] = pd.to_datetime(frame.date)
        if frame.date.isna().any() or frame.date.duplicated().any():
            raise ValueError(f"Invalid weather dates: {name}")
        sources[name] = frame.loc[frame.date.le(pd.Timestamp(end))].sort_values("date")
    return sources


class SelectedBundle:
    def __init__(self, path):
        path = Path(path)
        self.manifest = json.loads(path.read_text())
        manifest = self.manifest
        expected = {str(h): [f"{name}@{setting}" for name, setting in members] for h, members in SELECTION.items()}
        if (manifest.get("format_version") != 1 or manifest.get("selection") != expected
                or manifest.get("train_cutoff") != "2025-12-31"
                or manifest.get("news_policy") != "conditional_feature"):
            raise ValueError("Unsupported selected model manifest")
        self.columns = manifest["features"]
        if len(self.columns) != 28 or len(set(self.columns)) != 28 or self.columns[-1] != "news_sentiment":
            raise ValueError("Expected 27 numeric features plus news_sentiment")
        self.components = {}
        for horizon, members in SELECTION.items():
            for name, setting in members:
                for mode, width in (("base", 27), ("news", 28)):
                    stem = f"h{horizon}_{name}_{setting}_{mode}"
                    for suffix in (".npz", EXTENSIONS[name]):
                        file = path.parent / (stem + suffix)
                        if sha256(file.read_bytes()).hexdigest() != manifest["files"].get(file.name):
                            raise ValueError(f"Artifact checksum mismatch: {file.name}")
                    with np.load(path.parent / (stem + ".npz"), allow_pickle=False) as saved:
                        meta = {key: saved[key].copy() for key in saved.files}
                    mean, scale = meta["input_mean"], meta["input_scale"]
                    if (meta["features"].tolist() != self.columns[:width]
                            or mean.shape != (width,) or scale.shape != (width,)
                            or not np.isfinite([mean, scale]).all() or (scale <= 0).any()
                            or not np.isfinite([meta["target_mean"], meta["target_std"]]).all()
                            or meta["target_std"] <= 0 or str(meta["train_cutoff"]) != manifest["train_cutoff"]
                            or int(meta["horizon"]) != horizon or str(meta["model"]) != name
                            or int(meta["setting"]) != setting or bool(meta["use_news"]) != (mode == "news")
                            or (mode == "news" and (mean[-1] != 0 or scale[-1] != 1))):
                        raise ValueError(f"Invalid preprocessing metadata: {stem}")
                    file = path.parent / (stem + EXTENSIONS[name])
                    if name == "DLinear":
                        model = DLinear(width)
                        model.load_state_dict(torch.load(file, map_location="cpu", weights_only=True))
                        if any(not torch.isfinite(value).all() for value in model.state_dict().values()):
                            raise ValueError("Nonfinite DLinear weights")
                        model.eval()
                    elif name == "LightGBM":
                        from lightgbm import Booster
                        model = Booster(model_file=str(file))
                    else:
                        from xgboost import XGBRegressor
                        model = XGBRegressor(n_jobs=1)
                        model.load_model(file)
                    self.components[horizon, name, mode] = (model, meta)

    def model_id(self, horizon):
        names = "-".join(name.lower() for name, _ in SELECTION[horizon])
        return f"{names}-news-h{horizon}-{self.manifest['version']}"

    def predict_component(self, horizon, name, mode, windows):
        model, meta = self.components[horizon, name, mode]
        width = len(meta["features"])
        values = ((windows[:, :, :width] - meta["input_mean"]) / meta["input_scale"]).astype("float32")
        if values.ndim != 3 or values.shape[1:] != (LOOKBACK, width) or not np.isfinite(values).all():
            raise ValueError("Invalid selected model input")
        if name == "DLinear":
            with torch.no_grad():
                output = np.concatenate([model(torch.from_numpy(values[i:i+64])).numpy()
                                         for i in range(0, len(values), 64)])
        else:
            columns = [f"lag_{lag}_{column}" for lag in range(LOOKBACK-1, -1, -1) for column in meta["features"]]
            flat = pd.DataFrame(values.reshape(len(values), -1), columns=columns)
            output = model.predict(flat, num_threads=1) if name == "LightGBM" else model.predict(flat)
        return output * float(meta["target_std"]) + float(meta["target_mean"])

    def predict(self, horizon, windows):
        if windows.ndim != 3 or windows.shape[1:] != (LOOKBACK, 28):
            raise ValueError("Expected (n, 60, 28) windows")
        used = windows[:, -1, -1] != 0
        members = []
        for name, _ in SELECTION[horizon]:
            base = self.predict_component(horizon, name, "base", windows)
            if used.any():
                base[used] = self.predict_component(horizon, name, "news", windows[used])
            members.append(base)
        # Average member prices, then convert back to cumulative log return.
        result = np.logaddexp.reduce(np.stack(members), axis=0) - np.log(len(members))
        if not np.isfinite(result).all():
            raise ValueError("Nonfinite selected prediction")
        return result

    def model_records(self):
        return [{"model_id": self.model_id(h), "name": " + ".join(n for n, _ in members) + " + 뉴스",
                 "horizons": [h], "feature_columns": self.columns,
                 "training_start": "2022-01-01", "training_end": self.manifest["train_cutoff"],
                 "trained_at": self.manifest["exported_at"],
                 "metrics": {"news_policy": "conditional_feature", "news_availability": "live",
                             "training_news_availability": "research", "selection_source": "03_8",
                             "evaluation_note": "2026 과거 재생: 미사용 Test 아님; 뉴스는 실제 가용 시각 적용",
                             "components": [{"name": n, "setting": s, "weight": 1/len(members)} for n, s in members]}}
                for h, members in SELECTION.items()]


def selected_predictions(dataset, bundle, news):
    features = dataset.features[bundle.columns[:-1]].copy()
    valid = features.notna().all(axis=1).rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
    indices = np.flatnonzero(valid & dataset.prices.close.gt(0)
                            & (dataset.sessions > pd.Timestamp(bundle.manifest["train_cutoff"])))
    if not len(indices):
        raise ValueError("No complete post-training windows")
    features["news_sentiment"] = news.news_sentiment.reindex(features.index).fillna(0)
    values = features.to_numpy(dtype=float)
    windows = np.stack([values[i-LOOKBACK+1:i+1] for i in indices])
    rows = []
    for horizon in SELECTION:
        predicted = bundle.predict(horizon, windows)
        for i, result in zip(indices, predicted):
            origin = dataset.sessions[i]
            target = dataset.sessions[i+horizon] if i+horizon < len(dataset.sessions) else _future_target_date(origin, horizon)
            actual = dataset.prices.close.get(target, np.nan)
            score = float(features.iloc[i].news_sentiment)
            price = float(dataset.prices.close.iloc[i] * np.exp(result))
            if not np.isfinite(price) or price <= 0:
                raise ValueError("Invalid reconstructed price")
            rows.append({"model_id": bundle.model_id(horizon), "horizon": horizon,
                         "origin_date": origin.date(), "target_date": target.date(),
                         "predicted_return": float(result), "predicted_price": price,
                         "actual_price": None if pd.isna(actual) else float(actual),
                         "final_direction": "UP" if result > 0 else "DOWN" if result < 0 else "FLAT",
                         "news_impact_score": score, "news_article_count": int(news.loc[origin, "news_article_count"]),
                         "signal_status": "news_feature" if score != 0 else "numeric_fallback",
                         "model_version": bundle.model_id(horizon)})
    return pd.DataFrame(rows).sort_values(["origin_date", "horizon"]).reset_index(drop=True)
