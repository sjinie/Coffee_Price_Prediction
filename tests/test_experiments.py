import numpy as np
import pandas as pd
import pytest

from coffee_service.experiments import evaluation_origins, regression_metrics, score_period
from coffee_service.features import FeatureDataset, PRODUCTION_FEATURES
from coffee_service.intelligence import train_models


def test_zero_prediction_is_abstention_and_validation_target_is_purged():
    score = regression_metrics(np.array([-.1, .1, 0]), np.zeros(3))
    assert score["direction_accuracy"] == 1 / 3
    assert score["direction_balanced_accuracy"] == 0
    dates = pd.bdate_range("2020-01-01", "2022-03-31")
    prices = pd.DataFrame({"close": np.exp(np.sin(np.arange(len(dates)) / 20)) * 100}, index=dates)
    frame = pd.DataFrame({c: np.sin(np.arange(len(dates)) / (i + 2))
                          for i, c in enumerate(PRODUCTION_FEATURES)}, index=dates)
    dataset = FeatureDataset(frame, prices, dates, [], {})
    origins = evaluation_origins(dataset, frame, 60, "2021-01-01", "2021-12-31")
    assert dates[dates.get_loc(origins[-1]) + 60] <= pd.Timestamp("2021-12-31")
    models = train_models(dataset, training_start="2020-01-01", training_end="2020-12-31",
                          model_names=("logistic_ridge",))
    assert all(pd.Timestamp(groups["numeric"]["train_target_max"]) < pd.Timestamp("2021-01-01")
               for groups in models.values())
    # 미래 입력이 학습에 들어가지 않음을 실제 fit 결과로 확인한다.
    original = models[5]["numeric"]["models"]["logistic_ridge"]["classifier"]
    dataset.features.loc["2021-01-01":] *= 1e6
    later = train_models(dataset, training_start="2020-01-01", training_end="2020-12-31",
                        model_names=("logistic_ridge",))[5]["numeric"]["models"]["logistic_ridge"]["classifier"]
    np.testing.assert_array_equal(original[-1].coef_, later[-1].coef_)
