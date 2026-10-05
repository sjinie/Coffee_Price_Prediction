# 구조와 데이터 계약

이렇게 정한 이유는 [개발 기록](development_log.md)에 있다.

## 데이터 흐름

```
수집(sources.py) ─→ data/sources/*.parquet ─→ 피처(features.py) ─→ 거래일 표
                                                               │
                    노트북 01–07: walk-forward 비교(evaluate.py, models.py)
                                                               │
                         model_artifacts/<버전>/distribution (07의 설정을 노트북 06에서 동결)
                                                               │
뉴스(jev.py) ─→ Jev 분류 ─┐                                     ▼
                         └─→ pipeline.py daily/backfill ─→ PostgreSQL ─→ api.py ─→ frontend/
```

노트북과 파이프라인은 같은 `coffee.features.build_dataset`과 `coffee.models`를 쓴다. 학습할 때와 서비스할 때 피처 계산이 달라지는 일을 구조로 막기 위해서다. 노트북 06의 2026-10-02 예측과 파이프라인이 DB에 쓴 값이 같은지 확인했다.

## 모듈

| 파일 | 역할 |
|---|---|
| `config.py` | `configs/settings.yaml`을 읽고 경로·지평·기간을 상수로 둔다 |
| `sources.py` | Yahoo·FRED/ALFRED·NOAA·NASA 수집. 소스마다 Parquet 하나, 마지막 값 7일 전부터 다시 받아 합친다 |
| `features.py` | 거래일 축, as-of 결합, 원자료 이상치 정리(`clean_sources`), 피처 66개(기존 46개 + 03b 후보 20개)와 뉴스 점수(`add_news`), 날짜 단위 평년값, 타깃 |
| `evaluate.py` | 학습 행 선택(embargo), walk-forward 분할, 지표(CRPS 포함), 블록 bootstrap, 해마다 후보를 고르는 중첩 검증(`select_by_year`는 RMSE와 λ, `select_by_score`는 CRPS), 표본 밖 표준화 잔차(`walk_forward_residuals`) |
| `models.py` | 서비스의 분포 모델(`DistributionModel`), 이전 연구의 기준선·Ridge·LightGBM·DLinear·분류기와 수익률 설정, 구매 신호, 모델 저장(해시 검사, NaN은 null) |
| `news.py` | 보관한 Jev 분석 읽기, 거래일별 뉴스 점수(live·research 두 시점) |
| `jev.py` | Google News RSS 수집, 하루 2건 선정, Jev 배치 분류 |
| `db.py`, `schema.sql` | SQL은 이 두 파일에만 있다. `db.py`는 API 이미지용으로 pandas 없이 동작한다 |
| `pipeline.py` | `migrate`, `backfill`, `daily`, `weather` 명령과 종료 코드 |
| `api.py` | 읽기 전용 FastAPI |
| `portfolio.py` | 화면용 연구 자료(`frontend/src/research-data.json`) 생성. 예측 방법을 바꾸면 다시 실행한다 |

화면(`frontend/src/`)은 포트폴리오 한 페이지다. 구성과 콘텐츠 계약은 아래 '화면'에 있다. 차트 눈금·경로, 주간 집계, 연간 시계 격자, 적중률, 위험 등급 같은 계산은 `lib.js`에 모아 `node --test`로 검사한다. 차트 라이브러리 없이 SVG로 그린다.

처음에는 최근 400일 가격을 표시하고, ‘전체 2005–’를 선택할 때만 `/api/prices?days=8000`을 불러 재사용한다. 전체 보기의 주간 종가는 1월 1일부터 7일씩 나눈 주의 마지막 관측이다(마지막 51주는 8–9일). 마지막 관측이 결측이면 이전 가격으로 대체하지 않는다. 가격·예측의 결측은 선과 띠를 끊어 표시하고, 예측 부채꼴은 가격 표 종가가 아닌 예측 행의 `origin_close`에서 시작한다. 과거 예측의 x축은 `target_date`, 상승 확률의 x축은 `origin_date`다. 개별 API가 실패해도 나머지 자료는 표시한다.

## 거래일과 타깃

- 거래일 축은 NYSE 영업일에 커피 선물만 거래한 2일(2018-12-05, 2025-01-09)을 더한 것이다. 휴장일은 만들지 않는다.
- 기준 시각은 거래일 23:00 UTC(뉴욕 장 마감 이후)다.
- `y_h = ln(P[t+h] / P[t])`, `target_date_h`는 h번째 다음 거래일, `v_h`는 t+1…t+h 일간 로그수익률 표준편차의 로그다.
- 가격이 없는 날(Yahoo 결측)은 채우지 않는다. 피처는 마지막으로 알려진 종가로 계산하고 타깃은 실제 종가가 있는 날만 만든다.

## 시점 계약

| 자료 | 언제부터 쓴다고 보는가 | 오래된 값 허용 |
|---|---|---|
| KC=F 종가 | 당일 | – |
| COP=X | 다음 날 | 14일 |
| ALFRED(헤알·금리·WTI·운임) | 최초 공개일 다음 날 | 일별 14일, 월별 80일 |
| ALFRED 이전 기간 | FRED 현재값 + 가정 지연(헤알 7일, 금리 1일, WTI 7일, 운임 45일), `release_assumed` 표시 | 같음 |
| ENSO ONI | 3개월 계절이 끝난 다음 달 11일 | 80일 |
| NASA 기상 | 관측일 + 4일 | 4일 |
| 뉴스(서비스) | `available_at`(분석 완료) 이후 첫 23:00 UTC | – |
| 뉴스(연구) | 발행 + 1일(가정). 2022–2025년 기사는 2026년에 소급 분류해서 실제 이용 시각이 없다 | – |

허용 기간보다 오래된 값은 결측으로 둔다. 기상 편차는 직전 10년의 같은 달(그해 제외)과 비교한다.

원자료의 오류 시세는 `features.clean_sources`가 피처를 만들기 전에 바꾼다. 환율·WTI·운임·ENSO·기온은 하루 변화(환율·운임은 로그 변화)가 그 전까지 받아들인 변화로 만든 IQR 울타리 20배 밖이면 직전 값을 쓴다. 앞에서부터 차례로 판단하므로 그날까지의 값만 쓰고, 하루짜리 오류는 그날만 바뀐다. 울타리는 변화가 250개(월별 36개, 기상 365개) 쌓인 뒤부터 쓴다. 커피 가격·금리·강수는 그대로 두고 강수는 음수만 바꾼다. 지금 자료에서 바뀌는 값은 페소 17개와 WTI 2020-04-20 하나이고, 피처로는 `cop_chg_20`·`cop_chg_60`의 34행씩이다(WTI 그날 값은 ALFRED 공개 시점 때문에 원래 피처에 쓰이지 않았다). 2006년 3–4월 페소는 두 수준(약 2,170과 2,290)을 오가는 자료라 6개가 먼저 받아들인 낮은 쪽으로 바뀐다.

## 피처

| 묶음 | 개수 | 내용 |
|---|---|---|
| 가격 | 8 | 1·5·20·60일 수익률, 5·20·60일 변동성, 60일 이동평균 괴리 |
| 거시 | 5 | 헤알·페소·금리·WTI 20일 변화, 해상 운임 60일 변화 |
| 기후 | 29 | 산지 6곳의 30일 강수·기온 편차, 90일 표준화 강수, 고온일 편차, 브라질 한파 편차, ENSO와 3개월 변화 |
| 주기 | 4 | 월, 24개월 해거리(7월 시작 작물년도)의 sin·cos |
| 단기 위험 | 2 | 5·20일 변동성 ÷ 60일 변동성 |
| 기후 요약 | 12 | 브라질 3곳 평균(한파는 가장 추운 곳)·콜롬비아 3곳 평균의 z(90일 강수, 30일 기온, 30일 고온일, 7일 최저기온), 기준을 넘은 날 수(브라질 90일 강수 z ≤ −1.5, 콜롬비아 ≥ +1.5, 브라질 한파 z ≤ −2), ENSO 상태(ONI ±0.5), 서리철(6–8월) 한파 z, 개화기(9–10월) 30일 강수 z |
| 거시 장기 | 6 | 헤알 60·120일, 페소·금리·WTI 60일, 운임 120일 변화 |
| 뉴스 | 1 | 거래일별 뉴스 점수(`news_score`). 기사가 없는 날과 수집 전(2022년 이전)은 0 |

앞의 네 묶음(46개, `FEATURE_GROUPS`)과 행 선택 기준(`ALL_FEATURES`)은 그대로이고, 다음 세 묶음(`EXTRA_GROUPS`)은 03b의 수익률 후보로, 뉴스(`NEWS_FEATURES`)는 07의 분포 모델 입력으로 더했다. 기후 요약의 평년값은 직전 10년의 같은 날짜 ±15일(`daily_climatology`)이고 표준편차로 나눈 z를 쓴다. 같은 달 평년값(기존 기후 29개)은 달이 바뀌는 날 기준이 한 번에 뛴다.

서비스의 분포 모델은 67개 모두를 중심과 폭에 넣고, 폭에는 HAR 입력(`log_vol_5/20/60`)을 벌점 없이 더한다. 행은 03b와 같은 `ALL_FEATURES + EXTRA_FEATURES` 기준이다(뉴스는 결측이 없다).

## 학습과 평가

- 학습 시작 2006년. 개발 구간 2012–2021년은 해마다 그 전까지로 학습해 그해를 예측한다(10회). 보류 구간 2022–2025년도 같은 방식으로 한 번만 평가했다.
- 학습 구간 끝을 넘는 목표일의 행은 학습에서 뺀다(embargo). 스케일러는 각 학습 구간에서 fit해 모델과 함께 저장한다.
- 평가 행은 기준일이 그해인 행을 모두 쓰고 목표일은 평가 구간 끝(개발 2021-12-31, 보류 2025-12-31)까지 허용한다. 그해 끝에서 자르면 연말 기준일이 해마다 빠진다(`evaluate.fold_rows`).
- 모든 모델은 직전 60거래일 피처가 완전한 같은 행으로 비교한다. 03b의 수익률 후보는 새 피처까지 모두 있는 행(`ALL_FEATURES + EXTRA_FEATURES`)을 써서 행이 1.6% 적다(페소·WTI 공백이 60·120일 변화로 길게 번진다).
- 수익률 모델은 중첩 검증으로 고른다(03b). 후보마다 2009–2026년을 해마다 그 전까지로 학습해 예측해 두고, 평가 연도 Y마다 2009 … Y−1년 예측(목표일 Y−1년 말까지)으로 후보와 λ = Σpy/Σp²([0, 1])를 고른다. 어느 후보도 Naive보다 낫지 않으면 Naive를 쓴다.
- 서비스의 분포 모델(07)은 y = μ(x) + σ(x)·Z다. μ = w·x(절편 없음), log σ = b + u·HAR + v·x를 정규분포 CRPS의 합 + L2 벌점으로 함께 맞춘다(L-BFGS). 피처는 학습 행으로 표준화해 ±5에서 자르고, 서빙 때 빠진 피처는 표준화 값 0으로 본다. 모양 Z는 같은 후보의 표본 밖 표준화 잔차(목표일이 Y−1년 말 이전)에서 중앙값을 0으로 맞춘 것이다. 벌점 조합(α_μ, α_σ ∈ {10², 10³, 10⁴, 10⁵})은 평가 연도마다 안쪽 검증의 평균 CRPS로 고르고(`select_by_score`), 서비스 설정은 Y = 2026의 선택이다. 매수 기준은 개발 구간에서 신호 빈도 20% 이상이고 신호 적중률이 같은 날 늘 구매했을 때보다 높은 값 중 가장 적중률이 높은 것이며, 없으면 null(신호 없음)이다.
- 분포 모델의 기준선은 B1(중심 0, 폭 = k · HAR 예측 일간 변동성 · √h인 정규분포)과 B2(이전 서비스: 03b 수익률 × λ, 같은 폭, 03의 분류기)다. 사전 등록 v1(절편 있음, 학습 행 잔차 모양)은 실패했고 v2를 다시 등록했다(개발 기록 27).
- 유의성은 원형 블록 bootstrap(블록 = h, 1,000회, seed 42)으로 본다.

## 서비스 출력

| 출력 | 계산 | 지평 |
|---|---|---|
모든 출력은 지평마다 분포 모델 하나(`model_artifacts/<버전>/distribution/h{5,20,60}.joblib`)에서 나온다. DB 예측 열은 이전과 같다.

| 출력(DB 열) | 계산 | 지평 |
|---|---|---|
| 80% 가격 범위(`price_low`, `price_high`) | `종가 · exp(q10)`, `종가 · exp(q90)`. q는 분포의 분위수 | 5·20·60 |
| 가운데 가격(`predicted_price`, `predicted_return`) | `종가 · exp(q50)`. 맞히려는 값이 아니라 범위의 중심 | 5·20·60 |
| 상승 확률(`prob_up`) | 분포 가운데 로그수익률 > 0의 비율. 가운데 가격의 방향과 늘 맞는다 | 5·20·60 |
| 구매 신호(`signal`) | 확률 ≥ 기준이면 `buy`, ≤ 1−기준이면 `wait`, 그 사이는 `hold`. 기준이 null인 지평(5·60일)은 늘 `hold`, 20일 기준 52% | 5·20·60 |
| 예측 변동성(`predicted_vol`) | 분포의 표준편차(σ × 모양의 표준편차) × √(252/h), 연율 | 5·20·60 |
| 위험 수준(`vol_percentile`) | 최근 756거래일의 폭 σ 가운데 오늘 값의 백분위 | 5·20·60 |

분포 모델은 개발 구간에서 기준선(현재가 + HAR 폭)보다 낫다고 할 수 없고 20일은 조금 나쁘다. 그래도 출력이 서로 어긋나지 않는 하나의 모델로 바꾼 것은 사용자 결정이고(개발 기록 27), 지평별 검증 성적을 metadata(`distribution.horizons[h].dev`, `.holdout`, `.forward`: `crps_vs_b1_pct`와 95% 구간, `crps_vs_b2_pct`, `coverage80`, `width80_pct`, `auc`, `brier`, 신호 빈도·적중률)에 넣어 화면에 함께 표시한다. 지평 항목에는 모델 이름(`algorithm`), 피처(`features`), 매수 기준(`threshold`, 없으면 null), 벌점(`alpha_mean`, `alpha_scale`), 분위(`levels`), 모양 잔차 수(`shape_rows`), 이전 서비스의 같은 지표(`b2`)와 고른 방법(`selection`)도 적는다. 정의되지 않는 값(신호가 없는 지평의 적중률)은 null이다.

화면은 활성 모델의 metadata에서 매수 기준, 모델 이름, 검증 성적, 피처 목록을 읽는다. 모델 이름은 지평 항목, 묶음 순서로 찾는다(`lib.modelFor(metadata, 'distribution', h)`). 적중 기록은 실시간(`live`)과 소급(`backfill`) 예측을 나눠 따로 계산하고, 가운데 가격의 방향 적중률, 가격 오차(가운데 대 현재가 유지), 범위 적중률, 신호 적중률과 그 기준선(같은 신호일에 늘 '구매'라고 했을 때의 적중률)을 보여 준다.

## 화면

한 페이지를 위에서 아래로 읽는다: 첫 화면 → 작동 방식 → 왜 이렇게 만들었나 → 전체 보기 → 지금까지 확인한 것 → Q&A → 연락처.

| 구역 | 컴포넌트 | 자료 |
|---|---|---|
| 첫 화면(검정 지면, 화면 높이) | `PriceChart`(`mode="hero"`), `ForecastCards` | 최근 400일 가격, 지평별 이력·최신 예측. 카드에는 테두리가 없고 선택한 지평만 위쪽 굵은 선으로 표시한다. 카드를 누르면 차트 지평이 바뀐다 |
| 작동 방식 | `HowItWorks`, `PriceChart`(`mode="story"`) | 스파크라인은 `research-data.json`, 단계 그림은 20일 이력. 문장 다섯(모았다 → 알려진 날부터만 썼다 → 예측했다 → 한 장의 차트로 그렸다(읽는 법 범례) → 매일 갱신된다)이 스크롤에 따라 하나씩 쌓이고, 화면 가운데를 지나는 문장이 그림의 단계를 정한다 |
| 왜 이렇게 만들었나 | `WhySection`, `WhyChapter`와 차트들(`DotPlot` 등) | 장 순서: 문제 정의 → 데이터 탐색 → 피처 선택 → 뉴스 분석 → 평가 → 결과 → 시행착오 → 의사결정. 장마다 발견 한 문장 → 그림 2~3개 → 작은 글씨의 질문·방법·판단. 전체 가격(`/api/prices?days=8000`), 산지 기상(`/api/weather`), metadata, `research-data.json`, `research.js` |
| 전체 보기 | `PriceChart`(기본 `mode="explore"`), `NewsPanel` | 지평·기간 전환, 상승 확률 칸, 표 보기, 최근 30일 뉴스 |
| 지금까지 확인한 것 | `WrapUp`(`TrackRecord`, `StatusPanel`) | 결론 네 줄, 실시간·소급 적중 기록, 한계, 모델과 실행 상태 |
| Q&A | `App.vue` | 예상 질문마다 답, 확인할 곳, 다음 대응(`research.js`의 `QA`) |

콘텐츠 계약: 예측 방법에 따라 달라지는 화면 내용은 세 곳에서만 온다.

1. API와 모델 metadata: 예측, 매수 기준, 분포 모델의 구간별 검증 성적(CRPS, 80% 범위 적중률, AUC, 신호 적중률), 모델 이름, 피처. 화면이 저절로 따라간다.
2. `frontend/src/research-data.json`: `python -m coffee.portfolio`가 설정·피처 정의·노트북 결과 파일로 만든다(평가 구간, 피처 묶음, 뉴스 시차 상관, 스파크라인, `results`: 07의 단계별 기여·뉴스 빼기·구매 시뮬레이션과 03b 전후 오차, `fx_cop`: 이상치 규칙이 바꾼 페소 환율). 노트북을 다시 실행했으면 이것도 다시 실행한다.
3. `frontend/src/research.js`: 사람이 노트북 결과를 옮긴 문장과 숫자(장마다의 발견과 질문·방법·판단, HAR 변동성 결과, 시행착오, 결론, 한계, Q&A)와 이 문장이 설명하는 `MODEL_VERSION`.

`tests/test_portfolio.py`는 2와 3이 `configs/settings.yaml`의 동결 모델·평가 구간, `FEATURE_GROUPS`·`EXTRA_GROUPS`·`NEWS_FEATURES`와 같은지 검사한다. 결과의 CRPS 막대(`CrpsBars`)는 0을 기준으로 양쪽에 그린다(기준선보다 분포가 실제에 가까우면 왼쪽). 첫 화면 카드는 맞히려는 값인 80% 범위를 가장 크게 두고 가운데 가격은 그 아래 행에 둔다. 화면도 metadata의 모델 버전이 `MODEL_VERSION`과 다르면 Why 섹션 위에 그 사실을 표시한다. 바꾸는 순서는 AGENTS.md '예측 방법을 바꿀 때'에 있다.

강수 연간 시계와 최저·최고기온 편차 시계(같은 주 2005년부터 지난해까지 평균과의 차이, `lib.weeklyAnomalyGrid`)는 `/api/weather`가 배포되고 기상이 적재된 뒤에 그려진다. 그 전에는 빈 상태 문구를 보여 준다. 테마는 검정 단색 하나다. 색은 역할 토큰만 쓴다(실제 = 흰 잉크, 모델 출력·상승 = 네온 앰버, 하락 = 네온 시안, 강수 = 네온 라임). 로컬 DB가 없을 때는 `API_TARGET=<운영 주소> npx vite`로 운영 API를 읽기 전용으로 붙여 확인한다.

## DB

| 테이블 | 내용과 규칙 |
|---|---|
| `prices` | 날짜별 OHLCV. Yahoo가 최근 값을 고칠 수 있어 같은 날짜는 덮어쓴다 |
| `weather` | (산지, UTC 관측일)별 강수·평균/최저/최고기온. 같은 키는 수정값으로 갱신하고 NaN은 NULL로 저장한다. 화면 전용이며 모델 피처의 Parquet·관측일 + 4일 규칙과는 별개다 |
| `models` | 모델 버전과 metadata. 활성 모델은 부분 유일 인덱스로 하나만 허용 |
| `forecasts` | (버전, 기준일, 지평)마다 한 행. 기준일 종가, 예측 로그수익률·예측 가격, 상승 확률·신호, 80% 범위, 예측 변동성·백분위를 담는다. 한 번 쓰면 고치지 않는다(`ON CONFLICT DO NOTHING`). 같은 기준일의 backfill이 있으면 live는 저장되지 않는다. 실제 가격은 `prices`를 목표일로 조인해 본다 |
| `news_articles` | 기사별 Jev 분석과 요청 비용. `daily`는 여기서 분석이 끝난 시각(`available_at`)을 읽어 분포 모델의 뉴스 입력을 만든다(`db.read_news_inputs`) |
| `pipeline_runs` | 실행 기록(상태, 단계별 결과, 오류 메시지) |

스키마는 소유자 계정(`coffee_pipeline`)으로 적용하고 API는 SELECT 권한만 있는 `coffee_api`로 접속한다. 비밀번호는 URL이 아니라 `PGPASSWORD`로 넘긴다.

## 파이프라인

- `backfill`: 모델 등록, 가격·기상 전체, 2026-01-01부터 최신 기준일까지 예측(`kind='backfill'`), 보관 뉴스 1,446건. 소급 예측의 뉴스 입력은 학습·평가와 같은 연구용 시점(발행 + 1일)으로 보관 기사에서 만든다.
- `daily`: 소스 갱신 → 기상 최근 30일 적재 → 최신 기준일 예측(`kind='live'`, 뉴스 입력은 DB에서 기준일 마감까지 분석이 끝난 기사) → 최근 7일 기사 중 새 기사를 Jev로 분류(한 번에 20건). 오늘 분류한 기사는 다음 기준일부터 입력이 된다. 비용은 응답을 받아야 알 수 있어 실행마다 0.01 USD를 미리 잡고, 누적 상한 1 USD까지 남은 예산이 그보다 작으면 분류하지 않는다.
- `weather`: `data/sources/weather_<산지 id>.parquet` 6개만 읽어 기상 전체를 upsert한다. 가격·거시·ENSO Parquet는 필요 없다. 수집·모델 실행·가격 적재·뉴스 분류는 하지 않는다. 별도 적재는 `python -m coffee.pipeline weather`로 실행한다.
- 기상 적재 시작일은 `settings.yaml`의 `collect_start`(2005-01-01)다. `daily`의 30일은 실행일(UTC)을 포함한 달력 날짜이며 누락된 날을 채우지 않는다. 수집이 실패한 산지는 기존 `sources` 경고를 유지하고 보관 Parquet에서 해당 기간만 적재한다. `daily`·`backfill`은 기존 `load_sources`로 전체 소스를 읽는다. `weather`는 기상 6개만 직접 읽으며 기상 파일 누락이나 중복 날짜는 실행 실패로 처리한다.
- 기상 적재는 `pipeline_runs.steps`에 `{"step": "weather", "upserted": n}`을 남긴다. 건수는 새 행과 같은 키의 갱신 행을 모두 포함한다.
- `backfill`·`daily` 모두 23:00 UTC 마감이 지난 거래일의 가격만 쓴다. 장중에 실행해도 끝나지 않은 오늘 봉으로 예측을 저장하지 않는다.
- 종료 코드: `0` 성공, `3` 경고(예측은 저장, 일부 소스 실패·가격 지연·기준일 피처 결측·뉴스 실패), `1` 실패.

## 기상 API

`GET /api/weather?region=br_sul_minas`는 2005-01-01부터 최신 관측일까지 고정 조회한다. 기본 산지는 `br_sul_minas`이며 `br_cerrado`, `br_alta_mogiana`, `co_huila`, `co_caldas`, `co_antioquia`도 허용한다. 그 밖의 값은 422다. API 이미지에는 pandas가 없으므로 산지 id를 상수로 두고 설정 파일과의 일치를 테스트한다.

- 응답은 `region`, `years`, `days`, `precip`, `t_mean`, `t_min`, `t_max`다. `years`는 관측 행이 있는 연도를 오름차순으로 담으며 각 지표의 바깥 배열은 이 순서, 안쪽 배열은 0–51주 52칸이다. 자료가 없으면 모든 배열은 빈 배열이다.
- 주 번호는 `min((연중 일자 - 1) // 7, 51)`이다. 1월 1일을 기준으로 하며 ISO 주를 쓰지 않는다. 마지막 주는 평년 8일·윤년 9일이다.
- SQL로 강수 합계(mm), 평균기온 평균(℃), 최저기온 최솟값, 최고기온 최댓값을 구한 뒤 소수 한 자리로 반올림한다. NULL은 각 지표 집계에서 제외하며 전부 NULL이면 결과도 NULL이다.
- `days`는 그 주의 관측 행 수다(지표가 NULL인 행도 포함). 관측 행이 없는 주는 `days=0`, 나머지 지표는 `null`이다. 7일 미만인 주의 값도 그대로 보낸다. 프론트는 강수를 `precip / days * 7`로 환산하고 `days < 7`이면 강수를 그리지 않는다.
- 일별 원자료·평년값·기간 매개변수·응답 압축은 제공하지 않는다. 연도별 52칸 배열로 응답 크기를 줄인다.

기존 서비스에 적용할 때는 사용자 승인 후 소유자 계정으로 `python -m coffee.pipeline migrate`를 실행하고 API 이미지를 다시 빌드한다. `deploy/setup-db.sh`의 default privileges가 설정되어 있으면 새 표의 SELECT 권한도 API 계정에 주어진다. 최신 보관 소스를 준비한 다음 `weather` 명령으로 처음 한 번 적재하고 이후 `daily`로 갱신한다. 기상만 채울 때 `backfill`을 쓰면 로컬 가격도 DB에 덮어쓰므로 `weather` 명령을 사용한다.

## 배포

```
GitHub Actions (daily.yml)
  └ deploy/run-daily.sh
      1. VM /srv/coffee/v2/sources → runner data/sources (rsync)
      2. SSH 터널 127.0.0.1:15432 → VM PostgreSQL
      3. python -m coffee.pipeline daily   (exit 3은 경고로 표시하고 성공 처리)
      4. data/sources → VM (rsync)

Azure VM (deploy/compose.azure.yaml)
  postgres (127.0.0.1:15432만 열림) · api (coffee_api) · web (nginx) · caddy (80/443, HTTPS)
```

- VM의 SSH 계정 `coffee-actions`는 키로만 접속하고 포트 포워딩은 127.0.0.1:15432로만 허용한다(`deploy/prepare-vm.sh`). 명령 실행까지 막지는 않아서 키가 새면 VM 안에서 명령을 실행할 수 있다. rsync 전용 강제 명령(`rrsync`)으로 좁히는 것은 후속 과제다.
- 이미지는 digest로 고정하고 VM에서는 커밋 SHA를 태그로 붙여 빌드한다.
- CI(`ci.yml`)는 PostgreSQL 서비스와 함께 pytest, 프론트 테스트·빌드, Docker 빌드를 돌린다.

## 배포와 되돌리기

운영은 2026-10-04에 이전 database `coffee_price`에서 같은 클러스터의 `coffee_v2`로 전환했다(개발 기록 19). 이전 database와 이미지는 지우지 않았지만 이전 코드로 돌아가는 절차는 더 쓰지 않는다. 새 커밋은 아래 순서로 배포하고, VM에서 실행하는 단계마다 사용자 승인을 받는다.

1. 점검: compose 프로젝트 `coffee`, 볼륨 `coffee_postgres-data`, `/srv/coffee/.env`의 `COFFEE_DB_NAME=coffee_v2`를 확인하고 일일 workflow가 돌고 있지 않은지 본다.
2. 백업: 실행마다 `mktemp -d`로 새 폴더를 만들어 `coffee_v2` dump, `.env`, 이미지 이름, `/srv/coffee/v2/sources`를 모은다. 기존 백업을 덮지 않는다. dump는 리다이렉트로 저장하고 `pg_restore -l` 결과는 변수에 먼저 받는다. 파이프를 쓰면 실패한 종료 코드가 가려진다.
3. 소스: `/srv/coffee/app`은 git 저장소가 아닌 root 전용 폴더다. 이미지에 필요한 경로만 `git archive`로 묶어 보내고 SHA-256을 양쪽에서 대조한다. 새 폴더에 풀고 `.source-commit`에 커밋을 적는다. 이전 폴더는 백업 폴더로 옮긴 뒤 바꾼다. `.env`에서는 `COFFEE_SOURCE_SHA`만 바꾼다.
4. 적용: `setup-db.sh`가 API·웹 이미지를 하나씩 빌드하고 스키마(`IF NOT EXISTS`)와 권한을 적용한 뒤 api·web만 바꾼다. SSH가 끊겨도 계속되도록 분리해 실행하고 로그 끝의 종료 코드를 본다.
5. 자료: 새 표를 채울 때만 적재한다. 가격·예측이 있는 DB에 `backfill`을 돌리면 로컬 가격이 운영 가격을 덮으므로 쓰지 않는다. 기상은 `weather` 명령으로 적재하고, VM 보관본과 로컬 원자료의 날짜 범위를 비교해 더 최신인 쪽을 읽는다. 오래된 파일을 VM에 올리지 않는다.
6. 확인: API 응답을 DB 건수와 대조하고 `daily.yml`을 수동 실행한다. `run-daily.sh`는 종료 코드 3을 성공으로 바꾸므로 Actions가 녹색이어도 `pipeline_runs.status`를 본다.

```
# 1–2 VM: 점검·백업. 출력된 폴더가 이번 배포의 백업이다
sudo docker compose ls && sudo docker ps --format '{{.Names}} {{.Image}}' && sudo grep -E '^(COFFEE_DB_NAME|COFFEE_SOURCE_SHA)=' /srv/coffee/.env
sudo bash -s <<'EOF'
set -euo pipefail; umask 077
b=$(mktemp -d /srv/coffee/backups/deploy-<커밋>-XXXXXX); echo "$b"
docker exec coffee-postgres-1 pg_dump -U postgres -Fc coffee_v2 > "$b/coffee_v2.dump"
toc=$(docker exec -i coffee-postgres-1 pg_restore -l < "$b/coffee_v2.dump"); printf '%s\n' "$toc" > "$b/dump-toc.txt"
cp -p /srv/coffee/.env "$b/env"
docker ps --format '{{.Names}} {{.Image}}' > "$b/images.txt"
tar -C /srv/coffee/v2 -czpf "$b/v2-sources.tar.gz" sources
EOF

# 3 Mac: 이미지에 필요한 경로만 묶는다
git archive --format=tar.gz -o /tmp/coffee-app-<커밋>.tar.gz <커밋> -- Dockerfile .dockerignore coffee configs deploy frontend model_artifacts requirements-api.txt requirements-pipeline.txt
shasum -a 256 /tmp/coffee-app-<커밋>.tar.gz && scp /tmp/coffee-app-<커밋>.tar.gz <관리자>@<VM>:~/

# 3–4 VM: 해시가 Mac과 같은지 본 뒤 바꾸고 적용한다. <백업>은 2에서 출력된 폴더, <SHA>는 전체 커밋 SHA
sha256sum ~/coffee-app-<커밋>.tar.gz
sudo bash -s <<'EOF'
set -euo pipefail; umask 077
b=<백업>; sha=<SHA>
# 재시도로 남은 폴더가 있으면 멈춘다. 새 백업 폴더(2)부터 다시 한다
test ! -e /srv/coffee/app.new
test ! -e "$b/app"
install -d -m 0700 /srv/coffee/app.new
tar -xzf /home/<관리자>/coffee-app-<커밋>.tar.gz -C /srv/coffee/app.new
printf '%s\n' "$sha" > /srv/coffee/app.new/.source-commit
# 따로 실행해야 set -e가 각각의 실패에서 멈춘다(&& 왼쪽 실패는 멈추지 않는다)
mv -T /srv/coffee/app "$b/app"
mv -T /srv/coffee/app.new /srv/coffee/app
sed -i "s/^COFFEE_SOURCE_SHA=.*/COFFEE_SOURCE_SHA=$sha/" /srv/coffee/.env
setsid nohup bash -c "COFFEE_SOURCE_SHA=$sha /srv/coffee/app/deploy/setup-db.sh /srv/coffee/.env; echo SETUP_DB_EXIT=\$?" > "$b/setup-db.log" 2>&1 < /dev/null &
EOF
sudo tail -3 <백업>/setup-db.log   # SETUP_DB_EXIT=0이 나올 때까지 확인한다

# 5 Mac: 기상만 적재할 때. 터널은 다른 터미널에 열어 둔다
ssh -N -L 15432:127.0.0.1:15432 <관리자>@<VM>
read -s PGPASSWORD && export PGPASSWORD PGHOST=127.0.0.1 PGPORT=15432 PGDATABASE=coffee_v2 PGUSER=coffee_pipeline DATABASE_URL=postgresql://
$HOME/.virtualenvs/coffee-price-prediction/bin/python -m coffee.pipeline weather; rc=$?; unset PGPASSWORD; echo "weather exit=$rc"
```

`weather`는 코드 위치의 `data/sources/weather_*.parquet`를 읽으므로 배포 커밋을 체크아웃한 폴더에서 실행한다. zsh에서는 `status`가 읽기 전용 변수라 종료 코드를 `rc`에 담는다.

되돌리기: 이전 이미지는 VM에 남겨 두므로 다시 빌드하지 않는다. 백업 폴더의 app과 `.env`를 되돌리고 api·web을 이전 이미지로 띄운다. 스키마 변경이 표 추가뿐이면 이전 API가 새 표를 무시하므로 DB는 되돌리지 않는다. DB 복원이 꼭 필요하면 dump를 새 이름의 database에 먼저 복원해 비교한다.

```
sudo bash -c 'set -e; b=<백업>; mv -T /srv/coffee/app "$b/app_failed"; mv -T "$b/app" /srv/coffee/app; cp -p "$b/env" /srv/coffee/.env'
sudo docker compose --project-name coffee --env-file /srv/coffee/.env -f /srv/coffee/app/deploy/compose.azure.yaml up -d --no-build --wait api web caddy
```
