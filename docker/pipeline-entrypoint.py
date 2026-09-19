"""Seed immutable parquet inputs once, then run the existing pipeline CLI."""

from __future__ import annotations

from pathlib import Path
import os
import shutil
import sys
import tempfile


DATA_DIR = Path("/data")
SOURCES_DIR = DATA_DIR / "sources"
SEED_DIR = Path("/seed")


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


def main() -> None:
    arguments = sys.argv[1:]
    if "--help" in arguments or "-h" in arguments:
        os.execvp("python", ["python", "-m", "coffee_service.pipeline", *arguments])
    seed_sources()
    if not any(argument == "--source-dir" or argument.startswith("--source-dir=") for argument in arguments):
        arguments.extend(["--source-dir", str(SOURCES_DIR)])
    os.execvp("python", ["python", "-m", "coffee_service.pipeline", *arguments])


if __name__ == "__main__":
    main()
