# 코드·정합성 점검 (2026-10-02)

## 범위와 방법

- 기준 코드: 로컬 `main` `2406489`. 원격 `origin/main` `9181b7c`보다 3커밋 뒤처져 있지만, 그 차이는 workflow runner label(`ubuntu-24.04`)과 문서뿐이고 Python·Vue 코드는 같다.
- 읽은 범위: `coffee_service/` 중 pipeline·refresh·selected_models·transform·features·inference·ingestion·db·api·jev·jev_store·news_incremental·news_backfill, `frontend/src/App.vue`, `compose*.yaml`, `Dockerfile`, `deploy/`, `.github/workflows/`, `configs/`, README·architecture·Context·STATUS.
- 실행한 검증
  - `pytest tests`(CI와 같은 제외 범위, 외부 venv Python 3.12): **211 passed, 6 skipped, 2 subtests passed**. skip 6건은 `COFFEE_TEST_DATABASE_URL`이 없어 건너뛴 PostgreSQL 테스트다. 경고 3건(sklearn 단일 클래스, Starlette/httpx, AnyIO)은 기존과 같다.
  - GitHub Actions 일일 배치 최근 5회 조회(`gh run list/view`, 읽기 전용).
  - NASA POWER 최근 14일 응답 1회 조회(공개 API, 키 없음).
  - `data/jev/responses.json` 재분석 건수 집계.
- 하지 않은 것: 코드 수정, PostgreSQL 통합 테스트, Docker build, VM 접속, 외부 뉴스 원천 재현, Gateway 요청.

심각도 기준: **HIGH** 운영 기능이 지금 동작하지 않음 / **MEDIUM** 특정 조건에서 잘못된 결과·진단 불가·운영 혼선 / **LOW** 잠재 위험·유지보수.

## 요약

| ID | 심각도 | 구분 | 위치 | 내용 |
|---|---|---|---|---|
| C1 | HIGH | 코드 | `news_incremental.py:133-154` | WordPress 한 곳이 실패하면 RSS·Yahoo 수집까지 중단된다. 9/26 이후 운영 뉴스 수집이 멈춰 있다 |
| C2 | MEDIUM | 코드 | `news.py:195-202`, `news_incremental.py:148` | 수집 실패의 HTTP 상태를 버려서 원인을 진단할 수 없다 |
| C3 | MEDIUM | 코드 | `refresh.py:90-113` | 가격 예측이 성공해도 뉴스가 partial이면 exit 1. 일일 workflow가 5회 연속 실패로 표시된다 |
| C4 | MEDIUM | 코드 | `pipeline.py:33-36` | 선택 모델에는 기상이 사실상 필수인데 보조 소스로 분류된다. 실패 메시지도 원인을 가린다 |
| C5 | MEDIUM | 설계 | `refresh.py:14`, `selected_models.py:22,38` | 로컬 Compose의 7일 주기 갱신이 학습 때의 일별 뉴스 feature 의미와 어긋난다 |
| C6 | LOW | 코드 | `ingestion.py:261-272` | 증분 시작일이 값이 모두 비어 있는 마지막 행 기준이라 긴 장애 뒤 결측이 남을 수 있다 |
| C7 | LOW | 코드 | `pipeline.py:179`, `db.py:525-535` | 모델 입력은 최신 분석, DB는 최초 분석을 쓴다. 모델·프롬프트 버전 필터도 없다 |
| C8 | LOW | 재현성 | `transform.py:106` | 기상 월별 기준값을 artifact에 저장하지 않고 추론할 때마다 다시 계산한다 |
| C9 | LOW | 유지보수 | `db.py:281-355` | 같은 CASE 조건이 8번 반복된다 |
| C10 | LOW | 설정 | `configs/sources.yaml:1,13,45` | 코드가 읽지 않는 키와 오래된 주석이 남아 있다 |
| C11 | LOW | 저장소 | 루트 `kernel-ipc-1`~`10` | Jupyter 커널 소켓 파일 10개가 남아 있다 |
| D1 | MEDIUM | 문서·설정 | `.env.example:15`, `README.md:247,252` | `PIPELINE_ARTIFACT`는 Compose가 쓰지 않는다. 실제 변수 `PIPELINE_MODELS_DIR`는 예시에 없다 |
| D2 | MEDIUM | 문서 | `README.md:367` | VM 초기 준비물이 `production_dlinear_60.pt`로 적혀 있다. runner는 `models/selected_news/`를 요구한다 |
| D3 | LOW | 문서 | `docs/architecture.md:409` | Compose에 없는 "DLinear 호환 경로 mount"를 설명한다 |
| D4 | LOW | 문서 | `Context.md:98` | 이미 고친 Vue 뉴스 응답 경합을 "후속 수정 필요"로 남겨 두었다 |
| D5 | LOW | 문서 | 로컬 `Context.md:27-30` | 로컬 사본은 공개 운영이 `61c4cff` 이전 모델이라고 적혀 있다. 원격 최신 문서와 다르다 |
| D6 | LOW | 문서·배포 | `compose.deploy.yaml`, `deploy/deploy.py:20,32` | 이전 GHCR(`fab81c0`)·DLinear 경로가 실제 운영 경로처럼 보인다 |

## 조치 결과 (2026-10-02, 브랜치 `feat/news-collection-resilience`)

로컬 `main`을 `origin/main`(`9181b7c`)으로 fast-forward한 뒤 C1~C5를 고쳤다. 구조·계약 결정은 [architecture 2026-10-02 절](architecture.md), 실행 결과는 [STATUS](STATUS.md) 최신 항목에 적었다. C6~C11과 D1~D6은 이번 범위에서 제외했다.

| ID | 상태 | 변경 |
|---|---|---|
| C1 | 해결 | Google News RSS만 필수 소스로 둔다. WordPress·Yahoo가 실패하면 `source_gaps`에 기록하고 `partial`로 표시한 뒤 수집 완료일을 전진시킨다. RSS가 실패하면 기존처럼 해당 구간을 재시도한다 (`news_incremental.py`) |
| C2 | 해결 | `news.SourceRequestError`가 HTTP 상태만 보존한다. 오류 문자열은 `wordpress 2026-09-27~2026-10-01: HTTP 403` 형식이며 URL·질의는 남기지 않는다 (`news.py`, `news_backfill_sources.py`, `news_incremental.py`) |
| C3 | 해결 | `refresh --once` 종료 코드를 0/3/1로 나눴다. 3은 예측 적재 성공 + 뉴스 불완전이다. runner가 3을 GitHub 경고와 exit 0으로 바꾸고, `Refresh:` 로그에 `errors`·`collection_errors`를 출력한다 (`refresh.py`, `deploy/run-daily-pipeline.sh`) |
| C4 | 해결 | `validate_selected_weather`가 비어 있는 지역명을 담아 중단한다. 입력 검사 실패만 원인을 `pipeline_runs.message`와 refresh 상태에 남긴다(`PipelineInputError`/`PipelineRunError`). NASA 수집 실패 자체는 계속 보조 실패다 (`pipeline.py`, `refresh.py`) |
| C5 | 해결 | 로컬 refresh 주기를 7일에서 1일로 바꿨다(`REFRESH_INTERVAL`). 재시작 직후 몰아서 보충하는 경우 과거 기준일에 뉴스가 반영되지 않는 한계는 README에 적었다 |

정책상 선택한 기본값:
- 보조 소스가 실패한 구간은 자동으로 재수집하지 않는다. `source_gaps`에 누적만 해 두고, 나중에 보충할지는 따로 결정한다.
- 종료 코드 3은 가격 예측이 적재됐고 뉴스 저하가 다음 실행에서 이어지는 수준일 때만 쓴다. 수치 처리 실패, 필수 RSS 수집 실패, Jev 분류 중단(`failed`·`stopped`·`budget_stop`·`cost_unknown`·`retry_exhausted`)은 1이다.
- 보조 소스는 원천 오류(`requests.RequestException`·`RuntimeError`·`ValueError`)만 누락 구간으로 처리한다. 그 밖의 예외는 코드 오류로 보고 수집 실패로 올린다.

독립 리뷰(읽기 전용)는 BLOCKER/HIGH 없이 MEDIUM 2건·LOW 2건을 지적했다. 종료 코드 3의 범위가 너무 넓은 점, 보조 소스의 광범위한 `except`, 재시도할 구간의 누락이 확정 기록에 남는 점은 반영했다. `source_gaps`에 상한이 없는 점은 하루 1건 수준이라 보류했다.

## 코드

### C1. WordPress 실패가 뉴스 수집 전체를 막음 — HIGH

- **위치**: `coffee_service/news_incremental.py:133-154`
- **발생 조건**: `news.fetch_wordpress`(Daily Coffee News)가 예외를 내는 경우. 같은 `try` 안에서 뒤따르는 RSS 쿼리와 Yahoo 수집은 실행되지 않고, `break`로 남은 구간도 모두 건너뛴다.
- **영향**: 수집 완료일(`coverage_end`)이 9월 26일에서 멈췄다. 새 기사가 선정되지 않으니 Jev가 분류할 대상도 없고, 운영 예측은 계속 `numeric_fallback`(뉴스 없는 base 모델)으로만 나간다. 이 구조에서는 뉴스 feature를 쓰는 선택 모델의 핵심 기능이 꺼진 상태다.
- **근거**
  - Actions [36869017753](https://github.com/sjinie/Coffee_Price_Prediction/actions/runs/36869017753)(10-01): `Refresh: {'status': 'partial', 'numeric': 'success', 'collection': 'failed', 'classification': 'completed'}`.
  - 원격 STATUS 09-30: VM에서 같은 코드로 `dailycoffeenews.com`이 HTTP 403을 3회 반환했고, 실패 구간의 WordPress snapshot은 0개.
  - 코드: WordPress·RSS·Yahoo 호출이 한 `try` 블록(133-146행)에 묶여 있다.
- **수정 방향**: 소스별로 `try`를 나누고 소스별 상태를 `status`에 기록한다. WordPress가 실패해도 RSS·Yahoo 결과로 선정을 계속할지, 이때 `coverage_end`를 전진시킬지(누락 소스를 `partial`로 남김)는 **정책 결정이 필요하다**. 수집 완료의 의미가 바뀌기 때문이다. 차단 우회는 하지 않는다.

### C2. 수집 실패 원인을 남기지 않음 — MEDIUM

- **위치**: `coffee_service/news.py:195-202`(`raise RuntimeError(...) from None`), `coffee_service/news_incremental.py:148`(`type(exc).__name__`만 기록)
- **영향**: 403·429·timeout을 구분할 수 없다. 원격 STATUS 09-30에도 "당시 HTTP 상태는 보존되지 않았다"고 적혀 있다. C1을 진단하려고 VM에서 따로 재현해야 했다.
- **수정 방향**: 마지막 시도의 `response.status_code`, 또는 연결 실패 여부를 예외 메시지와 `errors`에 남긴다. WordPress URL에는 secret이 없지만, 다른 소스로 넓힐 때는 `ingestion.get_response`처럼 URL을 빼고 상태 코드만 남긴다.

### C3. 일일 배치의 성공·실패 신호가 가격과 뉴스를 구분하지 못함 — MEDIUM

- **위치**: `coffee_service/refresh.py:90-93`(`numeric`·`collection`·`classification`이 모두 성공해야 success), `refresh.py:113`(`--once`는 success가 아니면 1을 반환)
- **영향**: 9/27~10/1 다섯 번 모두 실패로 끝났다. 그중 9/28은 SSH timeout으로 Python 진입 전에 실패했고, 나머지 네 번은 수치 처리와 예측 적재가 성공했다(원격 STATUS·Actions 로그). 결과적으로 "가격 예측이 실패한 날"을 알림으로 구분할 수 없다.
- **수정 방향**: 수치 처리 실패만 exit 1로 두고, 뉴스 partial은 별도 exit 코드나 `$GITHUB_STEP_SUMMARY` 경고로 분리한다. 운영 알림 정책이므로 사용자 결정이 필요하다.

### C4. 기상이 사실상 필수 소스인데 보조로 분류됨 — MEDIUM

- **위치**: `coffee_service/pipeline.py:33-36`(`REQUIRED_SOURCES`는 커피·ALFRED 3종뿐), `pipeline.py:135-146`(`validate_latest_prediction_coverage`)
- **발생 조건**: NASA 수집이 실패해 최신 기상 관측이 `WEATHER_DELAY_DAYS=4` 허용 범위를 벗어나는 경우. 선택 모델의 27개 feature 창이 불완전해지면서 최신일 예측이 만들어지지 않는다.
- **영향**: 실행 자체는 rollback/failed로 안전하게 멈춘다. 다만 메시지가 "최신 가격일 예측이 없습니다"로 나와 원인(기상)이 드러나지 않고, `source_status`에는 기상이 보조 실패로만 남는다.
- **근거**: `architecture.md` 6장도 "선택 모델은 6개 기상 파일과 완전한 수치 입력 창도 필요"라고 적고 있지만, 코드의 필수 소스 목록에는 반영되지 않았다.
- **참고**: 2026-10-02 조회 기준 NASA POWER는 9/30 자료까지 제공하고 10/1~2는 fill 값이었다(지연 약 2일). 정상 상태라면 4일 가정 안에 들어온다.
- **수정 방향**: 선택 모델 경로에서는 `weather_*`를 필수로 다루거나, `validate_macro_freshness`처럼 기상 최신성을 따로 검사해 명확한 메시지를 낸다.

### C5. 7일 주기 refresh와 일별 뉴스 feature의 의미 차이 — MEDIUM(로컬 Compose 한정)

- **위치**: `coffee_service/refresh.py:14`(`WEEK`), `coffee_service/selected_models.py:22,38`(`MAX_NEWS_DELAY=7일`, 처음 이용 가능해진 거래일에 배정)
- **발생 조건**: `compose.yaml`의 `pipeline refresh`가 7일마다 실행될 때. 일주일치 기사가 한 번에 수집·분석되므로 이용 가능 시각이 모두 실행 시점 근처로 몰린다.
- **영향**
  - 그 주의 이전 기준일들은 뉴스 0건으로 base 모델 예측이 된다.
  - 최신 기준일 하나에 여러 날의 기사가 합산된다.
  - 발생 후 7일이 넘은 기사는 빠진다.
  - 결과적으로 일별로 정렬해 학습한 뉴스 feature와 분포가 달라진다. 운영(일일 Actions)에는 해당하지 않는다.
- **수정 방향**: 로컬 Compose의 뉴스 경로는 "수집 주기 차이로 뉴스 반영이 제한된다"고 README에 명시하거나, 로컬 refresh도 일 단위로 실행한다.

### C6. 증분 시작일이 값이 모두 빈 마지막 행을 기준으로 계산됨 — LOW

- **위치**: `coffee_service/ingestion.py:261-272`(`last_saved_date` → `incremental_start`)
- **발생 조건**: NASA가 최근 1~2일을 fill 값(`-999`)으로 돌려주면 `fetch_weather`가 이를 NaN 행으로 저장한다. 다음 실행은 이 마지막 날짜에서 7일만 겹쳐 다시 받는다. NASA 지연이나 장애가 7일을 넘으면, 겹침 구간보다 앞선 NaN 행은 다시 받지 않아 결측으로 남는다.
- **영향**: 평상시(지연 약 2일)에는 다음 실행에서 채워진다. 장시간 장애 뒤에는 rolling 집계 결측이 길게 이어져 C4로 번질 수 있다.
- **수정 방향**: 기상 소스는 값이 하나라도 있는 마지막 날짜를 기준으로 증분 시작일을 계산한다.

### C7. 뉴스 분석 버전 선택이 경로마다 다름 — LOW(현재 영향 없음)

- **위치**: `coffee_service/pipeline.py:179`(`read_analyses(latest=True)`, 모델·프롬프트 필터 없음), `pipeline.py:369`(뉴스 잔차 경로는 `MODEL`/`PROMPT_VERSION`으로 필터), `coffee_service/db.py:525-535`(`ON CONFLICT DO NOTHING`, 최초 분석 유지)
- **영향**
  - 같은 `analysis_id`를 다시 분석하면 모델 입력은 최신 분석을, DB의 `jev_analyses` 화면은 최초 분석을 보여 준다.
  - 프롬프트 버전이 바뀌면 다른 의미의 분류가 같은 `news_sentiment`에 섞인다. 선택 모델은 `arabica-kc-futures-v2` 기준으로 학습했는데 manifest에는 이 버전이 기록되지 않는다.
- **근거**: 현재 분석 1,449행 중 재분석된 ID는 1건이고, 라벨·방향 차이는 0건이다. 지금은 잠재 위험이다.
- **수정 방향**: manifest에 학습 때 쓴 Jev `model`/`prompt_version`을 기록하고, 선택 모델 경로에서 같은 버전만 사용한다.

### C8. 기상 기준값을 추론 때마다 다시 계산함 — LOW

- **위치**: `coffee_service/transform.py:106`(`fit = daily.loc[2015-01-01 : train_cutoff-4일]`)
- **영향**: 월별 강수·기온 기준값은 artifact에 들어 있지 않고, 실행 시점의 소스 Parquet에서 매번 다시 구한다. NASA가 과거 값을 수정하거나 seed Parquet가 바뀌면, 같은 artifact로도 학습 때와 다른 편차 feature가 나온다. `architecture.md:152`의 "기상 기준값은 저장된 학습 cutoff"는 cutoff 날짜만 저장한다는 뜻이어서, 값까지 고정된다고 읽히기 쉽다.
- **수정 방향**: 최소한 문서에 "기준값은 실행 시 소스에서 재계산"이라고 명시한다. 재현성을 높이려면 export 시 월별 기준값을 npz에 함께 저장한다.

### C9. `upsert_predictions`의 반복 조건 — LOW

- **위치**: `coffee_service/db.py:281-355`
- **영향**: 8개 신호 열마다 같은 10줄짜리 조건을 복사했다. 열을 추가하거나 조건을 바꿀 때 한 곳만 고치면 열마다 동작이 달라진다. 동작 자체는 테스트로 확인된다.
- **수정 방향**: 조건을 한 번만 쓰도록 Python에서 SQL 문자열을 조립하거나, 행 단위 `CASE`로 줄인다. 불변 UPSERT 회귀 테스트를 유지한 채 진행한다.

### C10. 설정 파일의 사용되지 않는 키와 오래된 주석 — LOW

- `configs/sources.yaml:1`: "API 요청은 02_backfill_10y.py" → 실제 위치는 `coffee_service/ingestion.py`.
- `configs/sources.yaml:45` `nasa.buffer_days: 30`: 코드가 읽지 않는다. 수집 CLI는 30일(`ingestion.WEATHER_BUFFER_DAYS`), pipeline backfill은 90일(`pipeline.WEATHER_BUFFER_DAYS`)을 하드코딩한다.
- `configs/sources.yaml:13` `prediction.baseline_features`: 현재 27개 feature 계약과 다르고 코드가 읽지 않는다.

### C11. 루트의 Jupyter 소켓 파일 — LOW

`kernel-ipc-1`~`kernel-ipc-10` 소켓이 루트에 남아 있다. Git은 소켓을 추적하지 않지만, 커널이 종료됐다면 지워도 된다. 실행 중인 커널이 쓰는지 확인한 뒤 사용자가 정리한다.

## 문서·설정 정합성

### D1. Compose 모델 경로 변수 — MEDIUM

- `compose.yaml:79`는 `PIPELINE_MODELS_DIR`(기본 `./model_artifacts`)를 `/models`에 mount하고, 명령을 `/models/selected_news/manifest.json`으로 고정한다.
- `.env.example:15`의 `PIPELINE_ARTIFACT=./model_artifacts/production_dlinear_60.pt`는 어디서도 읽지 않는다. `README.md:247`은 기본 artifact를 `production_dlinear_60.pt`로, `README.md:252`는 `PIPELINE_ARTIFACT`를 확인하라고 안내한다. `README.md:333`만 `PIPELINE_MODELS_DIR`를 언급한다.
- 수정: `.env.example`과 README 247·252를 `PIPELINE_MODELS_DIR`와 `selected_news/` 21개 파일 기준으로 바꾼다.

### D2. VM 초기 준비물 — MEDIUM

- `README.md:367`은 `/srv/coffee/pipeline/models`에 `production_dlinear_60.pt`를 최초 제공한다고 적는다.
- `deploy/run-daily-pipeline.sh:76,89`는 `models/selected_news/`를 rsync하고 그 manifest로 실행한다. 새 VM을 README대로 준비하면 일일 배치가 rsync에서 실패한다.
- 수정: README를 `selected_news/`(manifest + 20개 구성원 파일) 기준으로 바꾼다.

### D3. 없는 bind mount 설명 — LOW

`docs/architecture.md:409`의 "기존 DLinear 파일을 호환 경로에 read-only bind mount"에 해당하는 mount가 `compose.yaml`에 없다. 실제 mount는 `/seed`·`/seed-jev`·`/models` 세 개다.

### D4. 이미 고친 버그가 미해결로 남음 — LOW

`Context.md:98`은 Vue 뉴스 새로고침의 응답 순서 경합을 "후속 버그 수정 필요"로 적고 있다. 현재 코드는 `frontend/src/App.vue:36,108-120`의 `newsRequest` 순번으로 늦게 온 응답을 버리고, `frontend/tests/dashboard.test.js:296` 테스트가 이를 검증한다. 해결 완료로 갱신한다.

### D5. 로컬 문서가 원격보다 오래됨 — LOW

로컬 `Context.md:30`은 "공개 운영은 기존 main 61c4cff의 모델"이라고 적혀 있다. 원격 `origin/main`(`9181b7c`)의 Context는 "공개 운영은 main `2406489`의 선택 모델"(9/30 재배포)로 갱신됐다. 로컬을 원격에 맞춘(fast-forward) 뒤 문서를 고친다. 원격 Context에도 `Context.md:27`의 "PR #4 … 183 passed"가 현재 상태 문단에 그대로 남아 있어, 최신 검증 수치(217)와 섞여 보인다.

### D6. 이전 GHCR 배포 경로 — LOW

`compose.deploy.yaml`과 `deploy/deploy.py:20,32`는 `fab81c0` 게시 이미지와 `production_dlinear_60.pt`(이전 모델)를 쓴다. collect도 차단되어 있다. 실제 운영은 `deploy/compose.azure.yaml`의 VM 소스 build와 일일 Actions다. README·architecture에 구분이 적혀 있긴 하지만 파일 이름만 보면 운영 경로로 오해하기 쉽다. 새 GHCR 게시로 교체하기 전까지 파일 상단에 "이전 모델 복원 검증용"이라고 한 줄 표시하는 정도면 충분하다.

## 확인했고 문제가 없는 부분

다시 점검하지 않아도 되도록 남긴다.

- **시점 정렬**
  - `transform.align_available`은 backward as-of join이며, 공개 전 관측이 붙으면 예외를 낸다. ALFRED는 공개일 다음 날부터, NASA는 관측일 + 4일부터 쓴다.
  - `selected_models.daily_news`의 live 경로는 기준 거래일 23:00 UTC 이후 이용 가능해진 기사를 다음 거래일에 배정한다. 실행 시각 이후 기사는 배정하지 않고(`position >= len`), 7일 넘게 늦은 소급 분석은 제외한다. 2022~2025년 소급 분석은 이 규칙으로 운영 입력에서 빠진다.
- **예측 불변성**: `upsert_predictions`는 같은 키·같은 `target_date`일 때 실제값만 보완한다. 원격 STATUS 09-30에서 기존 2,616건이 그대로인 것을 확인했다.
- **artifact 검증**: `SelectedBundle`이 SHA-256, feature 순서·shape, 표준화 통계, 뉴스 열 비표준화(mean 0, scale 1), cutoff를 검사한다.
- **보안**
  - API는 GET만 받고, Azure에서는 SELECT 전용 역할을 쓴다. schema는 pipeline 역할로 따로 초기화한다.
  - FRED 키는 예외 메시지에서 가리고, Gateway 키는 pipeline 컨테이너에만 주입한다.
  - `start-azure.sh`는 비밀번호를 hex 64자로 검증한 뒤 SQL에 넣는다.
- **프론트엔드**: 선택 모델이 활성일 때 예전 잔차 보정 snapshot을 숨긴다(`App.vue`의 `!hasConditionalModels` 조건). 뉴스 응답 경합도 처리되어 있다.

## 우선순위 제안

1. **C1 + C2**: 뉴스 수집을 소스별로 분리하고 실패 상태를 기록한다. `coverage_end` 정책은 먼저 결정해야 한다.
2. **C3**: 일일 배치의 실패 신호를 가격과 뉴스로 분리한다(알림 정책 결정 필요).
3. **D1·D2**: 새 환경에서 바로 실패하는 안내를 바로잡는다.
4. **C4·C6**: 기상 최신성 검사와 증분 기준을 보완한다.
5. 나머지 LOW 항목은 관련 작업 때 함께 처리한다.

## 미검증

- PostgreSQL 통합 테스트 6건(이번 실행에서 skip), Docker 이미지 build, VM의 실제 파일·DB 상태.
- C1의 WordPress 403은 원격 STATUS 기록과 Actions 요약 로그에 근거한다. 이번에 직접 재현하지 않았다.
- C5·C6은 코드 경로를 분석한 결과다. 해당 조건을 실제로 실행해 보지는 않았다.
