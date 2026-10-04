<script setup>
import { computed, ref } from 'vue'
import {
  HORIZONS, SIGNAL_LABELS, bandPath, extent, formatNumber, formatPercent, formatReturn, formatSigned, isNumber,
  linePath, linearScale, modelFor, nearestIndex, ticks, toTime,
} from '../lib.js'
import { useWidth } from '../useWidth.js'

const props = defineProps({
  prices: { type: Array, required: true },   // [{date, close}]
  history: { type: Object, required: true }, // {5: [예측 행], 20: [...], 60: [...]}
  latest: { type: Array, required: true },
  metadata: { type: Object, default: null }, // 활성 모델의 metadata. 지평별 모델 이름과 신호 기준을 읽는다.
})
// 위 칸은 종가와 예상 범위, 아래 칸은 기준일마다 낸 상승 확률. 두 칸이 x축을 같이 쓴다.
const PRICE = { top: 16, bottom: 276 }
const PROB = { top: 300, bottom: 396 }
const HEIGHT = 420
const MARGIN = { right: 64, left: 48 } // 오른쪽 여백에 구매·미루기 기준 라벨을 둔다(선과 겹치지 않게)
const horizon = ref(20)
const box = ref(null)
const width = useWidth(box)

const returnModel = computed(() => modelFor(props.metadata, 'return', horizon.value))
const rangeModel = computed(() => modelFor(props.metadata, 'volatility', horizon.value))
const probModel = computed(() => modelFor(props.metadata, 'direction', horizon.value))
const threshold = computed(() => props.metadata?.direction?.horizons?.[horizon.value]?.threshold)

const prices = computed(() => props.prices.filter(row => isNumber(row.close)))
const start = computed(() => prices.value[0]?.date)
const current = computed(() => props.latest.find(row => row.horizon === horizon.value))
const rows = computed(() => props.history[horizon.value] || [])
// 목표일이 이미 지난 과거 예측. 목표일에 맞춰 그려 그날의 실제 종가와 비교한다.
// 아직 오지 않은 목표일은 오늘의 예측(부채꼴)으로만 보여 준다.
const past = computed(() => rows.value
  .filter(row => row.target_date >= start.value && row.target_date <= prices.value.at(-1)?.date))
const band = computed(() => past.value.filter(row => isNumber(row.price_low)))
const probRows = computed(() => rows.value.filter(row => isNumber(row.prob_up) && row.origin_date >= start.value))
const byTarget = computed(() => new Map(past.value.map(row => [row.target_date, row])))
const byOrigin = computed(() => new Map(probRows.value.map(row => [row.origin_date, row])))

const x = computed(() => {
  const end = current.value?.target_date || prices.value.at(-1)?.date
  return linearScale([toTime(start.value), toTime(end)], [MARGIN.left, width.value - MARGIN.right])
})
const yDomain = computed(() => extent([
  ...prices.value.map(row => row.close),
  ...band.value.flatMap(row => [row.price_low, row.price_high]),
  ...past.value.map(row => row.predicted_price),
  current.value?.price_low, current.value?.price_high, current.value?.predicted_price,
]))
const y = computed(() => linearScale(yDomain.value, [PRICE.bottom, PRICE.top]))
// 수축한 확률은 0.5 근처에 모이므로 0~100% 전체 대신 값과 기준선이 들어가는 범위만 보여 준다.
const probDomain = computed(() => {
  const t = threshold.value
  const [low, high] = extent([...probRows.value.map(row => row.prob_up), 0.5, t, isNumber(t) ? 1 - t : null], 0.15)
  return [Math.max(0, low), Math.min(1, high)]
})
const yProb = computed(() => linearScale(probDomain.value, [PROB.bottom, PROB.top]))

const point = (date, value) => [x.value(toTime(date)), y.value(value)]
const pricePath = computed(() => linePath(prices.value.map(row => point(row.date, row.close))))
const pastBand = computed(() => bandPath(
  band.value.map(row => point(row.target_date, row.price_high)),
  band.value.map(row => point(row.target_date, row.price_low))))
const fan = computed(() => {
  const row = current.value
  if (!row || !isNumber(row.price_low)) return ''
  const [ox, oy] = point(row.origin_date, row.origin_close)
  const [tx, high] = point(row.target_date, row.price_high)
  const low = y.value(row.price_low)
  return `M${ox},${oy} L${tx},${high} L${tx},${low} Z`
})
const pastPrediction = computed(() => linePath(past.value
  .map(row => (isNumber(row.predicted_price) ? point(row.target_date, row.predicted_price) : null))))
// 오늘의 예측: 기준일 종가에서 목표일의 예측 가격까지
const todayPrediction = computed(() => {
  const row = current.value
  if (!row || !isNumber(row.predicted_price)) return null
  return { from: point(row.origin_date, row.origin_close), to: point(row.target_date, row.predicted_price) }
})
const probPath = computed(() => linePath(probRows.value.map(row => [x.value(toTime(row.origin_date)), yProb.value(row.prob_up)])))
const lastProb = computed(() => probRows.value.at(-1))
const yTicks = computed(() => ticks(yDomain.value))
const probTicks = computed(() => ticks(probDomain.value, 3))
const xTicks = computed(() => {
  const [t0, t1] = [x.value.invert(MARGIN.left), x.value.invert(width.value - MARGIN.right)]
  const step = width.value < 600 ? 3 : 2
  const result = []
  for (let d = new Date(t0); d.getTime() <= t1; d.setUTCMonth(d.getUTCMonth() + 1)) {
    d.setUTCDate(1)
    if (d.getTime() >= t0 && d.getUTCMonth() % step === 0) {
      result.push({ time: d.getTime(), label: `${String(d.getUTCFullYear()).slice(2)}.${d.getUTCMonth() + 1}` })
    }
  }
  return result
})

const hover = ref(null)
function onMove(event) {
  const rect = event.currentTarget.getBoundingClientRect()
  const time = x.value.invert(event.clientX - rect.left)
  const lastPrice = prices.value.at(-1)
  if (current.value && time > toTime(lastPrice.date)) {
    hover.value = { left: x.value(toTime(current.value.target_date)), date: current.value.target_date, future: current.value }
    return
  }
  const index = nearestIndex(prices.value.map(row => toTime(row.date)), time)
  const row = prices.value[index]
  hover.value = {
    left: x.value(toTime(row.date)), date: row.date, close: row.close,
    past: byTarget.value.get(row.date), prob: byOrigin.value.get(row.date),
  }
}
</script>

<template>
  <section aria-labelledby="chart-title">
    <div class="section-head">
      <h2 id="chart-title">가격과 예측 <span class="muted">센트/파운드</span></h2>
      <div class="segmented" role="group" aria-label="지평 선택">
        <button v-for="h in HORIZONS" :key="h" :class="{ active: horizon === h }" :aria-pressed="horizon === h" @click="horizon = h">
          {{ h }}거래일
        </button>
      </div>
    </div>
    <ul class="legend">
      <li><i class="key line" />종가</li>
      <li><i class="key pred" />{{ horizon }}거래일 전에 예측한 가격</li>
      <li><i class="key band" />그때 예측한 80% 범위 (2026년은 소급 계산)</li>
      <li><i class="key fan" />오늘의 예측 가격·범위</li>
      <li><i class="key prob" />기준일에 낸 {{ horizon }}거래일 상승 확률 (아래 칸)</li>
      <li><i class="key threshold" />구매·미루기 기준</li>
    </ul>
    <div ref="box" class="chart-box">
      <svg :width="width" :height="HEIGHT" role="img"
           :aria-label="`KC=F 종가와 ${horizon}거래일 예측. 위 칸은 예상 범위, 아래 칸은 상승 확률입니다. 표는 아래 '표로 보기'에 있습니다.`"
           @pointermove="onMove" @pointerleave="hover = null">
        <g class="grid">
          <line v-for="(tick, i) in yTicks" :key="i" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="y(tick)" :y2="y(tick)" />
          <line v-for="(tick, i) in probTicks" :key="`p${i}`" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="yProb(tick)" :y2="yProb(tick)" />
        </g>
        <g class="axis-label">
          <text v-for="(tick, i) in yTicks" :key="`y${i}`" :x="MARGIN.left - 8" :y="y(tick) + 4" text-anchor="end">{{ Math.round(tick) }}</text>
          <text v-for="(tick, i) in probTicks" :key="`py${i}`" :x="MARGIN.left - 8" :y="yProb(tick) + 4" text-anchor="end">{{ formatPercent(tick) }}</text>
          <text v-for="tick in xTicks" :key="tick.time" :x="x(tick.time)" :y="HEIGHT - 8" text-anchor="middle">{{ tick.label }}</text>
        </g>
        <!-- 각 칸을 그린 모델. 지평마다 다를 수 있어 활성 모델의 metadata에서 읽는다. -->
        <text class="model-mark" :x="MARGIN.left + 8" :y="PRICE.bottom - 24">예측 가격: {{ returnModel ?? '없음' }}</text>
        <text class="model-mark" :x="MARGIN.left + 8" :y="PRICE.bottom - 8">예상 범위: {{ rangeModel ?? '없음' }}</text>
        <text class="model-mark" :x="MARGIN.left + 8" :y="PROB.top + 12">상승 확률: {{ probModel ?? '없음' }}</text>
        <path :d="pastBand" class="band" />
        <path :d="fan" class="fan" />
        <path :d="pastPrediction" class="pred" />
        <path :d="pricePath" class="price" />
        <g v-if="todayPrediction">
          <line class="pred" :x1="todayPrediction.from[0]" :y1="todayPrediction.from[1]"
                :x2="todayPrediction.to[0]" :y2="todayPrediction.to[1]" />
          <circle class="pred-dot" r="4" :cx="todayPrediction.to[0]" :cy="todayPrediction.to[1]" />
        </g>
        <g v-if="isNumber(threshold)" class="threshold-label">
          <line class="threshold" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="yProb(threshold)" :y2="yProb(threshold)" />
          <line class="threshold" :x1="MARGIN.left" :x2="width - MARGIN.right" :y1="yProb(1 - threshold)" :y2="yProb(1 - threshold)" />
          <text :x="width - MARGIN.right + 6" :y="yProb(threshold) + 4">구매 {{ formatPercent(threshold) }}</text>
          <text :x="width - MARGIN.right + 6" :y="yProb(1 - threshold) + 4">미루기 {{ formatPercent(1 - threshold) }}</text>
        </g>
        <path :d="probPath" class="prob" />
        <circle v-if="lastProb" class="prob-dot" r="4" :cx="x(toTime(lastProb.origin_date))" :cy="yProb(lastProb.prob_up)" />
        <g v-if="hover">
          <line class="crosshair" :x1="hover.left" :x2="hover.left" :y1="PRICE.top" :y2="PROB.bottom" />
        </g>
      </svg>
      <div v-if="hover" class="tooltip" :style="{ left: `${Math.min(hover.left + 12, width - 210)}px` }">
        <strong>{{ hover.date }}</strong>
        <template v-if="hover.future">
          <span>오늘 기준 {{ horizon }}거래일 예측</span>
          <span>예측 가격 {{ formatNumber(hover.future.predicted_price) }} <small>({{ formatReturn(hover.future.predicted_return) }})</small></span>
          <span v-if="isNumber(hover.future.price_low)">예상 범위 {{ formatNumber(hover.future.price_low) }} ~ {{ formatNumber(hover.future.price_high) }}</span>
          <span>상승 확률 {{ formatPercent(hover.future.prob_up, 1) }} · {{ SIGNAL_LABELS[hover.future.signal] }}</span>
        </template>
        <template v-else>
          <span>종가 {{ formatNumber(hover.close) }}</span>
          <template v-if="hover.past">
            <span>{{ horizon }}거래일 전 예측 {{ formatNumber(hover.past.predicted_price) }}
              <small>(오차 {{ formatSigned(hover.past.predicted_price - hover.close) }})</small></span>
            <span v-if="isNumber(hover.past.price_low)">예측 범위 {{ formatNumber(hover.past.price_low) }} ~ {{ formatNumber(hover.past.price_high) }}
              <small>({{ hover.past.price_low <= hover.close && hover.close <= hover.past.price_high ? '범위 안' : '범위 밖' }})</small></span>
            <small>기준일 {{ hover.past.origin_date }}{{ hover.past.kind === 'backfill' ? ' · 소급 계산' : '' }}</small>
          </template>
          <span v-if="hover.prob">이날 낸 상승 확률 {{ formatPercent(hover.prob.prob_up, 1) }} · {{ SIGNAL_LABELS[hover.prob.signal] }}</span>
        </template>
      </div>
    </div>
    <details>
      <summary>표로 보기 (최근 20개 예측)</summary>
      <table>
        <thead>
          <tr>
            <th>기준일</th><th>목표일</th><th class="num">기준 종가</th>
            <th class="num">예측 가격</th><th class="num">상승 확률</th><th>신호</th>
            <th class="num">80% 범위</th><th class="num">실제 종가</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows.slice(-20).reverse()" :key="row.origin_date">
            <td>{{ row.origin_date }}</td><td>{{ row.target_date }}</td><td class="num">{{ formatNumber(row.origin_close) }}</td>
            <td class="num">{{ formatNumber(row.predicted_price) }}</td>
            <td class="num">{{ formatPercent(row.prob_up, 1) }}</td><td>{{ SIGNAL_LABELS[row.signal] ?? '-' }}</td>
            <td class="num">{{ isNumber(row.price_low) ? `${formatNumber(row.price_low)} ~ ${formatNumber(row.price_high)}` : '-' }}</td>
            <td class="num">{{ formatNumber(row.actual_close) }}</td>
          </tr>
        </tbody>
      </table>
    </details>
  </section>
</template>
