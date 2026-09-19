"""기존 수집 전용 CLI. Local E2E는 coffee_service.pipeline을 사용한다."""

from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from coffee_service.ingestion import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
