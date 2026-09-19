import numpy as np
import pandas as pd
import pytest

from coffee_service.features import FeatureDataset, PRODUCTION_FEATURES
from coffee_service.intelligence import (_aligned_base, _choose_selection, fixed_ensemble, load_intelligence,
                                         make_bundle, metric_summary, model_records, predict_intelligence,
                                         save_intelligence)
from coffee_service.transform import coffee_sessions
from coffee_service.news import normalize_articles


def dataset(end="2024-03-29"):
    sessions = coffee_sessions("2023-01-01", end)
    returns = .003 + .002 * np.sin(np.arange(len(sessions)) / 5)
    close = pd.Series(100 * np.exp(np.cumsum(returns)), index=sessions)
    values = np.arange(len(sessions), dtype=float)
    features = pd.DataFrame({column: values * (position + 1) / 100 for position, column in enumerate(PRODUCTION_FEATURES)}, index=sessions)
    return FeatureDataset(features, pd.DataFrame({"close": close}), sessions, [], {})


def base_rows(origins, value=0., price=100.):
    return pd.DataFrame([{"origin_date": day, "target_date": day + pd.Timedelta(days=90), "horizon": horizon,
                          "predicted_return": value, "predicted_price": price}
                         for horizon in (5, 20, 60) for day in origins])


def test_metrics_handles_single_class_and_threshold():
    metrics = metric_summary([1, 1], [.5, .9])
    assert metrics["auc"] is None
    assert metrics["tp"] == 2
    with pytest.raises(ValueError, match="probability"):
        metric_summary([0, 1], [0, np.nan])


def test_alignment_rejects_duplicate_and_mismatch():
    index = pd.DatetimeIndex(["2024-01-02"])
    frame = pd.DataFrame({"origin_date": [index[0], index[0]], "horizon": [5, 5], "predicted_return": [0., 0.], "predicted_price": [1., 1.]})
    with pytest.raises(ValueError, match="duplicate"):
        _aligned_base(frame, 5, index)


def test_selection_rejects_one_lucky_fold():
    folds = [{"relative_rmse_improvement": .10, "direction_balanced_accuracy": .6}, {"relative_rmse_improvement": -.06, "direction_balanced_accuracy": .6}, {"relative_rmse_improvement": -.06, "direction_balanced_accuracy": .6}]
    assert _choose_selection([{"task": "regression", "name": "x", "feature_set": "numeric", "folds": folds}], 60)["regression"] == "base"


def test_fixed_ensemble_uses_only_approved_formula():
    assert fixed_ensemble([.2], [.75], .1, [.4], 3)[0] == pytest.approx(.129)
    with pytest.raises(ValueError, match="same-length"):
        fixed_ensemble([.2], [.75, .8], .1)


def test_native_torch_lightgbm_round_trip_in_fresh_process():
    import os
    import subprocess
    import sys
    env = {key: value for key, value in os.environ.items() if key != "OMP_NUM_THREADS"}
    code = """
import coffee_service
import io, joblib, numpy as np, torch
from coffee_service.modeling import DLinear
model = DLinear(7).eval()
x = torch.ones(3, 60, 7)
expected = model(x).detach().numpy()
from coffee_service.intelligence import _model_pair
classifier, _ = _model_pair('lightgbm')
features = np.arange(140, dtype=float).reshape(20, 7)
classifier.fit(features, np.arange(20) % 2)
buffer = io.BytesIO()
joblib.dump(classifier, buffer)
buffer.seek(0)
restored = joblib.load(buffer)
assert np.isfinite(restored.predict_proba(features)).all()
np.testing.assert_allclose(model(x).detach().numpy(), expected)
"""
    result = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


def test_serialization_and_future_rejection(tmp_path):
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    path = tmp_path / "intelligence.joblib"
    save_intelligence(bundle, path)
    restored = load_intelligence(path)
    origins = data.sessions[data.sessions > pd.Timestamp("2023-12-31")]
    base = base_rows(origins)
    result = predict_intelligence(data, base, None, restored, news_available=False)
    assert result.probability_up.notna().all()
    assert result.signal_status.eq("fallback").all()
    assert (pd.to_datetime(result.origin_date) > pd.Timestamp("2023-12-31")).all()
    assert set(result.final_direction) <= {"UP", "DOWN", "FLAT"}
    assert len(model_records(restored)) == 3
    names = {record["horizons"][0]: record["name"] for record in model_records(restored)}
    assert names[5] == "Persistence + logistic_ridge (P(up))"
    assert names[60] == "DLinear + logistic_ridge (P(up))"


def test_bundle_prunes_unselected_pairs_and_round_trips_selected_news_paths(tmp_path):
    data = dataset()
    from coffee_service.intelligence import train_models
    final_models = train_models(data, training_end="2023-12-31")
    selections = {}
    for horizon, window in {5: 1, 20: 7, 60: 14}.items():
        selections[horizon] = {
            "classifier": {"name": "logistic_ridge", "feature_set": f"news_{window}d", "candidate": 1},
            "numeric_classifier": {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1},
            "regression": {"name": "logistic_ridge", "feature_set": f"news_{window}d", "candidate": 1},
            "classifier_validated": True,
        }
    bundle = make_bundle(data, training_end="2023-12-31", models=final_models, selections=selections)
    for horizon, window in {5: 1, 20: 7, 60: 14}.items():
        assert set(bundle["models"][horizon]) == {"numeric", f"news_{window}d"}
        assert set(bundle["models"][horizon]["numeric"]["models"]) == {"logistic_ridge"}
        assert set(bundle["models"][horizon][f"news_{window}d"]["models"]) == {"logistic_ridge"}
        assert set(final_models[horizon]) == {"numeric", *{f"news_{value}d" for value in {5: (1, 3, 7), 20: (7, 14, 30), 60: (14, 30, 60)}[horizon]}}
        assert set(final_models[horizon]["numeric"]["models"]) == {"logistic_ridge", "catboost", "lightgbm"}
    path = tmp_path / "pruned.joblib"
    save_intelligence(bundle, path)
    restored = load_intelligence(path)
    articles = normalize_articles([{
        "url": "https://example.test/coffee-frost",
        "title": "Coffee frost reduces supply",
        "published_at": "2024-03-28T00:00:00Z",
        "collected_at": "2024-03-28T22:00:00Z",
    }])
    result = predict_intelligence(data, base_rows([data.sessions[-1]], .1, 110.5170918), articles, restored,
                                 news_available=True, availability_mode="live")
    assert result.probability_up.notna().all()
    assert result.predicted_return.notna().all()


def test_artifact_requires_matching_news_analysis_version(tmp_path):
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    bundle["analysis_version"] = "old-rules"
    path = tmp_path / "old.joblib"
    import joblib
    joblib.dump(bundle, path)
    with pytest.raises(ValueError, match="unsupported"):
        load_intelligence(path)


def test_news_outage_is_null_and_uses_numeric_fallback():
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    for horizon in (5, 20, 60):
        bundle["selection"][horizon]["classifier"]["feature_set"] = f"news_{(1, 7, 14)[(5, 20, 60).index(horizon)]}d"
    origins = data.sessions[data.sessions > pd.Timestamp("2023-12-31")]
    base = base_rows(origins)
    result = predict_intelligence(data, base, None, bundle, news_available=False, availability_mode="historical")
    assert result.news_impact_score.isna().all()
    assert result.news_article_count.isna().all()
    assert result.signal_status.eq("fallback").all()


def test_news_window_is_individual_and_nonfinite_rows_are_not_scored():
    data = dataset()
    data.features.iloc[-1, 0] = np.inf
    from coffee_service.intelligence import feature_frame
    numeric = feature_frame(data, horizon=5)
    assert numeric.columns.tolist() == PRODUCTION_FEATURES
    assert data.sessions[-1] not in numeric.index


def test_train_models_accepts_precomputed_feature_frames():
    from coffee_service.intelligence import train_models
    data = dataset()
    frames = {(5, "numeric"): __import__("coffee_service.intelligence", fromlist=["feature_frame"]).feature_frame(data, horizon=5)}
    models = train_models(data, training_end="2023-12-31", model_names=("logistic_ridge",), feature_frames=frames)
    assert models[5]["numeric"]["columns"] == PRODUCTION_FEATURES


def test_classifier_gate_rejects_single_class_fold_and_bad_brier():
    folds = [{"valid": False, "balanced_accuracy": .9, "baseline_balanced_accuracy": .5, "brier": .1, "train_prior_brier": .2}] * 3
    assert _choose_selection([{"task": "classifier", "name": "x", "feature_set": "numeric", "folds": folds}], 5)["classifier"] == "numeric"


def test_rejected_regression_preserves_base_price_and_incomplete_is_null():
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    origin = data.sessions[-1]
    data.features.loc[origin, PRODUCTION_FEATURES[0]] = np.nan
    base = base_rows([origin], .1, 110.5170918)
    result = predict_intelligence(data, base, None, bundle, news_available=False)
    assert result.probability_up.isna().all()
    assert result.predicted_return.tolist() == [.1, .1, .1]
    assert result.predicted_price.tolist() == pytest.approx([110.5170918] * 3)


def test_adopted_regression_with_no_complete_rows_keeps_base_predictions():
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    for horizon in (5, 20, 60):
        bundle["selection"][horizon]["regression"] = {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1}
    origin = data.sessions[-1]
    data.features.loc[origin, PRODUCTION_FEATURES] = np.nan
    result = predict_intelligence(data, base_rows([origin], .1, 110.5170918), None, bundle, news_available=False)
    assert result.predicted_return.tolist() == [.1, .1, .1]
    assert result.predicted_price.tolist() == pytest.approx([110.5170918] * 3)


def test_selected_numeric_regression_skips_empty_ten_origin_batch():
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    for horizon in (5, 20, 60):
        bundle["selection"][horizon]["regression"] = {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1}
    origins = data.sessions[-10:]
    data.features.loc[origins, PRODUCTION_FEATURES] = np.nan
    result = predict_intelligence(data, base_rows(origins, .1, 110.5170918), None, bundle, news_available=False)
    assert len(result) == 30
    assert result.predicted_return.eq(.1).all()
    assert result.predicted_price.eq(110.5170918).all()


def test_prediction_rejects_training_origin_and_uses_return_for_direction():
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    bad = pd.DataFrame([{"origin_date": pd.Timestamp("2023-12-29"), "target_date": pd.Timestamp("2024-01-09"), "horizon": 5, "predicted_return": 0., "predicted_price": 100.}])
    with pytest.raises(ValueError, match="training cutoff"):
        predict_intelligence(data, bad, None, bundle, news_available=False)
    origin = data.sessions[-1]
    base = base_rows([origin])
    result = predict_intelligence(data, base, None, bundle, news_available=False)
    assert result.final_direction.eq("FLAT").all()


def test_news_failure_uses_only_stored_numeric_classifier():
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    for horizon in (5, 20, 60):
        bundle["selection"][horizon].update({
            "classifier": {"name": "logistic_ridge", "feature_set": f"news_{(1, 7, 14)[(5, 20, 60).index(horizon)]}d", "candidate": 1},
            "numeric_classifier": {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1},
            "classifier_validated": True,
        })
    result = predict_intelligence(data, base_rows([data.sessions[-1]]), None, bundle, news_available=False)
    assert result.probability_up.notna().all()
    assert result.classifier_version.str.contains("numeric").all()
    assert result.signal_status.eq("fallback").all()


def test_availability_mode_is_part_of_model_record_identity():
    bundle = make_bundle(dataset(), training_end="2023-12-31")
    historical = model_records(bundle, availability_mode="historical")
    live = model_records(bundle, availability_mode="live")
    assert {item["model_id"] for item in historical}.isdisjoint({item["model_id"] for item in live})
    assert all(item["model_id"].endswith("-historical") for item in historical)
    assert all(item["metrics"]["news_availability"] == "historical" for item in historical)
    assert all(item["metrics"]["news_availability"] == "live" for item in live)
    from types import SimpleNamespace
    base = SimpleNamespace(metadata={"model_id": "original-h60", "notebook_metrics": {"test_rmse": .16794}})
    records = model_records(bundle, base_bundle=base)
    assert records[2]["metrics"]["test_rmse"] == .16794
    assert "test_rmse" not in records[0]["metrics"]
    bundle["selection"][60]["regression"] = {"name": "logistic_ridge", "feature_set": "numeric"}
    assert "test_rmse" not in model_records(bundle, base_bundle=base)[2]["metrics"]


def test_live_precollection_news_selection_falls_back_per_origin():
    data = dataset()
    windows = {5: 1, 20: 7, 60: 14}
    selections = {}
    for horizon, window in windows.items():
        selections[horizon] = {
            "classifier": {"name": "logistic_ridge", "feature_set": f"news_{window}d", "candidate": 1},
            "numeric_classifier": {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1},
            "regression": {"name": "logistic_ridge", "feature_set": f"news_{window}d", "candidate": 1},
            "classifier_validated": True,
        }
    bundle = make_bundle(data, training_end="2023-12-31", selections=selections)
    origin = data.sessions[-1]
    articles = normalize_articles([{
        "url": "https://example.test/coffee-frost",
        "title": "Coffee frost reduces supply",
        "published_at": "2024-03-28T00:00:00Z",
        "collected_at": "2024-04-05T00:00:00Z",
    }])
    base = base_rows([origin], .1, 110.5170918)
    live = predict_intelligence(data, base, articles, bundle, news_available=True, availability_mode="live")
    assert live.model_id.str.endswith("-live").all()
    assert live.probability_up.notna().all()
    assert live.classifier_version.str.contains("numeric").all()
    assert live.signal_status.eq("fallback").all()
    assert live.predicted_return.eq(.1).all()
    assert live.predicted_price.eq(110.5170918).all()
    assert live.news_impact_score.isna().all()
    assert live.news_article_count.isna().all()

    historical = predict_intelligence(data, base, articles, bundle, news_available=True, availability_mode="historical")
    assert historical.model_id.str.endswith("-historical").all()
    assert not historical.signal_status.eq("fallback").any()


def test_live_precollection_without_numeric_classifier_is_unavailable():
    data = dataset()
    selections = {}
    for horizon, window in {5: 1, 20: 7, 60: 14}.items():
        selections[horizon] = {
            "classifier": {"name": "logistic_ridge", "feature_set": f"news_{window}d", "candidate": 1},
            "numeric_classifier": None,
            "regression": "base",
            "classifier_validated": False,
        }
    bundle = make_bundle(data, training_end="2023-12-31", selections=selections)
    articles = normalize_articles([{
        "url": "https://example.test/coffee-frost",
        "title": "Coffee frost reduces supply",
        "published_at": "2024-03-28T00:00:00Z",
        "collected_at": "2024-04-05T00:00:00Z",
    }])
    result = predict_intelligence(data, base_rows([data.sessions[-1]]), articles, bundle, news_available=True)
    assert result.probability_up.isna().all()
    assert result.signal_status.eq("unavailable").all()


def test_live_precollection_news_regression_keeps_base_with_numeric_classifier():
    data = dataset()
    selections = {horizon: {
        "classifier": {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1},
        "numeric_classifier": {"name": "logistic_ridge", "feature_set": "numeric", "candidate": 1},
        "regression": {"name": "logistic_ridge", "feature_set": f"news_{window}d", "candidate": 1},
        "classifier_validated": False,
    } for horizon, window in {5: 1, 20: 7, 60: 14}.items()}
    bundle = make_bundle(data, training_end="2023-12-31", selections=selections)
    articles = normalize_articles([{
        "url": "https://example.test/coffee-frost",
        "title": "Coffee frost reduces supply",
        "published_at": "2024-03-28T00:00:00Z",
        "collected_at": "2024-04-05T00:00:00Z",
    }])
    result = predict_intelligence(data, base_rows([data.sessions[-1]], .1, 110.5170918), articles, bundle,
                                 news_available=True, availability_mode="live")
    assert result.probability_up.notna().all()
    assert result.signal_status.eq("experimental").all()
    assert result.predicted_return.eq(.1).all()
    assert result.predicted_price.eq(110.5170918).all()
    assert result.news_impact_score.isna().all()


def test_live_successfully_collected_empty_news_is_a_zero_signal():
    data = dataset()
    bundle = make_bundle(data, training_end="2023-12-31")
    empty = normalize_articles([])
    empty.attrs["last_collected_at"] = "2024-03-28T22:00:00+00:00"
    result = predict_intelligence(data, base_rows([data.sessions[-1]]), empty, bundle,
                                 news_available=True, availability_mode="live")
    assert result.news_impact_score.eq(0).all()
    assert result.news_article_count.eq(0).all()
    assert not result.signal_status.eq("fallback").any()
