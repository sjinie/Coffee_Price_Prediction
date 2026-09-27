# 작업 상태

## 2026-09-27 — 기존 역할 경계 검토·기능 보존 리팩토링

- 검토 범위: 수치·뉴스 수집/저장/추론/CLI, FastAPI·DB 접근, Vue·테스트, Docker/Compose와 배포/Actions 경로. 논리 경계는 pipeline 수집·분석·DB 적재 → PostgreSQL 저장 → api 조회/응답 → Vue 표시다. 로컬 Compose의 pipeline 컨테이너와 Azure의 외부 Actions Python 배치는 실행 위치가 다르며 기존 운영 계약을 변경하지 않았다.
- 중복 제거: `collection_jobs`·`SOURCE_GROUPS`를 ingestion에 모아 수집 전용 CLI와 pipeline이 재사용한다. `source_lock`도 ingestion으로 옮겨 pipeline/seed가 scheduler에 의존하던 경로를 제거했다. 기존 import 호환·소스 순서·CLI 옵션·legacy 기상 앞뒤 30일/pipeline 과거 90일·증분 7일 overlap은 유지한다. Jev JSON writer 세 곳은 기존 `_write_records`를 사용하도록 통합했다. 실행 코드 순감소 31줄이며 새 모듈·의존성·API·DB schema·모델·일정은 추가/변경하지 않았다.
- 검증: 외부 Python 3.12.14·임시 PostgreSQL 17.11에서 **186 passed, 2 subtests passed, 경고 3건**, Vue **8 passed + build 통과**. Python은 `test_core4_environment.py`(기존 연구 환경 전용)만 제외하고 DB 통합 검사까지 실행했다. 경고는 기존 sklearn 단일 클래스·Starlette/httpx·AnyIO deprecation이며 숨기지 않았다. 독립 최종 diff 리뷰에서 추가 확정 결함 없음. Jev 네 JSON 저장 경로는 변경 전후 UTF-8 bytes가 같고, rename 실패 시 기존 내용이 보존됨을 임시 파일로 대조했다. 원본·Jev·모델·노트북 27개 파일의 SHA-256이 동일하다. 실제 외부 수집·Gateway 요청·운영 DB·VM·GitHub 상태 변경은 하지 않았다.
- 남은 결함: `frontend/src/App.vue:69`의 뉴스 새로고침에서 이전 요청이 나중에 완료되면 최신 응답을 덮는다. 응답 순서를 제어한 로컬 fixture로 재현했다. 요청 순서 제어는 동작 변경이므로 이번 기능 보존 리팩토링에서 적용하지 않았고 후속 버그 수정 대상으로 남긴다. API/Vue 분리나 DB 계층 추가를 강제할 근거는 확인하지 못했다.
- 한계: 활성 Docker desktop-linux 엔진 소켓이 없어 이번 컨테이너 build/start는 미검증이다. 기존 Azure/CI 실행 기록을 이번 변경의 실행 증거로 대신하지 않는다. 기존 사용자 변경은 별도로 보존하며 push·배포하지 않는다.

## 2026-09-27 — Azure VM 생성·서비스 기동·일일 Actions 연결 준비

- 사용자 승인으로 Korea Central `rg-coffee-demo/vm-coffee-demo` 생성 완료. Azure for Students, Ubuntu 24.04 x64 Gen2/Trusted Launch, B2ats_v2(2 vCPU/1 GiB), P6 64 GiB, IPv4 `52.141.6.78`. 포털에서 B2ats_v2 Linux 750시간/월·P6 무료 사용량을 확인했지만 실제 청구액·잔여 크레딧은 미확인이다. 유료 구독 전환은 하지 않았다.
- 실제 VM에 공식 apt 경로로 Docker 29.8.1·Compose 5.5.1과 2 GiB swap 설치. 소스 `388cd691441e231ecafc389dc1aac1c3d80522c7`에서 API·web을 순차 build하고 `deploy/start-azure.sh`로 DB 계정/스키마와 postgres/api/web 기동(exit 0). 이미지 ID는 API `sha256:3939db849013f77144a05558367fc10f596ab35094bd504148fe462a21b1ed17`, web `sha256:2cae1d31f1980e262188ab607e323cf6e9b5e602d649edf04d28bb1049f320b2`; 새 GHCR 게시를 뜻하지 않는다. PostgreSQL 기존 17.11-bookworm digest를 유지했다.
- HTTP 80·키 기반 SSH 22 공개. DB는 호스트 loopback 15432, API는 컨테이너 내부 8000이다. 실제 DB 조회로 `coffee_api` SELECT 9개 테이블, API/pipeline 역할 모두 비-superuser·CREATEDB/CREATEROLE 없음 확인. Actions 계정은 sudo/docker 그룹·Docker socket·운영 env 접근 권한이 없다. Azure Run Command로 SSH 호스트 ED25519 fingerprint를 대조해 고정했고 `sshd -t` 및 실제 키 로그인·제한된 DB 터널을 검증했다. HTTPS·도메인은 미설정이다.
- GitHub `production` 환경(main만 허용)에 전용 SSH key/known_hosts, pipeline DB password, FRED/Gateway key와 host/user/state/DB 변수를 등록했다. `daily-pipeline.yml`은 기존 Actions SHA·Python 3.12/requirements-pipeline을 재사용하며 UTC 06:17/KST 15:17 하루 한 번 실행한다. NY 겨울 01:17·여름 02:17을 직접 계산해 완료된 전날 자료가 포함됨을 확인했다. 동시 실행을 제한하고 배치 종료·실패·SIGINT/SIGTERM 뒤 상태를 VM에 복사한다. workflow push·main 반영·GitHub-hosted 실행은 아직 하지 않았다.
- 실제 동일 script를 Mac Python 3.12.14에서 Actions용 키·DB 계정으로 실행: 수치 수집·추론·DB 적재 성공(커피 3,083행, 최신 2026-09-25), 뉴스 수집 성공. Gateway의 Jev `typesafe-ai/jev` 요청은 HTTP 403 `RestrictedModelsError`(free tier model access denied, providerAttemptCount=0)로 stopped. 전체 refresh는 partial/exit 1이며 실패 뒤 VM sources/jev 동기화 확인. 기존 분석 1,445건과 대기 2건, 응답 감사 기록을 보존했고 유료 결제·모델 교체·중단 상태 해제는 하지 않았다. 60일 모델은 기존 DLinear를 유지했다.
- 외부 HTTP 검증: `/health`, `/api/v1/prices/latest`, 5/20/60일 predictions 모두 200. 최신 종가 278.1000061035156, 60일 target 2026-12-21/예측 361.39453125 확인. HTML도 200이며 Aside는 해당 URL을 `ERR_BLOCKED_BY_CLIENT`로 막아 최종 화면 렌더링은 미검증이다. 저부하 관측 web 7.496 MiB/API 60.77 MiB/PostgreSQL 44.95 MiB, 호스트 used 532 MiB/available 310 MiB, swap 69 MiB이며 부하 보장은 아니다.
- 검증: 전송·실패·중단 보존 mock 회귀 4 passed(신호 2 subtests), Bash 문법·Compose/YAML 정적 검사·diff check 통과. 독립 리뷰의 뉴욕 날짜 경계 지적을 예약 시간 수정으로 해결했고 최종 추가 결함 없음. 실제 Linux 컨테이너 build·DB·외부 HTTP·SSH 데이터 경로는 확인했지만 GitHub runner E2E 및 예약 실행 성공은 미확인이다.
- 공식 근거 확인(2026-09-27): [Docker Ubuntu apt 설치](https://docs.docker.com/engine/install/ubuntu/), [Actions schedule](https://docs.github.com/en/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule), [환경 Secrets](https://docs.github.com/en/actions/how-tos/write-workflows/choose-what-workflows-do/use-secrets), [Azure Students](https://azure.microsoft.com/en-us/free/students/). 기존 사용자 문서·연구·데이터·secret은 보존한다. 후속은 workflow main 반영 후 실제 Actions 실행, Jev 접근 권한 결정이다.

## 2026-09-27 — 분리된 pipeline의 서버 시작·주간 갱신

- 사용자 요청에 따라 기존 네 컨테이너와 FastAPI/수집·분석 경계를 유지했다. `compose.yaml`의 수동 pipeline profile을 기본 `refresh` 실행으로 바꾸고, 시작 시 한 번 처리한 뒤 완료로부터 7일마다 다시 갱신한다. 종료 중 놓친 스케줄 횟수를 재생하지 않고 체크포인트 이후 기간을 보충한다. 기존 `jev` 30분 Codex 자동화 삭제 API는 `not_found`를 반환했으며 이미 존재하지 않음을 확인했다. 대체 Codex 자동화는 만들지 않았다.
- 수치 자료는 기존 모든 소스 그룹·원본별 7일 overlap·고정 모델 추론·DB UPSERT를 재사용한다. 뉴스는 원본·선정·조회 범위를 `news.json`에 누적하고 미분류만 기존 Jev worker로 보낸다. 요청·응답 원문/비용/대기 상태는 기존 JSON에 보존하고 CSV를 재생성한다. 새 뉴스는 연구용 CSV이며 새 앙상블 학습·채택이나 기존 뉴스 예측 snapshot 자동 변경은 포함하지 않는다.
- `/data` named volume에 수치 자료와 네 Jev 파일을 보관한다. read-only Jev seed는 완전한 네 파일을 함께 복사하고 기존 archive를 덮어쓰지 않는다. numeric CLI와 seed가 같은 디렉터리 잠금을 공유하며 뉴스 조회·Jev 대기에는 수치 잠금을 해제한다. 보조 소스를 포함한 수치·뉴스 조회 실패는 1시간 뒤 재시도하고 정상 주기는 7일이다. 동시 뉴스 수집의 조회 완료일 회귀와 동일 완료일의 미완료 실패 기록 유실을 방지했다. 수동 news CLI도 numeric 작업과 source snapshot 읽기에만 잠금을 건다. FastAPI에는 ML 의존성·스케줄러를 추가하지 않았다. 키가 없으면 수집한 뉴스를 보존하고 분류를 보류하며 기존 Gateway $1 예산을 변경하지 않는다.
- 검증: **118 passed, 4 skipped**(테스트 DB 미설정), 기존 deprecation 2건. 독립 리뷰의 잠금 범위·실패 재시도·동시 체크포인트 지적을 수정했고 재검토에서 추가 결함은 없었다. Compose 5.5.1에서 fixture 환경으로 config를 해석해 기존 네 서비스·별도 API/worker build·자동 refresh 명령·의존 healthcheck·read-only seed와 영구 volume을 확인했다. 275일 미운영·조회 실패 후 재개·동시 수집·mock HTTP 원문→CSV→재시작 재사용·종료 신호·잠금 충돌을 검증했다. 실제 데이터/DB/LLM에 새 요청을 보내거나 server worker를 현재 실행하지 않았다. 활성 `desktop-linux` Docker 엔진 소켓이 없어 컨테이너 build/start는 미검증이다. 기존 Starlette/httpx·AnyIO deprecation 안내는 숨기지 않았다.
- 공식 근거 확인(2026-09-27): [Compose startup](https://docs.docker.com/compose/how-tos/startup-order/)·[restart](https://docs.docker.com/reference/compose-file/services/#restart), Python 3.12 표준 라이브러리. 설치 FastAPI 0.141.1/Uvicorn 0.53.0/requests 2.34.2/pandas 2.3.3/yfinance 1.7.0을 유지했고 의존성 추가는 없다. 기존 digest 고정 GHCR 배포 경로는 새 코드를 포함하지 않으며 push·게시·VM 변경은 하지 않았다.

## 2026-09-27 — Jev 네 파일 통합·삭제·품질 및 이용 시점 점검

- 사용자 승인 범위는 Jev 자료 통합과 재생성 가능한 캐시·빌드·임시 산출물 및 참조 없는 중복 파일이다. 저장소 코드·설정·문서·노트북 참조 점검에서 그 밖의 삭제 가능한 추적 파일은 확인하지 못해 보존했다. 옛 Jev 경로의 394개와 `.pytest_cache`, 지정 Python `__pycache__`, `frontend/dist`의 63개 파일(총 457개·27,751,531바이트)을 삭제했다. 개별 해시·미추적 여부·symlink 부재·백필 프로세스 부재를 확인했고 삭제 전 임시 복구본을 `/tmp/coffee-jev-before-cleanup.tar.gz`에 만들었다. `node_modules`, `.agents`, `.codex`, lockfile, `.env`, 기존 연구/old 디렉터리·가격 모델은 이 정리 대상에 넣지 않았다.
- `data/jev/news.json`에 수집 원본 360개 스냅샷·선정·출처를, `requests.json`에 요청 본문/감사/worker 상태를, `responses.json`에 응답 본문/분석 원기록을, `sentiment.csv`에 모든 분석 시도를 통합했다. 과거 분석 파일 7개를 인덱스로 정확히 재구성할 수 있고 정규화 메타데이터 변형 2,958개를 보존했다. JSON·Parquet 원본(스키마·값·attrs)·선정·감사 기록 왕복 대조를 통과했다. 과거 HTTP 본문은 애초 미보관이므로 legacy 상태를 명시하며 만들어낸 요청/응답을 원본으로 부르지 않는다.
- CSV는 `(analysis_id, analyzed_at)` 1,449행, 최신 연구 선정은 1,445행(연도별 189/213/256/383/404)이다. 과거 prompt·미선정 probe·재분석 이전 결과도 남기고 최신/연구/제목 단독/라벨 확률 일치 여부를 구분한다. 기존 1,445행의 분류·확률·관련성·confidence·분석 시각·비용/usage 및 서비스 88건은 이전과 동일하다. 원래 선정 960건(1건/일)·549건(2건/일), 날짜가 다른 제목 중복의 앞선 재분류와 단일 대표 날짜 정책을 유지한다. 합집합의 최대 3건/일을 과거 1건/일 실험으로 오해하지 않는다.
- 후속 요청은 `news_backfill --input <기사목록.json> --once`, API 없는 CSV 재생성은 `news_unify`다. 실제 요청 본문을 전송 전에, 응답 본문을 파싱 전에 저장한다. HTTP 200 원문 저장 후 중단된 분석은 재요청 없이 복구하며 응답 미보관/비용 불명은 중단한다. 429/일시 오류 60초·성공 300초·더 긴 Retry-After를 재시작 후에도 지키고 대기 중에는 공통 잠금을 해제한다. API 원문은 mock으로 검증했으며 이번 정리의 실제 HTTP 요청은 0회다. 기록된 전체 분석 비용 합계 $0.019455030은 계정 청구액이 아니다. Gateway $1 설정과 완료된 heartbeat 일시정지는 유지하고, 30분 확인 프롬프트의 경로를 새 네 파일로 갱신했다.
- **분류 품질:** 1,445개 중 uncertain 864(59.8%), bullish 307, bearish 187, neutral 87; 요약이 빈 제목 단독 1,321(91.4%)다. 유한 확률·범위·합계 검사를 통과했으나 `Nespresso to supply coffee to The Renaissance Club`(2023-04-20)은 neutral 0.48/uncertain 0.49인데 label이 neutral인 1건이 있다(허용오차 1e-8; 미세한 부동소수 동률 2건 제외). 원값은 수정하지 않고 `label_matches_probabilities=False`로 표시한다. 방향 라벨인데 relevance<0.5인 11건도 재검토 대상이며 이 수치만으로 오답이라고 확정하지 않는다.
- 연도×라벨 각 1개(seed42)의 20건을 모델 보조로 정성 점검했다. `Brazil’s Safrinha crop improves with each day`는 제목에 커피가 없는데 bearish/relevance 0.76/confidence 0.89이고, `Business: Global coffee prices slide as robusta, arabica fut`는 신규 기초여건 없이 가격 움직임만 있는 불완전 제목인데 bearish다. Yemen 경매 고가 제목도 KC 전체 방향 근거가 약하다. 이들은 검토 후보이며 사람 정답 기반 정확도·Brier score·calibration이나 배치 크기별 품질을 검증한 결과는 아니다. 사용자가 추가 비용을 승인한 것으로 해석해 재분류하지 않았다.
- **시점별 이용 가능성:** 실제 `available_at`은 2026-09-26T09:12:42Z~2026-09-27T02:21:29Z다. 대표 기사 날짜 당시 이용 가능했던 분류는 0건이다. `research_available_at`은 사건·수정·일별 선정 완료 시각의 보수적 최댓값이며 수집/분석이 과거에 이루어졌다는 증거가 아니다. 2022~23 Validation/이미 본 2024~25 Test 표시를 유지하고, 소급 분류·모델 사전학습 오염 및 시점별 배포 가능성 한계를 구분한다. 앙상블 학습·채택·DB 변경은 하지 않았다.
- 검증: Mac 외부 Python 3.12에서 **71 passed, 4 skipped**(DB URL 미설정). 비용의 NaN/음수/boolean·비정상 metadata 및 401/402/403 응답 직후 중단 복구 검사 포함. 실제 자료 export 재실행 바이트 동일, 외부 HTTP 차단 transport로 완료 상태 재확인, 서비스 88건/노트북 CSV 읽기 구간 실행 및 기존 모든 output 보존. 독립 리뷰의 잠금 유지·새 import 누락 지적을 수정하고 회귀 검사를 추가했다. DB 통합 검사의 환경 미설정 skip은 실제 DB 실행으로 주장하지 않는다. 의존성·서빙 가격 모델·배포는 변경하지 않았으며 Docker/CI/VM 검증은 이번 범위가 아니다.
- 공식 근거(2026-09-27): [Choice](https://docs.typesafe.ai/primitives/choice)의 최고확률 라벨·확률합·confidence 정의와 [Noul](https://docs.typesafe.ai/primitives/noul)의 yes 확률 정의를 대조했다. relevance는 영향 크기의 직접 측정값이 아니다.

## 2026-09-27 — 2022~2026 Jev 뉴스 결과 통합

- 원자료·선정·요청 체크포인트는 `data/raw/jev/`에 보존하고, 통합 분석 산출물은 `data/processed/jev_news/articles.json`·`articles.csv`에 둔다. 재생성 명령은 `python -m coffee_service.news_unify`다. 추가 뉴스 수집·DB 적재·가격 모델 변경은 없다.
- 통합 키는 `content_hash`다. 같은 내용으로 선정된 행은 한 건으로 합치되 `backfill_jobs`와 `source_selections`에 원본 작업·날짜·출처를 남긴다. 날짜가 다른 중복 1건은 사용자 요청에 따라 JeV에 다시 질문해 HTTP 200 응답을 얻었으며, 더 이른 2025-07-18을 대표 날짜로 택했다. 추가 응답 비용은 $0.000023688이다. JeV 감성 결과로 기사 날짜의 진위를 판별할 수는 없고, 같은 제목·요약으로 2026-08-19에 발행된 별도 사건일 가능성은 남는다.
- `evaluation_split`은 `validation_2022_2023`, `seen_test_2024_2025`, `research_2026`으로 구분한다. 이 컬럼은 연구용 분할 표시이며 2026년 재분류 결과를 과거 시점의 live 신호로 만들지 않는다.
- 두 선정 정책의 합집합이므로 통합 파일의 2025년에는 날짜당 최대 3건이 있을 수 있다. 원래 2022~2025년 1건/일 실험을 재현할 때는 `backfill_jobs`에 `validation-2022-2025`가 포함된 행만 사용한다. CSV의 중첩 출처 필드는 JSON 배열 문자열로 저장한다.
- 실측: 선정 1,509건에서 내용 고유 1,445행(2022/23/24/25/26년 189/213/256/383/404)을 생성했다. 날짜가 다른 중복의 재분류 결과는 bearish, `p_bearish=0.69`, 관련성 0.82, 분석 시각 2026-09-27T02:21:29Z다. 원본 전체 선정의 출처 대응·확률 합계·이용 가능 시각·CSV/JSON 일치와 재실행 바이트 동일성을 확인했다. 관련 JeV·백필·통합 테스트 37개 통과. 새 분석 결과는 당시 2025년 실시간 이용 가능 신호가 아니다.
- 독립 리뷰에서 이 제목 단독 중복이 실제로는 별도 사건일 수 있다는 점과 JSON/CSV 두 파일의 동시 원자 게시가 아니라는 점을 지적했다. 전자는 사용자 지정 단일 날짜 규칙을 적용하되 두 원본 날짜를 `source_selections`에 보존한다. JSON을 기준 데이터로 사용하고 CSV 생성 중 중단되면 동일 명령으로 재생성한다.

## 2026-09-27 — Jev 뉴스 백필 완료

- 2026-09-26T14:46:15Z에 두 작업 모두 완료됐다. 최근 1년 549/549건(완료된 뉴욕 날짜당 최대 2건), 2022~2025년 960/960건(날짜당 최대 1건)을 선정 목록과 JSON/CSV 결과에 대조했다. 2022/2023/2024/2025년은 189/213/256/302건이다. 기사 내용 해시·날짜별 상한·기간·확률 합계·이용 가능 시각을 확인했고 두 기간이 겹치는 64건은 동일한 분석을 재사용했다.
- 백필 요청 기록은 HTTP 200 22회, 429 6회이며 성공 응답에 기록된 비용 합계는 **$0.016832382**다(계정 전체 청구액 아님). 전체 분석 캐시에 기록된 비용 합계는 $0.019431342다. 최대 성공 배치는 84기사였다. $1 Gateway budget은 변경하지 않았다.
- 이 채팅의 30분 상태 확인 자동화 `jev`를 완료 후 일시정지했다. 결과는 ignored `data/raw/jev/{year-20250926-20260925,validation-2022-2025}/results.json`과 `results.csv`에 남아 있다. 이는 현재 Jev로 과거 기사를 소급 분류한 연구용 결과이며 당시 실시간 이용 가능 신호가 아니다. 분류 정답의 사람 검토와 가격 예측 개선은 미검증이다. 2022~2023 Validation과 이미 본 2024~2025 Test를 구분한다.

## 2026-09-26 — Jev 요청당 질문 수 확대

- 공식 [TypeSafe API](https://docs.typesafe.ai/api)는 `state`와 질문 ID별 `questions`를 받고 질문별 답을 반환한다. [질문 병렬 처리](https://docs.typesafe.ai/primitives)와 [Jev 모델 한도](https://docs.typesafe.ai/models)를 확인했다. TypeSafe 직접 API는 요청 전체 64k 토큰이지만 [Vercel Gateway 모델 목록](https://vercel.com/ai-gateway/models/providers/typesafe-ai)은 32k 문맥으로 표시하므로 Gateway 사용에서는 더 낮은 표시를 기준으로 삼았다(2026-09-26 확인).
- 기존 기사별 두 질문과 기사별 입력 분리, 응답·비용 검증을 유지하면서 임의의 20건 상한을 제거했다. 백필은 JSON UTF-8 크기 100,000바이트 이내에 들어가는 질문을 순서대로 채운다. 과거 48건/84,939바이트 요청의 실제 입력 21,915토큰을 근거로 한 경험적 상한이며 정확한 토큰 계산이나 32k 보증은 아니다. 시작 시점 다음 최근 배치는 83기사/166질문/98,969바이트로 구성됐다.
- 검증: 기존 Python 3.12 venv에서 Jev·뉴스 백필·소스 테스트 **30 passed**. 확대 배치 83기사·166질문이 실제 HTTP 200으로 완료됐다(입력 25,338·출력 6,278토큰, 응답 비용 $0.001064196). 결과 83건 모두 고유 내용·유효한 확률로 저장됐다. 배치 크기별 분류 정확도 비교는 미실시다. 기존 $1 예산·429 재시도·성공 후 5분·Codex 30분 감시는 유지한다.

## 2026-09-26 — Jev 429 재시도 간격 조정

- 사용자 요청대로 429/일시 오류 뒤 60초마다 재시도하고 HTTP 200 뒤에는 300초를 기다린다. 더 긴 `Retry-After`는 우선하며 저장된 다음 요청 시각은 재시작 후에도 지킨다. Codex heartbeat는 기존 30분 간격이다. 요청당 최대 20건과 $1 Gateway 예산은 유지한다.
- 변경 시점에 최근 208/549건이 완료됐다. 과거 960건 중 기존 분석 캐시와 겹치는 기사는 재사용한다. 잔여 소요 시간은 429 빈도와 배치 완전성에 따라 달라진다.
- 검증: Python 3.12 기존 venv에서 뉴스 백필·소스·Jev 테스트 **29 passed**. 기존 워커를 대기 상태에서 중지하고 체크포인트로 재시작했다. 실서비스에서 새 간격의 연속 429→200 전환은 아직 관측 전이다.

## 2026-09-26 — Jev 5분 백그라운드 처리와 2022~2025 뉴스 선수집

- 사용자 요청: 최근 365일(2025-09-26~2026-09-25)은 완료된 뉴욕 날짜당 최대 2건, 그 다음 2022-01-01~2025-12-31은 최대 1건. 과거 뉴스는 제한 해제 전에 미리 수집한다. 현재 선정은 각각 549건/309일, 960건/960일이며 적합한 기사 미확보일은 56일/501일이다. 제목·제공된 짧은 요약만 보관하고 본문은 수집하지 않는다.
- 실제 수집: 최근 자료는 WordPress/Yahoo/Google News RSS 5,602레코드(고유 URL 5,101개)에서 선정했다. 과거는 기존 WordPress와 96개 월별 RSS 체크포인트 8,429레코드(고유 URL 7,538개)를 보관했다. 선정은 2022년 189·2023년 213·2024년 256·2025년 302건이며 URL/내용/NY 날짜 중복이 없다. 과거 시점 이후 수정된 기사 등 1,610건을 제외했다. RSS 쿼리당 100건 한계와 의미상 중복 가능성은 남으며 모든 뉴스의 완전한 아카이브가 아니다.
- 실행: `python -m coffee_service.news_backfill_sources`는 원자료 체크포인트를 재사용해 과거 선택을 고정한다. `python -m coffee_service.news_backfill`은 최근 목록부터 처리하고 이후 과거 목록으로 넘어간다. 단일 파일 lock, 불변 분석키 재사용, 원자적 캐시/상태 저장, JSON/CSV export를 사용하며 DB·서빙 모델을 바꾸지 않는다. Mac 외부 Python 3.12.14/requests 2.34.2/pandas 2.3.3을 그대로 사용했다.
- 429/비용: 공급자 응답은 `rate_limit_exceeded`/upstream high demand였다. 사용자 지정대로 기본 요청 간격 300초이며 더 긴 Retry-After(초/HTTP 날짜)는 준수한다. 재시작도 다음 요청 시각을 보존한다. 401/402/403·무효 응답·비용 미확인/로컬 비용 상한에서는 멈추며, 서버의 사용자 설정 $1 budget은 변경하지 않는다. 연속 실패 288회(약 24시간)에서 중단한다. [공식 rate-limits](https://vercel.com/docs/ai-gateway/rate-limits)는 429와 예산 초과 402를 구분한다(2026-09-26 확인). 공식 Free 표시와 달리 실제 응답에는 비용이 있으므로 무료로 단정하지 않는다.
- 실행 상태(2026-09-26T13:07 UTC 확인): 최근 188/549건 완료, 나머지는 백그라운드 진행 중이다. 과거 960건은 선정 완료·분류 대기이며 최근 완료 후 중복 분석을 재사용한다. 이번 작업 성공 응답 비용은 단건 확인 포함 $0.001543584(계정 전체 청구액 아님)이었다. 최신 상태는 `data/raw/jev/backfill-status.json`, 요청 이력은 `backfill-requests.jsonl`, 로그는 `backfill-worker.log`다. 결과와 원자료는 ignored `year-20250926-20260925/`, `validation-2022-2025/`에 있다.
- 감시: 이 채팅의 `Jev 뉴스 백필 상태 확인` heartbeat(id `jev`)는 30분마다 파일만 확인한다. 정상 진행/429 대기에는 조용히 종료하고 완료·실패·사용자 조치가 필요할 때 알린다. API 요청과 대기는 Python이 처리하므로 매분 모델을 호출하지 않는다. 로컬 Mac이 실행 가능한 상태여야 하며 재부팅 후에는 checkpoint 재개가 필요하다.
- 검증: 관련 fixture 테스트 28개 통과(재시작 요청 간격, Retry-After 1시간 초과, 402 중단, 작업 간 재사용, 출처·발행/수집 시각·publisher 보존, 일별 선정/숫자가 바뀐 후속 기사/수집 재개). 실제 단일 호출 성공·429 안전 기록·분리 프로세스 실행·과거 체크포인트 97개를 확인했다. 독립 리뷰의 재사용 출처/이용 가능 시각 문제와 publisher 잔존 문제를 수정했다. 전체 뉴스 분류는 아직 완료가 아니며 CI/Docker/DB/앙상블 학습은 실행하지 않았다.
- 시계열 한계: 기존 `arabica-kc-futures-v2`의 커피 선물가격 강세/약세/중립/불확실과 관련성 확률이다. 현재 시점의 과거 기사 재분류이며 당시 실시간 예측 자료가 아니다. published/modified/collected/analyzed/available 및 NY 다음 자정 선정 시점을 보존한다. 향후 연구에서도 2022~2023 Validation과 이미 본 2024~2025 Test를 구분하고 날짜를 맞췄다는 이유만으로 미래 정보나 모델 사전학습 오염이 제거됐다고 주장하지 않는다.

## 2026-09-26 — Persistence 제외 단순평균·뉴스 결합 실험

- `03_3_single_model_comparison.ipynb` 아래 9~12절(10셀, 코드 6셀)을 추가했다. 기존 21셀의 소스·출력은 그대로 보존했다. 6개 모델의 15개 쌍과 전체 6개 조합의 **예측 가격 산술평균**을 계산하고 로그수익률로 되돌려 비교했다. Persistence는 비교 기준으로만 사용한다. 기존 2022~2023년 검증 설정을 재사용하고 같은 검증 RMSE로 조합을 고정한 뒤 이미 본 2024~2025년을 재평가했다.
- 검증 선택: 5일 NLinear+TimesNet, 20일 NLinear+XGBoost, 60일 DLinear+XGBoost. 과거 재평가 로그수익률 RMSE는 각각 **0.057513 / 0.114463 / 0.162403**, Persistence 대비 **−5.64% / −1.29% / +12.57%**다. 전체 6개 평균은 **0.055734 / 0.111382 / 0.159296**으로 **−2.37% / +1.44% / +14.25%**다. 전체 평균의 가격 RMSE 개선은 **−3.07% / −0.09% / +9.83%**이므로 20일 가격 오차 개선으로 해석하지 않는다. 사후 최저 조합으로 검증 선택을 교체하지 않았다.
- 안정성: 검증 오차 상관, 연도별, 지평별 모든 offset의 비중첩 평가, paired circular bootstrap(seed42/1,000회/블록 h·2h)을 추가했다. 60일 전체 평균의 MSE 개선 조건부 95% 구간은 h에서 **[0.001308, 0.015856]**, 2h에서 **[0.001269, 0.014891]**이다. 연도별로도 개선됐으나 비중첩 offset 중 악화도 있고, 이미 본 기간·단일 seed·후보 선택 불확실성과 다중 비교는 보정하지 않았으므로 전향적 우월성을 확정하지 않는다.
- 뉴스: 과거 7,548건의 기존 `title-rules-v2`(방향 기사 32건)를 사용해 7달력일 신호로 전체 평균의 잔차를 보정하는 절편 없는 비음수 회귀를 추가했다. 2022년 적합→2023년 규제 선택→두 해 재적합→2024~2025년 고정 평가다. **2022년 방향 신호 0일** 때문에 보정 강도를 식별하지 못했고 최종 무보정으로 남았다. 0 개선·[0,0] 구간은 동일 예측의 기계적 결과이며 뉴스 효과 없음의 증거가 아니다. 수집 시점까지 지킨 과거 live 입력은 0건이다. 2026년에 재수집한 제목 규칙의 연구용 결과와 실제 Jev 감성분석을 구분했다.
- Jev 88건은 2026-03-13~09-25 기사로 기존 모델 평가 기준일과 공통 기간이 0개다. 따라서 **6개 모델+Jev 감성분석 비교는 미검증**으로 표시했다. 새 API 호출이나 분류·가격 재수집은 없었다. 후속은 고정 절차로 새 날짜의 모델 예측·뉴스 분석 완료 시점·성숙한 정답을 함께 누적하는 평가다.
- 검증: Mac 외부 Python 3.12.14에서 저장된 원본 예측을 복원하고 추가 코드 6셀을 최종 **14.6초**에 순차 실행했다. 전체 모델 재학습은 반복하지 않았다. 원본 21셀 객체 동일, nbformat/문법/오류 출력 없음, 한글 그림 2개, 가격 산술평균·Persistence/중복 혼입 거부·날짜 누락 차단·성숙 정답·미래 기사 불변·무신호/방향 제약·지표 대조 검사 통과. 결과 34,176행과 지표는 원본 run의 ignored `ensemble_news_v1/`에 보관했고 같은 결과의 재저장도 통과했다. 독립 리뷰에서 부분 실행 경로 추론을 지적해 제거하고 Run All의 `OUTPUT`만 사용하도록 정리했다. 커널의 기존 loopback TCP 안내는 남아 있으며 외부 공개 서버를 열지 않았다.
- 근거 확인(2026-09-26): [단순평균](https://otexts.com/fpp3/combinations.html), [시간순 평가](https://otexts.com/fpp3/tscv.html), [블록 bootstrap](https://bashtage.github.io/arch/bootstrap/timeseries-bootstraps.html). 의존성·서비스 모델·배포 계약은 변경하지 않았으며 CI/Docker/전향적 검증은 이번 범위가 아니다.

## 2026-09-26 — 가격·기후·거시 6개 단일 모델 지평별 비교

- 범위: `data_code/03_3_single_model_comparison.ipynb`에서 DLinear·NLinear·XGBoost·LightGBM·PatchTST·TimesNet을 5/20/60거래일 누적 로그수익률로 비교했다. 앙상블 없이 가격 4·거시 3·기후/달력 20개, 공통 60일 창을 사용하며 트리도 같은 창을 펼쳐 입력한다. 신경망은 scalar 회귀 변형으로 명시했다. 기존 DLinear 구조와 변환 함수를 재사용하고 새 네트워크만 `data_code/single_model_networks.py`로 분리했다. 기존 노트북·서비스·artifact·의존성은 변경하지 않았다.
- 평가: 2015년부터 expanding 학습, 2022/2023년 검증의 합산 RMSE로 각 모델의 10/30 epoch 또는 100/300 tree 설정을 선택했다. 월별 기후 기준값·scaler·target 통계는 fold별 학습 범위에서만 적합하고 정답 날짜 경계를 지켰다. 고정 설정으로 2015~2023년 재학습 후 이미 본 2024~2025년을 재평가했으며 미사용 Test라고 부르지 않는다. seed 42 한 개의 제한된 설정 비교다.
- 결과: 검증 선택은 5/20일 Persistence, 60일 DLinear다. 과거 재평가에서 단일 모델 최저 RMSE는 5일 DLinear **0.055739**(Persistence 0.054442보다 나쁨), 20일 LightGBM **0.112507**(Persistence 대비 +0.44%), 60일 PatchTST **0.160502**(+13.60%)다. 검증 선택 60일 DLinear는 **0.163876**(+11.78%)이며 사후 최저 모델로 선택을 바꾸지 않았다. 단일 seed·작은 후보군·겹치는 target 때문에 안정적 우월성을 확정하지 않는다.
- 실행/보관: Mac 외부 Python 3.12.14, CPU 1 thread에서 Notebook 전체 **535초** 실행 완료. 코드 셀 11개와 한글 그림 2개를 저장했다. 개별 예측 27,297행과 지표·연도별·비중첩 분석은 ignored `data/processed/single_model_comparison/20260926T115735770501Z/`에 보관했다. 후속 단순평균을 위한 개별 예측일·목표일·지평·seed·설정만 저장하며 평균 예측은 만들지 않았다.
- 검증: 새 모델 단위검사 **8 passed**(forward/backward, NLinear 채널 복원, TimesNet 상수 입력·배치 독립성·그룹 계산 대조), 미래 원천 변경 시 과거 입력 불변·target 날짜/결측·공통 평가 행·지표 재계산 검사 통과. 독립 리뷰의 결과 저장 재시도 문제를 수정해 임시 디렉터리 전체 기록 후 rename하며 동일 결과는 재사용하고 충돌은 보존한다. 수정한 저장 셀을 실제 결과로 재실행하고 저장 실패/재시도/충돌 검사를 통과했다. nbformat·코드 compile·저장 PNG 한글/수치와 HTML 본문을 확인했다.
- 근거 확인(2026-09-26): [DLinear/NLinear](https://github.com/cure-lab/LTSF-Linear), [PatchTST](https://github.com/yuqinie98/PatchTST), [TimesNet](https://github.com/thuml/Time-Series-Library/blob/main/models/TimesNet.py), [XGBoost](https://xgboost.readthedocs.io/en/stable/python/python_api.html), [LightGBM](https://lightgbm.readthedocs.io/en/stable/pythonapi/lightgbm.LGBMRegressor.html). 설치 Torch 2.14.0 / XGBoost 3.4.1 / LightGBM 4.7.0을 유지했다. 초기 font cache 권한 안내는 쓰기 가능한 cache 경로로 해결했다. Jupyter 실행에는 loopback TCP 비암호화 안내가 있었으며 외부 공개 서버나 secret 입력은 사용하지 않았다. CI·Docker·배포 및 다중 seed 검증은 이번 범위가 아니다.

## 2026-09-26 — 단색·Fade 대시보드와 목표일 기준 예측 비교

- 변경: 기존 Vue/SVG 화면을 단색 배경·청록 강조색·선 중심으로 정리했다. 가격 비교를 뉴스 설명보다 먼저 배치하고 밝게/어둡게/시스템 테마, 진입 및 지평 변경 CSS Fade, `prefers-reduced-motion` 대응을 추가했다. 뉴스 상세·모델·실행·소스 정보와 기존 anchor를 유지하며 의존성·API·DB·모델은 변경하지 않았다.
- 날짜 계약: 최근 180개 관측일의 종가에 현재 모델의 `target_date`를 연결한다. 예측 기준일은 목표일보다 앞서야 하며 미래 목표는 제외한다. 양쪽 선은 같은 달력 시간축을 사용하고 결측은 선을 끊는다. 최신 비교에는 정확히 같은 목표일의 예측만 쓰며 최신 종가가 없으면 비교·건수에서 제외한다. 과거 기준일 재생 예측과 당시 실시간 발표 기록을 구분하고, 이 차트에 Jev의 현재 보정값을 과거 시계열처럼 그리지 않는다.
- 실측: native API에서 최근 180개 관측일 각각에 5/20/60거래일 예측 180개가 대응하며 목표일 중복이 없었다. 2026-09-25 실제 278.600006 ¢/lb에 대응하는 예측은 5일 294.549988(기준 09-18), 20일 341.899994(08-27), 60일 278.228516(07-01)이다. 화면의 반올림 수치와 부호 있는 오차를 API와 대조했다.
- 구현 근거 확인(2026-09-26): [Vue 상태 변경 애니메이션](https://vuejs.org/guide/built-ins/transition.html), [MDN 모션 감소 설정](https://developer.mozilla.org/en-US/docs/Web/CSS/Reference/At-rules/@media/prefers-reduced-motion). 설치 Vue 3.5.43 / Vite 8.3.0 / plugin-vue 6.0.8을 유지했다. Mac Node 26.5.1에서 검증하며 CI의 Node 22 설정은 변경하지 않았다.
- 검증: Vue **8 passed**, Vite production build 성공(JS gzip 36.03 kB / CSS gzip 3.58 kB), `git diff --check` 통과. 섞인·불규칙 날짜, 현재 모델 필터, 미래/무효 기준일 제외, 지평 전환, 예측·종가 결측과 뉴스 조회 실패/지연을 확인했다. 독립 리뷰에서 발견한 결측 비교·테마 범위·상태 문구를 수정하고 최종 재검토에서 추가 결함이 없었다.
- 브라우저: 실제 로컬 API에 연결해 밝은/어두운 테마와 5/20/60거래일 전환, 최신 수치, 뉴스 보정 표를 확인했다. SVG의 grid 최소 크기로 축을 넘던 문제를 수정했으며 1280×720에서 차트/프레임 모두 250px, 최신 비교 하단은 약 698px였다. 320px 화면에서 페이지 가로 넘침이 없고 지평 버튼 높이는 44px다. 뉴스 표는 내부 가로 스크롤과 키보드 포커스를 제공하며 ArrowRight 이동을 확인했다. 390px 화면 캡처와 브라우저 오류/경고 없음도 확인했다. 주요 글자·강조색의 계산 대비는 밝은 테마 최소 4.70:1, 어두운 테마 최소 7.54:1이다.
- 범위/한계: Mac 네이티브 API·Vite와 in-app browser 검증이다. 모션 감소 CSS는 코드로 확인했으며 OS 설정 변경에 따른 실동작, Lighthouse 성능 지표, 다른 브라우저, Docker/CI/GHCR/Azure는 이번에 검증하지 않았다. 기존 문서 변경과 사용자 추가 스킬 파일은 분리해 보존하며 push·외부 배포는 하지 않았다.

## 2026-09-26 — 뉴스 확률·방향과 0~1 반영 강도 분리

- 결정/완료 조건: 88개 기존 Jev 분류를 재사용하고 기본 Persistence/DLinear 산출물을 보존한다. 방향은 `(P_bullish−P_bearish)×relevance`, 적용량은 0~1 강도·과거 변동성·최근성·거래일 시차로 결정한다. 강한 확신의 같은 방향 뉴스는 더 크게 반영하되 음의 회귀계수로 뉴스 방향을 뒤집지 않는다. 실험적 가격 변화와 성능 개선 근거는 별도 표시한다.
- 원인/변경: v1의 0은 기본값이 아니라 지평을 함께 학습한 비음수 Ridge의 경계 해였다. v2는 지평별 비중첩 수익률 구간으로 강도를 추정하고, 효과 없음 50% + Uniform(0,1) 50% 사전의 사후평균·95% 사후구간을 제공한다. `confidence`는 확률분포로부터 계산된 요약이므로 중복 곱을 제거했다. 최근 20일 로그수익률 표준편차×√5를 가격 규모로 사용하고 반감기 3일·시차 0/1/3/5·지평 τ10은 고정 가정으로 표시한다. 임의의 양수 하한이나 향상을 보장하는 기본 가중치는 넣지 않았다.
- 실제 실행: native PostgreSQL 17, Python 3.12.14에서 cache-only 실행 성공. 최종 run `a4948176-6d26-4663-acca-cbb02f67ed70`, 모델 `news-residual-v2-eeea07bd411f`, 발행 `2026-09-26T10:33:27.067045+00:00`. 분석 88건, 신규 Jev 요청 0회. 기존 가격 3,076행·기본 예측 2,061행은 API 전체 JSON 대조로 동일했다. v1 artifact는 ignored `data/raw/jev/articles.model.v1-backup.json`에 보존했다.
- 보정 실측: 5일 강도 **0.116431**, 95% 사후구간 **[0, 0.799194]**, 비중첩 학습 25구간. 기본 278.600006→**277.652812 ¢/lb**(약 −0.34%). 20일 강도 **0.251128**, 구간 **[0, 0.950379]**, 학습 6구간. **278.143751 ¢/lb**(약 −0.16%). 20일은 사전평균 0.25와 비슷해 사전 가정의 영향이 크다. 60일은 2구간으로 최소 6구간 미달이며 보정가 null이다. 현재 뉴스 입력은 시차 전체에서 22개 기사이며, 일별 대표 선정 규칙은 바뀌지 않았다.
- 평가: 연구 재평가 5일 origin 2026-07-10~09-18의 50개 동일 날짜에서 baseline→뉴스 RMSE **0.0561664→0.0565635**, MAE **0.0496921→0.0501057**(로그수익률). 평균 MSE 개선 −0.000044767, 5거래일 circular blocks/seed42/1,000회 95% 구간 **[−0.000213217, +0.000103672]**로 개선 미확인이다. 방향 일치 0%→40%는 Persistence가 보합만 예측하는 특성 때문에 단독 개선 근거로 사용하지 않는다. 20일은 walk-forward 각 시점의 최소 학습 표본을 충족하지 못해 평가 0개다. 과거 기사의 현재 분류·이미 확인한 기간이므로 미사용 Test나 실제 live 성과가 아니다.
- 검증: Python 뉴스·DB/API 관련 **37 passed, 2 warnings**, Vue **7 passed**, Vite build 성공. 시간 경계/일별 선정 완료/수정/미래 분석 차단, 강한·약한 확률과 부호, confidence 중복 미사용, target maturity·비중첩 학습·walk-forward purge, 결측 변동성·artifact 왕복을 확인했다. 별도 SciPy 적분으로 실측 5/20일 사후평균·null probability를 대조해 NumPy 격자 결과와 1e-6 이내 일치했다(런타임 의존성 추가 없음). 브라우저에서 보정가·가중치·사후구간·계산 설명·성능 비교·기사별 네 확률을 확인했다. 독립 리뷰 지적의 상단 성능 단정 문구를 수정했고 재검토에서 추가 결함이 없었다.
- 근거 확인(2026-09-26): [TypeSafe Choice](https://docs.typesafe.ai/primitives/choice)·[confidence](https://docs.typesafe.ai/confidence), [시간순 rolling-origin](https://otexts.com/fpp3/tscv.html), [시간 블록 bootstrap](https://bashtage.github.io/arch/bootstrap/timeseries-bootstraps.html). [Financial News Intelligence Platform](https://github.com/abhiminav/financial-news-intelligence-platform)과 [StockIntel](https://github.com/zhaymn/StockIntel)의 README에서 확률 차이·과거 수익률 통제·시간순 평가·부정적 결과 공개를 참고했으며 그 결과를 재현하거나 커피 가중치로 전용하지 않았다. 선택한 사전분포·감쇠 수치는 이 출처로부터 추정된 값이 아니다.
- 한계/다음: 통계적으로 유의미한 가격 예측 개선이나 인과 효과를 확인하지 못했다. 200일 중 88개 대표 기사라는 제한, 사람이 확인한 분류 정답 및 실제 확률 calibration 부재, 작은 비중첩 표본과 Bayesian 오차 가정을 유지해서 해석한다. 다음은 전향적 뉴스·실현 가격 누적과 분류 품질 검증이다. 기존 Starlette httpx/AnyIO deprecation 2건은 숨기지 않았으며 별도 호환성 작업이다. 의존성 변경·새 API 호출·Docker/CI/GHCR/Azure 검증·push는 하지 않았다.

## 2026-09-26 — 기본 예측의 빈 신호와 Jev 보정 표시 구분

- 원인: `/api/v1/predictions`의 기존 방향 분류·뉴스 항목은 null이고, Jev 결과는 별도 `/api/v1/news/jev`에 저장된다. 기본 표의 반복된 “이용 불가”가 정상 Jev 분석까지 실패한 것으로 보이게 했다.
- 수정: 기본 모델 예측과 Jev 실험적 가격 보정의 표제를 구분하고, 별도 분류가 없으면 이유와 Jev 결과 링크를 표시한다. 실제 저장 모델의 반감기·시차·지평 감쇠·계수를 펼쳐 볼 수 있다. 유효한 모델과 보정값이 모두 0인 경우에만 가격 변화 없음의 이유를 명시한다.
- 계산 확인: 기사 압력은 `(P_bullish-P_bearish)×relevance×confidence`, 발행 후 달력일 나이로 `2^(-age/3)` 감쇠 후 합계의 tanh를 사용한다. 0/1/3/5거래일 시차 계수는 현재 모두 0이다. 지평 배수 `exp(-(h-5)/10)`는 5일 1, 20일 0.22313, 60일 0.004087이나 60일은 학습·평가 자료 부족으로 적용하지 않는다.
- 실측: 저장 run `7d1f7ea6-668f-48e3-a890-6a58e808ff53`의 실제 이용 가능 시점으로 다시 계산한 최신 시차 입력은 `[-0.6711112202, 0, 0, 0]`이다. 과거 기사도 이번에 처음 분류했으므로 이전 거래일의 live 입력은 0이다. 최종 계수 0으로 5/20일 가격 보정은 0이며, 이 사실이 뉴스의 경제적 효과가 없다는 결론은 아니다. 모델·학습·DB·API 계약과 가격 수치는 변경하지 않았다.
- 검증: Vue 회귀 6 passed, Vite build 성공, 독립 리뷰의 JSON boolean/문자열 0 오인 조건을 수정했다. 실제 로컬 API와 브라우저에서 기본 예측의 빈 분류 설명, Jev 88건 완료·5/20일 0 보정·60일 자료 부족을 확인했다. 추가 Jev 호출·재학습·DB 쓰기·의존성 변경은 없었다.

## 2026-09-26 — 최근 200일 뉴스의 일별 1건 선정·Jev 실분류 완료

- 범위/원자료: 2026-03-11~09-26(200달력일). Yahoo KC=F 메타데이터 200건과 기존 WordPress 수집기의 Daily Coffee News 제목 343건, 합계 543건을 확보했다. Yahoo 표본은 5월 이후이며 전체 기간의 모든 뉴스를 보장하지 않는다. `data/raw/jev/combined-200d.json`에 후보, `wordpress-200d.parquet`에 제목 메타데이터, `daily-200d-results.csv`에 최종 88건을 저장했다(모두 ignored).
- 일별 선정: 완료된 America/New_York 날짜별 최대 1건. 현재 199개 완료일 중 88일 선정, 111일 미확보, 진행 중인 9월 26일 제외. 중요도는 커피 가격·공급·작황·기상·수출·재고 등 제목/요약 단어 점수다. 카페·장비·개별 주가 기사는 제외한다. 현재 표본의 정규화 URL/내용/동의어 유사 중복 검출은 0건이었다. 재게시 제거는 fixture로 검증했으며, 같은 주제라도 수치·방향이 바뀐 후속 기사와 표현이 크게 다른 의미상 중복을 완벽하게 구별하지는 못한다.
- 재현/시점: `.selection.json`에 선택 날짜·점수·정책·다음 NY 자정의 이용 가능 시각을 기록하고 완료 날짜를 고정한다. 다른 기간 재실행에서 과거 선택은 보관하되 요청 기간만 분류/조회한다. 수치·반대 방향 업데이트는 보존하며, 연구 feature도 기사 수정 시각과 일별 선정 완료보다 앞서 쓰지 않는다. 현재 분류는 당시 실시간 수집을 재현한 자료가 아니다.
- 실제 Jev: 단건 방식으로 신규 1건 성공 후 429가 발생해 질문별 기사 입력을 분리한 `arabica-kc-futures-v2`로 변경했다. 공통 state는 시장뿐이며 질문마다 해당 기사만 제공한다. 최종 88건은 **bullish 39 / bearish 40 / neutral 4 / uncertain 5**다. v2는 9회 요청(200 3회, 429 6회) 후 모두 완료했다. 제공자 오류는 upstream high demand였고 Retry-After는 없었다. 제한된 수동 재시도 간격을 60/120/240초로 늘렸다. 성공 배치는 20/20/48건: 마지막 48건은 앞선 사용량을 확인한 일회성 backfill 요청(84,939 JSON UTF-8 bytes, 실제 input 21,915 tokens)이었고 기존 응답 검증기로 모두 검사한 뒤 저장했다. 일반 CLI는 보수적인 최대 20건/60KB 상한을 유지한다.
- 비용/보존: v2 성공 응답 input 39,837 / output 6,593 tokens, 기록 비용 합계 **$0.001673154**(계정 전체 청구액 아님). 배치 usage/cost는 첫 레코드에만 기록해 중복 합산하지 않는다. 이전 v1 분석 2건은 파일에 보존하되 현재 API/학습에서는 v2 일별 선정 88건만 사용한다. `200d-request-audit.json`에 v2 HTTP 시도 이력을 보존했다.
- 실제 E2E: native PostgreSQL 17 / loopback 15439에서 뉴스 run `7d1f7ea6-668f-48e3-a890-6a58e808ff53` success, 분석 88건·예측 snapshot 3행. 기존 가격 3,076행과 2,061개 가격 예측 계약을 유지했으며 예측 checksum `462e019814a004984ed461c2d502c930`이 동일했다. 실제 캐시/후보 재실행은 네트워크를 금지한 상태에서 신규 API 요청 0회/88건 재사용이었다. 실제 API 200, 기사 88건/고유 NY 날짜 88개를 확인했고 브라우저에 선정 88·완료 88·최근 기사 목록이 표시됐다.
- 예측 결과: 연구용 Ridge는 반감기 3일, alpha 1, 지평 tau 10을 선택했으나 **계수 4개 모두 0**이었다. 5/20일 보정값은 0, 가격은 각각 기존 278.6000061 ¢/lb와 같았다. 60일은 mature tune/holdout 부족으로 null이다. 5일 holdout 25행 RMSE 0.0677684, 20일 10행 0.2312489로 뉴스 보정과 baseline이 동일했다. 입력 기사 18건이라는 표시는 실제 가격 변화나 정확도 향상을 뜻하지 않는다. 분류 정확도와 예측력 개선을 확인하지 않았다.
- 검증: Python 전체 서비스 회귀 **111 passed, 기존 경고 3개**, 77.98초. 마지막 중복 정규화/응답 type 검증/최신 기사 우선 처리 후 관련 회귀 **47 passed**. Vue **5 passed**, Vite build 성공. 독립 리뷰에서 발견한 NY 경계·반대 방향 유실·미선정 API 노출·범위 밖 고정 기사·동의어 재게시를 수정했다. 의존성/설치 변경 없음. 새 Docker/CI/GHCR/Azure 실행은 하지 않았다.
- 공식 확인(2026-09-26): [TypeSafe 질문 독립성](https://docs.typesafe.ai/primitives), [구조화된 instructions/API](https://docs.typesafe.ai/api), [모델 context limits](https://docs.typesafe.ai/models), [Vercel rate limits](https://vercel.com/docs/ai-gateway/rate-limits), [WordPress posts](https://developer.wordpress.org/rest-api/reference/posts/), [yfinance get_news](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_news.html). 다음은 분류 품질 표본 검토와 뉴스 계수 0의 원인 확인이며, 성능을 좋아 보이게 하려고 가격을 임의 조정하지 않는다.

## 2026-09-26 — Jev 뉴스 수집·분류와 실험적 가격 보정

- 추가: `jev.py`의 Yahoo KC 뉴스 수집과 TypeSafe choice/noul 요청, 불변 분석 캐시·원자 저장·요청 상한; `news_residual.py`의 최근성 감쇠·거래일 시차·단기 가중 잔차 Ridge; news CLI·별도 DB snapshot·`/api/v1/news/jev`·Vue 비교/기사 패널. 기존 가격 서빙 계약·가격 테이블을 유지했다. 설치/의존성 변경 없음. 예제 Gateway 키는 빈 placeholder로 정리했다.
- 실제 수집: 2026-09-26 Yahoo `KC=F` 메타데이터 199건 조회 중 2026-06-29~09-25의 122건을 확보했다. 최신 표본이므로 90일 전체 커버리지로 간주하지 않는다. 원자료는 ignored `data/raw/jev/yahoo-probe.json`, 성공 분석은 `articles.json`, 초기 실행 상태는 `articles.json.status.json`에 보존했다. GDELT는 timeout/429로 실수집 실패해 기본 소스를 Yahoo로 결정했다.
- 실제 Jev: 합성 smoke 1회와 실제 기사 1회가 HTTP 200이었다. 실제 기사 “Short Covering Boosts Coffee Prices”는 bullish 0.48/bearish 0.01, confidence 0.30, relevance 0.93을 반환했다. 이는 분류 품질 평가나 수익률 확률 검증이 아니다. 이후 HTTP 429가 반복되어 최초 요청 예산(합성 1+실배치 199)을 소진했다. 성공 응답에 기록된 비용만 합계 $0.000045906이며 계정 전체 청구액은 확인하지 않았다. 이 실패를 반영해 429 즉시 중단/연속 transient 3회 중단으로 수정했고 추가 Gateway 요청은 하지 않았다.
- 실제 가격·DB: 원본을 별도 `data/processed/jev_live`로 복사한 뒤 Yahoo/FRED incremental을 실행해 2026-09-25까지 가격 3,076행·예측 2,061행을 적재했다(run `a5d4f044-70f5-43e4-bee5-d2495bce6767`). 별도 native PostgreSQL 17, loopback 15439/coffee_jev를 사용했다. 실제 뉴스 cache-only 실행 2회(`1f4eedec-b6de-4c85-b104-7b631bc54cb3`, `2a23a0ef-198c-454e-9695-cf65edf7470f`)는 partial이며 신규 API 요청 0회, 분석 1건·지평별 snapshot 3행을 남겼다. 기존 2,061개 예측 전체 checksum `462e019814a004984ed461c2d502c930`을 유지했다.
- 결과: 세 지평 모두 `insufficient_data`, 보정 가격 null이다. 기존 5/20일 278.6000061, 60일 361.8698120 ¢/lb를 보존했다. API와 브라우저에서 부분 처리·기사 1건·자료 부족을 확인했다. 실제 자료로 잔차 학습·성능 개선을 검증하지 못했으며, 수치 보정·최근성·시차·시간순 평가 동작은 fixture로 검증했다.
- 검증: 외부 Python 3.12.14, requests 2.34.2/pandas 2.3.3/scikit-learn 1.7.2/yfinance 1.7.0/python-dotenv 1.2.3. 실제 PostgreSQL을 지정한 `python -m pytest tests --ignore=tests/test_core4_environment.py -q`: **102 passed, 3 warnings**, 72.71초. 기존 sklearn 단일 클래스 경고와 Starlette httpx/AnyIO deprecation은 남는다. Vue **5 passed**, Vite 8.3.0 build 성공, placeholder DB password로 Compose 설정 검증 성공. 새 Linux container/CI/GHCR/Azure 실행은 검증하지 않았다.
- 독립 리뷰: target 결측 정답 유입, 성숙 경계·tune purge·지평 자격, 미래 기사 노출, 실패 snapshot/cache 상태, 뉴스 요청 지연, 예제 키를 수정했다. 실코드/설정 리뷰와 관련 회귀 검증을 마쳤다. 원래 사용자 문서·AGENTS 변경과 원본 자료/.env는 보존했다.
- 공식 확인(2026-09-26): [Vercel TypeSafe endpoint/schema](https://vercel.com/docs/ai-gateway/sdks-and-apis/typesafe), [TypeSafe primitives](https://docs.typesafe.ai/primitives), [choice](https://docs.typesafe.ai/primitives/choice), [Jev 한계](https://docs.typesafe.ai/model-jaggedness/jev-1.13), [yfinance get_news](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.get_news.html), [GDELT DOC 2](https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/).
- 다음: Gateway 429 제한 해소 후 작은 batch로 분류를 누적하고 실제 이용 가능 시점 이후의 정답이 성숙하면 재평가한다. 강세/약세 정답을 사람이 검토한 표본과 시간순 baseline 비교 전에는 예측력 개선을 주장하지 않는다.

## 2026-09-20 — B. 게시 이미지 복원 배포 검증과 신규 수집 경계

- 게시 증거: [run 35455393542](https://github.com/sjinie/Coffee_Price_Prediction/actions/runs/35455393542) success, source와 조회 당시 원격 main 모두 `fab81c0cb2f72a58eab9244976a333940bae99d2`. 로그의 API/pipeline/web digest가 GHCR manifest 및 실제 pull과 일치했다. 모두 linux/amd64이며 전체 digest는 README·compose.deploy.yaml에 기록했다. 실제 이미지 안 API 3개·pipeline/config/entrypoint 17개 파일의 SHA-256도 게시 소스와 일치했다.
- 구성: 기존 Compose·Dockerfile을 보존하고 build 없는 compose.deploy.yaml, placeholder env, stdlib 배포 CLI를 추가했다. PostgreSQL 17.11-bookworm digest를 유지하며 web loopback만 열고 API/DB는 내부 통신한다. DB·pipeline은 project별 named volume, seed/model은 읽기 전용이다.
- 입력: 기존 2014-07-01~2025-12-31 Parquet 15개를 별도 seed에 복사했다. 추적된 DLinear artifact의 SHA-256은 `29ad00b20a0f71846d6a81c1f2edddd1d3960639b1819372b0fd2c0cdccc3b82`, ID dlinear-price-macro-h60-v1, 학습 2,011행이다. 원본 자료·artifact는 변경하지 않았다. 신규 수집·재학습·뉴스 적재는 실행하지 않았다.
- 실제 실행: Mac Docker Engine 29.8.0/Compose 5.5.1, ARM host에서 amd64 이미지를 실행한 별도 project `coffee-ghcr-20260920`. 같은 deploy.py up/restore/verify로 schema 초기화·DB health·정적 web·Nginx /api·가격 2,892행·예측 1,509행·모델 2개·소스 15개를 확인했다. 복원 run `b908aea4-9c6f-4a7e-a6a4-2d1c262ac1d4`, 사전 입력 검사 후 반복 run `51813c23-729c-4f99-961f-a399e5a615ce`가 성공했다.
- 재실행·영속성: 동일 기준일 재처리 전후 API 가격·예측 응답이 정확히 일치했고 예측 자연키 1,509개/중복 0개였다. postgres/api/web을 force-recreate한 뒤에도 동일 응답과 예측값·created_at의 정렬 checksum `5e1876332a04fd731950924e25691bb8`을 유지했다. volume 삭제는 하지 않았다. 이전 native snapshot과도 허용오차 내 일치했다(전체 수치 최대 차이 0.00006103515625, CPU 환경 차이의 원인은 별도 확정하지 않음).
- 브라우저: 게시 web image의 localhost 대시보드에서 2025-12-31 종가 348.75, 세 지평 예측, 15개 소스, 한국어 차트·5/20일 전환을 직접 확인했다. 60일 예측가 표시는 377.2였다. 뉴스·분류 artifact를 적재하지 않아 관련 항목의 이용 불가 표시는 예상 동작이다. Azure 브라우저 검증은 아니다.
- 리뷰에서 발견한 HIGH: 기존 pipeline은 짧은 feature 이력에서 h60 없이 success를 기록할 수 있었다. 수정 소스는 최신 가격일의 h5/h20/h60을 UPSERT 전에 검사하고 rollback/failed로 기록한다. 기존 이미지의 코드가 바뀐 것은 아니다. 배포 CLI는 기존 이미지 함수로 restore를 사전 검사하며 collect는 즉시 차단한다. 수정 소스의 push·새 GHCR 게시·digest 재검증과 차단 해제 후 신규 수집을 검증해야 한다.
- 최종 검증: 외부 Python 3.12.14 + 별도 PostgreSQL에서 `python -m pytest tests --ignore=tests/test_core4_environment.py -q`가 **79 passed, 3 warnings**(79.57초)였다. 데이터 기반 test_postgres_e2e도 포함하며 skip은 없었다. 기존 단일 클래스 sklearn 경고와 Starlette의 httpx/AnyIO deprecation 2건은 숨기지 않았고, 별도 라이브러리 호환성 작업으로 남긴다. macOS 환경 전용 검사는 이번 범위에서 제외했다. 새 배포 CLI 테스트 6개와 actionlint도 통과했다.
- 실패 경로: 별도 short project에 각 10행의 필수 자료를 제공하자 기존 이미지의 latest_predictions가 불완전 60거래일 창을 거부했고 CLI exit 1이었다. 실제 DB 컨테이너·적재 전에 실패했으며 collect 역시 legacy 이미지 단계에서 실행 전 차단한다.
- 독립 리뷰: A와 B의 실제 diff·공식 근거·image/source·초기화·secret·포트·재실행 경계를 검토했고 HIGH 수정 후 추가 조치할 결함이 없었다. 검증한 source guard는 아직 새 게시 이미지에서 실행한 것이 아니다.
- 공식 근거(2026-09-20): [Compose readiness](https://docs.docker.com/compose/how-tos/startup-order/), [up/volume 보존](https://docs.docker.com/reference/cli/docker/compose/up/), [GHCR digest·인증](https://docs.github.com/en/packages/working-with-a-github-packages-registry/working-with-the-container-registry), [PostgreSQL 공식 image](https://hub.docker.com/_/postgres). 새 의존성 설치·lockfile 변경 없음.
- 자원 실측(사용자 요청): 10 vCPU/7.748GiB Docker VM의 ARM host에서 amd64 게시 이미지를 실행했다. 대기 web 26.10/API 63.76/PostgreSQL 39.27MiB(합계 129.13MiB). 26.8초 복원 실행의 약 2초 간격 13회 샘플에서 pipeline 최대 331.2MiB·CPU 100.28%, API 84.54/web 27.18/DB 45.07MiB, 같은 시점 컨테이너 합계 관측 최대 477.5MiB였다. 짧은 컨테이너 종료 때 EOF로 비어 있는 샘플도 있어 절대 피크가 아닌 관측값이다. Docker stats는 cache 일부를 제외하며 OS·Docker 메모리는 별도다. 이미지 4개 실제 저장 합계 3.441GB, DB 논리 크기 9,142kB, 테스트 volume 전체 67.61MB였다. 신규 외부 수집·다중 사용자 부하·Azure 성능은 측정하지 않았다.
- 사양 제안(추정): 현재 4개 서비스·단일 batch·소수 사용자에 2 vCPU/4GiB RAM, OS/이미지/새 릴리스·로그 여유를 포함한 32~64GiB SSD. 컨테이너 0.5GiB 관측값에 OS/Docker 0.5~1GiB와 batch/cache 여유 0.5~1GiB를 예산으로 잡으면 4GiB 안에서 약 1.5~2.5GiB가 남는 계산이다(실측 OS 사용량 아님). [Azure Bsv2 공식 사양](https://learn.microsoft.com/en-us/azure/virtual-machines/sizes/general-purpose/bsv2-series)의 B2ls_v2는 2vCPU/4GiB, B2s_v2는 2vCPU/8GiB이며 CPU credit 소진 시 기준 성능으로 제한된다. 현 이미지는 amd64이므로 x86-64 VM을 선택한다. 지역 가용성·학생 구독 quota·가격은 미확인이다.
- Azure: 사용자가 VM을 아직 만들지 않았다고 확인했다. 이번 환경에는 Azure CLI도 없다. VM 생성·리소스 변경·시작·입력 업로드·배포·외부 공개는 수행하지 않았다. 실측 자원과 여유를 바탕으로 VM 사양·지역·비용을 정하고, 생성/배포 전 별도 승인받아야 한다.

## 2026-09-20 — A. Actions 내부 Node.js 24 전환

- 변경: checkout v4→[v5.1.0](https://github.com/actions/checkout/releases/tag/v5.1.0), setup-python v5→[v6.3.0](https://github.com/actions/setup-python/releases/tag/v6.3.0), setup-node v4→[v5.0.0](https://github.com/actions/setup-node/releases/tag/v5.0.0), login-action v3→[v4.6.0](https://github.com/docker/login-action/releases/tag/v4.6.0), build-push-action v6→[v7.4.0](https://github.com/docker/build-push-action/releases/tag/v7.4.0). 공식 release와 각 SHA의 `action.yml`에서 `node24`를 확인하고 full commit SHA+버전 주석으로 고정했다(확인일 2026-09-20 KST).
- 계약: 프로젝트 Node.js 22·Python 3.12, publish=false 검증, 동일 커밋 reusable CI 성공 후 publish=true+main 게시, 게시 job에만 packages:write를 유지했다. 의존성·lockfile·Dockerfile 변경은 없다.
- 호환성: Node.js 24 Action의 runner 최소 v2.327.1과 기존 게시 run의 v2.337.0을 대조했다. checkout v5.1.0의 pull_request_target 기본 동작 변경은 현재 event에 해당하지 않는다.
- 검증: 기존 run 35455393542의 Node.js 20 경고 대상과 수정 대상을 대조했고 YAML·SHA/게시 조건 정적 검사, 로컬 actionlint v1.7.7과 독립 리뷰를 통과했다. 실제 새 GitHub CI와 경고 소멸은 사용자 push/PR 반영 후 재검증해야 한다.

## 2026-09-19 — GitHub Actions CI·GHCR 게시 workflow 추가

- `.github/workflows/ci.yml`은 PR·main push에서 PostgreSQL 기반 fixture Python 테스트, Vue 테스트·build, API·pipeline·web Docker build를 실행한다. 저장소에 없는 처리 데이터·artifact가 필요한 `test_postgres_e2e.py`와 macOS 전용 환경 검사는 제외한다.
- `.github/workflows/publish-ghcr.yml`은 수동 실행만 가능하며 `publish=false`가 기본이다. 값과 관계없이 같은 커밋의 reusable CI를 실행하고, 성공 후 `publish=true`이면서 `refs/heads/main`일 때만 API·pipeline·web 이미지를 GHCR에 commit SHA tag로 게시한다. `packages: write`는 게시 job에만 부여한다.
- 로컬 actionlint·게시 조건 검사 통과. [새 PR #3의 Actions](https://github.com/sjinie/Coffee_Price_Prediction/actions/runs/35449683380)에서 Python 68개 통과·데이터 기반 재현 1개 skip, frontend 테스트 3개·build, API·pipeline·web Linux Docker build가 모두 성공했다. `test_postgres_e2e.py`와 macOS 전용 환경 검사 파일은 위와 같이 제외한다. 이미지 게시·Azure 배포는 수행하지 않았다.

## 2026-09-19 — 의존성 파일 확인·보완

- 기존 `frontend/package.json` 기준으로 `package-lock.json`을 offline 재생성했고 기존 버전은 유지했다. 직접 사용하는 `joblib==1.5.3`을 `requirements.txt`와 `requirements-pipeline.txt`에 명시했다. API 전용 목록은 유지했다.
- 외부 CPython 3.12.14에서 활성 Python·Notebook·테스트의 정적 import 대조상 누락 없음, `uv pip check` 통과, 세 requirements의 offline strict dry-run은 설치 변경 없음이었다. sandbox의 환경 lock 생성 경고가 있었으며 새 환경 설치 검증은 수행하지 않았다.
- 프런트엔드 테스트 3개와 production build 통과. 기존 staged 변경을 보존했으며 commit/push는 수행하지 않았다.

<a id="news-intelligence-v1"></a>

## 2026-09-19 — News Intelligence 진단·실험·서빙

**가격 모델은 5·20일 Persistence, 60일 DLinear를 유지했다. 뉴스 입력과 가격 결합 후보는 채택하지 않았으며, 이번 변경의 기존 서빙 대비 가격 RMSE 개선은 0%다.** 별도로 표시하는 상승 확률은 5일 수치 피처 CatBoost만 Validation 기준을 통과했고, 20일 CatBoost·60일 Logistic Regression은 실험적 확률로 남겼다. 기존 노트북·데이터·artifact와 아래 이전 실행 기록은 보존한다.

### 기존 예측 진단

외부 CPython 3.12.14에서 [baseline_diagnostics.parquet](../data/processed/news_intelligence_2026-09-19/diagnostics/baseline_diagnostics.parquet)와 [notebook_ses_diagnostics.parquet](../data/processed/news_intelligence_2026-09-19/diagnostics/notebook_ses_diagnostics.parquet)를 확인했다. 기간은 이미 본 2024~2025년이며, 아래 오차·표준편차는 로그수익률 단위다. 진단용 보합은 `abs(return) < 0.001`(10bp)이고 exact sign 평가와 구분한다.

| 지평·서빙 모델 | 사례 수 | RMSE / MAE | 예측 평균 / 표준편차 | 실제 표준편차 | 예측 10bp 미만 | exact / 10bp 방향 정확도 |
|---|---:|---:|---:|---:|---:|---:|
| 5일 Persistence | 498 | 0.054442 / 0.042810 | 0 / 0 | 0.054068 | 100% | 0.40% / 1.20% |
| 20일 Persistence | 483 | 0.113005 / 0.085380 | 0 / 0 | 0.109302 | 100% | 0% / 1.04% |
| 60일 DLinear | 443 | 0.167943 / 0.140408 | 0.015150 / 0.045342 | 0.157659 | 2.48% | 60.05% / 58.92% |

노트북의 5·20일 지수평활은 exact 방향 정확도가 51.0040%·50.9317%여도 예측 수익률 전부가 1bp 미만이었다. 10bp 보합을 적용하면 1.2048%·1.0352%로, 서빙의 가격 유지와 같은 값이다. 이 진단은 원 노트북 지표를 수정하거나 약 51%를 유효한 방향 예측력으로 인정한 것이 아니다. 전체 분포·분위수·예측별 값은 기존 diagnostics Parquet에 남겼다.

### 뉴스 범위와 시점

- Daily Coffee News WordPress 메타데이터 7,548건, 공개일 2014-07-01~2025-12-31. 실제 수집일은 2026-09-19이며 유료 API·LLM 호출은 0회다. [이용약관](https://dailycoffeenews.com/terms-of-service/)의 개인·비상업적 이용 제한을 확인했고, 본문 저장·기사 API 공개·원문 재배포는 하지 않는다. `summary`는 모두 빈 문자열이다.
- 저장 위치는 [news/articles.parquet](../data/processed/2014-07-01_2025-12-31/news/articles.parquet). `title-rules-v2`로 관련 4,554건, 강세 18건·약세 14건·중립 7,516건이다. 제목의 주제·수급 영향 규칙이며 일반 감성 분석이나 가격 인과효과로 해석하지 않는다.
- UTC 23시 기준 `published_at`과 `modified_at`을 모두 제한한다. 229건은 2025년 이후 수정되어 이번 과거 평가에서 제외됐다. `historical`은 수집 시각 제한을 풀어 과거 공개·수정 시각으로 재생하므로 당시 최초 공개본을 복원한 백테스트가 아니다. `live`는 `collected_at <= as_of`도 요구한다.
- 실제 뉴스 증분 수집은 7일 겹침으로 18건을 받아 최초 수집 버전과 7,548행을 그대로 보존했다. 연속 수집 범위를 Parquet 속성에 기록하며, 범위 부족·수집 실패와 정상 조회의 기사 0건을 구분한다.

### 실험 설계와 클래스 불균형

Train은 2015년부터 2020·2021·2022년 말까지 확장하고, 정답 날짜가 각 cutoff를 넘는 행은 제거했다. Validation은 각각 2021·2022·2023년이며 정답도 해당 연도 안에서 끝나야 한다. 수치 입력은 가격 4개+거시 3개, 뉴스 입력은 여기에 지평별 집계 창을 하나씩 더한다. 5일은 1/3/7일, 20일은 7/14/30일, 60일은 14/30/60일 창이다. 기사 0건 때문에 평가일을 제거하지 않으며 모든 후보와 현재 가격 기준을 같은 날짜로 비교했다.

Logistic Regression/Ridge, CatBoost, LightGBM의 고정 설정(seed 42)으로 수치/뉴스 회귀 A/B와 수치/뉴스 분류를 비교했다. 회귀 결합은 C1=회귀 원값, C2=회귀 절댓값×분류 방향, C3=`0.5*r + 0.5*σ_train*(2*p-1) + 0.1*σ_train*news_impact`다. 수치 전용의 뉴스 영향은 0이다. 학습된 meta-model은 없고 OOF·Test에 별도 모델이나 가중치를 fit하지 않았다. 60일 DLinear 기준도 fold별 cutoff까지 다시 학습했다. 선택을 고정한 뒤 2015~2023년으로 재학습하고 이미 본 2024~2025년을 기술 평가했다.

| 지평 | Train 행 수: 2020 / 2021 / 2022년 말 | Validation 2021 상승/하락/보합 | 2022 상승/하락/보합 | 2023 상승/하락/보합 | 이미 본 2024~2025 상승/하락/보합 |
|---|---|---|---|---|---|
| 5일 | 1,314 / 1,566 / 1,817 | 144 / 103 / 0 | 108 / 137 / 1 | 119 / 126 / 0 | 273 / 223 / 2 |
| 20일 | 1,299 / 1,551 / 1,802 | 169 / 63 / 0 | 95 / 136 / 0 | 120 / 110 / 0 | 289 / 194 / 0 |
| 60일 | 1,258 / 1,510 / 1,761 | 189 / 3 / 0 | 53 / 138 / 0 | 88 / 102 / 0 | 350 / 93 / 0 |

실제 수익률 0인 사례는 회귀에는 남기고 분류 학습·평가에서 제외한다. 따라서 `P(up)`은 보합 제외 조건부 확률이다. 분류 기준은 항상 상승·항상 하락·Train 다수 클래스·Train 상승 비율이며, 두 클래스가 있는 경우 상수 방향의 BA는 0.5다. 특히 60일 2021년에는 하락이 3건뿐이어서 BA·AUC도 변동에 민감하다.

### 선택 결과와 fold 변동

선택 규칙은 [설계의 채택 기준](architecture.md#news-intelligence-selection)에 고정했다. 분류는 평균 BA ≥0.52, 3개 중 2개 이상 fold에서 다수 클래스 BA 초과, 평균 Brier ≤Train 상승 비율 기준을 모두 요구한다. 가격 회귀는 평균 상대 RMSE 개선 ≥1%, 2개 이상 fold 개선, 최악 fold ≥−5%이며 5·20일에는 방향 BA 조건도 추가된다. 뉴스 후보는 동일 모델·동일 C번호의 수치 후보보다 평균과 2개 이상 fold에서 더 좋아야 한다.

수치 피처만 사용한 세 분류기의 Validation BA를 먼저 비교했다. 아래 값은 평균 ± fold 표준편차이며, 최고 BA라는 이유만으로 Brier 기준을 생략하지 않는다.

| 지평 | Logistic Regression | CatBoost | LightGBM |
|---|---:|---:|---:|
| 5일 | 0.517598 ± 0.038577 | 0.530362 ± 0.058599 | 0.522303 ± 0.025804 |
| 20일 | 0.546539 ± 0.054105 | 0.557942 ± 0.023629 | 0.488299 ± 0.080309 |
| 60일 | 0.642001 ± 0.038274 | 0.585839 ± 0.075208 | 0.577898 ± 0.134543 |

| 별도 분류기 | BA 2021 / 2022 / 2023 | BA 평균 ± fold 표준편차 | 평균 Accuracy / F1 / AUC | 평균 Brier / Train 비율 Brier | 결과 |
|---|---|---:|---|---|---|
| 5일 수치 CatBoost | 0.595267 / 0.481346 / 0.514472 | 0.530362 ± 0.058599 | 0.520929 / 0.454274 / 0.550295 | 0.250869 / 0.250897 | Validation 기준 통과 |
| 20일 수치 CatBoost | 0.570583 / 0.572562 / 0.530682 | 0.557942 ± 0.023629 | 0.513840 / 0.496519 / 0.544473 | 0.262426 / 0.256095 | Brier 미달, 실험적 확률 |
| 60일 수치 Logistic Regression | 0.600529 / 0.675964 / 0.649510 | 0.642001 ± 0.038274 | 0.487659 / 0.511560 / 0.697227 | 0.307916 / 0.275151 | Brier 미달, 실험적 확률 |

평균 BA가 가장 높았던 뉴스 분류 후보도 채택 조건을 만족하지 못했다. 아래는 지평별 뉴스 후보의 최고 BA이며, 결과를 본 뒤 기준을 낮추지 않았다.

| 지평·뉴스 후보 | BA 평균 ± fold 표준편차 | 평균 Brier / Train 비율 Brier | 채택하지 않은 근거 |
|---|---:|---|---|
| 5일 LightGBM·7일 창 | 0.546384 ± 0.033368 | 0.260460 / 0.250897 | Brier 기준 미달 |
| 20일 CatBoost·7일 창 | 0.563224 ± 0.027942 | 0.256613 / 0.256095 | Brier 기준 미달 |
| 60일 Logistic Regression·14일 창 | 0.625720 ± 0.021051 | 0.332559 / 0.275151 | Brier 미달, 수치 전용 평균 BA보다 낮음 |

같은 CatBoost 회귀(C1)에서도 수치 전용 A와 뉴스 추가 B를 비교했다. 뉴스 창은 각 지평의 **C1 Validation 평균 RMSE가 가장 낮은 창**이며, Test로 고르지 않았다. 모든 오차는 같은 평가 날짜의 로그수익률 기준이다.

| 지평 | 비교 뉴스 창 | Validation 수치 RMSE | Validation 뉴스 RMSE | 이미 본 2024~2025 수치 RMSE | 이미 본 2024~2025 뉴스 RMSE |
|---|---|---:|---:|---:|---:|
| 5일 | 1일 | 0.049840 | 0.049835 | 0.054934 | 0.054579 |
| 20일 | 14일 | 0.097173 | 0.094388 | 0.106710 | 0.102986 |
| 60일 | 30일 | 0.175376 | 0.178650 | 0.179451 | 0.170841 |

이는 뉴스 유무만 바꾼 동일 모델 비교이며 자동 채택 결과가 아니다. Validation 평균 RMSE가 조금 줄어도 기존 가격 기준 대비 fold별 상대 개선·최악 fold·방향 조건을 모두 만족해야 한다. 평균 RMSE의 비율과 fold별 상대 개선율의 평균도 서로 다른 집계다.

가격 기준을 제외한 후보 중 평균 상대 RMSE 개선이 가장 컸던 결과는 다음과 같다. 양수는 기존 가격 기준보다 오차가 작다는 뜻이며, **fold별 상대 개선율의 산술평균**이다.

| 지평·최상위 가격 후보 | 2021 / 2022 / 2023 개선 | 평균 ± fold 표준편차 | 가격 기준 유지 이유 |
|---|---|---|---|
| 5일 수치 Logistic/Ridge C2 | +0.7982% / −0.1638% / −0.7577% | −0.0411% ± 0.7852%p | 평균 1% 미달, 개선 fold 1개 |
| 20일 뉴스 14일 CatBoost C3 | +0.2500% / −6.3757% / +2.3404% | −1.2618% ± 4.5504%p | 평균 1%·최악 fold 기준 미달 |
| 60일 수치 Logistic/Ridge C3 | +3.4211% / −5.0365% / +11.8523% | +3.4123% ± 8.4444%p | 2022년이 −5% 미만 |

선택된 분류기의 **이미 본 2024~2025년 기술 평가**는 아래와 같다. 분류 평가는 5일 496건, 20일 483건, 60일 443건이다. Test를 보고 모델·threshold·가중치를 바꾸지 않았다.

| 지평 | Accuracy | BA | F1 | AUC | Brier / Train 비율 Brier | 항상 상승 Accuracy |
|---|---:|---:|---:|---:|---|---:|
| 5일 | 0.477823 | 0.475542 | 0.512241 | 0.464659 | 0.256612 / 0.251074 | 0.550403 |
| 20일 | 0.633540 | 0.597189 | 0.718601 | 0.627065 | 0.227808 / 0.250144 | 0.598344 |
| 60일 | 0.534989 | 0.512273 | 0.652027 | 0.555054 | 0.258575 / 0.244463 | 0.790068 |

5일의 `validated`는 위 Validation 기준 통과만 뜻한다. Brier 통과 폭도 약 0.000028로 작고, 기술 평가 BA는 0.5 아래로 내려갔다. 20일의 기술 평가가 좋아도 Validation 미달 상태를 승격하지 않는다. 3개 fold·단일 seed·중첩 수익률이고 같은 Validation에서 후보를 선택했으므로 독립 outer 검증이 아니다. 표의 표준편차는 연도별 산포이지 신뢰구간이 아니며, 이번 실험은 의존성을 고려한 신뢰구간이나 통계적 우월성을 제시하지 않는다.

전체 지표는 [fold_metrics](../data/processed/news_intelligence_2026-09-19/experiment_v1/fold_metrics.parquet)(477행), [validation_summary](../data/processed/news_intelligence_2026-09-19/experiment_v1/validation_summary.parquet)(159행), [seen_test_metrics](../data/processed/news_intelligence_2026-09-19/experiment_v1/seen_test_metrics.parquet)(159행)에 있다. Precision·Recall·confusion matrix도 fold 지표에 포함된다. [OOF 예측](../data/processed/news_intelligence_2026-09-19/experiment_v1/oof_predictions.parquet) 74,148행과 [기술 평가 예측](../data/processed/news_intelligence_2026-09-19/experiment_v1/seen_test_predictions.parquet) 52,688행을 대조해 후보 간 동일 날짜, 중복 키 0건, 학습 정답일 < 평가 시작일, 예측일 < 정답일을 확인했다. 새 보고서는 만들지 않았다.

### 서빙·검증 범위

- 선택 run ID는 `20260919T084409Z-467e2dc1`. 원본 [news_intelligence_v1.joblib](../model_artifacts/news_intelligence_v1.joblib)의 36개 모델 쌍은 보존하고 [news_intelligence_v1_serving.joblib](../model_artifacts/news_intelligence_v1_serving.joblib)에 선택된 3쌍만 담았다. 두 artifact의 선택과 Parquet에서 재계산한 선택이 일치한다. 기존 `production_dlinear_60.pt`는 그대로 사용한다.
- Mac의 Torch·LightGBM 동시 로드 segfault를 재현한 뒤 공통 `coffee_service/__init__.py`에서 `os.environ.setdefault("OMP_NUM_THREADS", "1")`을 적용했다. 사용자 override는 유지하며, 새 실험은 import 전에 셸에서 `export OMP_NUM_THREADS=1`을 권장한다. Reviewer는 두 import 순서와 전체 원본 artifact 로드를 검증했다.
- Coordinator 확인: 독립 Reviewer 두 명 모두 BLOCKER 0·HIGH 0. 실제 PostgreSQL을 포함한 최종 Python 테스트 **89개 통과, 67.54초, third-party 경고 3개**, Node 테스트 3개와 production build 통과. `uv pip check`는 165개 패키지 호환이다. Documenter는 이 테스트를 재실행하지 않고 저장 결과·코드·명령 인자를 대조했다.
- Coordinator 확인: native·Docker backfill 성공, 가격 2,892행·예측 1,509행·현재 intelligence 모델 3개·소스 상태 16개. 실제 API 신호와 Vue 표시를 확인했으며, 현재 60일 모델 metadata에는 기존 노트북 Test RMSE `0.16794`가 유지된다. 이 수치는 뉴스 모델의 새 성과가 아니다.
- Coordinator 추가 확인: Docker `live` incremental(종료일 2025-12-31)은 1,509건 모두 상승 확률이 있고 뉴스 영향·건수·수집 시각은 NULL이다. 2026년 실제 수집 시각을 과거에 사용할 수 없기 때문이다. 이어 `historical` incremental도 성공해 현재 3개 지평·1,509개 예측을 유지했고, historical 예측 전체 행 fingerprint는 재실행 전후 동일했다.
- 위 모드 전환 후 DB 합계는 가격 2,892행, 예측 3,018행(현재 historical 1,509 + 비활성 live 1,509), 기사 7,548행, 뉴스 일별 집계 34,752행(2,896일×6개 창×2개 모드)이다. 모드별 기록을 중복 오류로 해석하지 않는다.
- Coordinator 최종 확인: Docker `incremental --skip-ingestion --ingest-news`가 실제 18건을 수집한 뒤 가격 2,892행·예측 1,509행 적재에 성공했다. 뉴스 소스 상태는 `success`, 2025년 말 공개·수정 시각에 보이는 7,319행(7,548−229)이다. 전체 기사 7,548행과 historical 예측 snapshot은 다시 동일했고, 두 모드 예측 총 3,018행의 자연키도 모두 고유했다. native 실제 뉴스 incremental에서도 기사 7,548행이 유지됐다.
- Coordinator 최종 브라우저 확인: 실제 DB의 5·20·60일 예측 가격·단순수익률·별도 상승 확률·가격 방향·뉴스·검증 상태·모드 표시, 차트 지평 버튼, 최신 예측 표의 모델 버전 상세를 확인했다. 기존 60일 Test RMSE `0.16794`도 표시된다. 320px에서 `document.scrollWidth=320`, 표 오른쪽 좌표 295로 가로 넘침이 없고 콘솔 오류·경고는 모두 0건이다.
- Coordinator 최종 재시작 확인: 최종 API image가 healthy이며, 뉴스 작업 후 API·Nginx를 명시적으로 재시작해도 전체 가격 2,892행·현재 예측 1,509행·현재 모델 3개·뉴스 summary 응답 JSON이 재시작 전후 정확히 같고 health도 정상이다. 이번 로컬 runtime 점검은 완료했다. 미사용 미래 구간 검증, CI·클라우드 배포·정기 실행은 완료 범위에 포함하지 않는다.

재현 명령은 [README](../README.md#news-intelligence)를 따른다. 진단·실험은 기존 출력 디렉터리와 artifact를 덮어쓰지 않고 새 이름으로 실행한다. 기존 Notebook 전체 재실행이나 원본 데이터·학습 artifact 교체는 이번 문서 작업에 포함하지 않았다.

## 2026-09-19 — Docker Compose 로컬 재현

- 실행 환경: Colima의 Linux ARM64, Docker Engine 29.5.2·Compose 5.5.1, Python 3.12.14, PostgreSQL 17.11. 기존 native venv나 호스트 PostgreSQL 없이 컨테이너 서비스를 실행했다.
- 저장된 Parquet와 기존 60일 DLinear artifact를 입력으로 Compose의 PostgreSQL·API·Nginx 웹·파이프라인 작업을 재현했다. 새 프로젝트 volume에서 backfill과 같은 플래그의 incremental 두 번이 성공했고, 가격 2,892행·예측 1,509행·현재 모델 2건·소스 상태 15건이 저장됐다. 중복 가격·예측은 없었다. 외부 API 재수집과 모델 학습은 실행하지 않았다.
- API·웹에서 365개 가격, 3개 예측, 15개 소스와 5·20·60일 지평을 확인했다. API·웹 재시작, 같은 Docker 응답의 반복 조회, `docker compose down` 뒤 `up`(volume 유지)에서도 결과는 동일했다. native 기준과 전체 가격·예측 응답을 비교했을 때 예측 가격의 최대 차이는 `6.103515625e-05`였고, 상대 `1e-6`·절대 `1e-5` 허용 범위에서 일치했다. 브라우저 fetch override로 주입한 503 상태 뒤 실제 API 복구도 확인했다.
- 검증: native 전체 테스트 44개 통과(55.28초, deprecation 경고 2개), 컨테이너 seed 코드의 native 회귀 2개 통과, 프런트엔드 Node 테스트 2개는 native에서 통과했고 Vite production build는 native와 컨테이너에서 각각 통과했다. Reviewer 최종 pass는 0개 지적이었다. seed는 누락 파일만 임시 파일로 원자 복사하고 기존 작업 파일을 보존하도록 수정한 뒤 재검증했다. 원본 15개 Parquet와 artifact의 해시는 실행 전후 동일했다.
- 제한: 저장 응답 기반 수집 검증만 했고 라이브 외부 API는 다시 호출하지 않았다. Linux amd64, 병렬 파이프라인 작업, base image tag와 전이 의존성의 완전한 고정은 검증하지 않았다. API image에는 pandas·torch·yfinance·scikit-learn을 넣지 않았고, pipeline image에는 notebook·EDA 의존성을 넣지 않았지만 컨테이너 이미지를 공개 배포한 것은 아니다.
- 다음: CI/CD는 별도 작업으로 GitHub Actions 테스트·이미지 빌드, GHCR, Azure 배포의 범위를 설계한다. 구현은 아직 시작하지 않았다.

## 2026-09-19 — 예측 방향과 가격 오차 시각화

- 실제·예측 비교 아래에 기준일 종가 대비 등락률, 방향 일치율, 가격 MAE와 부호 있는 오차 막대를 추가했다. 방향 불일치는 빨간 막대로 구분하며 목표일 선택으로 기준가·등락률·예측/실제 가격을 확인한다.
- 현재 표시된 최대 180개 평가점으로 계산한다. 상승·하락·보합을 별도로 비교하며 실제값이 없는 예측은 제외한다. 조회된 365개 가격에 기준일이 없으면 방향 평가에서 제외하고 제외 건수를 표시한다. 가격 오차는 예측−실제이며 MAE와 기존 로그수익률 Test RMSE는 다른 지표다.
- 테스트 2개·빌드 통과. 독립 코드 검토에서 조치할 버그 없음. 저장 자료를 UTF-8 임시 PostgreSQL에 재적재해 5/20/60일 전환, 날짜 선택, 320px 가로 넘침·콘솔 오류 없음을 확인했다. 60일 최근 180건 방향 일치는 122건(67.78%), 가격 MAE는 43.83¢/lb였으며 전체 Test 성과로 해석하지 않는다. API·학습·원본 자료 변경은 없다.

## 2026-09-19 — 모노톤 데이터 콘솔로 정리

- 로고·브랜드 바·아이콘을 제거하고 종목명과 기준일 중심의 헤더로 줄였다. 패널·탭·상태는 흑백으로 통일하고 차트·가격 등락·실패 강조에만 색을 남겼다.
- 프런트엔드 테스트와 빌드 통과. 브라우저에서 일반 요소의 계산된 색상이 무채색임을 확인했고, 1440px 화면·320/390px 가로 넘침 없음·지평 전환·콘솔 오류 없음을 검증했다. 320px 고가/저가 숫자 간격도 조정했다. API·모델·데이터 변경은 없다.

## 2026-09-19 — 대시보드 정보 밀도 개선

- 큰 소개 영역을 줄이고 지표 띠, 가격·예측 차트와 요약의 2열 배치, 소스 상태 3열 목록으로 재구성했다. 차트 수치 눈금·정답 날짜·예측 기준일을 표시하고 모바일에서는 단일 열로 전환한다. 기존 API와 모델·데이터는 변경하지 않았다.
- 조회·오류 재시도·로딩·빈 데이터 상태, 키보드 포커스·본문 바로가기·동작 줄이기를 반영했다. 신규 의존성 없이 Vue와 CSS를 사용했다.
- `npm test`와 `npm run build` 통과. 브라우저에서 1440px 화면, 390·320px 모바일 가로 넘침 없음, 지평 전환과 15개 소스·3개 예측, 오류/빈 응답/로딩 후 실제 API 복구를 확인했다. 독립 검토에서 BLOCKER/HIGH/MEDIUM은 없었고, 재시도 후 포커스 복구 지적도 수정했다.

## 2026-09-19 — Local E2E 구현 및 검증

- 저장된 `data/processed/2014-07-01_2025-12-31/` 소스를 사용해 `coffee_service.pipeline backfill --skip-ingestion --end 2025-12-31`을 완료했다. PostgreSQL에는 가격 2,892행, 예측 1,509행, 소스 상태 15건, 현재 모델 2건이 저장됐다. 외부 API 재수집은 하지 않았다.
- API는 가격·예측·현재 모델·파이프라인·소스 상태를 조회하며, Vue 대시보드는 5·20·60일 전환과 세 카드·유한한 차트를 표시한다. 브라우저에서 500px 너비의 가로 넘침과 콘솔 오류 없이 확인했다.
- 5·20일은 Persistence, 60일은 DLinear artifact를 사용한다. 별도 `/tmp` 재학습에서 2,011 학습행과 weight·scaler가 artifact와 정확히 일치했다. 기존 443개 Test 사례의 DLinear RMSE는 `0.16794263786669386`, MAE는 `0.14040843700016423`이었다. 이 결과만으로 60일 우위를 확정하지 않는다.
- 검증: PostgreSQL 17.11 전용 DB를 연결한 `tests/test_local_e2e.py`·`tests/test_postgres_e2e.py` 13개(26.30초), 모델·추론 8개, 환경 19개를 통과했다. 최종 HIGH 수정 전 전체 36개, 수정 후 해당 회귀 13개를 각각 실행했으며 최종 40개를 한 번에 실행한 기록은 아니다. `uv pip check`는 165개 패키지 호환, `npm install`, `npm run build`도 성공했다. Reviewer 결과는 BLOCKER 0, HIGH 0이다.
- 수집은 7일 겹침 구간을 원자적으로 병합한다. 가격과 필수 ALFRED 3개가 실패하면 중단하고, NASA·COT 등 보조 소스 실패는 상태에 기록한다. 커피 최신일 기준 거시 자료 14일 초과는 서빙을 막는다. NYSE와 두 예외를 합친 거래일 달력, Yahoo·NASA 과거 자료 수정 한계는 유지한다.
- 다음: 다른 시점의 검증으로 DLinear 후보를 재평가한다. CI·클라우드 배포와 정기 수집은 아직 구현·검증하지 않았다.

## 2026-09-14 README에 분석 흐름과 결과 정리

- 03-1의 EDA·피처 그룹 비교부터 03-2의 개별 모델·두 앙상블 실험까지 README에 정리했다. 수집 기간과 노트북 링크를 갱신하고, 기상 버퍼·발표 시점·방향 기저율·가격 단위 오차에서 배운 점을 humanizer 스킬로 다듬었다.
- 저장된 노트북 출력에서 STL·피처 상관·미래 가격 비교·50:50 앙상블 차트 4장을 `docs/images/`로 추출했다. 개별 모델과 가중평균 요약표를 원 출력에 대조하고, 로컬 링크 15개·Markdown 표 6개·이미지 4개의 렌더 구조와 한글 표시를 확인했다. 이미지 바이트는 원 출력과 같고 노트북·데이터·환경은 변경하지 않았다. 모델 재학습은 하지 않았다.
- 다음: 시간 순서 검증에서 후보의 개선이 유지되는지 확인한 뒤 서빙 모델을 정한다.

## 2026-09-14 — RMSE·방향 정확도 1위의 가중평균 비교

03-2의 기존 33셀·출력은 보존하고 하단에 6셀을 추가했다. 50% 필터 없이 Validation RMSE 최저와 방향 정확도 최고 모델을 고른 뒤, RMSE 모델 비중 1/0.75/0.5/0.25/0을 같은 Validation·Test 날짜에서 비교했다. 방향 정확도는 결합한 예측의 부호로 재계산했다. 모델·그룹·설정 식별자와 지표 30행을 출력했으며, 두 역할이 같은 경우에는 단독 결과만 한 번 표시한다.

아래는 Validation RMSE가 가장 낮았던 비중과 그 비중의 Test 결과다. 가중치를 자동 채택하거나 기존 모델 선택을 바꾸지는 않았다.

| 지평 | RMSE 최저 / 방향 정확도 최고 모델 | RMSE 모델 비중 | 검증 RMSE | Test RMSE | Test 방향 정확도 |
|---|---|---:|---:|---:|---:|
| 5일 | 지수평활 / 전체 피처 CatBoost(150) | 1.00 | 0.04707 | 0.05444 | 51.00% |
| 20일 | 지수평활 / 전체 피처 CatBoost(150) | 0.50 | 0.09033 | 0.11209 | 53.00% |
| 60일 | 가격+거시 DLinear(50 epoch) / 가격+거시 LSTM(100 epoch) | 0.75 | 0.14611 | 0.17572 | 55.76% |

5일은 Validation에서 단독 모델이 가장 좋았다. 20일은 수익률 크기를 줄여 RMSE가 소폭 개선됐고 방향 정확도는 CatBoost 단독과 같았다. 60일의 75:25는 Test에서 DLinear 단독(RMSE 0.16794·방향 60.05%)보다 두 지표 모두 나빠졌다.

검증: 외부 Python 3.12 새 커널에서 필요한 기존 모델 예측을 재현하고 추가 코드 3셀을 실행했다(약 169초). 원 단독 지표가 저장 표의 소수점 5자리와 일치했고, 근소한 지수평활·Naive 순위는 반올림 전 값으로 확인했다. 공통 날짜·단독 가중치·방향 재계산·동일 후보·동률 처리·기존 선택 보존을 검사했다. 기존 33셀·출력 불변, notebook 구조·문법·오류 없음과 표의 수치를 확인했다. 앞부분 전체 재실행이나 패키지·데이터 변경은 하지 않았다.

다음: 이번 가중치별 표를 탐색 결과로 남기고, 시간을 옮긴 검증에서 단독 모델 대비 개선이 유지되는지 확인한다. Test로 가중치를 다시 고르지 않는다.

## 2026-09-14 — 앙상블 평가의 시행착오 기록

- `troubleshooting.md`에 60일 방향 기저율 변화(상승 41.04%→79.01%), 오차 상관 증가(0.74947→0.90429)와 단독 모델 대비 악화, 20일 가격 단위 개선폭을 기록했다. 50% 필터의 통계적 의미, 상관계수와 공분산, 로그수익률과 가격 RMSE를 구분하고 시장 원인·서빙 비용을 확정하는 표현은 제외했다.
- 검증: 03-2의 저장 표와 계산 코드를 대조했다. 20일 가격 RMSE 차이 0.0718센트/파운드·개선율 0.20%, 가격 MAE 개선율 1.93%를 표에서 재계산하고 공식 자료의 지표 정의·시장 배경을 확인했다. 이번에는 문서만 변경했으며 모델 재학습·노트북 재실행·서빙 비용 측정은 하지 않았다.
- 다음: 시간 순서 검증에서 앙상블과 단독 모델을 함께 비교하고, 방향별 재현율과 개선의 지속성을 확인한 뒤 서빙 복잡도를 늘릴지 결정한다. 기존 모델·가중치 선택은 유지한다.

## 2026-09-14 — 방향 조건을 적용한 50:50 앙상블 추가

03-2의 기존 26셀과 저장된 출력을 그대로 두고, 하단에 설명·코드·해석 7셀을 추가했다. Validation 방향 정확도 50% 이상인 모델·피처 조합 중 RMSE가 낮은 두 개를 골라 로그수익률을 반씩 평균했다. 개별 설정과 기존 선택은 유지했고 Test로 재선택하지 않았다.

| 지평 | 앙상블 구성 | 검증 RMSE | Test RMSE | Test 방향 정확도 | Test Naive 대비 RMSE 개선 |
|---|---|---:|---:|---:|---:|
| 5일 | CatBoost 가격+거시 / CatBoost 전체 | 0.04750 | 0.05473 | 51.41% | -0.53% |
| 20일 | ARIMA / CatBoost 전체 | 0.09034 | 0.11209 | 53.00% | +0.81% |
| 60일 | DLinear 가격+거시 / LightGBM 전체 | 0.13872 | 0.17236 | 62.30% | +7.21% |

5일은 구성원보다 조금 좋아도 Naive를 넘지 못했다. 20일은 두 구간에서 소폭 개선됐지만 연도별 Test 개선율은 +3.33%/-0.85%로 달랐다. 60일은 Validation에서 구성원 최저보다 5.76% 좋아졌으나 Test에서는 DLinear 단독보다 RMSE가 2.63% 높았다. 방향 정확도는 DLinear의 60.05%에서 62.30%로 올라갔다.

Test의 항상 상승 정확도는 5/20/60일 순서로 54.82%/59.83%/79.01%로 앙상블보다 높았다. 50% 통과만으로 방향 예측력을 확정하지 않는다. 60일 비중첩 시작점별 Naive 대비 개선도 -14.51%/+18.17%/+4.04%로 달랐다(8/8/7행). 현재 수정 자료·이미 확인한 Test·단일 seed라는 조건을 유지한다.

검증: 외부 Python 3.12 새 커널에서 필요한 기존 모델과 예측 배열을 재현하고 추가 코드 5셀을 실행했다. 이번에는 앞부분 전체를 재실행해 덮어쓰지 않았다. 필요한 단독 지표가 기존 저장 표의 소수점 5자리 정밀도에서 일치하고, 표시 반올림이 선택 경계·순위를 바꾸지 않는 것도 확인했다. 50% 경계·후보 부족·공통 날짜·50:50 평균·가격 환산·지표 재계산·선택과 원 예측 보존 검사를 통과했다. 기존 26셀 내용·출력 불변, notebook 구조·문법·오류 없음, 추가 한글 차트 2개를 확인했다.

다음 검토: 단독 모델을 자동 교체하지 않고 앙상블을 별도 후보로 남긴다. 가중치나 결합 방식을 확대하려면 시간 순서의 내부 검증 규칙부터 정한다.

## 2026-09-13 — 03-2 신경망 epoch를 50/100으로 확대

- DLinear·LSTM·Attention-LSTM의 epoch 후보를 30/60에서 50/100으로 바꿨다. Validation RMSE로 선택한 epoch를 Test 재학습에 적용했다. 데이터·분할·seed·모델 구조와 트리 150/300 설정은 유지했다.
- 5·20일의 선택은 지수평활로 그대로다. 60일은 가격+거시 DLinear의 50 epoch가 선택됐다. 학습량 증가가 일관된 성능 개선으로 이어지지는 않았다.

| 지평 | 선택 모델 / 그룹 | epoch | 검증 RMSE | Test RMSE | Test Naive 대비 개선 |
|---|---|---:|---:|---:|---:|
| 5일 | 지수평활 / 가격 단변량 | — | 0.04707 | 0.05444 | -0.00008% |
| 20일 | 지수평활 / 가격 단변량 | — | 0.09102 | 0.11300 | +0.00006% |
| 60일 | DLinear / 가격+거시 | 50 | 0.14719 | 0.16794 | +9.59% |

60일 DLinear의 Validation 개선율은 7.88%다. 이전 30 epoch 결과(검증 7.76%, Test 9.63%)와 거의 같다. Test 연도별 개선율은 2024년 2.94%, 2025년 20.71%, 비중첩 시작점 0/20/40은 -6.02%/+19.56%/+8.43%였다(8/8/7행). 서빙 검토 후보는 유지하되 안정적인 우위를 확정하지 않는다.

60일 Attention-LSTM은 세 그룹 모두 일반 LSTM보다 Validation RMSE가 낮았다. Test에서는 가격+거시·가격+기후가 11.06%·0.49% 개선됐고 전체 피처는 22.11% 악화됐다. 세 Attention 모델 모두 Test에서 Naive를 넘지 못했다. 전체 피처 LSTM(100 epoch)의 Test RMSE가 0.16648로 가장 낮았지만 Validation에서 Naive보다 4.17% 나빴으므로 선택을 바꾸지 않았다. 이미 본 Test·단일 seed·현재 수정 자료라는 조건을 유지한다.

검증: 외부 Python 3.12.14 새 커널에서 코드 16셀을 전체 실행했다(약 22분). 거래일·시퀀스·미래 변경 불변성·공통 평가 행·표 지표·통계 모델 상태 갱신·선택 고정 검사를 통과했다. 저장된 출력과 해석을 갱신하고 notebook 구조·문법·오류 없음 및 한글 차트 4개를 확인했다. 패키지 설치나 데이터 재수집은 하지 않았다.

## 2026-09-13 — 03-2 최초 비교 (30/60 epoch)

- `03_2_horizon_model_validation.ipynb`를 독립 실행 노트북으로 추가했다. 31개 후보와 지평별 Naive 3개를 비교하고, 외생변수 후보 27개는 설정 두 개씩 Validation에서 선택했다. 기존 03·04·학부 원본과 데이터는 유지했다.
- Train 2015~2021, Validation 2022~2023, Test 2024~2025를 유지했다. 신경망 입력은 60거래일이며 같은 유효 날짜를 모든 후보에 적용했다. 첫 학습일은 2015-01-13, Train은 5/20/60일 순서로 1,566/1,551/1,510행이다. Validation 496/481/441행, Test 498/483/443행은 그대로다.
- 일반 LSTM과 Attention-LSTM은 은닉 64·2층·동일 출력층으로 맞췄다. 학부 코드의 Entmax·gate를 유지하고 정적 피처 분기·복합 손실·Test 기반 학습률 조정은 제거했다. 두 모델의 본체·출력층 초기 가중치가 일치하는 것도 확인했다. Entmax 1.3만 추가 설치했다.

선택은 Validation RMSE로 고정했다. Test를 보고 다른 모델로 교체하지 않았다.

| 지평 | 선택 모델 / 그룹 | 검증 RMSE | Test RMSE | 검증 Naive 대비 개선 | Test Naive 대비 개선 |
|---|---|---:|---:|---:|---:|
| 5일 | 지수평활 / 가격 단변량 | 0.04707 | 0.05444 | +0.00025% | -0.00008% |
| 20일 | 지수평활 / 가격 단변량 | 0.09102 | 0.11300 | +0.00003% | +0.00006% |
| 60일 | DLinear / 가격+거시 | 0.14739 | 0.16787 | +7.76% | +9.63% |

5·20일 지수평활은 가격 유지와 사실상 같다. 20일 가격+거시 LightGBM은 Test에서 7.22% 개선됐지만 Validation에서는 Naive보다 11.97% 나빠 선택하지 않았다. 60일 전체 피처 LSTM도 Test 개선율 10.25%만 보고 선택하지 않았다(Validation 5.21% 악화).

20일 기후 추가는 CatBoost·LightGBM의 Validation RMSE를 각각 6.60%·6.20% 줄였지만 Test에서는 6.11%·4.36% 늘렸다. 60일 DLinear에 기후를 추가하면 Validation·Test 모두 악화됐다. 기후 그룹의 추가 효과가 일관되게 유지되지는 않았다.

60일 전체 피처에서 Attention은 일반 LSTM보다 Validation RMSE가 6.15% 낮았지만 Test에서는 21.12% 높았다. 가격+거시에서는 Test가 13.29% 개선됐으나 두 모델 모두 Naive를 넘지 못했다. 이번 설정에서 Attention의 안정적인 우위를 확인하지 못했다.

60일 DLinear의 예측 기준일 연도별 Test 개선율은 2024년 2.94%, 2025년 20.84%였다. 비중첩 시작점 0/20/40에서는 -7.94%/+20.01%/+8.24%로 달랐고 표본은 각각 8/8/7개였다. 서빙 검토 후보는 가격+거시 DLinear로 남기되 안정적인 우위를 확정하지 않는다. 이미 본 Test·단일 seed·현재 Yahoo/NASA 수정 자료라는 조건도 유지한다.

검증: 외부 Python 3.12.14의 새 커널에서 코드 16셀을 순차 실행해 저장했다(해석 Markdown 포함 총 26셀). 날짜·타깃·60일 시퀀스·미래 변경 불변성·공통 평가 행·표 지표 일치·통계 모델 상태 갱신·선택 고정 검사와 한글 차트 4개 확인을 마쳤다. 기존 03의 데이터·통계 모델 정의 8개도 일치했다. 환경 테스트 19개, 154개 패키지 호환성, requirements 오프라인 설치 dry-run을 통과했다.

첫 실행의 마지막 차트 검사에서 float32 가중치 합의 약 1.25e-6 차이가 있었다. 값을 보정하지 않고 60개 float32 값의 합산 정밀도에 맞춰 허용오차를 수정한 뒤 전체 재실행을 완료했다. 모델 설정과 지표는 유지됐다. 기존 03·04·학부 원본은 변경하지 않았으며 데이터 재수집·모델 파일 배포·FastAPI 구현은 하지 않았다.

## 2026-09-13 — requirements 의존성 복원

- `requirements.txt`를 실제 외부 CPython 3.12 환경의 33개 패키지 버전으로 다시 작성했다. Apple 내부 wheel 경로를 제거하고 수집·Parquet·EDA·모델·notebook·테스트 의존성을 복원했다. PyCaret `timeseries` extra와 04의 Torch·XGBoost도 포함한다.
- 검증: 활성 코드·테스트의 직접 import와 고정 버전 대조, extra를 포함한 전이 의존성 호환성 검사 통과. 현재 환경의 `uv pip check`는 153개 패키지 호환, `uv pip install --dry-run --offline --strict`는 변경 없음이었다. 기존 환경 테스트 17개 통과, 로컬 HTTP 테스트 2개 제외. 새 환경 설치나 패키지 변경·모델 재학습은 하지 않았다.

## 2026-09-13 — 버퍼 백필과 03 전면 재작성

03을 백업 없이 덮어쓰고 목표·정렬·Train EDA·그룹 선택·모델 비교의 다섯 장으로 정리했다. 기존 04와 데이터·학부 원본·`.env`는 유지했다. 현재 Yahoo·NASA 자료를 쓰는 연구용 비교는 사용자 확인을 받고 진행했다.

- 수집 기본값을 `2014-07-01~2025-12-31`로 바꾸고 `02_backfill_10y.py`를 실행했다. 새 폴더에 Core-4 12개와 ALFRED 최초 공개값 3개 Parquet를 저장했다. 기존 파일의 스키마는 유지하고 ALFRED 파일에 관측일·공개 날짜·값·계열 ID를 저장했다. DFF의 공개본 개수 제한은 연도별 요청으로 해결했다.
- 커피 원응답은 2,899행이며 OHLC 범위 이탈 455행은 원값을 유지했다. 거래일 달력의 가격 누락은 버퍼 1일·Train 3일이다. 기상 6곳은 2014-06-01~2026-01-30을 포함하며 원자료 결측 0개, 2015년 첫 학습일·Train 파생변수 결측도 0개였다. 기상 평균·중앙값 대치는 하지 않았다.
- Train은 2015~2021, Validation은 2022~2023, Test는 2024~2025로 고정했다. 5/20/60일 순서로 Train 1,633/1,618/1,577행, Validation 496/481/441행, Test 498/483/443행이다. 각 지평 안에서는 모든 모델·그룹이 같은 평가 행을 사용하며 정답 날짜가 구간 끝을 넘는 사례는 제외한다.
- 거시 입력은 ALFRED 최초 공개 BRL 환율·금리·WTI의 20거래일 변화다. 공개 날짜 다음 날부터 붙인다. 신 달러지수의 과거 소급 값과 실제 공개 일정이 미완성인 COT는 수집만 하고 이번 입력에서 제외했다. NASA 관측일+4일은 연구용 가정이며 과거 수정 전 공개본을 복원한 것은 아니다.
- 기상 편차·강수 결손을 만든 뒤 최대 변수 상관은 약 0.886이었다. 기준 0.90을 넘는 쌍이 없어 축소 후보는 생략했다. 브라질 격자 서리·가뭄×서리 6개는 Train에서 상수라 제외했고, 서리 효과를 검증했다고 해석하지 않았다.

같은 CatBoost 150개 트리·깊이 4로 비교한 결과다. **선택은 Validation에서 끝냈으며 Test 최저 그룹으로 다시 바꾸지 않았다.**

| 지평 | Validation 선택 그룹 | 검증 RMSE | 선택 그룹의 Test RMSE | Test에서 관찰한 최저 그룹 / RMSE |
|---|---|---:|---:|---|
| 5일 | 가격+거시 | 0.04770 | 0.05489 | 가격+거시 / 0.05489 |
| 20일 | 가격+거시+기후 | 0.09146 | 0.11227 | 가격+거시 / 0.10682 |
| 60일 | 가격+거시+기후 | 0.15255 | 0.18320 | 가격+거시 / 0.17979 |

20일 전체 그룹은 가격만 쓴 CatBoost보다 Validation에서 11.15% 개선됐지만 Test에서는 0.83% 악화됐다. 60일 전체 그룹은 같은 비교에서 각각 13.85%·2.25% 개선됐다. 다만 두 지평 모두 Test에서는 기후를 뺀 가격+거시가 더 나았다. 기상 편차의 추가 효과가 안정적으로 유지됐다고 결론 내리지 않는다.

모델은 Naive·지수평활·ARIMA·Ridge·CatBoost·LightGBM·DLinear 변형·LSTM을 비교했다. 머신러닝은 위에서 선택한 그룹을 공통으로 쓰고 모델별 설정 두 개를 Validation에서 비교했다. Test 전에는 2015~2023년으로 재학습했다.

| 지평 | Validation에서 고른 모델 | Test RMSE | Naive 대비 Test 개선율 |
|---|---|---:|---:|
| 5일 | 지수평활 | 0.05444 | -0.00008% |
| 20일 | 지수평활 | 0.11300 | +0.00006% |
| 60일 | LightGBM | 0.18258 | +1.71% |

5·20일 지수평활은 가격 유지와 사실상 같다. 60일 LightGBM은 2024년에는 4.40% 악화, 2025년에는 11.77% 개선됐고 비중첩 시작점별 개선율도 -29.15~+10.25%였다(7~8행). DLinear는 60일 Test RMSE 0.17493으로 가장 낮았지만 Validation에서는 Naive보다 17.21% 나빠 사전 선택되지 않았다. Test 순위로 모델을 교체하지 않는다.

정상성 진단은 가장 긴 연속 구간에서 5·20일 수익률의 ADF/KPSS가 정상성을 뒷받침하는 쪽이었지만, 60일은 ADF p=0.00817·KPSS p≤0.01로 엇갈렸다. CCF는 Train의 0~60거래일 시차 탐색으로 남겼다. 최고 상관 시차를 자동 채택하거나 인과적 전달 시간으로 설명하지 않았다.

검증: 외부 Python 3.12.14 새 커널에서 26셀(코드 17셀) 전체 실행, notebook 구조·문법·오류 출력·한글 차트 확인. 미래 값 변경 불변성, 기상 기준값 범위, 정답 경계, 공통 행, 거래일 시차·가격 결측, 고정 모수 상태 갱신, 표 지표 일치를 검사했다. 기존 테스트 19개와 설치된 153개 패키지의 `uv pip check`도 통과했다. LightGBM 4.7.0만 추가 설치하고 학습·예측을 확인했다. ARIMA는 수렴 문제를 해결한 동일 Powell 설정을 두 구간에 적용했다. 기존 requirements의 다른 미커밋 변경은 유지했다.

다음 단계: 설정 후보나 seed를 늘리기 전에 20·60일의 그룹 효과가 시간 구간을 바꿔도 유지되는지 확인한다. 신경망과 표 모델의 입력 표현 차이, 격자 서리의 한계, 과거 수정 자료·이미 본 Test·중첩 수익률을 유지해서 해석한다. 이번 결과만으로 특정 기후 피처나 모델의 안정적인 우월성을 확정하지 않는다. 04 재설계와 서비스 구현은 이번 작업에 포함하지 않았다.
