"""Time-safe Jev news signals and a small residual price correction model.

``research`` availability is intentionally retrospective: it uses event time,
not the time at which this service obtained the classification.  It is useful
for exploratory analysis, but must not be presented as a live backtest.
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
from sklearn.linear_model import Ridge


VERSION = "news-residual-v1"
HORIZONS = (5, 20, 60)
LAGS = (0, 1, 3, 5)
MIN_ROWS_PER_HORIZON = 12
MIN_VALIDATION_ROWS = 5
ATTRIBUTION_MIN_WEIGHT = 1e-4


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
    available = record.get("available_at")
    if available is None:
        candidates = [event]
        for field in ("collected_at", "analyzed_at"):
            if record.get(field) is not None:
                candidates.append(_utc(record[field], field))
        available = max(candidates)
    else:
        available = _utc(available, "available_at")
    required_at = [event]
    for field in ("collected_at", "analyzed_at"):
        if record.get(field) is not None:
            required_at.append(_utc(record[field], field))
    if available < max(required_at):
        raise ValueError("available_at must not precede event_at")
    return event, available


def _identity(record):
    for field in ("analysis_id", "article_id", "url"):
        value = record.get(field)
        if value:
            return (field, str(value).strip())
    title = str(record.get("title") or "").strip().casefold()
    source = str(record.get("source") or "").strip().casefold()
    if not title:
        raise ValueError("record needs analysis_id, article_id, url, or title")
    return ("content", source, title)


def _article_id(record):
    return str(record.get("analysis_id") or record.get("article_id") or record.get("url") or _identity(record)[-1])


def _prepared(records):
    """Deduplicate both source identifiers and identical source/title content."""
    output, seen_ids, seen_content = [], set(), set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("records must contain dictionaries")
        event, available = _record_times(record)
        probabilities = [record.get(key) for key in ("p_bullish", "p_bearish", "p_neutral", "p_uncertain")]
        if any(value is None for value in probabilities):
            raise ValueError("record misses Jev choice probabilities")
        probabilities = np.asarray(probabilities, dtype=float)
        relevance, confidence = float(record.get("relevance", 1.0)), float(record.get("confidence", 1.0))
        if (not np.isfinite(probabilities).all() or (probabilities < 0).any() or
                not np.isclose(probabilities.sum(), 1.0, atol=.02) or
                not np.isfinite(relevance) or not np.isfinite(confidence) or not 0 <= relevance <= 1 or not 0 <= confidence <= 1):
            raise ValueError("record probabilities, relevance, and confidence must be finite and valid")
        identity = _identity(record)
        title = str(record.get("title") or "").strip().casefold()
        content = (title, str(record.get("summary") or "").strip().casefold()) if title else None
        if identity in seen_ids or (content and content in seen_content):
            continue
        seen_ids.add(identity)
        if content:
            seen_content.add(content)
        selection_at = _utc(record.get("selection_available_at", event), "selection_available_at")
        modified_at = _utc(record.get("modified_at") or event, "modified_at")
        output.append({"id": _article_id(record), "event": event,
                       "research_at": max(event, selection_at, modified_at),
                       "available": max(available, selection_at, modified_at),
                       "weight": relevance * confidence, "pressure": float(probabilities[0] - probabilities[1])})
    return sorted(output, key=lambda row: (row["event"], row["available"], row["id"]))


def _daily_signal(prepared, cutoff, availability, half_life):
    total, ids = 0.0, []
    for record in prepared:
        visible_at = record["available"] if availability == "live" else record["research_at"]
        if record["event"] > cutoff or visible_at > cutoff:
            continue
        age = (cutoff - record["event"]).total_seconds() / 86400
        contribution = record["weight"] * record["pressure"] * (2.0 ** (-age / half_life))
        total += contribution
        if abs(contribution) >= ATTRIBUTION_MIN_WEIGHT:
            ids.append(record["id"])
    return math.tanh(total), ids


def _signals_from_prepared(prepared, index, half_life, availability, bound):
    values, counts, article_ids = [], [], []
    for day in index:
        signal, ids = _daily_signal(prepared, _cutoff(day, bound), availability, half_life)
        values.append(signal)
        counts.append(len(ids))
        article_ids.append(ids)
    result = pd.DataFrame({"news_signal": values, "news_article_count": counts, "news_article_ids": article_ids}, index=index)
    for lag in LAGS:
        result[f"news_lag_{lag}"] = result.news_signal.shift(lag, fill_value=0.0)
        result[f"news_lag_{lag}_article_ids"] = result.news_article_ids.shift(lag).map(
            lambda value: list(value) if isinstance(value, list) else []
        )
    return result


def signal_features(records, sessions, half_life=3, availability="live", as_of=None):
    """Return event-time-decayed signals plus 0/1/3/5 *trading-session* lags.

    In live mode an article must have completed analysis before the origin's
    23:00 UTC cutoff.  Old articles received late keep their old event age.
    """
    if availability not in {"live", "research"}:
        raise ValueError("availability must be live or research")
    if not np.isfinite(half_life) or half_life <= 0:
        raise ValueError("half_life must be positive")
    index, prepared = _sessions(sessions), _prepared(records)
    bound = None if as_of is None else _utc(as_of, "as_of")
    if bound is not None and (index > bound.tz_convert(None).normalize()).any():
        raise ValueError("sessions after as_of are not valid news feature origins")
    return _signals_from_prepared(prepared, index, half_life, availability, bound)


def latest_snapshot_features(records, sessions, half_life, availability, issued_as_of):
    """Issue a new forecast from the latest close using the actual issue time.

    Only lag 0 receives after-close news.  Earlier lags retain their original
    session cutoffs, so this never rewrites a historical Friday forecast.
    """
    issued = _utc(issued_as_of, "issued_as_of")
    index, prepared = _sessions(sessions), _prepared(records)
    if index[-1] > issued.tz_convert(None).normalize():
        raise ValueError("latest price session is after issued_as_of")
    result = _signals_from_prepared(prepared, index, half_life, availability, issued)
    latest = index[-1]
    signal, ids = _daily_signal(prepared, issued, availability, half_life)
    result.at[latest, "news_signal"] = signal
    result.at[latest, "news_article_count"] = len(ids)
    result.at[latest, "news_article_ids"] = ids
    result.at[latest, "news_lag_0"] = signal
    result.at[latest, "news_lag_0_article_ids"] = ids
    return result


def _prices(prices):
    close = prices["close"] if isinstance(prices, pd.DataFrame) else prices
    close = pd.Series(close).copy()
    close.index = pd.DatetimeIndex(pd.to_datetime(close.index, errors="raise")).normalize()
    values = close.to_numpy(float)
    observed = values[~np.isnan(values)]
    if (not len(observed) or close.index.has_duplicates or not close.index.is_monotonic_increasing or
            not np.isfinite(observed).all() or (observed <= 0).any()):
        raise ValueError("observed prices must be sorted, finite, and positive")
    return close.astype(float)


def _base_frame(base_predictions):
    required = {"model_id", "origin_date", "target_date", "horizon", "predicted_return", "predicted_price", "actual_price"}
    if not isinstance(base_predictions, pd.DataFrame) or not required <= set(base_predictions):
        raise ValueError(f"base_predictions miss columns: {sorted(required - set(base_predictions))}")
    frame = base_predictions.copy()
    frame["origin_date"] = pd.to_datetime(frame.origin_date, errors="raise").dt.normalize()
    frame["target_date"] = pd.to_datetime(frame.target_date, errors="raise").dt.normalize()
    raw_horizon = pd.to_numeric(frame.horizon, errors="raise")
    if not np.isfinite(raw_horizon).all() or not np.equal(raw_horizon, np.floor(raw_horizon)).all():
        raise ValueError("horizon must be finite whole numbers")
    frame["horizon"] = raw_horizon.astype(int)
    if frame.duplicated(["model_id", "origin_date", "horizon"]).any() or not frame.horizon.isin(HORIZONS).all():
        raise ValueError("base predictions need unique supported model/origin/horizon rows")
    numeric = frame[["predicted_return", "predicted_price", "actual_price"]].apply(pd.to_numeric, errors="raise")
    if (not np.isfinite(numeric[["predicted_return", "predicted_price"]].to_numpy(float)).all() or
            not np.isfinite(numeric.actual_price.dropna().to_numpy(float)).all() or
            (numeric.actual_price.dropna() <= 0).any() or (frame.target_date <= frame.origin_date).any() or
            (numeric.predicted_price <= 0).any()):
        raise ValueError("base prediction dates and prices are invalid")
    frame[["predicted_return", "predicted_price", "actual_price"]] = numeric
    return frame


def _candidate_rows(base, close, signals, cutoff, earliest, availability):
    frame = base.copy()
    target_cutoffs = pd.to_datetime(frame.target_date, errors="raise").dt.tz_localize("UTC") + pd.Timedelta(hours=23)
    frame = frame[target_cutoffs.le(cutoff) & frame.actual_price.notna()].copy()
    frame["origin_close"] = frame.origin_date.map(close)
    frame["target_close"] = frame.target_date.map(close)
    frame = frame[frame.origin_close.notna() & frame.target_close.notna()].copy()
    # The price series is the authoritative target label; base.actual_price is
    # retained for persistence but not trusted to fill a missing target close.
    frame["actual_return"] = np.log(frame.target_close.astype(float) / frame.origin_close)
    frame["residual"] = frame.actual_return - frame.predicted_return.astype(float)
    frame = frame.join(signals[[f"news_lag_{lag}" for lag in LAGS]], on="origin_date", how="left")
    # No coverage before the first observable article: zero there means unknown, not neutral.
    start = pd.Timestamp(earliest).tz_convert(None).normalize() if earliest is not None else None
    if start is None:
        return frame.iloc[0:0]
    sessions_after = signals.index[signals.index >= start]
    if len(sessions_after) <= max(LAGS):
        return frame.iloc[0:0]
    warm_start = sessions_after[max(LAGS)]
    frame = frame[frame.origin_date >= warm_start]
    return frame[np.isfinite(frame[["residual", *[f"news_lag_{lag}" for lag in LAGS]]]).all(axis=1)].copy()


def _metrics(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    return {"n": int(len(actual)), "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))),
            "mae": float(np.mean(np.abs(actual - predicted))),
            "direction_accuracy": float(np.mean(np.sign(actual) == np.sign(predicted)))}


def _fit_model(rows, alpha, tau):
    decay = np.exp(-(rows.horizon.to_numpy(float) - 5.0) / tau)
    x = rows[[f"news_lag_{lag}" for lag in LAGS]].to_numpy(float) * decay[:, None]
    return Ridge(alpha=alpha, fit_intercept=False, positive=True).fit(x, rows.residual.to_numpy(float))


def _segments(rows):
    origins = np.sort(rows.origin_date.unique())
    train_end, tune_end = int(len(origins) * .6), int(len(origins) * .8)
    if train_end < 1 or tune_end <= train_end or tune_end >= len(origins):
        return None
    tune_start, holdout_start = pd.Timestamp(origins[train_end]), pd.Timestamp(origins[tune_end])
    train = rows[(rows.origin_date < tune_start) & (rows.target_date < tune_start)]
    tune = rows[(rows.origin_date >= tune_start) & (rows.origin_date < holdout_start) & (rows.target_date < holdout_start)]
    # The final fit can see labels strictly before the untouched holdout.
    final_train = rows[(rows.origin_date < holdout_start) & (rows.target_date < holdout_start)]
    holdout = rows[rows.origin_date >= holdout_start]
    return train, tune, final_train, holdout, tune_start, holdout_start


def _eligibility(train, tune, holdout):
    result, eligible = {}, []
    for horizon in HORIZONS:
        counts = {"train_rows": int((train.horizon == horizon).sum()),
                  "tune_rows": int((tune.horizon == horizon).sum()),
                  "holdout_rows": int((holdout.horizon == horizon).sum())}
        ready = (counts["train_rows"] >= MIN_ROWS_PER_HORIZON and
                 counts["tune_rows"] >= MIN_VALIDATION_ROWS and
                 counts["holdout_rows"] >= MIN_VALIDATION_ROWS)
        result[str(horizon)] = {**counts, "status": "eligible" if ready else "insufficient_data"}
        if ready:
            eligible.append(horizon)
    return result, eligible


def _request_version(base, records, cutoff, availability, suffix="insufficient"):
    record_ids = [str(record.get("analysis_id") or record.get("article_id") or record.get("url") or "") for record in records]
    payload = {"availability": availability, "cutoff": cutoff.isoformat(), "rows": len(base), "record_ids": sorted(record_ids)}
    return f"{VERSION}-{suffix}-{sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()[:12]}"


def _insufficient_bundle(base, records, cutoff, availability, eligibility):
    return {"version": VERSION, "model_version": _request_version(base, records, cutoff, availability),
            "availability": availability, "status": "insufficient_data", "training_cutoff": cutoff.isoformat(),
            "half_life": None, "horizon_decay": None, "coefficients": None, "eligibility": eligibility,
            "metrics": {"tuning": None, "holdout": {}}}


def _predict_rows(model, rows, tau):
    decay = np.exp(-(rows.horizon.to_numpy(float) - 5.0) / tau)
    return rows.predicted_return.to_numpy(float) + model.predict(
        rows[[f"news_lag_{lag}" for lag in LAGS]].to_numpy(float) * decay[:, None]
    )


def _per_horizon_metrics(rows, adjusted):
    result = {}
    for horizon in HORIZONS:
        mask = rows.horizon.eq(horizon).to_numpy()
        if mask.any():
            actual = rows.loc[mask, "actual_return"]
            result[str(horizon)] = {"baseline": _metrics(actual, rows.loc[mask, "predicted_return"]),
                                    "adjusted": _metrics(actual, adjusted[mask])}
    return result


def _chronological_validation(train, tune, alpha, tau):
    if len(train) < MIN_ROWS_PER_HORIZON or len(tune) < MIN_VALIDATION_ROWS:
        return None
    model = _fit_model(train, alpha, tau)
    adjusted = _predict_rows(model, tune, tau)
    return _metrics(tune.actual_return, adjusted)


def fit_residual(base_predictions, prices, records, *, as_of, availability="research"):
    """Fit one positive Ridge residual coefficient vector shared across horizons.

    ``research`` uses retrospective event visibility and stays marked experimental.
    A live fit uses the article's analysis completion time for every origin.
    """
    if availability not in {"live", "research"}:
        raise ValueError("availability must be live or research")
    cutoff = _utc(as_of, "as_of")
    base, close = _base_frame(base_predictions), _prices(prices)
    sessions = close.index
    prepared = _prepared(records)
    earliest = min((row["available"] if availability == "live" else row["research_at"] for row in prepared), default=None)
    best = None
    observed_eligibility = {str(h): {"train_rows": 0, "tune_rows": 0, "holdout_rows": 0, "status": "insufficient_data"} for h in HORIZONS}
    for half_life in (1, 3, 7):
        signals = signal_features(records, sessions, half_life, availability, cutoff)
        rows = _candidate_rows(base, close, signals, cutoff, earliest, availability)
        segments = _segments(rows)
        if segments is None:
            continue
        train, tune, final_train, holdout, _tune_start, _holdout_start = segments
        eligibility, eligible_horizons = _eligibility(train, tune, holdout)
        observed_eligibility = eligibility
        if not eligible_horizons:
            continue
        train = train[train.horizon.isin(eligible_horizons)]
        tune = tune[tune.horizon.isin(eligible_horizons)]
        for alpha in (1, 10):
            for tau in (10, 20, 40):
                validation = _chronological_validation(train, tune, alpha, tau)
                if validation is not None:
                    candidate = (validation["rmse"], half_life, alpha, tau, validation)
                    best = min(best, candidate, key=lambda item: item[0]) if best else candidate
    if best is None:
        return _insufficient_bundle(base, records, cutoff, availability, observed_eligibility)
    _, half_life, alpha, tau, validation = best
    selected_signals = signal_features(records, sessions, half_life, availability, cutoff)
    all_rows = _candidate_rows(base, close, selected_signals, cutoff, earliest, availability)
    selected_segments = _segments(all_rows)
    if selected_segments is None:
        return _insufficient_bundle(base, records, cutoff, availability, observed_eligibility)
    train, tune, final_train, holdout, _tune_start, holdout_start = selected_segments
    eligibility, eligible_horizons = _eligibility(train, tune, holdout)
    fit_rows = final_train[final_train.horizon.isin(eligible_horizons)]
    if fit_rows.empty:
        return _insufficient_bundle(base, records, cutoff, availability, eligibility)
    model = _fit_model(fit_rows, alpha, tau)
    fitted = _predict_rows(model, fit_rows, tau)
    holdout_rows = holdout[holdout.horizon.isin(eligible_horizons)]
    holdout_adjusted = _predict_rows(model, holdout_rows, tau)
    feature_rows = [[row.origin_date.date().isoformat(), row.target_date.date().isoformat(), int(row.horizon),
                     round(float(row.residual), 12), *[round(float(row[f"news_lag_{lag}"]), 12) for lag in LAGS]]
                    for _, row in fit_rows.iterrows()]
    analysis = sorted((str(record.get("analysis_id") or record.get("article_id") or record.get("url") or ""),
                       str(record.get("prompt_version") or record.get("analysis_version") or "")) for record in records)
    provenance = {"availability": availability, "training_cutoff": cutoff.isoformat(), "holdout_start": holdout_start.date().isoformat(),
                  "half_life": half_life, "alpha": alpha, "tau": tau, "coefficients": [float(value) for value in model.coef_],
                  "data_feature_hash": sha256(json.dumps(feature_rows).encode()).hexdigest(), "analysis": analysis}
    model_version = f"{VERSION}-{sha256(json.dumps(provenance, sort_keys=True).encode()).hexdigest()[:12]}"
    return {"version": VERSION, "model_version": model_version, "availability": availability, "status": "experimental",
            "training_cutoff": cutoff.isoformat(), "half_life": half_life, "horizon_decay": {"tau": tau},
            "alpha": alpha, "coefficients": [float(value) for value in model.coef_], "lags": list(LAGS),
            "usable_from": holdout_start.date().isoformat(), "eligibility": eligibility,
            "metrics": {"tuning": validation, "training": _metrics(fit_rows.actual_return, fitted),
                        "holdout": _per_horizon_metrics(holdout_rows, holdout_adjusted)}}


def _validate_bundle(bundle):
    if not isinstance(bundle, dict) or bundle.get("version") != VERSION or bundle.get("status") not in {"experimental", "insufficient_data"}:
        raise ValueError("unsupported news residual artifact")
    if bundle.get("availability") not in {"live", "research"} or not bundle.get("model_version"):
        raise ValueError("news residual artifact is incomplete")
    _utc(bundle.get("training_cutoff"), "training_cutoff")
    if bundle["status"] == "experimental":
        coefficients = np.asarray(bundle.get("coefficients", []), dtype=float)
        tau = bundle.get("horizon_decay", {}).get("tau") if isinstance(bundle.get("horizon_decay"), dict) else None
        if (len(coefficients) != len(LAGS) or not np.isfinite(coefficients).all() or (coefficients < 0).any() or
                bundle.get("half_life") not in (1, 3, 7) or not isinstance(tau, (int, float)) or not np.isfinite(tau) or tau <= 0 or
                not bundle.get("usable_from")):
            raise ValueError("news residual artifact is incomplete")
        try:
            pd.Timestamp(bundle["usable_from"])
        except (TypeError, ValueError):
            raise ValueError("news residual artifact is incomplete") from None
    return bundle


def save_residual(bundle, path):
    _validate_bundle(bundle)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        json.dump(bundle, handle, sort_keys=True, allow_nan=False)
    try:
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_residual(path):
    with Path(path).open(encoding="utf-8") as handle:
        return _validate_bundle(json.load(handle))


def predict_residual(base_predictions, prices, records, bundle, *, as_of, availability="live"):
    """Apply only a time-safe correction; serving defaults to live visibility.

    Training may be retrospective ``research`` while current serving still uses
    only classifications completed before the forecast origin cutoff.
    """
    bundle = _validate_bundle(bundle)
    if availability not in {"live", "research"}:
        raise ValueError("availability must be live or research")
    cutoff, base, close = _utc(as_of, "as_of"), _base_frame(base_predictions), _prices(prices)
    if (base.origin_date > cutoff.tz_convert(None).normalize()).any():
        raise ValueError("base prediction origin is after as_of")
    if bundle["status"] != "experimental":
        return [_prediction_row(row, None, "insufficient_data", "residual fit has insufficient data", [], 0, bundle, cutoff) for _, row in base.iterrows()]
    if _utc(bundle["training_cutoff"], "training_cutoff") > cutoff:
        return [_prediction_row(row, None, "unavailable", "residual model was trained after this issuance", [], 0, bundle, cutoff)
                for _, row in base.iterrows()]
    latest_price_date = close[close.notna()].index[-1]
    if (cutoff.tz_convert(None).normalize() - latest_price_date).days > 7:
        return [_prediction_row(row, None, "unavailable", "latest price is older than seven calendar days", [], 0, bundle, cutoff)
                for _, row in base.iterrows()]
    if (base.origin_date != latest_price_date).any():
        return [_prediction_row(row, None, "unavailable", "only the latest price snapshot can receive a new correction", [], 0, bundle, cutoff)
                for _, row in base.iterrows()]
    usable_from = bundle.get("usable_from")
    if usable_from is None or latest_price_date < pd.Timestamp(usable_from):
        return [_prediction_row(row, None, "unavailable", "origin predates residual model usability", [], 0, bundle, cutoff)
                for _, row in base.iterrows()]
    sessions = close.index[close.index <= latest_price_date]
    signals = latest_snapshot_features(records, sessions, bundle["half_life"], availability, cutoff)
    coefficients, tau = np.asarray(bundle["coefficients"], float), float(bundle["horizon_decay"]["tau"])
    output = []
    for _, row in base.sort_values(["origin_date", "horizon"]).iterrows():
        eligibility = bundle["eligibility"].get(str(int(row.horizon)), {})
        visible = signals.loc[row.origin_date] if row.origin_date in signals.index else None
        if eligibility.get("status") != "eligible":
            output.append(_prediction_row(row, None, "insufficient_data", "horizon lacks mature residual rows", [], 0, bundle, cutoff))
            continue
        if visible is None:
            output.append(_prediction_row(row, None, "unavailable", "origin session is unavailable", [], 0, bundle, cutoff))
            continue
        x = visible[[f"news_lag_{lag}" for lag in LAGS]].to_numpy(float)
        lag_count = int((np.abs(x) > 0).sum())
        signal_ids = []
        current_ids = list(visible.news_lag_0_article_ids)
        old_ids = []
        for lag, value in zip(LAGS, x):
            if value:
                values = list(visible[f"news_lag_{lag}_article_ids"])
                signal_ids.extend(values)
                if lag:
                    old_ids.extend(values)
        ids = list(dict.fromkeys(signal_ids))
        count = len(ids)
        if not lag_count:
            output.append(_prediction_row(row, 0.0, "no_news", "no eligible news in signal lags", ids, count, bundle, cutoff,
                                          len(current_ids), len(list(dict.fromkeys(old_ids)))))
            continue
        correction = float(np.dot(coefficients, x) * math.exp(-(float(row.horizon) - 5.0) / tau))
        output.append(_prediction_row(row, correction, "experimental", None, ids, count, bundle, cutoff,
                                      len(current_ids), len(list(dict.fromkeys(old_ids)))))
    return output


def _prediction_row(row, correction, status, reason, article_ids, article_count, bundle, as_of,
                    current_article_count=0, old_article_count=0):
    base_return, base_price = float(row.predicted_return), float(row.predicted_price)
    adjusted_return = None if correction is None else base_return + correction
    adjusted_price = None if correction is None else base_price * math.exp(correction)
    return {"base_model_id": row.model_id, "base_predicted_price": base_price, "base_predicted_return": base_return,
            "adjusted_price": adjusted_price, "adjusted_return": adjusted_return, "news_correction": correction,
            "status": status, "reason": reason, "origin_date": row.origin_date.date().isoformat(),
            "target_date": row.target_date.date().isoformat(), "horizon": int(row.horizon),
            "as_of": as_of.isoformat(), "news_article_count": article_count, "article_ids": article_ids,
            "current_news_article_count": current_article_count, "older_news_article_count": old_article_count,
            "model_version": bundle.get("model_version", VERSION), "metrics": bundle.get("metrics")}
