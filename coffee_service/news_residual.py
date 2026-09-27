"""Time-safe Jev news signals and an exploratory, direction-preserving correction.

The fitted quantity is a bounded *strength*, never a free signed price effect.
News direction stays in the article-derived signal; this module only estimates how
much of that signal to apply.
"""
from __future__ import annotations

import json
import math
import os
from hashlib import sha256
from pathlib import Path
from tempfile import NamedTemporaryFile

import numpy as np
import pandas as pd

VERSION = "news-residual-v3"
SIGNAL_DEFINITION = "argmax_direction_times_relevance_confidence"
LEGACY_SIGNAL_DEFINITION = "probability_margin_times_relevance"
HORIZONS = (5, 20, 60)
LAGS = (0, 1, 3, 5)
HALF_LIFE = 3
TAU = 10
MIN_CALIBRATION_ROWS = 6
GRID = np.linspace(0.0, 1.0, 1001)
LAG_WEIGHTS = np.asarray([2.0 ** (-lag / HALF_LIFE) for lag in LAGS], float)
LAG_WEIGHTS /= LAG_WEIGHTS.sum()


def _utc(value, name):
    stamp = pd.Timestamp(value)
    if pd.isna(stamp) or stamp.tzinfo is None:
        raise ValueError(f"{name} must be a timezone-aware timestamp")
    return stamp.tz_convert("UTC")


def _sessions(sessions):
    index = pd.DatetimeIndex(pd.to_datetime(sessions, errors="raise")).normalize()
    if not len(index) or index.has_duplicates or not index.is_monotonic_increasing:
        raise ValueError("sessions must be non-empty, sorted, and unique")
    return index


def _cutoff(day, as_of):
    cutoff = pd.Timestamp(day, tz="UTC") + pd.Timedelta(hours=23)
    return min(cutoff, as_of) if as_of is not None else cutoff


def _record_times(record):
    event = _utc(record.get("event_at"), "event_at")
    required = [event]
    for field in ("collected_at", "analyzed_at"):
        if record.get(field) is not None:
            required.append(_utc(record[field], field))
    available = _utc(record["available_at"], "available_at") if record.get("available_at") is not None else max(required)
    if available < max(required):
        raise ValueError("available_at must not precede event_at")
    return event, available


def _identity(record):
    for field in ("analysis_id", "article_id", "url"):
        if record.get(field):
            return field, str(record[field]).strip()
    title = str(record.get("title") or "").strip().casefold()
    if not title:
        raise ValueError("record needs analysis_id, article_id, url, or title")
    return "content", str(record.get("source") or "").casefold(), title


def _article_id(record):
    return str(record.get("analysis_id") or record.get("article_id") or record.get("url") or _identity(record)[-1])


def _prepared(records, signal_definition=SIGNAL_DEFINITION):
    if signal_definition not in {SIGNAL_DEFINITION, LEGACY_SIGNAL_DEFINITION}:
        raise ValueError("unsupported news signal definition")
    output, seen_ids, seen_content = [], set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("records must contain dictionaries")
        event, available = _record_times(record)
        values = [record.get(key) for key in ("p_bullish", "p_bearish", "p_neutral", "p_uncertain")]
        if any(value is None for value in values):
            raise ValueError("record misses Jev choice probabilities")
        if signal_definition == SIGNAL_DEFINITION and any(record.get(key) is None for key in ("relevance", "confidence")):
            raise ValueError("record misses relevance or confidence")
        probabilities = np.asarray(values, float)
        relevance, confidence = float(record.get("relevance", 1)), float(record.get("confidence", 1))
        if (not np.isfinite(probabilities).all() or (probabilities < 0).any() or (probabilities > 1).any()
                or not np.isclose(probabilities.sum(), 1, atol=.02)
                or not np.isfinite([relevance, confidence]).all() or not 0 <= relevance <= 1 or not 0 <= confidence <= 1):
            raise ValueError("record probabilities, relevance, and confidence must be finite and valid")
        identity = _identity(record)
        content = (str(record.get("title") or "").strip().casefold(), str(record.get("summary") or "").strip().casefold())
        if identity in seen_ids or (content[0] and content in seen_content):
            continue
        seen_ids.add(identity)
        if content[0]:
            seen_content.add(content)
        selection = _utc(record.get("selection_available_at") or event, "selection_available_at")
        modified = _utc(record.get("modified_at") or event, "modified_at")
        winners = np.isclose(probabilities, probabilities.max(), atol=1e-8, rtol=0)
        direction = (1 if winners.sum() == 1 and winners[0] else
                     -1 if winners.sum() == 1 and winners[1] else 0)
        output.append({"id": _article_id(record), "event": event,
                       "available": max(available, selection, modified),
                       "research_at": max(event, selection, modified),
                       "weight": relevance * (confidence if signal_definition == SIGNAL_DEFINITION else 1),
                       "pressure": direction if signal_definition == SIGNAL_DEFINITION else float(probabilities[0] - probabilities[1])})
    return sorted(output, key=lambda row: (row["event"], row["available"], row["id"]))


def _daily_signal(prepared, cutoff, availability, half_life):
    total, ids = 0.0, []
    for record in prepared:
        visible = record["available"] if availability == "live" else record["research_at"]
        if record["event"] > cutoff or visible > cutoff:
            continue
        age = (cutoff - record["event"]).total_seconds() / 86400
        contribution = record["weight"] * record["pressure"] * 2 ** (-age / half_life)
        total += contribution
        if abs(contribution) >= 1e-4:
            ids.append(record["id"])
    return math.tanh(total), ids


def _signals_from_prepared(prepared, index, half_life, availability, bound):
    values, counts, ids = [], [], []
    for day in index:
        signal, article_ids = _daily_signal(prepared, _cutoff(day, bound), availability, half_life)
        values.append(signal); counts.append(len(article_ids)); ids.append(article_ids)
    frame = pd.DataFrame({"news_signal": values, "news_article_count": counts, "news_article_ids": ids}, index=index)
    for lag in LAGS:
        frame[f"news_lag_{lag}"] = frame.news_signal.shift(lag, fill_value=0.0)
        frame[f"news_lag_{lag}_article_ids"] = frame.news_article_ids.shift(lag).map(lambda value: list(value) if isinstance(value, list) else [])
    return frame


def signal_features(records, sessions, half_life=HALF_LIFE, availability="live", as_of=None,
                    signal_definition=SIGNAL_DEFINITION):
    if availability not in {"live", "research"}:
        raise ValueError("availability must be live or research")
    if not np.isfinite(half_life) or half_life <= 0:
        raise ValueError("half_life must be positive")
    index, prepared = _sessions(sessions), _prepared(records, signal_definition)
    bound = _utc(as_of, "as_of") if as_of is not None else None
    if bound is not None and (index > bound.tz_convert(None).normalize()).any():
        raise ValueError("sessions after as_of are not valid news feature origins")
    return _signals_from_prepared(prepared, index, half_life, availability, bound)


def latest_snapshot_features(records, sessions, half_life, availability, issued_as_of,
                             signal_definition=SIGNAL_DEFINITION):
    issued = _utc(issued_as_of, "issued_as_of")
    index, prepared = _sessions(sessions), _prepared(records, signal_definition)
    if index[-1] > issued.tz_convert(None).normalize():
        raise ValueError("latest price session is after issued_as_of")
    frame = _signals_from_prepared(prepared, index, half_life, availability, issued)
    signal, ids = _daily_signal(prepared, issued, availability, half_life)
    latest = index[-1]
    frame.at[latest, "news_signal"] = signal
    frame.at[latest, "news_article_count"] = len(ids)
    frame.at[latest, "news_article_ids"] = ids
    frame.at[latest, "news_lag_0"] = signal
    frame.at[latest, "news_lag_0_article_ids"] = ids
    return frame


def _prices(prices):
    close = prices["close"] if isinstance(prices, pd.DataFrame) else prices
    close = pd.Series(close).copy()
    close.index = pd.DatetimeIndex(pd.to_datetime(close.index, errors="raise")).normalize()
    observed = close.to_numpy(float)[~np.isnan(close.to_numpy(float))]
    if not len(observed) or close.index.has_duplicates or not close.index.is_monotonic_increasing or not np.isfinite(observed).all() or (observed <= 0).any():
        raise ValueError("observed prices must be sorted, finite, and positive")
    return close.astype(float)


def _base_frame(base_predictions):
    required = {"model_id", "origin_date", "target_date", "horizon", "predicted_return", "predicted_price", "actual_price"}
    if not isinstance(base_predictions, pd.DataFrame) or not required <= set(base_predictions):
        raise ValueError(f"base_predictions miss columns: {sorted(required - set(base_predictions))}")
    frame = base_predictions.copy()
    frame["origin_date"] = pd.to_datetime(frame.origin_date, errors="raise").dt.normalize()
    frame["target_date"] = pd.to_datetime(frame.target_date, errors="raise").dt.normalize()
    horizon = pd.to_numeric(frame.horizon, errors="raise")
    if not np.isfinite(horizon).all() or not np.equal(horizon, np.floor(horizon)).all() or not horizon.isin(HORIZONS).all():
        raise ValueError("horizon must be finite supported whole numbers")
    frame["horizon"] = horizon.astype(int)
    if frame.duplicated(["model_id", "origin_date", "horizon"]).any():
        raise ValueError("base predictions need unique supported model/origin/horizon rows")
    numeric = frame[["predicted_return", "predicted_price", "actual_price"]].apply(pd.to_numeric, errors="raise")
    if (not np.isfinite(numeric[["predicted_return", "predicted_price"]].to_numpy(float)).all() or not np.isfinite(numeric.actual_price.dropna()).all() or (numeric.actual_price.dropna() <= 0).any() or (numeric.predicted_price <= 0).any() or (frame.target_date <= frame.origin_date).any()):
        raise ValueError("base prediction dates and prices are invalid")
    frame[["predicted_return", "predicted_price", "actual_price"]] = numeric
    return frame


def _price_controls(close):
    log_close = np.log(close)
    daily = log_close.diff()
    return pd.DataFrame({"price_return_5": log_close - log_close.shift(5),
                         "volatility_5": daily.rolling(20, min_periods=20).std(ddof=1) * math.sqrt(5)}, index=close.index)


def _candidate_rows(base, close, signals, cutoff, earliest, availability):
    frame = base.copy()
    target_cutoffs = pd.to_datetime(frame.target_date).dt.tz_localize("UTC") + pd.Timedelta(hours=23)
    frame = frame[target_cutoffs.le(cutoff) & frame.actual_price.notna()].copy()
    frame["origin_close"], frame["target_close"] = frame.origin_date.map(close), frame.target_date.map(close)
    frame = frame[frame.origin_close.notna() & frame.target_close.notna()].copy()
    frame["actual_return"] = np.log(frame.target_close / frame.origin_close)
    frame["residual"] = frame.actual_return - frame.predicted_return
    frame = frame.join(signals[[f"news_lag_{lag}" for lag in LAGS]], on="origin_date").join(_price_controls(close), on="origin_date")
    start = pd.Timestamp(earliest).tz_convert(None).normalize() if earliest is not None else None
    if start is None:
        return frame.iloc[:0]
    sessions_after = signals.index[signals.index >= start]
    if len(sessions_after) <= max(LAGS):
        return frame.iloc[:0]
    frame = frame[frame.origin_date >= sessions_after[max(LAGS)]]
    fields = ["residual", "actual_return", "predicted_return", "price_return_5", "volatility_5", *[f"news_lag_{lag}" for lag in LAGS]]
    return frame[np.isfinite(frame[fields]).all(axis=1) & frame.volatility_5.gt(0)].copy()


def _news_index(rows):
    return rows[[f"news_lag_{lag}" for lag in LAGS]].to_numpy(float) @ LAG_WEIGHTS


def _non_overlapping(rows):
    selected, last_target = [], None
    for _, row in rows.sort_values(["origin_date", "target_date"]).iterrows():
        if last_target is None or row.origin_date >= last_target:
            selected.append(row)
            last_target = row.target_date
    return pd.DataFrame(selected, columns=rows.columns)


def _design(rows, horizon):
    rows = _non_overlapping(rows[rows.horizon.eq(horizon)])
    if rows.empty:
        return rows, np.empty(0), np.empty((0, 3)), np.empty(0)
    x = _news_index(rows) * math.exp(-(horizon - 5) / TAU)
    y = rows.residual.to_numpy(float) / rows.volatility_5.to_numpy(float)
    controls = np.column_stack([np.ones(len(rows)), rows.price_return_5.to_numpy(float) / rows.volatility_5.to_numpy(float)])
    return rows, y, controls, x


def _posterior(rows, horizon):
    rows, y, controls, x = _design(rows, horizon)
    if len(rows) < MIN_CALIBRATION_ROWS or np.ptp(x) <= 1e-12 or np.linalg.matrix_rank(controls) < controls.shape[1]:
        return None
    projection = controls @ np.linalg.pinv(controls)
    yr, xr = y - projection @ y, x - projection @ x
    if np.ptp(xr) <= 1e-12:
        return None
    df = len(y) - np.linalg.matrix_rank(controls)
    if df <= 0:
        return None
    residuals = yr[:, None] - xr[:, None] * GRID
    scale = max(float(np.mean(yr * yr)), np.finfo(float).tiny)
    floor = max(np.finfo(float).tiny, scale * np.finfo(float).eps * len(yr))
    sse = np.maximum(np.sum(residuals * residuals, axis=0), floor)
    log_weight = -.5 * df * np.log(sse)
    # A point-null prevents the positive slab from mechanically excluding zero.
    relative = np.exp(log_weight - log_weight.max())
    area = np.trapezoid(relative, GRID)
    if not np.isfinite(area) or area <= 0:
        return None
    null_relative = float(relative[0])
    null_probability = null_relative / (null_relative + area)
    density = relative / area
    slab_mean = float(np.trapezoid(GRID * density, GRID))
    mean = float((1 - null_probability) * slab_mean)
    cdf = np.concatenate([[0.0], np.cumsum((density[:-1] + density[1:]) * np.diff(GRID) / 2)])
    def quantile(probability):
        if probability <= null_probability:
            return 0.0
        return float(np.interp((probability - null_probability) / (1 - null_probability), cdf, GRID))
    interval = [quantile(.025), quantile(.975)]
    return {"weight": mean, "weight_interval": interval, "null_probability": float(null_probability),
            "prior": {"null_mass": .5, "slab": "uniform_0_1"}, "rows": rows, "y": y,
            "controls": controls, "x": x}


def _metrics(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    return {"n": int(len(actual)), "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))), "mae": float(np.mean(np.abs(actual - predicted))), "direction_accuracy": float(np.mean(np.sign(actual) == np.sign(predicted)))}


def _bootstrap(improvement, horizon):
    values = np.asarray(improvement, float)
    block = max(horizon, 5)
    if len(values) < block * 5:
        return {"estimate": float(values.mean()) if len(values) else None, "ci95": None, "block_length": block, "resamples": 0, "status": "insufficient_evaluation"}
    rng, draws = np.random.default_rng(42), []
    starts = np.arange(len(values))
    blocks = int(math.ceil(len(values) / block))
    for _ in range(1000):
        sample = np.concatenate([values[(start + np.arange(block)) % len(values)] for start in rng.choice(starts, blocks)])[:len(values)]
        draws.append(float(sample.mean()))
    return {"estimate": float(values.mean()), "ci95": [float(np.quantile(draws, .025)), float(np.quantile(draws, .975))], "block_length": block, "resamples": 1000, "status": "ok"}


def _walk_evaluation(rows, horizon):
    # Every final-window origin is evaluated.  Only each origin's training
    # sample is greedily purged of overlapping forward-return labels.
    ordered = rows[rows.horizon.eq(horizon)].sort_values("origin_date")
    split = int(len(ordered) * .6)
    predictions = []
    for _, row in ordered.iloc[split:].iterrows():
        history = ordered[(ordered.origin_date < row.origin_date) & (ordered.target_date < row.origin_date)]
        posterior = _posterior(history, horizon)
        if posterior is None:
            continue
        multiplier = math.exp(-(horizon - 5) / TAU)
        index = float(_news_index(pd.DataFrame([row]))[0])
        correction = row.volatility_5 * multiplier * index * posterior["weight"]
        predictions.append((row, correction, history.target_date.max()))
    if len(predictions) < 5:
        return {"n": len(predictions), "baseline": None, "adjusted": None, "mse_improvement": {"estimate": None, "ci95": None, "block_length": max(horizon, 5), "resamples": 0, "status": "insufficient_evaluation"}, "period_start": None, "period_end": None}, predictions
    actual = np.asarray([row.actual_return for row, _, _ in predictions])
    base = np.asarray([row.predicted_return for row, _, _ in predictions])
    adjusted = base + np.asarray([correction for _, correction, _ in predictions])
    improvement = (actual - base) ** 2 - (actual - adjusted) ** 2
    return {"n": len(predictions), "baseline": _metrics(actual, base), "adjusted": _metrics(actual, adjusted), "mse_improvement": _bootstrap(improvement, horizon), "period_start": predictions[0][0].origin_date.date().isoformat(), "period_end": predictions[-1][0].origin_date.date().isoformat()}, predictions


def _evidence(evaluation):
    summary = evaluation["mse_improvement"]
    if summary["status"] != "ok":
        return "insufficient_evaluation"
    return "improvement_supported" if summary["ci95"][0] > 0 else "not_demonstrated"


def _request_version(base, records, cutoff, availability):
    ids = sorted(str(record.get("analysis_id") or record.get("article_id") or record.get("url") or "") for record in records)
    raw = json.dumps({"cutoff": cutoff.isoformat(), "availability": availability, "rows": len(base), "ids": ids}, sort_keys=True)
    return f"{VERSION}-{sha256(raw.encode()).hexdigest()[:12]}"


def _empty_horizon(rows=None):
    calibration = _non_overlapping(rows) if rows is not None and not rows.empty else pd.DataFrame()
    return {"status": "insufficient_data", "weight": None, "weight_interval": None,
            "calibration_rows": len(calibration),
            "calibration_start": None if calibration.empty else calibration.origin_date.min().date().isoformat(),
            "calibration_end": None if calibration.empty else calibration.origin_date.max().date().isoformat(),
            "evaluation": None, "evidence_status": "insufficient_evaluation"}


def fit_residual(base_predictions, prices, records, *, as_of, availability="research"):
    if availability not in {"live", "research"}:
        raise ValueError("availability must be live or research")
    cutoff, base, close = _utc(as_of, "as_of"), _base_frame(base_predictions), _prices(prices)
    prepared = _prepared(records, SIGNAL_DEFINITION)
    earliest = min((row["available"] if availability == "live" else row["research_at"] for row in prepared), default=None)
    signals = signal_features(records, close.index, HALF_LIFE, availability, cutoff,
                              signal_definition=SIGNAL_DEFINITION)
    rows = _candidate_rows(base, close, signals, cutoff, earliest, availability)
    models, eligibility = {}, {}
    for horizon in HORIZONS:
        horizon_rows = rows[rows.horizon.eq(horizon)]
        final = _posterior(horizon_rows, horizon)
        evaluation, _ = _walk_evaluation(horizon_rows, horizon)
        if final is None:
            models[str(horizon)] = _empty_horizon(horizon_rows)
            eligibility[str(horizon)] = {"status": "insufficient_data", "train_rows": models[str(horizon)]["calibration_rows"], "tune_rows": 0, "holdout_rows": evaluation["n"]}
            continue
        calibration = final["rows"]
        evaluation_status = _evidence(evaluation)
        models[str(horizon)] = {"status": "experimental", "weight": final["weight"], "weight_interval": final["weight_interval"], "null_probability": final["null_probability"], "prior": final["prior"], "calibration_rows": len(calibration), "calibration_start": calibration.origin_date.min().date().isoformat(), "calibration_end": calibration.origin_date.max().date().isoformat(), "evaluation": evaluation, "evidence_status": evaluation_status}
        eligibility[str(horizon)] = {"status": "eligible", "train_rows": len(calibration), "tune_rows": 0, "holdout_rows": evaluation["n"]}
    status = "experimental" if any(model["status"] == "experimental" for model in models.values()) else "insufficient_data"
    bundle = {"version": VERSION, "model_version": _request_version(base, records, cutoff, availability), "availability": availability, "status": status, "training_cutoff": cutoff.isoformat(), "half_life": HALF_LIFE, "lags": list(LAGS), "lag_weights": [float(value) for value in LAG_WEIGHTS], "signal_definition": SIGNAL_DEFINITION, "horizon_decay": {"tau": TAU}, "eligibility": eligibility, "horizon_models": models, "metrics": {"evaluation_kind": "retrospective_reanalysis", "holdout": {str(h): models[str(h)]["evaluation"] for h in HORIZONS}}}
    return bundle


def _validate_bundle(bundle):
    if not isinstance(bundle, dict) or not (
            bundle.get("version") == VERSION and bundle.get("signal_definition") == SIGNAL_DEFINITION or
            bundle.get("version") == "news-residual-v2" and bundle.get("signal_definition") == LEGACY_SIGNAL_DEFINITION
    ) or bundle.get("status") not in {"experimental", "insufficient_data"} or bundle.get("availability") not in {"live", "research"} or not bundle.get("model_version"):
        raise ValueError("unsupported news residual artifact")
    _utc(bundle.get("training_cutoff"), "training_cutoff")
    try:
        valid_weights = np.asarray(bundle.get("lag_weights", []), float).shape == LAG_WEIGHTS.shape and np.allclose(bundle.get("lag_weights", []), LAG_WEIGHTS)
    except (TypeError, ValueError):
        valid_weights = False
    if bundle.get("half_life") != HALF_LIFE or bundle.get("lags") != list(LAGS) or not valid_weights or bundle.get("horizon_decay", {}).get("tau") != TAU or not isinstance(bundle.get("horizon_models"), dict):
        raise ValueError("news residual artifact is incomplete")
    for horizon in HORIZONS:
        model = bundle["horizon_models"].get(str(horizon))
        if not isinstance(model, dict) or model.get("status") not in {"experimental", "insufficient_data"}:
            raise ValueError("news residual artifact is incomplete")
        weight, interval, null_probability = model.get("weight"), model.get("weight_interval"), model.get("null_probability")
        if model["status"] == "experimental":
            numeric_weight = isinstance(weight, (int, float, np.number)) and not isinstance(weight, (bool, np.bool_))
            numeric_null = isinstance(null_probability, (int, float, np.number)) and not isinstance(null_probability, (bool, np.bool_))
            if (not numeric_weight or not np.isfinite(weight) or not 0 <= weight <= 1
                    or not numeric_null or not np.isfinite(null_probability) or not 0 <= null_probability <= 1
                    or not isinstance(interval, list) or len(interval) != 2
                    or any(isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.number)) or not np.isfinite(value) for value in interval)
                    or not 0 <= interval[0] <= interval[1] <= 1
                    or model.get("prior") != {"null_mass": .5, "slab": "uniform_0_1"}):
                raise ValueError("news residual artifact is incomplete")
    return bundle


def save_residual(bundle, path):
    _validate_bundle(bundle)
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name); json.dump(bundle, handle, sort_keys=True, allow_nan=False)
    try: os.replace(temporary, path)
    finally: temporary.unlink(missing_ok=True)


def load_residual(path):
    with Path(path).open(encoding="utf-8") as handle:
        return _validate_bundle(json.load(handle))


def _snapshot(records, sessions, bundle, issued):
    signal_definition = bundle["signal_definition"]
    if bundle["availability"] == "live":
        return latest_snapshot_features(records, sessions, HALF_LIFE, "live", issued, signal_definition), "strict_live"
    # Retrospective model: only articles actually acquired by issuance are known,
    # then reconstruct their event/selection timing without using later analysis.
    known = [record for record in records if _record_times(record)[1] <= issued]
    return latest_snapshot_features(known, sessions, HALF_LIFE, "research", issued, signal_definition), "research_known_at_issue"


def _prediction_row(row, correction, status, reason, bundle, signal=0., volatility=None, ids=None, interval=None, mode=None, issued_as_of=None):
    base_return, base_price = float(row.predicted_return), float(row.predicted_price)
    safe_volatility = float(volatility) if volatility is not None and np.isfinite(volatility) and volatility > 0 else None
    model = bundle.get("horizon_models", {}).get(str(int(row.horizon)), {})
    return {
        "base_model_id": row.model_id, "base_predicted_price": base_price,
        "base_predicted_return": base_return,
        "adjusted_price": None if correction is None else base_price * math.exp(correction),
        "adjusted_return": None if correction is None else base_return + correction,
        "news_correction": correction, "status": status, "reason": reason,
        "origin_date": row.origin_date.date().isoformat(),
        "target_date": row.target_date.date().isoformat(), "horizon": int(row.horizon),
        "as_of": issued_as_of or bundle.get("training_cutoff"),
        "news_article_count": len(ids or []), "article_ids": ids or [],
        "model_version": bundle["model_version"], "metrics": bundle.get("metrics"),
        "news_weight": model.get("weight"), "news_weight_interval": model.get("weight_interval"),
        "news_signal": signal, "volatility_scale": safe_volatility,
        "horizon_multiplier": math.exp(-(int(row.horizon) - 5) / TAU),
        "news_effect_interval": interval, "evidence_status": model.get("evidence_status"),
        "evaluation": model.get("evaluation"), "availability_mode": mode,
    }


def predict_residual(base_predictions, prices, records, bundle, *, as_of, availability="live"):
    bundle = _validate_bundle(bundle)
    if availability not in {"live", "research"}:
        raise ValueError("availability must be live or research")
    issued, base, close = _utc(as_of, "as_of"), _base_frame(base_predictions), _prices(prices)
    if (base.origin_date > issued.tz_convert(None).normalize()).any():
        raise ValueError("base prediction origin is after as_of")
    if bundle["status"] != "experimental":
        return [_prediction_row(row, None, "insufficient_data", "residual fit has insufficient data", bundle, issued_as_of=issued.isoformat()) for _, row in base.iterrows()]
    if _utc(bundle["training_cutoff"], "training_cutoff") > issued:
        return [_prediction_row(row, None, "unavailable", "residual model was trained after this issuance", bundle, issued_as_of=issued.isoformat()) for _, row in base.iterrows()]
    latest = close[close.notna()].index[-1]
    if (issued.tz_convert(None).normalize() - latest).days > 7:
        return [_prediction_row(row, None, "unavailable", "latest price is older than seven calendar days", bundle, issued_as_of=issued.isoformat()) for _, row in base.iterrows()]
    if (base.origin_date != latest).any():
        return [_prediction_row(row, None, "unavailable", "only the latest price snapshot can receive a new correction", bundle, issued_as_of=issued.isoformat()) for _, row in base.iterrows()]
    sessions = close.index[close.index <= latest]
    signals, mode = _snapshot(records, sessions, bundle, issued)
    controls = _price_controls(close)
    output = []
    for _, row in base.sort_values(["origin_date", "horizon"]).iterrows():
        model = bundle["horizon_models"][str(int(row.horizon))]
        visible = signals.loc[row.origin_date]
        ids = list(dict.fromkeys(sum((list(visible[f"news_lag_{lag}_article_ids"]) for lag in LAGS), [])))
        index = float(np.dot(visible[[f"news_lag_{lag}" for lag in LAGS]].to_numpy(float), LAG_WEIGHTS))
        volatility = controls.loc[row.origin_date, "volatility_5"] if row.origin_date in controls.index else np.nan
        if model["status"] != "experimental":
            output.append(_prediction_row(row, None, "insufficient_data", "horizon lacks calibration rows", bundle, index, volatility, ids, mode=mode, issued_as_of=issued.isoformat())); continue
        if not np.isfinite(volatility) or volatility <= 0:
            output.append(_prediction_row(row, None, "unavailable", "price volatility scale is unavailable", bundle, index, volatility, ids, mode=mode, issued_as_of=issued.isoformat())); continue
        multiplier = math.exp(-(row.horizon - 5) / TAU)
        if index == 0:
            output.append(_prediction_row(row, 0., "no_news", "no eligible news in signal lags", bundle, index, volatility, ids, [0., 0.], mode, issued.isoformat())); continue
        correction = float(volatility * multiplier * index * model["weight"])
        interval = [float(volatility * multiplier * index * bound) for bound in model["weight_interval"]]
        output.append(_prediction_row(row, correction, "experimental", None, bundle, index, volatility, ids, sorted(interval), mode, issued.isoformat()))
    return output
