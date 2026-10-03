-- 커피 예측 서비스 스키마. `python -m coffee.pipeline migrate`가 소유자 계정으로 적용한다.
-- 여러 번 실행해도 결과가 같다. API는 SELECT 권한만 있는 계정으로 접속한다.

CREATE TABLE IF NOT EXISTS prices (
    date   date PRIMARY KEY,
    open   double precision,
    high   double precision,
    low    double precision,
    close  double precision NOT NULL,
    volume double precision
);

-- 동결한 모델 묶음(model_artifacts/<버전>). 서비스 중인 버전은 하나뿐이다.
CREATE TABLE IF NOT EXISTS models (
    model_version text PRIMARY KEY,
    train_end     date NOT NULL,
    metadata      jsonb NOT NULL,                  -- 방향·변동성 metadata.json
    is_active     boolean NOT NULL DEFAULT false,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS models_single_active ON models (is_active) WHERE is_active;

-- 예측은 한 번 쓰면 고치지 않는다. 실제 가격은 prices를 target_date로 조인해 본다.
CREATE TABLE IF NOT EXISTS forecasts (
    model_version  text NOT NULL REFERENCES models,
    origin_date    date NOT NULL,
    horizon        smallint NOT NULL CHECK (horizon IN (5, 20, 60)),
    target_date    date NOT NULL,
    origin_close   double precision NOT NULL,      -- 중심 전망(현재가 유지)
    prob_up        double precision NOT NULL CHECK (prob_up BETWEEN 0 AND 1),
    signal         text NOT NULL CHECK (signal IN ('buy', 'wait', 'hold')),
    price_low      double precision,               -- 80% 범위 (20·60일만)
    price_high     double precision,
    predicted_vol  double precision,               -- 예측 변동성(연율)
    vol_percentile double precision CHECK (vol_percentile BETWEEN 0 AND 1),  -- 최근 3년 예측 중 위치
    kind           text NOT NULL CHECK (kind IN ('live', 'backfill')),
    created_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (model_version, origin_date, horizon),
    CHECK (target_date > origin_date),
    CHECK (price_low IS NULL OR price_low <= price_high)
);

-- Jev로 분류한 뉴스. 모델 입력이 아니라 참고 정보다(노트북 05).
CREATE TABLE IF NOT EXISTS news_articles (
    content_hash   text PRIMARY KEY,               -- sha256(제목 + "\n" + 요약)
    url            text NOT NULL,
    title          text NOT NULL,
    source         text NOT NULL,
    event_at       timestamptz NOT NULL,           -- 발행 시각
    analyzed_at    timestamptz NOT NULL,
    available_at   timestamptz NOT NULL,           -- 이 시각 이후에만 쓸 수 있다
    label          text NOT NULL CHECK (label IN ('bullish', 'bearish', 'neutral', 'uncertain')),
    p_bullish      double precision NOT NULL,
    p_bearish      double precision NOT NULL,
    p_neutral      double precision NOT NULL,
    p_uncertain    double precision NOT NULL,
    relevance      double precision NOT NULL,
    confidence     double precision NOT NULL,
    model          text NOT NULL,
    prompt_version text NOT NULL,
    cost_usd       double precision NOT NULL DEFAULT 0  -- 새 파이프라인이 쓴 요청 비용(보관 분석은 0)
);
CREATE INDEX IF NOT EXISTS news_articles_event_at ON news_articles (event_at);

CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    command     text NOT NULL,
    started_at  timestamptz NOT NULL DEFAULT now(),
    finished_at timestamptz,
    status      text NOT NULL CHECK (status IN ('running', 'success', 'warning', 'failed')),
    steps       jsonb NOT NULL DEFAULT '[]',
    message     text
);
