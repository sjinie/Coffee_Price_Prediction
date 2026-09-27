"""Seed immutable inputs once, then run the pipeline or weekly refresh worker."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys
import tempfile


DATA_DIR = Path("/data")
SOURCES_DIR = DATA_DIR / "sources"
SEED_DIR = Path("/seed")
JEV_SEED_DIR = Path("/seed-jev")


def seed_sources() -> None:
    SOURCES_DIR.mkdir(parents=True, exist_ok=True)
    for source in (*SEED_DIR.glob("*.parquet"), *SEED_DIR.glob("news/*.parquet")):
        destination = SOURCES_DIR / source.relative_to(SEED_DIR)
        if destination.exists():
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent, delete=False) as temporary_file:
            temporary_path = Path(temporary_file.name)
        try:
            shutil.copy2(source, temporary_path)
            temporary_path.replace(destination)
        finally:
            temporary_path.unlink(missing_ok=True)



def seed_jev() -> None:
    """Copy the four-file snapshot atomically, never overwriting a running archive."""
    from coffee_service import jev_store
    destination = DATA_DIR / "jev"
    if destination.exists():
        jev_store.initialize(destination)
        return
    names = {"news.json", "requests.json", "responses.json", "sentiment.csv"}
    present = {p.name for p in JEV_SEED_DIR.iterdir()} if JEV_SEED_DIR.exists() else set()
    if present and present != names:
        raise ValueError("Jev seed must contain exactly the four archive files")
    with tempfile.TemporaryDirectory(dir=DATA_DIR, prefix="jev-seed-") as directory:
        temporary = Path(directory) / "archive"
        if present:
            shutil.copytree(JEV_SEED_DIR, temporary)
        else:
            temporary.mkdir()
        jev_store.initialize(temporary)
        temporary.rename(destination)

def main() -> None:
    arguments = sys.argv[1:]
    if "--help" in arguments or "-h" in arguments:
        os.execvp("python", ["python", "-m", "coffee_service.pipeline", *arguments])
    from coffee_service.ingestion import source_lock
    with source_lock(DATA_DIR):
        with source_lock(SOURCES_DIR):
            seed_sources()
        seed_jev()
    if not any(argument == "--source-dir" or argument.startswith("--source-dir=") for argument in arguments):
        arguments.extend(["--source-dir", str(SOURCES_DIR)])
    if not any(argument == "--jev-cache" or argument.startswith("--jev-cache=") for argument in arguments):
        arguments.extend(["--jev-cache", str(DATA_DIR / "jev/responses.json")])
    os.execvp("python", ["python", "-m", "coffee_service.pipeline", *arguments])


if __name__ == "__main__":
    main()
