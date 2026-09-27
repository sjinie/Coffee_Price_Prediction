<script setup>
import { computed, onMounted, ref } from 'vue'

const prices = ref([])
const predictions = ref([])
const models = ref([])
const pipeline = ref(null)
const sources = ref([])
const newsView = ref(null)
const newsError = ref('')
const newsLoading = ref(false)
const loading = ref(true)
const error = ref('')
const selectedHorizon = ref(60)
const theme = ref('system')
const refreshButton = ref(null)

const formatter = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 })
const dateFormatter = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium' })
const formatDate = value => value ? dateFormatter.format(new Date(`${value}T00:00:00`)) : '-'
const formatTime = value => value ? new Intl.DateTimeFormat('ko-KR', {
  dateStyle: 'medium', timeStyle: 'short',
}).format(new Date(value)) : '-'
const finiteNumber = value => value !== null && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null
const formatNumber = value => {
  const number = finiteNumber(value)
  return number === null ? '-' : formatter.format(number)
}
const formatPercent = value => {
  const number = finiteNumber(value)
  return number === null ? '이용 불가' : `${number >= 0 ? '+' : ''}${formatNumber(number * 100)}%`
}
const formatProbability = value => finiteNumber(value) === null ? '이용 불가' : `${formatNumber(Number(value) * 100)}%`
const signalStatusLabel = value => ({ validated: 'Validation 기준 통과', experimental: '실험적 확률', fallback: '뉴스 제외 대체 확률', news_feature: '뉴스 피처 사용', numeric_fallback: '수치 피처로 예측', unavailable: '이용 불가' }[value] || value || '이용 불가')
const newsModeLabel = value => ({ historical: '과거 공개 시각 기반 연구 재생', live: '실제 수집 시각 적용' }[value] || '뉴스 시점 정보 없음')
const signalDirection = item => {
  return ['UP', 'DOWN', 'FLAT'].includes(item.final_direction) ? item.final_direction : '이용 불가'
}
const directionSignalLabel = value => ({ UP: '상승', DOWN: '하락', FLAT: '보합' }[value] || value)
const newsImpactLabel = value => {
  const score = finiteNumber(value)
  return score === null ? '이용 불가' : score > 0 ? '강세' : score < 0 ? '약세' : '중립'
}
const conditionalModel = model => model?.metrics?.news_policy === 'conditional_feature'
const hasConditionalModels = computed(() => models.value.some(conditionalModel))
const newsUseLabel = item => {
  if (item.signal_status === 'news_feature') return `뉴스 피처 사용 · ${formatNumber(item.news_article_count)}건 · 점수 ${formatNumber(item.news_impact_score)}`
  if (item.signal_status === 'numeric_fallback') return finiteNumber(item.news_article_count) > 0
    ? `분석 기사 ${formatNumber(item.news_article_count)}건 · 집계 점수 0, 수치 피처로 예측`
    : '이용 가능한 기사 없음 · 수치 피처로 예측'
  return '뉴스 피처 사용 여부 확인 불가'
}

async function get(path) {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`)
  return response.json()
}

async function loadData() {
  loading.value = true
  error.value = ''
  void loadNews()
  try {
    ;[prices.value, predictions.value, models.value, pipeline.value, sources.value] = await Promise.all([
      get('/api/v1/prices?limit=365'),
      get('/api/v1/predictions?limit=1200'),
      get('/api/v1/models/current'),
      get('/api/v1/pipeline/status'),
      get('/api/v1/data-sources/status'),
    ])
  } catch (reason) {
    error.value = reason instanceof Error ? reason.message : String(reason)
  } finally {
    loading.value = false
  }
}
async function loadNews() {
  newsError.value = ''
  newsLoading.value = true
  try {
    newsView.value = await get('/api/v1/news/jev?limit=50')
  } catch {
    newsError.value = '뉴스 조회에 실패했습니다. 가격과 기존 예측은 계속 확인할 수 있습니다.'
  } finally {
    newsLoading.value = false
  }
}
const newsForecasts = computed(() => newsView.value?.forecast?.forecasts || [])
const newsArticles = computed(() => newsView.value?.articles || [])
const jevModel = computed(() => newsView.value?.forecast?.model || null)
const jevLabel = value => ({ bullish: 'Bullish · 상승 압력', bearish: 'Bearish · 하락 압력', neutral: '중립', uncertain: '판단 유보' }[value] || '미분류')
const newsForecastLabel = value => ({ experimental: '실험적 보정', insufficient_data: '학습 데이터 부족', unavailable: '이용 불가', no_news: '반영 가능한 뉴스 없음', stale_price: '가격 갱신 필요' }[value] || value)
const newsReason = item => ({
  insufficient_data: '평가 가능한 뉴스·후속 가격 이력이 부족합니다.',
  no_news: '해당 시점에 반영할 뉴스 신호가 없습니다.',
  unavailable: '자료 시점 또는 처리 상태를 확인해 주세요.',
}[item.status] || '')
const usedByForecast = article => newsForecasts.value.some(row => (row.article_ids || []).includes(article.article_id) || (row.article_ids || []).includes(article.analysis_id))
const hasLegacyDirection = item => finiteNumber(item.probability_up) !== null || ['UP', 'DOWN', 'FLAT'].includes(item.final_direction)
const hasLegacyNews = item => finiteNumber(item.news_impact_score) !== null || finiteNumber(item.news_article_count) !== null || Boolean(item.news_updated_at)
const formatCoefficient = value => {
  const number = finiteNumber(value)
  return number === null ? '-' : String(number)
}
const zeroNewsWeights = computed(() => {
  const coefficients = jevModel.value?.coefficients
  const lags = jevModel.value?.lags
  return jevModel.value?.status === 'experimental'
    && Array.isArray(coefficients)
    && Array.isArray(lags)
    && coefficients.length > 0
    && coefficients.length === lags.length
    && coefficients.every(value => value === 0)
})
const noExperimentalChange = item => item.status === 'experimental' && item.news_correction === 0 && zeroNewsWeights.value
const boundedWeight = value => typeof value === 'number' && Number.isFinite(value) && value >= 0 && value <= 1
const formatWeight = value => boundedWeight(value) ? value.toFixed(3) : '-'
const formatWeightInterval = value => Array.isArray(value) && value.length === 2 && value.every(boundedWeight) && value[0] <= value[1]
  ? `${formatWeight(value[0])} ~ ${formatWeight(value[1])}` : '-'
const newsEvidenceLabel = value => ({ improvement_supported: '연구 재평가에서 개선 근거 있음', not_demonstrated: '예측력 개선 미확인', insufficient_evaluation: '개선 판단에 필요한 평가 자료 부족' }[value] || '예측력 미평가')
const priceCorrectionPercent = item => item.adjusted_price == null || typeof item.news_correction !== 'number' ? '-' : formatPercent(Math.expm1(item.news_correction))
const newsEvaluationRows = computed(() => newsForecasts.value.filter(item => item.evaluation))
const articlePressure = article => {
  const values = [article.p_bullish, article.p_bearish, article.p_neutral, article.p_uncertain]
  if (![...values, article.relevance, article.confidence].every(boundedWeight)) return null
  const highest = Math.max(...values)
  const winners = values.filter(value => Math.abs(value - highest) <= 1e-8)
  return (winners.length === 1 && values[0] === highest ? 1
    : winners.length === 1 && values[1] === highest ? -1 : 0) * article.relevance * article.confidence
}
async function retry() {
  await loadData()
  refreshButton.value?.focus()
}
onMounted(loadData)

const orderedPrices = computed(() => [...prices.value].sort((a, b) => String(a.date).localeCompare(String(b.date))))
const latestPrice = computed(() => orderedPrices.value.at(-1))
const latestPriceChange = computed(() => {
  if (orderedPrices.value.length < 2) return null
  const previous = finiteNumber(orderedPrices.value.at(-2).close)
  const latest = finiteNumber(latestPrice.value?.close)
  if (previous === null || latest === null || previous === 0) return null
  return ((latest / previous) - 1) * 100
})
const latestPriceDate = computed(() => latestPrice.value?.date || '')
const currentModelForHorizon = horizon => models.value.find(model => model.horizons?.includes(horizon))
const isCurrentPrediction = item => {
  const currentModel = currentModelForHorizon(item.horizon)
  if (conditionalModel(currentModel)) return item.model_id === currentModel.model_id
  return !item.model_id || !currentModel?.model_id || item.model_id === currentModel.model_id
}
const futurePredictions = computed(() => Object.values(predictions.value
  .filter(isCurrentPrediction)
  .filter(item => item.actual_price == null && item.target_date > latestPriceDate.value)
  .reduce((latest, item) => {
    if (!latest[item.horizon] || item.origin_date > latest[item.horizon].origin_date) latest[item.horizon] = item
    return latest
  }, {}))
  .sort((a, b) => a.horizon - b.horizon))
const selectedPredictions = computed(() => predictions.value
  .filter(isCurrentPrediction)
  .filter(item => item.horizon === selectedHorizon.value
    && finiteNumber(item.actual_price) !== null && finiteNumber(item.predicted_price) !== null)
  .sort((a, b) => String(a.target_date).localeCompare(String(b.target_date)))
  .slice(-180))
// 방향은 인접 평가점이 아닌 각 예측 기준일 종가와 비교한다.
const evaluation = computed(() => {
  const originPrices = new Map(prices.value.map(item => [item.date, finiteNumber(item.close)]))
  return selectedPredictions.value.map(item => {
    const origin = originPrices.get(item.origin_date)
    const predicted = Number(item.predicted_price)
    const actual = Number(item.actual_price)
    const known = origin != null && origin > 0
    const predictedChange = known ? (predicted / origin - 1) * 100 : null
    const actualChange = known ? (actual / origin - 1) * 100 : null
    return { ...item, origin, predictedChange, actualChange,
      matched: known ? Math.sign(predicted - origin) === Math.sign(actual - origin) : null,
      priceError: predicted - actual }
  })
})
const evaluationSummary = computed(() => {
  const rows = evaluation.value
  const known = rows.filter(item => item.matched !== null)
  const matched = known.filter(item => item.matched).length
  const directional = known.filter(item => item.actualChange !== 0)
  const up = directional.filter(item => item.actualChange > 0)
  const down = directional.filter(item => item.actualChange < 0)
  const balancedAccuracy = up.length && down.length
    ? (up.filter(item => item.predictedChange > 0).length / up.length
      + down.filter(item => item.predictedChange < 0).length / down.length) * 50 : null
  return { count: known.length, matched, missing: rows.length - known.length,
    accuracy: known.length ? matched / known.length * 100 : null,
    balancedAccuracy, directionalCount: directional.length,
    mae: rows.length ? rows.reduce((sum, item) => sum + Math.abs(item.priceError), 0) / rows.length : null,
    rmse: rows.length ? Math.sqrt(rows.reduce((sum, item) => sum + item.priceError ** 2, 0) / rows.length) : null }
})
const directionDomain = computed(() => {
  const values = evaluation.value.flatMap(item => [item.actualChange, item.predictedChange])
    .filter(value => value !== null)
  const bound = Math.max(...values.map(Math.abs), 1) * 1.05
  return [-bound, bound]
})
const errorBound = computed(() => Math.max(...evaluation.value.map(item => Math.abs(item.priceError)), 1) * 1.05)
const evaluationX = index => (index + .5) / evaluation.value.length * 900
const directionY = value => 100 - value / directionDomain.value[1] * 100
const directionPaths = key => {
  // 기준일 가격이 없는 구간은 선을 연결하지 않는다.
  let drawing = false
  return evaluation.value.map((item, index) => {
    if (item[key] === null) { drawing = false; return '' }
    const command = drawing ? 'L' : 'M'
    drawing = true
    return command + evaluationX(index) + ',' + directionY(item[key])
  }).join(' ').trim()
}
const directionLabel = value => value === null ? '확인 불가' : value > 0 ? '↑ 상승' : value < 0 ? '↓ 하락' : '→ 보합'
const matchLabel = value => value === null ? '평가 제외' : value ? '일치' : '불일치'
const selectedEvaluationKey = ref('')
const evaluationKey = item => item.model_id + ':' + item.origin_date + ':' + item.target_date
const selectedEvaluation = computed(() => evaluation.value.find(item => evaluationKey(item) === selectedEvaluationKey.value) ?? evaluation.value.at(-1))
const evaluationTitle = item => `${formatDate(item.target_date)} · 예측 ${directionLabel(item.predictedChange)} / 실제 ${directionLabel(item.actualChange)} · ${matchLabel(item.matched)} · 오차 ${formatNumber(item.priceError)} ¢/lb`
const selectedModel = computed(() => currentModelForHorizon(selectedHorizon.value))
const dlinearModel = computed(() => models.value.find(model => model.horizons?.includes(60) && model.name?.startsWith('DLinear')))
const oldestFreshness = computed(() => {
  if (!sources.value.length) return null
  return [...sources.value].sort((a, b) => String(a.last_data_date).localeCompare(String(b.last_data_date)))[0]
})

const historicalPrices = computed(() => orderedPrices.value
  .map(item => ({ ...item, close: finiteNumber(item.close) }))
  .filter(item => item.date)
  .slice(-180))
const historicalForecasts = computed(() => {
  const byTarget = new Map()
  predictions.value
    .filter(isCurrentPrediction)
    .filter(item => item.horizon === selectedHorizon.value)
    .filter(item => item.target_date && item.origin_date && item.origin_date < item.target_date)
    .filter(item => item.target_date <= latestPriceDate.value && finiteNumber(item.predicted_price) !== null)
    .forEach(item => {
      const previous = byTarget.get(item.target_date)
      if (!previous || item.origin_date > previous.origin_date) byTarget.set(item.target_date, item)
    })
  return byTarget
})
const historicalComparison = computed(() => historicalPrices.value.map(item => ({
  ...item,
  forecast: item.close === null ? null : historicalForecasts.value.get(item.date) || null,
})))
const comparisonDomain = computed(() => chartDomain(historicalComparison.value.flatMap(item => [
  item.close, item.forecast?.predicted_price,
]).map(finiteNumber).filter(value => value !== null)))
function chartDomain(values) {
  if (!values.length) return [0, 1]
  const min = Math.min(...values)
  const max = Math.max(...values)
  const padding = (max - min) * 0.05 || Math.max(Math.abs(max) * 0.05, 1)
  return [min - padding, max + padding]
}
const comparisonDateRange = computed(() => [
  historicalComparison.value[0]?.date || '',
  historicalComparison.value.at(-1)?.date || '',
])
const dateX = value => {
  const [start, end] = comparisonDateRange.value.map(date => Date.parse(`${date}T00:00:00Z`))
  const date = Date.parse(`${value}T00:00:00Z`)
  return Number.isFinite(date) && Number.isFinite(start) && Number.isFinite(end)
    ? ((date - start) / (end - start || 1)) * 900 : 0
}
const priceY = value => {
  const [min, max] = comparisonDomain.value
  return 280 - ((Number(value) - min) / (max - min || 1)) * 280
}
function datedPath(values, valueFor) {
  let drawing = false
  return values.map(item => {
    const value = finiteNumber(valueFor(item))
    if (value === null) { drawing = false; return '' }
    const command = drawing ? 'L' : 'M'
    drawing = true
    return `${command}${dateX(item.date).toFixed(1)},${priceY(value).toFixed(1)}`
  }).join(' ').trim()
}
const comparisonActualPath = computed(() => datedPath(historicalComparison.value, item => item.close))
const comparisonPredictedPath = computed(() => datedPath(historicalComparison.value, item => item.forecast?.predicted_price))
const comparisonForecastCount = computed(() => historicalComparison.value.filter(item => item.close !== null && item.forecast).length)
const latestHistoricalForecast = computed(() => finiteNumber(latestPrice.value?.close) === null
  ? null : historicalForecasts.value.get(latestPriceDate.value) || null)
const latestForecastError = computed(() => {
  const prediction = finiteNumber(latestHistoricalForecast.value?.predicted_price)
  const actual = finiteNumber(latestPrice.value?.close)
  return prediction === null || actual === null ? null : prediction - actual
})
const formatSignedNumber = value => {
  const number = finiteNumber(value)
  return number === null ? '-' : `${number > 0 ? '+' : ''}${formatNumber(number)}`
}
const statusLabel = value => ({ success: '완료', partial: '부분 완료', failed: '실패', running: '실행 중', existing: '저장 자료', current: '최신 확인' }[value] || '대기')
const ticks = domain => Array.from({ length: 5 }, (_, index) => domain[1] - (domain[1] - domain[0]) * index / 4)
</script>

<template>
  <a class="skip-link" href="#dashboard">본문 바로가기</a>
  <main id="dashboard" tabindex="-1" :data-theme="theme">
    <header class="page-heading">
      <div class="brand"><h1><a href="#dashboard" aria-label="커피 선물 대시보드 처음으로">커피 선물</a></h1><span>KC=F · ICE Coffee C</span></div>
      <nav aria-label="대시보드 탐색"><a href="#comparison-title">예측 검증</a><a href="#jev-title">뉴스 분석</a><a href="#sources-title">데이터</a></nav>
      <div class="heading-tools"><span>가격 기준 <b>{{ formatDate(latestPrice?.date) }}</b></span><label class="theme-control"><span class="sr-only">화면 테마</span><select v-model="theme" aria-label="화면 테마"><option value="system">시스템</option><option value="light">밝게</option><option value="dark">어둡게</option></select></label><button ref="refreshButton" class="refresh" :disabled="loading" @click="loadData">{{ loading ? '조회 중…' : '새로고침' }}</button></div>
    </header>

    <div v-if="loading" class="loading-state" role="status" aria-live="polite"><p>가격과 예측 데이터를 불러오고 있습니다.</p><div class="skeleton metrics-skeleton"></div><div class="skeleton chart-skeleton"></div></div>
    <div v-else-if="error" class="notice error" role="alert"><strong>데이터를 불러오지 못했습니다.</strong><p>{{ error }}</p><button class="refresh" @click="retry">다시 시도</button></div>
    <template v-else>
      <div v-if="!prices.length" class="notice" role="status"><strong>아직 저장된 가격이 없습니다.</strong><p>데이터 적재가 끝나면 다시 조회해 주세요.</p></div>
      <section class="metrics" aria-label="주요 지표">
        <article class="primary-metric"><span>최근 종가</span><strong>{{ formatNumber(latestPrice?.close) }}<small>¢/lb</small></strong><small>{{ formatDate(latestPrice?.date) }}</small></article>
        <article><span>직전 거래일 대비</span><strong :class="latestPriceChange === null ? '' : latestPriceChange >= 0 ? 'up' : 'down'">{{ latestPriceChange === null ? '-' : (latestPriceChange >= 0 ? '+' : '') + formatNumber(latestPriceChange) + '%' }}</strong><small>종가 변동률</small></article>
        <article><span>최근일 고가 / 저가</span><strong class="range-value">{{ formatNumber(latestPrice?.high) }}<em>/</em>{{ formatNumber(latestPrice?.low) }}</strong><small>원천 OHLC · ¢/lb</small></article>
        <article v-if="hasConditionalModels"><span>{{ selectedHorizon }}일 표시 구간 가격 RMSE</span><strong>{{ formatNumber(evaluationSummary.rmse) }}</strong><small>성숙 예측 {{ evaluation.length }}건 · ¢/lb</small></article>
        <article v-else><span>60일 Test RMSE</span><strong>{{ dlinearModel?.metrics?.test_rmse ?? '-' }}</strong><small>로그수익률 · DLinear</small></article>
        <article><span>가장 오래된 소스</span><strong class="date-value">{{ formatDate(oldestFreshness?.last_data_date) }}</strong><small class="source-id">{{ oldestFreshness?.source ?? '-' }}</small></article>
      </section>

      <section class="comparison hero-comparison" aria-labelledby="comparison-title">
        <div class="comparison-heading">
          <div><h2 id="comparison-title">실제 가격과 과거 예측</h2><span id="price-title" class="sr-only">가격 추이</span><p>같은 목표 날짜에 맞춰 비교합니다. 점선은 과거 기준일의 저장 예측입니다.</p></div>
          <div class="tabs" role="group" aria-label="예측 horizon 선택"><button v-for="horizon in [5,20,60]" :key="horizon" :aria-pressed="selectedHorizon === horizon" :class="{ active: selectedHorizon === horizon }" @click="selectedHorizon = horizon">{{ horizon }}거래일</button></div>
        </div>
        <div class="chart-meta"><div class="legend"><span class="actual">실제 종가</span><span class="predicted">과거 예측</span></div><span>{{ selectedModel?.name ?? '모델 없음' }} · 최근 {{ historicalComparison.length }}거래일</span></div>
        <template v-if="historicalComparison.length">
          <div :key="selectedHorizon" class="chart-frame comparison-frame">
            <div class="y-axis" aria-hidden="true"><span v-for="(tick,index) in ticks(comparisonDomain)" :key="index">{{ formatNumber(tick) }}</span></div>
            <div class="chart" role="img" :aria-label="`${selectedHorizon}거래일 예측과 실제 종가를 목표 날짜로 정렬한 비교 차트, 단위 센트/파운드`"><svg viewBox="0 0 900 280" preserveAspectRatio="none"><line v-for="y in [0,70,140,210,280]" :key="y" x1="0" :y1="y" x2="900" :y2="y"/><path :d="comparisonActualPath" class="actual-line"/><path :d="comparisonPredictedPath" class="predicted-line"/></svg></div>
          </div>
          <div class="axis"><span>{{ formatDate(comparisonDateRange[0]) }}</span><span>목표 날짜 기준</span><span>{{ formatDate(comparisonDateRange[1]) }}</span></div>
          <div class="comparison-callout"><template v-if="finiteNumber(latestPrice?.close) !== null"><span>최근 실제값 <b>{{ formatNumber(latestPrice?.close) }} ¢/lb</b> · {{ formatDate(latestPriceDate) }}</span><span v-if="latestHistoricalForecast" class="prediction-callout">그날의 예측 <b>{{ formatNumber(latestHistoricalForecast.predicted_price) }} ¢/lb</b> · 기준 {{ formatDate(latestHistoricalForecast.origin_date) }}<small>오차 {{ formatSignedNumber(latestForecastError) }} ¢/lb</small></span><span v-else>최근 실제값의 과거 예측은 아직 없습니다.</span></template><span v-else>최신 종가가 없어 비교할 수 없습니다.</span><span>연결된 예측 {{ comparisonForecastCount }}개</span></div>
          <p class="footnote">저장된 과거 기준일 예측이며, 당시 실시간으로 공개한 기록을 뜻하지 않습니다.</p>
        </template>
        <p v-else class="empty-state">가격 데이터가 없습니다.</p>
      </section>

      <section class="panel news-panel" aria-labelledby="jev-title">
        <div class="panel-heading"><h2 id="jev-title">{{ hasConditionalModels ? 'Jev 뉴스 분석' : 'Jev 뉴스 기반 실험적 가격 보정' }}</h2><span class="unit">아라비카 가격 압력</span></div>
        <p v-if="newsError" role="status">{{ newsError }}</p>
        <p v-if="newsView?.latest_run?.status === 'failed'" role="status">최근 뉴스 처리에 실패했습니다. 아래는 마지막 저장 결과입니다.</p>
        <p v-else-if="newsView?.latest_run?.status === 'partial'" role="status">일부 뉴스만 처리되었습니다. 뉴스 분석의 수집 범위를 확인해 주세요.</p>
        <p v-if="hasConditionalModels" class="section-note">선택 모델은 기준 거래일 UTC 23:00까지 공개·수정·선정·분석이 완료된 기사만 뉴스 피처에 사용합니다. 실제 이용 가능해진 첫 거래일에 반영하며, 기사 발생 후 7일을 넘겨 분석한 기록은 새 뉴스로 넣지 않습니다. 기사별 방향값(+1/−1/0) × 관련성 × 분류 확신도를 합산한 뒤 tanh를 적용하며, 해당 날짜의 점수가 없거나 0이면 수치 피처 모델로 예측합니다. 아래 기사 목록은 분석 증거이며 현재 예측의 입력 기사 목록과 같지 않을 수 있습니다.</p>
        <p v-else class="section-note">뉴스의 분류 확률은 미래 가격의 상승 확률이 아닙니다. 실험적 보정의 개선 근거는 지평별 평가 상태를 확인하세요.</p>
        <p v-if="newsView?.selection?.selected_count != null" class="section-note">{{ newsView.selection.requested_start }} ~ {{ newsView.selection.requested_end }} · 뉴욕 날짜별 최대 1건 · 선정 {{ newsView.selection.selected_count }}건 · 분석 완료 {{ newsView.selection.selected_analysis_ids?.length ?? 0 }}건. 공급·작황·날씨·무역 관련성을 기준으로 선정합니다.</p>
        <template v-if="newsView?.forecast && !hasConditionalModels">
          <p class="section-note">실행 {{ formatTime(newsView.forecast.as_of) }} · 가격 기준 {{ formatDate(newsView.forecast.price_date) }} · {{ newsView.forecast.training_availability === 'research' ? '연구용 시점 기준 · 과거 기사 재분류' : '실제 이용 가능 시각 기준' }}</p>
          <p class="section-note">이 예측을 만들 때 수집한 후보 {{ newsView.forecast.source_status.source_count ?? '-' }}건 · 수집처의 표본으로, 기간 내 모든 뉴스를 포함하지 않습니다.</p>
          <template v-if="jevModel?.horizon_models">
            <p class="section-note">반영 강도는 0~1입니다. 방향은 뉴스가 정하고, 강도는 가격 변동성에 비례한 효과를 얼마나 적용할지 정합니다. 강도 0.2는 가격 20% 변화를 뜻하지 않습니다.</p>
            <p class="section-note">현재 뉴스 신호 {{ formatNumber(newsForecasts[0]?.news_signal) }} · {{ newsImpactLabel(newsForecasts[0]?.news_signal) }}. 최신 기사에 더 큰 비중을 두고, 이전 거래일의 뉴스도 시차를 두어 반영합니다.</p>
          </template>
          <details v-if="jevModel" class="forecast-version"><summary>계산 방식과 가정</summary>
            <small>반감기 {{ formatCoefficient(jevModel.half_life) }} 달력일 · 시차 {{ (jevModel.lags || []).join(', ') || '-' }} 거래일 · 지평 감쇠 τ {{ formatCoefficient(jevModel.horizon_decay?.tau) }} (exp(-(h-5)/τ))</small>
            <template v-if="jevModel.horizon_models">
              <small>기사 신호 = (Bullish 확률 − Bearish 확률) × 커피 관련성. 확률 차이가 클수록 강하게 반영합니다. 중립·판단 유보 확률도 유지하며, 같은 확률분포에서 계산된 Jev confidence는 다시 곱하지 않습니다.</small>
              <small>보정 로그수익률 = 최근 변동성 × 지평 배수 × 뉴스 신호 × 반영 강도. 보정 가격 = 기본 예측 × exp(보정 로그수익률).</small>
              <small>시차별 비중 {{ (jevModel.lag_weights || []).map(formatWeight).join(' / ') }}. 반감기·시차 비중·지평 감쇠는 고정 가정이며 통계적으로 확정된 값이 아닙니다.</small>
              <small>강도는 지평별 후속 수익률로 추정합니다. 사전 가정은 효과 없음 50% + 0~1 균등분포 50%이며, 과거 5일 수익률과 평균을 통제합니다. 95% 사후구간은 이 가정에 따른 강도의 불확실성으로 가격 예측구간이나 상승 확률이 아닙니다.</small>
              <small>현재까지 확보한 기사의 발행·선정 시점으로 시차를 재구성한 연구용 추정입니다. 과거 당시 실시간 성능을 검증한 결과가 아닙니다.</small>
            </template>
            <small v-else>기존 모델 계수 {{ (jevModel.coefficients || []).map(formatCoefficient).join(', ') || '-' }}</small>
          </details>
          <div class="news-table-wrap" tabindex="0" role="region" aria-label="뉴스 보정 예측 표"><table class="forecast-table"><caption class="sr-only">기존 예측과 뉴스 보정 예측 및 반영 강도 비교</caption><thead><tr><th scope="col">지평</th><th scope="col">기존 예측</th><th scope="col">뉴스 반영 예측</th><th scope="col">가격 보정</th><th v-if="jevModel?.horizon_models" scope="col">반영 강도 · 0~1</th><th scope="col">상태</th></tr></thead><tbody>
            <tr v-for="item in newsForecasts" :key="item.horizon">
              <th scope="row">{{ item.horizon }}거래일</th><td>{{ formatNumber(item.base_predicted_price) }} ¢/lb</td>
              <td>{{ formatNumber(item.adjusted_price) }}<span v-if="item.adjusted_price != null"> ¢/lb</span></td>
              <td>{{ item.adjusted_price == null ? '-' : formatNumber(item.adjusted_price - item.base_predicted_price) + ' ¢/lb' }}<small>{{ priceCorrectionPercent(item) }}</small></td>
              <td v-if="jevModel?.horizon_models">{{ formatWeight(item.news_weight) }}<small v-if="boundedWeight(item.news_weight)">95% 사후구간 {{ formatWeightInterval(item.news_weight_interval) }}</small><small>겹치지 않는 학습 구간 {{ jevModel.horizon_models[item.horizon]?.calibration_rows ?? 0 }}개</small></td>
              <td>{{ newsForecastLabel(item.status) }}<small>{{ noExperimentalChange(item) ? '학습된 뉴스 가중치가 모두 0이어서 가격 변화가 없습니다.' : newsReason(item) }}</small><small v-if="item.evidence_status">{{ newsEvidenceLabel(item.evidence_status) }}</small><small>입력 기사 {{ item.news_article_count ?? 0 }}건</small></td>
            </tr>
          </tbody></table></div>
          <details v-if="newsEvaluationRows.length" class="news-details"><summary>기본 예측과 성능 비교 · 과거 기사 재분류 연구</summary>
            <p class="section-note">뒤쪽 40% 기간을 순서대로 평가하며, 각 예측일보다 먼저 결과가 확정된 자료만 학습합니다. 같은 날짜의 기본 예측과 비교합니다. RMSE·MAE는 로그수익률 단위입니다.</p>
            <div class="news-table-wrap" tabindex="0" role="region" aria-label="뉴스 보정 성능 비교 표"><table class="forecast-table"><caption class="sr-only">동일 기준일 뉴스 보정 전후 연구 재평가</caption><thead><tr><th scope="col">지평 / 평가 수</th><th scope="col">기간</th><th scope="col">RMSE · 기본 → 뉴스</th><th scope="col">MAE · 기본 → 뉴스</th><th scope="col">방향 일치율 · 기본 → 뉴스</th></tr></thead><tbody><tr v-for="item in newsEvaluationRows" :key="item.horizon"><th scope="row">{{ item.horizon }}거래일<small>{{ item.evaluation.n }}개</small></th><td>{{ formatDate(item.evaluation.period_start) }} ~ {{ formatDate(item.evaluation.period_end) }}</td><td>{{ item.evaluation.baseline?.rmse?.toFixed(4) ?? '-' }} → {{ item.evaluation.adjusted?.rmse?.toFixed(4) ?? '-' }}</td><td>{{ item.evaluation.baseline?.mae?.toFixed(4) ?? '-' }} → {{ item.evaluation.adjusted?.mae?.toFixed(4) ?? '-' }}</td><td>{{ formatProbability(item.evaluation.baseline?.direction_accuracy) }} → {{ formatProbability(item.evaluation.adjusted?.direction_accuracy) }}</td></tr></tbody></table></div>
            <p class="section-note">시간 의존성을 고려한 블록 재표본추출로 오차 개선을 검토합니다. 평가 자료가 부족하거나 개선 구간이 0을 포함하면 개선을 확인했다고 표시하지 않습니다.</p>
            <p class="section-note">방향 일치율은 상승·하락·보합을 모두 비교합니다. Persistence는 항상 보합을 예측하므로 이 수치만으로 뉴스의 개선을 판단하지 않습니다.</p>
          </details>
        </template>
        <p v-else-if="newsLoading" class="empty-state" role="status">뉴스 분석 결과를 불러오는 중입니다.</p>
        <p v-else-if="!hasConditionalModels" class="empty-state">아직 뉴스 분석·보정 결과가 없습니다.</p>
        <details v-if="newsArticles.length" class="news-details"><summary>분석한 최근 뉴스 {{ newsArticles.length }}건</summary><ul class="news-list"><li v-for="article in newsArticles" :key="article.analysis_id">
          <a v-if="/^https?:\/\//.test(article.url)" :href="article.url" target="_blank" rel="noopener noreferrer">{{ article.title }}</a><span v-else>{{ article.title }}</span>
          <small>{{ article.source }} · {{ article.time_basis === 'published_at' ? '발행' : '발견' }} {{ formatTime(article.event_at) }} · 분석 {{ formatTime(article.analyzed_at) }}</small>
          <span>{{ jevLabel(article.label) }} · 분류 확률 강세 {{ formatProbability(article.p_bullish) }} / 약세 {{ formatProbability(article.p_bearish) }} / 중립 {{ formatProbability(article.p_neutral) }} / 판단 유보 {{ formatProbability(article.p_uncertain) }}</span>
          <small v-if="hasConditionalModels">기사 방향값 × 관련성 × 확신도 {{ formatNumber(articlePressure(article)) }} (−1~1) · 커피 관련성 {{ formatProbability(article.relevance) }} · Jev 분류 확신도 {{ formatProbability(article.confidence) }}</small><small v-else-if="jevModel?.horizon_models">기사 방향 신호 {{ formatNumber(article.p_bullish - article.p_bearish) }} × 관련성 {{ formatProbability(article.relevance) }} · Jev 분류 확신도 {{ formatProbability(article.confidence) }} (참고용)</small><small v-if="!hasConditionalModels && usedByForecast(article)">이번 보정의 뉴스 입력에 포함</small>
        </li></ul></details>
      </section>

      <div class="workspace">
        <section class="panel future" aria-labelledby="forecast-title">
          <div class="panel-heading"><h2 id="forecast-title">{{ hasConditionalModels ? '선택 모델 예측' : '기본 모델 예측' }}</h2><span class="unit">¢/lb</span></div>
          <p class="section-note">각 지평의 가장 최근 예측 기준일<span v-if="hasConditionalModels"> · 방향은 예측 가격과 기준일 가격의 비교이며 상승 확률이 아닙니다. 뉴스 이용 가능 시각은 각 기준 거래일 23:00 UTC까지입니다.</span></p>
          <table v-if="futurePredictions.length" class="forecast-table"><caption class="sr-only">거래일 지평별 예측 가격, 방향, 뉴스 사용 상태</caption><thead><tr><th scope="col">지평 / 모델</th><th scope="col">목표일</th><th scope="col" class="number">예측가 / 방향·뉴스</th></tr></thead><tbody><tr v-for="item in futurePredictions" :key="item.horizon"><th scope="row">{{ item.horizon }}거래일<small>{{ currentModelForHorizon(item.horizon)?.name || '모델 정보 없음' }}</small></th><td>{{ formatDate(item.target_date) }}<small>기준 {{ formatDate(item.origin_date) }}</small></td><td class="number forecast-number">{{ formatNumber(item.predicted_price) }}<small>예상 단순수익률 {{ formatPercent(finiteNumber(item.predicted_return) === null ? null : Math.expm1(Number(item.predicted_return))) }}</small><template v-if="conditionalModel(currentModelForHorizon(item.horizon))"><small>가격 예측 방향 <b>{{ directionSignalLabel(signalDirection(item)) }}</b> · 상승 확률 없음</small><span class="signal-status" :class="item.signal_status">{{ signalStatusLabel(item.signal_status) }}</span><small>{{ newsUseLabel(item) }}</small></template><template v-else-if="hasLegacyDirection(item)"><span class="signal-status" :class="item.signal_status">{{ signalStatusLabel(item.signal_status) }}</span><small title="실제 보합 사례를 제외한 상승 확률">보합 제외 상승 확률 {{ formatProbability(item.probability_up) }} · 방향 {{ directionSignalLabel(signalDirection(item)) }}</small></template><small v-else>별도 상승 확률·방향 분류 결과 없음</small><template v-if="!conditionalModel(currentModelForHorizon(item.horizon))"><template v-if="hasLegacyNews(item)"><small>뉴스 {{ newsImpactLabel(item.news_impact_score) }} · {{ finiteNumber(item.news_article_count) === null ? '이용 불가' : formatNumber(item.news_article_count) + '건' }} · {{ formatTime(item.news_updated_at) }}</small><small>{{ newsModeLabel(item.news_availability) }}</small></template><small v-else><a href="#jev-title">Jev 뉴스 기반 실험적 보정 보기</a></small></template><details v-if="item.classifier_version || item.model_version" class="forecast-version"><summary>모델 버전</summary><small v-if="!conditionalModel(currentModelForHorizon(item.horizon))">분류 {{ item.classifier_version || '이용 불가' }}</small><small>모델 {{ item.model_version || '이용 불가' }}</small></details></td></tr></tbody></table>
          <p v-else class="empty-state">표시할 미성숙 예측이 없습니다.</p>
          <p class="footnote">표시된 기준일의 예측입니다. 현재 시점의 실시간 예측이 아닐 수 있습니다.</p>
        </section>

        <section class="panel evaluation-panel" aria-labelledby="evaluation-title">
          <div class="panel-heading"><h2 id="evaluation-title">예측 오차와 방향 검증</h2><span class="unit">현재 표시 구간</span></div>
          <div v-if="evaluation.length" class="evaluation">
            <div class="evaluation-summary">
              <span>균형 방향 정확도 <strong>{{ formatNumber(evaluationSummary.balancedAccuracy) }}{{ evaluationSummary.balancedAccuracy === null ? '' : '%' }}</strong> <small>상승·하락 {{ evaluationSummary.directionalCount }}건</small></span>
              <span>방향 일치 <strong>{{ formatNumber(evaluationSummary.accuracy) }}{{ evaluationSummary.accuracy === null ? '' : '%' }}</strong> <small>{{ evaluationSummary.matched }}/{{ evaluationSummary.count }}건</small></span>
              <span>가격 RMSE <strong>{{ formatNumber(evaluationSummary.rmse) }}</strong> <small>¢/lb · {{ evaluation.length }}건</small></span>
              <span>가격 MAE <strong>{{ formatNumber(evaluationSummary.mae) }}</strong> <small>¢/lb</small></span>
            </div>
            <p class="section-note">현재 표시된 성숙 예측 {{ evaluation.length }}건의 가격 오차와 방향을 재계산합니다. 균형 정확도는 실제 보합을 제외한 상승·하락 재현율의 평균이며 예측 보합은 오답입니다. 한쪽 실제 방향이 없으면 표시하지 않습니다. 방향 평가에서 기준가 없음 {{ evaluationSummary.missing }}건 제외.</p>
            <p v-if="hasConditionalModels" class="section-note">2026년 과거 예측 재생으로 이미 관측한 결과입니다. 미사용 Test 또는 당시 실시간 공개 성능을 뜻하지 않습니다. 뉴스 입력은 각 날짜의 실제 이용 가능 시각으로 제한하며, 03_8의 소급 연구 뉴스 성능과 직접 같지 않습니다.</p>
            <div class="panel-heading"><h3>예측 방향 · 실제 등락률</h3><span class="unit">기준일 대비 % · 위 범례와 동일</span></div>
            <div class="chart-frame direction-frame">
              <div class="y-axis" aria-hidden="true"><span>{{ formatNumber(directionDomain[1]) }}%</span><span>0</span><span>{{ formatNumber(directionDomain[0]) }}%</span></div>
              <div class="chart" role="img" aria-label="기준일 대비 실제 및 예측 등락률. 0 위는 상승, 아래는 하락.">
                <svg viewBox="0 0 900 200" preserveAspectRatio="none">
                  <line v-for="y in [0,100,200]" :key="y" x1="0" :y1="y" x2="900" :y2="y" :class="{ 'zero-line': y === 100 }"/>
                  <path :d="directionPaths('actualChange')" class="actual-line"/>
                  <path :d="directionPaths('predictedChange')" class="predicted-line"/>
                </svg>
              </div>
            </div>
            <div class="panel-heading"><h3>가격 오차 <span>예측 − 실제 · + 과대 / − 과소</span></h3><span class="unit">빨강: 방향 불일치 · 회색: 일치 · 빈 막대: 평가 제외</span></div>
            <div class="chart-frame error-frame">
              <div class="y-axis" aria-hidden="true"><span>+{{ formatNumber(errorBound) }}</span><span>0</span><span>−{{ formatNumber(errorBound) }}</span></div>
              <div class="chart" role="img" aria-label="가격 오차 막대. 0 위는 과대예측, 아래는 과소예측. 단위 센트/파운드.">
                <svg viewBox="0 0 900 160" preserveAspectRatio="none">
                  <line v-for="y in [0,80,160]" :key="y" x1="0" :y1="y" x2="900" :y2="y" :class="{ 'zero-line': y === 80 }"/>
                  <rect v-for="(item,index) in evaluation" :key="evaluationKey(item)" :x="evaluationX(index) - 900 / evaluation.length * .35" :y="80 - Math.max(item.priceError, 0) / errorBound * 80" :width="900 / evaluation.length * .7" :height="Math.abs(item.priceError) / errorBound * 80" :class="['error-bar', { mismatch: item.matched === false, unknown: item.matched === null }]"><title>{{ evaluationTitle(item) }}</title></rect>
                </svg>
              </div>
            </div>
            <div class="axis"><span>{{ formatDate(evaluation[0]?.target_date) }}</span><span>목표일 · ¢/lb</span><span>{{ formatDate(evaluation.at(-1)?.target_date) }}</span></div>
            <div v-if="selectedEvaluation" class="evaluation-detail">
              <label>목표일별 확인 <select :value="evaluationKey(selectedEvaluation)" @change="selectedEvaluationKey = $event.target.value"><option v-for="item in evaluation" :key="evaluationKey(item)" :value="evaluationKey(item)">{{ formatDate(item.target_date) }} · {{ matchLabel(item.matched) }}</option></select></label>
              <p>기준 {{ formatDate(selectedEvaluation.origin_date) }} · {{ formatNumber(selectedEvaluation.origin) }} ¢/lb</p>
              <p>예측 <b>{{ directionLabel(selectedEvaluation.predictedChange) }} {{ formatNumber(selectedEvaluation.predictedChange) }}%</b> / 실제 <b>{{ directionLabel(selectedEvaluation.actualChange) }} {{ formatNumber(selectedEvaluation.actualChange) }}%</b> · <strong :class="{ down: selectedEvaluation.matched === false }">{{ matchLabel(selectedEvaluation.matched) }}</strong></p>
              <p>예측 {{ formatNumber(selectedEvaluation.predicted_price) }} / 실제 {{ formatNumber(selectedEvaluation.actual_price) }} · 오차 <b>{{ formatNumber(selectedEvaluation.priceError) }} ¢/lb</b></p>
            </div>
          </div>
          <p v-else class="empty-state">이 지평의 실제값이 연결된 예측이 없습니다.</p>
        </section>

        <aside class="side-details" aria-label="모델 및 실행 정보">
          <section class="panel model"><div class="panel-heading"><h2>사용 모델</h2><span class="unit">{{ models.length }}개</span></div><div v-for="model in models" :key="model.model_id" class="model-row"><strong>{{ model.name }}</strong><span>{{ model.horizons.join(' · ') }}거래일</span><template v-if="conditionalModel(model)"><small>구성 {{ model.metrics.components?.map(part => `${part.name} 설정 ${part.setting} · ${formatNumber(part.weight * 100)}%`).join(' / ') || '-' }}</small><small>뉴스 정책 {{ model.metrics.news_policy }} · 학습 {{ model.metrics.training_news_availability }}(소급 연구) / 예측 {{ model.metrics.news_availability }}(실제 이용 시각)</small><small>학습 종료 {{ formatDate(model.training_end) }} · 선택 근거 {{ model.metrics.selection_source }}</small></template><code>{{ model.model_id }}</code></div><p v-if="!models.length" class="empty-state">등록된 모델이 없습니다.</p><p v-if="!hasConditionalModels" class="caveat">60일 DLinear의 기존 Test RMSE는 Naive 대비 9.59% 낮았지만, 기간에 따른 차이로 안정적 우위는 미확정입니다.</p></section>
          <section class="panel run-panel"><div class="panel-heading"><h2>마지막 실행</h2><span class="status" :class="pipeline?.status"><i aria-hidden="true"></i>{{ statusLabel(pipeline?.status) }}</span></div><dl v-if="pipeline"><div><dt>모드</dt><dd>{{ pipeline.mode }}</dd></div><div><dt>완료</dt><dd>{{ formatTime(pipeline.finished_at) }}</dd></div><div><dt>처리 결과</dt><dd>{{ pipeline.message }}</dd></div></dl><p v-else class="empty-state">실행 기록이 없습니다.</p></section>
        </aside>
      </div>

      <section class="panel sources-panel" aria-labelledby="sources-title"><div class="panel-heading"><h2 id="sources-title">데이터 소스 <span>{{ sources.length }}개</span></h2><span class="section-note">마지막 관측일 · 수집 상태</span></div><ul v-if="sources.length" class="source-list"><li v-for="source in sources" :key="source.source"><div><strong>{{ source.source }}</strong><span class="source-status" :class="source.status">{{ statusLabel(source.status) }}</span></div><div><time :datetime="source.last_data_date">{{ formatDate(source.last_data_date) }}</time><span>{{ formatNumber(source.row_count) }}행</span></div></li></ul><p v-else class="empty-state">소스 상태가 없습니다.</p></section>
      <footer><span>저장 자료 기준 · 관측일과 실제 이용 가능 시점은 다를 수 있습니다.</span></footer>
    </template>
  </main>
</template>
