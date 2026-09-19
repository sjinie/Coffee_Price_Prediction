"""가격 예측 위에 시점 안전 방향 신호를 더하는 작은 실험용 모델 묶음."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from uuid import uuid4
from catboost import CatBoostClassifier, CatBoostRegressor
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.dummy import DummyClassifier, DummyRegressor
from sklearn.linear_model import LogisticRegression, Ridge
from sklearn.metrics import (accuracy_score, balanced_accuracy_score, brier_score_loss,
                             confusion_matrix, f1_score, precision_score, recall_score,
                             roc_auc_score)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from .features import LOOKBACK, PRODUCTION_FEATURES, FeatureDataset, make_targets
from .news import ANALYSIS_VERSION, aggregate_news, news_feature_columns, read_news_frame


HORIZONS = (5, 20, 60)
NEWS_WINDOWS = {5: (1, 3, 7), 20: (7, 14, 30), 60: (14, 30, 60)}
VERSION = "v1"
DEFAULT_ARTIFACT = Path("model_artifacts/news_intelligence_v1.joblib")


def _model_pair(name):
    if name == "logistic_ridge":
        return (make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, random_state=42)),
                make_pipeline(StandardScaler(), Ridge(alpha=100)))
    if name == "catboost":
        common = dict(iterations=150, depth=4, learning_rate=.03, l2_leaf_reg=5, random_seed=42, thread_count=1, verbose=False, allow_writing_files=False)
        return CatBoostClassifier(**common), CatBoostRegressor(**common)
    if name == "lightgbm":
        common = dict(n_estimators=150, num_leaves=15, max_depth=4, learning_rate=.03, random_state=42, n_jobs=1, verbosity=-1)
        return LGBMClassifier(**common), LGBMRegressor(**common)
    raise ValueError(f"unknown model: {name}")


def metric_summary(y, probability, threshold=.5):
    """Binary metrics that remain usable when validation has one class."""
    y = np.asarray(y)
    probability = np.asarray(probability, dtype=float)
    if y.ndim != 1 or probability.ndim != 1 or len(y) != len(probability):
        raise ValueError("target and probability lengths differ")
    if not np.isfinite(probability).all() or ((probability < 0) | (probability > 1)).any():
        raise ValueError("probability must be finite and between zero and one")
    if not np.isin(y, (0, 1)).all():
        raise ValueError("binary target must contain only zero and one")
    y = y.astype(int)
    predicted = probability >= threshold
    tn, fp, fn, tp = confusion_matrix(y, predicted, labels=[0, 1]).ravel()
    return {"accuracy": float(accuracy_score(y, predicted)), "balanced_accuracy": float(balanced_accuracy_score(y, predicted)),
            "precision": float(precision_score(y, predicted, zero_division=0)), "recall": float(recall_score(y, predicted, zero_division=0)),
            "f1": float(f1_score(y, predicted, zero_division=0)), "auc": None if len(np.unique(y)) < 2 else float(roc_auc_score(y, probability)),
            "tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp), "brier": float(brier_score_loss(y, probability))}


def _fit_classifier(model, x, y):
    if len(np.unique(y)) < 2:
        model = DummyClassifier(strategy="constant", constant=int(y[0]))
    return model.fit(x, y)


def _fit_regressor(model, x, y):
    return DummyRegressor(strategy="mean").fit(x, y) if np.ptp(y) == 0 else model.fit(x, y)


def fixed_ensemble(regression, probability, train_return_std, news_impact=0.0, candidate=1):
    """The three approved fixed combinations; no fitted meta-model is introduced."""
    regression = np.asarray(regression, dtype=float)
    probability = np.asarray(probability, dtype=float)
    news_impact = np.asarray(news_impact, dtype=float)
    if regression.ndim != 1 or probability.ndim != 1 or len(regression) != len(probability):
        raise ValueError("regression and probability must be same-length vectors")
    if news_impact.ndim > 1 or (news_impact.size not in (1, len(regression))):
        raise ValueError("news impact must be scalar or align with regression")
    if not np.isfinite(regression).all() or not np.isfinite(probability).all() or not np.isfinite(news_impact).all():
        raise ValueError("ensemble inputs must be finite")
    if candidate == 1:
        return regression
    if candidate == 2:
        return np.abs(regression) * np.sign(probability - .5)
    if candidate == 3:
        return .5 * regression + .5 * train_return_std * (2 * probability - 1) + .1 * train_return_std * news_impact
    raise ValueError("candidate must be 1, 2, or 3")


def feature_frame(dataset: FeatureDataset, articles=None, horizon=5, news=False, news_window=None, collected_bound=None):
    """Use complete numeric 60-session histories; missing news remains a valid zero signal."""
    numeric = dataset.features.loc[:, PRODUCTION_FEATURES].copy()
    complete = pd.DataFrame(np.isfinite(numeric), index=numeric.index, columns=numeric.columns).all(axis=1).rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
    result = numeric.loc[complete].copy()
    if news:
        if news_window not in NEWS_WINDOWS[horizon]:
            raise ValueError(f"invalid news window for h{horizon}: {news_window}")
        columns = news_feature_columns(news_window)
        values = aggregate_news(articles, dataset.sessions, (news_window,),
                                collected_bound=collected_bound).reindex(dataset.features.index).fillna(0.0)
        result = result.join(values.loc[result.index, columns])
    return result


def _probability(model, x):
    if hasattr(model, "predict_proba"):
        probabilities = model.predict_proba(x)
        classes = list(model.classes_)
        return probabilities[:, classes.index(1)] if 1 in classes else np.zeros(len(x))
    raise TypeError("classifier does not expose probabilities")


def train_models(dataset, articles=None, training_start="2015-01-01", training_end="2023-12-31", model_names=("logistic_ridge", "catboost", "lightgbm"), feature_frames=None):
    """Train each numeric/news pair through the supplied target-date cutoff."""
    targets = make_targets(dataset.prices["close"])
    output = {}
    for horizon in HORIZONS:
        output[horizon] = {}
        target = targets[f"y_{horizon}"]
        target_date = targets[f"target_date_{horizon}"]
        feature_sets = [("numeric", False, None)] + [(f"news_{window}d", True, window) for window in NEWS_WINDOWS[horizon]]
        for feature_set, include_news, news_window in feature_sets:
            x = (feature_frames or {}).get((horizon, feature_set))
            if x is None:
                x = feature_frame(dataset, articles, horizon, include_news, news_window)
            rows = x.index[(x.index >= pd.Timestamp(training_start)) & (target_date.loc[x.index] <= pd.Timestamp(training_end)) & target.loc[x.index].notna()]
            if not len(rows):
                raise ValueError(f"no training rows for h{horizon}/{feature_set}")
            y_return = target.loc[rows].to_numpy(float)
            nonzero = np.isfinite(y_return) & (y_return != 0)
            if not nonzero.any():
                raise ValueError(f"no non-zero direction targets for h{horizon}/{feature_set}")
            direction_target = y_return[nonzero] > 0
            entry = {"columns": list(x.columns), "window": news_window,
                     "train_return_std": float(np.std(y_return)), "train_std": float(np.std(y_return)),
                     "train_prior": float(direction_target.mean()), "training_rows": int(len(rows)),
                     "train_target_max": str(pd.Timestamp(target_date.loc[rows].max()).date()), "models": {}}
            for name in model_names:
                classifier, regressor = _model_pair(name)
                classifier = _fit_classifier(classifier, x.loc[rows[nonzero]], direction_target.astype(int))
                regressor = _fit_regressor(regressor, x.loc[rows], y_return)
                entry["models"][name] = {"classifier": classifier, "regressor": regressor}
            output[horizon][feature_set] = entry
    return output


def _aligned_base(base_predictions, horizon, index):
    required = {"origin_date", "horizon", "predicted_return", "predicted_price"}
    if not required <= set(base_predictions):
        raise ValueError(f"base predictions miss columns: {sorted(required - set(base_predictions))}")
    base = base_predictions.loc[base_predictions.horizon.eq(horizon)].copy()
    base.index = pd.to_datetime(base.origin_date)
    if base.index.has_duplicates:
        raise ValueError(f"duplicate base prediction origins for h{horizon}")
    if not base.index.equals(pd.DatetimeIndex(index)):
        raise ValueError(f"base prediction origins do not match feature origins for h{horizon}")
    return base


def _selection_gate(folds, horizon, task):
    if len(folds) != 3 or any(not fold.get("valid", True) for fold in folds):
        return False
    if task == "classifier":
        scores = [row["balanced_accuracy"] for row in folds]
        majority = [row["baseline_balanced_accuracy"] for row in folds]
        return np.mean(scores) >= .52 and sum(a > b for a, b in zip(scores, majority)) >= 2 and np.mean([row["brier"] for row in folds]) <= np.mean([row["train_prior_brier"] for row in folds])
    improvements = [row["relative_rmse_improvement"] for row in folds]
    rmse_ok = np.mean(improvements) >= .01 and sum(value > 0 for value in improvements) >= 2 and min(improvements) >= -.05
    if horizon not in (5, 20):
        return rmse_ok
    direction = [row["direction_balanced_accuracy"] for row in folds]
    return rmse_ok and np.mean(direction) >= .52 and sum(value > .5 for value in direction) >= 2


def _choose_selection(metrics, horizon):
    """Choose only validation winners; a news option must beat its exact numeric pair."""
    selected = {"regression": "base", "classifier": "numeric", "reason": "original baseline retained"}
    for task in ("regression", "classifier"):
        key = "relative_rmse_improvement" if task == "regression" else "balanced_accuracy"
        candidates = [item for item in metrics if item["task"] == task and _selection_gate(item["folds"], horizon, task)]
        if not candidates:
            continue
        all_numeric = [item for item in metrics if item["task"] == task and item["feature_set"] == "numeric"]
        numeric = [item for item in candidates if item["feature_set"] == "numeric"]
        best = max(numeric, key=lambda item: np.mean([fold[key] for fold in item["folds"]]), default=None)
        news = [item for item in candidates if item["feature_set"].startswith("news_")]
        approved_news = []
        for news_best in news:
            peer = next((item for item in all_numeric if item["name"] == news_best["name"] and item.get("candidate") == news_best.get("candidate")), None)
            if peer is None:
                continue
            news_values = [fold[key] for fold in news_best["folds"]]
            peer_values = [fold[key] for fold in peer["folds"]]
            if np.mean(news_values) > np.mean(peer_values) and sum(a > b for a, b in zip(news_values, peer_values)) >= 2:
                approved_news.append(news_best)
        if approved_news:
            best = max([candidate for candidate in (best, *approved_news) if candidate], key=lambda item: np.mean([fold[key] for fold in item["folds"]]))
        if best:
            selected[task] = {"name": best["name"], "feature_set": best["feature_set"], "candidate": best.get("candidate", 1)}
            selected["reason"] = "validation gate passed"
    return selected


def _prune_models(models, selections):
    """Copy only the fitted pairs that serving can reach; leave experiment fits intact."""
    pruned = {}
    for horizon in HORIZONS:
        selection = selections.get(horizon, selections.get(str(horizon)))
        if not isinstance(selection, dict):
            raise ValueError(f"missing selection for h{horizon}")
        required = []
        for choice_key in ("classifier", "numeric_classifier", "regression"):
            choice = selection.get(choice_key)
            if isinstance(choice, dict):
                required.append((choice.get("feature_set", "numeric"), choice.get("name")))
        pruned[horizon] = {}
        for feature_set, name in required:
            if feature_set not in models[horizon] or name not in models[horizon][feature_set]["models"]:
                raise ValueError(f"missing selected model for h{horizon}/{feature_set}/{name}")
            if feature_set not in pruned[horizon]:
                entry = models[horizon][feature_set]
                pruned[horizon][feature_set] = {key: value for key, value in entry.items() if key != "models"}
                pruned[horizon][feature_set]["models"] = {}
            pruned[horizon][feature_set]["models"][name] = models[horizon][feature_set]["models"][name]
        if "numeric" not in pruned[horizon]:
            entry = models[horizon]["numeric"]
            pruned[horizon]["numeric"] = {key: value for key, value in entry.items() if key != "models"}
            pruned[horizon]["numeric"]["models"] = {}
    return pruned


def make_bundle(dataset, articles=None, *, training_start="2015-01-01", training_end="2023-12-31", selections=None, validation_metrics=None, models=None, feature_frames=None):
    models = train_models(dataset, articles, training_start, training_end, feature_frames=feature_frames) if models is None else models
    default = {h: {"regression": "base", "classifier": {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1}, "numeric_classifier": {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1}, "classifier_validated": False, "reason": "experimental classifier"} for h in HORIZONS}
    selections = selections or default
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid4().hex[:8]
    return {"version": VERSION, "analysis_version": ANALYSIS_VERSION, "run_id": run_id, "training_start": str(training_start), "training_cutoff": str(training_end), "trained_at": datetime.now(timezone.utc).isoformat(), "models": _prune_models(models, selections), "selection": selections, "validation_metrics": validation_metrics or {}}


def save_intelligence(bundle, path):
    path = Path(path)
    if path.exists():
        raise FileExistsError(f"refusing to overwrite intelligence artifact: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    try:
        joblib.dump(bundle, temporary)
        if path.exists():
            raise FileExistsError(f"refusing to overwrite intelligence artifact: {path}")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def load_intelligence(path):
    """Load only a locally trusted joblib artifact; joblib must not receive untrusted files."""
    bundle = joblib.load(path)
    if not isinstance(bundle, dict) or bundle.get("version") != VERSION or bundle.get("analysis_version") != ANALYSIS_VERSION or not bundle.get("run_id"):
        raise ValueError("unsupported intelligence artifact")
    if set(bundle.get("models", {})) != set(HORIZONS):
        raise ValueError("artifact does not contain every horizon")
    for horizon in HORIZONS:
        if "numeric" not in bundle["models"][horizon] or horizon not in bundle.get("selection", {}):
            raise ValueError(f"artifact contract is incomplete for h{horizon}")
    return bundle


def _display_news(articles, origins, horizon, collected_bound, available):
    if not available:
        return pd.DataFrame(index=origins, columns=["impact", "count", "updated_at"])
    window = NEWS_WINDOWS[horizon][-1]
    values = aggregate_news(articles, origins, (window,), collected_bound=collected_bound)
    frame = read_news_frame(articles) if articles is not None else None
    rows = []
    for origin in origins:
        asof = pd.Timestamp(origin, tz="UTC") + pd.Timedelta(hours=23)
        visible = frame.loc[(frame.available_at <= asof) & (frame.published_at <= asof)] if frame is not None else frame
        if visible is not None and collected_bound is not None:
            visible = visible.loc[visible.collected_at <= min(collected_bound, asof)]
        rows.append({"impact": values.loc[origin, f"news_weighted_impact_{window}d"],
                     "count": values.loc[origin, f"news_relevant_count_{window}d"],
                     "updated_at": None if visible is None or visible.empty else visible.collected_at.max()})
    return pd.DataFrame(rows, index=origins)


def predict_intelligence(dataset, base_predictions, articles, bundle, news_available=True, availability_mode="live"):
    """Enrich aligned base forecasts without changing rejected regression prices."""
    if availability_mode not in {"live", "historical"}:
        raise ValueError("availability_mode must be live or historical")
    cutoff = pd.Timestamp(bundle["training_cutoff"])
    collected_bound = pd.Timestamp.now(tz="UTC") if availability_mode == "live" else None
    first_collected = None
    if news_available and availability_mode == "live" and articles is not None:
        frame = read_news_frame(articles)
        if frame.empty:
            first_collected = articles.attrs.get("last_collected_at")
        else:
            first_collected = frame.collected_at.min()
        if first_collected is not None:
            first_collected = pd.Timestamp(first_collected)
            if first_collected.tzinfo is None:
                raise ValueError("last_collected_at must include a timezone")
            first_collected = first_collected.tz_convert("UTC")
    rows = []
    for horizon in HORIZONS:
        base = base_predictions.loc[base_predictions.horizon.eq(horizon)].copy()
        base.index = pd.to_datetime(base.origin_date)
        base = _aligned_base(base_predictions, horizon, base.index)
        if base.index.has_duplicates:
            raise ValueError(f"duplicate base prediction origins for h{horizon}")
        if (base.index <= cutoff).any():
            raise ValueError(f"base prediction origin is not after training cutoff for h{horizon}")
        if not len(base):
            continue
        if not base.index.isin(dataset.features.index).all():
            raise ValueError(f"base prediction origins do not match dataset for h{horizon}")
        if not np.isfinite(base[["predicted_return", "predicted_price"]].to_numpy(float)).all() or (base.predicted_price <= 0).any():
            raise ValueError(f"base predictions must have finite positive prices for h{horizon}")
        if "target_date" not in base or (pd.to_datetime(base.target_date) <= base.index).any():
            raise ValueError(f"base prediction target dates must follow origins for h{horizon}")
        origins = base.index
        origin_times = pd.DatetimeIndex(origins)
        origin_times = origin_times.tz_localize("UTC") if origin_times.tz is None else origin_times.tz_convert("UTC")
        live_unknown = pd.Series(False, index=origins)
        if first_collected is not None:
            live_unknown = pd.Series(origin_times.normalize() + pd.Timedelta(hours=23) < first_collected, index=origins)
        display_news = _display_news(articles, origins, horizon, collected_bound, news_available)
        choice = bundle["selection"].get(horizon, bundle["selection"].get(str(horizon), {}))
        classifier_choice = choice.get("classifier", {"name": "logistic_ridge", "feature_set": "numeric"})
        if not isinstance(classifier_choice, dict):
            classifier_choice = {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1}
        requested_set = classifier_choice.get("feature_set", "numeric")
        validated = bool(choice.get("classifier_validated", False))
        fallback_choice = choice.get("numeric_classifier")
        if fallback_choice is None and classifier_choice.get("feature_set") == "numeric":
            fallback_choice = classifier_choice
        active_choice = classifier_choice if news_available else fallback_choice
        active_set = active_choice.get("feature_set", "numeric") if isinstance(active_choice, dict) else "numeric"
        news_window = int(active_set.split("_")[1][:-1]) if active_set.startswith("news_") else None
        x = feature_frame(dataset, articles if news_available else None, horizon,
                          active_set != "numeric", news_window, collected_bound).reindex(origins)
        model_entry = bundle["models"][horizon][active_set]
        pair = model_entry["models"][active_choice["name"]] if isinstance(active_choice, dict) else None
        valid = pd.DataFrame(np.isfinite(x), index=x.index, columns=x.columns).all(axis=1)
        probability = pd.Series(np.nan, index=origins)
        if pair is not None and valid.any():
            probability.loc[valid] = _probability(pair["classifier"], x.loc[valid])
        used_numeric_fallback = pd.Series(False, index=origins)
        if live_unknown.any() and requested_set.startswith("news_"):
            if live_unknown.any():
                numeric_choice = fallback_choice
                if isinstance(numeric_choice, dict):
                    numeric_x = feature_frame(dataset, None, horizon).reindex(origins[live_unknown])
                    numeric_valid = pd.DataFrame(np.isfinite(numeric_x), index=numeric_x.index, columns=numeric_x.columns).all(axis=1)
                    if numeric_valid.any():
                        numeric_pair = bundle["models"][horizon]["numeric"]["models"][numeric_choice["name"]]
                        probability.loc[numeric_x.index[numeric_valid]] = _probability(numeric_pair["classifier"], numeric_x.loc[numeric_valid])
                        used_numeric_fallback.loc[numeric_x.index[numeric_valid]] = True
                probability.loc[live_unknown & ~used_numeric_fallback] = np.nan
        regression_choice = choice.get("regression", "base")
        regression = None
        if isinstance(regression_choice, dict):
            if not news_available and regression_choice["feature_set"].startswith("news_"):
                regression_choice = "base"
            if isinstance(regression_choice, dict):
                regression_set = regression_choice["feature_set"]
            else:
                regression_set = None
        if isinstance(regression_choice, dict):
            regression_window = int(regression_set.split("_")[1][:-1]) if regression_set.startswith("news_") else None
            regression_x = feature_frame(dataset, articles if news_available else None, horizon,
                                         regression_set != "numeric", regression_window, collected_bound).reindex(origins)
            regression_valid = pd.DataFrame(np.isfinite(regression_x), index=regression_x.index, columns=regression_x.columns).all(axis=1)
            regression = pd.Series(np.nan, index=origins)
            if regression_valid.any():
                regression_pair = bundle["models"][horizon][regression_set]["models"][regression_choice["name"]]
                regression_probability = _probability(regression_pair["classifier"], regression_x.loc[regression_valid])
                regression_values = fixed_ensemble(regression_pair["regressor"].predict(regression_x.loc[regression_valid]), regression_probability,
                                                    bundle["models"][horizon][regression_set]["train_return_std"],
                                                    regression_x.loc[regression_valid, f"news_weighted_impact_{regression_window}d"] if regression_window else 0., regression_choice.get("candidate", 1))
                regression.loc[regression_valid] = regression_values
            if first_collected is not None and availability_mode == "live" and regression_set.startswith("news_"):
                regression.loc[live_unknown] = np.nan
        for origin, base_row in base.iterrows():
            probability_up = probability.loc[origin]
            served_return = base_row.predicted_return if regression is None or pd.isna(regression.loc[origin]) else float(regression.loc[origin])
            direction = "UP" if served_return > 0 else "DOWN" if served_return < 0 else "FLAT"
            served_price = base_row.predicted_price if regression is None or pd.isna(regression.loc[origin]) else float(base_row.predicted_price * np.exp(served_return - base_row.predicted_return))
            status = "unavailable" if pd.isna(probability_up) else "fallback" if used_numeric_fallback.loc[origin] or not news_available else "validated" if validated else "experimental"
            display = display_news.loc[origin]
            row_choice = fallback_choice if used_numeric_fallback.loc[origin] else active_choice
            row_set = row_choice.get("feature_set", "numeric") if isinstance(row_choice, dict) else active_set
            if live_unknown.loc[origin]:
                display_impact, display_count, display_updated_at = np.nan, np.nan, None
            else:
                display_impact, display_count, display_updated_at = display.impact, display["count"], display.updated_at
            rows.append({**base_row.to_dict(), "origin_date": origin.date(), "predicted_return": served_return, "predicted_price": served_price, "model_id": f"intelligence-h{horizon}-{bundle['run_id']}-{availability_mode}", "probability_up": probability_up, "final_direction": direction, "news_impact_score": display_impact, "news_article_count": display_count, "news_updated_at": display_updated_at, "signal_status": status, "classifier_version": f"{row_choice['name']}-{row_set}-{bundle['run_id']}" if isinstance(row_choice, dict) else None, "model_version": bundle["run_id"]})
    return pd.DataFrame(rows)


def model_records(bundle, availability_mode="live", base_bundle=None):
    if availability_mode not in {"live", "historical"}:
        raise ValueError("availability_mode must be live or historical")
    def classifier_name(choice):
        return choice.get("name", "unknown") if isinstance(choice, dict) else "unavailable"

    def record_name(horizon, selection):
        regression = selection.get("regression", "base")
        classifier = classifier_name(selection.get("classifier"))
        if isinstance(regression, dict):
            price_name = f"Ensemble C{regression.get('candidate', 1)} {regression.get('name', 'unknown')}"
        else:
            price_name = "Persistence" if horizon in (5, 20) else "DLinear"
        return f"{price_name} + {classifier} (P(up))"

    records = []
    for horizon, selection in bundle["selection"].items():
        horizon = int(horizon)
        classifier = selection.get("classifier", {})
        if not isinstance(classifier, dict):
            continue
        feature_set = classifier.get("feature_set", "numeric")
        entry = bundle["models"][horizon][feature_set]
        metrics = dict(bundle.get("validation_metrics", {}).get(horizon, {}))
        if horizon == 60 and selection.get("regression", "base") == "base" and base_bundle is not None:
            metrics.update(base_bundle.metadata.get("notebook_metrics", {}))
            metrics["baseline_model_id"] = base_bundle.metadata["model_id"]
        metrics["news_availability"] = availability_mode
        records.append({"model_id": f"intelligence-h{horizon}-{bundle['run_id']}-{availability_mode}", "name": record_name(horizon, selection), "horizons": [horizon], "feature_columns": entry["columns"], "metrics": metrics, "training_start": bundle["training_start"], "training_end": bundle["training_cutoff"], "trained_at": bundle["trained_at"]})
    return records
