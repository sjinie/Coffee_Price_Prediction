<script setup>
import { computed, onMounted, ref } from 'vue'

const prices = ref([])
const predictions = ref([])
const models = ref([])
const pipeline = ref(null)
const sources = ref([])
const loading = ref(true)
const error = ref('')
const selectedHorizon = ref(60)
const refreshButton = ref(null)

const formatter = new Intl.NumberFormat('ko-KR', { maximumFractionDigits: 2 })
const dateFormatter = new Intl.DateTimeFormat('ko-KR', { dateStyle: 'medium' })
const formatDate = value => value ? dateFormatter.format(new Date(`${value}T00:00:00`)) : '—'
const formatTime = value => value ? new Intl.DateTimeFormat('ko-KR', {
  dateStyle: 'medium', timeStyle: 'short',
}).format(new Date(value)) : '—'
const finiteNumber = value => value !== null && value !== '' && Number.isFinite(Number(value)) ? Number(value) : null
const formatNumber = value => {
  const number = finiteNumber(value)
  return number === null ? '—' : formatter.format(number)
}
const formatPercent = value => {
  const number = finiteNumber(value)
  return number === null ? '이용 불가' : `${number >= 0 ? '+' : ''}${formatNumber(number * 100)}%`
}
const formatProbability = value => finiteNumber(value) === null ? '이용 불가' : `${formatNumber(Number(value) * 100)}%`
const signalStatusLabel = value => ({ validated: 'Validation 기준 통과', experimental: '실험적 확률', fallback: '뉴스 제외 대체 확률', unavailable: '이용 불가' }[value] || value || '이용 불가')
const newsModeLabel = value => ({ historical: '과거 공개 시각 기반 연구 재생', live: '실제 수집 시각 적용' }[value] || '뉴스 시점 정보 없음')
const signalDirection = item => {
  return ['UP', 'DOWN', 'FLAT'].includes(item.final_direction) ? item.final_direction : '이용 불가'
}
const directionSignalLabel = value => ({ UP: '상승', DOWN: '하락', FLAT: '보합' }[value] || value)
const newsImpactLabel = value => {
  const score = finiteNumber(value)
  return score === null ? '이용 불가' : score > 0 ? '강세' : score < 0 ? '약세' : '중립'
}

async function get(path) {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`)
  return response.json()
}

async function loadData() {
  loading.value = true
  error.value = ''
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
async function retry() {
  await loadData()
  refreshButton.value?.focus()
}
onMounted(loadData)

const latestPrice = computed(() => prices.value.at(-1))
const latestPriceChange = computed(() => {
  if (prices.value.length < 2) return null
  const previous = finiteNumber(prices.value.at(-2).close)
  const latest = finiteNumber(latestPrice.value?.close)
  if (previous === null || latest === null || previous === 0) return null
  return ((latest / previous) - 1) * 100
})
const latestPriceDate = computed(() => latestPrice.value?.date || '')
const currentModelForHorizon = horizon => models.value.find(model => model.horizons.includes(horizon))
const isCurrentPrediction = item => {
  const currentModel = currentModelForHorizon(item.horizon)
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
  return { count: known.length, matched, missing: rows.length - known.length,
    accuracy: known.length ? matched / known.length * 100 : null,
    mae: rows.length ? rows.reduce((sum, item) => sum + Math.abs(item.priceError), 0) / rows.length : null }
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
  }).join(' ')
}
const directionLabel = value => value === null ? '확인 불가' : value > 0 ? '↑ 상승' : value < 0 ? '↓ 하락' : '→ 보합'
const matchLabel = value => value === null ? '평가 제외' : value ? '일치' : '불일치'
const selectedEvaluationKey = ref('')
const evaluationKey = item => item.model_id + ':' + item.origin_date + ':' + item.target_date
const selectedEvaluation = computed(() => evaluation.value.find(item => evaluationKey(item) === selectedEvaluationKey.value) ?? evaluation.value.at(-1))
const evaluationTitle = item => `${formatDate(item.target_date)} · 예측 ${directionLabel(item.predictedChange)} / 실제 ${directionLabel(item.actualChange)} · ${matchLabel(item.matched)} · 오차 ${formatNumber(item.priceError)} ¢/lb`
const selectedModel = computed(() => currentModelForHorizon(selectedHorizon.value))
const dlinearModel = computed(() => models.value.find(model => model.horizons?.includes(60) && model.name.startsWith('DLinear')))
const oldestFreshness = computed(() => {
  if (!sources.value.length) return null
  return [...sources.value].sort((a, b) => String(a.last_data_date).localeCompare(String(b.last_data_date)))[0]
})

function linePoints(values, domain, width = 900, height = 280) {
  if (!values.length) return ''
  const [min, max] = domain
  const span = max - min || 1
  return values.map((value, index) => {
    const x = (index / Math.max(values.length - 1, 1)) * width
    const y = height - ((value - min) / span) * height
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

const priceDomain = computed(() => {
  const values = prices.value.map(item => finiteNumber(item.close)).filter(value => value !== null)
  return chartDomain(values)
})
const comparisonDomain = computed(() => {
  const values = selectedPredictions.value.flatMap(item => [item.actual_price, item.predicted_price])
    .map(finiteNumber).filter(value => value !== null)
  return chartDomain(values)
})
function chartDomain(values) {
  if (!values.length) return [0, 1]
  const min = Math.min(...values)
  const max = Math.max(...values)
  const padding = (max - min) * 0.05 || Math.max(Math.abs(max) * 0.05, 1)
  return [min - padding, max + padding]
}
const pricePoints = computed(() => linePoints(prices.value.map(item => finiteNumber(item.close)), priceDomain.value))
const actualPoints = computed(() => linePoints(
  selectedPredictions.value.map(item => item.actual_price), comparisonDomain.value,
))
const predictedPoints = computed(() => linePoints(
  selectedPredictions.value.map(item => item.predicted_price), comparisonDomain.value,
))
const statusLabel = value => ({ success: '완료', failed: '실패', running: '실행 중', existing: '저장 자료', current: '최신 확인' }[value] || '대기')
const ticks = domain => Array.from({ length: 5 }, (_, index) => domain[1] - (domain[1] - domain[0]) * index / 4)
</script>

<template>
  <a class="skip-link" href="#dashboard">본문 바로가기</a>
  <main id="dashboard" tabindex="-1">
    <header class="page-heading">
      <div><h1>커피 선물</h1><p>KC=F · ICE Coffee C · 아라비카 · ¢/lb</p></div>
      <div class="heading-tools"><span>가격 기준 <b>{{ formatDate(latestPrice?.date) }}</b></span><button ref="refreshButton" class="refresh" :disabled="loading" @click="loadData">{{ loading ? '조회 중…' : '다시 조회' }}</button></div>
    </header>

    <div v-if="loading" class="loading-state" role="status" aria-live="polite"><p>가격과 예측 데이터를 불러오고 있습니다.</p><div class="skeleton metrics-skeleton"></div><div class="skeleton chart-skeleton"></div></div>
    <div v-else-if="error" class="notice error" role="alert"><strong>데이터를 불러오지 못했습니다.</strong><p>{{ error }}</p><button class="refresh" @click="retry">다시 시도</button></div>
    <template v-else>
      <div v-if="!prices.length" class="notice" role="status"><strong>아직 저장된 가격이 없습니다.</strong><p>데이터 적재가 끝나면 다시 조회해 주세요.</p></div>
      <section class="metrics" aria-label="주요 지표">
        <article class="primary-metric"><span>최근 종가</span><strong>{{ formatNumber(latestPrice?.close) }}<small>¢/lb</small></strong><small>{{ formatDate(latestPrice?.date) }}</small></article>
        <article><span>직전 거래일 대비</span><strong :class="latestPriceChange === null ? '' : latestPriceChange >= 0 ? 'up' : 'down'">{{ latestPriceChange === null ? '—' : (latestPriceChange >= 0 ? '+' : '') + formatNumber(latestPriceChange) + '%' }}</strong><small>종가 변동률</small></article>
        <article><span>최근일 고가 / 저가</span><strong class="range-value">{{ formatNumber(latestPrice?.high) }}<em>/</em>{{ formatNumber(latestPrice?.low) }}</strong><small>원천 OHLC · ¢/lb</small></article>
        <article><span>60일 Test RMSE</span><strong>{{ dlinearModel?.metrics?.test_rmse ?? '—' }}</strong><small>로그수익률 · DLinear</small></article>
        <article><span>가장 오래된 소스</span><strong class="date-value">{{ formatDate(oldestFreshness?.last_data_date) }}</strong><small class="source-id">{{ oldestFreshness?.source ?? '—' }}</small></article>
      </section>

      <div class="workspace">
        <section class="panel price-panel" aria-labelledby="price-title">
          <div class="panel-heading"><h2 id="price-title">가격 추이 <span>최근 {{ prices.length }}개 관측</span></h2><span class="unit">¢/lb</span></div>
          <template v-if="prices.length">
            <div class="chart-frame"><div class="y-axis" aria-hidden="true"><span v-for="(tick, index) in ticks(priceDomain)" :key="index">{{ formatNumber(tick) }}</span></div><div class="chart" role="img" :aria-label="`커피 종가 ${formatDate(prices[0]?.date)}부터 ${formatDate(latestPrice?.date)}까지, 단위 센트/파운드`"><svg viewBox="0 0 900 280" preserveAspectRatio="none"><line v-for="y in [0,70,140,210,280]" :key="y" x1="0" :y1="y" x2="900" :y2="y"/><polyline :points="pricePoints" class="price-line"/></svg></div></div>
            <div class="axis"><span>{{ formatDate(prices[0]?.date) }}</span><span>{{ formatDate(latestPrice?.date) }}</span></div>
          </template><p v-else class="empty-state">가격 데이터가 없습니다.</p>
        </section>

        <section class="panel future" aria-labelledby="forecast-title">
          <div class="panel-heading"><h2 id="forecast-title">최신 예측</h2><span class="unit">¢/lb</span></div>
          <p class="section-note">각 지평의 가장 최근 예측 기준일</p>
          <table v-if="futurePredictions.length" class="forecast-table"><caption class="sr-only">거래일 지평별 예측 가격, 수익률, 방향 신호와 뉴스 영향</caption><thead><tr><th scope="col">지평 / 모델</th><th scope="col">목표일</th><th scope="col" class="number">예측가 / 신호</th></tr></thead><tbody><tr v-for="item in futurePredictions" :key="item.horizon"><th scope="row">{{ item.horizon }}거래일<small>{{ currentModelForHorizon(item.horizon)?.name || '모델 정보 없음' }}</small></th><td>{{ formatDate(item.target_date) }}<small>기준 {{ formatDate(item.origin_date) }}</small></td><td class="number forecast-number">{{ formatNumber(item.predicted_price) }}<small>예상 단순수익률 {{ formatPercent(finiteNumber(item.predicted_return) === null ? null : Math.expm1(Number(item.predicted_return))) }}</small><span class="signal-status" :class="item.signal_status">{{ signalStatusLabel(item.signal_status) }}</span><small title="실제 보합 사례를 제외한 상승 확률">보합 제외 상승 확률 {{ formatProbability(item.probability_up) }} · 방향 {{ directionSignalLabel(signalDirection(item)) }}</small><small>뉴스 {{ newsImpactLabel(item.news_impact_score) }} · {{ finiteNumber(item.news_article_count) === null ? '이용 불가' : formatNumber(item.news_article_count) + '건' }} · {{ formatTime(item.news_updated_at) }}</small><small>{{ newsModeLabel(item.news_availability) }}</small><details v-if="item.classifier_version || item.model_version" class="forecast-version"><summary>모델 버전</summary><small>분류 {{ item.classifier_version || '이용 불가' }} · 모델 {{ item.model_version || '이용 불가' }}</small></details></td></tr></tbody></table>
          <p v-else class="empty-state">표시할 미성숙 예측이 없습니다.</p>
          <p class="footnote">표시된 기준일의 예측입니다. 현재 시점의 실시간 예측이 아닐 수 있습니다.</p>
        </section>

        <section class="panel comparison" aria-labelledby="comparison-title">
          <div class="panel-heading"><h2 id="comparison-title">실제 · 예측 비교</h2><div class="tabs" role="group" aria-label="예측 horizon 선택"><button v-for="horizon in [5,20,60]" :key="horizon" :aria-pressed="selectedHorizon === horizon" :class="{ active: selectedHorizon === horizon }" @click="selectedHorizon = horizon">{{ horizon }}일</button></div></div>
          <div class="chart-meta"><div class="legend"><span class="actual">실제</span><span class="predicted">예측</span></div><span>{{ selectedModel?.name ?? '모델 없음' }} · {{ selectedPredictions.length }}개 평가점 · ¢/lb</span></div>
          <template v-if="selectedPredictions.length"><div class="chart-frame"><div class="y-axis" aria-hidden="true"><span v-for="(tick,index) in ticks(comparisonDomain)" :key="index">{{ formatNumber(tick) }}</span></div><div class="chart" role="img" :aria-label="`${selectedHorizon}거래일 실제와 예측 가격 비교, 단위 센트/파운드`"><svg viewBox="0 0 900 280" preserveAspectRatio="none"><line v-for="y in [0,70,140,210,280]" :key="y" x1="0" :y1="y" x2="900" :y2="y"/><polyline :points="actualPoints" class="actual-line"/><polyline :points="predictedPoints" class="predicted-line"/></svg></div></div><div class="axis"><span>{{ formatDate(selectedPredictions[0]?.target_date) }}</span><span>정답 날짜 기준</span><span>{{ formatDate(selectedPredictions.at(-1)?.target_date) }}</span></div></template>
          <p v-else class="empty-state">이 지평의 실제값이 연결된 예측이 없습니다.</p>
          <div v-if="evaluation.length" class="evaluation">
            <div class="evaluation-summary">
              <span>방향 일치 <strong>{{ formatNumber(evaluationSummary.accuracy) }}{{ evaluationSummary.accuracy === null ? '' : '%' }}</strong> <small>{{ evaluationSummary.matched }}/{{ evaluationSummary.count }}건</small></span>
              <span>가격 MAE <strong>{{ formatNumber(evaluationSummary.mae) }}</strong> <small>¢/lb</small></span>
            </div>
            <p class="section-note">현재 표시 구간 · 기준일 종가 대비 상승/하락/보합 비교(보합은 실제 보합만 일치) · 기준가 없음 {{ evaluationSummary.missing }}건 제외</p>
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
        </section>

        <aside class="side-details" aria-label="모델 및 실행 정보">
          <section class="panel model"><div class="panel-heading"><h2>사용 모델</h2><span class="unit">{{ models.length }}개</span></div><div v-for="model in models" :key="model.model_id" class="model-row"><strong>{{ model.name }}</strong><span>{{ model.horizons.join(' · ') }}거래일</span><code>{{ model.model_id }}</code></div><p v-if="!models.length" class="empty-state">등록된 모델이 없습니다.</p><p class="caveat">60일 DLinear의 기존 Test RMSE는 Naive 대비 9.59% 낮았지만, 기간에 따른 차이로 안정적 우위는 미확정입니다.</p></section>
          <section class="panel run-panel"><div class="panel-heading"><h2>마지막 실행</h2><span class="status" :class="pipeline?.status"><i aria-hidden="true"></i>{{ statusLabel(pipeline?.status) }}</span></div><dl v-if="pipeline"><div><dt>모드</dt><dd>{{ pipeline.mode }}</dd></div><div><dt>완료</dt><dd>{{ formatTime(pipeline.finished_at) }}</dd></div><div><dt>처리 결과</dt><dd>{{ pipeline.message }}</dd></div></dl><p v-else class="empty-state">실행 기록이 없습니다.</p></section>
        </aside>
      </div>

      <section class="panel sources-panel" aria-labelledby="sources-title"><div class="panel-heading"><h2 id="sources-title">데이터 소스 <span>{{ sources.length }}개</span></h2><span class="section-note">마지막 관측일 · 수집 상태</span></div><ul v-if="sources.length" class="source-list"><li v-for="source in sources" :key="source.source"><div><strong>{{ source.source }}</strong><span class="source-status" :class="source.status">{{ statusLabel(source.status) }}</span></div><div><time :datetime="source.last_data_date">{{ formatDate(source.last_data_date) }}</time><span>{{ formatNumber(source.row_count) }}행</span></div></li></ul><p v-else class="empty-state">소스 상태가 없습니다.</p></section>
      <footer><span>저장 자료 기준 · 관측일과 실제 이용 가능 시점은 다를 수 있습니다.</span></footer>
    </template>
  </main>
</template>
