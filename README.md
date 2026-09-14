# Coffee Price Prediction

아라비카 커피 선물(`KC=F`)의 **5·20·60거래일 뒤 종가**를 예측하는 프로젝트입니다. 과거 가격에 환율·금리·유가와 산지 기상을 더하면 예측이 나아지는지 살펴봤습니다.

## 프로젝트 목적
과거 가격, 거시경제 데이터(환율, 유가 등), 원산지 기후 데이터 등 다양한 시계열 데이터를 바탕으로 Feature Selection과 모델별 RMSE를 비교하여 단/중/장기 예측의 정확도를 올리는 것을 목표로 하였습니다.

기존 학부 프로젝트의 Attention-LSTM 예측을 바탕으로 데이터 수집, 시간 정렬, EDA, 모델 비교를 다시 구성했습니다. 현재는 Attention-LSTM과 앙상블 조합별 비교까지 진행했습니다. 5·20일 예측에서는 가격 유지 기준을 크게 넘지 못했고, 60일에서는 가격+거시 DLinear가 다음 검토 후보로 남았습니다. FastAPI와 차트 화면은 이후 구현할 예정입니다.

분석은 [03-1: EDA·기준 모델](data_code/03_1_eda_and_baseline_models.ipynb) → [03-2: 지평별 재검증·앙상블](data_code/03_2_horizon_model_validation.ipynb) 순서로 읽으면 됩니다. 아래 이미지는 두 노트북에 저장된 실제 출력입니다.

## 데이터와 예측 기준

모델은 미래 로그수익률 `log(P[t+h] / P[t])`를 학습합니다. 이를 `P[t] × exp(예측 수익률)`로 바꿔 종가와 비교합니다. 가격 유지(Naive)는 수익률을 0으로 예측하므로, 미래 가격의 예측값이 오늘 종가와 같습니다.

| 구간 | 기간 | 용도 |
|---|---|---|
| 과거 버퍼 | 2014년 7~12월 | 이동 집계와 입력 창 계산 |
| Train | 2015~2021년 | EDA와 모델 학습 |
| Validation | 2022~2023년 | 피처 그룹·모델·설정 선택 |
| Test | 2024~2025년 | 선택을 고정한 뒤 비교 |

예측 기준일뿐 아니라 정답 날짜도 각 구간 안에 들어오도록 나눴습니다. 예를 들어 2021년 말에 만든 60일 뒤 수익률은 Train에 넣지 않습니다. Test 평가 전에는 선택한 설정을 유지한 채 2015~2023년으로 다시 학습합니다.

| 모델 입력 | 출처 | 만든 피처 |
|---|---|---|
| 가격 4개 | Yahoo Finance · [yfinance](https://ranaroussi.github.io/yfinance/reference/api/yfinance.Ticker.history.html) | 1·5·20거래일 수익률, 20일 변동성 |
| 거시 3개 | [FRED·ALFRED](https://fred.stlouisfed.org/docs/api/fred/series_observations.html) 최초 공개값 | BRL/USD 환율의 20일 로그변화, 금리·WTI의 20일 차이 |
| 기후 20개 | [NASA POWER](https://power.larc.nasa.gov/docs/services/api/temporal/daily/) 브라질·콜롬비아 6개 지점 | 지점별 30일 강수·기온 편차와 90일 강수 결손, 월 주기 sin·cos |

기상 집계의 30·90일은 달력일, 예측 지평의 5·20·60일은 거래일입니다. 기상 편차의 기준은 학습 구간에서 계산한 월별 평균입니다. 결측값을 평균으로 채우는 데 쓰지는 않습니다.

커피 거래일에 맞춰, 소스별로 정한 이용 가능 시점이 지난 가장 최근 값을 As-of Join했습니다. ALFRED는 공개 날짜 다음 날부터 사용하고, NASA는 관측일+4일을 가정했습니다. 가격 누락은 위치를 유지한 채 시차와 타깃을 계산하며, 평균·중앙값 대치나 미래 값 보간은 하지 않습니다.

수집 스크립트는 비교용 Yahoo 환율·FRED 수정본과 [CFTC 포지션](https://www.cftc.gov/MarketReports/CommitmentsofTraders/HistoricalCompressed/index.htm)까지 총 15개 Parquet를 저장합니다. 현재 모델에서는 COT의 실제 발표 지연과 달러지수의 과거 소급 구간을 충분히 확인하지 못해 두 변수를 제외했습니다. 자세한 소스와 좌표는 [sources.yaml](configs/sources.yaml), [regions.yaml](configs/regions.yaml)에 있습니다.

## 03-1에서 확인한 것

먼저 Train에서 가격의 흐름과 분포를 살펴봤습니다. 월평균 가격에 STL 분해를 적용하고, ACF/PACF와 ADF·KPSS를 확인했습니다. 각 피처의 히스토그램·박스플롯, 미래 수익률을 y로 둔 regplot도 함께 그렸습니다.

![2015~2021년 월평균 커피 종가의 추세·계절 성분·잔차](docs/images/train_price_stl.png)

STL은 84개월에 연간 주기 12를 적용한 설명용 분석입니다. 이 분해 결과를 모델 입력으로 사용하지는 않았습니다. Train 내 연속된 유효 구간을 검정했을 때 5·20일 수익률은 가격 수준보다 정상성 가정에 잘 맞았지만, 60일 수익률은 ADF와 KPSS의 판단이 갈렸습니다. 수익률로 바꿨다고 모든 지평의 문제가 해결되는 것은 아니었습니다.

외생변수는 0~60거래일의 입력 시차를 주고 미래 수익률과의 상관을 탐색했습니다. 여기서 큰 상관이 나온 시차를 곧바로 채택하지는 않았습니다. 계절성과 서로 겹치는 수익률 구간도 상관에 영향을 줄 수 있기 때문입니다.

<details>
<summary>Train 피처 상관관계 히트맵</summary>

![가격·거시·기후 27개 피처의 Train Pearson 상관관계](docs/images/train_feature_correlation.png)

월별 편차로 바꾼 기상 피처 사이의 최대 절대 상관은 약 0.886이었습니다. 정한 기준인 0.90을 넘는 쌍이 없어 이번에는 중복 제거 후보를 만들지 않았습니다.

</details>

피처 선택은 **가격 / 가격+거시 / 가격+기후 / 전체** 네 그룹을 같은 CatBoost 설정과 같은 날짜에서 비교하는 방식으로 진행했습니다. 한 변수만 넣었을 때 실패했다고 나머지 변수를 모두 버리지는 않았습니다.

Validation에서는 5일에 가격+거시, 20·60일에 전체 그룹이 선택됐습니다. 다만 20일의 전체 그룹은 가격만 쓴 경우보다 검증 RMSE가 11.15% 낮아도 Test에서는 0.83% 높았습니다. 이 차이를 보고 03-2에서 피처 그룹과 모델 조합을 다시 비교했습니다.

## 03-2 모델 비교 결과

03-1에서는 Naive·지수평활·ARIMA·Ridge·CatBoost·LightGBM·DLinear·LSTM을 비교했습니다. 03-2에서는 지평별 피처 조합을 넓히고 학부 코드의 Attention-LSTM을 추가했습니다.

| 지평 | 비교한 피처 그룹 | 그룹별 모델 | 별도 가격 단변량 모델 |
|---|---|---|---|
| 5·20일 | 가격+거시 / 전체 | CatBoost, LightGBM, Attention-LSTM | ARIMA, 지수평활 |
| 60일 | 가격+거시 / 가격+기후 / 전체 | CatBoost, LightGBM, DLinear, LSTM, Attention-LSTM | 없음 |

31개 후보와 지평별 Naive 3개를 비교했습니다. 신경망은 과거 60거래일의 시퀀스를, 트리 모델은 현재 행에 계산된 과거 집계 피처를 받습니다. 모든 모델을 같은 유효 날짜에서 평가했습니다. 트리 수는 150/300, 신경망은 50/100 epoch를 비교했고 CPU·seed 42로 실행했습니다.

PyCaret 4.0은 가격 단변량 모델 연결에 사용하고, 신경망은 PyTorch로 별도 학습했습니다. DLinear는 분해 선형 구조를 지평별 수익률 하나를 출력하도록 바꾼 모델입니다. 일반 LSTM과 Attention-LSTM은 은닉 64·2층으로 맞췄고, Attention에는 기존 Entmax와 gate 결합을 사용했습니다.

아래는 **Validation RMSE로 선택한 개별 모델**의 결과입니다. RMSE·MAE는 로그수익률 기준이며 낮을수록 좋습니다. Test 사례 수는 5·20·60일 순서로 498·483·443개입니다.

| 지평 | 선택 모델·입력 | 검증 RMSE | Test RMSE | Test MAE | Test 방향 정확도 | Test Naive 대비 RMSE 개선 |
|---|---|---:|---:|---:|---:|---:|
| 5일 | 지수평활 · 가격 단변량 | 0.04707 | 0.05444 | 0.04281 | 51.00% | 약 0% |
| 20일 | 지수평활 · 가격 단변량 | 0.09102 | 0.11300 | 0.08538 | 50.93% | 약 0% |
| 60일 | DLinear · 가격+거시 · 50 epoch | 0.14719 | 0.16794 | 0.14041 | 60.05% | +9.59% |

같은 Test 날짜에서 Naive RMSE는 각각 0.05444·0.11300·0.18576입니다. 5·20일의 지수평활은 가격 유지와 사실상 같았습니다. 거의 0인 예측도 작은 부호 차이는 방향 정확도에 반영되므로 RMSE와 함께 읽어야 합니다. Naive의 0 예측은 보합으로 셉니다.

60일 가격+거시 DLinear는 검증에서도 Naive보다 RMSE가 7.88% 낮았습니다. 다만 Test 개선율이 2024년 2.94%, 2025년 20.71%로 달랐고, 수익률 구간이 겹치지 않게 뽑으면 시작점에 따라 -6.02~+19.56%였습니다. 다음 검토 후보로 남기되, 어느 기간에서도 잘 맞는 모델이라고 보기는 어렵습니다.

Attention-LSTM도 일관되게 낫지는 않았습니다. 60일 전체 피처에서는 일반 LSTM보다 Test RMSE가 22.11% 높았습니다. 전체 피처 일반 LSTM이 Test 최저 RMSE를 기록했지만 Validation에서는 Naive보다 나빴으므로, Test 순위를 보고 선택을 바꾸지 않았습니다.

![5·20·60거래일 실제 미래 종가와 Validation에서 선택한 모델 및 가격 유지 예측](docs/images/horizon_selected_price_forecasts.png)

가로축은 세 선 모두 **정답 날짜 `t+h`**입니다. 매일 새로 만든 h일 앞 예측을 같은 정답 날짜의 실제 종가와 비교한 그림입니다. 가격 유지선은 `P[t]`를 `t+h`에 그리므로 실제 가격을 뒤따르는 모양이 됩니다. 지평이 길어질수록 급등락을 미리 따라가지 못하는 모습도 보입니다.

## 앙상블을 해보니

먼저 Validation 방향 정확도 50% 이상인 후보 중 RMSE가 낮은 두 개를 골라 수익률을 반씩 섞었습니다. 아래 그림의 앙상블은 이 규칙으로 만든 결과입니다.

![방향 정확도 50% 필터 후 선택한 두 모델의 50대50 앙상블과 개별 모델 Test 비교](docs/images/horizon_ensemble_comparison.png)

5일은 CatBoost 두 구성을 섞어도 Naive보다 RMSE가 0.53% 높았습니다. 20일은 ARIMA와 전체 피처 CatBoost를 섞어 0.81% 개선됐습니다. 60일은 DLinear와 LightGBM을 섞었는데, 검증 RMSE는 좋아졌어도 Test에서는 DLinear 단독보다 2.63% 나빠졌습니다.

그다음에는 50% 필터를 빼고 Validation의 RMSE 1위와 방향 정확도 1위를 골랐습니다. RMSE 모델 비중을 100·75·50·25·0%로 바꿔 비교했습니다. 아래는 각 지평에서 검증 RMSE가 가장 낮았던 비중입니다.

| 지평 | 검증에서 가장 낮은 RMSE를 낸 조합 | 검증 RMSE | Test RMSE | Test 방향 정확도 |
|---|---|---:|---:|---:|
| 5일 | 지수평활 100% | 0.04707 | 0.05444 | 51.00% |
| 20일 | 지수평활 50% + 전체 피처 CatBoost 50% | 0.09033 | 0.11209 | 53.00% |
| 60일 | 가격+거시 DLinear 75% + 가격+거시 LSTM 25% | 0.14611 | 0.17572 | 55.76% |

20일은 예측 수익률의 크기를 줄여 오차가 조금 낮아졌지만, 방향 정확도는 CatBoost 단독과 같았습니다. 60일은 방향 정확도 1위 모델을 섞었는데도 Test에서 오차와 방향 정확도가 모두 나빠졌습니다. 가중치별 결과는 탐색 기록으로 남겼고 기존 모델을 자동으로 교체하지 않았습니다.

## Troubleshooting & 배운 점

### 결측값을 채우기 전에 어디서 생겼는지부터 보기

기상 원자료에 빈 값이 없어도 `rolling(30)`을 계산하면 첫 29일은 NaN이 됩니다. 당시 중앙값으로 채웠던 부분을 없애고 실제 과거 관측값을 더 받았습니다. 현재는 2014년 자료를 버퍼로 써서 2015년 첫 학습일의 30·90일 기상 집계를 완성합니다. 이 경험 이후로 원자료 누락과 계산에 필요한 이력 부족을 구분하게 됐습니다.

### 관측일과 이용 가능 시점은 다르다

가격이 빠진 거래일을 먼저 삭제하면 20행 뒤가 20거래일 뒤라는 보장이 없어집니다. 날짜 위치를 유지한 상태에서 타깃을 만들도록 바꿨습니다. 거시지표는 관측일과 실제 공개일이 달라, 예측할 때 알 수 있었던 값인지도 확인해야 했습니다. ALFRED 최초 공개값을 따로 수집한 이유입니다.

### 기대한 피처가 실제 자료에서 작동하는지 확인하기

가뭄 뒤 서리가 오는 상황을 반영하려고 서리 강도와 가뭄×서리 피처를 만들었습니다. 그런데 사용한 브라질 격자 자료에서는 Train 값이 모두 0이라 제외했습니다. 실제 산지에 서리가 없었다고 해석할 수는 없습니다. 상관이 높은 변수를 줄이는 일도 마찬가지였습니다. 중복을 줄였다는 사실과 예측 오차가 줄었다는 결과는 따로 확인해야 했습니다.

### 방향 정확도 50%와 작은 RMSE 개선을 다시 읽기

60일 Test는 실제 상승 비율이 79.01%였습니다. 항상 상승만 예측해도 얻는 정확도라, 앙상블의 62.30%를 50%와만 비교하면 성능을 좋게 오해할 수 있었습니다. 또 DLinear·LightGBM의 오차 상관은 Validation 0.749에서 Test 0.904로 높아졌습니다. 모델 종류가 다르다는 이유만으로 서로의 오차를 줄여주지는 않았습니다.

20일 ARIMA+CatBoost 앙상블의 로그수익률 RMSE 개선은 0.81%였지만, 가격 RMSE는 36.09749 → 36.02569센트/파운드로 약 0.20% 줄었습니다. 어떤 단위의 오차가 얼마나 줄었는지 함께 확인하게 됐습니다.

[트러블슈팅](docs/troubleshooting.md)에는 기상 버퍼 전후 Jupyter 출력, Yahoo OHLC 불일치, 지수평활 상태 갱신 문제와 앙상블 해석을 남겼습니다. 시간 정렬과 모델 선택 기준은 [설계 노트](docs/architecture.md), 실행 내역은 [작업 기록](docs/STATUS.md)에서 볼 수 있습니다.

이번 결과는 Yahoo·NASA가 현재 제공하는 과거 자료를 쓴 연구용 비교입니다. 당시 수정 전 자료를 모두 복원한 것은 아니며, Test도 이전 실험에서 확인한 기간입니다. 단일 seed 결과와 중첩된 수익률 표본이라는 점을 고려해, 다음에는 검증 기간을 앞으로 옮겨도 개선이 유지되는지 확인하려 합니다. 그 결과를 바탕으로 서빙 모델을 정할 예정입니다.

## 실행하기

Python 3.12를 사용합니다. 가상환경은 Google Drive가 동기화하는 프로젝트 폴더 밖에 둡니다. FRED·ALFRED 수집에는 `.env`의 `FRED_API_KEY`가 필요합니다.

처음 설치할 때만 가상환경을 만듭니다. PyCaret은 실행을 확인한 **4.0.0a8 알파 버전**으로 고정했습니다. 설치 extra는 `timeseries`입니다.

```sh
uv venv --python 3.12 "$HOME/.virtualenvs/coffee-price-prediction"
source "$HOME/.virtualenvs/coffee-price-prediction/bin/activate"
uv pip install -r requirements.txt
python -m ipykernel install --user --name coffee-price-prediction --display-name "Coffee Price Prediction (Python 3.12)"
```

이후에는 활성화 명령만 실행하면 됩니다. Notebook에서는 위 이름의 커널을 선택합니다. Apple Silicon에서 OpenMP 라이브러리가 없다는 오류가 나면 `brew install libomp`로 설치합니다.

```sh
source "$HOME/.virtualenvs/coffee-price-prediction/bin/activate"
python data_code/02_backfill_10y.py
```

기본 수집 기간은 **2014-07-01 ~ 2025-12-31**이고, 결과는 `data/processed/2014-07-01_2025-12-31/`에 저장합니다. 기상은 이 기간 앞뒤에 30일을 더 수집합니다. 모델의 90일 집계에는 2015년 이전 버퍼를 사용하며 미래 관측을 넣지 않습니다.

짧은 구간이나 일부 소스만 다시 받을 수도 있습니다.

```sh
python data_code/02_backfill_10y.py --start 2025-01-01 --end 2025-01-31
python data_code/02_backfill_10y.py --sources yahoo fred
```

같은 기간을 다시 실행하면 성공한 테이블을 갱신하고 실패한 테이블의 기존 파일은 유지합니다. 하나라도 실패하면 종료 코드 1과 소스 이름을 출력합니다. `.env`와 수집 데이터는 로컬 파일이므로, 저장소를 새로 받았다면 전체 기간을 수집한 뒤 분석 노트북을 실행합니다.

03-1과 03-2는 각각 저장된 Parquet에서 시작하므로 독립 실행할 수 있습니다. 각 노트북에서 위부터 Run All 하면 됩니다. 표·차트는 한국어로 표시하며 Windows·macOS·Linux용 폰트 설정과 Linux 설치 안내를 첫 셀에 두었습니다.

## 파일 안내

| 파일·폴더 | 내용 |
|---|---|
| [01_small_batch_probe.ipynb](data_code/01_small_batch_probe.ipynb) | 파일럿 수집값 점검과 시각화 |
| [02_backfill_10y.py](data_code/02_backfill_10y.py) | API 수집, 결측 표시 정리, Parquet 저장 |
| [03_1_eda_and_baseline_models.ipynb](data_code/03_1_eda_and_baseline_models.ipynb) | 시간 정렬, EDA, 피처 그룹과 기준 모델 비교 |
| [03_2_horizon_model_validation.ipynb](data_code/03_2_horizon_model_validation.ipynb) | 지평별 조합, Attention-LSTM, 앙상블 검증 |
| `configs/` | 수집 소스와 기상 좌표 |
| `data/processed/` | 기간별 Parquet 데이터, 로컬 보관 |
| `docs/` | 설계·시행착오·실행 기록과 README 이미지 |
| `data_code/old_code/`, `data/old_data/`, `docs/old_docs/` | 학부 프로젝트 코드·데이터·문서 보관 |

## 레퍼런스
[AutoML] PyCaret을 활용한 시계열 데이터 예측 모형 생성 (https://teddylee777.github.io/machine-learning/pycaret-timeseries/)

마의 벽 9.4를 넘은 데이터 접근법 / XGB, LGBM, CAT, ET (0.942) (https://dacon.io/competitions/official/235871/codeshare/4494)

