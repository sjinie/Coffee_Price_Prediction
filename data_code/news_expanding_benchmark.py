"""Mature-label expanding folds for conditional Jev feature research."""

from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.preprocessing import StandardScaler

from coffee_service.features import make_targets
from coffee_service.training import make_windows
from coffee_service.transform import transform_market
from data_code.fixed_ensemble_news import LOOKBACK, SEED, build_features
from data_code.single_model_networks import make_network


def prepare_fold(sources, sessions, columns, news_feature, horizon, cutoff, evaluation_end):
    """Use only mature labels and fit feature statistics on training windows."""
    sessions = pd.DatetimeIndex(sessions)
    cutoff, evaluation_end = pd.Timestamp(cutoff), pd.Timestamp(evaluation_end)
    if (sessions.empty or sessions.tz is not None or sessions.hasnans or not sessions.is_unique
            or not sessions.is_monotonic_increasing or not sessions.equals(sessions.normalize())
            or any(day.tz is not None or day != day.normalize() for day in (cutoff, evaluation_end))
            or evaluation_end <= cutoff or not isinstance(horizon, int) or horizon < 1
            or len(columns) != 27 or len(set(columns)) != 27 or "news_sentiment" in columns):
        raise ValueError("Invalid sessions, dates, horizon, or 27 common features")
    if news_feature is None:
        news_feature = pd.Series(dtype=float, index=pd.DatetimeIndex([], name=sessions.name))
    if (not isinstance(news_feature, pd.Series) or not isinstance(news_feature.index, pd.DatetimeIndex)
            or news_feature.index.tz is not None or news_feature.index.hasnans
            or news_feature.index.has_duplicates or not news_feature.index.is_monotonic_increasing
            or not news_feature.index.equals(news_feature.index.normalize())
            or not pd.api.types.is_numeric_dtype(news_feature.dtype)):
        raise ValueError("news_feature needs unique naive daily dates and numeric scores")
    scores = news_feature.dropna().to_numpy(dtype=float)
    if not np.isfinite(scores).all() or (np.abs(scores) > 1).any():
        raise ValueError("news_feature scores must be finite within [-1, 1] or NaN")

    features, _ = build_features(sources, sessions, cutoff)
    if not features.index.equals(sessions) or not set(columns).issubset(features.columns):
        raise ValueError("Feature dates or columns differ from requested sessions")
    prices, _ = transform_market(sources, sessions)
    if not prices.index.equals(sessions):
        raise ValueError("Price dates differ from requested sessions")
    targets = make_targets(prices.close, (horizon,))
    dates, y = targets[f"target_date_{horizon}"], targets[f"y_{horizon}"]
    common = features[columns]
    finite = pd.Series(np.isfinite(common.to_numpy(dtype=float)).all(axis=1), index=sessions)
    window_ok = finite.rolling(LOOKBACK, min_periods=LOOKBACK).sum().eq(LOOKBACK)
    mature = np.isfinite(y) & dates.notna() & np.isfinite(prices.close) & prices.close.gt(0)
    train = np.flatnonzero(window_ok & mature & (sessions >= pd.Timestamp("2022-01-01"))
                           & (sessions <= cutoff) & dates.le(cutoff))
    evaluation = np.flatnonzero(window_ok & mature & (sessions > cutoff)
                                & dates.le(evaluation_end))
    if not len(train) or not len(evaluation):
        raise ValueError("No mature training or evaluation windows")
    used = np.unique((train[:, None] - np.arange(LOOKBACK)[None, :]).ravel())
    scaler = StandardScaler().fit(common.iloc[used])
    values = scaler.transform(common).astype("float32")
    news = news_feature.reindex(sessions)
    values = np.column_stack((values, news.fillna(0).to_numpy(dtype="float32")))
    train_x, eval_x = make_windows(values, train), make_windows(values, evaluation)
    train_y = y.iloc[train].to_numpy(dtype=float)
    mean, std = float(train_y.mean()), float(train_y.std(ddof=0))
    if not np.isfinite(std) or std <= 0:
        raise ValueError("Training targets need finite positive variance")
    present = news.iloc[evaluation].notna().to_numpy(dtype=bool)
    news_score = news.iloc[evaluation].to_numpy(dtype=float)
    reference = pd.DataFrame({
        "origin_date": sessions[evaluation], "target_date": dates.iloc[evaluation].to_numpy(),
        "actual_return": y.iloc[evaluation].to_numpy(dtype=float),
        "current_price": prices.close.iloc[evaluation].to_numpy(dtype=float),
        "news_score": news_score, "news_present": present,
        "news_used": present & (news_score != 0),
        "train_cutoff": cutoff, "train_rows": len(train),
    })
    metadata = {
        "features": np.asarray([*columns, "news_sentiment"]),
        "input_mean": np.r_[scaler.mean_, 0.], "input_scale": np.r_[scaler.scale_, 1.],
        "target_mean": mean, "target_std": std,
        "train_origin_dates": np.asarray(sessions[train].strftime("%Y-%m-%d"), dtype=str),
        "train_target_dates": np.asarray(dates.iloc[train].dt.strftime("%Y-%m-%d"), dtype=str),
        "train_cutoff": str(cutoff.date()), "train_rows": len(train),
        "horizon": horizon, "seed": SEED,
    }
    return {"train_x": train_x, "train_y": ((train_y-mean)/std).astype("float32"),
            "eval_x": eval_x, "reference": reference, "metadata": metadata}


def fit_candidates(name, settings, prepared, artifact_dir):
    """Fit both feature modes on identical rows and route only nonzero news origins."""
    import torch
    from torch import nn
    from torch.utils.data import DataLoader, TensorDataset
    from xgboost import XGBRegressor
    from lightgbm import Booster, LGBMRegressor

    neural = {"DLinear", "NLinear", "PatchTST", "TimesNet"}
    if name not in neural | {"XGBoost", "LightGBM"}:
        raise ValueError(f"Unknown model: {name}")
    settings = tuple(settings)
    if not settings or any(not isinstance(s, int) or s < 1 for s in settings) or len(set(settings)) != len(settings):
        raise ValueError("settings must be distinct positive integers")
    train_x, train_y, eval_x = (prepared[key] for key in ("train_x", "train_y", "eval_x"))
    reference, metadata = prepared["reference"], prepared["metadata"]
    if (train_x.ndim != 3 or eval_x.ndim != 3 or train_x.shape[1:] != (LOOKBACK, 28)
            or eval_x.shape[1:] != (LOOKBACK, 28) or train_y.shape != (len(train_x),)
            or len(reference) != len(eval_x) or not len(train_x) or not len(eval_x)
            or not np.isfinite(train_x).all() or not np.isfinite(eval_x).all()
            or not np.isfinite(train_y).all()):
        raise ValueError("Invalid prepared windows")
    artifact_dir = Path(artifact_dir)
    artifact_dir.mkdir(parents=True, exist_ok=True)
    predictions = {}
    torch.set_num_threads(1)
    torch.use_deterministic_algorithms(True)

    def predict_network(network, values):
        network.eval()
        with torch.no_grad():
            return np.concatenate([network(torch.from_numpy(values[i:i+64])).numpy()
                                   for i in range(0, len(values), 64)])

    for mode, width in (("base", 27), ("news", 28)):
        tx, ex = np.ascontiguousarray(train_x[:, :, :width]), np.ascontiguousarray(eval_x[:, :, :width])
        if name in neural:
            torch.manual_seed(SEED)
            model = make_network(name, width, LOOKBACK)
            loader = DataLoader(TensorDataset(torch.from_numpy(tx), torch.from_numpy(train_y)),
                                batch_size=64, shuffle=True, generator=torch.Generator().manual_seed(SEED))
            optimizer = torch.optim.AdamW(model.parameters(), lr=.001, weight_decay=.01)
            # Save during training, restore only after the final epoch so checkpoint
            # construction cannot advance the RNG used by later dropout epochs.
            for epoch in range(1, max(settings)+1):
                model.train()
                for bx, by in loader:
                    optimizer.zero_grad()
                    loss = nn.functional.mse_loss(model(bx), by)
                    if not torch.isfinite(loss):
                        raise ValueError("Nonfinite network loss")
                    loss.backward()
                    nn.utils.clip_grad_norm_(model.parameters(), 1., error_if_nonfinite=True)
                    optimizer.step()
                if epoch in settings:
                    predictions[mode, epoch] = predict_network(model, ex)
                    torch.save(model.state_dict(), artifact_dir / f"{name}_{epoch}_{mode}.pt")
            for setting in settings:
                path = artifact_dir / f"{name}_{setting}_{mode}.pt"
                restored = make_network(name, width, LOOKBACK)
                restored.load_state_dict(torch.load(path, weights_only=True))
                np.testing.assert_allclose(predictions[mode, setting], predict_network(restored, ex),
                                           rtol=0, atol=0)
        else:
            columns = [f"lag_{lag}_{column}" for lag in range(LOOKBACK-1, -1, -1)
                       for column in metadata["features"][:width]]
            flat_train = pd.DataFrame(tx.reshape(len(tx), -1), columns=columns)
            flat_eval = pd.DataFrame(ex.reshape(len(ex), -1), columns=columns)
            for setting in settings:
                common = dict(n_estimators=setting, max_depth=4, learning_rate=.03, reg_lambda=5,
                              random_state=SEED, n_jobs=1)
                if name == "XGBoost":
                    model = XGBRegressor(**common, objective="reg:squarederror", tree_method="hist")
                    path = artifact_dir / f"{name}_{setting}_{mode}.json"
                    model.fit(flat_train, train_y)
                    prediction = model.predict(flat_eval)
                    model.save_model(path)
                    restored = XGBRegressor()
                    restored.load_model(path)
                    roundtrip = restored.predict(flat_eval)
                else:
                    model = LGBMRegressor(**common, num_leaves=15, min_child_samples=40,
                                          deterministic=True, force_col_wise=True, verbosity=-1)
                    path = artifact_dir / f"{name}_{setting}_{mode}.txt"
                    model.fit(flat_train, train_y)
                    prediction = model.predict(flat_eval)
                    model.booster_.save_model(str(path))
                    roundtrip = Booster(model_file=str(path)).predict(flat_eval)
                np.testing.assert_allclose(roundtrip, prediction, rtol=0, atol=0)
                predictions[mode, setting] = prediction
        for setting in settings:
            details = {**metadata, "features": metadata["features"][:width],
                       "input_mean": metadata["input_mean"][:width],
                       "input_scale": metadata["input_scale"][:width],
                       "model": name, "setting": setting, "use_news": mode == "news"}
            np.savez(artifact_dir / f"{name}_{setting}_{mode}.npz", **details)

    result = []
    used = reference.news_used.to_numpy(dtype=bool)
    for setting in settings:
        base = predictions["base", setting] * metadata["target_std"] + metadata["target_mean"]
        news = predictions["news", setting] * metadata["target_std"] + metadata["target_mean"]
        if not np.isfinite(base).all() or not np.isfinite(news).all():
            raise ValueError("Nonfinite model predictions")
        frame = reference.copy()
        frame["model"], frame["setting"] = name, setting
        frame["base_prediction"], frame["news_prediction"] = base, news
        frame["predicted_return"] = np.where(used, news, base)
        frame["feature_count"], frame["use_news"] = 28, True
        result.append(frame)
    return pd.concat(result, ignore_index=True)
