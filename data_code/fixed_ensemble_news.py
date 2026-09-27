"""Small, reproducible helpers for the fixed-pair Jev research notebook."""

from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from coffee_service.features import PRICE_FEATURES, MACRO_FEATURES, make_targets
from coffee_service.news_residual import SIGNAL_DEFINITION, _cutoff, _prepared, _sessions
from coffee_service.transform import (
    REGIONS, coffee_sessions, transform_market, transform_macro, transform_weather,
)

PAIRS = {5: ("NLinear", "XGBoost"), 20: ("NLinear", "DLinear"), 60: ("DLinear",)}
LOOKBACK, SEED = 60, 42
FINAL_CUTOFF = pd.Timestamp("2023-12-31")


def scores(frame, prediction):
    actual, predicted, current = (np.asarray(v, float) for v in
        (frame.actual_return, prediction, frame.current_price))
    if (actual.ndim != 1 or len(actual) == 0 or actual.shape != predicted.shape
            or actual.shape != current.shape or not np.isfinite([actual, predicted, current]).all()
            or (current <= 0).any()):
        raise ValueError("Metrics need aligned finite predictions and positive prices")
    actual_price, predicted_price = current * np.exp(actual), current * np.exp(predicted)
    if not np.isfinite([actual_price, predicted_price]).all():
        raise ValueError("Nonfinite reconstructed prices")
    up, down = actual > 0, actual < 0
    up_recall = np.mean(predicted[up] > 0) if up.any() else np.nan
    down_recall = np.mean(predicted[down] < 0) if down.any() else np.nan
    return {"표본": len(actual), "가격 RMSE": float(np.sqrt(np.mean((actual_price-predicted_price)**2))),
            "수익률 RMSE": float(np.sqrt(np.mean((actual-predicted)**2))),
            "방향 적중률 (%)": float(100*np.mean(np.sign(actual)==np.sign(predicted))),
            "균형 정확도 (%)": float(50*(up_recall+down_recall)),
            "상승 누락률 (%)": float(100*(1-up_recall))}


def average_prices(predictions):
    values = np.asarray(predictions, float)
    if values.ndim != 2 or not len(values) or values.shape[1] == 0 or not np.isfinite(values).all():
        raise ValueError("Average requires finite aligned model columns")
    return np.logaddexp.reduce(values, axis=1)-np.log(values.shape[1])


def align_models(frame, members):
    reference = frame.loc[frame.model.eq(members[0])].sort_values("origin_date").set_index("origin_date")
    if reference.empty or reference.index.has_duplicates:
        raise ValueError("Missing or duplicate model origins")
    columns = ["target_date", "actual_return", "current_price"]
    predictions = pd.DataFrame(index=reference.index)
    for name in members:
        part = frame.loc[frame.model.eq(name)].sort_values("origin_date").set_index("origin_date")
        pd.testing.assert_frame_equal(reference[columns], part[columns], check_exact=True)
        predictions[name] = part.predicted_return
    return reference[columns].copy(), predictions


def adjust(prediction, news_x, beta):
    prediction, news_x = np.asarray(prediction, float), np.asarray(news_x, float)
    if (prediction.shape != news_x.shape or not np.isfinite([prediction, news_x]).all()
            or not np.isfinite(beta) or not 0 <= beta <= 1):
        raise ValueError("Invalid direction-preserving news correction")
    return prediction + beta*news_x


def fit_strength(frame, prediction, cutoff):
    """Price-MSE fit from 0.5. Zero is a valid learned boundary, not an initializer."""
    cutoff = pd.Timestamp(cutoff)
    if (cutoff > FINAL_CUTOFF or frame.empty or not frame.target_date.le(cutoff).all()
            or not frame.index.to_series().lt(frame.target_date).all()
            or frame.index.min() < pd.Timestamp("2022-01-01")):
        raise ValueError("Coefficient fitting needs mature 2022-2023 labels only")
    scores(frame, prediction)
    base = frame.current_price.to_numpy(float)*np.exp(np.asarray(prediction, float))
    actual = frame.current_price.to_numpy(float)*np.exp(frame.actual_return.to_numpy(float))
    x = frame.news_x.to_numpy(float)
    if not np.isfinite(x).all():
        raise ValueError("Nonfinite news input")
    scale = max(float(np.mean((base-actual)**2)), np.finfo(float).eps)

    def loss_gradient(values):
        corrected = base*np.exp(float(values[0])*x)
        error = corrected-actual
        return float(np.mean(error**2)/scale), np.array([2*np.mean(error*corrected*x)/scale])

    path = [0.5]
    if np.count_nonzero(x) == 0:
        beta, status = 0., "no_identifiable_signal"
    else:
        result = minimize(loss_gradient, np.array([0.5]), jac=True, method="L-BFGS-B",
                          bounds=[(0., 1.)], callback=lambda value: path.append(float(value[0])),
                          options={"ftol": 1e-14, "gtol": 1e-10, "maxiter": 500})
        if not result.success:
            raise RuntimeError(f"News fit failed: {result.message}")
        beta, status = float(result.x[0]), "fitted"
        # Boundary comparison checks the optimizer; it does not force a nonzero effect.
        candidates = [beta, 0., 1.]
        beta = min(candidates, key=lambda b: (loss_gradient([b])[0], b))
    path.append(beta)
    return {"initial_beta": .5, "beta": beta, "status": status, "path": path,
            "fit_rows": len(frame), "fit_max_target": frame.target_date.max().isoformat(),
            "loss_at_zero": loss_gradient([0])[0], "loss_at_initial": loss_gradient([.5])[0],
            "loss_at_fit": loss_gradient([beta])[0], "gradient_at_zero": float(loss_gradient([0])[1][0])}


def load_market(source_dir):
    names = ["coffee", "alfred_dexbzus", "alfred_dff", "alfred_dcoilwtico",
             *[f"weather_{region}" for region in REGIONS]]
    sources = {}
    for name in names:
        frame = pd.read_parquet(Path(source_dir)/f"{name}.parquet").sort_values("date").reset_index(drop=True)
        frame["date"] = pd.to_datetime(frame.date).astype("datetime64[ns]")
        if frame.date.isna().any() or frame.date.duplicated().any():
            raise ValueError(f"Invalid source dates: {name}")
        if name.startswith("weather_") and len(pd.date_range(frame.date.min(), frame.date.max()).difference(frame.date)):
            raise ValueError(f"Uncollected weather dates: {name}")
        sources[name] = frame
    sessions = coffee_sessions("2014-07-01", sources["coffee"].date.max())
    prices, _ = transform_market(sources, sessions)
    return sources, sessions, prices


def build_features(sources, sessions, cutoff):
    _, market = transform_market(sources, sessions)
    macro, macro_dates = transform_macro(sources, sessions)
    weather, weather_columns, weather_dates = transform_weather(sources, sessions, cutoff)
    for trace in [*macro_dates.values(), *weather_dates.values()]:
        available = trace.available_at.dropna()
        assert (available <= available.index).all()
    return market.join(macro).join(weather), weather_columns


def fixed_features(sources, sessions):
    first, weather = build_features(sources, sessions, "2021-12-31")
    constant = [c for c in weather if first.loc["2015-01-01":"2021-12-31", c].nunique() <= 1]
    columns = PRICE_FEATURES+MACRO_FEATURES+[c for c in weather if c not in constant]
    return columns, constant


def daily_news_feature(records, sessions, availability="research"):
    """Assign each article once to the first price session after it becomes visible."""
    if availability not in {"research", "live"}:
        raise ValueError("availability must be live or research")
    index = _sessions(sessions)
    closes = pd.DatetimeIndex([_cutoff(day, None) for day in index])
    totals = np.zeros(len(index))
    counts = np.zeros(len(index), dtype=int)
    for article in _prepared(records, SIGNAL_DEFINITION):
        visible = max(article["event"], article["available" if availability == "live" else "research_at"])
        position = closes.searchsorted(visible)
        if position < len(index):
            totals[position] += article["pressure"] * article["weight"]
            counts[position] += 1
    sentiment = pd.Series(np.tanh(totals), index=index).where(counts > 0)
    return pd.DataFrame({"news_sentiment": sentiment, "news_present": counts > 0,
                         "news_article_count": counts}, index=index)


def forecast(sources, sessions, columns, horizon, name, setting, artifact, *,
             news_feature: pd.Series | None = None, use_news: bool = False,
             cutoff: date = FINAL_CUTOFF, evaluation_end: date | None = None):
    """Fit on mature labels through cutoff, then evaluate on later origins."""
    cutoff = pd.Timestamp(cutoff)
    evaluation_end = pd.Timestamp(evaluation_end) if evaluation_end is not None else None
    if cutoff > FINAL_CUTOFF or (evaluation_end is not None and evaluation_end <= cutoff):
        raise ValueError("Cutoff must be at most 2023-12-31; evaluation end must follow cutoff")
    if use_news and news_feature is None:
        raise ValueError("use_news requires news_feature")
    if news_feature is not None:
        if (not isinstance(news_feature, pd.Series) or not isinstance(news_feature.index, pd.DatetimeIndex)
                or news_feature.index.tz is not None or news_feature.index.has_duplicates
                or not news_feature.index.equals(news_feature.index.normalize())
                or not pd.api.types.is_numeric_dtype(news_feature.dtype)
                or "news_sentiment" in columns):
            raise ValueError("news_feature needs unique daily dates and numeric values")
        valid_news = news_feature.dropna().to_numpy(dtype=float)
        if not np.isfinite(valid_news).all() or (np.abs(valid_news) > 1).any():
            raise ValueError("news_feature values must be finite or NaN and within [-1, 1]")
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
    from sklearn.preprocessing import StandardScaler
    from xgboost import XGBRegressor
    from data_code.single_model_networks import make_network
    from coffee_service.training import make_windows

    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)
    features, _ = build_features(sources, sessions, cutoff)
    if news_feature is not None:
        features = features.join(news_feature.sort_index().rename("news_sentiment"))
    input_columns = [*columns, "news_sentiment"] if use_news else list(columns)
    news_present = features.news_sentiment.notna() if news_feature is not None else pd.Series(False, index=sessions)
    prices, _ = transform_market(sources, sessions)
    targets = make_targets(prices.close, (horizon,))
    target_dates, y = targets[f"target_date_{horizon}"], targets[f"y_{horizon}"]
    finite = pd.Series(np.isfinite(features[columns]).all(axis=1), index=sessions)
    window_ok = finite.rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
    mature = y.notna() & target_dates.notna()
    train = np.flatnonzero(window_ok & mature & target_dates.le(cutoff)
                           & (sessions >= ("2022-01-01" if news_feature is not None else "2015-01-01")))
    evaluation_ok = window_ok & mature & (sessions > cutoff)
    if evaluation_end is not None:
        evaluation_ok &= target_dates.le(evaluation_end)
    evaluation = np.flatnonzero(evaluation_ok)
    if not len(train) or not len(evaluation):
        raise ValueError("No usable training/evaluation windows")
    assert target_dates.iloc[train].max() <= cutoff < sessions[evaluation[0]]
    used = np.unique((train[:, None]-np.arange(LOOKBACK)[None, :]).ravel())
    scaler = StandardScaler().fit(features.iloc[used][columns])
    values = scaler.transform(features[columns]).astype("float32")
    if use_news:
        # A missing article is a tensor placeholder; the observed/missing flag stays separate.
        values = np.column_stack((values, features.news_sentiment.fillna(0).to_numpy(dtype="float32")))
    tx, ex = make_windows(values, train), make_windows(values, evaluation)
    mean, std = float(y.iloc[train].mean()), float(y.iloc[train].std(ddof=0))
    ty = ((y.iloc[train].to_numpy()-mean)/std).astype("float32")
    artifact = Path(artifact)
    if name == "XGBoost":
        flat_columns = [f"lag_{lag}_{c}" for lag in range(LOOKBACK-1, -1, -1) for c in input_columns]
        model = XGBRegressor(n_estimators=int(setting), max_depth=4, learning_rate=.03, reg_lambda=5,
                             random_state=SEED, n_jobs=1, objective="reg:squarederror", tree_method="hist")
        model.fit(pd.DataFrame(tx.reshape(len(tx), -1), columns=flat_columns), ty)
        prediction = model.predict(pd.DataFrame(ex.reshape(len(ex), -1), columns=flat_columns))
        model.save_model(artifact.with_suffix(".json"))
        restored = XGBRegressor()
        restored.load_model(artifact.with_suffix(".json"))
        roundtrip = restored.predict(pd.DataFrame(ex[:64].reshape(min(64, len(ex)), -1), columns=flat_columns))
    else:
        torch.manual_seed(SEED)
        model = make_network(name, len(input_columns), LOOKBACK)
        loader = DataLoader(TensorDataset(torch.from_numpy(tx), torch.from_numpy(ty)), batch_size=64,
                            shuffle=True, generator=torch.Generator().manual_seed(SEED))
        optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
        for _ in range(int(setting)):
            model.train()
            for bx, by in loader:
                optimizer.zero_grad()
                loss = nn.functional.mse_loss(model(bx), by)
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite network loss")
                loss.backward()
                nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
                optimizer.step()
        model.eval()
        with torch.no_grad():
            prediction = np.concatenate([model(torch.from_numpy(ex[i:i+64])).numpy() for i in range(0, len(ex), 64)])
        torch.save(model.state_dict(), artifact.with_suffix(".pt"))
        restored = make_network(name, len(input_columns), LOOKBACK)
        restored.load_state_dict(torch.load(artifact.with_suffix(".pt"), weights_only=True))
        restored.eval()
        with torch.no_grad():
            roundtrip = restored(torch.from_numpy(ex[:64])).numpy()
    np.testing.assert_allclose(roundtrip, prediction[:64], rtol=0, atol=0)
    input_mean = np.r_[scaler.mean_, 0.] if use_news else scaler.mean_
    input_scale = np.r_[scaler.scale_, 1.] if use_news else scaler.scale_
    np.savez(artifact.with_suffix(".npz"), features=np.array(input_columns), input_mean=input_mean,
             input_scale=input_scale, target_mean=mean, target_std=std, train_cutoff=str(cutoff.date()),
             train_rows=len(train), train_origin_dates=np.asarray(sessions[train].strftime("%Y-%m-%d"), dtype=str),
             train_target_dates=np.asarray(target_dates.iloc[train].dt.strftime("%Y-%m-%d"), dtype=str),
             use_news=use_news, horizon=horizon, setting=setting, seed=SEED)
    result = pd.DataFrame({"origin_date": sessions[evaluation], "target_date": target_dates.iloc[evaluation].to_numpy(),
        "actual_return": y.iloc[evaluation].to_numpy(), "current_price": prices.close.iloc[evaluation].to_numpy(),
        "predicted_return": prediction*std+mean, "model": name, "horizon": horizon,
        "setting": setting, "train_cutoff": cutoff, "train_rows": len(train),
        "feature_count": len(input_columns), "use_news": use_news,
        "news_present": news_present.iloc[evaluation].to_numpy(dtype=bool)})
    for column in ("origin_date", "target_date", "train_cutoff"):
        result[column] = result[column].astype("datetime64[ns]")
    scores(result, result.predicted_return)
    return result


def paired_interval(frame, prediction, baseline, block, repeats=1000):
    scores(frame, prediction)
    scores(frame, baseline)
    y, p, b, c = (np.asarray(v, float) for v in (frame.actual_return, prediction, baseline, frame.current_price))
    gains = (c*(np.exp(y)-np.exp(b)))**2-(c*(np.exp(y)-np.exp(p)))**2
    n = len(y)
    if not 1 <= block <= n:
        raise ValueError("Invalid bootstrap block")
    starts = np.random.default_rng(SEED).integers(0, n, (repeats, int(np.ceil(n/block))))
    ix = ((starts[..., None]+np.arange(block)) % n).reshape(repeats, -1)[:, :n]
    low, high = np.quantile(gains[ix].mean(axis=1), [.025, .975])
    return {"가격 MSE 개선": gains.mean(), "95% 하한": low, "95% 상한": high}
