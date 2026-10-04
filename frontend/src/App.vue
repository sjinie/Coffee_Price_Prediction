<script setup>
import { computed, onMounted, ref } from 'vue'
import ForecastCards from './components/ForecastCards.vue'
import NewsPanel from './components/NewsPanel.vue'
import PriceChart from './components/PriceChart.vue'
import StatusPanel from './components/StatusPanel.vue'
import TrackRecord from './components/TrackRecord.vue'
import { HORIZONS, SIGNAL_LABELS, formatNumber, formatPercent, formatReturn, isNumber } from './lib.js'

const loading = ref(true)
const data = ref(null)
const failures = ref([])
const horizon = ref(20)
const allPrices = ref(null)
const fullLoading = ref(false)
const fullError = ref('')
const current = computed(() => data.value?.latest.find(row => row.horizon === horizon.value))
const threshold = computed(() => data.value?.model?.metadata?.direction?.horizons?.[horizon.value]?.threshold)
const dateLabel = value => value ? new Date(value + 'T00:00:00Z').toLocaleDateString('ko-KR', { timeZone: 'UTC', month: 'long', day: 'numeric' }) : '확인 중'
const paths = [
  '/api/prices?days=400', '/api/forecasts/latest',
  ...HORIZONS.map(h => `/api/forecasts/history?horizon=${h}&days=1000`),
  '/api/news?days=30', '/api/models', '/api/status',
]
async function getJson(path) {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`자료 응답 ${response.status}`)
  return response.json()
}
async function load() {
  loading.value = true
  const results = await Promise.allSettled(paths.map(getJson))
  const value = (i, fallback) => results[i].status === 'fulfilled' ? results[i].value : fallback
  failures.value = results.map((result, i) => result.status === 'rejected' ? i : null).filter(i => i !== null)
  data.value = {
    prices: value(0, []), latest: value(1, []),
    history: Object.fromEntries(HORIZONS.map((h, i) => [h, value(i + 2, [])])),
    news: value(5, null), model: value(6, null), status: value(7, null),
  }
  loading.value = false
}
// 전체 자료는 요청한 경우에만 한 번 받아 둔다. 지평을 바꿔도 같은 자료를 쓴다.
async function loadAllPrices() {
  if (allPrices.value || fullLoading.value) return
  fullLoading.value = true
  fullError.value = ''
  try { allPrices.value = (await getJson('/api/prices?days=8000')).filter(row => row.date >= '2005-01-01') }
  catch { fullError.value = '전체 가격을 불러오지 못했습니다. 다시 시도하거나 1년 보기를 선택하세요.' }
  finally { fullLoading.value = false }
}
onMounted(load)
</script>

<template>
  <a class="skip-link" href="#price-section">가격 그래프로 바로 가기</a>
  <main>
    <header class="page-head">
      <p class="eyebrow">아라비카 커피 선물 KC=F<span v-if="current"> · {{ current.origin_date }} 종가 기준</span></p>
      <h1>원두를 지금 살까, 기다릴까</h1>
      <p class="description">아라비카 커피 선물의 5·20·60거래일 뒤 가격과 80% 예상 범위, 상승 확률을 매일 계산합니다.</p>
      <p v-if="current" class="outlook" aria-live="polite">
        {{ dateLabel(current.origin_date) }} 예측 기준 종가 <span>{{ formatNumber(current.origin_close, 2) }}¢</span>에서
        <span>{{ horizon }}거래일</span> 뒤인 {{ dateLabel(current.target_date) }}의 예측은
        <span>{{ formatNumber(current.predicted_price) }}¢</span>({{ formatReturn(current.predicted_return) }}),
        80% 범위는 <span>{{ formatNumber(current.price_low, 0) }}–{{ formatNumber(current.price_high, 0) }}¢</span>입니다.
        상승 확률 <span>{{ formatPercent(current.prob_up, 1) }}</span><template v-if="isNumber(threshold)">
          <template v-if="current.signal === 'buy'">가 구매 기준 {{ formatPercent(threshold) }} 이상으로 <span>지금 구매</span> 신호입니다.</template>
          <template v-else-if="current.signal === 'wait'">가 미루기 기준 {{ formatPercent(1 - threshold) }} 이하로 <span>미루기</span> 신호입니다.</template>
          <template v-else>가 구매({{ formatPercent(threshold) }})와 미루기({{ formatPercent(1 - threshold) }}) 기준 사이라 <span>보류</span>입니다.</template>
        </template><template v-else>, 신호는 <span>{{ SIGNAL_LABELS[current.signal] ?? '확인 불가' }}</span>입니다. 판단 기준은 현재 불러올 수 없습니다.</template>
      </p>
      <p class="note">예측 가격은 과거 검증에서 현재가 유지보다 오차가 컸습니다. 아래 지평별 숫자에 기준선 대비 성적을 함께 표시합니다.</p>
    </header>

    <p v-if="loading" class="loading-state" role="status">가격과 예측 자료를 불러오고 있습니다…</p>
    <template v-else-if="data">
      <p v-if="failures.length" class="load-note" role="status">일부 자료를 불러오지 못했습니다. 확인된 자료만 표시합니다. <button class="toggle" @click="load">다시 불러오기</button></p>
      <PriceChart v-model:horizon="horizon" :prices="data.prices" :history="data.history" :latest="data.latest"
        :metadata="data.model?.metadata" :all-prices="allPrices" :full-loading="fullLoading" :full-error="fullError"
        :history-failed="failures.includes(HORIZONS.indexOf(horizon) + 2)" @load-all="loadAllPrices" />
      <ForecastCards v-if="data.latest.length" v-model:horizon="horizon" :forecasts="data.latest" :metadata="data.model?.metadata" />
      <p v-else class="note">저장된 예측을 확인할 수 없습니다. 가격 자료는 그래프에서 확인할 수 있습니다.</p>
      <TrackRecord :history="data.history" :failed-horizons="HORIZONS.filter((_, i) => failures.includes(i + 2))" />
      <NewsPanel v-if="data.news" :news="data.news" />
      <section v-else aria-labelledby="news-title"><p class="eyebrow">참고 정보</p><h2 id="news-title">뉴스는 무엇을 말할까</h2><p class="note">뉴스를 불러오지 못했습니다.</p></section>
      <StatusPanel :model="data.model" :status="data.status" />
    </template>

    <footer>
      <p>개인 포트폴리오용 분석입니다. 가격은 Yahoo Finance(KC=F) 값이라 거래소 공식 정산가와 다를 수 있습니다.</p>
      <a href="https://github.com/sjinie/Coffee_Price_Prediction" target="_blank" rel="noopener noreferrer">프로젝트와 분석 기록 ↗</a>
    </footer>
  </main>
</template>
