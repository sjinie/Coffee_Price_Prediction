<script setup>
import { computed, onMounted, ref } from 'vue'
import ForecastCards from './components/ForecastCards.vue'
import NewsPanel from './components/NewsPanel.vue'
import PriceChart from './components/PriceChart.vue'
import StatusPanel from './components/StatusPanel.vue'
import TrackRecord from './components/TrackRecord.vue'
import { HORIZONS } from './lib.js'

const loading = ref(true)
const error = ref('')
const data = ref(null)

async function getJson(path) {
  const response = await fetch(path)
  if (!response.ok) throw new Error(`${path} 응답 ${response.status}`)
  return response.json()
}

async function load() {
  loading.value = true
  error.value = ''
  try {
    const [prices, latest, news, model, status, ...histories] = await Promise.all([
      getJson('/api/prices?days=400'), getJson('/api/forecasts/latest'), getJson('/api/news?days=30'),
      getJson('/api/models'), getJson('/api/status'),
      ...HORIZONS.map(h => getJson(`/api/forecasts/history?horizon=${h}&days=1000`)),
    ])
    const history = Object.fromEntries(HORIZONS.map((h, i) => [h, histories[i]]))
    data.value = { prices, latest, news, model, status, history }
  } catch (exc) {
    error.value = exc.message
  } finally {
    loading.value = false
  }
}

// 지평별 신호 기준은 화면에 적어 두지 않고 활성 모델의 metadata에서 읽는다.
const thresholds = computed(() => Object.fromEntries(
  Object.entries(data.value?.model?.metadata?.direction?.horizons ?? {}).map(([h, item]) => [h, item.threshold])))

onMounted(load)
</script>

<template>
  <main>
    <header class="page-head">
      <p class="eyebrow">아라비카 커피 선물 · KC=F</p>
      <h1>커피 선물 가격 전망</h1>
      <p class="lead">원두를 미리 살지 판단할 수 있도록 5·20·60거래일 뒤 가격의 예상 범위와 상승 확률을 매일 계산합니다.</p>
      <p v-if="data?.latest.length" class="muted">기준일 {{ data.latest[0].origin_date }} 종가 · 모델 {{ data.model?.model_version }}</p>
    </header>

    <p v-if="loading" class="muted">불러오는 중…</p>
    <div v-else-if="error" class="error" role="alert">
      <p>자료를 불러오지 못했습니다: {{ error }}</p>
      <button type="button" @click="load">다시 시도</button>
    </div>
    <template v-else-if="data">
      <p v-if="!data.latest.length" class="muted">저장된 예측이 없습니다. 파이프라인(backfill, daily)을 먼저 실행하세요.</p>
      <template v-else>
        <ForecastCards :forecasts="data.latest" :thresholds="thresholds" />
        <PriceChart :prices="data.prices" :history="data.history" :latest="data.latest" />
        <TrackRecord :history="data.history" />
      </template>
      <NewsPanel :news="data.news" />
      <StatusPanel :model="data.model" :status="data.status" />
    </template>

    <footer class="muted">
      개인 포트폴리오용 분석입니다. 가격은 Yahoo Finance(KC=F) 값이라 거래소 공식 정산가와 다를 수 있습니다.
    </footer>
  </main>
</template>
