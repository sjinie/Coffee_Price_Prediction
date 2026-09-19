"""Purged expanding validation. 이미 본 2024~2025년은 선택 후 기술 평가만 한다."""
from __future__ import annotations

import argparse
from pathlib import Path
import numpy as np
import pandas as pd

from .features import PRODUCTION_FEATURES, assemble_features, load_sources, make_targets
from .intelligence import (DEFAULT_ARTIFACT, HORIZONS, NEWS_WINDOWS, _choose_selection, _probability,
                           feature_frame, fixed_ensemble, make_bundle, metric_summary,
                           save_intelligence, train_models)
from .modeling import DEFAULT_ARTIFACT as BASE_ARTIFACT, load_bundle
from .news import aggregate_news, news_feature_columns, read_news
from .training import make_windows, train_bundle


def prepare_features(dataset, articles):
    """타깃과 무관한 집계는 한 번 만들고 각 window를 별도로 비교한다."""
    numeric = feature_frame(dataset, news=False)
    windows = sorted({w for values in NEWS_WINDOWS.values() for w in values})
    news = aggregate_news(articles, dataset.sessions, windows)
    frames = {}
    for horizon in HORIZONS:
        frames[horizon, "numeric"] = numeric
        for window in NEWS_WINDOWS[horizon]:
            frames[horizon, f"news_{window}d"] = numeric.join(news[news_feature_columns(window)])
    return frames, news


def regression_metrics(actual, predicted):
    actual, predicted = np.asarray(actual, float), np.asarray(predicted, float)
    if actual.shape != predicted.shape or not len(actual) or not np.isfinite([actual, predicted]).all():
        raise ValueError("유한한 실제·예측 수익률이 같은 날짜 순서로 필요합니다.")
    # 실제 보합은 binary BA에서 제외. 예측 보합은 상승/하락 어느 쪽도 맞히지 못한다.
    recalls = [np.mean(np.sign(predicted[actual * sign > 0]) == sign)
               for sign in (-1, 1) if (actual * sign > 0).any()]
    return {
        "rmse": float(np.sqrt(np.mean((actual - predicted) ** 2))),
        "mae": float(np.mean(np.abs(actual - predicted))),
        "direction_accuracy": float(np.mean(np.sign(actual) == np.sign(predicted))),
        "direction_balanced_accuracy": float(np.mean(recalls)) if len(recalls) == 2 else None,
        "predicted_mean": float(predicted.mean()), "predicted_std": float(predicted.std()),
        "near_zero_ratio": float(np.mean(np.abs(predicted) < .001)), "valid": len(recalls) == 2,
    }


def evaluation_origins(dataset, frame, horizon, start, end):
    targets = make_targets(dataset.prices["close"], (horizon,))
    index = frame.index
    actual = targets.loc[index, f"y_{horizon}"]
    target_date = targets.loc[index, f"target_date_{horizon}"]
    valid = ((index >= pd.Timestamp(start)) & (index <= pd.Timestamp(end))
             & target_date.between(pd.Timestamp(start), pd.Timestamp(end)) & np.isfinite(actual)
             & np.isfinite(frame.to_numpy(float)).all(axis=1))
    return index[valid]


def score_period(dataset, frames, models, baseline_bundle, start, end, *, split):
    """같은 origin/target에서 baseline, A/B 회귀, C/D 분류, 고정 ensemble을 평가한다."""
    targets = make_targets(dataset.prices["close"])
    metrics, predictions = [], []
    for horizon in HORIZONS:
        origins = evaluation_origins(dataset, frames[horizon, "numeric"], horizon, start, end)
        if not len(origins):
            raise ValueError(f"평가일이 없습니다: {horizon}/{start}/{end}")
        actual = targets.loc[origins, f"y_{horizon}"].to_numpy(float)
        target_dates = targets.loc[origins, f"target_date_{horizon}"]
        entry = models[horizon]["numeric"]
        train_max = pd.Timestamp(entry["train_target_max"])
        if train_max >= pd.Timestamp(start):
            raise ValueError("학습 정답일이 평가 시작일을 침범합니다.")
        indices = dataset.sessions.get_indexer(origins)
        baseline_return = (baseline_bundle.predict(make_windows(
            dataset.features[PRODUCTION_FEATURES].to_numpy(float), indices))
            if horizon == 60 else np.zeros(len(origins)))
        baseline = regression_metrics(actual, baseline_return)
        nonzero = actual != 0
        binary = (actual[nonzero] > 0).astype(int)
        prior = entry["train_prior"]
        prior_brier = float(np.mean((binary - prior) ** 2))
        majority_ba = metric_summary(binary, np.full(len(binary), float(prior >= .5)))["balanced_accuracy"]
        common = {"split": split, "fold_start": pd.Timestamp(start), "fold_end": pd.Timestamp(end),
                  "train_target_max": train_max, "horizon": horizon, "n": len(origins),
                  "training_rows": entry["training_rows"], "actual_up_ratio": float(np.mean(actual > 0)),
                  "actual_down_ratio": float(np.mean(actual < 0)), "zero_targets": int((~nonzero).sum())}

        def record_predictions(name, feature_set, candidate, predicted, probability):
            predictions.extend({
                **common, "origin_date": origin, "target_date": target_date,
                "name": name, "feature_set": feature_set, "candidate": candidate,
                "actual_return": float(value), "predicted_return": float(prediction), "probability_up": float(p),
            } for origin, target_date, value, prediction, p in zip(
                origins, target_dates, actual, predicted, probability))

        metrics.append({**common, "name": "current_production", "feature_set": "numeric",
                        "candidate": 0, "task": "regression", **baseline, "relative_rmse_improvement": 0.0})
        record_predictions("current_production", "numeric", 0, baseline_return, np.full(len(origins), np.nan))
        for name, probability in (("always_up", 1.), ("always_down", 0.),
                                  ("majority", float(prior >= .5)), ("train_prior", prior)):
            metrics.append({**common, "name": name, "feature_set": "baseline", "candidate": 0,
                            "task": "classifier", **metric_summary(binary, np.full(len(binary), probability)),
                            "valid": len(np.unique(binary)) == 2,
                            "baseline_balanced_accuracy": majority_ba, "train_prior_brier": prior_brier})
        for feature_set, model_entry in models[horizon].items():
            frame = frames[horizon, feature_set]
            if not origins.equals(evaluation_origins(dataset, frame, horizon, start, end)):
                raise ValueError("뉴스 전후 평가 날짜가 다릅니다.")
            x = frame.loc[origins, model_entry["columns"]]
            window = model_entry["window"]
            impact = x[f"news_weighted_impact_{window}d"].to_numpy(float) if window else np.zeros(len(x))
            for name, pair in model_entry["models"].items():
                probability = _probability(pair["classifier"], x)
                raw_return = np.asarray(pair["regressor"].predict(x), float)
                classification = metric_summary(binary, probability[nonzero])
                metrics.append({**common, "name": name, "feature_set": feature_set, "candidate": 1,
                                "task": "classifier", **classification,
                                "baseline_balanced_accuracy": majority_ba, "train_prior_brier": prior_brier,
                                "valid": len(np.unique(binary)) == 2})
                for candidate in (1, 2, 3):
                    prediction = fixed_ensemble(raw_return, probability, model_entry["train_return_std"], impact, candidate)
                    regression = regression_metrics(actual, prediction)
                    metrics.append({**common, "name": name, "feature_set": feature_set,
                                    "candidate": candidate, "task": "regression", **regression,
                                    "relative_rmse_improvement": 1 - regression["rmse"] / baseline["rmse"]})
                    record_predictions(name, feature_set, candidate, prediction, probability)
    return pd.DataFrame(metrics), pd.DataFrame(predictions)


def selection_candidates(metrics, horizon):
    frame = metrics.loc[metrics.horizon.eq(horizon) & metrics.name.isin(("logistic_ridge", "catboost", "lightgbm"))]
    return [{"task": task, "feature_set": feature_set, "name": name, "candidate": int(candidate),
             "folds": group.sort_values("fold_start").to_dict("records")}
            for (task, feature_set, name, candidate), group in frame.groupby(["task", "feature_set", "name", "candidate"])]


def choose_models(metrics):
    selections = {}
    for horizon in HORIZONS:
        candidates = selection_candidates(metrics, horizon)
        choice = _choose_selection(candidates, horizon)
        numeric = [c for c in candidates if c["task"] == "classifier" and c["feature_set"] == "numeric"]
        best_numeric = max(numeric, key=lambda c: np.mean([f["balanced_accuracy"] for f in c["folds"]]))
        fallback = {key: best_numeric[key] for key in ("name", "feature_set", "candidate")}
        choice["numeric_classifier"] = fallback
        choice["classifier_validated"] = isinstance(choice.get("classifier"), dict)
        if not choice["classifier_validated"]:
            choice["classifier"] = fallback
        selections[horizon] = choice
    return selections


def summarize(metrics):
    keys = ["horizon", "task", "feature_set", "name", "candidate"]
    values = [c for c in ("rmse", "mae", "direction_accuracy", "direction_balanced_accuracy",
                          "relative_rmse_improvement", "accuracy", "balanced_accuracy", "f1", "auc", "brier") if c in metrics]
    return metrics.groupby(keys, as_index=False).agg(folds=("fold_start", "nunique"),
        **{f"{column}_{stat}": (column, stat) for column in values for stat in ("mean", "std")})


def run_experiments(source_dir, articles, output_dir, artifact, validation_years=(2021, 2022, 2023)):
    years = tuple(validation_years)
    if len(years) != 3 or tuple(sorted(set(years))) != years or years[-1] >= 2024:
        raise ValueError("2024년 전의 서로 다른 오름차순 검증 연도 세 개가 필요합니다.")
    output_dir, artifact = Path(output_dir), Path(artifact)
    if output_dir.exists() or artifact.exists():
        raise FileExistsError("기존 결과를 보존하도록 새 output-dir/artifact를 지정하세요.")
    output_dir.mkdir(parents=True)
    dataset = assemble_features(load_sources(source_dir))
    print("뉴스 window별 입력을 조립합니다.", flush=True)
    frames, daily = prepare_features(dataset, articles)
    daily.rename_axis("date").to_parquet(output_dir / "news_daily_features.parquet")
    fold_metrics, fold_predictions = [], []
    for year in years:
        cutoff = f"{year - 1}-12-31"
        print(f"검증 {year}: {cutoff}까지 target을 확정한 사례만 학습", flush=True)
        models = train_models(dataset, articles, training_end=cutoff, feature_frames=frames)
        baseline_bundle = train_bundle(source_dir, train_end=cutoff)
        metrics, predictions = score_period(dataset, frames, models, baseline_bundle,
                                             f"{year}-01-01", f"{year}-12-31", split="validation")
        fold_metrics.append(metrics)
        fold_predictions.append(predictions)
        print(f"검증 {year}: {len(metrics)}개 지표행 완료", flush=True)
    metrics, oof = pd.concat(fold_metrics, ignore_index=True), pd.concat(fold_predictions, ignore_index=True)
    metrics.to_parquet(output_dir / "fold_metrics.parquet", index=False)
    oof.to_parquet(output_dir / "oof_predictions.parquet", index=False)
    summary = summarize(metrics)
    summary.to_parquet(output_dir / "validation_summary.parquet", index=False)
    selections = choose_models(metrics)
    print(f"Validation 선택 고정: {selections}", flush=True)
    final_models = train_models(dataset, articles, training_end="2023-12-31", feature_frames=frames)
    selection_metadata = {h: {"selection": selections[h], "validation_years": list(years),
                             "selection_bias": "같은 validation에서 후보를 선택함; 독립 outer 검증 아님",
                             "probability_target": "UP conditional on non-FLAT",
                             "news_mode": "published/modified historical research"} for h in HORIZONS}
    bundle = make_bundle(dataset, articles, selections=selections, validation_metrics=selection_metadata,
                         models=final_models)
    save_intelligence(bundle, artifact)
    print("선택 후 2024~2025년 기술 평가", flush=True)
    seen_metrics, seen_predictions = score_period(dataset, frames, final_models, load_bundle(BASE_ARTIFACT),
                                                   "2024-01-01", "2025-12-31", split="seen_test")
    seen_metrics.to_parquet(output_dir / "seen_test_metrics.parquet", index=False)
    seen_predictions.to_parquet(output_dir / "seen_test_predictions.parquet", index=False)
    print(f"완료: {output_dir} | artifact {artifact}", flush=True)
    return bundle, metrics, seen_metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_dir", type=Path)
    parser.add_argument("--news", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--validation-years", nargs="+", type=int, default=[2021, 2022, 2023])
    args = parser.parse_args()
    if not args.news.is_file():
        raise FileNotFoundError("뉴스 ablation에는 실제로 수집된 뉴스 파일이 필요합니다.")
    run_experiments(args.source_dir, read_news(args.news), args.output_dir, args.artifact, args.validation_years)


if __name__ == "__main__":
    main()
