# 작업 규칙

커피 선물 가격 예측 포트폴리오 프로젝트에서 사람·AI 도구가 함께 지키는 규칙이다. 학부 졸업생이 읽고 설명할 수 있는 코드를 우선한다. 필요 없는 프레임워크·추상화·서비스는 추가하지 않는다.

## 시작할 때

- `README.md`, `docs/architecture.md`, `docs/development_log.md`의 최신 항목과 `git status`를 먼저 확인한다.
- 판단은 문서보다 현재 코드와 실행 결과를 기준으로 한다.

## 환경

- Mac에서는 외부 venv `$HOME/.virtualenvs/coffee-price-prediction`(Python 3.12)을 쓴다. 프로젝트 안에 `.venv`를 만들지 않는다.
- 의존성: `requirements.txt`(노트북·개발), `requirements-pipeline.txt`(수집·추론), `requirements-api.txt`(API). 서빙 이미지에는 torch를 넣지 않는다.
- 비밀값(FRED·AI Gateway 키, DB 비밀번호)은 환경 변수로만 넣는다. 로그·예외·URL·커밋에 남기지 않는다.
- 의존성이나 Actions 버전을 바꿀 때는 공식 문서와 실제 설치 버전을 확인하고, 근거를 개발 기록에 한 줄 남긴다.

## 데이터와 시점

- 수집 데이터(`data/`)는 git에 올리지 않는다. `data/old_data/`, `data_code/old_code/`, `docs/old_docs/`는 지우지 않는다.
- 각 값이 **언제 알려졌는지**를 지킨다. ALFRED는 공개일 다음 날, NASA 기상은 관측일 + 4일, 뉴스는 분석이 끝난 뒤 첫 거래일 마감(23:00 UTC)부터 쓴다.
- 가격 결측이나 OHLC 이상값을 임의로 채우거나 고치지 않는다. 휴장일을 만들지 않는다.
- 학습 구간을 넘는 목표일의 행은 학습에서 뺀다. 기준값·스케일러는 학습 구간에서만 fit한다.

## 검증

- 바꾼 부분에 맞춰 `pytest`, 노트북 재실행, 프론트 build, Docker build 중 필요한 것을 실행하고 실제 결과를 확인한다.
- 외부 API와 LLM 호출은 테스트에서 fixture로 대체한다. fixture 성공을 실제 서비스 검증으로 보지 않는다.
- 모델 비교는 같은 날짜·같은 지평의 Naive 기준선과 한다. 한 번 본 평가 구간을 미사용 test라고 부르지 않는다.
- 표·그래프는 한국어로 표시한다.

## 예측 방법을 바꿀 때

포트폴리오 화면(`frontend/`)은 예측 결과와 그 근거를 함께 보여 준다. 모델·피처·평가 구간·뉴스 처리·신호 규칙을 바꾸면 화면도 같은 변경에서 고친다.

- API와 모델 metadata로 읽는 값(예측, 매수 기준, 수익률 검증 성적, 모델 이름, 모델별 피처)은 화면이 저절로 따라간다. 새 지표를 화면에 보이려면 metadata에 넣는 쪽을 먼저 고려한다.
- `python -m coffee.portfolio`를 다시 실행해 `frontend/src/research-data.json`(평가 구간, 피처 묶음, 뉴스 시차 상관, 스파크라인)을 갱신한다.
- `frontend/src/research.js`의 문장과 숫자(변동성·방향 결과, 결론, 시행착오, 한계)를 노트북 결과와 맞추고 `MODEL_VERSION`을 새 동결 모델로 바꾼다.
- 출력 이름이나 의미가 바뀌면(예: 신호 종류, 범위 정의) `lib.js`와 해당 컴포넌트, `docs/architecture.md` '화면'을 함께 고친다.
- `tests/test_portfolio.py`는 동결 모델·피처·평가 구간이 화면 자료와 다르면 실패한다. 테스트를 고쳐 통과시키지 말고 화면 자료를 갱신한다.
- `npm test`, `npm run build`로 확인하고, 화면을 직접 열어 바뀐 문장과 그래프를 본다.

## Git

- 장기 브랜치는 `main` 하나다. 작업은 짧은 기능 브랜치에서 한다.
- 커밋 메시지는 `<type>(<scope>): <summary>` 형식이다. 필요하면 본문에 이유와 검증 결과를 적는다.
- `git add .`를 쓰지 않는다. 사용자 변경과 섞어 커밋하지 않는다.
- push, PR 병합, force push, 이력 수정, 운영 서버 변경은 사용자가 하거나 사용자 승인을 받은 뒤에 한다.

## 문서 역할

- `README.md`: 프로젝트 소개, 결과, 실행 방법
- `docs/architecture.md`: 현재 구조와 데이터 계약
- `docs/development_log.md`: 시행착오와 의사결정 기록. 새로 결정한 내용만 짧게 추가한다.
