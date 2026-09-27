import numpy as np
import pandas as pd
import pytest

from data_code import news_expanding_benchmark as benchmark


def sample_fold(monkeypatch, *, news=None):
    sessions = pd.bdate_range("2021-09-01", "2022-08-31")
    n = np.arange(len(sessions))
    columns = [f"feature_{i}" for i in range(27)]
    features = pd.DataFrame({column: np.sin(n / (8 + i)) + n * .0001 + i
                             for i, column in enumerate(columns)}, index=sessions)
    close = pd.Series(100 * np.exp(n * .0003 + .025 * np.sin(n / 13)), index=sessions, name="close")
    monkeypatch.setattr(benchmark, "build_features", lambda sources, dates, cutoff: (features, []))
    monkeypatch.setattr(benchmark, "transform_market",
                        lambda sources, dates: (pd.DataFrame({"close": close}), None))
    if news is None:
        news = pd.Series(np.nan, index=sessions)
        news.loc["2022-02-01"] = .4
        news.loc["2022-04-04"] = 0.
        news.loc["2022-04-05"] = -.5
    return sessions, columns, features, news


def test_fold_maturity_missing_news_and_future_mutation(monkeypatch):
    sessions, columns, features, news = sample_fold(monkeypatch)

    def prepare():
        return benchmark.prepare_fold({}, sessions, columns, news, 5, "2022-04-01", "2022-06-30")

    first = prepare()
    metadata, reference = first["metadata"], first["reference"]
    assert first["train_x"].shape[1:] == first["eval_x"].shape[1:] == (60, 28)
    assert min(metadata["train_origin_dates"]) >= "2022-01-01"
    assert max(metadata["train_target_dates"]) <= "2022-04-01"
    assert reference.origin_date.min() > pd.Timestamp("2022-04-01")
    assert reference.target_date.max() <= pd.Timestamp("2022-06-30")
    assert reference.origin_date.eq("2022-04-04").any()
    assert reference.origin_date.eq("2022-04-06").any()
    assert not reference.loc[reference.origin_date.eq("2022-04-04"), "news_used"].any()
    assert not reference.loc[reference.origin_date.eq("2022-04-06"), "news_present"].any()
    assert reference.loc[reference.origin_date.eq("2022-04-05"), "news_used"].all()
    assert metadata["input_mean"][-1] == 0 and metadata["input_scale"][-1] == 1

    features.loc["2022-04-04":, columns[0]] += 100
    news.loc["2022-04-04":] = .8
    later = prepare()
    np.testing.assert_array_equal(first["train_x"], later["train_x"])
    np.testing.assert_array_equal(first["train_y"], later["train_y"])
    np.testing.assert_array_equal(metadata["input_mean"], later["metadata"]["input_mean"])
    np.testing.assert_array_equal(metadata["input_scale"], later["metadata"]["input_scale"])
    assert not np.array_equal(first["eval_x"], later["eval_x"])


@pytest.mark.parametrize("bad", [
    pd.Series([2.], index=pd.to_datetime(["2022-02-01"])),
    pd.Series([np.inf], index=pd.to_datetime(["2022-02-01"])),
    pd.Series([.1, .2], index=pd.to_datetime(["2022-02-01", "2022-02-01"])),
    pd.Series([.1, .2], index=pd.to_datetime(["2022-02-02", "2022-02-01"])),
    pd.Series([.1], index=pd.to_datetime(["2022-02-01 12:00"])),
])
def test_rejects_bad_news(monkeypatch, bad):
    sessions, columns, _, _ = sample_fold(monkeypatch)
    with pytest.raises(ValueError, match="news_feature"):
        benchmark.prepare_fold({}, sessions, columns, bad, 5, "2022-04-01", "2022-06-30")


@pytest.mark.parametrize("name", ["DLinear", "NLinear", "XGBoost", "LightGBM", "PatchTST", "TimesNet"])
def test_candidate_checkpoints_roundtrip_and_fallback(monkeypatch, tmp_path, name):
    sessions, columns, _, news = sample_fold(monkeypatch)
    prepared = benchmark.prepare_fold({}, sessions, columns, news, 5, "2022-04-01", "2022-06-30")
    result = benchmark.fit_candidates(name, (1, 2), prepared, tmp_path / name)
    reference = prepared["reference"]
    assert len(result) == 2 * len(reference)
    assert result.feature_count.eq(28).all() and result.use_news.all()
    assert np.isfinite(result[["base_prediction", "news_prediction", "predicted_return"]]).all().all()
    np.testing.assert_array_equal(result.loc[~result.news_used, "predicted_return"],
                                  result.loc[~result.news_used, "base_prediction"])
    np.testing.assert_array_equal(result.loc[result.news_used, "predicted_return"],
                                  result.loc[result.news_used, "news_prediction"])
    extension = ".pt" if name in {"DLinear", "NLinear", "PatchTST", "TimesNet"} else ".json" if name == "XGBoost" else ".txt"
    for setting in (1, 2):
        for mode, width in (("base", 27), ("news", 28)):
            assert (tmp_path / name / f"{name}_{setting}_{mode}{extension}").exists()
            with np.load(tmp_path / name / f"{name}_{setting}_{mode}.npz") as saved:
                assert len(saved["features"]) == width
                assert int(saved["train_rows"]) == len(prepared["train_x"])
                assert max(saved["train_target_dates"]) <= "2022-04-01"


def test_later_checkpoint_is_independent_of_earlier_restore(monkeypatch, tmp_path):
    sessions, columns, _, news = sample_fold(monkeypatch)
    prepared = benchmark.prepare_fold({}, sessions, columns, news, 5, "2022-04-01", "2022-06-30")
    both = benchmark.fit_candidates("PatchTST", (10, 30), prepared, tmp_path / "both")
    later = benchmark.fit_candidates("PatchTST", (30,), prepared, tmp_path / "later")
    for column in ("base_prediction", "news_prediction", "predicted_return"):
        np.testing.assert_array_equal(both.loc[both.setting.eq(30), column], later[column])
