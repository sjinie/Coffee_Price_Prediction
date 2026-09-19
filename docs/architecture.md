# 커피 선물 예측 설계

오늘까지의 가격·거시·기상으로 5·20·60거래일 뒤 종가를 예측한다. 모델은 미래 로그수익률을 학습하고, 화면에는 `오늘 종가 × exp(예측 수익률)`로 환산한다. 가격 유지 모델의 예측 수익률은 0이다.

## 데이터와 기간

수집은 `02_backfill_10y.py` 하나에서 처리한다. 일별 자료라 Parquet면 충분하고, SQL로 묶어 볼 때는 DuckDB를 사용할 수 있다. 새 데이터는 `data/processed/2014-07-01_2025-12-31/`에 저장한다. 기존 데이터와 학부 원본은 유지한다.

| 구간 | 예측 기준일 | 역할 |
|---|---|---|
| 버퍼 | 2014-07~2014-12 | 과거 집계·입력 창 계산 |
| Train | 2015-01-01~2021-12-31 | EDA·기상 기준값·피처 후보·모델 학습 |
| Validation | 2022-01-01~2023-12-31 | 그룹·모델 설정 선택 |
| Test | 2024-01-01~2025-12-31 | 선택을 고정한 뒤 최종 비교 |

각 사례의 정답 날짜도 해당 구간 안에서 끝나야 한다. 60일 예측이면 경계 근처의 60거래일을 학습 사례에서 제외한다. Test 전에 2015~2023년으로 재학습하고 전처리 통계도 그 범위에서 다시 계산한다. 피처 목록은 Validation 선택 후 고정한다. 이미 살펴본 2024~2025년을 완전히 새로운 미사용 검증 기간이라고 부르지 않는다.

## 시점과 결측을 다루는 방식

- 커피 거래일을 먼저 만들고 가격 누락 칸을 유지한다. 주말·휴장일은 만들지 않으며, `dropna` 뒤에 시차를 계산하지 않는다. 현재 달력은 NYSE를 기준으로 커피가 거래된 2018-12-05·2025-01-09를 보완했다.
- 예측은 거래일 종가를 확인한 뒤 UTC 23시를 기준으로 한다. Yahoo `KC=F`의 Close를 그대로 사용하며, 공식 정산가와 동일하다고 확정하지 않는다. OHLC 범위 이탈도 임의 보정하지 않는다.
- 거시는 ALFRED의 최초 공개값(`output_type=4`)을 사용한다. 공개 날짜만 있으므로 다음 날부터 과거 방향으로 결합한다. 휴일·공개 중단 중에는 이미 알려진 마지막 값을 유지한다. 수정본으로 과거 값을 갈아 끼우지 않는다.
- 환율은 `DEXBZUS`의 BRL/USD, 금리는 `DFF`, 유가는 `DCOILWTICO`다. 환율은 H.10 뉴욕 정오 기준으로 Yahoo 환율 종가와 다르다. 20거래일 환율 로그변화, 금리·유가 차이를 입력한다. 음수가 있는 WTI에는 로그를 쓰지 않는다.
- NASA는 UTC 일별 관측일+4달력일을 이용 가능 시점으로 가정한다. 월별 기준값은 해당 학습 구간에서만 계산한다. 관측일 자체를 발표일로 취급하지 않지만, 이 가정이 당시 공개본을 복원하지는 않는다.

Yahoo와 NASA는 현재 제공 자료를 사용하는 **연구용 백테스트**로 진행하기로 했다. 시간 분할·정답·전처리 누수는 막되, 원천 자료의 과거 수정 이력은 해결하지 못했다. 금리·환율·유가의 ALFRED 최초 공개값과 구분한다.

## 피처는 정보원별로 비교

가격 피처는 1·5·20거래일 수익률과 20일 변동성이다. 기상은 기존 여섯 좌표를 사용한다. 좌표 목록은 [regions.yaml](../configs/regions.yaml)에 있고 국가 전체의 대표 관측점으로 가정하지 않는다.

각 지점에서 30일 강수·기온의 월별 편차와 90일 강수 결손을 만든다. 브라질 세 곳에는 7일 격자 서리 강도와 직전 가뭄×서리를 추가한다. 서리 강도는 `max(0, -Tmin)`의 합이며 실제 서리 관측은 아니다. 가뭄은 강수 부족량으로, SPI/SPEI와 구분한다. 월 주기 sin·cos도 기후 그룹에 포함하므로 그룹 효과에는 계절 변수의 효과도 섞인다.

초기 집계 결측은 실제 과거 버퍼로 해결한다. 전체 평균·중앙값 대치는 하지 않는다. Train에서 상수인 변수는 제외한다. Train의 변수 간 절대 상관이 0.90 이상인 기상 쌍은 타깃 상관이 약한 쪽을 제외한 후보를 만든다. 높은 상관이나 VIF만으로 제거를 확정하지 않는다.

각 지평에서 같은 CatBoost 설정·날짜로 다음 네 구성을 비교하고, 중복 쌍이 있으면 축소 후보를 추가한다.

1. 가격
2. 가격+거시
3. 가격+기후
4. 가격+거시+기후

기상 축소 후보는 실제로 변수가 제거될 때만 비교한다. 남긴 열의 순서를 원본과 같게 유지해 열 순서 변경을 피처 효과로 혼동하지 않는다. 이번 월별 편차 입력의 최대 상관은 약 0.886으로, 0.90 기준을 넘는 쌍이 없어 축소 후보는 생략했다.

전체에서 그룹을 하나씩 빼는 비교는 2·3번과 같다. 같은 계산을 다시 실행하지 않는다. 그룹 비교는 깊이 4·트리 150개·학습률 0.03·L2 규제 5·seed 42로 통일하고, Validation RMSE가 낮은 그룹을 선택한다. 동률이면 피처가 적은 쪽을 쓴다. 한 변수의 단독 실패로 다른 변수를 버리지 않는다.

달러지수 `DTWEXBGS`의 2019년 이전 값은 도입 후 소급 자료라 이번 입력에서 제외한다. COT도 수집은 유지하지만 실제 공개 지연 일정을 연속 확인하지 못해 별도 추가 비교로 남긴다.

## EDA와 모델 비교

03은 다섯 장으로 구성한다. 목표 → 정렬 → Train 진단 → 그룹 선택 → 모델 비교 순서다. STL은 Train 월평균 가격의 설명용 분해이며 모델 입력에 넣지 않는다. ACF/PACF, ADF/KPSS, 분포·박스플롯·regplot을 함께 읽는다. 정상성이나 차분 필요성을 검정 하나로 증명했다고 쓰지 않는다.

CCF는 입력을 거래일 기준 0~60일 지연한 뒤 미래 수익률과의 Pearson·Spearman 관계를 탐색한다. 지평별로 같은 유효 행을 쓰며 최고 시차를 자동 채택하지 않는다. 계절성과 중첩 때문에 큰 상관을 충격 도착 시간이나 미래 예측력으로 해석할 수 없다.

모델 비교는 Validation에서 고른 그룹을 공통으로 사용한다. Ridge의 alpha 10/100, CatBoost·LightGBM의 트리 150/300, 신경망의 epoch 30/60을 비교한다. LSTM은 은닉 16, 입력 창은 30거래일이다. DLinear는 분해 선형 구조를 단일 로그수익률 출력으로 바꾼 변형이다. 설정 후보는 각 모델 두 개로 제한한다.

PyCaret 4.0.0a8은 상태공간 지수평활·ARIMA 모델 생성에 쓴다. 자체 점예측 루프로 평가하며, 평가 중 모수는 고정하고 새 관측으로 상태만 갱신한다. 결측 가격은 상태공간 필터가 관측 갱신을 건너뛰게 하며 대치하지 않는다. Naive·통계 모델·머신러닝 모두 같은 예측일에서 평가한다. 신경망은 3차원 입력 창, 표 모델은 현재 행의 과거 집계라는 차이를 남긴다.

주 지표는 로그수익률 RMSE, 보조 지표는 MAE·방향 정확도다. 가격 환산 차트에는 정답 날짜를 쓴다. 0 예측은 보합이므로 Naive 방향 정확도를 50% 기준선으로 해석하지 않는다. 연도별·비중첩 시작점별 결과도 확인하되, 작은 개선을 통계적 우월성으로 부르지 않는다.

## 03-2: 피처 조합과 Attention-LSTM 재검증

03에서 본 후보를 `03_2_horizon_model_validation.ipynb`에서 비교한다. 기존 03의 상위 모델만 추린 실험은 아니다. Train 2015~2021, Validation 2022~2023, Test 2024~2025의 분할은 유지한다. 이미 본 Test를 참고해 후보를 정했으므로 새 독립 검증으로 해석하지 않는다.

| 지평 | 피처 그룹 | 그룹별 모델 | 가격 단변량 모델 |
|---|---|---|---|
| 5·20일 | 가격+거시 / 전체 | CatBoost·LightGBM·Attention-LSTM | ARIMA·지수평활 |
| 60일 | 가격+거시 / 가격+기후 / 전체 | CatBoost·LightGBM·DLinear·LSTM·Attention-LSTM | — |

31개 후보와 지평별 Naive 3개를 비교한다. 가격+거시는 7개, 가격+기후는 24개, 전체는 27개다. 가격만 쓰는 ARIMA·지수평활을 피처 그룹마다 중복 계산하지 않는다.

신경망 입력은 모두 `(사례 수, 60거래일, 피처 수)`다. 트리는 현재 행의 과거 집계 피처를 사용한다. 전체 피처의 60일 창이 유효한 날짜를 모든 모델에 공통 적용한다. 버퍼 가격 누락 때문에 첫 학습 사례는 2015-01-13이 되며, 이를 대치하지 않는다. 03과 달리 입력 창과 LSTM 용량도 바뀌므로 두 노트북의 지표 차이를 attention 효과로 설명하지 않는다.

일반 LSTM과 Attention-LSTM은 은닉 64·2층·dropout 0.1·출력층 64→64→32→1로 맞춘다. Attention-LSTM은 학부 코드의 Entmax와 gate를 유지해 전체 시점의 문맥과 마지막 은닉 상태를 섞는다. 정적 피처 분기를 없애고 지평별 로그수익률 하나를 출력한다. Attention 가중치는 시간 위치에 대한 참고값이며 변수 중요도나 인과관계가 아니다.

신경망은 MSE·AdamW·학습률 0.001·weight decay 0.01·batch 64·gradient clipping 1을 사용한다. 50/100 epoch를 같은 학습 과정에서 평가한다. 트리 모델은 03과 같은 설정에서 150/300개를 비교한다. CPU·seed 42로 실행하고 조기 종료와 Test 기반 학습률 조정은 하지 않는다.

각 모델·그룹의 설정과 지평별 후보는 Validation RMSE로 선택한다. 정확히 동률이면 Naive, 적은 피처, 작은 설정 순이다. Test 전 2015~2023년으로 재학습하며 전처리 통계만 다시 계산한다. Test에서 순위가 달라져도 선택을 바꾸지 않는다. 그룹 비교는 조합별 설정 선택까지 포함한 결과이며 피처만의 순수 효과로 단정하지 않는다.

전체와 부분 그룹, 60일의 일반 LSTM과 Attention-LSTM을 각각 비교한다. 이번 서빙 검토 후보는 60일 가격+거시 DLinear(50 epoch)다. 5·20일에서 선택된 지수평활은 가격 유지와 사실상 같았다. 연도별·비중첩 결과도 함께 보고 실제 배포 모델을 결정하며, 상세 결과는 [STATUS](STATUS.md)에 기록한다.

03-2 하단에는 별도의 앙상블 실험을 둔다. Validation 방향 정확도가 50% 이상인 후보 중 로그수익률 RMSE가 낮은 두 개를 선택하고 예측 수익률을 50:50으로 평균한다. 후보는 모델과 피처 그룹의 조합이며, 같은 아키텍처도 허용한다. 기존에 고른 트리 수·epoch를 유지하고 Test에서 구성원이나 가중치를 변경하지 않는다. 조건에 맞는 후보가 두 개 미만이면 선택을 중단한다.

50%는 후보 필터이며 통계적 우월성의 증명이 아니다. 항상 상승·항상 하락 방향 기준과 가격 단위 MAE·RMSE도 함께 표시한다. 모든 가격 선은 정답 날짜에 놓는다. 기존 단독 모델 셀·출력·선택은 보존하며, 앙상블 결과로 자동 교체하지 않는다. 이 50:50 실험에서는 가중치 탐색과 부호/크기 결합·스위칭을 하지 않는다.

그다음 별도 절에서 Validation RMSE 최저 모델과 방향 정확도 최고 모델을 기계적으로 고른다. 50% 필터 없이 기존 설정이 선택된 전체 후보를 비교하며, 두 역할이 같으면 단독 결과만 남긴다. 방향 정확도 동률은 낮은 RMSE와 기존 순위 규칙으로 처리한다. RMSE 모델의 비중을 1/0.75/0.5/0.25/0으로 두고 두 예측 로그수익률을 평균한다. 같은 평가 날짜에서 RMSE·MAE·방향 정확도를 다시 계산해 Validation·Test 표를 모두 출력한다. Test로 조합·가중치를 선택하거나 기존 모델을 자동 교체하지 않으며, 추가 후보 탐색이나 Stacking은 하지 않는다.

## 실행

외부 uv Python 3.12 환경에서 `python data_code/02_backfill_10y.py`를 실행한 뒤 03을 Run All 한다. 기본 수집 기간은 `configs/sources.yaml`을 읽는다. 03-2는 저장된 Parquet로 독립 실행하며 03의 실행 상태가 필요 없다. 04는 이전 실험으로 보존한다. 실행 결과와 다음 작업은 [STATUS](STATUS.md)에 기록한다.

참고: [ALFRED 최초 공개값](https://fred.stlouisfed.org/docs/api/fred/series_observations.html#output_type), [Fed 달러지수 개편](https://www.federalreserve.gov/econres/notes/feds-notes/revisions-to-the-federal-reserve-dollar-indexes-20190115.html), [NASA 수정 안내](https://power.larc.nasa.gov/docs/tutorials/), [Farmdoc 가뭄·서리 분석](https://farmdocdaily.illinois.edu/2023/12/the-weather-risk-premium-in-coffee-futures-prices.html), [DLinear 원 구현](https://github.com/cure-lab/LTSF-Linear).

## Local E2E 서빙 구조

저장된 Parquet를 입력으로 `coffee_service.pipeline`이 PostgreSQL에 가격·모델·예측·파이프라인 실행 상태·소스 상태를 저장한다. `--skip-ingestion`은 이미 저장된 소스를 지정한 기준일에서 자른 뒤 적재할 때 사용한다. 수집 경로는 소스별 7일 겹침 데이터를 원자적으로 병합한다. 가격과 ALFRED의 BRL/USD·금리·WTI는 필수이며, 필수 수집 실패나 커피 최신일 대비 14일을 넘긴 거시 자료는 적재를 중단한다. 기상·COT 등의 보조 수집 실패는 소스 상태에 남긴다.

`predictions`의 자연키는 `(model_id, origin_date, horizon)`이다. UPSERT는 같은 target date일 때만 실제값을 보완하므로 이미 발행한 예측의 목표일·예측값을 바꾸지 않는다. 실제값은 저장된 `target_date`와 `prices.date`를 조인해 채운다. 파이프라인의 가격·예측 적재는 한 트랜잭션으로 롤백하며, 수집 파일 상태인 `source_status`는 별도 commit으로 남긴다.

현재 서빙 모델은 5·20일 가격 유지와 60일 가격+거시 DLinear이다. 60일 artifact는 노트북의 후보 설정을 재현한 것이며, 기존 Test의 개선은 시간 구간과 비중첩 시작점에 따라 달랐다. 그러므로 모델 선택을 안정적인 성능 우위로 해석하지 않는다.

FastAPI는 PostgreSQL의 읽기 전용 조회 경로(`/health`, 가격, 예측, 모델, 파이프라인·소스 상태)를 제공한다. Vue 화면은 이 API에서 5·20·60일을 선택해 카드·차트·상태를 표시한다.

### Docker Compose 경계와 시작 순서

Compose는 `postgres`, `api`, `web`, profile 작업인 `pipeline` 서비스로 구성한다. PostgreSQL healthcheck가 먼저 통과하고, API 컨테이너가 `python -m coffee_service.db`로 스키마를 생성한 뒤 Uvicorn을 시작한다. 별도 migration version이나 Alembic은 사용하지 않는다. 웹은 Nginx가 런타임 `API_UPSTREAM=http://api:8000`을 대상으로 `/api/`와 `/health`를 프록시한다.

API와 pipeline의 DB 연결은 `PGHOST`·`PGPORT`·`PGDATABASE`·`PGUSER`·`PGPASSWORD`를 런타임에 받는다. 호스트 `DATABASE_URL`은 Compose에 전달하지 않는다. `POSTGRES_PASSWORD`와 PostgreSQL 변수는 로컬 `.env.docker` 또는 셸 환경변수로 설정한다. 웹·API 포트는 기본적으로 각각 8080·8001이며, host loopback에만 바인딩한다.

pipeline의 `/seed` Parquet와 artifact는 read-only bind mount다. PostgreSQL과 `/data`는 각각 named volume을 쓴다. entrypoint는 `/seed`의 파일이 `/data/sources`에 없을 때만 임시 파일로 복사한 다음 rename한다. 따라서 중간 실패·빈 원본·부분 원본은 다음 실행에서 재시도할 수 있고, 기존 작업 파일과 같은 이름의 원본 수정본은 자동 반영하지 않는다. 이 경계로 적재 작업이 입력 원본이나 artifact를 수정하지 않도록 했다.

CI, 클라우드 배포, 정기 수집·복구·모니터링은 아직 구현하지 않았다.

<a id="news-intelligence-contract"></a>

## News Intelligence 데이터·예측 계약

이 절은 2026-09-19에 추가한 뉴스 실험·서빙 경로다. 앞의 03-1·03-2는 기존 노트북 설계와 결과로 보존한다. 구현은 `coffee_service/news.py`(수집·집계), `intelligence.py`(분류·회귀·선택·추론), `experiments.py`(시간 순서 검증), `pipeline.py`(적재)로 나눈다. 현재는 세 지평 모두 기존 가격 모델을 유지하고 수치 피처 기반 상승 확률만 별도로 제공한다. 수치와 실행 증거는 [STATUS](STATUS.md#news-intelligence-v1)에 기록한다.

### 메타데이터·중복·이용 가능 시점

Daily Coffee News WordPress의 공개 posts 경로에서 ID·제목·URL·공개/수정 시각만 요청한다. [이용약관](https://dailycoffeenews.com/terms-of-service/)의 개인·비상업적 복사 제한을 확인했으며, 개인 연구용 메타데이터만 보관하고 본문 저장·기사 API 제공·원문 재배포는 하지 않는다. 유료 소스나 LLM 호출은 사용하지 않는다.

| 자료 | 계약 |
|---|---|
| 기사 Parquet | `source_dir/news/articles.parquet` 또는 `--news-path`. `article_id`, `source`, `title`, 빈 `summary`, `url`, `language`, `rights`, UTC `published_at`·`modified_at`·`collected_at`·`available_at`, 규칙 결과와 `analysis_version` |
| 기사 식별·보존 | URL의 추적 파라미터·fragment·끝 슬래시를 정리한 뒤 SHA-256을 ID로 사용한다. 같은 URL은 최초 수집 버전을 유지하고, 정규화 제목이 같으며 이용 가능 시각이 24시간 이내인 기사도 중복 제거한다. 원본 제목을 다시 HTML 파싱하지 않는다. |
| 시간 계약 | `published_at <= collected_at`, `available_at = max(published_at, modified_at)`. 예측일 UTC 23시까지 공개되고 이용 가능해진 기사만 집계한다. 조회 시 timezone 없는 값·범위 밖 점수·잘못된 URL/ID를 거부한다. |
| 수집 범위 | Parquet 속성의 `coverage_start`, `coverage_end`, `last_collected_at`으로 연속 성공 구간을 기록한다. 페이지 총수·중복·누락을 검사하고 성공한 전체 결과만 임시 파일 후 rename으로 저장한다. 증분은 마지막 공개일/coverage 끝을 기준으로 7일 겹쳐 조회한다. |
| DB | `news_articles`는 `article_id` 충돌 시 최초 행을 보존한다. `news_daily_features`는 `(as_of_date, window_days, availability_mode)`별 JSONB 집계다. 원본 기사 API는 없고 `/api/v1/news/summary`만 집계를 반환한다. |

`title-rules-v2`는 영어 제목의 커피 관련성(0/1), 주제, 강세/약세/중립, 규칙 신뢰도를 산출한다. 날씨·생산·공급·재고·수출·환율·수요·물류·정책·기타를 구분하고, 공급 감소와 수요 감소처럼 가격 영향이 다른 표현을 나눈다. 부정·상충 표현은 보수적으로 처리한다. 이는 제목 규칙의 출력이며 감성 모델의 확률·확정적 가격 영향이 아니다. 읽을 때 파생 필드만 현 규칙으로 재계산하고 원본 메타데이터는 보존한다.

집계는 `(as_of - window, as_of]`의 공개 시각을 사용한다. 창별 전체/관련 건수, 강세·약세 비율, 평균/가중 영향, 시간 감쇠, 규칙 신뢰도 합, 주제별 건수·평균 영향의 28개 열을 만든다. 부호는 강세 +1·약세 −1·중립 0, 시간 가중치는 `exp(-age_days/window)`, 가중 영향은 `sum(부호×신뢰도×시간 가중치)/sum(시간 가중치)`다. 전체 6개 창은 168개 열이며, 분모와 기사 범위를 임의로 바꾸지 않는다.

### historical·live와 0·결측의 구분

- `historical`은 공개·수정 시각으로 과거를 재생한다. 수집 시각 제한을 풀기 때문에 나중에 확보한 제목을 이용하는 회고적 연구이며, 당시 최초 공개본을 복원하지 못한다.
- pipeline 기본값 `live`는 `collected_at <= min(현재 시각, 예측일 UTC 23시)`도 요구한다. 최초 수집 전 날짜에는 뉴스 표시값을 NULL로 두고 뉴스 모델 대신 수치 분류기·기존 가격으로 대체한다. 모드별 모델 ID는 `intelligence-h{horizon}-{run_id}-{live|historical}`로 분리한다. 뉴스 summary API의 기본 조회 모드는 `historical`이므로 live 조회는 `availability_mode=live`를 명시한다.
- 수집 범위를 확인한 정상 빈 창은 건수·영향 0이다. 파일 없음·수집 실패·요청 구간을 덮지 못한 coverage는 이용 불가다. 이때 뉴스 영향·건수·수집 시각은 NULL, 수치 분류기를 쓸 수 있으면 `fallback`, 확률도 계산할 수 없으면 `unavailable`로 표시한다. 뉴스 장애는 필수 가격·ALFRED 소스의 성공을 막지 않는다.
- 회귀 학습은 정확한 수익률 0도 포함한다. 분류 학습·지표는 실제 수익률 0을 제외하며 확률의 의미는 `P(UP | non-FLAT)`다. 분류 지표의 기준은 `p >= 0.5`지만, C2의 방향은 `sign(p-0.5)`이므로 정확히 0.5면 수익률 0이다. 진단의 10bp 보합 기준은 분류 라벨·서빙 방향에 적용하지 않는다.
- `final_direction`은 최종 가격 예측 수익률의 부호로 정한다. 5·20일 Persistence는 FLAT이므로 별도 `P(up)`이 0.5보다 높거나 낮아도 가격 방향은 보합이다. 결측 수치 피처는 임의 대치하지 않는다.

### 학습과 고정 결합

수치 입력은 `PRODUCTION_FEATURES`의 가격 4개(1/5/20일 수익률·20일 변동성)와 거시 3개(BRL/USD·금리·유가의 20일 변화)다. 60거래일 입력 이력이 모두 유효한 날짜를 공통 기준으로 삼되, 분류기·표 회귀는 현재 행의 7개 집계 피처를 받는다. 뉴스 모델은 지평별 3개 창(5일: 1/3/7일, 20일: 7/14/30일, 60일: 14/30/60일) 중 하나의 28개 열을 더한다. 뉴스가 없는 날도 0으로 유지해 수치/뉴스/기존 가격 모델을 같은 날짜로 비교한다.

모델마다 분류·회귀를 한 쌍으로 학습한다. Logistic Regression(`max_iter=1000`)/Ridge(`alpha=100`)는 각 Train에서 StandardScaler를 fit한다. CatBoost는 150 trees·depth 4·learning rate 0.03·L2 5, LightGBM은 150 trees·15 leaves·max depth 4·learning rate 0.03이다. seed 42와 단일 thread를 사용하며 추가 하이퍼파라미터 탐색은 하지 않는다. 학습 타깃이 단일 클래스면 상수 분류기, 회귀 타깃이 상수면 평균 회귀기로 처리한다.

Train 시작은 2015-01-01로 고정하고 정답 날짜가 2020/2021/2022년 말을 넘지 않도록 purge해 다음 2021/2022/2023년을 검증한다. 평가 정답도 해당 연도 안에서 끝나야 한다. 60일 DLinear 기준의 scaler·가중치도 fold Train만으로 다시 fit한다. Validation 선택 후 2023년 말까지 재학습하며, 이미 본 2024~2025년은 기술 평가로 남긴다.

| 가격 후보 | 최종 로그수익률 |
|---|---|
| C1 | `r`(해당 회귀 원값) |
| C2 | `abs(r) * sign(p - 0.5)` |
| C3 | `0.5*r + 0.5*σ_train*(2*p-1) + 0.1*σ_train*news_impact` |

`p`는 같은 모델 쌍의 상승 확률, `σ_train`은 해당 Train 타깃의 표준편차다. 수치 전용의 `news_impact`는 0이다. 세 식의 가중치는 고정이며 학습된 meta-model·OOF fit·Test fit을 추가하지 않는다. OOF 예측은 검증 결과 기록에만 쓴다.

<a id="news-intelligence-selection"></a>

### 채택 기준과 서빙 결과

3개 fold가 모두 유효해야 하며, 회귀·분류를 각각 선택한다. 상대 RMSE 개선은 `1 - 후보 RMSE / 같은 fold의 기존 가격 기준 RMSE`다. 아래 평균은 fold의 산술평균이며, 뉴스 후보는 기준 통과에 더해 **동일 모델·동일 C번호의 수치 후보**보다 평균과 2개 이상 fold에서 엄격히 좋아야 한다.

| 대상 | 모두 만족해야 하는 조건 |
|---|---|
| 가격 회귀 | 평균 상대 RMSE 개선 ≥1%, 3개 중 2개 이상 개선, 최악 fold ≥−5% |
| 5·20일 가격 회귀 추가 조건 | 평균 방향 BA ≥0.52, 2개 이상 fold 방향 BA >0.5. 실제 보합은 BA에서 제외하고 예측 보합은 상승/하락 어느 쪽도 맞힌 것으로 세지 않는다. |
| 방향 분류 | 평균 BA ≥0.52, 2개 이상 fold에서 Train 다수 클래스 BA 초과, 평균 Brier ≤Train 상승 비율을 상수 확률로 쓴 기준 |

기준을 통과한 후보 중 회귀는 평균 상대 개선, 분류는 평균 BA가 높은 것을 선택한다. 회귀 후보가 없으면 기존 가격 모델을 유지하고, 분류 후보가 없으면 수치 전용 최고 평균 BA를 `experimental`로 표시한다. `validated`는 이 Validation 기준 통과이며 미사용 미래 성과 보장이 아니다. 현재 선택은 가격 세 지평 모두 `base`, 분류는 수치 CatBoost(5일 validated·20일 experimental), 수치 Logistic Regression(60일 experimental)이다. 뉴스 피처는 채택되지 않았다.

서빙 artifact는 선택·수치 대체 경로에 필요한 모델 쌍만 저장한다. 현재 `news_intelligence_v1_serving.joblib`의 3쌍은 원본 `news_intelligence_v1.joblib`의 36쌍과 같은 선택·run ID를 유지하며, 기존 DLinear artifact도 변경하지 않는다. 새 artifact는 기존 파일을 덮어쓰지 않는다. 모드와 run ID가 다른 예측은 별도 자연키로 저장하고, 재실행은 이미 발행된 가격·방향·확률을 바꾸지 않는다. 이전 예측의 신호 필드가 전부 비어 있는 경우만 목표일·가격·수익률이 같을 때 한 번 보완한다.

pipeline의 `--skip-ingestion`은 수치 수집만 생략한다. `--ingest-news`는 독립된 선택 플래그이며, 없으면 저장된 뉴스만 읽는다. `--intelligence-artifact`를 지정해야 새 신호를 적재한다. Docker는 `PIPELINE_MODELS_DIR`를 `/models`에 읽기 전용 마운트하고 `/seed/news/*.parquet`를 기존 소스와 함께 누락된 작업 파일에만 복사한다. native 새 실험은 import 전에 `OMP_NUM_THREADS=1`을 명시한다. 패키지의 기본값은 `setdefault`라 기존 사용자 값을 덮어쓰지 않는다.

API·Vue는 최종 가격·수익률·방향과 별도 상승 확률, 뉴스 영향·관련 기사 수·수집 시각, 상태·모드·버전을 제공한다. 뉴스 표시 창은 5/20/60일 지평별 7/30/60일이다. 60일 현재 모델 metadata의 Test RMSE는 기존 DLinear 노트북 지표를 유지하므로, 새 분류기나 뉴스 입력의 성과로 해석하지 않는다.
