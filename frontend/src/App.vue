<script setup>
import { computed, onMounted, ref } from 'vue'
import ForecastCards from './components/ForecastCards.vue'
import HowItWorks from './components/HowItWorks.vue'
import NewsPanel from './components/NewsPanel.vue'
import PriceChart from './components/PriceChart.vue'
import WhySection from './components/WhySection.vue'
import WrapUp from './components/WrapUp.vue'
import { HORIZONS } from './lib.js'
import { QA } from './research.js'

const loading = ref(true)
const data = ref(null)
const failures = ref([])
const heroHorizon = ref(20)
const exploreHorizon = ref(20)
const allPrices = ref(null)
const fullLoading = ref(false)
const fullError = ref('')
const weather = ref(null)
const weatherFailed = ref(false)
const asof = computed(() => data.value?.latest[0]?.origin_date)
const forecast20 = computed(() => data.value?.latest.find(row => row.horizon === 20) ?? data.value?.latest[0] ?? null)
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
  // 첫 화면을 그린 뒤 Why 섹션과 전체 보기에 쓰는 자료를 받는다
  loadAllPrices()
  loadWeather()
}
async function loadAllPrices() {
  if (allPrices.value || fullLoading.value) return
  fullLoading.value = true
  fullError.value = ''
  try { allPrices.value = (await getJson('/api/prices?days=8000')).filter(row => row.date >= '2005-01-01') }
  catch { fullError.value = '전체 가격을 불러오지 못했습니다. 다시 시도하거나 1년 보기를 선택하세요.' }
  finally { fullLoading.value = false }
}
async function loadWeather() {
  weatherFailed.value = false
  try { weather.value = await getJson('/api/weather?region=br_sul_minas') }
  catch { weatherFailed.value = true }
}
onMounted(load)
</script>

<template>
  <a class="skip-link" href="#price-section">전체 차트로 바로 가기</a>
  <header class="hero">
    <div class="hero-head">
      <div>
        <h1>커피 원두 가격 예측</h1>
        <p class="hero-sub">아라비카 커피 선물 가격이 5·20·60거래일 뒤 어디쯤 있을지, 매일 예측하고 기록합니다.</p>
      </div>
      <div class="hero-meta">
        <p v-if="asof" class="asof">{{ asof }} 종가 기준</p>
        <ul class="keys" aria-label="첫 화면 차트 범례">
          <li><i class="key line" />실제 종가</li>
          <li><i class="key pred" />그때의 가운데</li>
          <li><i class="key band" />그때의 80% 범위</li>
          <li><i class="key fan" />오늘의 예측</li>
        </ul>
      </div>
    </div>
    <div class="hero-chart">
      <p v-if="loading" class="cap" role="status">가격과 예측 자료를 불러오고 있습니다…</p>
      <PriceChart v-else-if="data" mode="hero" :horizon="heroHorizon" :prices="data.prices" :history="data.history"
        :latest="data.latest" :metadata="data.model?.metadata" />
    </div>
    <div class="hero-foot">
      <ForecastCards v-if="data?.latest.length" v-model:horizon="heroHorizon" :forecasts="data.latest" />
      <p v-else-if="data" class="cap">저장된 예측을 확인할 수 없습니다. 가격 자료는 그래프에서 확인할 수 있습니다.</p>
      <p v-if="failures.length" class="cap" role="status">일부 자료를 불러오지 못했습니다. 확인된 자료만 표시합니다. <button class="text-button" @click="load">다시 불러오기</button></p>
    </div>
  </header>

  <main v-if="data">
    <HowItWorks :prices="data.prices" :history="data.history" :latest="data.latest" />
    <WhySection :all-prices="allPrices" :weather="weather" :weather-failed="weatherFailed" :full-error="fullError"
      :model="data.model" :forecast="forecast20" @retry-prices="loadAllPrices" @retry-weather="loadWeather" />
    <section class="block explore">
      <div class="inner">
        <PriceChart v-model:horizon="exploreHorizon" :prices="data.prices" :history="data.history" :latest="data.latest"
          :metadata="data.model?.metadata" :all-prices="allPrices" :full-loading="fullLoading" :full-error="fullError"
          :history-failed="failures.includes(HORIZONS.indexOf(exploreHorizon) + 2)" @load-all="loadAllPrices" />
        <NewsPanel v-if="data.news" :news="data.news" />
        <p v-else class="cap">뉴스를 불러오지 못했습니다.</p>
      </div>
    </section>
    <WrapUp :history="data.history" :failed-horizons="HORIZONS.filter((_, i) => failures.includes(i + 2))"
      :model="data.model" :status="data.status" />
    <section class="block qa" aria-labelledby="qa-title">
      <div class="inner">
        <h2 id="qa-title" class="h2">Q&amp;A</h2>
        <p class="prose qa-lead">예상하는 질문과 답, 그 답을 확인할 곳과 다음 대응을 적었다.</p>
        <div class="qa-list">
          <details v-for="item in QA" :key="item.q">
            <summary>{{ item.q }}</summary>
            <p>{{ item.a }}</p>
            <dl class="qmj"><div><dt>확인할 곳</dt><dd>{{ item.check }}</dd></div><div><dt>다음 대응</dt><dd>{{ item.next }}</dd></div></dl>
          </details>
        </div>
      </div>
    </section>
  </main>

  <footer class="block cta">
    <div class="inner">
      <p class="name">홍석진</p>
      <p class="intro">MJU, Data Science</p>
      <div class="links">
        <a href="https://github.com/sjinie" target="_blank" rel="noopener noreferrer"><small>GitHub</small>github.com/sjinie</a>
        <a href="https://github.com/sjinie/Coffee_Price_Prediction" target="_blank" rel="noopener noreferrer"><small>이 프로젝트</small>Coffee_Price_Prediction</a>
        <a href="mailto:sjinie98@gmail.com"><small>이메일</small>sjinie98@gmail.com</a>
      </div>
      <p class="cap legal">개인 포트폴리오용 분석이며 투자 조언이 아닙니다. 가격은 Yahoo Finance(KC=F) 값이라 거래소 공식 정산가와 다를 수 있습니다.</p>
    </div>
  </footer>
</template>
