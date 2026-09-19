import numpy as np
import pandas as pd

from coffee_service.features import FeatureDataset, PRODUCTION_FEATURES
from coffee_service.inference import generate_predictions, incremental_predictions
from coffee_service.modeling import DLinear, HORIZON, ModelBundle
from coffee_service.transform import coffee_sessions


class Bundle:
    def predict(self, windows):
        return windows[:, -1, 0] * 0.001


def model_bundle():
    return ModelBundle(
        model=DLinear(len(PRODUCTION_FEATURES)),
        feature_columns=PRODUCTION_FEATURES,
        scaler_mean=np.zeros(len(PRODUCTION_FEATURES)),
        scaler_scale=np.ones(len(PRODUCTION_FEATURES)),
        target_mean=0.0,
        target_std=1.0,
        metadata={},
    )


def dataset(end="2024-04-05"):
    sessions = coffee_sessions("2023-09-01", end)
    close = pd.Series(np.arange(len(sessions), dtype=float) + 100, index=sessions)
    prices = pd.DataFrame({"close": close}, index=sessions)
    features = pd.DataFrame(1.0, index=sessions, columns=PRODUCTION_FEATURES)
    return FeatureDataset(features, prices, sessions, [], {})


def test_incremental_predictions_keep_pending_origins_and_update_actuals():
    early = incremental_predictions(dataset("2024-03-01"), Bundle())
    mature_dataset = dataset()
    later = incremental_predictions(mature_dataset, Bundle())

    pending = early[(early.horizon == HORIZON) & early.actual_price.isna()]
    assert not pending.empty
    origin = pending.iloc[0].origin_date
    before = pending.iloc[0]
    after = later[(later.horizon == HORIZON) & (later.origin_date == origin)].iloc[0]

    assert before.target_date == after.target_date
    assert before.predicted_price == after.predicted_price
    assert pd.notna(after.actual_price)
    assert after.actual_price == mature_dataset.prices.loc[pd.Timestamp(after.target_date), "close"]
    assert (early.origin_date >= pd.Timestamp("2024-01-01").date()).all()


def test_future_feature_change_does_not_change_existing_origin_prediction():
    original = dataset()
    expected = incremental_predictions(original, Bundle())
    changed = dataset()
    changed.features.loc["2024-03-01":, PRODUCTION_FEATURES] = 999.0
    actual = incremental_predictions(changed, Bundle())

    origin = pd.Timestamp("2024-01-02").date()
    expected_row = expected[(expected.horizon == HORIZON) & (expected.origin_date == origin)].iloc[0]
    actual_row = actual[(actual.horizon == HORIZON) & (actual.origin_date == origin)].iloc[0]
    changed_origin = pd.Timestamp("2024-03-05").date()
    expected_changed = expected[(expected.horizon == HORIZON) & (expected.origin_date == changed_origin)].iloc[0]
    actual_changed = actual[(actual.horizon == HORIZON) & (actual.origin_date == changed_origin)].iloc[0]

    assert actual_row.predicted_price == expected_row.predicted_price
    assert actual_changed.predicted_price != expected_changed.predicted_price


def test_incremental_predictions_allow_empty_post_cutoff_history():
    result = incremental_predictions(dataset("2023-12-29"), Bundle())

    assert result.empty


def test_historical_predictions_allow_empty_indices():
    from coffee_service.inference import historical_predictions

    result = historical_predictions(dataset(), Bundle(), start="2025-01-01")

    assert result.empty


def test_generate_predictions_accepts_loaded_bundle():
    result = generate_predictions(dataset("2023-12-29"), model_bundle())

    assert result.empty
