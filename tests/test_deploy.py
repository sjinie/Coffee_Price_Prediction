"""배포 CLI의 비밀 없는 입력 검사와 Compose 명령 계약."""

import importlib.util
from pathlib import Path

import pytest


SPEC = importlib.util.spec_from_file_location("deploy_cli", Path(__file__).resolve().parents[1] / "deploy" / "deploy.py")
deploy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(deploy)


def write_env(tmp_path, *, seed=True, fred=True):
    source, models = tmp_path / "seed", tmp_path / "models"
    source.mkdir()
    models.mkdir()
    if seed:
        for name in deploy.REQUIRED_SEED:
            (source / f"{name}.parquet").touch()
    (models / deploy.MODEL_FILE).touch()
    env = tmp_path / "deploy.env"
    env.write_text("\n".join((
        "POSTGRES_DB=coffee_price", "POSTGRES_USER=coffee", "POSTGRES_PASSWORD=secret",
        "WEB_PORT=8080", f"SOURCE_DIR={source}", f"MODELS_DIR={models}",
        "FRED_API_KEY=key" if fred else "FRED_API_KEY=",
    )))
    return env


def config_for(tmp_path, *, fred="key"):
    return {
        "services": {
            "pipeline": {
                "environment": {"FRED_API_KEY": fred},
                "volumes": [
                    {"type": "bind", "target": "/seed", "source": str(tmp_path / "seed")},
                    {"type": "bind", "target": "/models", "source": str(tmp_path / "models")},
                ],
            },
            "web": {"ports": [{"published": "8080"}]},
        }
    }


def test_restore_requires_all_seed_files(monkeypatch, tmp_path):
    env = write_env(tmp_path, seed=False)
    monkeypatch.setattr(deploy, "resolved_config", lambda _base: config_for(tmp_path))
    try:
        deploy.validate_config(deploy.compose_base(env, "coffee-test"), require_seed=True)
    except ValueError as exc:
        assert "복원 필수 Parquet" in str(exc)
    else:
        raise AssertionError("missing seed must fail")


def test_collect_requires_fred_key(monkeypatch, tmp_path):
    env = write_env(tmp_path, fred=False)
    monkeypatch.setattr(deploy, "resolved_config", lambda _base: config_for(tmp_path, fred=""))
    try:
        deploy.validate_config(deploy.compose_base(env, "coffee-test"), require_fred=True)
    except ValueError as exc:
        assert "FRED_API_KEY" in str(exc)
    else:
        raise AssertionError("collect without FRED key must fail")


def test_restore_command_is_pinned_to_existing_images(monkeypatch, tmp_path):
    env = write_env(tmp_path)
    commands = []
    monkeypatch.setattr(deploy, "resolved_config", lambda _base: config_for(tmp_path))
    monkeypatch.setattr(deploy, "run", commands.append)
    monkeypatch.setattr(deploy, "verify_http", lambda _port: None)
    assert deploy.main(["--env-file", str(env), "--project", "coffee-test", "restore", "--start", "2014-07-01", "--end", "2025-12-31"]) == 0
    assert "--entrypoint" in commands[0] and deploy.INPUT_CHECK in commands[0]
    command = commands[1]
    assert command[-9:] == ["pipeline", "backfill", "--skip-ingestion", "--start", "2014-07-01", "--end", "2025-12-31", "--artifact", "/models/production_dlinear_60.pt"]
    assert ["--pull", "never"] == command[command.index("--pull"):command.index("--pull") + 2]


def test_collect_blocks_legacy_image_before_any_side_effect(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(deploy, "run", commands.append)
    assert deploy.main(["--env-file", str(tmp_path / "missing.env"), "--project", "coffee-test", "collect", "--start", "2025-01-01", "--end", "2025-12-31"]) == 1
    assert commands == []


def test_up_pulls_profile_job_before_waiting(monkeypatch, tmp_path):
    env = write_env(tmp_path)
    commands = []
    monkeypatch.setattr(deploy, "resolved_config", lambda _base: config_for(tmp_path))
    monkeypatch.setattr(deploy, "run", commands.append)
    assert deploy.main(["--env-file", str(env), "--project", "coffee-test", "up"]) == 0
    assert commands[0][-4:] == ["postgres", "api", "web", "pipeline"]
    assert commands[1][-6:] == ["--wait", "--wait-timeout", "120", "postgres", "api", "web"]


def test_verify_rejects_stale_h60(monkeypatch):
    monkeypatch.setattr(deploy, "request_text", lambda _url: "<html></html>")
    def response(url):
        if url.endswith('/health'):
            return {'status': 'ok'}
        if url.endswith('/prices/latest'):
            return {'date': '2025-12-31'}
        return [{'origin_date': '2025-12-30' if 'horizon=60' in url else '2025-12-31',
                 'predicted_price': 300.0}]
    monkeypatch.setattr(deploy, "request_json", response)
    try:
        deploy.verify_http(8080)
    except RuntimeError as exc:
        assert 'h60' in str(exc)
    else:
        raise AssertionError('stale h60 must fail')
