"""API의 작은 런타임과 native/Compose DB 연결 설정을 검사한다."""

import subprocess
import sys

from coffee_service import db


def test_api_import_does_not_load_data_or_model_dependencies():
    subprocess.run(
        [sys.executable, "-c", "import sys; import coffee_service.api; "
         "assert not {'pandas', 'numpy', 'torch', 'yfinance'} & sys.modules.keys()"],
        check=True,
    )


def test_database_connection_settings_precedence(monkeypatch):
    monkeypatch.setattr(db, "load_dotenv", lambda *_: None)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.delenv("PGHOST", raising=False)
    assert db.database_url() == db.DEFAULT_DATABASE_URL
    monkeypatch.setenv("PGHOST", "postgres")
    assert db.database_url() == ""
    monkeypatch.setenv("DATABASE_URL", "postgresql://example/test")
    assert db.database_url() == "postgresql://example/test"
