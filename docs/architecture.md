# 구조와 데이터 계약

현재 코드가 따르는 규칙을 정리한다. 왜 이렇게 정했는지는 [개발 기록](development_log.md)에 있다.

## 데이터 흐름

```
수집(sources.py) ─→ data/sources/*.parquet ─→ 피처(features.py) ─→ 거래일 표
                                                               │
                    노트북 01–06: walk-forward 비교(evaluate.py, models.py)
                                                               │
                                     model_artifacts/<버전>/ (노트북 06에서 동결)
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
| `features.py` | 거래일 축, as-of 결합, 피처 46개, 타깃 |
| `evaluate.py` | 학습 행 선택(embargo), walk-forward 분할, 지표, 블록 bootstrap |
| `models.py` | 기준선·Ridge·LightGBM·DLinear·분류기, 확률 수축, 구매 신호, 가격 범위, 모델 저장(해시 검사) |
| `news.py` | 보관한 Jev 분석 읽기, 거래일별 뉴스 점수(live·research 두 시점) |
| `jev.py` | Google News RSS 수집, 하루 2건 선정, Jev 배치 분류 |
| `db.py`, `schema.sql` | SQL은 이 두 파일에만 있다. `db.py`는 API 이미지용으로 pandas 없이 동작한다 |
| `pipeline.py` | `migrate`, `backfill`, `daily` 명령과 종료 코드 |
| `api.py` | 읽기 전용 FastAPI |

## 거래일과 타깃

- 거래일 축은 NYSE 영업일에 커피 선물만 거래한 2일(2018-12-05, 2025-01-09)을 더한 것이다. 휴장일은 만들지 않는다.
- 기준 시각은 거래일 23:00 UTC(뉴욕 장 마감 이후)다.
- `y_h = ln(P[t+h] / P[t])`, `target_date_h`는 h번째 다음 거래일, `v_h`는 t+1…t+h 일간 로그수익률 표준편차의 로그다.
- 가격이 없는 날(Yahoo 결측)은 채우지 않는다. 피처는 마지막으로 알려진 종가로 계산하고, 타깃은 실제 종가가 있는 날만 만든다.

## 시점 계약

| 자료 | 언제부터 쓴다고 보는가 | 오래된 값 허용 |
|---|---|---|
| KC=F 종가 | 당일 | – |
| COP=X | 다음 날 | 14일 |
| ALFRED(헤알·금리·WTI·운임) | 최초 공개일 다음 날 | 일별 14일, 월별 80일 |
| ALFRED 이전 기간 | FRED 현재값 + 가정 지연(헤알 7일, 금리 1일, WTI 7일, 운임 45일), `release_assumed` 표시 | 같음 |
| ENSO ONI | 3개월 계절이 끝나고 10일 뒤 | 80일 |
| NASA 기상 | 관측일 + 4일 | 4일 |
| 뉴스(서비스) | `available_at`(분석 완료) 이후 첫 23:00 UTC | – |
| 뉴스(연구) | 발행 + 1일(가정). 2022–2025년 기사는 2026년에 소급 분류해서 실제 이용 시각이 없다 | – |

허용 기간보다 오래된 값은 결측으로 둔다. 기상 편차는 직전 10년의 같은 달(그해 제외)과 비교한다.

## 피처

| 묶음 | 개수 | 내용 |
|---|---|---|
| 가격 | 8 | 1·5·20·60일 수익률, 5·20·60일 변동성, 60일 이동평균 괴리 |
| 거시 | 5 | 헤알·페소·금리·WTI 20일 변화, 해상 운임 60일 변화 |
| 기후 | 29 | 산지 6곳의 30일 강수·기온 편차, 90일 표준화 강수, 고온일 편차, 브라질 한파 편차, ENSO와 3개월 변화 |
| 주기 | 4 | 월, 24개월 해거리(7월 시작 작물년도)의 sin·cos |

변동성 모델은 별도로 `log_vol_5/20/60`을 쓴다.

## 학습과 평가

- 학습 시작 2006년. 개발 구간 2012–2021년은 해마다 그 전까지로 학습해 그해를 예측한다(10회). 보류 구간 2022–2025년도 같은 방식으로 한 번만 평가했다.
- 학습 구간 끝을 넘는 목표일의 행은 학습에서 뺀다(embargo). 스케일러는 각 학습 구간에서 fit해 모델과 함께 저장한다.
- 모든 모델은 직전 60거래일 피처가 완전한 같은 행으로 비교한다.
- 유의성은 원형 블록 bootstrap(블록 = h, 1,000회, seed 42)으로 본다.

## 서비스 출력

| 출력 | 계산 | 지평 |
|---|---|---|
| 중심 전망 | 현재가 | 5·20·60 |
| 상승 확률 | LightGBM 분류 확률을 학습 구간 상승 비율 쪽으로 수축(계수 0.20–0.25) | 5·20·60 |
| 구매 신호 | 확률 ≥ 기준이면 `buy`, ≤ 1−기준이면 `wait`, 그 사이는 `hold`(기준 52/52/60%) | 5·20·60 |
| 80% 가격 범위 | `종가 · exp(±1.28 · k · σ̂ · √h)`, σ̂는 HAR 예측 일간 변동성, k = 1.01/0.90 | 20·60 |
| 위험 수준 | 최근 756거래일의 예측 변동성 중 오늘 값의 백분위 | 20·60 |

설정값은 `model_artifacts/<버전>/*/metadata.json`에 있고, 대시보드는 활성 모델의 metadata에서 신호 기준을 읽는다.

## DB

| 테이블 | 내용과 규칙 |
|---|---|
| `prices` | 날짜별 OHLCV. Yahoo가 최근 값을 고칠 수 있어 같은 날짜는 덮어쓴다 |
| `models` | 모델 버전과 metadata. 활성 모델은 부분 유일 인덱스로 하나만 허용 |
| `forecasts` | (버전, 기준일, 지평)마다 한 행. 한 번 쓰면 고치지 않는다(`ON CONFLICT DO NOTHING`). 같은 기준일의 backfill이 있으면 live는 저장되지 않는다. 실제 가격은 `prices`를 목표일로 조인해 본다 |
| `news_articles` | 기사별 Jev 분석과 요청 비용. 모델 입력이 아닌 참고 정보 |
| `pipeline_runs` | 실행 기록(상태, 단계별 결과, 오류 메시지) |

스키마는 소유자 계정(`coffee_pipeline`)으로 적용하고, API는 SELECT 권한만 있는 `coffee_api`로 접속한다. 비밀번호는 URL이 아니라 `PGPASSWORD`로 넘긴다.

## 파이프라인

- `backfill`: 모델 등록, 가격 전체, 2026-01-01부터 최신 기준일까지 예측(`kind='backfill'`), 보관 뉴스 1,446건.
- `daily`: 소스 갱신 → 최신 기준일 예측(`kind='live'`) → 최근 7일 기사 중 새 기사를 Jev로 분류(누적 비용 상한 1 USD, 한 번에 20건).
- 두 명령 모두 23:00 UTC 마감이 지난 거래일의 가격만 쓴다. 장중에 실행해도 끝나지 않은 오늘 봉으로 예측을 저장하지 않는다.
- 종료 코드: `0` 성공, `3` 경고(예측은 저장, 일부 소스 실패·가격 지연·기준일 피처 결측·뉴스 실패), `1` 실패.

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

- VM의 SSH 계정 `coffee-actions`는 키로만 접속하고, 포트 포워딩은 127.0.0.1:15432로만 허용한다(`deploy/prepare-vm.sh`). 명령 실행까지 막지는 않아서 키가 새면 VM 안에서 명령을 실행할 수 있다. rsync 전용 강제 명령(`rrsync`)으로 좁히는 것은 후속 과제다.
- 이미지는 digest로 고정하고, VM에서는 커밋 SHA를 태그로 붙여 빌드한다.
- CI(`ci.yml`)는 PostgreSQL 서비스와 함께 pytest, 프론트 테스트·빌드, Docker 빌드를 돈다.

## 운영 전환 절차

기존 운영(이전 코드, database `coffee_price`)은 그대로 두고 같은 PostgreSQL 클러스터에 새 database `coffee_v2`를 만든다. 실행 직전에 사용자 승인을 받는다.

1. 이전 database를 `pg_dump`로 백업한다(`/srv/coffee/backups`).
2. VM `/srv/coffee/.env`에 `COFFEE_DB_NAME=coffee_v2`를 넣고 `sudo COFFEE_SOURCE_SHA=<커밋> deploy/setup-db.sh /srv/coffee/.env`를 실행한다. database·권한·스키마를 만들고 API·대시보드를 새 이미지로 바꾼다.
3. Mac의 `data/sources/`를 VM `/srv/coffee/v2/sources/`로 올린다.
4. Mac에서 SSH 터널을 열고 `python -m coffee.pipeline backfill`을 실행한다(보관 뉴스 `data/jev/`가 Mac에만 있다). 2와 4 사이에는 대시보드가 비어 있다.
5. 브랜치를 병합하고 `daily.yml`을 수동 실행해 결과를 확인한다.
6. `daily.yml`에 schedule을 추가한다(이전 `daily-pipeline.yml`은 병합과 함께 사라진다).

되돌리기: 이전 커밋을 체크아웃해 이전 `deploy/start-azure.sh`로 API·대시보드를 다시 띄운다. 이전 database는 바꾸지 않았으므로 그대로 쓸 수 있다.
