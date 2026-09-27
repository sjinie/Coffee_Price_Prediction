from coffee_service import jev_store, news_unify


def test_compatibility_cli_regenerates_csv_without_mutating_json(tmp_path):
    jev_store.initialize(tmp_path)
    originals = {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
    assert news_unify.run(tmp_path) == []
    assert (tmp_path/'sentiment.csv').exists()
    assert originals == {p.name: p.read_bytes() for p in tmp_path.glob('*.json')}
