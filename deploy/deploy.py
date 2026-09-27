#!/usr/bin/env python3
"""Run the pinned VM deployment without rebuilding images."""

from __future__ import annotations

import argparse
from datetime import date
import json
import math
from pathlib import Path
import subprocess
import sys
from urllib.error import URLError
from urllib.request import urlopen


ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILE = ROOT / "compose.deploy.yaml"
REQUIRED_SEED = ("coffee", "alfred_dexbzus", "alfred_dff", "alfred_dcoilwtico")
MODEL_FILE = "production_dlinear_60.pt"
INPUT_CHECK = """
from datetime import date
from pathlib import Path
import runpy, sys
from coffee_service.pipeline import sources_as_of, validate_macro_freshness
from coffee_service.features import assemble_features
from coffee_service.modeling import load_bundle
from coffee_service.inference import latest_predictions
runpy.run_path('/app/docker/pipeline-entrypoint.py')['seed_sources']()
sources = sources_as_of(Path('/data/sources'), date.fromisoformat(sys.argv[1]))
validate_macro_freshness(sources, date.fromisoformat(sys.argv[1]))
latest_predictions(assemble_features(sources), load_bundle(Path('/models/production_dlinear_60.pt')))
print('입력 검증: 최신 5/20/60일 추론 가능')
"""


def require_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("날짜는 YYYY-MM-DD 형식이어야 합니다.") from exc


def resolved_config(base: list[str]) -> dict:
    result = subprocess.run([*base, "--profile", "jobs", "config", "--format", "json"], check=False, capture_output=True, text=True)
    if result.returncode:
        raise ValueError("Compose 설정을 해석할 수 없습니다.")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ValueError("Compose 설정 형식이 올바르지 않습니다.") from exc


def validate_config(base: list[str], *, require_seed: bool = False, require_fred: bool = False) -> int:
    services = resolved_config(base).get("services", {})
    try:
        pipeline = services["pipeline"]
        volumes = {item["target"]: item["source"] for item in pipeline["volumes"] if item.get("type") == "bind"}
        source_dir, models_dir = Path(volumes["/seed"]), Path(volumes["/models"])
        fred_key = pipeline["environment"].get("FRED_API_KEY", "")
        port = int(services["web"]["ports"][0]["published"])
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("Compose 설정에 배포 필수 항목이 없습니다.") from exc
    if not 1 <= port <= 65535:
        raise ValueError("WEB_PORT 범위는 1~65535입니다.")
    for directory in (source_dir, models_dir):
        if not directory.is_absolute() or not directory.is_dir():
            raise ValueError("SOURCE_DIR와 MODELS_DIR는 존재하는 절대 디렉터리여야 합니다.")
    model = models_dir / MODEL_FILE
    if not model.is_file():
        raise ValueError(f"모델 파일이 없습니다: {MODEL_FILE}")
    if require_seed:
        missing_seed = [name for name in REQUIRED_SEED if not (source_dir / f"{name}.parquet").is_file()]
        if missing_seed:
            raise ValueError("복원 필수 Parquet가 없습니다: " + ", ".join(missing_seed))
    if require_fred and not fred_key:
        raise ValueError("collect에는 FRED_API_KEY가 필요합니다.")
    return port


def compose_base(env_file: Path, project: str) -> list[str]:
    return ["docker", "compose", "--env-file", str(env_file), "--project-name", project, "--file", str(COMPOSE_FILE)]


def run(command: list[str]) -> None:
    subprocess.run(command, check=True)


def run_job(base: list[str], mode: str, start: date, end: date, *, skip_ingestion: bool) -> None:
    if skip_ingestion:
        run([*base, "run", "--rm", "--pull", "never", "--no-deps", "--entrypoint", "python",
             "pipeline", "-c", INPUT_CHECK, end.isoformat()])
    command = [*base, "run", "--rm", "--pull", "never", "pipeline", mode]
    if skip_ingestion:
        command.append("--skip-ingestion")
    command.extend(["--start", start.isoformat(), "--end", end.isoformat(), "--artifact", f"/models/{MODEL_FILE}"])
    run(command)


def request_json(url: str):
    try:
        with urlopen(url, timeout=10) as response:
            return json.load(response)
    except (OSError, URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"HTTP 검증 실패: {url}") from exc


def request_text(url: str) -> str:
    try:
        with urlopen(url, timeout=10) as response:
            return response.read().decode("utf-8", errors="replace")
    except (OSError, URLError) as exc:
        raise RuntimeError(f"HTTP 검증 실패: {url}") from exc


def verify_http(port: int) -> None:
    base = f"http://127.0.0.1:{port}"
    if "<html" not in request_text(base + "/").lower():
        raise RuntimeError("web 정적 파일 응답이 올바르지 않습니다.")
    health = request_json(base + "/health")
    if health != {"status": "ok"}:
        raise RuntimeError("health 응답이 올바르지 않습니다.")
    latest = request_json(base + "/api/v1/prices/latest")
    latest_date = latest.get("date") if isinstance(latest, dict) else None
    if not latest_date:
        raise RuntimeError("최신 가격이 없습니다.")
    for horizon in (5, 20, 60):
        rows = request_json(base + f"/api/v1/predictions?horizon={horizon}&limit=5000")
        valid = [row for row in rows if isinstance(row, dict) and row.get("origin_date") == latest_date]
        if not valid:
            raise RuntimeError(f"h{horizon} 최신 origin 예측이 없습니다.")
        if not all(isinstance(row.get("predicted_price"), (int, float)) and math.isfinite(row["predicted_price"]) for row in valid):
            raise RuntimeError(f"h{horizon} 예측값이 유효하지 않습니다.")
    status = request_json(base + "/api/v1/pipeline/status")
    if not isinstance(status, dict) or status.get("status") != "success":
        raise RuntimeError("성공한 pipeline run이 없습니다.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--project", required=True)
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("up")
    for name in ("restore", "collect"):
        action = actions.add_parser(name)
        action.add_argument("--start", required=True, type=require_date)
        action.add_argument("--end", required=True, type=require_date)
    actions.add_parser("verify")
    args = parser.parse_args(argv)
    if getattr(args, "start", None) and args.start > args.end:
        parser.error("start는 end보다 늦을 수 없습니다.")
    try:
        if args.action == "collect":
            raise ValueError("현재 고정된 fab81c0 이미지는 이력 부족을 DB success로 기록할 수 있어 collect를 차단합니다. "
                             "pipeline 입력 검증 수정의 새 GHCR 게시와 digest 확인 후 해제해야 합니다.")
        base = compose_base(args.env_file, args.project)
        port = validate_config(base, require_seed=args.action == "restore", require_fred=args.action == "collect")
        if args.action == "up":
            run([*base, "pull", "postgres", "api", "web", "pipeline"])
            run([*base, "up", "--detach", "--no-build", "--wait", "--wait-timeout", "120", "postgres", "api", "web"])
        elif args.action == "restore":
            run_job(base, "backfill", args.start, args.end, skip_ingestion=True)
            verify_http(port)
        elif args.action == "collect":
            run_job(base, "incremental", args.start, args.end, skip_ingestion=False)
            verify_http(port)
        else:
            verify_http(port)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as exc:
        print(f"실패: {exc}", file=sys.stderr)
        return 1
    print(f"완료: {args.action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
