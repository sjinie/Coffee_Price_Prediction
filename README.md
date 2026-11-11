# 커피 원두 가격 범위 예측

아라비카 커피 선물(KC=F)이 **5·20·60거래일 뒤 어느 가격 범위에 있을지** 매일 예측하고, 그 근거와 검증 성적을 함께 공개하는 1인 E2E 데이터 프로젝트입니다. 공개 데이터 수집부터 시점 규칙, 모델 검증, PostgreSQL 적재, API, 대시보드, 클라우드 운영까지 직접 만들고 운영합니다.

**[운영 대시보드](https://coffee-price-sjinie.koreacentral.cloudapp.azure.com/)** · [일일 배치 실행 기록](https://github.com/sjinie/Coffee_Price_Prediction/actions/workflows/daily.yml) · [API 상태](https://coffee-price-sjinie.koreacentral.cloudapp.azure.com/health) · [개발 기록(시행착오 30개)](docs/development_log.md) · [구조와 데이터 계약](docs/architecture.md)

[![운영 대시보드 첫 화면: 가격 차트와 5·20·60거래일 전망 카드](docs/images/dashboard.png)](https://coffee-price-sjinie.koreacentral.cloudapp.azure.com/)

*2026-11-02(KST) 운영 사이트 캡처. 이미지를 누르면 현재 대시보드로 이동합니다.*

## 한눈에 보기

| | |
|---|---|
| **무엇을** | 커피 선물 가격의 5·20·60거래일 뒤 로그수익률 **분포**를 매일 예측해 80% 가격 범위, 가운데 가격, 상승 확률, 매수 신호, 위험 수준을 보여 준다 |
| **왜** | 카페는 원두를 몇 주에서 몇 달 앞서 계약한다. 필요한 것은 정확한 가격 하나가 아니라 가격이 들어갈 범위와 오를 가능성이다 |
| **내 역할** | 1인. 수집·적재 파이프라인, 시점 규칙 설계, 모델 검증, API, 화면, 배포·운영 |
| **기간** | 2026-09-12 – 10-06 재설계·개발(모델 동결), 2026-10-05부터 매일 운영 중. 출발점은 2025년 학부 캡스톤(팀) |
| **스택** | Python 3.12 · pandas · scikit-learn · LightGBM · SciPy · PostgreSQL 17 · FastAPI · Vue 3 · Docker Compose · GitHub Actions · Azure VM |
| **규모** | 가격·거시 2005년~, 피처 67개, 산지 6곳 기상, 보관 뉴스 1,446건, 테스트 pytest 83개(DB 포함)·프론트 22개 |

**결과를 먼저 말하면**

- 기준선을 **분명히 넘은 예측**은 5·20일 변동성 하나다. HAR 모델이 직전 변동성보다 RMSE를 5일 25%(개발)·27%(보류), 20일 14%·20% 줄였다. 다만 보류 구간에서 예측과 실제 변동성의 상관은 0에 가깝고(5일 0.05, 20일 −0.02), 장기 평균만 써도 RMSE가 22%·8% 준다. 개선의 대부분은 변동성의 오르내림이 아니라 수준을 맞춘 데서 나왔다.
- 가격 방향과 범위는 **기준선을 넘지 못했다.** 서비스 분포 모델의 CRPS는 기준선(현재가 + HAR 폭)보다 5 / 20 / 60일 +0.2 / +1.4 / +2.1%(낮을수록 좋음)다. 이 성적을 화면에 그대로 표시한다.
- 그래서 이 프로젝트가 보여 주는 것은 높은 정확도가 아니라 **"그날 알 수 있던 정보만 쓰는 파이프라인"과 "기준선과 미리 정한 규칙으로 실험을 걸러 낸 검증 과정"**이다. 서비스 모델은 기준선을 넘지 못했지만 출력 일관성을 위해 채택했고, 그 성적을 그대로 공개한다.

## 화면

한 페이지 안에서 예측, 예측 과정, 데이터·모델 선택의 근거, 실시간 적중 기록을 차례로 보여 줍니다.

| 작동 방식: 그날 알 수 있던 값만 쓴다 | 데이터 탐색: 산지 기후를 52주 시계로 |
|---|---|
| ![기준일 2026-10-08에는 그날까지 알려진 가격만 쓰는 과정](docs/images/screen_asof.jpg) | ![브라질 산지의 강수와 최저·최고기온 편차를 연도 × 52주 원형 시계로 그린 화면](docs/images/screen_clock.jpg) |
| **결과: 기준선 대비 성적을 숨기지 않는다** | **적중 기록: 동결 뒤 실시간 예측만 따로 채점** |
| ![지평별 기준선 대비 CRPS 막대. 개발 구간은 모두 0보다 오른쪽(나쁨)](docs/images/screen_result.jpg) | ![실시간 예측의 지평별 채점 수, 방향 적중률, 가격 오차, 80% 범위 적중률](docs/images/screen_track.jpg) |

## 아키텍처

```mermaid
flowchart LR
  subgraph src["공개 데이터"]
    Y["Yahoo Finance<br/>KC=F · COP=X"]
    F["FRED · ALFRED"]
    E["NOAA ENSO"]
    W["NASA POWER<br/>산지 6곳"]
    G["Google News RSS"]
  end
  subgraph lab["연구 (노트북 01–07)"]
    NB["walk-forward 검증<br/>coffee 패키지 공용"]
    ART["model_artifacts/2026-10-06<br/>동결 모델 + metadata"]
  end
  subgraph gha["GitHub Actions (매일)"]
    P["coffee.pipeline daily<br/>수집 → 피처 → 예측 → 뉴스 분류"]
  end
  J["TypeSafe Jev<br/>(Vercel AI Gateway)"]
  subgraph vm["Azure VM · Docker Compose"]
    DB[("PostgreSQL 17")]
    API["FastAPI<br/>읽기 전용 계정"]
    WEB["Vue 3 + nginx"]
    C["Caddy (HTTPS)"]
  end
  src --> NB --> ART --> P
  src --> P
  P <--> J
  P -- "SSH 터널" --> DB
  U["사용자"] --> C --> WEB --> API --> DB
```

- 노트북과 일일 파이프라인이 **같은 피처·모델 코드**(`coffee` 패키지)를 쓴다. 노트북의 2026-10-02 예측과 파이프라인이 DB에 쓴 값이 같은지 확인했다.
- 일일 배치는 GitHub Actions에서 06:17 UTC에 예약 실행한다(실제 시작은 큐 사정으로 몇 시간 늦을 수 있고, 마감된 전 거래일 종가만 쓴다). SSH 터널로 VM의 PostgreSQL에 쓰고, DB 포트는 VM 밖에 열지 않는다.
- API는 SELECT 권한만 있는 계정으로 접속하고, 이미지에 pandas·모델 라이브러리를 넣지 않았다(274MB).
- 웹·API 배포는 커밋을 `git archive`로 VM에 보내 커밋별 이미지를 빌드하는 수동 배포다. 백업·되돌리기 절차는 [architecture.md](docs/architecture.md#배포와-되돌리기)에 있다.

## 데이터 엔지니어링에서 내린 판단

### 1. 모든 값에 "언제 알 수 있었나"를 붙였다

학부 캡스톤은 병합 전에 결측을 앞 값으로 채워 미래 정보가 섞였고, 테스트 손실을 보며 학습률을 정했다. 다시 만들면서 소스마다 실제로 쓸 수 있게 된 시점을 정해 as-of로 결합했다.

| 자료 | 수집 | 언제부터 쓰는가 |
|---|---|---|
| KC=F 종가 (Yahoo) | yfinance | 당일 23:00 UTC 마감 이후 |
| 헤알·금리·WTI·운임 (FRED·ALFRED) | REST API | 최초 공개일 다음 날. ALFRED 기록이 없는 과거 구간은 FRED 현재값에 공개 지연을 가정(수정치가 섞일 수 있음) |
| ENSO (NOAA) | 텍스트 파일 | 3개월 계절이 끝난 다음 달 11일 |
| 산지 강수·기온 (NASA POWER) | REST API | 관측일 + 4일 |
| 뉴스 점수 (Google News RSS → LLM 분류) | RSS | 분류가 끝난 뒤 첫 거래일 마감 |

- 기후 평년값은 직전 10년의 같은 시기로 계산하고 그해 자료는 넣지 않는다. 목표일이 학습 구간을 넘는 행은 학습에서 뺀다(embargo).
- "기준일 이후 원자료를 모두 바꿔도 기준일까지의 피처는 같다", "늦게 공개된 과거 관측은 먼저 공개된 값을 고치는 데 쓰이지 않는다" 같은 시점 성질을 pytest로 고정했다.

### 2. 예측 기록은 덮어쓰지 않는다

화면은 과거 예측의 적중 기록을 보여 준다. 예측을 나중에 고칠 수 있으면 그 기록을 믿을 수 없다.

- 가격은 Yahoo가 최근 값을 고치므로 덮어쓰고(upsert), 예측은 `ON CONFLICT DO NOTHING`으로 한 번만 쓴다.
- 실시간(`live`)과 소급 계산(`backfill`) 예측을 따로 저장하고 따로 채점한다. 둘이 섞여 계산되던 문제를 리뷰에서 찾아 테스트로 막았다.
- 가격 수집이 늦어 기대한 기준일보다 오래된 날이면 예측을 저장하지 않는다. 지난 기준일의 예측을 나중에 `live`로 남기면 "그날 처음 낸 예측"이라는 뜻을 잃는다.

### 3. 실패는 등급을 나눈다

다시 쓰기 전의 첫 파이프라인에서 일일 배치가 5번 연속 실패로 끝난 적이 있다(2026-09-27–10-01). 4번은 보조 뉴스 사이트가 HTTP 403을 돌려주자 같은 단계의 수집까지 멈춘 것이고, 1번은 SSH 시간 초과였다. 예측은 저장됐는데 실패로 표시됐다.

- 종료 코드를 0 성공 / 3 경고 / 1 실패로 나눴다. 일부 소스·뉴스가 실패한 경고는 예측을 저장하고, 가격이 늦은 날의 경고는 예측을 저장하지 않는다. 뉴스 실패가 예측을 막지 않는다는 것을 테스트로 고정했다.
- 경고 이유는 Actions 주석과 공개 상태 API의 `message`에 남는다. 뉴스 분류는 하루 2건, 누적 비용 1 USD 상한을 지킨다.

### 4. 원자료 오류는 실제 자료로 규칙을 정해 고친다

Yahoo 페소 환율에 2013-07-12 종가 1,910 대신 3.67 같은 잘못된 시세가 있었고, 이 값 하나가 분포 모델 첫 버전의 60일 예측 폭을 σ = 61,083까지 키웠다.

- 흔한 IQR 1.5배 규칙을 실제 자료에 적용해 보니 페소 일간 변화의 7.9%, 강수 변화의 31%, 2021년 서리 주간의 실제 급등까지 지웠다.
- 그래서 "하루 변화가 그 전까지의 변화로 만든 IQR 울타리 20배 밖이면 직전 값"으로 정했다(`features.clean_sources`). 바뀐 값은 페소 17개와 WTI 2020-04-20 하나다. 커피 가격은 예측 대상이라 고치지 않는다.

## 모델링과 검증

**예측 대상.** 거래일 t 종가 기준 h거래일 뒤 로그수익률 y = ln(P<sub>t+h</sub> / P<sub>t</sub>)의 분포. 캡스톤은 14일 뒤 가격 수준을 맞혔지만, 구매 결정에는 범위와 방향이 필요해 대상을 바꿨다.

**평가 설계**

| 구간 | 기간 | 쓰임 |
|---|---|---|
| 개발 | 2012–2021 | 해마다 그 전까지로 학습해 그해를 예측(walk-forward 10회). 후보·지표·판단 규칙은 여기서 정한다 |
| 보류 | 2022–2025 | 같은 규칙으로 한 번만 평가. 이전 연구에서 본 적이 있어 "미사용 test"라고 부르지 않는다 |
| 사후 확인 | 2026– | 2025년까지 학습한 동결 모델을 그대로 적용 |

- 모델 설정(모델·피처 묶음·축소 계수·벌점)은 평가 연도마다 그 이전 해들의 성적으로 다시 고른다(중첩 검증).
- 기준선: 수익률은 현재가 유지(Naive), 상승 확률은 학습 구간의 상승 비율, 변동성은 직전 변동성, 분포는 현재가 + HAR 변동성 폭(B1).
- h일 수익률은 날짜가 겹치므로 블록 bootstrap(블록 = h, 1,000회)으로 95% 구간을 낸다.
- 후보와 판단 규칙은 계산 전에 노트북 첫 셀에 적고 SHA-256을 기록했다(사전 등록). 노트북을 두 번 실행해 결과 JSON과 모델 파일 해시가 같은지 확인했다.

**서비스 모델** (`coffee.models.DistributionModel`, 노트북 07): 지평마다 y = μ(x) + σ(x)·Z 하나를 CRPS + L2 벌점으로 학습한다. 중심 μ는 절편 없는 피처 선형 결합, 폭 σ는 HAR 입력 3개 + 피처, 모양 Z는 학습에 쓰지 않은 해의 표준화 잔차다. 80% 범위·가운데 가격·상승 확률·매수 신호·위험 수준이 모두 이 분포 하나에서 나와 서로 어긋나지 않는다.

### 주요 결과

| 질문 | 결과 | 판단 |
|---|---|---|
| 수익률 크기를 맞히는가 | 03의 모든 모델이 개발 구간에서 Naive보다 RMSE가 컸다(5일 LightGBM +0.9%, 20일 LightGBM +3.8%, 60일 Ridge +4.9%, 20일 단순 LSTM +17.1%). 재설계(03b) 후 0.0 / −0.5 / +3.6%, 95% 구간 [−0.5, +0.5] / [−2.6, +1.7] / [+0.9, +6.5] | Naive보다 낫다고 할 지평 없음. 개선의 대부분은 예측을 현재가 쪽으로 줄인 계수에서 나옴 |
| 방향을 맞히는가 | 서비스 분포 모델의 상승 확률 AUC 개발 0.53 / 0.56 / 0.64, 보류 0.52 / 0.58 / 0.63. 20일 신호대로 산 단가는 정기 구매 대비 개발 −0.16%, 보류 −0.79%(미래를 알았다면 −3.3%) | 약한 신호. 구매 이득은 작고, 보류 구간은 이미 본 구간이라 판단에 쓰지 않음 |
| 변동성을 맞히는가 | 직전 변동성 대비 RMSE 5일 −25%(개발) / −27%(보류) / −21%(2026), 20일 −14% / −20% / −18%. 60일은 보류 +2%. 보류 구간에서 예측과 실제의 상관은 5일 0.05, 20일 −0.02이고, 장기 평균만으로도 −22% / −8% | 기준선을 넘은 유일한 예측. 다만 대부분 변동성 수준을 맞춘 효과다 |
| 뉴스가 돕는가 | 점수는 기사 당일 수익률과 상관 0.21, 이후 수익률과는 ±2 표준오차 안. 사전 등록 실험 05·05b 모두 채택 규칙 미달 | 별도 보정으로는 기각. 분포 모델에는 희소 입력 1개로만 들어감 |
| 앙상블이 돕는가 | 60일 DLinear + LightGBM 평균이 검증 RMSE −5.76%, 테스트 +2.63% | 기각. 앙상블로 성능 문제를 덮지 않음 |

**서비스 분포 모델 `2026-10-06`** (개발 구간 2012–2021)

| 지평 | 기준선(B1) 대비 CRPS | 95% 구간 | 80% 범위 적중률 (B1) | 상승 확률 AUC | 매수 기준 |
|---|---|---|---|---|---|
| 5일 | +0.2% | [−0.2, +0.6] | 82.7% (79.8%) | 0.53 | 없음(늘 보류) |
| 20일 | +1.4% | [+0.1, +2.6] | 82.4% (79.9%) | 0.56 | 52% |
| 60일 | +2.1% | [−0.4, +4.4] | 82.1% (81.6%) | 0.64 | 없음(늘 보류) |

- 기준선보다 낫다고 할 지평은 없고 20일은 조금 나쁘다. 출력이 서로 어긋나지 않는 모델 하나로 바꾸기로 결정했고, 이 성적을 화면에 그대로 표시한다.
- 20일 매수 기준은 같은 개발 자료에서 고른 것이고 늘 구매하는 것과의 차이가 작아(신호 적중률 54.3% 대 53.9%) 우위가 검증된 신호는 아니다.
- 보류 구간(재사용)은 −0.3 / −1.1 / −2.8%, 2026년 사후 확인은 +0.0 / +0.4 / −8.8%다. 이미 본 구간이라 판단에 쓰지 않는다. 2026년 80% 범위 적중률은 75.5 / 61.5 / 76.0%로 목표보다 낮다.
- 첫 등록(v1)은 실패했다. 개발 CRPS가 5일 +0.6%, 20일 +1.9%였고 60일 폭이 터졌다. 원인 셋(페소 오류 시세, 과거 추세를 미래에 적용한 절편, 너무 좁은 잔차 모양)을 고쳐 v2로 다시 등록했다. v1을 본 뒤 고쳤으므로 개발 구간은 두 번째로 본 것이다.
- 실시간 예측(동결 이후, 처음 보는 자료)은 2026-11-11 기준 20일 지평 26건 중 6건이 채점됐고 방향은 6건 모두 틀렸다. 그중 매수 신호 4건도 모두 가격이 내렸다. 우열을 판단하기에는 표본이 부족하다.

![분포 모델의 단계별 결과](docs/images/distribution_ladder.png)

## 시행착오에서 배운 것

- **기준선을 먼저 정한다.** 캡스톤의 LSTM은 가격 RMSE 6.70으로 좋아 보였지만 기준선이 없었다. 같은 조건으로 다시 재니 단순 LSTM은 현재가 유지보다 20일 RMSE가 17.1% 나빴다.
- **평가 코드에도 버그가 있다.** 연도별 평가가 해마다 연말 기준일을 빼고 있었고(외부 리뷰), 고치자 60일 결론이 바뀌었다. 방향 Brier의 기준선을 연도별 값이 아니라 평균 하나로 채점하던 것도 고쳤다.
- **신호가 약하면 크게 예측할수록 틀린다.** 수익률 재설계의 개선은 대부분 예측을 현재가 쪽으로 줄인 데서 나왔고, 60일은 후보를 넓힐수록 나빠졌다.
- **리뷰도 검증한다.** 두 AI 모델에게 전체 검토를 받아 지적을 코드·원자료로 대조했고, 사실이 아닌 2건은 버리고 나머지를 고쳤다. 동작을 바꾼 곳은 테스트를 더하거나 고쳐 고정했다(개발 기록 29).
- **AI 도구로 빠르게 만든 코드는 설명할 수 있을 때까지 다시 쓴다.** 1차 버전은 서비스 코드 7,500줄에 채택하지 않은 실험 경로가 섞여 있었다. 기록은 남기고 코드는 다시 썼다(지금 `coffee` 패키지는 SQL 포함 약 2,300줄).

자세한 과정은 [개발 기록](docs/development_log.md)에 시간 순서로 남겼습니다.

## 실행 방법

비밀값은 환경 변수로만 넣습니다. `.env.example`을 `.env`로 복사해 값을 채웁니다(`.env`는 커밋하지 않음).

```dotenv
FRED_API_KEY=          # 경제지표 수집
AI_GATEWAY_API_KEY=    # 뉴스 분류(Vercel AI Gateway → TypeSafe Jev)
DATABASE_URL=          # Mac에서 파이프라인·API를 직접 실행할 때
POSTGRES_PASSWORD=     # docker compose
WEB_PORT=8080
CORS_ORIGINS=          # 다른 출처의 프론트엔드가 API를 부를 때만
```

표시: ✅ 2026-11-11에 실행해 확인 / ⚠️ 이번에 실행하지 않음

```bash
# 환경 (Python 3.12, 프로젝트 밖 가상환경)
uv venv --python 3.12 "$HOME/.virtualenvs/coffee-price-prediction"                                  # ✅
uv pip install --python "$HOME/.virtualenvs/coffee-price-prediction/bin/python" -r requirements.txt  # ✅
source "$HOME/.virtualenvs/coffee-price-prediction/bin/activate"

python -m pytest          # ✅ DB 없이 68 passed, 15 skipped
                          # ✅ COFFEE_TEST_DATABASE_URL을 주면 DB 테스트 포함 83 passed (PostgreSQL 17)
python -m coffee.sources  # ⚠️ 원자료 수집 → data/sources/*.parquet (FRED_API_KEY 필요)
```

노트북 재실행(07이 고른 설정을 06이 동결하므로 07 다음에 06):

```bash
for nb in 01_data_and_eda 02_feature_selection 03_return_direction 03b_return_redesign 04_volatility 05_news_signal 05b_news_correction 07_distribution_model 06_final_model; do
  jupyter nbconvert --to notebook --execute --inplace --ExecutePreprocessor.kernel_name=coffee-price-prediction "notebooks/$nb.ipynb"
done                       # 03·07은 ✅, 나머지는 ⚠️
python -m coffee.portfolio # 화면용 frontend/src/research-data.json 갱신 ✅
```

로컬 E2E(Docker Compose: PostgreSQL + API + 대시보드):

```bash
docker compose up -d --build                # ⚠️ http://localhost:8080 (이미지 빌드는 CI에서 확인)
docker compose run --rm pipeline migrate    # ⚠️ 스키마 적용
docker compose run --rm pipeline backfill   # ⚠️ 가격·보관 뉴스·2026년 예측 적재 (data/ 필요)
docker compose run --rm pipeline daily      # ⚠️ 매일 실행하는 갱신
```

프론트엔드만:

```bash
cd frontend && npm ci
npm test        # ✅ 22 passed
npm run build   # ✅
API_TARGET=https://coffee-price-sjinie.koreacentral.cloudapp.azure.com npm run dev   # ⚠️ 운영 API를 읽기 전용으로 붙여 확인
```

수집 데이터(`data/`)는 저장소에 없습니다. `backfill`은 보관 뉴스(`data/jev/responses.json`)가 없으면 아무것도 쓰기 전에 멈춥니다.

## 디렉터리 구조

```
coffee/              수집·피처·평가·모델·뉴스·DB·파이프라인·API (노트북과 서비스가 같이 씀)
  sources.py           Yahoo·FRED/ALFRED·NOAA·NASA 수집, 소스마다 Parquet 하나
  features.py          거래일 축, as-of 결합, 이상치 규칙, 피처 67개, 타깃
  evaluate.py          embargo, walk-forward, CRPS, 블록 bootstrap, 중첩 검증
  models.py            DistributionModel, 기준선, 구매 신호, 모델 저장(해시 검사)
  jev.py, news.py      Google News RSS 수집, Jev 분류, 거래일별 뉴스 점수
  db.py, schema.sql    SQL은 이 두 파일에만 있음
  pipeline.py          migrate · backfill · daily · weather, 종료 코드 0/3/1
  api.py               읽기 전용 FastAPI
  portfolio.py         화면용 연구 자료 생성
notebooks/           01–07 분석 노트북, results/ 결과 JSON
model_artifacts/     동결 모델(2026-10-06)과 metadata
frontend/            Vue 3 대시보드(차트 라이브러리 없이 SVG), node --test
deploy/              VM compose, Caddy, DB 설정·일일 실행 스크립트
.github/workflows/   ci.yml(pytest·프론트·Docker 빌드), daily.yml(일일 배치)
configs/settings.yaml 기간·지평·소스·산지 좌표·모델 버전
tests/               pytest (시점 성질, 평가, 파이프라인, DB 계약)
docs/                architecture.md(구조·데이터 계약), development_log.md(시행착오·결정)
data_code/old_code/, docs/old_docs/   학부 캡스톤과 이전 버전 기록
```

## 한계와 다음 단계

**한계**

- 수익률·방향·범위 예측은 기준선을 넘지 못했다. 서비스 모델의 가치는 정확도보다 일관된 출력과 정직한 성적 표시에 있다.
- Yahoo KC=F는 근월물 연결 가격이라 만기 교체 때의 가격 점프가 타깃에 섞여 있다.
- 2022–2026년은 개발 중 이미 본 구간이다. 진짜 평가는 동결 이후 쌓이는 실시간 예측뿐이고 아직 짧다.
- 2022–2025년 뉴스는 2026년에 소급 분류했다. 점수와 이후 수익률 사이에 선형 관계는 보이지 않았지만, 분류 모델의 학습 자료 기간은 확인하지 못했다.
- 배치용 SSH 계정은 포트 포워딩을 127.0.0.1:15432로만 허용하지만 명령 실행까지 막지는 않는다.

**다음 단계**

- 운영 DB에 동결 모델의 소급 예측(2026-01–)을 적재해 화면의 소급 성적 표를 채운다.
- 실시간 예측과 뉴스 점수가 쌓이면 범위·신호 적중률과 뉴스 효과를 같은 사전 등록 규칙으로 다시 검증한다.
- 60일 후보를 줄인 설정은 다음 버전에서 새로 등록해 시험한다.
- 배치용 SSH 계정을 rsync 전용 강제 명령(`rrsync`)으로 좁힌다.

이 프로젝트는 투자 조언이 아닙니다.
