<script setup>
import { computed } from 'vue'
import {
  HORIZONS, extremeWeek, formatPercent, formatSigned, isNumber, seasonality, weeklyAnomalyGrid, weeklyRainGrid,
  weeklyReturnGrid,
} from '../lib.js'
import { FINDINGS, FIXES, MODEL_VERSION, NEWS_FACTS, NOTES, RESEARCH, VOLATILITY, VOLATILITY_NOTE } from '../research.js'
import CopSpike from './CopSpike.vue'
import CrpsBars from './CrpsBars.vue'
import DecisionSpec from './DecisionSpec.vue'
import DotPlot from './DotPlot.vue'
import FeatureUnits from './FeatureUnits.vue'
import LongPrice from './LongPrice.vue'
import NewsLag from './NewsLag.vue'
import WalkForward from './WalkForward.vue'
import WhyChapter from './WhyChapter.vue'
import YearClock from './YearClock.vue'

const props = defineProps({
  allPrices: { type: Array, default: null },
  weather: { type: Object, default: null },
  weatherFailed: Boolean,
  fullError: { type: String, default: '' },
  model: { type: Object, default: null },
  forecast: { type: Object, default: null },
})
const emit = defineEmits(['retry-prices', 'retry-weather'])
const metadata = computed(() => props.model?.metadata ?? null)
// 연간 시계는 끝까지 관측한 해만 쓴다(올해는 아직 52주가 차지 않았다)
const years = computed(() => {
  const last = Number((props.allPrices?.at(-1)?.date ?? props.forecast?.origin_date ?? '2026').slice(0, 4)) - 1
  return Array.from({ length: last - 2005 + 1 }, (_, i) => 2005 + i)
})
const returnGrid = computed(() => (props.allPrices ? weeklyReturnGrid(props.allPrices, years.value) : null))
const rainGrid = computed(() => (props.weather ? weeklyRainGrid(props.weather, years.value) : null))
// 이상기후: 최저기온은 서리·한파, 최고기온은 폭염이 드러나도록 같은 주 평년과의 차이로 그린다
const TEMPS = [
  { kind: 'tmin', key: 't_min', name: '최저기온', lowest: true, note: '서리·한파가' },
  { kind: 'tmax', key: 't_max', name: '최고기온', lowest: false, note: '폭염이' },
]
const temps = computed(() => TEMPS.map(t => {
  const grid = props.weather ? weeklyAnomalyGrid(props.weather, years.value, t.key) : null
  const extreme = grid && extremeWeek(grid, years.value, t.lowest)
  const start = extreme && new Date(Date.UTC(extreme.year, 0, 1 + extreme.week * 7))
  return { ...t, grid, extreme, when: start ? `${extreme.year}년 ${start.getUTCMonth() + 1}월 ${start.getUTCDate()}일부터 한 주` : '' }
}))
// 자료마다 불러오는 중, 실패(다시 시도), 빈 응답을 구별한다. 실패를 '불러오는 중'으로 남겨 두지 않는다.
const hasValues = grid => Boolean(grid?.some(row => row.some(isNumber)))
const priceState = computed(() => (props.allPrices?.length ? 'ready' : props.fullError ? 'error' : props.allPrices ? 'empty' : 'loading'))
const weatherState = computed(() => (hasValues(rainGrid.value) ? 'ready' : props.weatherFailed ? 'error' : props.weather ? 'empty' : 'loading'))
const PRICE_TEXT = { loading: '전체 가격을 불러오는 중입니다.', error: '전체 가격을 불러오지 못했습니다.', empty: '표시할 가격 자료가 없습니다.' }
const WEATHER_TEXT = { loading: '산지 기상 자료를 불러오는 중입니다.', error: '산지 기상 자료를 불러오지 못했습니다.', empty: '표시할 기상 자료가 없습니다.' }
const consistency = grid => { const value = grid && seasonality(grid); return value === null || value === undefined ? '-' : value.toFixed(2) }
const stale = computed(() => props.model?.model_version && props.model.model_version !== MODEL_VERSION)

// 그림 축과 숫자 형식
const pct = digits => v => `${formatSigned(v, digits)}%`
const plain = digits => v => `${v.toFixed(digits)}%`
const PERIODS = [['dev', '개발', 'muted'], ['holdout', '보류', 'amber'], ['forward', '2026년', 'cyan']]
const periodSeries = PERIODS.map(([key, name, color]) => ({ key, name, color }))
const P = RESEARCH.periods
const R = RESEARCH.results

// 문제 정의: 2005년부터 h거래일 뒤 가격이 움직인 폭
// coffee.portfolio가 모델 목표 y_h와 같은 거래 세션 달력으로 계산한다(종가가 없는 쌍은 빼고, 간격을 늘리지 않는다)
const moveRows = HORIZONS.map(h => ({ label: `${h}일`, values: RESEARCH.moves[h] }))
// 피처 선택: 07 단계별 기여(개발 구간)
const ladderRows = R.ladder.steps.map((step, i) => ({ label: step, values: Object.fromEntries(HORIZONS.map(h => [`h${h}`, R.ladder.dev[h][i]])) }))
// 결과·의사결정: 모델 metadata의 구간별 성적
const byPeriod = (h, field, scale = 1) => Object.fromEntries(PERIODS.map(([key]) => {
  const value = metadata.value?.distribution?.horizons?.[h]?.[key]?.[field]
  return [key, isNumber(value) ? value * scale : null]
}))
const metricRows = (field, scale) => HORIZONS.map(h => ({ label: `${h}일`, values: byPeriod(h, field, scale) }))
const signalRows = computed(() => PERIODS.map(([key, name]) => {
  const item = metadata.value?.distribution?.horizons?.[20]?.[key]
  const value = field => (isNumber(item?.[field]) ? item[field] * 100 : null)
  return { label: name, values: { precision: value('signal_precision'), always: value('signal_up_rate') } }
}))
const PURCHASE_LABELS = { '개발 Test': '개발', '재사용 확인': '보류', '2026년': '2026년' }
const purchaseRows = R.purchase.map(row => ({ label: PURCHASE_LABELS[row.period] ?? row.period, values: row }))
const evaluationNotes = {
  ...NOTES.evaluation,
  method: `${P.train_start.slice(0, 4)}년부터 학습하고 ${P.development.join('–')}년을 한 해씩 예측하며 설정을 골랐다. 고른 설정은 ${P.holdout.join('–')}년에 한 번만 적용했고, ${P.forward_start.slice(0, 4)}년은 동결한 모델의 사후 확인이다. 목표일이 학습 구간을 넘는 행은 학습에서 뺐다.`,
}
</script>

<template>
  <section class="block why" aria-labelledby="why-title">
    <div class="inner">
      <h2 id="why-title" class="h2">왜 이렇게 만들었을까</h2>
      <p v-if="stale" class="notice" role="status">아래 연구 결과는 모델 {{ MODEL_VERSION }} 기준입니다. 지금 서비스 중인 모델은 {{ model.model_version }}입니다.</p>

      <WhyChapter side="문제 정의" :finding="FINDINGS.problem" :notes="NOTES.problem">
        <figure v-if="priceState === 'ready'"><LongPrice :prices="allPrices" /><figcaption class="cap">KC=F 주간 종가(센트/파운드), 2005년부터.</figcaption></figure>
        <p v-else class="cap" role="status">{{ PRICE_TEXT[priceState] }} <button v-if="priceState === 'error'" class="text-button" type="button" @click="emit('retry-prices')">다시 시도</button></p>
        <DotPlot :rows="moveRows" :domain="[-20, 30]" :format="pct(0)" label="지평별 h거래일 뒤 가격 변화의 하위 10%, 중앙값, 상위 10%"
          :series="[{ key: 'q10', name: '하위 10%', color: 'cyan' }, { key: 'q50', name: '중앙값', color: 'ink' }, { key: 'q90', name: '상위 10%', color: 'amber' }]">
          2005년부터 모든 거래일에서 잰 h거래일 뒤 가격 변화. 20거래일 뒤에는 {{ formatPercent(RESEARCH.moves[20].big) }}의 날에 가격이 10% 넘게 움직였다.
        </DotPlot>
      </WhyChapter>

      <WhyChapter side="데이터 탐색" :finding="FINDINGS.seasons" :notes="NOTES.seasons">
        <div class="clocks">
          <figure class="clock-figure">
            <YearClock v-if="weatherState === 'ready'" :grid="rainGrid" :years="years" kind="rain" label="브라질 남미나스 산지의 주간 강수 연간 시계" />
            <p v-else class="clock-empty cap" role="status"><span>{{ WEATHER_TEXT[weatherState] }} <button v-if="weatherState === 'error'" class="text-button" type="button" @click="emit('retry-weather')">다시 시도</button></span></p>
            <div class="ramp"><span>적음</span><i class="ramp-rain" /><span>많음</span></div>
            <p v-if="weatherState === 'ready'" class="big">{{ consistency(rainGrid) }}</p>
            <figcaption class="cap">브라질 남미나스 산지의 주간 강수(7일 기준). 숫자는 한 해의 모양이 나머지 해들의 평균과 얼마나 닮았는지(상관의 중앙값)다.</figcaption>
          </figure>
          <figure v-for="t in temps" :key="t.kind" class="clock-figure">
            <YearClock v-if="weatherState === 'ready' && hasValues(t.grid)" :grid="t.grid" :years="years" :kind="t.kind" :label="`브라질 남미나스 산지의 주간 ${t.name} 편차 연간 시계`" />
            <p v-else class="clock-empty cap" role="status"><span>{{ WEATHER_TEXT[weatherState === 'ready' ? 'empty' : weatherState] }} <button v-if="weatherState === 'error'" class="text-button" type="button" @click="emit('retry-weather')">다시 시도</button></span></p>
            <div class="ramp"><span>평년보다 추움</span><i class="ramp-return" /><span>더움</span></div>
            <p v-if="t.extreme" class="big">{{ formatSigned(t.extreme.value, 1) }}℃</p>
            <figcaption class="cap">{{ t.name }}이 같은 주 평년({{ years[0] }}–{{ years.at(-1) }}년 평균)보다 얼마나 높거나 낮았는지. {{ t.note }} 드러난다. 숫자는 평년에서 가장 크게 벗어난 주({{ t.when }})의 값이다.</figcaption>
          </figure>
        </div>
        <div class="clocks">
          <figure class="clock-figure">
            <YearClock v-if="priceState === 'ready' && hasValues(returnGrid)" :grid="returnGrid" :years="years" kind="return" label="KC=F 주간 로그수익률 연간 시계" />
            <p v-else class="clock-empty cap" role="status"><span>{{ PRICE_TEXT[priceState === 'ready' ? 'empty' : priceState] }} <button v-if="priceState === 'error'" class="text-button" type="button" @click="emit('retry-prices')">다시 시도</button></span></p>
            <div class="ramp"><span>하락</span><i class="ramp-return" /><span>상승</span></div>
            <p v-if="priceState === 'ready' && hasValues(returnGrid)" class="big">{{ consistency(returnGrid) }}</p>
            <figcaption class="cap">KC=F 주간 로그수익률. 숫자는 강수와 같은 방법으로 잰 해마다의 닮은 정도다.</figcaption>
          </figure>
        </div>
        <p class="cap">고리 하나가 한 해다. 안쪽이 {{ years[0] }}년, 바깥이 {{ years.at(-1) }}년이고 12시 방향에서 1월이 시작한다.</p>
      </WhyChapter>

      <WhyChapter side="피처 선택" :finding="FINDINGS.features" :notes="NOTES.features">
        <figure>
          <FeatureUnits :groups="RESEARCH.feature_groups" :metadata="metadata" />
          <figcaption class="cap">칸 하나가 피처 하나다(20거래일 모델, 모델 metadata에서 읽는다). 피처 67개는 분포의 중심(방향)과 폭(변동폭)에 함께, 변동성(별도) 3개(HAR 입력)는 폭에만 들어간다. 얼마나 쓸지는 벌점이 정한다.</figcaption>
        </figure>
        <DotPlot :rows="ladderRows" :domain="[-1, 3]" :format="pct(1)" :tick-format="pct(0)" :label-width="132"
          label="단계별로 피처를 더했을 때 기준선 대비 개발 구간 CRPS"
          :series="HORIZONS.map((h, i) => ({ key: `h${h}`, name: `${h}일`, color: ['muted', 'amber', 'cyan'][i] }))">
          현재가 유지 + HAR 폭(기준선) 대비 개발 구간 CRPS. 위에서 아래로 한 단계씩 더했다. 0보다 왼쪽이면 기준선보다 실제 가격에 가까웠다(노트북 07).
        </DotPlot>
      </WhyChapter>

      <WhyChapter side="뉴스 분석" :finding="FINDINGS.news" :notes="NOTES.news">
        <div class="split">
          <figure>
            <NewsLag :lag="RESEARCH.news_lag" />
            <figcaption class="cap">거래일 t의 뉴스 점수와 t+k일 수익률의 상관. 옅은 띠는 상관이 0일 때의 ±2 표준오차다.</figcaption>
          </figure>
          <dl class="facts-list"><div v-for="f in NEWS_FACTS" :key="f.term"><dt>{{ f.term }}</dt><dd>{{ f.text }}</dd></div></dl>
        </div>
        <DotPlot :rows="HORIZONS.map(h => ({ label: `${h}일`, values: R.news_ablation[h] }))" :domain="[-1, 1]" :format="pct(2)" :tick-format="pct(1)"
          label="분포 모델에서 뉴스만 빼고 다시 계산한 CRPS 변화" :series="periodSeries.slice(1)">
          분포 모델에서 뉴스 점수만 빼고 다시 계산했을 때의 CRPS 변화. 0보다 오른쪽이면 뺀 쪽이 나빴다(뉴스가 도왔다). 개발 구간({{ P.development.join('-') }})에는 기사가 없어 비교하지 않는다.
        </DotPlot>
      </WhyChapter>

      <WhyChapter side="평가" :finding="FINDINGS.evaluation" :notes="evaluationNotes">
        <div class="split">
          <figure>
            <ul class="keys"><li><i class="key sq train" />학습</li><li><i class="key sq dev" />개발 평가</li><li><i class="key sq holdout" />보류 평가, 한 번만</li><li><i class="key sq forward" />사후 확인</li></ul>
            <WalkForward :periods="P" />
          </figure>
          <dl class="facts-list">
            <div><dt>분포의 점수</dt><dd>CRPS, 예측 분포 전체와 실제 가격의 거리</dd></div>
            <div><dt>분포의 기준선</dt><dd>현재가 유지 + HAR 변동성 폭</dd></div>
            <div><dt>방향의 기준선</dt><dd>학습 구간의 상승 비율</dd></div>
          </dl>
        </div>
        <DotPlot :rows="VOLATILITY.map(r => ({ label: `${r.horizon}일`, values: r }))" :domain="[-5, 30]" :format="v => `${v}%`"
          label="지평별 HAR 변동성의 직전 변동성 대비 RMSE 감소율" :series="periodSeries">
          {{ VOLATILITY_NOTE }}
        </DotPlot>
      </WhyChapter>

      <WhyChapter side="결과" :finding="FINDINGS.results" :notes="NOTES.results">
        <figure>
          <ul class="keys"><li><i class="key sq dev" />개발 {{ P.development.join('-') }}</li><li><i class="key sq holdout" />보류 {{ P.holdout.join('-') }}</li></ul>
          <CrpsBars :metadata="metadata" />
          <figcaption class="cap">현재가 유지 + HAR 변동성 폭(기준선) 대비 CRPS. 0보다 왼쪽이면 예측 분포가 실제 가격에 더 가까웠고, 오른쪽이면 멀었다. 개발 구간의 차이는 20일만 우연을 넘었다(나쁜 쪽).</figcaption>
        </figure>
        <div class="pair">
          <DotPlot :rows="metricRows('coverage80', 100)" :domain="[55, 90]" :reference="80" :format="plain(0)"
            label="지평별 80% 범위에 실제 가격이 들어간 비율" :series="periodSeries">
            80% 범위에 실제 가격이 들어간 비율. 세로선이 목표 80%다.
          </DotPlot>
          <DotPlot :rows="metricRows('auc', 1)" :domain="[0.4, 0.9]" :reference="0.5" :format="v => v.toFixed(2)" :tick-format="v => v.toFixed(1)"
            label="지평별 상승 확률의 AUC" :series="periodSeries">
            상승 확률로 오른 날과 내린 날을 가른 정도(AUC). 세로선 0.5가 동전 던지기다. 2026년은 기준일이 서로 겹쳐 몇 번의 큰 움직임에 좌우된다.
          </DotPlot>
        </div>
      </WhyChapter>

      <WhyChapter side="시행착오" :finding="FINDINGS.fixes" :notes="NOTES.fixes">
        <div class="pair">
          <DotPlot :rows="HORIZONS.map(h => ({ label: `${h}일`, values: R.return_stages[h] }))" :domain="[-2, 6]" :format="pct(1)" :tick-format="pct(0)"
            label="수익률 모델 재설계 전후의 현재가 유지 대비 개발 구간 RMSE"
            :series="[{ key: 'before', name: '03 수익률 모델', color: 'muted' }, { key: 'after', name: '03b 재설계', color: 'amber' }]">
            예측이 너무 컸다: 현재가 유지 대비 개발 구간 RMSE(같은 평가 행). 0보다 오른쪽이면 현재가 유지보다 오차가 컸다.
          </DotPlot>
          <CopSpike :fx="RESEARCH.fx_cop">
            환율 오류 시세: Yahoo 페소 환율(COP/USD). 흐린 선이 원자료, 흰 선이 이상치 규칙을 거친 값이다. 이 기간에 원자료가 하루 만에 크게 떨어졌다 돌아온 {{ RESEARCH.fx_cop.replaced.length }}일을 직전 값으로 바꿨다.
          </CopSpike>
        </div>
        <div class="fixes">
          <div v-for="fix in FIXES" :key="fix.title" class="fix"><h4>{{ fix.title }}</h4><p>{{ fix.text }}</p></div>
        </div>
      </WhyChapter>

      <WhyChapter side="의사결정" :finding="FINDINGS.decision" :notes="NOTES.decision">
        <div class="pair">
          <DotPlot :rows="signalRows" :domain="[40, 70]" :reference="50" :format="plain(1)" :tick-format="plain(0)" :label-width="64"
            label="20거래일 신호의 적중률과 같은 날 늘 구매했을 때의 적중률"
            :series="[{ key: 'precision', name: '신호 적중률', color: 'amber' }, { key: 'always', name: '같은 날 늘 구매', color: 'muted' }]">
            20거래일 매수 신호를 낸 날의 적중률과, 그날들에 늘 구매라고 했을 때의 적중률(그날들의 상승 비율).
          </DotPlot>
          <DotPlot :rows="purchaseRows" :domain="[-7, 1]" :format="pct(2)" :tick-format="pct(0)" :label-width="64"
            label="20거래일 구매 시뮬레이션, 정기 구매 대비 구매가 차이"
            :series="[{ key: 'model', name: '신호대로', color: 'amber' }, { key: 'random', name: '무작위', color: 'muted' }, { key: 'oracle', name: '미래를 알 때', color: 'cyan' }]">
            20거래일마다 한 번 산다고 할 때, 미루기 신호면 20거래일 뒤에 사고 아니면 바로 산 평균 구매가를 정기 구매와 비교했다. 왼쪽일수록 싸게 샀다. 무작위는 반은 지금, 반은 나중에 산 경우다.
          </DotPlot>
        </div>
        <DecisionSpec :forecast="forecast" :metadata="metadata" />
      </WhyChapter>
    </div>
  </section>
</template>
