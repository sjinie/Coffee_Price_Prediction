# 프로젝트 맥락
- 상태: Reviewer 최종 pass(0건) 뒤 Docker Compose Local E2E를 재현했다. 저장된 `data/processed/2014-07-01_2025-12-31/`와 `model_artifacts/production_dlinear_60.pt`로 backfill·반복 incremental, API·웹 조회를 검증했다.
- 실행: Docker Compose가 PostgreSQL → 스키마 생성 API → Nginx 웹 순서로 시작한다. 데이터·artifact는 새 clone에 포함되지 않으므로 외부 제공본을 준비하고, `.env.example`을 `.env.docker`로 복사해 로컬 `POSTGRES_PASSWORD`를 설정한다.
- 환경: native는 프로젝트 밖의 Python 3.12 uv 환경을 유지한다. Docker 실행은 이 venv와 호스트 PostgreSQL에 의존하지 않는다.
- 제약: 기존 Notebook·분석 결과·데이터·artifact·사용자 변경·실제 .env를 보존한다.
- 서빙: 5·20일은 Persistence, 60일은 가격+거시 DLinear artifact다. 기존 단일 기간 결과는 후보 근거이며 안정적 우위가 확정된 것은 아니다.

# 다음 단계
- GitHub Actions CI와 수동 GHCR 게시 workflow를 PR로 main에 반영한 뒤 실제 실행 결과를 확인한다. Azure 배포는 별도 작업으로 유지한다.
- 시간을 옮긴 검증으로 60일 DLinear 후보를 재평가한다.

## 2026-09-26 — Jev 뉴스 연결
- Yahoo KC=F 제목·짧은 요약 → Vercel TypeSafe Jev → 분석 캐시·DB → 별도 뉴스 가격 보정 → API·Vue를 추가했다. 기존 5/20 Persistence와 60 DLinear 가격 계약을 유지한다.
- 실수집 최근 90일 표본 122건 중 분류 1건 성공 후 Gateway HTTP 429가 발생했다. 현재 세 지평은 `insufficient_data`이며 보정 가격은 비어 있다. 실제 예측력 개선은 확인하지 않았다.
- 최근성 감쇠·0/1/3/5거래일 시차·단기 가중 잔차 회귀를 구현했다. 실시간 이용 가능 시점과 과거 기사 재분류 연구를 구분한다.
- 다음: Gateway 제한 원인/해제 확인 후 소량 재수집으로 분류 이력을 누적하고, 성숙한 정답으로 같은 기간의 baseline 대비 개선 여부를 평가한다. 자세한 실행 증거는 STATUS 최신 항목을 따른다.

## 2026-09-26 — 200일 뉴스 일별 선정 실행 완료
- 3월 11일~9월 26일 후보 543건에서 완료된 뉴욕 날짜별 대표 기사 88건을 선정해 Jev v2 분류를 모두 마쳤다(bullish 39/bearish 40/neutral 4/uncertain 5). 적합한 기사를 확보하지 못한 완료일 111일은 비워 두었다.
- 중복/동의어 재게시 검사, 수치·방향 변경 후속 보존, 일별 선택 고정, 신규 요청 없는 재실행을 검증했다. 원자료·일별 결과 CSV는 `data/raw/jev/`, 실제 분석 88건은 DB/API/대시보드에서 확인할 수 있다.
- 5/20일 뉴스 보정은 학습 계수가 모두 0이어서 기존 가격과 동일하다. 60일은 평가 자료 부족이다. 예측력 개선은 확인되지 않았다. 다음은 분류 품질과 계수 0의 원인을 확인하는 단계이며 자세한 실측·검증은 STATUS 최신 항목을 따른다.
