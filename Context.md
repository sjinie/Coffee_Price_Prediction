# 프로젝트 맥락
- 상태: Reviewer 최종 pass(0건) 뒤 Docker Compose Local E2E를 재현했다. 저장된 `data/processed/2014-07-01_2025-12-31/`와 `model_artifacts/production_dlinear_60.pt`로 backfill·반복 incremental, API·웹 조회를 검증했다.
- 실행: Docker Compose가 PostgreSQL → 스키마 생성 API → Nginx 웹 순서로 시작한다. 데이터·artifact는 새 clone에 포함되지 않으므로 외부 제공본을 준비하고, `.env.example`을 `.env.docker`로 복사해 로컬 `POSTGRES_PASSWORD`를 설정한다.
- 환경: native는 프로젝트 밖의 Python 3.12 uv 환경을 유지한다. Docker 실행은 이 venv와 호스트 PostgreSQL에 의존하지 않는다.
- 제약: 기존 Notebook·분석 결과·데이터·artifact·사용자 변경·실제 .env를 보존한다.
- 서빙: 5·20일은 Persistence, 60일은 가격+거시 DLinear artifact다. 기존 단일 기간 결과는 후보 근거이며 안정적 우위가 확정된 것은 아니다.

# 다음 단계
- 별도 작업에서 GitHub Actions 테스트·이미지 빌드, GHCR, Azure 배포의 CI/CD 범위를 설계한다.
- 시간을 옮긴 검증으로 60일 DLinear 후보를 재평가한다.
