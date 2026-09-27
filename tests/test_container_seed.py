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


def test_jev_seed_is_atomic_and_never_overwrites_existing_archive(tmp_path,monkeypatch):
    from coffee_service import jev_store
    seed, data=tmp_path/'seed-jev',tmp_path/'data'
    data.mkdir();jev_store.initialize(seed)
    monkeypatch.setattr(entrypoint,'DATA_DIR',data)
    monkeypatch.setattr(entrypoint,'JEV_SEED_DIR',seed)
    copy=entrypoint.shutil.copytree
    def interrupted(*args):
        copy(*args);raise OSError('interrupted')
    monkeypatch.setattr(entrypoint.shutil,'copytree',interrupted)
    with pytest.raises(OSError):entrypoint.seed_jev()
    assert not (data/'jev').exists()
    monkeypatch.setattr(entrypoint.shutil,'copytree',copy)
    entrypoint.seed_jev()
    document=jev_store.read_document(data/'jev/news.json')
    document['sources']['incremental']=[{'title':'later'}]
    jev_store.write_document(data/'jev/news.json',document)
    entrypoint.seed_jev()
    assert jev_store.read_document(data/'jev/news.json')==document
    assert len(list((data/'jev').iterdir()))==4


def test_partial_jev_seed_is_rejected_without_a_half_archive(tmp_path,monkeypatch):
    data,seed=tmp_path/'data',tmp_path/'seed'
    data.mkdir();seed.mkdir();(seed/'news.json').write_text('{}')
    monkeypatch.setattr(entrypoint,'DATA_DIR',data)
    monkeypatch.setattr(entrypoint,'JEV_SEED_DIR',seed)
    with pytest.raises(ValueError,match='four'):entrypoint.seed_jev()
    assert not (data/'jev').exists()


def test_entrypoint_seed_and_manual_cli_share_source_lock(tmp_path,monkeypatch):
    from coffee_service import refresh
    monkeypatch.setattr(entrypoint,'DATA_DIR',tmp_path)
    monkeypatch.setattr(entrypoint,'SOURCES_DIR',tmp_path/'sources')
    monkeypatch.setattr(entrypoint.sys,'argv',['entrypoint','refresh'])
    with refresh.source_lock(tmp_path/'sources'):
        with pytest.raises(RuntimeError,match='already'):
            entrypoint.main()
    assert not (tmp_path/'jev').exists()
