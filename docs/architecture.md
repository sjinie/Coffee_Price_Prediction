# Coffee Price Prediction Architecture

## 2026-09-28 — 사용자 확정 모델 운영 적용

- 5일 **LightGBM300 + DLinear10**, 20일 **LightGBM100 + XGBoost100**의 가격 50:50 평균, 60일 **DLinear30**을 사용한다. 모두 가격·기후·거시 27개 + 조건부 뉴스 feature이며 Naive는 구성원에 없다.
- `03_9_selected_models_serving.ipynb`와 `python -m data_code.export_selected_models`가 03_8의 최종 가중치·표준화 통계를 `model_artifacts/selected_news/manifest.json` 및 구성원 파일로 내보낸다. 학습 정답 cutoff는 2025-12-31이며 재학습·추가 모델 탐색은 없다. artifact는 Git에 포함하지 않는다.
- 기사 점수는 유일 최댓값 방향 × relevance × confidence, 일별 합에 tanh를 적용한다. 뉴스 없음/일별 점수0이면 동일 설정의 기본 feature 모델로 복귀한다. 실제 서비스는 분석이 이용 가능해진 첫 거래일 UTC23시 기준으로 반영한다. 7일 넘게 지연된 소급 분석은 제외하고 과거 기사 사후분류를 오늘의 신규 신호로 투입하지 않는다. 별도 잔차 보정은 중복 적용하지 않는다.
- 기본 pipeline artifact와 Compose/일일 runner 경로를 선택 manifest로 연결했다. 구 artifact를 명시한 기존 연구 경로는 보존한다. 2026년 이후 예측은 새 모델 ID로 추가하며 기존 예측은 삭제하지 않는다. 새 clone에서는 연구 실행 산출물 또는 내보낸 artifact 21개 파일과 10개 원천 Parquet가 필요하다.

> 작업 브랜치·검증용 화면은 위의 선택 모델을 사용한다. 공개 Azure 운영은 main `61c4cff`의 기존 모델이며 전환은 별도 작업이다. 기존 GHCR digest 배포 경로는 `fab81c0` 게시 이미지를 참조하므로 세 상태를 구분한다.
> 이 문서는 **현재 구현**, **기존 연구·평가 계약**, **후속 배포·확장 목표**를 구분한다. 목표로 적힌 기능을 구현 완료로 해석하지 않는다.
>
> 설치·실행 명령은 [README](../README.md), 최신 상태는 [Context](../Context.md), 실행 증거·실험 결과는 [STATUS](STATUS.md), 장애 해결 과정은 [Troubleshooting](troubleshooting.md), 에이전트 작업·Git 정책은 [AGENTS](../AGENTS.md)에서 관리한다.

## 목차

1. [시스템 범위와 현재·목표 구조](#system-scope)
2. [모듈 경계와 실행 흐름](#modules)
3. [데이터·시간·피처 계약](#data-contract)
4. [모델·추론·평가 계약](#model-contract)
5. [뉴스 분석과 확장 경계](#news-intelligence-contract)
6. [저장·정합성·실패 처리](#persistence)
7. [API·프론트엔드 계약](#serving)
8. [실행 환경·컨테이너 경계](#runtime)
9. [CI·이미지 게시·배포·보안](#delivery)
10. [현재 한계와 다음 단계의 검증 조건](#limitations)

<a id="system-scope"></a>

## 1. 시스템 범위와 현재·목표 구조

외부 데이터를 수집·전처리하고 피처 생성·모델 추론·DB 적재를 수행한 뒤 FastAPI와 Vue로 제공하는 DA/DE 포트폴리오다. 노트북은 연구 기록으로 보존하고 서비스는 Python 모듈로 실행한다. 지평은 **5·20·60거래일**이며, 일별·주별 자료를 다루는 배치 구조다. 실시간 스트리밍을 구현한 것은 아니다.

| 구분 | 현재 구현·확인 범위 | 후속 목표 |
|---|---|---|
| 처리 | 소스별 수집·Parquet, 가격·거시 피처, 저장 모델 추론·적재 | 빈 환경의 데이터·artifact 초기화와 복원 검증 |
| 뉴스 | Yahoo·Google News 등의 제목/짧은 설명, Jev 분류·캐시, 조건부 뉴스 feature | 전향적 효과·분류 품질 검증 |
| 실행 | PostgreSQL·FastAPI·Vue·pipeline의 native/Docker E2E, Azure 소스 build 운영 | 새 GHCR digest의 Azure 복원 E2E |
| 자동화 | GitHub Actions CI·GHCR 게시, 운영 일일 배치, 로컬 주간 refresh | 뉴스 접근 제한 해소·장애 복원 검증 |

GHCR 게시 [실행 35455393542](https://github.com/sjinie/Coffee_Price_Prediction/actions/runs/35455393542)는 게시 성공의 근거다. Azure 실행·데이터 기반 전체 E2E의 근거는 아니며, 후속 실행 증거는 STATUS에서 관리한다.

### 현재 데이터·조회 흐름

```text
수치 API → 수집·Parquet → 가격·기후·거시 피처 → 모델 추론 ─┐
                                                        │
뉴스 API → 제목·metadata → Jev → 조건부 뉴스 feature ──────┤
                                                        ▼
                                                    PostgreSQL
                                                        ▲
브라우저의 Vue → Nginx 프록시 → FastAPI ────────── 조회 ───┘
```

선택 모델은 가격·기후·거시 27개와 조건부 뉴스 1개 feature를 사용한다. COT는 입력에서 제외한다. 아래 7개 feature/Persistence와 제목 규칙 경로는 기존 artifact 호환·연구용이며 공개 운영의 현재 모델과 작업 브랜치의 선택 모델을 구분한다.

### 목표 배포 — 현재 구현과 구분

```text
GitHub Actions → 테스트·빌드 → GHCR → Azure Linux VM
                                         └─ Docker Compose
                                            ├─ web: Vue 정적 파일·API 프록시
                                            ├─ api: 저장 결과 조회
                                            ├─ pipeline: 수집·처리·추론·적재
                                            ├─ llm: 뉴스 해석 응답
                                            └─ postgres: 영속 데이터
```

단일 VM에서 역할을 분리한다. pipeline이 LLM을 호출하고 결과를 저장하며, Ollama가 직접 수집·적재하는 구조는 아니다. CI/CD는 코드 배포 흐름이고 데이터 처리의 직렬 단계가 아니다.

<a id="modules"></a>

## 2. 모듈 경계와 실행 흐름

| 위치 | 현재 책임 |
|---|---|
| [`data_code/`](../data_code/) | 수집 탐색·EDA·모델 비교와 학부 실험 기록 |
| [`02_backfill_10y.py`](../data_code/02_backfill_10y.py) | `coffee_service.ingestion.main`을 호출하는 기존 수집 CLI 호환 진입점 |
| [`ingestion.py`](../coffee_service/ingestion.py) | 수치 소스 조회, 증분 구간, 파일 병합 |
| [`transform.py`](../coffee_service/transform.py) | 거래일·가격·거시 정렬과 시점 처리 |
| [`features.py`](../coffee_service/features.py) | `FeatureDataset`, 피처 순서·유효 구간·타깃 |
| [`modeling.py`](../coffee_service/modeling.py), [`training.py`](../coffee_service/training.py) | DLinear 정의, 학습, 통계·가중치 저장/로드 |
| [`inference.py`](../coffee_service/inference.py) | 기존 artifact 가격 예측, 목표 거래일·실제값 연결 |
| [`selected_models.py`](../coffee_service/selected_models.py) | 선택 manifest·전처리 검증, 조건부 뉴스와 구성원 가격 평균 |
| [`jev.py`](../coffee_service/jev.py), [`jev_store.py`](../coffee_service/jev_store.py) | Jev 호출·분류 검증과 뉴스 archive |
| [`refresh.py`](../coffee_service/refresh.py) | 수치·뉴스 갱신, 복구·반복 실행 |
| [`news.py`](../coffee_service/news.py) | 뉴스 수집·중복 제거·제목 규칙·집계 |
| [`intelligence.py`](../coffee_service/intelligence.py), [`experiments.py`](../coffee_service/experiments.py), [`diagnostics.py`](../coffee_service/diagnostics.py) | 분류·회귀 결합·선택·서빙, 시간 순서 실험, 진단 |
| [`pipeline.py`](../coffee_service/pipeline.py) | 수집부터 적재까지 실행 순서·실패 경계 |
| [`db.py`](../coffee_service/db.py), [`api.py`](../coffee_service/api.py), [`frontend/`](../frontend/) | SQL 저장·조회, HTTP API, 화면 |

현재는 작은 함수·모듈을 조합하며 범용 plugin framework·feature store·DAG orchestrator는 없다. 수집은 모델·API를, 모델은 원천 API를 몰라도 되도록 경계를 유지한다. 결합 순서는 pipeline이 담당하고 API는 대량 수집·학습을 요청 안에서 수행하지 않는다.

### 실행 단위

- `pipeline backfill`: 지정 구간 수집 또는 저장 자료 재처리·적재.
- `pipeline incremental`: 겹침 구간 증분 수집과 예측·실제값 보완.
- `coffee_service.training`: 가격 artifact 학습. 수집 실행과 자동 연동하지 않는다.
- 뉴스 실험 경로: 기존 가격 모델과 분리한 후보 비교·선택.

위 `pipeline`은 `coffee_service.pipeline` 모듈을 뜻한다. CLI는 `backfill`·`incremental`·`news`·`refresh`를 제공하며 `daily` 별칭은 없다. `--skip-ingestion`은 수치 수집만 생략하고, `--ingest-news`는 독립 플래그다. 별도 확률·신호 적재에는 `--intelligence-artifact`가 필요하다. 실행 명령은 README를 참조한다.

새 소스·피처는 기존 조립 지점에 연결하되 **피처를 추가한 채 기존 artifact에 그대로 입력하지 않는다**. 이름·순서·shape·전처리·이용 가능 시점이 달라지면 새 계약으로 학습·평가하고 artifact를 구분하는 것이 확장 목표다.

<a id="data-contract"></a>

## 3. 데이터·시간·피처 계약

### 소스와 역할

| 자료 | 사용 목적 | 현재 가격 서빙에서의 위치 |
|---|---|---|
| Yahoo `KC=F` | 종가·OHLCV, 거래일 가격 피처 | 필수 |
| ALFRED `DEXBZUS`, `DFF`, `DCOILWTICO` | BRL/USD 환율·금리·WTI 최초 공개값 | 필수 |
| Yahoo `BRL=X`, FRED 수정본·달러지수 | 수집·비교 자료 | 선택 모델 입력에서는 제외 |
| NASA POWER | 브라질·콜롬비아 6개 지점의 기상 | 선택 모델의 27개 수치 feature에 포함 |
| CFTC COT | 포지션 자료 수집 | 공개 지연 검증 부족으로 모델 입력에서는 제외 |
| Yahoo·Google News / Jev | 커피 가격 압력 분류 | 조건부 뉴스 feature |
| Daily Coffee News | 제목 기반 사건·가격 영향 규칙 | 기존 연구 경로 |

소스·좌표·기본 수집 기간은 [`sources.yaml`](../configs/sources.yaml), [`regions.yaml`](../configs/regions.yaml)을 따른다. 6개 기상 지점을 국가 전체의 대표 관측으로 가정하지 않는다.

### 저장 위치와 논리 계층

원응답은 `data/raw/pilot_probe/`, 수집 테이블은 `data/processed/<시작일>_<종료일>/`의 Parquet, 뉴스는 소스 디렉터리 아래 `news/articles.parquet`에 보존한다. 모델 파일은 `model_artifacts/` 또는 실행 시 지정한 경로를 사용한다.

원천 보존 → 정제 → 모델 입력이라는 논리적 계층은 유지하지만, **Bronze/Silver/Gold라는 별도 저장 디렉터리·클라우드 저장소를 모두 구축한 것은 아니다**. 현재 서빙 `FeatureDataset`은 Python에서 조립하며, 대시보드용 결과는 PostgreSQL에 적재한다. 새 VM의 데이터·artifact 준비는 이미지 빌드와 별개의 절차다.

### 거래일·가격·결측

예측 기준은 거래일 종가 확인 후 **UTC 23시**다. `KC=F` Close를 공식 정산가와 동일하다고 확정하지 않는다. 가격 단위 표시는 원천 단위를 유지하며, 수익률 지표와 가격 단위 지표를 구분한다.

현재 거래일 함수는 NYSE 달력에 커피 거래일 `2018-12-05`, `2025-01-09`를 보완한 방식이다. 완전한 ICE 거래일 이력을 구현했다고 주장하지 않으며, 이후 달력 변경은 기존 `target_date`와 예측 이력의 영향까지 검토해야 한다.

가격이 없는 거래일도 시계열의 위치는 유지한 뒤 lag·target을 계산한다. `dropna`로 날짜를 압축한 후 거래일 지평을 계산하지 않는다. 주말·휴장일을 임의 생성하거나 미래 값·전체 평균으로 가격을 보정하지 않는다. FRED `.`와 NASA 결측 표시는 결측값으로 처리한다.

### 이용 가능 시점

| 자료 | 기존 연구·처리 기준 | 한계 |
|---|---|---|
| ALFRED | 최초 공개값 `output_type=4`; 공개 날짜의 다음 날부터 backward as-of join | 일 단위 공개일을 사용한 보수적 기준 |
| NASA | UTC 관측일 + 4달력일을 이용 가능 시점으로 가정 | 당시 공개본·수정 이력을 복원한 것은 아님 |
| Yahoo·NASA 과거 자료 | 현재 제공되는 과거 자료를 사용 | 실제 과거 시점의 원본을 완전히 재현하지 못함 |
| 뉴스 | 공개·수정·수집 시각을 모드별로 제한 | [뉴스 계약](#news-intelligence-contract) 참조 |

ALFRED는 이미 알려진 마지막 값을 이후 시점에 유지하며 미래 공개값을 앞당겨 넣지 않는다. `DEXBZUS`는 Yahoo 환율 종가와 다른 관측 기준이다. WTI에는 음수가 존재하므로 유가 피처에 로그변환을 적용하지 않는다.

`DTWEXBGS`의 2019년 이전 소급 자료와 COT의 공개 지연은 기존 연구에서 입력 제외 이유로 기록되어 있다. 문서 정리를 이유로 해당 변수를 자동 재채택하지 않는다.

### 선택 모델과 기존 피처 계약

선택 모델은 manifest의 27개 수치 feature와 `news_sentiment`의 순서로 60거래일 창을 받는다. 기상 기준값은 저장된 학습 cutoff, 입력·타깃 표준화는 저장 통계를 사용한다. 당일 뉴스 점수가 없거나 0이면 같은 설정의 27개 feature 모델로 복귀한다.

기존 artifact 경로는 [`features.py`](../coffee_service/features.py)의 `PRODUCTION_FEATURES` 순서를 유지한다.

```text
return_1, return_5, return_20, volatility_20,
brl_change_20, rate_change_20, oil_change_20
```

가격 피처 4개와 거시 피처 3개다. 환율은 20거래일 로그변화, 금리·유가는 20거래일 차이를 사용한다. `FeatureDataset`은 `features`, `prices`, `sessions`, `weather_columns`, `availability`를 포함하며 이 기존 경로의 `weather_columns`는 비어 있다. 선택 모델은 `assemble_selected`에서 기상 열을 추가한다.

DLinear 입력은 `(n, 60거래일, 7피처)`다. 날짜는 정렬된 고유 값이어야 하고, 입력 창의 결측·무한대 및 artifact의 피처 순서 불일치는 거부한다. 추론 시 새로운 기상 기준값이나 모델 표준화 통계를 fit하지 않는다.

### 기후 피처의 연구·선택 모델 계약

연구에서는 가격 / 가격+거시 / 가격+기후 / 전체 그룹을 비교했다. 03-2의 그룹 크기는 각각 가격+거시 7개, 가격+기후 24개, 전체 27개다.

기후 후보는 지점별 30달력일 강수·기온의 월별 편차, 90일 강수 결손, 브라질 지점의 7일 격자 서리 강도와 직전 가뭄×서리, 월 주기 sin·cos다. 서리 강도는 `max(0, -Tmin)` 합이며 실제 서리 관측이 아니고, 강수 결손은 SPI/SPEI가 아니다. 계절 피처도 포함되므로 그룹 성과를 순수 기상 효과로 단정하지 않는다.

월별 기준값과 상수·상관 필터는 Train에서만 계산한다. 부족한 초기 집계는 실제 과거 버퍼로 해결하며, 전체 평균·중앙값으로 채우지 않는다. 절대 상관 0.90 이상인 기상 쌍에서 타깃 상관이 약한 피처를 제외한 후보를 비교하되, 상관·VIF만으로 제거를 확정하지 않는다. 기존 실험에서는 해당 임계값을 넘는 쌍이 없어 축소 후보를 생략했다.

<a id="model-contract"></a>

## 4. 모델·추론·평가 계약

### 타깃과 출력

```text
r(t,h)      = log(P[t+h] / P[t])
predicted_P = P[t] × exp(predicted_r)
h           ∈ {5, 20, 60} 거래일
```

`origin_date`는 기준 거래일, `target_date`는 목표 거래일이다. `predicted_return`은 로그수익률, `predicted_price`는 환산 가격, `actual_price`는 목표일 실제값이며 미도래 시 NULL이다. `final_direction`은 가격 예측의 부호이고 `probability_up`은 별도 분류기의 조건부 확률이다.

차트는 같은 `target_date`에 실제·예측 가격을 놓는다. Persistence는 수익률 0·가격 `P[t]`이므로 지평만큼 이동한 가격선처럼 보이며, 이는 모델 정의이지 구현 오류 자체가 아니다.

선택 모델의 구성·학습 cutoff·뉴스 정책은 문서 상단과 `selected_models.py`를 따른다. 아래 표는 기존 artifact 경로다.

| 지평 | 기존 가격 모델 | 계약 |
|---|---|---|
| 5·20일 | Persistence | 기준일 종가; ID `persistence-h5-h20-v1` |
| 60일 | 가격+거시 DLinear | `(n,60,7)` 입력; `production_dlinear_60.pt`, ID `dlinear-price-macro-h60-v1` |

DLinear는 분해 선형 구조의 지평별 로그수익률 출력 변형이며 기존 50 epoch·seed 42 설정을 사용한다. artifact는 가중치·피처 목록·입력 scaler·타깃 평균/표준편차·metadata·format version을 포함한다. 로드 시 ID·순서·통계 shape·유한값·양의 scale·가중치를 검사하고 추론에서 저장 통계를 재사용한다.

기존 가격 모델 ID는 고정되어 있다. 선택 모델은 구성원 이름·지평·manifest 버전을 model ID에 포함한다. 후속 모델 변경 시 artifact만 덮어쓰지 않고 ID·피처 계약·학습 범위의 버전 정책을 보강해야 한다. `generate_predictions`는 2024년 이후 유효 기준일의 성숙·미성숙 예측을 생성하며 범위 밖 목표일은 달력으로 계산한다. **증분 수집과 신규 날짜만 추론하는 것은 다르다.** 기존 발행 결과 보존은 DB 계약이 담당한다.

### 기존 03-1/03-2의 시간 분할과 평가

| 구간 | 기간 | 역할 |
|---|---|---|
| 버퍼 | 2014-07~2014-12 | 과거 집계·입력 창 |
| Train | 2015~2021 | EDA·전처리·피처 후보·학습 |
| Validation | 2022~2023 | 그룹·모델·설정 선택 |
| Test | 2024~2025 | 선택 고정 후 비교; 이미 확인한 기간 |

정답 날짜도 해당 구간 안에 끝나야 한다. 학습 종료를 넘는 60일 타깃 등은 제외한다. Test 전 설정·피처를 고정하고 2015~2023년으로 재학습하며 전처리도 그 범위에서 fit한다.

주 지표는 로그수익률 RMSE이고 MAE·방향·가격 단위 오차를 함께 본다. 동일 날짜·지평의 Naive와 항상 상승/하락 기준을 사용한다. 0 예측은 보합이고 Naive 방향 정확도를 50%로 가정하지 않는다. 연도별·비중첩 시작점 결과를 확인하며 중첩 수익률·단일 seed·이미 본 Test의 한계를 보존한다.

### 기존 노트북 비교는 서빙과 구분

[03-1](../data_code/03_1_eda_and_baseline_models.ipynb), [03-2](../data_code/03_2_horizon_model_validation.ipynb)의 상세 설정·출력은 원 노트북과 STATUS를 참조한다. 다음 차이는 유지한다.

| 실험 | 비교 계약 |
|---|---|
| 03-1 그룹 선택 | 가격/가격+거시/가격+기후/전체를 동일 CatBoost 설정·날짜로 비교. Validation RMSE, 동률은 적은 피처 |
| 03-1 모델 비교 | 모델별 설정 후보 2개. LSTM 30일 창·hidden 16, 신경망 30/60 epoch |
| 03-2 재검증 | 31개 후보와 지평별 Naive. 신경망 60일 창·50/100 epoch, LSTM/Attention-LSTM hidden 64·2층·dropout 0.1. 전체 피처 60일 창의 공통 유효 날짜 |
| 03-2 선택·해석 | Validation RMSE로 선택, 정확한 동률은 Naive→적은 피처→작은 설정. Test 재선택 금지. Attention의 Entmax/gate 유지; attention을 변수 중요도·인과관계로 해석하지 않음 |

STL·정상성 검정·CCF는 설명·탐색용이며 최대 상관 lag를 자동 채택하지 않는다. PyCaret 상태공간 지수평활·ARIMA는 평가 중 모수를 고정하고 관측으로 상태만 갱신하며 결측을 대치하지 않는다. 03-1과 03-2는 입력 창·용량도 달라 점수 차이를 attention 효과로 단정하지 않는다.

03-2의 두 앙상블은 별도 연구다. 첫째는 Validation 방향 정확도≥50%인 후보 중 RMSE 상위 두 조합의 50:50 평균이다. 둘째는 Validation RMSE 최저·방향 최고 후보를 정한 뒤 RMSE 모델 비중 1/0.75/0.5/0.25/0을 비교한다. 기존 설정 고정, Test 재선택·자동 서빙 교체 금지이며 50% 필터는 통계적 우월성의 증명이 아니다.

<a id="news-intelligence-contract"></a>

## 5. 뉴스 분석과 확장 경계

### 5.1. 기존 제목 규칙 수집·집계

Daily Coffee News WordPress의 제목·URL·공개/수정 시각 등 metadata를 사용한다. `summary`는 빈 문자열이고 본문은 저장하지 않는다. `rights`를 기록하며 원문 기사 API는 제공하지 않는다. 이 범위를 다른 소스의 이용·재배포 권한으로 일반화하지 않는다.

| 항목 | 현재 계약 |
|---|---|
| 저장 | `source_dir/news/articles.parquet` 또는 `--news-path`; metadata·규칙 결과·`analysis_version` |
| 식별 | 추적 파라미터·fragment·끝 슬래시 정리 URL의 SHA-256. 같은 URL의 최초 수집본 보존 |
| 중복 | 정규화 제목이 같고 이용 가능 시각이 24시간 이내인 기사 제거 |
| 시점 | UTC `published_at`, `modified_at`, `collected_at`; `available_at=max(published_at, modified_at)` |
| 검사 | 공개≤수집 시각, timezone·URL/ID·점수 범위 확인 |
| coverage | Parquet 속성 `coverage_start`, `coverage_end`, `last_collected_at`; 전체 페이지·누락 확인 후 임시 파일에서 원자적 교체 |
| 증분 | 마지막 공개일/coverage 끝 기준 7일 겹침 |

`title-rules-v2`는 영어 제목의 커피 관련성 0/1, 주제, bullish/bearish/neutral, 규칙 신뢰도를 산출한다. 주제는 날씨·생산·공급·재고·수출·환율·수요·물류·정책·기타다. **규칙 신뢰도는 학습·보정한 확률이 아니다.** 읽을 때 파생 결과를 재계산하더라도 최초 metadata는 보존한다.

예측일 UTC 23시까지 이용 가능한 기사 중 공개 시각이 `(as_of-window, as_of]`인 기사를 집계한다. 창별 전체/관련 수, 강세·약세 비율, 평균·가중 영향, 신뢰도 합, 주제별 수·영향 등 28개 열이며 6개 창은 168개 열이다.

```text
sign   = bullish +1 / neutral 0 / bearish -1
weight = exp(-age_days / window)
weighted impact = Σ(sign × rule_confidence × weight) / Σ(weight)
```

이는 기존 규칙 feature의 정의다. LLM 교체 시 동일 의미라고 가정하거나 보정되지 않은 모델 자기신뢰도로 가중치를 대체하지 않는다.

### 5.2. 기존 제목 규칙의 historical/live와 결측

| 상태 | 현재 처리 |
|---|---|
| `historical` | 공개·수정 시각으로 재생; 수집 시각 제한 해제. 당시 최초 공개본이 아닌 회고 연구 |
| `live` | 추가로 `collected_at <= min(현재 시각, 예측일 UTC 23시)` 요구 |
| 정상 수집·빈 창 | 건수·영향 0 |
| 파일 없음·실패·coverage 부족·수집 전 시점 | 뉴스 표시 NULL. 수치 분류기가 가능하면 `fallback`, 확률도 불가하면 `unavailable` |

pipeline 기본값은 `live`, summary API 기본값은 `historical`이다. 기본값이 같다고 가정하지 않는다. 뉴스 장애는 필수 수치 경로의 성공을 막지 않는다.

### 5.3. 기존 방향 분류·고정 결합의 평가 계약

이 기존 실험의 수치 입력은 7개 피처다. 60일 이력이 유효한 공통 날짜를 사용하되 표 모델은 현재 행의 집계를 받는다. 뉴스 후보는 지평별 5일:1/3/7일, 20일:7/14/30일, 60일:14/30/60일 창 중 하나의 28개 열을 더한다. 정상 뉴스 0건 때문에 평가 날짜를 제외하지 않는다.

분류/회귀 쌍은 Logistic Regression/Ridge, CatBoost, LightGBM이다. 선형 scaler는 각 Train에서 fit하고 기존 고정 설정·seed 42·단일 thread를 유지한다. 단일 클래스와 상수 타깃은 상수 분류기·평균 회귀기로 처리한다. 상세 설정은 `intelligence.py`와 STATUS를 따른다.

2015년부터 2020/2021/2022년 말까지 Train을 확장하고 다음 2021/2022/2023년을 검증한다. 학습·평가 정답 모두 각 구간을 넘지 않아야 하며 DLinear 기준도 fold별로 재학습한다. 선택 후 2023년까지 재학습하고 이미 본 2024~2025년은 기술 평가로 남긴다.

| 후보 | 예측 로그수익률 |
|---|---|
| C1 | `r` |
| C2 | `abs(r) × sign(p-0.5)` |
| C3 | `0.5×r + 0.5×σ_train×(2×p-1) + 0.1×σ_train×news_impact` |

`r`은 회귀 출력, `p`는 같은 쌍의 상승 확률, `σ_train`은 Train 타깃 표준편차다. 수치 전용 뉴스 영향은 0이다. 고정 휴리스틱 실험이며 기대수익률의 일반 공식·학습형 stacking이 아니다. OOF는 기록용이고 별도 meta-model fit은 없다.

실제 수익률 0은 회귀에 포함하고 분류에서는 제외하므로 확률은 `P(UP | non-FLAT)`다. 분류 판정은 `p>=0.5`지만 C2는 정확히 0.5에서 0을 반환한다. 진단용 10bp 보합 기준은 별개다.

<a id="news-intelligence-selection"></a>

### 5.4. 기존 intelligence 실험의 채택 기준과 선택

3개 유효 fold의 산술평균을 쓴다. 상대 RMSE 개선은 `1-후보RMSE/같은 fold의 기존가격RMSE`다. 뉴스는 아래 조건 외에도 **동일 모델·동일 C번호의 수치 후보보다 평균과 2개 이상 fold에서 엄격히 좋아야 한다**.

| 대상 | 기존 실험의 채택 조건 |
|---|---|
| 회귀 | 평균 상대 RMSE 개선≥1%, 2개 이상 fold 개선, 최악 fold≥−5% |
| 5·20일 회귀 추가 | 평균 방향 BA≥0.52, 2개 이상 fold BA>0.5. 실제 보합 제외, 예측 보합은 상승·하락 모두 오답 |
| 분류 | 평균 BA≥0.52, 2개 이상 fold에서 Train 다수 클래스 BA 초과, 평균 Brier≤Train 상승 비율의 상수 확률 기준 |

통과 후보 중 회귀는 평균 상대 개선, 분류는 평균 BA로 선택한다. 회귀 후보가 없으면 기존 가격을, 분류 후보가 없으면 수치 전용 최고 평균 BA를 `experimental`로 제공한다.

해당 실험의 가격은 모두 기존 `base`, 분류는 수치 CatBoost(5일 `validated`·20일 `experimental`)와 Logistic Regression(60일 `experimental`)이다. `validated`는 **위 Validation 조건 통과**이지 미래 성과 보장이 아니다. 뉴스·가격 결합은 미채택이다.

`news_intelligence_v1_serving.joblib`는 실험 artifact의 36쌍 중 선택·fallback에 필요한 3쌍과 동일 run ID를 유지한다. 기존 DLinear는 바꾸지 않는다. `final_direction`은 가격 예측의 부호여서 5·20일은 별도 상승 확률과 관계없이 FLAT이다. 뉴스 표시 창은 지평별 7/30/60일이며 DLinear의 기존 Test RMSE를 뉴스·분류 성과로 표시하지 않는다.

### 5.5. 로컬 LLM 확장 — 대체 후보/후속 목표

현재 선택 모델은 문서 상단의 조건부 뉴스 feature를 사용하며, 문서 마지막의 **2026-09-26 Jev 뉴스 잔차 보정 계약**은 별도 호환 경로다. 아래 Ollama·다중 소스·사건/근거 추출은 구현된 Jev 가격 압력 분류를 대체하거나 확장할 후속 후보이며 현재 동작을 뜻하지 않는다.

본문 크롤링·BERT 필터 없이 **제목+제공된 짧은 설명**으로 관련성·사건·전달 경로·가격 압력·근거를 한 번에 추출한다. 수집 범위는 커피·농업·원산지·거시·국제물류다. 소스의 무료 조건·지연·과거 coverage는 도입 시 확인하며 후보 선정과 구현 완료를 구분한다.

pipeline이 Ollama의 Qwen 계열 모델을 호출하는 구조를 목표로 하며 모델·양자화·버전은 실측 후 확정한다.

- 일반 positive/negative가 아니라 **USD 아라비카 선물의 부분적 가격 압력**을 분류한다. 공급 감소와 수요 감소, 부정·전망 수정·비교 기준을 구분한다.
- 정보 부족·상충·무관을 neutral과 분리한다. 기존 3라벨 규칙 schema와 구분한다. Jev 분석은 이미 uncertain을 포함한 4분류를 보존한다.
- 텍스트·모델·프롬프트·schema 버전으로 캐시하고 live 이용 시점에 수집·분류 완료를 반영한다. 기존 규칙 결과는 보존한다.
- 구조화 출력·입력 근거 검증과 사람 표본의 해석 정확도를 따로 평가한다. 새 피처는 동일 기간 ablation과 새 artifact 검증 후 채택한다.

<a id="persistence"></a>

## 6. 저장·정합성·실패 처리

### PostgreSQL schema

현재 DB 접근은 `psycopg`와 직접 SQL이다. `python -m coffee_service.db`가 테이블·인덱스·enum을 생성하고 필요한 열을 `ADD COLUMN IF NOT EXISTS`로 보완한다. **SQLAlchemy ORM이나 Alembic migration/version 체계는 사용하지 않는다.**

| 테이블 | 식별 키 | 역할 |
|---|---|---|
| `prices` | `date` | 현재 단일 가격 종목의 OHLCV·symbol |
| `models` | `model_id` | 피처·지표 JSONB, 지평, 학습 기간, 현재 모델 표시 |
| `predictions` | `(model_id, origin_date, horizon)` | 목표일·가격·수익률·실제값·확률·뉴스 신호·버전 |
| `pipeline_runs` | `run_id` UUID | mode·시작/종료·성공/실패·처리 행 수 |
| `source_status` | `source` | 소스별 상태·최신 관측일·행 수·오류 |
| `news_articles` | `article_id` | 최초 기사 metadata와 규칙 결과 |
| `news_daily_features` | `(as_of_date, window_days, availability_mode)` | 기존 규칙 뉴스 집계 JSONB |
| `jev_analyses` | `analysis_id` | Jev 확률·관련성·확신도·분석 버전 |
| `jev_selections` | `content_hash` | 선정 기사 metadata·이용 가능 시각 |
| `news_forecast_runs` | `run_id` | 기존 뉴스 보정 예측 snapshot |

`predictions.model_id`는 `models`를 참조한다. 예측 목표일·지평과 기사 이용 가능 시각에 조회 인덱스가 있다. `prices`의 키는 현재 단일 종목 전제이므로 다종목 확장 시 schema 변경이 필요하다.

### 재실행과 발행 결과 보존

가격 자료는 갱신할 수 있지만, 예측 UPSERT는 **같은 자연키의 예측값·목표일을 덮어쓰지 않고 목표일이 일치할 때 실제값을 보완**한다. 실제값은 저장된 `target_date`와 `prices.date`를 조인해 채운다.

뉴스 intelligence의 model ID는 `intelligence-h{horizon}-{run_id}-{live|historical}`로 모드·버전을 구분한다. 이미 발행된 가격·방향·확률을 재실행으로 바꾸지 않는다. 이전 예측의 신호 필드가 전부 비어 있을 때만 목표일·가격·수익률이 같은 조건에서 한 번 보완한다. 기사 ID 충돌도 최초 행을 유지한다.

이 계약의 목적은 중복 삽입 방지뿐 아니라 과거 발행 내용을 나중의 코드·자료로 조용히 바꾸지 않는 것이다. 모델 교체·달력 변경·기사 수정본 처리는 기존 행 덮어쓰기와 별도로 설계한다.

### 수집·트랜잭션·복구 경계

수치 수집은 소스별 7일 겹침 구간을 병합하고 임시 파일 후 교체한다. 실패한 소스 때문에 기존 정상 파일을 지우지 않는다. pipeline 실행 시작과 종료는 DB에 기록한다.

필수 소스는 `coffee`, `alfred_dexbzus`, `alfred_dff`, `alfred_dcoilwtico`다. 필수 수집 실패 또는 최신 커피 가격일 대비 거시 자료가 **14달력일을 초과해 오래된 경우** 현재 파이프라인은 적재를 중단한다. 14일은 현재 서비스의 운영 제한이지, 원천 공개 주기나 분석용 보간 규칙이 아니다.

COT·뉴스 등 보조 소스 실패는 source 상태에 남긴다. 선택 모델은 6개 기상 파일과 완전한 수치 입력 창도 필요하며, 기존 7개 feature 경로와 달리 기상 자료 없이 추론할 수 없다. 따라서 pipeline 전체가 `success`여도 보조 소스 실패가 있을 수 있으며 상세 소스 상태·메시지를 함께 읽어야 한다.

가격·예측 등의 DB 적재는 처리 실패 시 롤백하고, 수집 파일 상태인 `source_status`는 별도 commit으로 남긴다. **파일 저장과 PostgreSQL을 묶는 분산 트랜잭션은 없다.** DB 적재 실패 후에도 소스 파일은 남을 수 있으므로 저장 소스로 재실행한다. 로컬 refresh와 Azure 일일 Actions의 반복 실행·동시성 제어는 별도 구현되어 있다. VM 디스크 손실 복원까지 검증한 것은 아니다.

<a id="serving"></a>

## 7. API·프론트엔드 계약

FastAPI의 HTTP 경로는 조회 중심이다. Azure는 API 전용 SELECT 역할을 사용하며 로컬 Compose의 schema 초기화 계정과 구분한다. 실제 route·파라미터는 [`api.py`](../coffee_service/api.py)를 기준으로 한다.

| GET 경로 | 현재 동작 |
|---|---|
| `/health` | DB `SELECT 1` 포함. 순수 liveness가 아니며 별도 `/ready`는 없음 |
| `/api/v1/prices` | 최근 limit개, 기본 365·최대 5000 |
| `/api/v1/prices/latest` | 최신 가격 |
| `/api/v1/predictions` | horizon·model_id·limit 조회. model ID 미지정 시 현재 모델, 기본 1000·최대 5000 |
| `/api/v1/models/current` | 모델·피처·학습 기간·지표 |
| `/api/v1/pipeline/status` | news를 제외한 최신 가격 실행 |
| `/api/v1/data-sources/status` | 소스별 상태 |
| `/api/v1/news/summary` | 창·기준일·모드별 집계, 기본 7일·historical |
| `/api/v1/news/jev` | 기존 snapshot과 `inventory`; 전체/분석 완료/미분석 필터, offset/limit, 기사·분석 시각 |

예측 응답에는 가격·수익률·실제값 외에 확률·뉴스 영향·기사 수·갱신 시각·상태·모드·버전이 있다. 별도 metrics endpoint나 날짜 범위 페이지네이션을 구현한 것으로 쓰지 않는다.

Vue는 브라우저에서 API 결과로 카드·차트를 구성하며 DB·artifact에 직접 접근하지 않는다. Nginx가 정적 파일을 제공하고 `/api/`와 `/health`를 내부 API로 프록시한다.

```text
Vue의 /api/... → web의 Nginx → http://api:8000 → postgres:5432
```

Docker 내부 서비스 이름을 브라우저 API 주소로 쓰지 않는다. 가격 예측·조건부 확률·뉴스 해석과 NULL/fallback/historical 상태를 구분하며, 과거 재생을 실시간 확정값처럼 표시하지 않는다.

<a id="runtime"></a>

## 8. 실행 환경·컨테이너 경계

### native와 컨테이너

Mac native 분석은 외부 Python 3.12 uv 환경을 사용한다. Docker·CI·VM은 그 경로에 의존하지 않으며 각 의존성 파일과 이미지에서 실행 환경을 준비한다. 버전·설치 명령은 `requirements*.txt`, frontend manifest/lockfile, Dockerfile과 README에서 관리한다.

| 서비스 | 이미지 책임 | 시작·저장 경계 |
|---|---|---|
| `postgres` | 공식 PostgreSQL 이미지 | healthcheck, `postgres-data` named volume |
| `api` | FastAPI·Uvicorn·DB client와 API 코드 | DB 준비 후 schema 초기화 → Uvicorn. pandas·PyTorch 미포함 |
| `web` | Node 단계의 Vue build 결과를 Nginx에 복사 | API health 뒤 실행, 런타임 `API_UPSTREAM` |
| `pipeline` | 수집·피처·CPU 추론 의존성 | 로컬 Compose의 반복 refresh, `/data` named volume. Azure는 Actions의 일회 실행 |
| `llm` — 목표 | Ollama와 별도 모델 가중치 | 추가 구현·메모리·처리량 검증 필요 |

현재 API·pipeline은 non-root 애플리케이션 사용자로 실행한다. DB 접속은 Compose의 `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER`, `PGPASSWORD`를 사용하고 호스트의 `DATABASE_URL`을 그대로 넘기지 않는다. API 키는 필요한 pipeline에만 전달한다.

### 현재 Compose의 입력과 포트

현재 [`compose.yaml`](../compose.yaml)은 API·pipeline·web을 `build:`로 구성한다. web/API는 각각 기본 8080/8001 포트를 host loopback에 바인딩한다. DB는 외부 포트를 공개하지 않는다. 이 로컬 설정을 그대로 VM의 공개 배포 설정으로 간주하지 않는다.

pipeline은 소스 Parquet를 `/seed`, 모델 디렉터리를 `/models`, 기존 DLinear 파일을 호환 경로에 read-only bind mount한다. entrypoint는 `/seed`에 있는 파일 중 `/data/sources`에 없는 파일만 임시 파일을 거쳐 복사한다. 기존 작업 파일과 같은 이름의 수정 원본을 자동 덮어쓰지는 않는다.

따라서 기존 Docker Local E2E의 기본 경로는 **저장된 Parquet·artifact를 준비한 재처리**다. read-only 모델 mount를 유지한 채, 아무 입력도 없는 VM에서 자동 재수집·재학습된다고 설명하지 않는다.

### 초기화·복원 목표

새 배포에서는 DB schema, 소스 재수집 또는 검증된 데이터 복원, 가격 artifact 제공 또는 별도 학습, 필요 시 LLM 가중치 준비를 명시해야 한다. 이미지·모델·데이터의 준비 완료를 각각 확인한다.

컨테이너 재시작과 DB·artifact 복원은 다른 검증이다. 기존 volume을 삭제해서 fresh start를 만들지 않고 별도 테스트 환경에서 검증한다. VM 삭제 전에 보존할 데이터와 백업·복원 방법도 별도로 마련해야 한다.

<a id="delivery"></a>

## 9. CI·이미지 게시·배포·보안

### 현재 CI·GHCR

[`ci.yml`](../.github/workflows/ci.yml)은 PR·main push·reusable 호출에서 Python fixture와 테스트용 PostgreSQL, Vue 테스트/build, API·pipeline·web Docker build를 실행한다. macOS 전용 검사와 처리 데이터 기반 전체 E2E는 일반 CI와 분리되어 있다.

[`publish-ghcr.yml`](../.github/workflows/publish-ghcr.yml)은 수동 실행이다. `publish=false`도 CI를 수행하며 동일 커밋 검증 성공·`publish=true`·`refs/heads/main`일 때만 게시한다. `packages: write`는 게시 job에 한정하고 `GITHUB_TOKEN`으로 인증한다.

```text
ghcr.io/sjinie/coffee-price-prediction-api:<commit-sha>
ghcr.io/sjinie/coffee-price-prediction-pipeline:<commit-sha>
ghcr.io/sjinie/coffee-price-prediction-web:<commit-sha>
```

태그는 전체 commit SHA다. **현재 publish job은 CI 빌드와 별도로 다시 빌드**하므로 같은 코드 SHA 검증과 동일 image digest 검증은 다르다. 게시 이미지의 로컬 복원 실행 결과는 STATUS에 기록하며, Azure 소스 build·수치 수집 실행 기록은 별개이며 새 게시 digest의 Azure 실행은 후속 검증이다. Action 런타임 경고·업데이트 결과는 STATUS/Troubleshooting에서 관리한다.

### 첫 배포용 구성과 현재 실행 경계

[`compose.deploy.yaml`](../compose.deploy.yaml)은 4개 서비스만 정의하며, 확인한 linux/amd64 GHCR digest와 PostgreSQL 17.11-bookworm digest를 사용한다. 기존 `compose.yaml`과 Dockerfile은 그대로다. API/DB의 host port는 없고 web만 loopback에 바인딩한다. 컨테이너 간 주소는 service name이며, Mac 서비스·venv·로컬 소스 코드 mount에 의존하지 않는다.

[`deploy.py`](../deploy/deploy.py)는 네 이미지 pull → 기존 DB/API 초기화 → web 준비 → 입력 사전검사 → 복원 batch → HTTP/최신 5·20·60일 예측 검증을 수행한다. 복원 사전검사는 게시 이미지 안의 기존 seed·feature·모델·latest_predictions 함수를 사용하며 애플리케이션 소스를 주입하지 않는다. Parquet는 별도 제공본, artifact는 fab81c0 추적 파일의 검증된 복사본이며 둘 다 이미지 밖에 둔다. UID 10001의 읽기 권한과 DB·pipeline named volume을 유지한다.

기존 이미지에서는 입력 이력이 짧아도 h5/h20만 적재한 뒤 DB run이 success일 수 있다. 수정 소스는 run_pipeline의 UPSERT 전에 최신 유효 가격일의 세 지평 완전성을 검사하고 실패 시 rollback/failed를 기록한다. **이 guard는 기존 이미지에 없으므로 배포 CLI의 collect는 현재 차단한다.** 새 소스의 사용자 push·GHCR 게시, digest/플랫폼 확인 후 차단 해제와 신규 수집 검증이 필요하다. 기존 이미지로 확인한 복원 E2E와 새 코드의 테스트를 합쳐 새 이미지 E2E 성공으로 보고하지 않는다.

로컬 게시 이미지 복원·반복 실행과 실제 Azure 실행은 구분한다. Azure는 `deploy/compose.azure.yaml`의 PostgreSQL·API·web·Caddy를 사용하며 일일 수집·추론은 Actions에서 실행한다. 공개 서비스는 main 61c4cff의 소스 build, 새 선택 모델·뉴스 UI는 별도 검증용 컨테이너다. HTTPS·실행 결과는 STATUS, 준비·복원·진단 명령은 README에서 관리한다. PR 게시만으로 운영 전환을 수행하지 않는다.

### 단일 VM 배포 후속 목표

기존 Compose를 보존하고 VM용 설정에서 `build:` 대신 검증된 GHCR `image:`·digest를 사용한다. 대상 CPU 아키텍처에서 재빌드 없이 실행하고 데이터·가격 artifact·LLM 가중치를 별도 준비한다.

```text
release 선택 → 설정·백업 → pull → schema 준비 → 서비스·readiness
→ 소규모 pipeline → DB/API/web 확인
```

수동으로 검증한 배포 스크립트를 Actions에서 호출한다. 인증은 OIDC, VM 명령 실행은 Run Command를 계획하며 **Azure 접근과 GHCR pull 인증은 별개**다. 실제 권한·패키지 공개 범위를 확인하고 계정 설정만으로 배포 완료라 하지 않는다.

웹만 필요한 범위에 공개하고 공개 시연에는 HTTPS를 적용한다. DB·Ollama·API 내부 포트를 인터넷에 직접 열지 않는다. secret은 런타임에 주입하고 Git·이미지·브라우저 번들·로그에서 제외한다.

LLM과 DB/API의 메모리·CPU 경합을 측정하고 요청 동시성·timeout·자원·로그 제한을 정한다. 현재 일일 Actions의 concurrency·복구 기록과 새 배포 자동화의 검증은 구분한다. 이전 digest의 application rollback과 DB schema rollback은 구분한다.

학생 크레딧의 단기 실습이므로 비용 확인·백업·종료·복원까지 포함한다. 컨테이너 종료, VM 할당 해제, 디스크·IP 삭제는 서로 다른 작업이다. Container Apps·관리형 DB·Blob·AKS·Airflow 등은 필수로 추가하지 않는다.

<a id="limitations"></a>

## 10. 현재 한계와 다음 단계의 검증 조건

현재 연구는 수정된 과거 자료·이미 확인한 Test·중첩 수익률을 사용한다. 모델의 안정적 미래 성과를 확정하지 않으며, 새로운 검증은 전향적 기록과 구분한다. 뉴스 규칙의 낮은 방향 신호 coverage도 LLM의 효과나 뉴스 자체의 예측력을 판단한 결과가 아니다.

다음 단계의 완료 조건은 **게시 이미지로 새 VM의 초기 데이터·artifact 준비 → 실제 수집·추론 → DB·API·web**을 재현하는 것이다. 기존 Jev·정기 실행 결과와 구분해 새 배포 경로의 복구·입력 준비를 검증한다. 기존 원본·volume을 지우거나 fixture 성공을 실제 운영 성공으로 대체하지 않는다.

구조·계약 변경은 이 문서에, 실행 증거·성과·미검증 사항은 STATUS에 기록한다. 계획을 구현 사실로 바꾸는 근거는 파일 생성이나 이미지 게시가 아니라 해당 경로의 실제 실행 검증이다.

## 2026-09-26 — Jev 뉴스 잔차 보정 계약

기존 가격 산출물과 새 뉴스 산출물은 독립적으로 보존한다. `pipeline news`는 기존 수집·feature·가격 모델을 재사용하며, `jev_analyses`에는 content/model/prompt 기반 불변 분석을, `news_forecast_runs`에는 발행 시각별 별도 예측 snapshot을 저장한다. 기사 조회가 느리거나 실패해도 가격 화면은 계속 로드된다. 실패한 뉴스 run은 마지막 정상/부분 성공 snapshot을 대체하지 않는다.

뉴스 보관 조회는 `jev_selections`(content_hash PK, 이용 가능 시각, 공개 기사 메타데이터)를 추가한다. pipeline이 기존 네 파일의 선정 기사를 적재하고, API는 이용 가능 시각이 현재 이하인 선정 기사와 저장 분석을 내용 hash별로 합친 `inventory`를 반환한다. 분석 결과는 저장된 버전 중 가장 최근 분석 시각을 표시하며, 이 목록을 과거 예측에 실제 반영된 기사 목록으로 해석하지 않는다. 전체/분석 완료/미분석 수와 최근 기록 시각, 상태 필터·offset/limit을 제공하고 프론트는 10건씩 표시한다. 수집 후보 전체나 작업 큐의 실행 상태는 나타내지 않는다. API 교체 전 pipeline 권한으로 schema를 갱신하고 API 조회 역할에 신규 테이블 SELECT 권한이 있어야 한다.

- 입력: Yahoo KC=F의 제목(최대 512자)·공급자 요약(최대 1,200자), URL, 출처, 발행/수집/분석 시각. 선택적 GDELT는 발행 대신 발견 시각을 사용한다. URL·내용·제목 중복을 제거하고 모델/프롬프트 버전별 분류 캐시를 재사용한다. 원문 본문은 수집하지 않는다.
- TypeSafe: `POST https://ai-gateway.vercel.sh/typesafe/v1/systemone`, model `typesafe-ai/jev`, `state`와 `questions`. `choice`는 bullish/bearish/neutral/uncertain, `noul`은 아라비카 선물 관련성이다. 텍스트의 일반 감성과 가격 압력을 구분하고 기사 내용은 지시가 아닌 데이터로 취급한다. 응답의 확률·confidence·relevance·토큰·비용을 보존한다. 분류 확률은 미래 가격 상승 확률이 아니다.
- 시점: `event_at`은 발행(없으면 발견), `available_at`은 발행/수정/발견/수집/분석 중 가장 늦은 시각이다. live에서는 cutoff 이전에 이용 가능해진 기사만 쓴다. 연구 모드는 과거 발행 시각으로 재분류 결과를 붙이므로 실시간 성과로 해석하지 않는다.
- 신호(v2): `tanh(sum(relevance * (p_bullish-p_bearish) * 2**(-age_days/3)))`. `confidence`는 같은 확률분포의 요약이므로 중복 곱하지 않는다. 0/1/3/5거래일 시차를 `2**(-lag/3)`를 합 1로 정규화한 비중으로 합친다. 오래된 기사 수집이 늦어져도 나이를 초기화하지 않는다. 현재 연구 snapshot은 실제 발행 시각까지 확보한 기사만 필터링한 뒤 발행·수정·일별 선정 완료 시점으로 시차를 재구성한다. 과거 live feature와 저장 예측을 소급 수정하지 않는다.
- 보정(v2): `σ5 * exp(-(h-5)/10) * signed_news_index * w_h`. `σ5`는 origin까지 최근 20개 일간 로그수익률 표준편차(ddof=1)×√5, `w_h`는 지평별 0~1 반영 강도다. 부호는 뉴스만 정한다. 반감기·시차 커널·지평 감쇠는 고정 설계 가정이지 커피 시장에서 검증된 전달 지연이나 배분 비율이 아니다.
- 강도 추정: 실제−기본 예측 로그수익률을 σ5로 나눈 타깃과 지평 감쇠를 곱한 뉴스 입력을 사용한다. forward 수익률이 겹치지 않는 최소 6개 행을 시간순 선택하고, 학습 행에서만 절편·과거 5일 수익률/σ5를 투영 제거한다. Gaussian 오차와 분산의 Jeffreys 사전, `P(w=0)=0.5` + `0.5*Uniform(0,1)`을 사용해 `SSE(w)^(-(n-rank(C))/2)`를 0~1 격자로 적분한다. 사후평균·95% 사후구간·null probability를 저장한다. 통제변수 효과는 뉴스 보정가에 더하지 않는다. 오차분포·사전 가정에 조건부인 탐색적 추정이며 인과 효과를 식별하지 않는다.
- 평가: 일별 origin의 앞 60% 뒤에서 expanding walk-forward를 수행하고 각 평가 origin보다 앞서 target이 확정된 행만 학습한다. 학습만 비중첩 표본을 선택하고 평가는 같은 일별 origin으로 baseline과 비교한다. RMSE/MAE/3방향 일치율을 기록하며 Persistence의 보합 예측으로 방향 지표를 과장하지 않는다. 평균 제곱오차 개선의 circular-block bootstrap은 block=max(h,5), 1,000회, seed42이며 최소 5개 블록 미만이면 개선 판단을 보류한다. 95% 구간 하한>0일 때만 연구 재평가에서 개선 근거가 있다고 표시한다. 이미 확인한 기간이며 과거 기사도 현재 분류했으므로 `retrospective_reanalysis`로 명시하고 미사용 Test·실제 live 성과로 부르지 않는다.
- 상태: `experimental`, `insufficient_data`, `unavailable`, `no_news`. 데이터가 부족하면 보정가 null이며 기존 가격을 조작하지 않는다. 모델 hash·학습 cutoff·기사 ID·선택 설정·평가 지표를 snapshot에 기록한다. artifact가 발행 시각보다 나중에 학습됐거나 가격이 7일보다 오래되면 보정을 차단한다.
- 운영: key는 pipeline에만 주입한다. 최초 범위 최대 90일/200요청, 캐시 단위 배타 잠금, 원자 저장, 429 즉시 중단/Retry-After 기록, 연속 네트워크·서버 오류 3회 중단을 적용한다. 일반 CI는 외부 API 대신 fixture를 사용한다.


## 2026-09-27 — 간헐 운영 서버의 갱신 경계

이 절은 앞선 수동 jobs profile·스케줄링 미구현 설명을 대체하는 현재 소스 계약이다. `compose.yaml`의 `pipeline`은 기존 수집·추론 image로 `pipeline refresh`를 실행하고, `api`는 기존 경량 FastAPI image를 유지한다. 두 컨테이너를 합치거나 FastAPI lifespan에 수집 작업을 넣지 않는다. API health 이후 worker가 시작되지만 API는 데이터 갱신 완료를 기다리지 않는다.

프로세스 시작 → 소스별 저장일 이후 수치 보충·고정 모델 추론·DB 적재 → 뉴스 조회 날짜 이후 기간별 보충 → 미분류 Jev 요청·네 파일 저장 → 7일 대기 순서다. 오프라인 기간의 주간 실행 횟수를 재생하지 않고 날짜 범위를 합쳐 처리한다. refresh 상태는 `requests.json.refresh_state`, 뉴스 조회 완료 범위와 제한은 `news.json.selection_metadata.incremental`에 보관한다. 수치 source directory 잠금으로 CLI 수치 작업 충돌을 막으며 뉴스 조회·Jev 대기 중에는 이 잠금을 해제한다. 보조 소스를 포함한 수치 수집 또는 뉴스 조회 실패는 1시간 뒤 재시도한다. 뉴스 조회 체크포인트는 동시 실행에도 뒤로 돌아가지 않는다. Jev 원문 저장·재시도·비용 중단은 기존 archive 코드가 담당한다.

`/seed`와 `/seed-jev`는 읽기 전용 초기 자료이며 `/data/sources`와 `/data/jev`가 영구 작업 자료다. Jev 네 파일은 최초에 원자적으로 복사하고 기존 archive를 재시작 때 덮어쓰지 않는다. 최신 뉴스 CSV를 서빙 앙상블에 자동 채택하지 않는다. 소스별 API 범위·응답 상한 때문에 조회 성공과 전 세계 뉴스 완전 확보는 다르다. 이 구현의 컨테이너 실동작·GHCR/VM 검증 범위는 STATUS를 따른다.


## 2026-09-27 — 구조 리뷰 후 공용 코드 위치

기존 실행 경계는 유지한다. `ingestion.py`는 원천 수집 함수·공용 작업 목록(`collection_jobs`, `SOURCE_GROUPS`)·source 디렉터리 잠금을 가진다. 수집 전용 CLI와 `pipeline.collect`는 이 작업 목록을 함께 쓰되, 각 CLI의 기상 buffer와 증분 정책은 그대로 둔다. `pipeline.py`는 수집·추론·DB 적재 조합과 CLI 분기, `refresh.py`는 시작/주간 실행 조정, Docker entrypoint는 seed 준비만 담당한다. 공용 잠금 사용 때문에 스케줄러를 import하던 역방향 의존은 제거했다.

Jev의 원자적 JSON 쓰기는 기존 `_write_records`를 재사용하며 파일 이름·schema·원문·시각 필드는 바꾸지 않는다. api의 요청 처리는 DB 조회만 수행하고 API 이미지에 수집/ML 모듈을 추가하지 않는다. Vue에는 분석·DB 접근 로직을 넣지 않는다. PostgreSQL은 별도 저장 서비스다. 로컬 Compose는 pipeline 컨테이너를, Azure 배포는 기존 Actions Python 배치를 사용한다. 이 실행 위치 차이와 일정은 이번 리팩토링의 변경 대상이 아니다.
