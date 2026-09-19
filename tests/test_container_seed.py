"""컨테이너 작업 복사본은 재실행 때 원본으로 덮어쓰지 않는다."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location(
    "pipeline_entrypoint", Path(__file__).resolve().parents[1] / "docker/pipeline-entrypoint.py"
)
entrypoint = importlib.util.module_from_spec(spec)
spec.loader.exec_module(entrypoint)


def test_seed_is_atomic_and_preserves_incremental_work(tmp_path, monkeypatch):
    seed, data = tmp_path / "seed", tmp_path / "data"
    seed.mkdir()
    data.mkdir()
    (seed / "coffee.parquet").write_bytes(b"original")
    monkeypatch.setattr(entrypoint, "SEED_DIR", seed)
    monkeypatch.setattr(entrypoint, "DATA_DIR", data)
    monkeypatch.setattr(entrypoint, "SOURCES_DIR", data / "sources")
    original_copy = entrypoint.shutil.copy2

    def interrupted_copy(*args):
        original_copy(*args)
        raise OSError("interrupted")

    monkeypatch.setattr(entrypoint.shutil, "copy2", interrupted_copy)
    with pytest.raises(OSError):
        entrypoint.seed_sources()
    assert not list((data / "sources").iterdir())
    monkeypatch.setattr(entrypoint.shutil, "copy2", original_copy)
    entrypoint.seed_sources()
    copied = data / "sources/coffee.parquet"
    assert copied.read_bytes() == b"original"
    copied.write_bytes(b"incremental")
    entrypoint.seed_sources()
    assert copied.read_bytes() == b"incremental"
    assert (seed / "coffee.parquet").read_bytes() == b"original"


def test_empty_or_partial_seed_can_be_completed_later(tmp_path, monkeypatch):
    seed, data = tmp_path / "seed", tmp_path / "data"
    seed.mkdir()
    monkeypatch.setattr(entrypoint, "SEED_DIR", seed)
    monkeypatch.setattr(entrypoint, "SOURCES_DIR", data / "sources")
    entrypoint.seed_sources()
    (seed / "coffee.parquet").write_bytes(b"coffee")
    entrypoint.seed_sources()
    (data / "sources/coffee.parquet").write_bytes(b"new observations")
    for name in ("alfred_dexbzus", "alfred_dff", "alfred_dcoilwtico"):
        (seed / f"{name}.parquet").write_bytes(b"macro")
    entrypoint.seed_sources()
    assert len(list((data / "sources").glob("*.parquet"))) == 4
    assert (data / "sources/coffee.parquet").read_bytes() == b"new observations"


def test_news_seed_is_separate_and_preserves_existing_collection(tmp_path, monkeypatch):
    seed, destination = tmp_path / "seed", tmp_path / "sources"
    (seed / "news").mkdir(parents=True)
    (seed / "news/articles.parquet").write_bytes(b"historical")
    monkeypatch.setattr(entrypoint, "SEED_DIR", seed)
    monkeypatch.setattr(entrypoint, "SOURCES_DIR", destination)
    entrypoint.seed_sources()
    assert (destination / "news/articles.parquet").read_bytes() == b"historical"
    (destination / "news/articles.parquet").write_bytes(b"incremental")
    entrypoint.seed_sources()
    assert (destination / "news/articles.parquet").read_bytes() == b"incremental"
